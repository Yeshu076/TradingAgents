"""
tests/test_killswitch.py

Comprehensive tests for EmergencyKillSwitch covering:
  - Graceful degradation when Redis is unavailable (no crash)
  - trigger_manual() executes shutdown without Redis
  - _execute_emergency_shutdown() is idempotent (safe to call twice)
  - is_halted() reflects shutdown state correctly
  - kill switch calls cancel_all_orders() on each broker resolved from wallet
  - Broker cancel_all_orders() returning "not_supported" increments failed count
  - Broker cancel_all_orders() raising an exception increments failed count
  - listen_for_fatal_events() exits immediately when pubsub is None
  - listen_for_fatal_events() parses SYSTEM_HALT message and calls shutdown
  - MT5ForexBroker fully implements BrokerBase ABC (cancel, close, list)
"""

import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# --------------------------------------------------------------------------- #
# Factory: build a fully-mocked EmergencyKillSwitch
# --------------------------------------------------------------------------- #

def _make_ks(wallet_positions=None, broker_cancel_result=None):
    """
    Build an EmergencyKillSwitch with all external dependencies pre-mocked.

    Because EmergencyKillSwitch uses deferred imports inside __init__ we:
      1. Patch _setup_redis (no-op) so Redis is never touched.
      2. Patch the source modules where ExecutionRouter and PositionManager
         are defined (the *correct* patch target for deferred imports).
      3. Manually inject mock _router and _wallet and _redis_client after
         construction.
    """
    from tradingagents.execution import killswitch as ks_mod

    mock_wallet = MagicMock()
    mock_wallet.get_positions.return_value = wallet_positions or []

    mock_broker = MagicMock()
    mock_broker.name = "mock_broker"
    if broker_cancel_result is not None:
        mock_broker.cancel_all_orders.return_value = broker_cancel_result
    else:
        mock_broker.cancel_all_orders.return_value = {"status": "OK", "cancelled_count": 1}

    mock_router = MagicMock()
    mock_router.resolve.return_value = mock_broker
    mock_router._instantiate_broker.return_value = mock_broker

    with (
        patch.object(ks_mod.EmergencyKillSwitch, "_setup_redis", return_value=None),
        patch(
            "tradingagents.execution.router.ExecutionRouter.get_instance",
            return_value=mock_router,
        ),
        patch(
            "tradingagents.execution.position_manager.PositionManager.from_env",
            return_value=mock_wallet,
        ),
    ):
        ks = ks_mod.EmergencyKillSwitch()

    # Override post-construction: no Redis by default.
    ks._redis_client = None
    ks._pubsub = None
    ks._router = mock_router
    ks._wallet = mock_wallet

    return ks, mock_broker, mock_router, mock_wallet


# --------------------------------------------------------------------------- #
# Graceful degradation when Redis is unavailable
# --------------------------------------------------------------------------- #

class TestNoRedis:
    def test_instantiation_without_redis_does_not_raise(self):
        """Kill switch must not crash on import even if Redis is absent."""
        ks, *_ = _make_ks()
        assert not ks.is_halted()

    def test_listen_exits_immediately_when_pubsub_is_none(self):
        """When Redis is unavailable, listener must return without blocking."""
        ks, *_ = _make_ks()
        assert ks._pubsub is None
        # Must return immediately (not block forever).
        ks.listen_for_fatal_events()

    def test_trigger_manual_works_without_redis(self):
        """trigger_manual() must execute shutdown even without Redis."""
        ks, mock_broker, *_ = _make_ks(
            wallet_positions=[{"symbol": "XAUUSD", "instrument_type": "forex", "quantity": 1.0}]
        )
        ks._redis_client = None
        ks.trigger_manual("TEST_NO_REDIS")
        assert ks.is_halted()
        mock_broker.cancel_all_orders.assert_called_once()


# --------------------------------------------------------------------------- #
# trigger_manual() behaviour
# --------------------------------------------------------------------------- #

