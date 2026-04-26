"""
tests/test_position_sizer.py

F-02: Tests for the Dynamic Position Sizing Engine.
Covers all three sizing modes, edge cases, fallback chains,
SizerConfig loading, and integration with the execution engine.
"""
from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from tradingagents.execution.position_sizer import (
    SizerConfig,
    SizingMode,
    _clamp,
    _floor_to_step,
    calculate_position_size,
    size_fixed,
    size_percent_equity,
    size_volatility_adjusted,
)
from tradingagents.execution.models import TradeIntent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _cfg(**kwargs) -> SizerConfig:
    defaults = dict(
        mode=SizingMode.FIXED,
        risk_per_trade_pct=1.0,
        atr_multiplier=2.0,
        min_quantity=0.01,
        max_quantity=100.0,
        quantity_step=0.01,
    )
    defaults.update(kwargs)
    return SizerConfig(**defaults)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class TestFloorToStep:
    def test_exact_multiple(self):
        assert _floor_to_step(1.0, 0.25) == 1.0

    def test_rounds_down(self):
        assert abs(_floor_to_step(1.049, 0.05) - 1.0) < 1e-9

    def test_zero_step_passthrough(self):
        assert _floor_to_step(3.14159, 0.0) == 3.14159

    def test_small_step(self):
        val = _floor_to_step(0.1234, 0.01)
        assert abs(val - 0.12) < 1e-9


class TestClamp:
    def test_within_range(self):
        assert _clamp(5.0, 1.0, 10.0) == 5.0

    def test_below_min(self):
        assert _clamp(0.001, 0.01, 100.0) == 0.01

    def test_above_max(self):
        assert _clamp(200.0, 0.01, 100.0) == 100.0


# ---------------------------------------------------------------------------
# size_fixed
# ---------------------------------------------------------------------------

class TestSizeFixed:
    def test_returns_stepped_quantity(self):
        cfg = _cfg(quantity_step=0.05)
        result = size_fixed(1.049, cfg)
        assert abs(result - 1.0) < 1e-9

    def test_clamped_to_min(self):
        cfg = _cfg(min_quantity=0.5, quantity_step=0.01)
        result = size_fixed(0.1, cfg)
        assert result == 0.5

    def test_clamped_to_max(self):
        cfg = _cfg(max_quantity=10.0, quantity_step=0.01)
        result = size_fixed(500.0, cfg)
        assert result == 10.0

    def test_exact_quantity_unchanged(self):
        cfg = _cfg(quantity_step=0.01, min_quantity=0.01, max_quantity=100.0)
        assert size_fixed(5.0, cfg) == 5.0


# ---------------------------------------------------------------------------
# size_percent_equity
# ---------------------------------------------------------------------------

class TestSizePercentEquity:
    def test_basic_calculation(self):
        """1% of 100k equity / $100 stop distance = 10.0 units."""
        cfg = _cfg(risk_per_trade_pct=1.0, quantity_step=0.01)
        result = size_percent_equity(100_000.0, entry=1000.0, stop_loss=900.0, config=cfg)
        # risk_amount = 1000, stop_dist = 100, qty = 10.0
        assert abs(result - 10.0) < 0.01

    def test_tight_stop_gives_smaller_position(self):
        """Same equity, tighter stop → smaller dollar risk → more units."""
        cfg = _cfg(risk_per_trade_pct=1.0, quantity_step=0.01)
        wide = size_percent_equity(100_000.0, entry=100.0, stop_loss=50.0, config=cfg)  # dist=50
        tight = size_percent_equity(100_000.0, entry=100.0, stop_loss=95.0, config=cfg)  # dist=5
        # Tighter stop → higher quantity
        assert tight > wide

    def test_zero_equity_returns_min(self):
        cfg = _cfg(min_quantity=0.01)
        result = size_percent_equity(0.0, entry=100.0, stop_loss=90.0, config=cfg)
        assert result == 0.01

    def test_zero_stop_distance_falls_back_to_fixed(self):
        """Stop loss = entry → fallback quantity used."""
        cfg = _cfg(min_quantity=0.01, max_quantity=100.0, quantity_step=0.01)
        result = size_percent_equity(
            100_000.0, entry=100.0, stop_loss=100.0, config=cfg, fallback_quantity=2.5
        )
        assert result == 2.5

    def test_result_clamped_to_max(self):
        """Very small stop distance → enormous raw qty → must be clamped."""
        cfg = _cfg(risk_per_trade_pct=1.0, max_quantity=50.0, quantity_step=0.01)
        result = size_percent_equity(
            100_000.0, entry=100.0, stop_loss=99.99, config=cfg
        )
        assert result == 50.0

    def test_result_clamped_to_min(self):
        """Very large stop distance → tiny qty → floored to min."""
        cfg = _cfg(risk_per_trade_pct=0.01, min_quantity=0.5, quantity_step=0.01)
        result = size_percent_equity(
            1_000.0, entry=100.0, stop_loss=0.01, config=cfg
        )
        assert result == 0.5

    def test_quantity_step_applied(self):
        """Result is floored to quantity_step."""
        cfg = _cfg(risk_per_trade_pct=1.0, quantity_step=0.5)
        result = size_percent_equity(100_000.0, entry=100.0, stop_loss=97.0, config=cfg)
        # risk=1000, dist=3, raw=333.33... → floor to 0.5 → 333.0
        assert result % 0.5 < 1e-9

    def test_sell_side_stop_above_entry(self):
        """For short trades: entry < stop_loss (stop above entry). Distance is still positive."""
        cfg = _cfg(risk_per_trade_pct=1.0, quantity_step=0.01)
        result = size_percent_equity(100_000.0, entry=90.0, stop_loss=100.0, config=cfg)
        # dist = |90 - 100| = 10, risk = 1000, qty = 100
        assert abs(result - 100.0) < 0.01


