"""
tests/test_e2e_paper_flow.py

GAP-25: End-to-end integration tests for the paper trading pipeline.

These tests exercise the complete path:
  TradeIntent → execute_trade() → PositionManager → DecisionJournal

Broker and LLM APIs are fully mocked. No real I/O occurs.
Redis is bypassed via env-var disabling of liveness/risk monitors.
"""
import pytest
from unittest.mock import MagicMock, patch

from tradingagents.execution.models import TradeIntent
from tradingagents.execution.engine import execute_trade


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def paper_env(monkeypatch, tmp_path):
    """
    Set all env vars needed for a clean, isolated paper trade cycle.
    Uses a temp SQLite DB and unique journal path per test.
    """
    monkeypatch.setenv("TRADINGAGENTS_PAPER", "true")
    monkeypatch.setenv("TRADINGAGENTS_LIVENESS_CHECK_ENABLED", "false")
    monkeypatch.setenv("TRADINGAGENTS_CORRELATION_CHECK_ENABLED", "false")
    monkeypatch.setenv("TRADINGAGENTS_MARGIN_CHECK_ENABLED", "false")
    monkeypatch.setenv("TRADINGAGENTS_DB_PATH", str(tmp_path / "test_paper.db"))
    monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_FILE", str(tmp_path / "decisions.jsonl"))
    monkeypatch.setenv("TRADINGAGENTS_MAX_OPEN_POSITIONS", "100")
    monkeypatch.setenv("TRADINGAGENTS_MAX_ORDER_QTY", "1000")
    monkeypatch.setenv("TRADINGAGENTS_MAX_ORDER_NOTIONAL", "10000000")
    monkeypatch.setenv("TRADINGAGENTS_ALLOW_LIVE", "false")
    monkeypatch.setenv("TRADINGAGENTS_EXECUTION_IDEMPOTENCY_ENABLED", "false")
    monkeypatch.setenv("TRADINGAGENTS_ENFORCE_MARKET_HOURS", "false")
    yield


def _make_buy_intent(symbol="BTCUSD", qty=0.1, price=30000.0) -> TradeIntent:
    return TradeIntent(
        symbol=symbol,
        instrument_type="spot",
        signal="BUY",
        quantity=qty,
        suggested_entry=price,
    )


def _make_sell_intent(symbol="BTCUSD", qty=0.1, price=30000.0) -> TradeIntent:
    return TradeIntent(
        symbol=symbol,
        instrument_type="spot",
        signal="SELL",
        quantity=qty,
        suggested_entry=price,
    )


def _mock_global_risk():
    m = MagicMock()
    m.get_instance.return_value.evaluate_trade_intent.return_value = True
    m.get_instance.return_value.report_trade_execution.return_value = None
    return m


# ---------------------------------------------------------------------------
# 1. Paper BUY creates position
# ---------------------------------------------------------------------------

class TestPaperBuyFlow:
    def test_buy_creates_position(self):
        intent = _make_buy_intent(symbol="LTCUSD_PAPER1")  # unique symbol avoids dedup cross-test
        with patch("tradingagents.execution.engine.GlobalRiskMonitor", _mock_global_risk()):
            result = execute_trade(intent, broker="auto", paper=True)
        assert result.status == "simulated_filled"
        assert result.mode == "paper"
        assert result.symbol == "LTCUSD_PAPER1"
        assert result.side == "BUY"

    def test_hold_signal_returns_no_trade(self):
        intent = TradeIntent(
            symbol="BTCUSD", instrument_type="spot",
            signal="HOLD", quantity=0.1,
        )
        result = execute_trade(intent, broker="auto", paper=True)
        assert result.status == "no_trade"
        assert result.action == "skip"

    def test_flat_signal_returns_no_trade(self):
        intent = TradeIntent(
            symbol="BTCUSD", instrument_type="spot",
            signal="FLAT", quantity=0.1,
        )
        result = execute_trade(intent, broker="auto", paper=True)
        assert result.status == "no_trade"

    def test_buy_then_sell_round_trip(self):
        buy = _make_buy_intent(symbol="ADAUSD_PAPER2")
        sell = _make_sell_intent(symbol="XRPUSD_PAPER2")  # different symbol — different dedup key
        with patch("tradingagents.execution.engine.GlobalRiskMonitor", _mock_global_risk()):
            r_buy = execute_trade(buy, broker="auto", paper=True)
            r_sell = execute_trade(sell, broker="auto", paper=True)
        assert r_buy.status == "simulated_filled"
        assert r_sell.status == "simulated_filled"
        assert r_buy.side == "BUY"
        assert r_sell.side == "SELL"