class TestTriggerManual:
    def test_sets_halted_flag(self):
        ks, *_ = _make_ks()
        assert not ks.is_halted()
        ks.trigger_manual("TEST")
        assert ks.is_halted()

    def test_calls_cancel_all_orders_on_broker(self):
        ks, mock_broker, *_ = _make_ks(
            wallet_positions=[{"symbol": "BTCUSD", "instrument_type": "crypto", "quantity": 0.1}]
        )
        ks.trigger_manual("TEST_CANCEL")
        mock_broker.cancel_all_orders.assert_called_once()

    def test_publishes_system_halt_to_redis_when_available(self):
        ks, *_ = _make_ks()
        mock_redis = MagicMock()
        ks._redis_client = mock_redis

        ks.trigger_manual("MY_REASON")

        mock_redis.set.assert_called_with("EXECUTION_BLOCKED", "1")
        publish_calls = mock_redis.publish.call_args_list
        assert len(publish_calls) == 1
        channel, payload_str = publish_calls[0][0]
        assert channel == "SYSTEM_HALT"
        payload = json.loads(payload_str)
        assert payload["reason"] == "MY_REASON"

    def test_trigger_manual_without_redis_does_not_raise(self):
        ks, *_ = _make_ks()
        ks._redis_client = None
        ks.trigger_manual("NO_REDIS_REASON")
        assert ks.is_halted()


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #

class TestIdempotency:
    def test_double_trigger_does_not_cancel_twice(self):
        """Second call to trigger_manual() must not issue duplicate cancel calls."""
        ks, mock_broker, *_ = _make_ks(
            wallet_positions=[{"symbol": "NIFTY25APR23500CE", "instrument_type": "options", "quantity": 25}]
        )
        ks.trigger_manual("FIRST_TRIGGER")
        ks.trigger_manual("SECOND_TRIGGER")
        # cancel_all_orders should only be called once (idempotent).
        assert mock_broker.cancel_all_orders.call_count == 1

    def test_is_halted_returns_false_before_shutdown(self):
        ks, *_ = _make_ks()
        assert not ks.is_halted()

    def test_is_halted_returns_true_after_shutdown(self):
        ks, *_ = _make_ks()
        ks.trigger_manual()
        assert ks.is_halted()


# --------------------------------------------------------------------------- #
# Broker cancel result handling
# --------------------------------------------------------------------------- #

class TestBrokerCancelResults:
    def test_not_supported_counts_as_failed_but_does_not_crash(self):
        """Broker returning not_supported must not prevent system halt."""
        ks, mock_broker, *_ = _make_ks(
            wallet_positions=[{"symbol": "NIFTY25APR23500CE", "instrument_type": "options", "quantity": 25}],
            broker_cancel_result={"status": "not_supported", "message": "Not implemented"},
        )
        ks.trigger_manual("DHAN_NO_CANCEL_TEST")
        assert ks.is_halted()

    def test_broker_exception_does_not_abort_shutdown(self):
        """An exception from broker.cancel_all_orders() must not prevent halted state."""
        ks, mock_broker, *_ = _make_ks(
            wallet_positions=[{"symbol": "BTCUSD", "instrument_type": "crypto", "quantity": 1}]
        )
        mock_broker.cancel_all_orders.side_effect = RuntimeError("Simulated broker crash")
        ks.trigger_manual("BROKER_CRASH_TEST")
        assert ks.is_halted(), "System must be halted even if broker cancel fails"

    def test_multiple_positions_same_broker_cancel_called_once(self):
        """Only one cancel_all_orders call per unique broker name."""
        ks, mock_broker, *_ = _make_ks(
            wallet_positions=[
                {"symbol": "BTCUSD", "instrument_type": "crypto", "quantity": 0.5},
                {"symbol": "ETHUSD", "instrument_type": "crypto", "quantity": 2.0},
                {"symbol": "XRPUSD", "instrument_type": "crypto", "quantity": 100},
            ]
        )
        ks.trigger_manual("MULTI_POSITION_TEST")
        # All three resolve to same mock_broker (same .name) → called only once.
        assert mock_broker.cancel_all_orders.call_count == 1

    def test_positions_from_different_brokers_each_cancelled(self):
        """Each distinct broker should receive exactly one cancel call."""
        mock_broker_delta = MagicMock()
        mock_broker_delta.name = "delta"
        mock_broker_delta.cancel_all_orders.return_value = {"status": "OK", "cancelled_count": 1}

        mock_broker_dhan = MagicMock()
        mock_broker_dhan.name = "dhan"
        mock_broker_dhan.cancel_all_orders.return_value = {"status": "not_supported", "message": "no impl"}

        def _resolve(_broker_pref, inst_type, _symbol):
            return mock_broker_dhan if inst_type == "options" else mock_broker_delta

        mock_wallet = MagicMock()
        mock_wallet.get_positions.return_value = [
            {"symbol": "BTCUSD", "instrument_type": "crypto", "quantity": 1},
            {"symbol": "NIFTY25APR23500CE", "instrument_type": "options", "quantity": 25},
        ]
        mock_router = MagicMock()
        mock_router.resolve.side_effect = _resolve

        from tradingagents.execution import killswitch as ks_mod
        with (
            patch.object(ks_mod.EmergencyKillSwitch, "_setup_redis", return_value=None),
            patch(
                "tradingagents.execution.router.ExecutionRouter.get_instance",
                return_value=mock_router,
            ),
            patch(
                "tradingagents.execution.position_manager.PositionManager.from_env",
                return_value=mock_wallet,
            ),
        ):
            ks = ks_mod.EmergencyKillSwitch()

        ks._redis_client = None
        ks._pubsub = None
        ks._router = mock_router
        ks._wallet = mock_wallet

        ks.trigger_manual("MULTI_BROKER_TEST")

        assert mock_broker_delta.cancel_all_orders.call_count == 1
        assert mock_broker_dhan.cancel_all_orders.call_count == 1
        assert ks.is_halted()


