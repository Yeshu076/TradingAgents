"""
tests/test_e2e_broker_chaos.py

GAP-25: Broker fault-injection (chaos) integration tests.

Exercises the execution engine's behaviour under:
  - broker timeout / exception
  - broker returning unexpected response shapes
  - slippage guard firing
  - liveness monitor blocking
  - OrderChaseManager sync behaviour
  - MarginValidator sync integration
"""
import pytest
from unittest.mock import MagicMock, patch

from tradingagents.execution.models import TradeIntent
from tradingagents.execution.engine import execute_trade


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def chaos_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGAGENTS_ALLOW_LIVE", "true")
    monkeypatch.setenv("TRADINGAGENTS_LIVENESS_CHECK_ENABLED", "false")
    monkeypatch.setenv("TRADINGAGENTS_CORRELATION_CHECK_ENABLED", "false")
    monkeypatch.setenv("TRADINGAGENTS_MARGIN_CHECK_ENABLED", "false")
    monkeypatch.setenv("TRADINGAGENTS_EXECUTION_IDEMPOTENCY_ENABLED", "false")
    monkeypatch.setenv("TRADINGAGENTS_DB_PATH", str(tmp_path / "chaos.db"))
    monkeypatch.setenv("TRADINGAGENTS_JOURNAL_PATH", str(tmp_path / "chaos.jsonl"))
    monkeypatch.setenv("TRADINGAGENTS_MAX_ORDER_QTY", "10000")
    monkeypatch.setenv("TRADINGAGENTS_MAX_ORDER_NOTIONAL", "10000000")
    monkeypatch.setenv("TRADINGAGENTS_ENFORCE_MARKET_HOURS", "false")


def _live_intent(symbol="BTCUSD", qty=0.1, price=30000.0) -> TradeIntent:
    return TradeIntent(
        symbol=symbol,
        instrument_type="spot",
        signal="BUY",
        quantity=qty,
        suggested_entry=price,
    )


def _mock_global_risk():
    m = MagicMock()
    m.get_instance.return_value.evaluate_trade_intent.return_value = True
    m.get_instance.return_value.report_trade_execution.return_value = None
    return m


# ---------------------------------------------------------------------------
# 1. Broker timeout / exception propagates gracefully
# ---------------------------------------------------------------------------

class TestBrokerTimeout:
    def test_broker_exception_propagates(self):
        intent = _live_intent()
        broker_mock = MagicMock()
        broker_mock.name = "delta"
        broker_mock.get_quote.return_value = {"bid": 30000, "ask": 30000, "last": 30000}
        broker_mock.place_market_order.side_effect = RuntimeError("broker API timeout")

        with patch("tradingagents.execution.engine.GlobalRiskMonitor", _mock_global_risk()), \
             patch("tradingagents.execution.engine.resolve_broker", return_value=broker_mock), \
             patch("tradingagents.execution.engine.DataLivenessMonitor") as mock_live:
            mock_live.get_instance.return_value.validate_all_feeds_or_halt.return_value = True

            with pytest.raises(RuntimeError, match="broker API timeout"):
                execute_trade(intent, broker="delta", paper=False)

    def test_broker_partial_response_still_records(self):
        """Broker returns a dict without 'order_id' — engine should not crash."""
        intent = _live_intent()
        broker_mock = MagicMock()
        broker_mock.name = "delta"
        broker_mock.get_quote.return_value = {"bid": 29990, "ask": 30010, "last": 30000}
        broker_mock.place_market_order.return_value = {"status": "PENDING"}  # no order_id

        with patch("tradingagents.execution.engine.GlobalRiskMonitor", _mock_global_risk()), \
             patch("tradingagents.execution.engine.resolve_broker", return_value=broker_mock), \
             patch("tradingagents.execution.engine.DataLivenessMonitor") as mock_live, \
             patch("tradingagents.execution.engine._verify_fill_async"):
            mock_live.get_instance.return_value.validate_all_feeds_or_halt.return_value = True

            result = execute_trade(intent, broker="delta", paper=False)

        assert result is not None
        assert result.symbol == "BTCUSD"


# ---------------------------------------------------------------------------
# 2. Slippage guard fires when price moves
# ---------------------------------------------------------------------------

class TestSlippageGuard:
    def test_slippage_exceeded_blocks_trade(self):
        # suggested_entry=100.0, ask=200.0 → 100% slippage → must trigger slippage guard
        intent = _live_intent(price=100.0, qty=0.1)
        broker_mock = MagicMock()
        broker_mock.name = "delta"
        broker_mock.get_quote.return_value = {"bid": 195.0, "ask": 200.0, "last": 197.0}
        broker_mock.place_market_order.return_value = {"order_id": "x"}

        with patch("tradingagents.execution.engine.GlobalRiskMonitor", _mock_global_risk()), \
             patch("tradingagents.execution.engine.resolve_broker", return_value=broker_mock), \
             patch("tradingagents.execution.engine.DataLivenessMonitor") as mock_live:
            mock_live.get_instance.return_value.validate_all_feeds_or_halt.return_value = True

            with pytest.raises(RuntimeError, match="Slippage guard"):
                execute_trade(intent, broker="delta", paper=False)

    def test_slippage_within_tolerance_passes(self):
        intent = _live_intent(price=30000.0, qty=0.1)
        broker_mock = MagicMock()
        broker_mock.name = "delta"
        broker_mock.get_quote.return_value = {"bid": 29990, "ask": 30010, "last": 30000}  # 0.03% slip
        broker_mock.place_market_order.return_value = {"order_id": "x2", "status": "OPEN"}

        with patch("tradingagents.execution.engine.GlobalRiskMonitor", _mock_global_risk()), \
             patch("tradingagents.execution.engine.resolve_broker", return_value=broker_mock), \
             patch("tradingagents.execution.engine.DataLivenessMonitor") as mock_live, \
             patch("tradingagents.execution.engine._verify_fill_async"):
            mock_live.get_instance.return_value.validate_all_feeds_or_halt.return_value = True

            result = execute_trade(intent, broker="delta", paper=False)

        assert result is not None


