"""
tests/test_mtm_service.py

F-01: Tests for MarkToMarketService, PositionManager MTM methods,
and GlobalRiskMonitor unrealized PnL integration.
"""
from __future__ import annotations

import time
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

import pytest

from tradingagents.execution.position_manager import PositionManager
from tradingagents.execution.global_risk import GlobalRiskMonitor
from tradingagents.execution.mtm_service import MarkToMarketService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pm(tmp_path) -> PositionManager:
    """Fresh PositionManager backed by a temp SQLite DB."""
    return PositionManager(db_path=tmp_path / "portfolio.db", initial_balance=100_000.0)


@pytest.fixture
def grm() -> GlobalRiskMonitor:
    """Fresh GlobalRiskMonitor (in-memory, no Redis)."""
    GlobalRiskMonitor.reset_instance()
    inst = GlobalRiskMonitor.get_instance()
    inst.client = None  # Force in-memory mode
    return inst


def _buy(pm: PositionManager, symbol: str, qty: float, price: float, inst: str = "spot") -> None:
    pm.place_order(symbol, "BUY", qty, price, inst)


def _make_broker(price: float) -> MagicMock:
    broker = MagicMock()
    broker.get_quote.return_value = price
    return broker


# ---------------------------------------------------------------------------
# PositionManager — MTM schema migration
# ---------------------------------------------------------------------------

class TestPositionManagerMTMSchema:
    def test_new_db_has_mtm_columns(self, pm):
        """New DB should have last_price, unrealized_pnl, mtm_updated_ts."""
        _buy(pm, "BTC", 1.0, 50_000.0)
        positions = pm.get_positions()
        assert len(positions) == 1
        pos = positions[0]
        assert "last_price" in pos
        assert "unrealized_pnl" in pos
        assert "mtm_updated_ts" in pos

    def test_default_last_price_is_zero(self, pm):
        _buy(pm, "BTC", 1.0, 50_000.0)
        pos = pm.get_positions()[0]
        assert pos["last_price"] == 0.0
        assert pos["unrealized_pnl"] == 0.0


# ---------------------------------------------------------------------------
# PositionManager — update_mark_to_market
# ---------------------------------------------------------------------------

class TestUpdateMarkToMarket:
    def test_long_position_gain(self, pm):
        """Long 1 BTC @ 50k, mark at 55k → unrealized = +5k."""
        _buy(pm, "BTC", 1.0, 50_000.0)
        pm.update_mark_to_market("BTC", 55_000.0)
        pos = pm.get_positions()[0]
        assert pos["last_price"] == 55_000.0
        assert abs(pos["unrealized_pnl"] - 5_000.0) < 0.01

    def test_long_position_loss(self, pm):
        """Long 2 BTC @ 50k, mark at 47k → unrealized = -6k."""
        _buy(pm, "BTC", 2.0, 50_000.0)
        pm.update_mark_to_market("BTC", 47_000.0)
        pos = pm.get_positions()[0]
        assert abs(pos["unrealized_pnl"] - (-6_000.0)) < 0.01

    def test_short_position_gain(self, pm):
        """Short 1 AAPL @ 200, mark at 190 → unrealized = +10."""
        pm.place_order("AAPL", "BUY", 1.0, 200.0, "equity")
        pm.place_order("AAPL", "SELL", 2.0, 200.0, "equity")  # net short -1
        pm.update_mark_to_market("AAPL", 190.0)
        pos = pm.get_positions()[0]
        # net qty = -1, unrealized = -1 * (190 - 200) = +10
        assert abs(pos["unrealized_pnl"] - 10.0) < 0.01

    def test_bad_price_rejected(self, pm):
        """Price of 0 or negative should not update last_price."""
        _buy(pm, "BTC", 1.0, 50_000.0)
        pm.update_mark_to_market("BTC", 55_000.0)  # set a valid price first
        pm.update_mark_to_market("BTC", 0.0)       # should be rejected
        pos = pm.get_positions()[0]
        assert pos["last_price"] == 55_000.0  # unchanged

    def test_unknown_symbol_no_crash(self, pm):
        """Updating MTM for an unknown symbol should not raise."""
        pm.update_mark_to_market("NONEXISTENT", 100.0)  # no error

    def test_closed_position_not_updated(self, pm):
        """After position is closed (qty=0), MTM update should no-op."""
        _buy(pm, "BTC", 1.0, 50_000.0)
        pm.place_order("BTC", "SELL", 1.0, 51_000.0, "spot")  # close
        pm.update_mark_to_market("BTC", 99_000.0)
        # get_positions only returns open positions; BTC should not appear
        open_positions = [p for p in pm.get_positions() if p["symbol"] == "BTC"]
        assert len(open_positions) == 0

    def test_mtm_ts_updated(self, pm):
        """mtm_updated_ts should be set to a recent timestamp after update."""
        _buy(pm, "BTC", 1.0, 50_000.0)
        before = int(time.time()) - 1
        pm.update_mark_to_market("BTC", 55_000.0)
        # Query raw to get mtm_updated_ts
        with pm._get_conn() as conn:
            row = conn.execute(
                "SELECT mtm_updated_ts FROM positions WHERE symbol = ?", ("BTC",)
            ).fetchone()
        assert row["mtm_updated_ts"] >= before


