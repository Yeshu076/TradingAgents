"""
Module: mtm_service.py
Part of the execution subsystem.

F-01: Real-Time Mark-to-Market P&L Service.

Runs as a background daemon thread. Polls all open positions via broker
get_quote() calls, writes updated last_price + unrealized_pnl back to
SQLite (via PositionManager), and pushes the total unrealized PnL into
GlobalRiskMonitor to enable unrealized drawdown gating.

Design principles:
  - Never raises: all errors are caught and logged. The system continues
    to trade even if the MTM service fails.
  - Stale-price safety: if a broker quote fails, the last known price is
    retained in SQLite and a WARNING is logged.
  - Thread-safe: uses the same WAL-mode SQLite connection pattern as
    PositionManager.

Environment variables:
  TRADINGAGENTS_MTM_POLL_INTERVAL_S  – seconds between polls (default: 30)
  TRADINGAGENTS_MTM_ENABLED          – "true"/"false" (default: "true")
  TRADINGAGENTS_MTM_MAX_STALE_S      – max seconds before a price is considered
                                       stale and logged as a warning (default: 120)

Usage (from run_daemon.py or ops/daemon.py):
    from tradingagents.execution.mtm_service import MarkToMarketService
    from tradingagents.execution.position_manager import PositionManager
    from tradingagents.execution.global_risk import GlobalRiskMonitor

    pm = PositionManager.from_env()
    grm = GlobalRiskMonitor.get_instance()

    mtm = MarkToMarketService(position_manager=pm, risk_monitor=grm)
    mtm.start()       # non-blocking
    ...
    mtm.stop()
    mtm.join(timeout=5)
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

_ENV_POLL_INTERVAL = "TRADINGAGENTS_MTM_POLL_INTERVAL_S"
_ENV_ENABLED = "TRADINGAGENTS_MTM_ENABLED"
_ENV_MAX_STALE = "TRADINGAGENTS_MTM_MAX_STALE_S"


class MarkToMarketService:
    """
    Background daemon thread that keeps SQLite position rows current with
    real-time market prices and feeds unrealized P&L into GlobalRiskMonitor.

    Broker resolution: accepts an optional `broker_resolver` callable that,
    given a symbol and instrument_type, returns a broker instance with a
    `get_quote(symbol) -> float` method. When no resolver is provided, the
    service logs a warning and runs in pass-through mode (prices not updated).

    This design means:
      - The service can be unit-tested without real brokers.
      - It composes cleanly with the existing broker router.
    """

    def __init__(
        self,
        position_manager: Any,
        risk_monitor: Any,
        broker_resolver: Optional[Callable[[str, str], Any]] = None,
    ) -> None:
        """
        Args:
            position_manager: A PositionManager instance (or duck-typed equivalent).
            risk_monitor: A GlobalRiskMonitor instance.
            broker_resolver: Optional callable(symbol, instrument_type) -> broker.
                             The returned broker must implement get_quote(symbol) -> float.
        """
        self.pm = position_manager
        self.grm = risk_monitor
        self.broker_resolver = broker_resolver

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._poll_interval: float = float(
            os.environ.get(_ENV_POLL_INTERVAL, "30")
        )
        self._enabled: bool = (
            os.environ.get(_ENV_ENABLED, "true").strip().lower() != "false"
        )
        self._max_stale_s: float = float(
            os.environ.get(_ENV_MAX_STALE, "120")
        )

        # Track last-successful quote time per symbol for staleness warnings
        self._last_quote_ts: Dict[str, float] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the MTM daemon thread (idempotent)."""
        if not self._enabled:
            logger.info("MarkToMarketService: disabled via env — not starting.")
            return
        if self._thread and self._thread.is_alive():
            logger.debug("MarkToMarketService: already running.")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="mtm-service",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "MarkToMarketService: started (poll_interval=%ss, max_stale=%ss).",
            self._poll_interval,
            self._max_stale_s,
        )

    def stop(self) -> None:
        """Signal the MTM thread to stop."""
        self._stop_event.set()

    def join(self, timeout: float = 5.0) -> None:
        """Wait for the MTM thread to exit."""
        if self._thread:
            self._thread.join(timeout=timeout)

    def run_once(self) -> Dict[str, Any]:
        """
        Execute a single MTM poll cycle synchronously.

        Returns a summary dict with results per symbol — useful for testing
        and for the health-check endpoint.

        Returns:
            {
              "updated": ["BTC", ...],    # symbols successfully quoted
              "failed": ["XAU", ...],     # symbols where quote fetch failed
              "skipped": ["ETH", ...],    # symbols with no open position
              "total_unrealized_pnl": -123.45,
            }
        """
        updated: list = []
        failed: list = []
        skipped: list = []

        try:
            positions = self.pm.get_positions()
        except Exception as exc:
            logger.error("MarkToMarketService: failed to read positions: %s", exc)
            return {"updated": [], "failed": [], "skipped": [], "total_unrealized_pnl": 0.0}

        for pos in positions:
            symbol = pos["symbol"]
            instrument_type = pos.get("instrument_type", "")
            qty = pos.get("quantity", 0.0)

            if abs(qty) < 1e-9:
                skipped.append(symbol)
                continue

            price = self._fetch_quote(symbol, instrument_type)
            if price is None or price <= 0:
                failed.append(symbol)
                self._check_staleness(symbol, pos)
                continue

            try:
                self.pm.update_mark_to_market(symbol, price)
                self._last_quote_ts[symbol] = time.time()
                updated.append(symbol)
            except Exception as exc:
                logger.error(
                    "MarkToMarketService: MTM write failed for %s: %s", symbol, exc
                )
                failed.append(symbol)

        # Push total unrealized PnL into GlobalRiskMonitor
        try:
            total_unrealized = self.pm.get_total_unrealized_pnl()
            self.grm.update_unrealized_pnl(total_unrealized)
        except Exception as exc:
            logger.error(
                "MarkToMarketService: failed to update risk monitor: %s", exc
            )
            total_unrealized = 0.0

        return {
            "updated": updated,
            "failed": failed,
            "skipped": skipped,
            "total_unrealized_pnl": total_unrealized,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        """Main polling loop — runs in daemon thread."""
        logger.info("MarkToMarketService: poll thread started.")
        while not self._stop_event.is_set():
            try:
                result = self.run_once()
                logger.debug(
                    "MarkToMarketService: cycle done — updated=%d failed=%d unrealized_pnl=%.2f",
                    len(result["updated"]),
                    len(result["failed"]),
                    result["total_unrealized_pnl"],
                )
            except Exception as exc:
                logger.error("MarkToMarketService: unexpected poll error: %s", exc)

            # Interruptible sleep
            elapsed = 0.0
            while elapsed < self._poll_interval and not self._stop_event.is_set():
                time.sleep(min(5.0, self._poll_interval - elapsed))
                elapsed += 5.0

        logger.info("MarkToMarketService: poll thread exiting.")

    def _fetch_quote(self, symbol: str, instrument_type: str) -> Optional[float]:
        """
        Resolve broker for this symbol and call get_quote(symbol).

        Returns None if no broker resolver is configured or if the call fails.
        """
        if self.broker_resolver is None:
            # No resolver configured — service runs in pass-through / no-op mode
            return None

        try:
            broker = self.broker_resolver(symbol, instrument_type)
            if broker is None:
                logger.warning(
                    "MarkToMarketService: broker_resolver returned None for %s.", symbol
                )
                return None
            price = broker.get_quote(symbol)
            return float(price) if price and float(price) > 0 else None
        except Exception as exc:
            logger.warning(
                "MarkToMarketService: get_quote failed for %s: %s", symbol, exc
            )
            return None

    def _check_staleness(self, symbol: str, pos: Dict[str, Any]) -> None:
        """Emit a warning if MTM data for a symbol is older than max_stale_s."""
        last_ok = self._last_quote_ts.get(symbol)
        stored_ts = pos.get("mtm_updated_ts", 0) or 0

        # Use the more recent of our in-process tracker and the stored DB timestamp
        reference_ts = max(last_ok or 0.0, float(stored_ts))
        if reference_ts == 0.0:
            return  # Never quoted — not a staleness issue yet

        age_s = time.time() - reference_ts
        if age_s > self._max_stale_s:
            logger.warning(
                "MarkToMarketService: %s MTM price is stale (%.0fs old, limit=%ss). "
                "Retaining last known price.",
                symbol,
                age_s,
                self._max_stale_s,
            )
