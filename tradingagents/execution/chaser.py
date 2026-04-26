"""
Module: chaser.py
Part of the execution subsystem.

OrderChaseManager — synchronous, threaded implementation.

Previously async (dead code in a sync codebase). Rewritten as a threaded
worker so it can be called from the synchronous execute_trade() pipeline.

GAP-12 fix: All async def / await removed. Uses threading.Event for
cancellation and time.sleep for polling, compatible with the sync engine.
"""

import logging
import threading
import time
from typing import Any, Optional

logger = logging.getLogger("tradingagents.execution.chaser")

# Configuration env-var names (all configurable without code changes)
_ENV_CHASE_INTERVAL = "TRADINGAGENTS_CHASE_INTERVAL_S"       # default 0.5s
_ENV_MAX_TICKS      = "TRADINGAGENTS_CHASE_MAX_TICKS"         # default 10
_ENV_CHASE_ENABLED  = "TRADINGAGENTS_ORDER_CHASE_ENABLED"     # default true


class OrderChaseResult:
    """Lightweight result object returned after chase completes."""
    def __init__(self, filled: bool, order_id: str, reason: str):
        self.filled = filled
        self.order_id = order_id
        self.reason = reason

    def __repr__(self) -> str:
        return f"OrderChaseResult(filled={self.filled}, order_id={self.order_id!r}, reason={self.reason!r})"


