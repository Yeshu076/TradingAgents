"""
Module: session_manager.py
Part of the execution subsystem.

BrokerSessionManager — synchronous, threaded implementation.

Previously async (dead code in a sync codebase). Rewritten as a background
daemon thread that checks token health every N seconds and refreshes when
within the warning window.

GAP-12 fix: All async def / await removed. Runs in a persistent daemon
thread started by start() — compatible with the sync execution engine and
the APScheduler daemon.

GAP-19 integration: Works alongside check_dhan_token_health() in daemon.py.
This manager handles runtime refreshes; the daemon job handles pre-market
alerting.
"""

import logging
import os
import threading
import time
from typing import Any, Optional

logger = logging.getLogger("tradingagents.execution.session_manager")

_ENV_CHECK_INTERVAL   = "TRADINGAGENTS_SESSION_CHECK_INTERVAL_S"   # default 300 (5 min)
_ENV_REFRESH_WINDOW   = "TRADINGAGENTS_SESSION_REFRESH_WINDOW_S"    # default 1800 (30 min)
_ENV_SESSION_ENABLED  = "TRADINGAGENTS_SESSION_MANAGER_ENABLED"     # default true


class BrokerSessionManager:
    """
    Monitors broker authentication tokens in a background daemon thread.

    Instead of failing mid-trade with a 401 Unauthorized, this manager
    proactively refreshes keys before the token expires.

    Synchronous — no event loop required. Starts a daemon thread that calls
    broker.refresh_authentication() when the token is within the refresh window.

    Usage:
        manager = BrokerSessionManager(broker_client, initial_expiry=expiry_ts)
        manager.start()          # non-blocking — starts background thread
        ...
        manager.stop()           # graceful shutdown
        manager.join(timeout=5)  # wait for thread exit
    """

    def __init__(
        self,
        broker_client: Any,
        initial_expiry: float = 0.0,
    ):
        import os
        self.broker = broker_client
        self.token_expiry: float = initial_expiry
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._check_interval: float = float(
            os.environ.get(_ENV_CHECK_INTERVAL, "300")
        )
        self._refresh_window: float = float(
            os.environ.get(_ENV_REFRESH_WINDOW, "1800")
        )
        self._enabled: bool = (
            os.environ.get(_ENV_SESSION_ENABLED, "true").strip().lower() != "false"
        )

    def start(self) -> None:
        """Start the background monitor thread (idempotent)."""
        if not self._enabled:
            logger.info("BrokerSessionManager: disabled via env — not starting.")
            return
        if self._thread and self._thread.is_alive():
            logger.debug("BrokerSessionManager: monitor already running.")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop,
            name=f"session-mgr-{getattr(self.broker, 'name', 'broker')}",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "BrokerSessionManager: started for broker '%s' (check_interval=%ss, refresh_window=%ss).",
            getattr(self.broker, "name", "unknown"),
            self._check_interval,
            self._refresh_window,
        )

    def stop(self) -> None:
        """Signal the monitor thread to stop."""
        self._stop_event.set()

    def join(self, timeout: float = 5.0) -> None:
        """Wait for the monitor thread to exit."""
        if self._thread:
            self._thread.join(timeout=timeout)

    def update_expiry(self, new_expiry: float) -> None:
        """Update the tracked token expiry timestamp (epoch seconds)."""
        self.token_expiry = new_expiry
        logger.debug(
            "BrokerSessionManager: token_expiry updated to %.0f (TTL %.0fs).",
            new_expiry, max(0.0, new_expiry - time.time()),
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _monitor_loop(self) -> None:
        """Main polling loop — runs in daemon thread."""
        logger.info("BrokerSessionManager: monitor thread started.")
        while not self._stop_event.is_set():
            try:
                if self._needs_refresh():
                    logger.info(
                        "BrokerSessionManager: token expiry approaching (TTL=%.0fs). Refreshing...",
                        max(0.0, self.token_expiry - time.time()),
                    )
                    self._refresh_token()
            except Exception as e:
                logger.error("BrokerSessionManager: monitor iteration failed: %s", e)

            # Sleep in short increments so stop_event is responsive
            elapsed = 0.0
            while elapsed < self._check_interval and not self._stop_event.is_set():
                time.sleep(min(5.0, self._check_interval - elapsed))
                elapsed += 5.0

        logger.info("BrokerSessionManager: monitor thread exiting.")

    def _needs_refresh(self) -> bool:
        """Returns True if the token will expire within the refresh window."""
        if self.token_expiry == 0.0:
            return False  # Expiry not set — skip
        time_to_expiry = self.token_expiry - time.time()
        return time_to_expiry < self._refresh_window

    def _refresh_token(self) -> None:
        """Call broker.refresh_authentication() synchronously and update expiry."""
        if not hasattr(self.broker, "refresh_authentication"):
            logger.warning(
                "BrokerSessionManager: broker '%s' does not implement refresh_authentication().",
                getattr(self.broker, "name", "unknown"),
            )
            return
        try:
            new_expiry = self.broker.refresh_authentication()
            if new_expiry:
                self.update_expiry(float(new_expiry))
                logger.info("BrokerSessionManager: token refreshed. New expiry=%.0f.", float(new_expiry))
            else:
                logger.warning("BrokerSessionManager: refresh_authentication() returned nothing.")
        except Exception as e:
            logger.error("BrokerSessionManager: token refresh failed: %s", e)