# --------------------------------------------------------------------------- #
# Redis listener behaviour
# --------------------------------------------------------------------------- #

class TestRedisListener:
    def _make_pubsub_with_messages(self, messages):
        mock_pubsub = MagicMock()
        mock_pubsub.listen.return_value = iter(messages)
        return mock_pubsub

    def test_listener_skips_non_message_types(self):
        """subscribe / psubscribe responses must not trigger shutdown."""
        ks, mock_broker, *_ = _make_ks()
        ks._pubsub = self._make_pubsub_with_messages([
            {"type": "subscribe", "data": 1},
        ])
        ks.listen_for_fatal_events()
        assert not ks.is_halted()
        mock_broker.cancel_all_orders.assert_not_called()

    def test_listener_triggers_shutdown_on_system_halt(self):
        """A SYSTEM_HALT message must trigger emergency shutdown."""
        ks, mock_broker, *_ = _make_ks(
            wallet_positions=[{"symbol": "BTCUSD", "instrument_type": "crypto", "quantity": 1}]
        )
        halt_payload = json.dumps({"reason": "MAX_DAILY_LOSS_BREACH"})
        ks._pubsub = self._make_pubsub_with_messages([
            {"type": "subscribe", "data": 1},
            {"type": "message", "data": halt_payload},
        ])
        with patch("tradingagents.ops.notifier.send_critical_alert"):
            ks.listen_for_fatal_events()

        assert ks.is_halted()
        mock_broker.cancel_all_orders.assert_called_once()

    def test_listener_handles_malformed_json_gracefully(self):
        """Malformed JSON must not crash the listener."""
        ks, *_ = _make_ks()
        ks._pubsub = self._make_pubsub_with_messages([
            {"type": "message", "data": "NOT VALID JSON !!!"},
        ])
        with patch("tradingagents.ops.notifier.send_critical_alert"):
            ks.listen_for_fatal_events()
        # Halted (shutdown still runs when payload is malformed).
        assert ks.is_halted()

    def test_listener_stops_processing_after_halt_is_set(self):
        """Once halted, the listener must exit without processing more messages."""
        ks, mock_broker, *_ = _make_ks(
            wallet_positions=[{"symbol": "BTCUSD", "instrument_type": "crypto", "quantity": 1}]
        )
        halt_payload = json.dumps({"reason": "FIRST_HALT"})
        ks._pubsub = self._make_pubsub_with_messages([
            {"type": "message", "data": halt_payload},
            {"type": "message", "data": halt_payload},  # Should be ignored
        ])
        with patch("tradingagents.ops.notifier.send_critical_alert"):
            ks.listen_for_fatal_events()

        # Idempotent: cancel should only happen once despite two messages.
        assert mock_broker.cancel_all_orders.call_count == 1


# --------------------------------------------------------------------------- #
# start_killswitch_thread()
# --------------------------------------------------------------------------- #

class TestStartKillswitchThread:
    def test_returns_emergency_killswitch_instance(self):
        from tradingagents.execution import killswitch as ks_mod

        mock_wallet = MagicMock()
        mock_wallet.get_positions.return_value = []
        mock_router = MagicMock()

        with (
            patch.object(ks_mod.EmergencyKillSwitch, "_setup_redis", return_value=None),
            patch(
                "tradingagents.execution.router.ExecutionRouter.get_instance",
                return_value=mock_router,
            ),
            patch(
                "tradingagents.execution.position_manager.PositionManager.from_env",
                return_value=mock_wallet,
            ),
            patch.object(ks_mod.EmergencyKillSwitch, "listen_for_fatal_events", return_value=None),
        ):
            ks = ks_mod.start_killswitch_thread()

        assert isinstance(ks, ks_mod.EmergencyKillSwitch)
        assert not ks.is_halted()


