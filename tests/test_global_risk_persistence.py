"""Tests for GlobalRiskMonitor Redis-backed state persistence."""
import json
import pytest
from unittest.mock import MagicMock, patch
from tradingagents.execution.global_risk import GlobalRiskMonitor


@pytest.fixture(autouse=True)
def reset_singleton():
    """Ensure each test starts with a fresh singleton."""
    GlobalRiskMonitor.reset_instance()
    yield
    GlobalRiskMonitor.reset_instance()


def test_is_globally_safe_returns_true_by_default():
    grm = GlobalRiskMonitor()
    assert grm.is_globally_safe() is True


def test_is_globally_safe_false_when_daily_loss_breached():
    grm = GlobalRiskMonitor()
    grm._mem_daily_pnl = -grm.max_daily_loss_usd - 1
    assert grm.is_globally_safe() is False


def test_evaluate_trade_passes_on_fresh_state():
    grm = GlobalRiskMonitor()
    result = grm.evaluate_trade_intent("test_strategy", "BTCUSD", 100.0)
    assert result is True


def test_evaluate_trade_rejected_when_daily_loss_breached():
    grm = GlobalRiskMonitor()
    grm._mem_daily_pnl = -99999.0  # Way over limit
    result = grm.evaluate_trade_intent("test_strategy", "BTCUSD", 100.0)
    assert result is False


def test_evaluate_trade_rejected_when_symbol_exposure_breached():
    grm = GlobalRiskMonitor()
    # Pre-load exposure at the cap
    grm._mem_symbol_exposure["BTCUSD"] = grm.max_symbol_exposure_usd
    result = grm.evaluate_trade_intent("test_strategy", "BTCUSD", 1.0)
    assert result is False


def test_report_trade_execution_updates_mem_symbol_exposure():
    grm = GlobalRiskMonitor()
    grm.report_trade_execution("strat_a", "NIFTY", 500.0)
    assert grm._mem_symbol_exposure.get("NIFTY", 0.0) == 500.0


def test_report_closed_pnl_updates_daily_pnl_and_frees_exposure():
    grm = GlobalRiskMonitor()
    grm._mem_symbol_exposure["NIFTY"] = 1000.0
    grm.report_closed_pnl("strat_a", "NIFTY", notional_freed=500.0, realized_pnl=-200.0)
    assert grm._mem_symbol_exposure["NIFTY"] == 500.0
    assert grm._mem_daily_pnl == -200.0


def test_state_persisted_to_redis_on_report():
    """When Redis is available, report_trade_execution should flush state."""
    grm = GlobalRiskMonitor()
    mock_pipe = MagicMock()
    mock_redis = MagicMock()
    mock_redis.pipeline.return_value = mock_pipe
    mock_pipe.__enter__ = lambda s: s
    mock_pipe.__exit__ = MagicMock(return_value=False)
    grm.client = mock_redis

    grm.report_trade_execution("strat_a", "XAUUSD", 2000.0)

    # pipeline().set() should have been called (state flush)
    assert mock_pipe.set.called


def test_state_loaded_from_redis_on_init():
    """Verify that _load_state_from_redis reads daily_pnl correctly."""
    from datetime import date
    today = date.today().isoformat()

    mock_redis = MagicMock()
    mock_redis.get.side_effect = lambda key: {
        "grm:state_date": today,
        "grm:daily_pnl": "-350.50",
        "grm:symbol_exposure": json.dumps({"NIFTY": 1500.0}),
        "grm:strategy_daily_pnl": json.dumps({"momentum": -100.0}),
    }.get(key)

    grm = GlobalRiskMonitor()
    grm.client = mock_redis
    grm._load_state_from_redis()

    assert grm._mem_daily_pnl == -350.50
    assert grm._mem_symbol_exposure["NIFTY"] == 1500.0
    assert grm._mem_strategy_daily_pnl["momentum"] == -100.0


def test_state_resets_on_new_trading_day():
    """If stored date is yesterday, accumulators must reset."""
    mock_redis = MagicMock()
    mock_pipe = MagicMock()
    mock_redis.pipeline.return_value = mock_pipe
    mock_pipe.__enter__ = lambda s: s
    mock_pipe.__exit__ = MagicMock(return_value=False)
    mock_redis.get.return_value = "1999-01-01"  # Old date

    grm = GlobalRiskMonitor()
    grm._mem_daily_pnl = -999.0  # Simulate stale loaded state
    grm.client = mock_redis
    grm._load_state_from_redis()

    # After new-day reset, pnl should be 0
    assert grm._mem_daily_pnl == 0.0


def test_graceful_degradation_when_redis_down():
    """System must not block trades when Redis is unavailable."""
    grm = GlobalRiskMonitor()
    mock_redis = MagicMock()
    mock_redis.get.side_effect = Exception("Connection refused")
    grm.client = mock_redis

    # Should return True (allow trade) despite Redis failure
    result = grm.evaluate_trade_intent("strat", "BTCUSD", 100.0)
    assert result is True