# ---------------------------------------------------------------------------
# size_volatility_adjusted
# ---------------------------------------------------------------------------

class TestSizeVolatilityAdjusted:
    def test_basic_atr_calculation(self):
        """1% of 100k / (5.0 ATR × 2.0 mult) = 100.0 units."""
        cfg = _cfg(risk_per_trade_pct=1.0, atr_multiplier=2.0, quantity_step=0.01)
        result = size_volatility_adjusted(equity=100_000.0, atr=5.0, config=cfg)
        assert abs(result - 100.0) < 0.01

    def test_higher_atr_gives_smaller_position(self):
        cfg = _cfg(risk_per_trade_pct=1.0, atr_multiplier=2.0, quantity_step=0.01)
        low_vol = size_volatility_adjusted(100_000.0, atr=2.0, config=cfg)
        high_vol = size_volatility_adjusted(100_000.0, atr=10.0, config=cfg)
        assert low_vol > high_vol

    def test_zero_atr_falls_back_to_percent_equity(self):
        """ATR=0 with stop pair → falls back to percent_equity."""
        cfg = _cfg(risk_per_trade_pct=1.0, quantity_step=0.01)
        vol_result = size_volatility_adjusted(
            equity=100_000.0, atr=0.0, config=cfg,
            entry=1000.0, stop_loss=900.0,
        )
        pct_result = size_percent_equity(100_000.0, 1000.0, 900.0, cfg)
        assert abs(vol_result - pct_result) < 0.01

    def test_zero_atr_no_stop_falls_back_to_fixed(self):
        """ATR=0, no entry/stop → falls back to fixed quantity."""
        cfg = _cfg(quantity_step=0.01, min_quantity=0.01, max_quantity=100.0)
        result = size_volatility_adjusted(
            equity=100_000.0, atr=0.0, config=cfg, fallback_quantity=3.75
        )
        assert result == 3.75

    def test_zero_equity_returns_min(self):
        cfg = _cfg(min_quantity=0.01)
        result = size_volatility_adjusted(equity=0.0, atr=5.0, config=cfg)
        assert result == 0.01

    def test_result_clamped_to_max(self):
        cfg = _cfg(risk_per_trade_pct=1.0, atr_multiplier=0.001, max_quantity=10.0, quantity_step=0.01)
        result = size_volatility_adjusted(100_000.0, atr=0.0001, config=cfg)
        # Will hit max cap
        assert result == 10.0


# ---------------------------------------------------------------------------
# calculate_position_size dispatch
# ---------------------------------------------------------------------------