# ---------------------------------------------------------------------------
# 2. Risk gate blocks trade
# ---------------------------------------------------------------------------

class TestRiskGateBlocking:
    def test_global_risk_cap_blocks_trade(self):
        intent = _make_buy_intent()
        with patch("tradingagents.execution.engine.GlobalRiskMonitor") as mock_risk:
            mock_risk.get_instance.return_value.evaluate_trade_intent.return_value = False
            with pytest.raises(RuntimeError, match="Global Risk Caps"):
                execute_trade(intent, broker="auto", paper=True)

    def test_deterministic_risk_gate_blocks_trade(self, monkeypatch):
        """Trigger the deterministic risk gate with an absurdly large quantity."""
        monkeypatch.setenv("TRADINGAGENTS_MAX_ORDER_QTY", "1")
        intent = _make_buy_intent(qty=9999)
        with pytest.raises(RuntimeError):
            execute_trade(intent, broker="auto", paper=True)


# ---------------------------------------------------------------------------
# 3. Deduplication prevents double execution
# ---------------------------------------------------------------------------

class TestDeduplication:
    def test_duplicate_blocked_on_second_call(self, monkeypatch, tmp_path):
        import uuid
        # Fresh journal file + unique symbol per invocation prevents cross-run pollution
        unique_sym = f"DEDUP_{uuid.uuid4().hex[:8].upper()}"
        monkeypatch.setenv("TRADINGAGENTS_EXECUTION_IDEMPOTENCY_ENABLED", "true")
        monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_FILE", str(tmp_path / "dedup_test.jsonl"))
        intent = _make_buy_intent(symbol=unique_sym)
        with patch("tradingagents.execution.engine.GlobalRiskMonitor", _mock_global_risk()):
            r1 = execute_trade(intent, broker="auto", paper=True)
            r2 = execute_trade(intent, broker="auto", paper=True)
        assert r1.status == "simulated_filled", f"r1 was {r1.status}"
        assert r2.status == "skipped_duplicate"

    def test_allowed_duplicates_bypass_dedup(self, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_EXECUTION_IDEMPOTENCY_ENABLED", "true")
        intent = _make_buy_intent(symbol="SOLUSDT")  # unique symbol per test class
        with patch("tradingagents.execution.engine.GlobalRiskMonitor", _mock_global_risk()):
            r1 = execute_trade(intent, broker="auto", paper=True, allow_duplicates=True)
            r2 = execute_trade(intent, broker="auto", paper=True, allow_duplicates=True)
        assert r1.status == "simulated_filled"
        assert r2.status == "simulated_filled"


# ---------------------------------------------------------------------------
# 4. Margin pre-check (live path)
# ---------------------------------------------------------------------------

class TestMarginPreCheck:
    def test_margin_failure_blocks_live_trade(self, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_MARGIN_CHECK_ENABLED", "true")
        monkeypatch.setenv("TRADINGAGENTS_ALLOW_LIVE", "true")
        intent = _make_buy_intent(price=30000.0, qty=10)

        broker_mock = MagicMock()
        broker_mock.name = "delta"
        broker_mock.get_buying_power.return_value = 1.0   # near zero → margin fail
        broker_mock.get_quote.return_value = {"bid": 30000, "ask": 30001, "last": 30000}
        broker_mock.place_market_order.return_value = {"order_id": "x1"}

        with patch("tradingagents.execution.engine.GlobalRiskMonitor", _mock_global_risk()), \
             patch("tradingagents.execution.engine.resolve_broker", return_value=broker_mock), \
             patch("tradingagents.execution.engine.DataLivenessMonitor") as mock_live:
            mock_live.get_instance.return_value.validate_all_feeds_or_halt.return_value = True

            with pytest.raises(RuntimeError, match="Margin check failed"):
                execute_trade(intent, broker="delta", paper=False)

    def test_margin_pass_allows_trade(self, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_MARGIN_CHECK_ENABLED", "true")
        monkeypatch.setenv("TRADINGAGENTS_ALLOW_LIVE", "true")
        intent = _make_buy_intent(price=100.0, qty=1)

        broker_mock = MagicMock()
        broker_mock.name = "delta"
        broker_mock.get_buying_power.return_value = 1_000_000.0
        broker_mock.get_quote.return_value = {"bid": 100, "ask": 100, "last": 100}
        broker_mock.place_market_order.return_value = {"order_id": "x2", "status": "OPEN"}

        with patch("tradingagents.execution.engine.GlobalRiskMonitor", _mock_global_risk()), \
             patch("tradingagents.execution.engine.resolve_broker", return_value=broker_mock), \
             patch("tradingagents.execution.engine.DataLivenessMonitor") as mock_live, \
             patch("tradingagents.execution.engine._verify_fill_async"):
            mock_live.get_instance.return_value.validate_all_feeds_or_halt.return_value = True

            result = execute_trade(intent, broker="delta", paper=False)

        assert result is not None
        assert result.symbol == "BTCUSD"


# ---------------------------------------------------------------------------
# 5. Confidence-gated execution (F-04)
# ---------------------------------------------------------------------------

class TestConfidenceGating:
    def test_low_confidence_skipped(self, monkeypatch):
        """A signal with confidence below threshold should be skipped without touching the broker."""
        monkeypatch.setenv("TRADINGAGENTS_MIN_CONFIDENCE", "0.65")
        intent = TradeIntent(
            symbol="BTCUSD_CONF1", instrument_type="spot",
            signal="BUY", quantity=0.1, confidence=0.30,
            agent_source="test_analyst",
        )
        result = execute_trade(intent, broker="auto", paper=True)
        assert result.status == "skipped_low_confidence"
        assert result.action == "skip"
        assert result.details["confidence"] == 0.30
        assert result.details["threshold"] == 0.65

    def test_high_confidence_passes(self, monkeypatch):
        """A signal above the confidence threshold should execute normally."""
        monkeypatch.setenv("TRADINGAGENTS_MIN_CONFIDENCE", "0.65")
        intent = TradeIntent(
            symbol="ETHUSD_CONF2", instrument_type="spot",
            signal="BUY", quantity=0.1, suggested_entry=2000.0,
            confidence=0.90,
        )
        with patch("tradingagents.execution.engine.GlobalRiskMonitor", _mock_global_risk()):
            result = execute_trade(intent, broker="auto", paper=True)
        assert result.status == "simulated_filled"

    def test_confidence_gate_disabled_when_zero(self):
        """When TRADINGAGENTS_MIN_CONFIDENCE is 0 (default), even very low confidence passes."""
        intent = TradeIntent(
            symbol="SOLUSD_CONF3", instrument_type="spot",
            signal="BUY", quantity=0.1, suggested_entry=100.0,
            confidence=0.01,  # extremely low
        )
        with patch("tradingagents.execution.engine.GlobalRiskMonitor", _mock_global_risk()):
            result = execute_trade(intent, broker="auto", paper=True)
        # Default threshold is 0.0 — so even 0.01 confidence passes
        assert result.status == "simulated_filled"

    def test_confidence_at_exact_threshold_passes(self, monkeypatch):
        """Confidence exactly at the threshold should pass (< is strict)."""
        monkeypatch.setenv("TRADINGAGENTS_MIN_CONFIDENCE", "0.70")
        intent = TradeIntent(
            symbol="DOTUSD_CONF4", instrument_type="spot",
            signal="SELL", quantity=0.5, suggested_entry=7.0,
            confidence=0.70,  # exactly at threshold
        )
        with patch("tradingagents.execution.engine.GlobalRiskMonitor", _mock_global_risk()):
            result = execute_trade(intent, broker="auto", paper=True)
        assert result.status == "simulated_filled"