# ---------------------------------------------------------------------------
# 3. Liveness monitor blocks trade when feeds are stale
# ---------------------------------------------------------------------------

class TestLivenessGuard:
    def test_stale_feed_blocks_execution(self, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_LIVENESS_CHECK_ENABLED", "true")
        intent = _live_intent()

        with patch("tradingagents.execution.engine.GlobalRiskMonitor", _mock_global_risk()), \
             patch("tradingagents.execution.engine.DataLivenessMonitor") as mock_live:
            mock_live.get_instance.return_value.validate_all_feeds_or_halt.return_value = False

            with pytest.raises(RuntimeError, match="STALE"):
                execute_trade(intent, broker="delta", paper=False)


# ---------------------------------------------------------------------------
# 4. OrderChaseManager sync integration
# ---------------------------------------------------------------------------

class TestOrderChaseManagerSync:
    def test_chase_fills_on_second_poll(self):
        from tradingagents.execution.chaser import OrderChaseManager

        broker = MagicMock()
        broker.get_order_status.side_effect = ["OPEN", "FILLED"]

        chaser = OrderChaseManager(
            broker_client=broker,
            symbol="BTCUSD",
            side="BUY",
            qty=0.1,
            initial_price=30000.0,
        )
        chaser.chase_interval = 0.001  # fast for tests

        result = chaser.chase("order-123")
        assert result.filled is True
        assert result.reason == "filled"

    def test_chase_aborts_on_slippage(self):
        from tradingagents.execution.chaser import OrderChaseManager

        broker = MagicMock()
        broker.get_order_status.return_value = "OPEN"
        broker.get_quote.return_value = {"ask": 99999.0, "bid": 99998.0, "last": 99999.0}

        chaser = OrderChaseManager(
            broker_client=broker,
            symbol="BTCUSD",
            side="BUY",
            qty=0.1,
            initial_price=30000.0,
            max_slippage_pct=0.005,
        )
        chaser.chase_interval = 0.001

        result = chaser.chase("order-999")
        assert result.filled is False
        assert result.reason == "slippage_exceeded"

    def test_chase_handles_rejected_order(self):
        from tradingagents.execution.chaser import OrderChaseManager

        broker = MagicMock()
        broker.get_order_status.return_value = "REJECTED"

        chaser = OrderChaseManager(
            broker_client=broker,
            symbol="BTCUSD",
            side="BUY",
            qty=0.1,
            initial_price=30000.0,
        )
        chaser.chase_interval = 0.001

        result = chaser.chase("order-666")
        assert result.filled is False
        assert result.reason == "rejected"


# ---------------------------------------------------------------------------
# 5. MarginValidator sync integration
# ---------------------------------------------------------------------------

class TestMarginValidatorSync:
    def test_insufficient_margin_returns_not_approved(self, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_MARGIN_CHECK_ENABLED", "true")
        from tradingagents.execution.margin import MarginValidator
        from tradingagents.execution.models import TradeIntent

        broker = MagicMock()
        broker.get_buying_power.return_value = 10.0
        broker.get_quote.return_value = {"bid": 30000, "ask": 30001, "last": 30000}

        intent = TradeIntent(symbol="BTCUSD", instrument_type="spot",
                             signal="BUY", quantity=1.0, suggested_entry=30000.0)
        validator = MarginValidator(broker)
        result = validator.validate(intent)
        assert result.approved is False
        assert "insufficient_margin" in result.reason

    def test_sufficient_margin_approved(self, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_MARGIN_CHECK_ENABLED", "true")
        from tradingagents.execution.margin import MarginValidator
        from tradingagents.execution.models import TradeIntent

        broker = MagicMock()
        broker.get_buying_power.return_value = 1_000_000.0
        broker.get_quote.return_value = {"bid": 100, "ask": 101, "last": 100}

        intent = TradeIntent(symbol="BTCUSD", instrument_type="spot",
                             signal="BUY", quantity=1.0, suggested_entry=100.0)
        validator = MarginValidator(broker)
        result = validator.validate(intent)
        assert result.approved is True

    def test_broker_without_buying_power_returns_optimistic(self, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_MARGIN_CHECK_ENABLED", "true")
        from tradingagents.execution.margin import MarginValidator
        from tradingagents.execution.models import TradeIntent

        broker = MagicMock(spec=[])   # no get_buying_power attribute
        intent = TradeIntent(symbol="BTCUSD", instrument_type="spot",
                             signal="BUY", quantity=1.0, suggested_entry=100.0)
        validator = MarginValidator(broker)
        result = validator.validate(intent)
        assert result.approved is True
        assert result.reason == "broker_unsupported"