class TestCalculatePositionSize:
    def test_fixed_mode_returns_raw(self):
        cfg = _cfg(mode=SizingMode.FIXED, quantity_step=0.01)
        result = calculate_position_size(
            mode=SizingMode.FIXED, equity=100_000.0, config=cfg,
            raw_quantity=3.14,
        )
        assert abs(result - 3.14) < 0.01

    def test_percent_equity_mode_with_stop(self):
        cfg = _cfg(mode=SizingMode.PERCENT_EQUITY, risk_per_trade_pct=1.0)
        result = calculate_position_size(
            mode=SizingMode.PERCENT_EQUITY,
            equity=100_000.0,
            config=cfg,
            entry=100.0,
            stop_loss=90.0,
        )
        # risk=1000, dist=10, qty=100
        assert abs(result - 100.0) < 0.01

    def test_percent_equity_mode_no_stop_falls_back_to_fixed(self):
        cfg = _cfg(mode=SizingMode.PERCENT_EQUITY, quantity_step=0.01)
        result = calculate_position_size(
            mode=SizingMode.PERCENT_EQUITY, equity=100_000.0, config=cfg,
            raw_quantity=7.5,
        )
        assert result == 7.5

    def test_volatility_adjusted_mode(self):
        cfg = _cfg(mode=SizingMode.VOLATILITY_ADJUSTED, risk_per_trade_pct=1.0, atr_multiplier=2.0)
        result = calculate_position_size(
            mode=SizingMode.VOLATILITY_ADJUSTED, equity=50_000.0, config=cfg, atr=5.0,
        )
        # risk=500, atr_adj=10, qty=50
        assert abs(result - 50.0) < 0.01

    def test_unknown_mode_falls_back_to_fixed(self):
        """An invalid/unknown SizingMode value should fall back gracefully."""
        cfg = _cfg(quantity_step=0.01)
        # Pass an invalid string as mode (coerced via Enum — bypass by passing value directly)
        result = calculate_position_size(
            mode="INVALID_MODE",  # type: ignore[arg-type]
            equity=100_000.0,
            config=cfg,
            raw_quantity=2.0,
        )
        assert result == 2.0


# ---------------------------------------------------------------------------
# SizerConfig.from_env
# ---------------------------------------------------------------------------