class OrderChaseManager:
    """
    Actively manages unfilled or partially-filled LIMIT orders.

    Instead of waiting indefinitely or firing a MARKET order at a bad spread,
    this manager 'chases' the market price by updating the LIMIT order
    sequentially up to a hard maximum slippage boundary.

    This is a synchronous implementation — safe to call from the execution
    engine without an event loop. Each chase runs in caller's thread; callers
    wishing non-blocking behaviour should spawn it in a daemon thread.

    Usage (blocking):
        chaser = OrderChaseManager(broker, symbol="BTCUSD", side="BUY",
                                   qty=0.1, initial_price=30000.0)
        result = chaser.chase(order_id="abc123")

    Usage (fire-and-forget):
        t = chaser.chase_async(order_id="abc123")  # returns Thread
    """

    def __init__(
        self,
        broker_client: Any,
        symbol: str,
        side: str,
        qty: float,
        initial_price: float,
        max_slippage_pct: float = 0.005,
    ):
        import os
        self.broker = broker_client
        self.symbol = symbol.upper()
        self.side = side.upper()
        self.qty = qty
        self.initial_price = initial_price

        # Absolute price boundaries to prevent buying a spiked candle
        if self.side == "BUY":
            self.worst_acceptable_price = initial_price * (1 + max_slippage_pct)
        else:
            self.worst_acceptable_price = initial_price * (1 - max_slippage_pct)

        self.chase_interval: float = float(os.environ.get(_ENV_CHASE_INTERVAL, "0.5"))
        self.max_chase_ticks: int = int(os.environ.get(_ENV_MAX_TICKS, "10"))
        self._stop_event = threading.Event()

    def stop(self) -> None:
        """Signal the running chase to halt at the next tick."""
        self._stop_event.set()

    def chase(self, initial_order_id: str) -> OrderChaseResult:
        """
        Synchronously chases an unfilled limit order.

        Returns:
            OrderChaseResult with filled=True on success.
        """
        current_order_id = initial_order_id

        for tick in range(self.max_chase_ticks):
            if self._stop_event.is_set():
                logger.warning("[CHASE STOPPED] External stop signal received for %s.", self.symbol)
                self._safe_cancel(current_order_id)
                return OrderChaseResult(False, current_order_id, "stopped")

            time.sleep(self.chase_interval)

            # 1. Check order status directly from the broker
            status = self._safe_get_status(current_order_id)
            if status == "FILLED":
                logger.info("[CHASE SUCCESS] Order %s filled on tick %d.", current_order_id, tick + 1)
                return OrderChaseResult(True, current_order_id, "filled")
            if status in {"CANCELLED", "REJECTED"}:
                logger.warning("[CHASE DEAD] Order %s %s by broker.", current_order_id, status)
                return OrderChaseResult(False, current_order_id, status.lower())

            # 2. Still open — fetch new best price and attempt cancel-replace
            logger.info(
                "[CHASE %d/%d] %s unfilled. Cancel-replace at new market price.",
                tick + 1, self.max_chase_ticks, current_order_id,
            )
            current_market_price = self._get_best_price()
            if not current_market_price:
                logger.debug("[CHASE] Could not fetch price on tick %d — skipping.", tick + 1)
                continue

            # 3. Guard against excessive slippage
            if self.side == "BUY" and current_market_price > self.worst_acceptable_price:
                logger.error(
                    "[CHASE ABORTED] Market %s > max BUY %s for %s.",
                    current_market_price, self.worst_acceptable_price, self.symbol,
                )
                self._safe_cancel(current_order_id)
                return OrderChaseResult(False, current_order_id, "slippage_exceeded")

            if self.side == "SELL" and current_market_price < self.worst_acceptable_price:
                logger.error(
                    "[CHASE ABORTED] Market %s < min SELL %s for %s.",
                    current_market_price, self.worst_acceptable_price, self.symbol,
                )
                self._safe_cancel(current_order_id)
                return OrderChaseResult(False, current_order_id, "slippage_exceeded")

            # 4. Cancel and replace
            self._safe_cancel(current_order_id)
            new_id = self._safe_replace(current_market_price)
            if not new_id:
                logger.error("[CHASE] Replacement order failed — abandoning chase for %s.", self.symbol)
                return OrderChaseResult(False, current_order_id, "replace_failed")
            current_order_id = new_id

        # Loop exhausted
        logger.warning(
            "[CHASE ABANDONED] Max ticks exhausted for %s. Cancelling orphan %s.",
            self.symbol, current_order_id,
        )
        self._safe_cancel(current_order_id)
        return OrderChaseResult(False, current_order_id, "ticks_exhausted")

    def chase_async(self, initial_order_id: str) -> threading.Thread:
        """
        Non-blocking wrapper — spawns a daemon thread and returns it.
        Callers can join() or simply let it run until completion.
        """
        t = threading.Thread(
            target=self.chase,
            args=(initial_order_id,),
            name=f"chaser-{self.symbol}-{initial_order_id}",
            daemon=True,
        )
        t.start()
        return t

    # ------------------------------------------------------------------
    # Internal helpers — synchronous broker calls
    # ------------------------------------------------------------------

    def _safe_get_status(self, order_id: str) -> str:
        """Get order status; returns 'UNKNOWN' on any error."""
        try:
            if hasattr(self.broker, "get_order_status"):
                return str(self.broker.get_order_status(order_id)).upper()
            return "UNKNOWN"
        except Exception as e:
            logger.debug("[CHASE] get_order_status(%s) failed: %s", order_id, e)
            return "UNKNOWN"

    def _get_best_price(self) -> float:
        """Fetch current best price using the broker's get_quote() if available."""
        try:
            if hasattr(self.broker, "get_quote"):
                quote = self.broker.get_quote(self.symbol)
                if self.side == "BUY":
                    return float(quote.get("ask") or quote.get("last") or 0.0)
                return float(quote.get("bid") or quote.get("last") or 0.0)
            if hasattr(self.broker, "get_latest_price"):
                return float(self.broker.get_latest_price(self.symbol))
        except Exception as e:
            logger.debug("[CHASE] Price fetch failed for %s: %s", self.symbol, e)
        return 0.0

    def _safe_cancel(self, order_id: str) -> None:
        """Cancel an order; logs but does not propagate errors."""
        try:
            if hasattr(self.broker, "cancel_order"):
                self.broker.cancel_order(order_id)
        except Exception as e:
            logger.error("[CHASE] cancel_order(%s) failed: %s", order_id, e)

    def _safe_replace(self, new_price: float) -> str:
        """Place a replacement limit order; returns new order id or empty string."""
        try:
            if hasattr(self.broker, "place_limit_order"):
                resp = self.broker.place_limit_order(
                    symbol=self.symbol,
                    side=self.side,
                    qty=self.qty,
                    price=new_price,
                )
                return str(resp.get("id") or resp.get("order_id") or "")
        except Exception as e:
            logger.error("[CHASE] place_limit_order at %s failed: %s", new_price, e)
        return ""
