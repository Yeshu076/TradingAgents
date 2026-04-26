"""
tests/test_portfolio_heat.py

F-05: Tests for the Portfolio Heat Monitor.

Coverage:
  - PositionManager.get_position_heat()     — with and without stop, fallback
  - PositionManager.get_total_position_heat() — multi-position aggregation
  - PositionManager.set_stop_loss()         — persisted correctly
  - GlobalRiskMonitor.update_portfolio_heat() — in-memory + property
  - GlobalRiskMonitor.evaluate_trade_intent() — heat gate enabled/disabled
  - Engine integration                      — heat stored after paper fill
  - Edge cases                              — zero quantity, no stop, closed positions
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tradingagents.execution.position_manager import PositionManager
from tradingagents.execution.global_risk import GlobalRiskMonitor
from tradingagents.execution.models import TradeIntent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_pm(tmp_path: Path, balance: float = 100_000.0) -> PositionManager:
    """Return a PositionManager backed by a temp SQLite file."""
    return PositionManager(db_path=tmp_path / "test.db", initial_balance=balance)


def _open_position(pm: PositionManager, symbol: str, qty: float, price: float,
                   stop: float = 0.0, instrument: str = "crypto") -> None:
    """Open a position and optionally set a stop loss."""
    pm.place_order(symbol, "BUY", qty, price, instrument)
    if stop > 0:
        pm.set_stop_loss(symbol, stop)


# ---------------------------------------------------------------------------
# PositionManager — schema
# ---------------------------------------------------------------------------

class TestStopLossColumn:
    def test_new_db_has_stop_loss_column(self, tmp_path):
        pm = _fresh_pm(tmp_path)
        pm.place_order("BTC", "BUY", 1.0, 50_000.0, "crypto")
        positions = pm.get_positions()
        assert len(positions) == 1
        assert "stop_loss" in positions[0]
        assert positions[0]["stop_loss"] == 0.0  # default

    def test_set_stop_loss_persists(self, tmp_path):
        pm = _fresh_pm(tmp_path)
        pm.place_order("BTC", "BUY", 1.0, 50_000.0, "crypto")
        pm.set_stop_loss("BTC", 48_000.0)
        positions = pm.get_positions()
        assert abs(positions[0]["stop_loss"] - 48_000.0) < 0.01

    def test_set_stop_loss_zero_ignored(self, tmp_path):
        pm = _fresh_pm(tmp_path)
        pm.place_order("BTC", "BUY", 1.0, 50_000.0, "crypto")
        pm.set_stop_loss("BTC", 48_000.0)  # set first
        pm.set_stop_loss("BTC", 0.0)       # zero must not overwrite
        positions = pm.get_positions()
        assert abs(positions[0]["stop_loss"] - 48_000.0) < 0.01

    def test_set_stop_loss_negative_ignored(self, tmp_path):
        pm = _fresh_pm(tmp_path)
        pm.place_order("BTC", "BUY", 1.0, 50_000.0, "crypto")
        pm.set_stop_loss("BTC", 48_000.0)
        pm.set_stop_loss("BTC", -100.0)
        positions = pm.get_positions()
        assert abs(positions[0]["stop_loss"] - 48_000.0) < 0.01

    def test_set_stop_loss_updates_on_recall(self, tmp_path):
        pm = _fresh_pm(tmp_path)
        pm.place_order("BTC", "BUY", 1.0, 50_000.0, "crypto")
        pm.set_stop_loss("BTC", 48_000.0)
        pm.set_stop_loss("BTC", 47_000.0)  # trailing stop move
        positions = pm.get_positions()
        assert abs(positions[0]["stop_loss"] - 47_000.0) < 0.01


# ---------------------------------------------------------------------------
# PositionManager — get_position_heat()
# ---------------------------------------------------------------------------

class TestGetPositionHeat:
    def test_heat_with_stop_loss(self, tmp_path):
        """Heat = qty × |entry - stop|."""
        pm = _fresh_pm(tmp_path)
        _open_position(pm, "BTC", qty=2.0, price=50_000.0, stop=49_000.0)
        # 2 × |50000 - 49000| = 2000
        heat = pm.get_position_heat("BTC")
        assert abs(heat - 2_000.0) < 0.01

    def test_heat_short_side_stop_above_entry(self, tmp_path):
        """For short: stop above entry. Heat = qty × |entry - stop|."""
        pm = _fresh_pm(tmp_path)
        pm.place_order("XAUUSD", "BUY", 1.0, 1_900.0, "forex")
        pm.set_stop_loss("XAUUSD", 1_950.0)  # stop above (short scenario stored as stop)
        # 1 × |1900 - 1950| = 50
        heat = pm.get_position_heat("XAUUSD")
        assert abs(heat - 50.0) < 0.01

    def test_heat_fallback_no_stop(self, tmp_path, monkeypatch):
        """No stop set → fallback: qty × avg_price × fallback_pct / 100."""
        monkeypatch.setenv("TRADINGAGENTS_HEAT_FALLBACK_PCT", "2.0")
        pm = _fresh_pm(tmp_path)
        pm.place_order("ETH", "BUY", 10.0, 3_000.0, "crypto")
        # 10 × 3000 × 2% = 600
        heat = pm.get_position_heat("ETH")
        assert abs(heat - 600.0) < 0.01

    def test_fallback_pct_configurable(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_HEAT_FALLBACK_PCT", "5.0")
        pm = _fresh_pm(tmp_path)
        pm.place_order("ETH", "BUY", 10.0, 3_000.0, "crypto")
        # 10 × 3000 × 5% = 1500
        heat = pm.get_position_heat("ETH")
        assert abs(heat - 1_500.0) < 0.01

    def test_heat_closed_position_is_zero(self, tmp_path):
        pm = _fresh_pm(tmp_path)
        pm.place_order("BTC", "BUY", 1.0, 50_000.0, "crypto")
        pm.place_order("BTC", "SELL", 1.0, 50_000.0, "crypto")  # flat
        heat = pm.get_position_heat("BTC")
        assert heat == 0.0

    def test_heat_unknown_symbol_is_zero(self, tmp_path):
        pm = _fresh_pm(tmp_path)
        heat = pm.get_position_heat("NONEXISTENT")
        assert heat == 0.0

    def test_heat_fractional_qty(self, tmp_path):
        pm = _fresh_pm(tmp_path)
        _open_position(pm, "BTC", qty=0.5, price=60_000.0, stop=59_000.0)
        # 0.5 × |60000 - 59000| = 500
        heat = pm.get_position_heat("BTC")
        assert abs(heat - 500.0) < 0.01


# ---------------------------------------------------------------------------
# PositionManager — get_total_position_heat()
# ---------------------------------------------------------------------------

class TestGetTotalPositionHeat:
    def test_single_position(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_HEAT_FALLBACK_PCT", "0.0")  # no fallback noise
        pm = _fresh_pm(tmp_path)
        _open_position(pm, "BTC", qty=1.0, price=50_000.0, stop=48_000.0)
        # 1 × 2000 = 2000
        assert abs(pm.get_total_position_heat() - 2_000.0) < 0.01

    def test_multiple_positions_sum(self, tmp_path):
        pm = _fresh_pm(tmp_path)
        _open_position(pm, "BTC", qty=1.0, price=50_000.0, stop=49_000.0)   # heat = 1000
        _open_position(pm, "ETH", qty=5.0, price=3_000.0,  stop=2_900.0)    # heat = 500
        _open_position(pm, "XAUUSD", qty=2.0, price=1_900.0, stop=1_870.0)  # heat = 60
        total = pm.get_total_position_heat()
        assert abs(total - 1_560.0) < 0.01

    def test_no_positions_returns_zero(self, tmp_path):
        pm = _fresh_pm(tmp_path)
        assert pm.get_total_position_heat() == 0.0

    def test_all_positions_closed_returns_zero(self, tmp_path):
        pm = _fresh_pm(tmp_path)
        pm.place_order("BTC", "BUY", 1.0, 50_000.0, "crypto")
        pm.place_order("BTC", "SELL", 1.0, 51_000.0, "crypto")
        assert pm.get_total_position_heat() == 0.0

    def test_mixed_with_and_without_stop(self, tmp_path, monkeypatch):
        """Positions with stop use exact heat; those without use fallback."""
        monkeypatch.setenv("TRADINGAGENTS_HEAT_FALLBACK_PCT", "2.0")
        pm = _fresh_pm(tmp_path)
        _open_position(pm, "BTC", qty=1.0, price=50_000.0, stop=49_000.0)  # heat = 1000 (exact)
        pm.place_order("ETH", "BUY", 10.0, 3_000.0, "crypto")              # no stop, fallback = 600
        total = pm.get_total_position_heat()
        assert abs(total - 1_600.0) < 0.01


# ---------------------------------------------------------------------------
# GlobalRiskMonitor — portfolio heat state
# ---------------------------------------------------------------------------

class TestGRMPortfolioHeat:
    def setup_method(self):
        GlobalRiskMonitor.reset_instance()
        os.environ.pop("TRADINGAGENTS_MAX_PORTFOLIO_HEAT_USD", None)

    def teardown_method(self):
        GlobalRiskMonitor.reset_instance()

    def test_default_heat_is_zero(self):
        grm = GlobalRiskMonitor()
        assert grm.portfolio_heat == 0.0

    def test_update_portfolio_heat(self):
        grm = GlobalRiskMonitor()
        grm.update_portfolio_heat(5_000.0)
        assert abs(grm.portfolio_heat - 5_000.0) < 0.01

    def test_negative_heat_clamped_to_zero(self):
        grm = GlobalRiskMonitor()
        grm.update_portfolio_heat(-999.0)
        assert grm.portfolio_heat == 0.0

    def test_heat_gate_disabled_by_default(self, monkeypatch):
        monkeypatch.setenv("MAX_SYMBOL_EXPOSURE_USD", "999999999")
        grm = GlobalRiskMonitor()
        grm.update_portfolio_heat(999_999.0)  # huge heat
        # Gate is disabled (max=0); should still allow
        result = grm.evaluate_trade_intent(
            "test_strat", "BTC", 1000.0, proposed_heat=500_000.0
        )
        assert result is True

    def test_heat_gate_blocks_when_exceeded(self, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_MAX_PORTFOLIO_HEAT_USD", "5000")
        monkeypatch.setenv("MAX_SYMBOL_EXPOSURE_USD", "999999999")
        grm = GlobalRiskMonitor()
        # Existing heat = 4000, proposed = 2000 → total 6000 > 5000 → blocked
        grm.update_portfolio_heat(4_000.0)
        result = grm.evaluate_trade_intent(
            "test_strat", "BTC", 1000.0, proposed_heat=2_000.0
        )
        assert result is False

    def test_heat_gate_allows_within_cap(self, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_MAX_PORTFOLIO_HEAT_USD", "5000")
        monkeypatch.setenv("MAX_SYMBOL_EXPOSURE_USD", "999999999")
        grm = GlobalRiskMonitor()
        grm.update_portfolio_heat(2_000.0)
        # 2000 + 1500 = 3500 < 5000 → allowed
        result = grm.evaluate_trade_intent(
            "test_strat", "BTC", 1000.0, proposed_heat=1_500.0
        )
        assert result is True

    def test_heat_gate_exactly_at_cap_is_blocked(self, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_MAX_PORTFOLIO_HEAT_USD", "5000")
        monkeypatch.setenv("MAX_SYMBOL_EXPOSURE_USD", "999999999")
        grm = GlobalRiskMonitor()
        grm.update_portfolio_heat(3_000.0)
        # 3000 + 2000 = 5000, not > 5000 → allowed (strictly greater check)
        result = grm.evaluate_trade_intent(
            "test_strat", "BTC", 1000.0, proposed_heat=2_000.0
        )
        assert result is True  # exactly at cap is allowed

    def test_heat_gate_just_over_cap_is_blocked(self, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_MAX_PORTFOLIO_HEAT_USD", "5000")
        monkeypatch.setenv("MAX_SYMBOL_EXPOSURE_USD", "999999999")
        grm = GlobalRiskMonitor()
        grm.update_portfolio_heat(3_000.0)
        # 3000 + 2000.01 > 5000 → blocked
        result = grm.evaluate_trade_intent(
            "test_strat", "BTC", 1000.0, proposed_heat=2_000.01
        )
        assert result is False

    def test_zero_proposed_heat_does_not_trigger_gate(self, monkeypatch):
        """If TradeIntent has no stop, proposed_heat=0 — gate must not block."""
        monkeypatch.setenv("TRADINGAGENTS_MAX_PORTFOLIO_HEAT_USD", "5000")
        monkeypatch.setenv("MAX_SYMBOL_EXPOSURE_USD", "999999999")
        grm = GlobalRiskMonitor()
        grm.update_portfolio_heat(4_999.0)
        result = grm.evaluate_trade_intent(
            "test_strat", "BTC", 1000.0, proposed_heat=0.0
        )
        assert result is True

    def test_heat_accumulates_after_update(self, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_MAX_PORTFOLIO_HEAT_USD", "3000")
        monkeypatch.setenv("MAX_SYMBOL_EXPOSURE_USD", "999999999")
        grm = GlobalRiskMonitor()
        # First trade: heat = 1000, cap = 3000 → ok
        grm.update_portfolio_heat(1_000.0)
        assert grm.evaluate_trade_intent("s", "BTC", 100.0, proposed_heat=500.0) is True
        # Second trade: heat = 2800, cap = 3000 → block 500 more
        grm.update_portfolio_heat(2_800.0)
        assert grm.evaluate_trade_intent("s", "ETH", 100.0, proposed_heat=500.0) is False


# ---------------------------------------------------------------------------
# Engine integration — heat wired end-to-end
# ---------------------------------------------------------------------------

class TestEngineHeatIntegration:
    """Verify that stop_loss is persisted and heat updated after paper fill."""

    def _make_intent(self, qty: float = 1.0) -> TradeIntent:
        return TradeIntent(
            symbol="BTC",
            instrument_type="crypto",
            signal="BUY",
            quantity=qty,
            suggested_entry=50_000.0,
            suggested_stop_loss=49_000.0,
            confidence=1.0,
        )

    def _common_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TRADINGAGENTS_ALLOW_LIVE", "false")
        monkeypatch.setenv("TRADINGAGENTS_SQLITE_STATE_FILE", str(tmp_path / "port.db"))
        monkeypatch.setenv("TRADINGAGENTS_CORRELATION_CHECK_ENABLED", "false")
        monkeypatch.setenv("TRADINGAGENTS_LIVENESS_CHECK_ENABLED", "false")
        monkeypatch.setenv("MAX_SYMBOL_EXPOSURE_USD", "999999999")
        monkeypatch.setenv("MAX_DAILY_LOSS_USD", "999999999")
        monkeypatch.setenv("TRADINGAGENTS_MAX_PORTFOLIO_HEAT_USD", "0")  # disabled
        monkeypatch.setenv("TRADINGAGENTS_PAPER_INITIAL_BALANCE", "500000")
        # Ensure policy doesn't reject the trade on notional/qty limits
        monkeypatch.setenv("TRADINGAGENTS_MAX_ORDER_NOTIONAL", "999999999")
        monkeypatch.setenv("TRADINGAGENTS_MAX_ORDER_QTY", "1000")
        GlobalRiskMonitor.reset_instance()

    @staticmethod
    def _mock_broker():
        """Return a properly configured broker mock with a string .name attribute."""
        broker = MagicMock(spec=[])
        broker.name = "paper_mock"
        return broker

    def test_stop_loss_persisted_after_paper_fill(self, monkeypatch, tmp_path):
        self._common_env(monkeypatch, tmp_path)

        from tradingagents.execution.engine import execute_trade

        intent = self._make_intent(qty=1.0)

        with patch("tradingagents.execution.engine.resolve_broker") as mock_b, \
             patch("tradingagents.execution.engine.DeterministicRiskGate") as mock_rg, \
             patch("tradingagents.execution.engine.send_notification"):
            mock_b.return_value = self._mock_broker()
            mock_rg.from_env.return_value.evaluate.return_value = MagicMock(
                approved=True, rejection_reason=None, warnings=[]
            )
            execute_trade(intent, paper=True, allow_duplicates=True)

        pm = PositionManager(tmp_path / "port.db")
        positions = pm.get_positions()
        assert len(positions) == 1
        assert abs(positions[0]["stop_loss"] - 49_000.0) < 0.01

    def test_portfolio_heat_updated_after_paper_fill(self, monkeypatch, tmp_path):
        self._common_env(monkeypatch, tmp_path)

        from tradingagents.execution.engine import execute_trade

        intent = self._make_intent(qty=1.0)

        with patch("tradingagents.execution.engine.resolve_broker") as mock_b, \
             patch("tradingagents.execution.engine.DeterministicRiskGate") as mock_rg, \
             patch("tradingagents.execution.engine.send_notification"):
            mock_b.return_value = self._mock_broker()
            mock_rg.from_env.return_value.evaluate.return_value = MagicMock(
                approved=True, rejection_reason=None, warnings=[]
            )
            execute_trade(intent, paper=True, allow_duplicates=True)

        # Read heat from PositionManager (SQLite ground truth)
        pm = PositionManager(tmp_path / "port.db")
        total_heat = pm.get_total_position_heat()
        # 1 × |50000 - 49000| = 1000
        assert abs(total_heat - 1_000.0) < 0.01

    def test_heat_gate_blocks_second_trade(self, monkeypatch, tmp_path):
        """With a tight heat cap, the second trade should be blocked."""
        self._common_env(monkeypatch, tmp_path)
        monkeypatch.setenv("TRADINGAGENTS_MAX_PORTFOLIO_HEAT_USD", "500")  # cap < 1000

        from tradingagents.execution.engine import execute_trade

        intent1 = self._make_intent(qty=1.0)  # proposed heat = 1000 > cap

        with patch("tradingagents.execution.engine.resolve_broker") as mock_b, \
             patch("tradingagents.execution.engine.DeterministicRiskGate") as mock_rg, \
             patch("tradingagents.execution.engine.send_notification"):
            mock_b.return_value = self._mock_broker()
            mock_rg.from_env.return_value.evaluate.return_value = MagicMock(
                approved=True, rejection_reason=None, warnings=[]
            )
            # proposed_heat = 1 × |50000 - 49000| = 1000 > cap 500 → blocked
            with pytest.raises(RuntimeError, match="Global Risk Caps breached"):
                execute_trade(intent1, paper=True, allow_duplicates=True)