class TestSizerConfigFromEnv:
    def test_defaults(self, monkeypatch):
        for key in [
            "TRADINGAGENTS_SIZING_MODE", "TRADINGAGENTS_RISK_PER_TRADE_PCT",
            "TRADINGAGENTS_ATR_MULTIPLIER", "TRADINGAGENTS_MIN_QUANTITY",
            "TRADINGAGENTS_MAX_QUANTITY", "TRADINGAGENTS_MAX_ORDER_QTY",
            "TRADINGAGENTS_QUANTITY_STEP",
        ]:
            monkeypatch.delenv(key, raising=False)

        cfg = SizerConfig.from_env()
        assert cfg.mode == SizingMode.FIXED
        assert cfg.risk_per_trade_pct == 1.0
        assert cfg.atr_multiplier == 2.0
        assert cfg.min_quantity == 0.01
        assert cfg.quantity_step == 0.01

    def test_custom_values(self, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_SIZING_MODE", "percent_equity")
        monkeypatch.setenv("TRADINGAGENTS_RISK_PER_TRADE_PCT", "2.5")
        monkeypatch.setenv("TRADINGAGENTS_ATR_MULTIPLIER", "3.0")
        monkeypatch.setenv("TRADINGAGENTS_MIN_QUANTITY", "0.1")
        monkeypatch.setenv("TRADINGAGENTS_MAX_QUANTITY", "50.0")
        monkeypatch.setenv("TRADINGAGENTS_QUANTITY_STEP", "0.5")

        cfg = SizerConfig.from_env()
        assert cfg.mode == SizingMode.PERCENT_EQUITY
        assert cfg.risk_per_trade_pct == 2.5
        assert cfg.atr_multiplier == 3.0
        assert cfg.min_quantity == 0.1
        assert cfg.max_quantity == 50.0
        assert cfg.quantity_step == 0.5

    def test_invalid_mode_defaults_to_fixed(self, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_SIZING_MODE", "gibberish")
        cfg = SizerConfig.from_env()
        assert cfg.mode == SizingMode.FIXED

    def test_max_quantity_falls_back_to_max_order_qty(self, monkeypatch):
        """When TRADINGAGENTS_MAX_QUANTITY not set, uses TRADINGAGENTS_MAX_ORDER_QTY."""
        monkeypatch.delenv("TRADINGAGENTS_MAX_QUANTITY", raising=False)
        monkeypatch.setenv("TRADINGAGENTS_MAX_ORDER_QTY", "30.0")
        cfg = SizerConfig.from_env()
        assert cfg.max_quantity == 30.0


# ---------------------------------------------------------------------------
# TradeIntent — sizing fields
# ---------------------------------------------------------------------------

class TestTradeIntentSizingFields:
    def test_default_sizing_fields_are_none(self):
        intent = TradeIntent(
            symbol="BTC", instrument_type="crypto", signal="BUY", quantity=1.0
        )
        assert intent.raw_quantity is None
        assert intent.sized_quantity is None
        assert intent.sizing_mode is None

    def test_sizing_fields_can_be_set(self):
        intent = TradeIntent(
            symbol="BTC", instrument_type="crypto", signal="BUY", quantity=0.5,
            raw_quantity=1.0, sized_quantity=0.5, sizing_mode="percent_equity"
        )
        assert intent.raw_quantity == 1.0
        assert intent.sized_quantity == 0.5
        assert intent.sizing_mode == "percent_equity"


# ---------------------------------------------------------------------------
# Engine integration — TRADINGAGENTS_SIZING_ENABLED
# ---------------------------------------------------------------------------

class TestEngineSizingIntegration:
    """Verify the sizer is called when enabled and skipped when disabled."""

    def _make_intent(self, qty: float = 5.0) -> TradeIntent:
        return TradeIntent(
            symbol="BTC",
            instrument_type="crypto",
            signal="BUY",
            quantity=qty,
            suggested_entry=50_000.0,
            suggested_stop_loss=49_000.0,
            confidence=1.0,
        )

    def _common_patches(self, monkeypatch, tmp_path):
        """Apply all env patches needed to isolate the engine from external services."""
        monkeypatch.setenv("TRADINGAGENTS_ALLOW_LIVE", "false")
        monkeypatch.setenv("TRADINGAGENTS_SQLITE_STATE_FILE", str(tmp_path / "port.db"))
        monkeypatch.setenv("TRADINGAGENTS_CORRELATION_CHECK_ENABLED", "false")
        monkeypatch.setenv("TRADINGAGENTS_LIVENESS_CHECK_ENABLED", "false")
        # Set high caps so GRM doesn't interfere with sizing tests
        monkeypatch.setenv("MAX_SYMBOL_EXPOSURE_USD", "999999999")
        monkeypatch.setenv("MAX_DAILY_LOSS_USD", "999999999")
        from tradingagents.execution.global_risk import GlobalRiskMonitor
        GlobalRiskMonitor.reset_instance()

    def test_sizing_disabled_by_default_quantity_unchanged(self, monkeypatch, tmp_path):
        """With TRADINGAGENTS_SIZING_ENABLED=false, quantity on intent must == original."""
        monkeypatch.delenv("TRADINGAGENTS_SIZING_ENABLED", raising=False)
        self._common_patches(monkeypatch, tmp_path)

        from tradingagents.execution.engine import execute_trade

        intent = self._make_intent(qty=5.0)

        with patch("tradingagents.execution.engine.resolve_broker") as mock_broker, \
             patch("tradingagents.execution.engine.DeterministicRiskGate") as mock_rg, \
             patch("tradingagents.execution.engine.send_notification"):

            mock_broker.return_value = MagicMock(name="paper_mock")
            mock_broker.return_value.name = "paper_mock"
            mock_rg.from_env.return_value.evaluate.return_value = MagicMock(
                approved=True, rejection_reason=None, warnings=[]
            )

            result = execute_trade(intent, paper=True)

        # Intent quantity should remain 5.0 (untouched)
        assert intent.quantity == 5.0
        assert intent.sized_quantity is None

    def test_sizing_enabled_modifies_quantity(self, monkeypatch, tmp_path):
        """With TRADINGAGENTS_SIZING_ENABLED=true and percent_equity, quantity changes."""
        monkeypatch.setenv("TRADINGAGENTS_SIZING_ENABLED", "true")
        monkeypatch.setenv("TRADINGAGENTS_SIZING_MODE", "percent_equity")
        monkeypatch.setenv("TRADINGAGENTS_RISK_PER_TRADE_PCT", "1.0")
        monkeypatch.setenv("TRADINGAGENTS_MIN_QUANTITY", "0.01")
        monkeypatch.setenv("TRADINGAGENTS_MAX_QUANTITY", "100.0")
        monkeypatch.setenv("TRADINGAGENTS_QUANTITY_STEP", "0.01")
        monkeypatch.setenv("TRADINGAGENTS_PAPER_INITIAL_BALANCE", "100000")
        self._common_patches(monkeypatch, tmp_path)

        from tradingagents.execution.engine import execute_trade

        # entry=50000, stop=49000 → dist=1000
        # equity=100000 (fresh PM, no positions), risk=1.0% → risk_amt=1000 → qty=1.0
        intent = self._make_intent(qty=5.0)

        with patch("tradingagents.execution.engine.resolve_broker") as mock_broker, \
             patch("tradingagents.execution.engine.DeterministicRiskGate") as mock_rg, \
             patch("tradingagents.execution.engine.send_notification"):

            mock_broker.return_value = MagicMock(name="paper_mock")
            mock_broker.return_value.name = "paper_mock"
            mock_rg.from_env.return_value.evaluate.return_value = MagicMock(
                approved=True, rejection_reason=None, warnings=[]
            )

            execute_trade(intent, paper=True)

        # sizing_mode should be set, quantity should have been modified
        assert intent.sizing_mode == "percent_equity"
        assert intent.raw_quantity == 5.0
        assert intent.sized_quantity is not None
        # 1% of 100k / 1000 stop-dist = 1.0
        assert abs(intent.sized_quantity - 1.0) < 0.01