# ---------------------------------------------------------------------------
# PositionManager — get_portfolio_equity / get_total_unrealized_pnl
# ---------------------------------------------------------------------------

class TestPortfolioEquity:
    def test_equity_with_no_positions(self, pm):
        """Equity with no positions = initial cash."""
        assert pm.get_portfolio_equity() == 100_000.0

    def test_equity_uses_last_price(self, pm):
        """Equity = cash (after trade) + qty × last_price."""
        _buy(pm, "BTC", 1.0, 50_000.0)
        pm.update_mark_to_market("BTC", 55_000.0)
        equity = pm.get_portfolio_equity()
        # cash = 100k - 50k = 50k; market_value = 1 × 55k = 55k → equity = 105k
        assert abs(equity - 105_000.0) < 0.01

    def test_equity_falls_back_to_avg_price_when_no_mtm(self, pm):
        """When last_price = 0, equity uses avg_price as fallback."""
        _buy(pm, "BTC", 1.0, 50_000.0)
        equity = pm.get_portfolio_equity()
        # cash = 50k, market_value = 1 × 50k, equity = 100k
        assert abs(equity - 100_000.0) < 0.01

    def test_total_unrealized_pnl_zero_initially(self, pm):
        _buy(pm, "BTC", 1.0, 50_000.0)
        assert pm.get_total_unrealized_pnl() == 0.0

    def test_total_unrealized_pnl_after_mtm(self, pm):
        _buy(pm, "BTC", 1.0, 50_000.0)
        _buy(pm, "ETH", 10.0, 3_000.0)
        pm.update_mark_to_market("BTC", 55_000.0)  # +5k
        pm.update_mark_to_market("ETH", 2_800.0)   # -2k
        total = pm.get_total_unrealized_pnl()
        assert abs(total - 3_000.0) < 0.01  # +5k - 2k = +3k

    def test_get_summary_uses_stored_last_price(self, pm):
        """get_summary() without mark_prices should use stored last_price."""
        _buy(pm, "BTC", 1.0, 50_000.0)
        pm.update_mark_to_market("BTC", 55_000.0)
        summary = pm.get_summary()
        assert abs(summary["unrealized_pnl"] - 5_000.0) < 0.01

    def test_get_summary_override_wins_over_stored_price(self, pm):
        """Caller override dict should take priority over stored last_price."""
        _buy(pm, "BTC", 1.0, 50_000.0)
        pm.update_mark_to_market("BTC", 55_000.0)
        summary = pm.get_summary(mark_prices={"BTC": 60_000.0})
        assert abs(summary["unrealized_pnl"] - 10_000.0) < 0.01


# ---------------------------------------------------------------------------
# GlobalRiskMonitor — unrealized PnL integration
# ---------------------------------------------------------------------------

class TestGRMUnrealizedPnL:
    def test_unrealized_pnl_default_zero(self, grm):
        assert grm.unrealized_pnl == 0.0

    def test_update_unrealized_pnl(self, grm):
        grm.update_unrealized_pnl(-2_500.0)
        assert grm.unrealized_pnl == -2_500.0

    def test_unrealized_drawdown_blocks_trade_when_enabled(self, grm):
        """When MAX_UNREALIZED_DRAWDOWN_USD is set and breached, trade should be blocked."""
        grm.max_unrealized_drawdown_usd = 1_000.0
        grm.update_unrealized_pnl(-1_500.0)  # exceeds -1000
        allowed = grm.evaluate_trade_intent("test_strat", "BTC", 100.0)
        assert not allowed

    def test_unrealized_drawdown_disabled_by_default(self, grm):
        """With limit=0, unrealized losses should NOT block trades."""
        grm.max_unrealized_drawdown_usd = 0.0
        grm.update_unrealized_pnl(-999_999.0)
        # Should pass through (other limits not breached)
        allowed = grm.evaluate_trade_intent("test_strat", "BTC", 100.0)
        assert allowed

    def test_is_globally_safe_respects_unrealized_limit(self, grm):
        grm.max_unrealized_drawdown_usd = 500.0
        grm.update_unrealized_pnl(-600.0)
        assert not grm.is_globally_safe()

    def test_is_globally_safe_ok_when_within_limit(self, grm):
        grm.max_unrealized_drawdown_usd = 500.0
        grm.update_unrealized_pnl(-400.0)
        assert grm.is_globally_safe()


# ---------------------------------------------------------------------------
# MarkToMarketService — run_once()
# ---------------------------------------------------------------------------

