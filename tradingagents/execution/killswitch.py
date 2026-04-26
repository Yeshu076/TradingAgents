from __future__ import annotations
"""
Module: killswitch.py
Part of the execution subsystem.

EmergencyKillSwitch — listens on the Redis SYSTEM_HALT pub-sub channel and
synchronously cancels all open broker orders when triggered.

Key design decisions:
 - Fully synchronous: no async/await. All broker cancel calls are blocking.
 - Redis-optional: degrades gracefully if Redis is unavailable.
 - Manually triggerable: call trigger_manual() in tests or from code.
 - Thread-safe: uses threading.Event for halted state.
 - Config-driven: REDIS_HOST, REDIS_PORT, REDIS_PASSWORD,
   KILLSWITCH_CANCEL_TIMEOUT_SECONDS.

Configuration env vars:
  REDIS_HOST                       - Redis hostname (default: 127.0.0.1)
  REDIS_PORT                       - Redis port (default: 6379)
  REDIS_PASSWORD                   - Redis auth password (optional)
  KILLSWITCH_CANCEL_TIMEOUT_SECONDS - Max seconds for each broker cancel call (default: 10)
"""

import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger("killswitch")


class EmergencyKillSwitch:
    """
    Subscribes to the Redis ``SYSTEM_HALT`` channel and, upon receipt,
    synchronously cancels all open orders across every known broker and
    marks the system as halted.

    The class is safe to instantiate even when Redis is unavailable:
    it logs a warning and the Redis-backed listener is simply a no-op.
    ``trigger_manual()`` always works regardless of Redis state.
    """

    def __init__(self) -> None:
        self.cancel_timeout: int = int(
            os.getenv("KILLSWITCH_CANCEL_TIMEOUT_SECONDS", "10")
        )
        self._halt_event = threading.Event()

        # Redis is optional — connect lazily and fail gracefully.
        self._redis_client = None
        self._pubsub = None
        self._setup_redis()

        # Import here to avoid circular imports at module load time.
        from tradingagents.execution.router import ExecutionRouter
        from tradingagents.execution.position_manager import PositionManager

        self._router = ExecutionRouter.get_instance()
        self._wallet = PositionManager.from_env()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def listen_for_fatal_events(self) -> None:
        """
        Blocking loop. Subscribe to Redis SYSTEM_HALT and call
        _execute_emergency_shutdown() synchronously on receipt.

        Call this from a dedicated daemon thread (see start_killswitch_thread()).
        Returns immediately if Redis is not available.
        """
        if self._pubsub is None:
            logger.warning(
                "[KILLSWITCH] Redis pub-sub unavailable. "
                "Redis-based halt events will not be received. "
                "Use trigger_manual() for programmatic shutdown."
            )
            return

        self._pubsub.subscribe("SYSTEM_HALT")
        logger.info("[KILLSWITCH] Armed. Listening for SYSTEM_HALT events on Redis...")

        try:
            for message in self._pubsub.listen():
                if self._halt_event.is_set():
                    logger.info("[KILLSWITCH] Already halted — stopping listener loop.")
                    break

                if message.get("type") != "message":
                    continue

                raw = message.get("data", "{}")
                try:
                    payload: Dict[str, Any] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    payload = {}

                reason = str(payload.get("reason", "UNKNOWN_FATAL_ERROR"))
                logger.critical("[KILLSWITCH] TRIGGERED via Redis. Reason: %s", reason)

                try:
                    from tradingagents.ops.notifier import send_critical_alert
                    send_critical_alert(
                        summary=f"SYSTEM HALT INITIATED: {reason}",
                        source="EmergencyKillSwitch",
                        details={"payload": payload},
                    )
                except Exception as notify_exc:  # noqa: BLE001
                    logger.error("[KILLSWITCH] Notifier failed: %s", notify_exc)

                self._execute_emergency_shutdown()

        except Exception as exc:  # noqa: BLE001
            logger.error("[KILLSWITCH] Listener loop terminated unexpectedly: %s", exc)

    def trigger_manual(self, reason: str = "MANUAL_TRIGGER") -> None:
        """
        Programmatically trigger emergency shutdown without waiting for a
        Redis message. Useful for testing and code-level circuit breakers.

        Also publishes SYSTEM_HALT to Redis (if connected) so that any
        other kill switch listeners on the bus also receive the event.
        """
        logger.critical("[KILLSWITCH] Manual trigger invoked. Reason: %s", reason)

        if self._redis_client is not None:
            try:
                self._redis_client.set("EXECUTION_BLOCKED", "1")
                self._redis_client.publish(
                    "SYSTEM_HALT", json.dumps({"reason": reason})
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[KILLSWITCH] Could not publish to Redis during manual trigger: %s", exc
                )

        self._execute_emergency_shutdown()

    def is_halted(self) -> bool:
        """Return True if emergency shutdown has been executed on this instance."""
        return self._halt_event.is_set()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _setup_redis(self) -> None:
        """
        Attempt to connect to Redis. Fails gracefully if Redis is
        not installed or not reachable.
        """
        try:
            import redis  # type: ignore[import]
        except ImportError:
            logger.warning(
                "[KILLSWITCH] 'redis' package not installed. "
                "Redis-based halt events disabled."
            )
            return

        host = os.getenv("REDIS_HOST", "127.0.0.1")
        port = int(os.getenv("REDIS_PORT", "6379"))
        password = os.getenv("REDIS_PASSWORD") or None

        try:
            client = redis.Redis(
                host=host,
                port=port,
                password=password,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            client.ping()
            self._redis_client = client
            self._pubsub = client.pubsub()
            logger.info(
                "[KILLSWITCH] Redis connected at %s:%d.", host, port
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[KILLSWITCH] Redis connection failed (%s). "
                "Redis-based halt events disabled.",
                exc,
            )

    def _execute_emergency_shutdown(self) -> None:
        """
        Synchronous three-phase emergency shutdown:

        Phase 1 — Block new order generation via Redis EXECUTION_BLOCKED flag.
        Phase 2 — Cancel all open orders across every known broker.
        Phase 3 — Mark system as halted.

        This method is idempotent: safe to call multiple times.
        """
        if self._halt_event.is_set():
            logger.warning("[KILLSWITCH] Shutdown already in progress. Skipping duplicate call.")
            return

        # Phase 1: Block new order generation.
        logger.warning("[KILLSWITCH] Phase 1: Blocking new order generation...")
        if self._redis_client is not None:
            try:
                self._redis_client.set("EXECUTION_BLOCKED", "1")
                logger.info("[KILLSWITCH] EXECUTION_BLOCKED flag set in Redis.")
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[KILLSWITCH] Failed to set EXECUTION_BLOCKED in Redis: %s", exc
                )

        # Phase 2: Cancel all open orders.
        logger.warning("[KILLSWITCH] Phase 2: Cancelling all open orders across brokers...")
        cancelled, failed = self._cancel_all_broker_orders()

        # Phase 3: Mark system as halted.
        self._halt_event.set()
        logger.critical(
            "[KILLSWITCH] Phase 3: System is now in SAFE MODE. "
            "Broker cancel results — successful: %d, failed/unsupported: %d. "
            "Manual intervention required to resume trading.",
            cancelled,
            failed,
        )

    def _cancel_all_broker_orders(self) -> tuple[int, int]:
        """
        Read open positions from the paper wallet, resolve each to its
        live broker, and call cancel_all_orders() once per broker.

        Returns (cancelled_count, failed_count).
        """
        try:
            open_positions: List[Dict[str, Any]] = self._wallet.get_positions()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[KILLSWITCH] Could not read open positions from wallet: %s. "
                "Attempting cancel on all known brokers instead.",
                exc,
            )
            open_positions = []

        # Group by resolved broker name to deduplicate calls.
        broker_registry: Dict[str, Any] = {}

        for pos in open_positions:
            symbol = str(pos.get("symbol", "")).strip()
            inst_type = str(pos.get("instrument_type", "options")).strip()
            if not symbol:
                continue
            try:
                broker = self._router.resolve("auto", inst_type, symbol)
                if broker.name not in broker_registry:
                    broker_registry[broker.name] = broker
                    logger.debug(
                        "[KILLSWITCH] Will cancel on broker '%s' (symbol=%s).",
                        broker.name, symbol,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[KILLSWITCH] Could not resolve broker for %s (%s): %s",
                    symbol, inst_type, exc,
                )

        # If no positions were found, attempt cancel on all known brokers
        # as a belt-and-suspenders measure.
        if not broker_registry:
            logger.warning(
                "[KILLSWITCH] No open positions found in wallet. "
                "Attempting cancel on all configured brokers."
            )
            broker_registry = self._instantiate_all_known_brokers()

        cancelled = 0
        failed = 0

        for broker_name, broker in broker_registry.items():
            if not hasattr(broker, "cancel_all_orders"):
                logger.warning(
                    "[KILLSWITCH] Broker '%s' has no cancel_all_orders method. Skipping.",
                    broker_name,
                )
                failed += 1
                continue

            try:
                result = broker.cancel_all_orders()
                if isinstance(result, dict) and result.get("status") == "not_supported":
                    logger.warning(
                        "[KILLSWITCH] Broker '%s' does not support cancel_all_orders: %s",
                        broker_name,
                        result.get("message", ""),
                    )
                    failed += 1
                else:
                    logger.info(
                        "[KILLSWITCH] cancel_all_orders succeeded on broker '%s': %s",
                        broker_name,
                        result,
                    )
                    cancelled += 1
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[KILLSWITCH] cancel_all_orders FAILED on broker '%s': %s",
                    broker_name,
                    exc,
                )
                failed += 1

        return cancelled, failed

    def _instantiate_all_known_brokers(self) -> Dict[str, Any]:
        """
        Attempt to instantiate all known broker types as a fallback when
        the wallet has no open positions to guide broker selection.
        """
        brokers: Dict[str, Any] = {}
        known = ["delta", "dhan", "mt5_forex"]
        for name in known:
            try:
                brokers[name] = self._router._instantiate_broker(name)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[KILLSWITCH] Could not instantiate broker '%s': %s", name, exc
                )
        return brokers


def start_killswitch_thread() -> EmergencyKillSwitch:
    """
    Convenience factory: creates an EmergencyKillSwitch and starts its
    Redis listener in a background daemon thread.

    Returns the EmergencyKillSwitch instance so the caller can also use
    trigger_manual() and is_halted().

    Example usage (in daemon.py):
        from tradingagents.execution.killswitch import start_killswitch_thread
        ks = start_killswitch_thread()
    """
    ks = EmergencyKillSwitch()
    listener_thread = threading.Thread(
        target=ks.listen_for_fatal_events,
        daemon=True,
        name="killswitch-listener",
    )
    listener_thread.start()
    logger.info("[KILLSWITCH] Listener thread started (daemon=True).")
    return ks