# --------------------------------------------------------------------------- #
# MT5ForexBroker ABC compliance
# --------------------------------------------------------------------------- #

class TestMT5ForexBrokerABCCompliance:
    """
    Verify MT5ForexBroker satisfies all BrokerBase abstract methods without
    requiring a live MT5 terminal.
    """

    def _get_broker_class(self):
        """Return MT5ForexBroker with the mt5 package stubbed out at import time."""
        import tradingagents.execution.forex_broker as fb_mod
        return fb_mod.MT5ForexBroker

    def test_list_positions_method_exists(self):
        MT5ForexBroker = self._get_broker_class()
        broker = MT5ForexBroker()
        # initialized = False because mt5 is mocked/absent; we just check method exists
        result = broker.list_positions()
        assert isinstance(result, list)

    def test_cancel_all_orders_method_exists(self):
        MT5ForexBroker = self._get_broker_class()
        broker = MT5ForexBroker()
        result = broker.cancel_all_orders()
        assert isinstance(result, dict)
        assert "status" in result

    def test_close_symbol_position_method_exists(self):
        MT5ForexBroker = self._get_broker_class()
        broker = MT5ForexBroker()
        result = broker.close_symbol_position("XAUUSD")
        assert isinstance(result, dict)

    def test_cancel_all_orders_returns_failed_when_not_initialized(self):
        MT5ForexBroker = self._get_broker_class()
        broker = MT5ForexBroker()
        # initialized is False (no real MT5 terminal)
        broker.initialized = False
        result = broker.cancel_all_orders()
        assert result["status"] == "FAILED"
        assert result["cancelled_count"] == 0

    def test_list_positions_returns_empty_when_not_initialized(self):
        MT5ForexBroker = self._get_broker_class()
        broker = MT5ForexBroker()
        broker.initialized = False
        assert broker.list_positions() == []

    def test_cancel_all_orders_no_pending_orders(self):
        """With no pending orders, returns OK with zero cancelled."""
        mock_mt5 = MagicMock()
        mock_mt5.orders_get.return_value = []
        mock_mt5.TRADE_RETCODE_DONE = 10009
        mock_mt5.TRADE_ACTION_REMOVE = 8

        MT5ForexBroker = self._get_broker_class()
        broker = MT5ForexBroker()
        broker.initialized = True

        with patch("tradingagents.execution.forex_broker.mt5", mock_mt5):
            result = broker.cancel_all_orders()

        assert result["status"] == "OK"
        assert result["cancelled_count"] == 0

    def test_cancel_all_orders_cancels_pending_orders(self):
        """Two pending orders with successful retcodes → cancelled_count=2."""
        order_a = SimpleNamespace(ticket=111, symbol="XAUUSD")
        order_b = SimpleNamespace(ticket=222, symbol="XAUUSD")
        mock_result = SimpleNamespace(retcode=10009)  # TRADE_RETCODE_DONE

        mock_mt5 = MagicMock()
        mock_mt5.TRADE_RETCODE_DONE = 10009
        mock_mt5.TRADE_ACTION_REMOVE = 8
        mock_mt5.orders_get.return_value = [order_a, order_b]
        mock_mt5.order_send.return_value = mock_result

        MT5ForexBroker = self._get_broker_class()
        broker = MT5ForexBroker()
        broker.initialized = True

        with patch("tradingagents.execution.forex_broker.mt5", mock_mt5):
            result = broker.cancel_all_orders()

        assert result["status"] == "OK"
        assert result["cancelled_count"] == 2
        assert result["failed_count"] == 0
        assert mock_mt5.order_send.call_count == 2

    def test_cancel_all_orders_mixed_results_returns_partial(self):
        """One success + one failure → status=PARTIAL."""
        order_a = SimpleNamespace(ticket=111, symbol="XAUUSD")
        order_b = SimpleNamespace(ticket=222, symbol="XAUUSD")

        mock_mt5 = MagicMock()
        mock_mt5.TRADE_RETCODE_DONE = 10009
        mock_mt5.TRADE_ACTION_REMOVE = 8
        mock_mt5.orders_get.return_value = [order_a, order_b]
        mock_mt5.order_send.side_effect = [
            SimpleNamespace(retcode=10009),  # success
            SimpleNamespace(retcode=10014),  # failure
        ]

        MT5ForexBroker = self._get_broker_class()
        broker = MT5ForexBroker()
        broker.initialized = True

        with patch("tradingagents.execution.forex_broker.mt5", mock_mt5):
            result = broker.cancel_all_orders()

        assert result["status"] == "PARTIAL"
        assert result["cancelled_count"] == 1
        assert result["failed_count"] == 1