class TestMarkToMarketServiceRunOnce:
    def test_updates_position_via_broker_resolver(self, pm, grm):
        """run_once() should call get_quote and update position in PM."""
        _buy(pm, "BTC", 1.0, 50_000.0, "crypto")

        def resolver(symbol, instrument_type):
            return _make_broker(55_000.0) if symbol == "BTC" else None

        svc = MarkToMarketService(pm, grm, broker_resolver=resolver)
        result = svc.run_once()

        assert "BTC" in result["updated"]
        pos = pm.get_positions()[0]
        assert pos["last_price"] == 55_000.0
        assert abs(pos["unrealized_pnl"] - 5_000.0) < 0.01

    def test_failed_quote_lands_in_failed_list(self, pm, grm):
        _buy(pm, "BTC", 1.0, 50_000.0, "crypto")

        def resolver(symbol, instrument_type):
            broker = MagicMock()
            broker.get_quote.side_effect = RuntimeError("API down")
            return broker

        svc = MarkToMarketService(pm, grm, broker_resolver=resolver)
        result = svc.run_once()

        assert "BTC" in result["failed"]
        assert "BTC" not in result["updated"]

    def test_no_resolver_all_skipped(self, pm, grm):
        """Without a resolver, no positions are updated but service doesn't crash."""
        _buy(pm, "BTC", 1.0, 50_000.0, "crypto")
        svc = MarkToMarketService(pm, grm, broker_resolver=None)
        result = svc.run_once()
        # BTC has qty so it won't be in skipped, but get_quote returns None
        assert "BTC" in result["failed"]

    def test_zero_qty_position_is_skipped(self, pm, grm):
        """Positions with qty=0 (closed) should be skipped without calling broker."""
        # Buy and then sell back to close
        _buy(pm, "BTC", 1.0, 50_000.0, "crypto")
        pm.place_order("BTC", "SELL", 1.0, 51_000.0, "crypto")

        resolver_called = []

        def resolver(symbol, instrument_type):
            resolver_called.append(symbol)
            return _make_broker(55_000.0)

        svc = MarkToMarketService(pm, grm, broker_resolver=resolver)
        result = svc.run_once()

        # BTC is closed (qty=0), get_positions returns only open positions
        assert "BTC" not in resolver_called

    def test_risk_monitor_updated_with_total_unrealized(self, pm, grm):
        """After run_once, GRM should reflect the total unrealized PnL."""
        _buy(pm, "BTC", 1.0, 50_000.0, "crypto")
        svc = MarkToMarketService(pm, grm, broker_resolver=lambda s, i: _make_broker(55_000.0))
        svc.run_once()
        assert abs(grm.unrealized_pnl - 5_000.0) < 0.01

    def test_multiple_positions_aggregate_correctly(self, pm, grm):
        """Multiple positions' unrealized PnL aggregates to correct total."""
        _buy(pm, "BTC", 1.0, 50_000.0, "crypto")
        _buy(pm, "ETH", 10.0, 3_000.0, "crypto")

        prices = {"BTC": 55_000.0, "ETH": 2_500.0}

        def resolver(symbol, instrument_type):
            return _make_broker(prices[symbol]) if symbol in prices else None

        svc = MarkToMarketService(pm, grm, broker_resolver=resolver)
        svc.run_once()

        # BTC: +5k, ETH: -5k → net 0
        total = pm.get_total_unrealized_pnl()
        assert abs(total) < 0.01

    def test_disabled_service_does_not_start(self, pm, grm, monkeypatch):
        """With TRADINGAGENTS_MTM_ENABLED=false, start() should no-op."""
        monkeypatch.setenv("TRADINGAGENTS_MTM_ENABLED", "false")
        svc = MarkToMarketService(pm, grm)
        svc.start()
        assert svc._thread is None


# ---------------------------------------------------------------------------
# MarkToMarketService — start/stop thread lifecycle
# ---------------------------------------------------------------------------

class TestMTMServiceLifecycle:
    def test_start_and_stop(self, pm, grm):
        """Service should start a daemon thread and stop cleanly."""
        svc = MarkToMarketService(pm, grm, broker_resolver=None)
        svc._poll_interval = 0.1  # Very fast for testing
        svc.start()
        assert svc._thread is not None
        assert svc._thread.is_alive()

        svc.stop()
        svc.join(timeout=2.0)
        assert not svc._thread.is_alive()

    def test_idempotent_start(self, pm, grm):
        """Calling start() twice should not create two threads."""
        svc = MarkToMarketService(pm, grm, broker_resolver=None)
        svc._poll_interval = 0.1
        svc.start()
        thread_id_1 = id(svc._thread)
        svc.start()  # should be no-op
        thread_id_2 = id(svc._thread)
        assert thread_id_1 == thread_id_2
        svc.stop()
        svc.join(timeout=2.0)
