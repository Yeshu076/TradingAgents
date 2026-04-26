"""
tests/test_limit_orders.py

F-07: Limit order support – comprehensive test suite.

Coverage areas:
  1. TradeIntent model — new OrderType / TimeInForce fields
  2. PendingOrderStore — upsert / mark_status / get_pending / get_expired / cleanup
  3. DeltaBroker — place_limit_order, cancel_order, get_order_status (mocked HTTP)
  4. DhanBroker  — place_limit_order, cancel_order, get_order_status (mocked HTTP)
  5. Engine (paper mode) — limit order records PendingOrder with status=filled
  6. Engine (live mode)  — routes to place_limit_order and stores PendingOrder
  7. TIF watcher         — cancels expired unfilled limit orders, optional market fallback
  8. Edge/failure paths  — partial data, PendingOrderStore failures
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import time
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Path helpers — ensure repo root is importable
# ---------------------------------------------------------------------------
import sys
repo_root = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, repo_root)

from tradingagents.execution.models import (
    OrderType, TimeInForce, PendingOrder, TradeIntent
)
from tradingagents.execution.pending_orders import PendingOrderStore


# ===========================================================================
# 1. TradeIntent Model tests
# ===========================================================================

class TestTradeIntentModel:
    def _base_intent(self, **overrides) -> TradeIntent:
        kwargs = dict(
            symbol="BTCUSDT",
            signal="bullish",
            quantity=1.0,
            instrument_type="crypto",
        )
        kwargs.update(overrides)
        return TradeIntent(**kwargs)

    def test_defaults_to_market_day(self):
        intent = self._base_intent()
        assert intent.order_type == OrderType.MARKET
        assert intent.time_in_force == TimeInForce.DAY
        assert intent.limit_price is None
        assert intent.tif_seconds is None

    def test_limit_order_fields(self):
        intent = self._base_intent(
            order_type=OrderType.LIMIT,
            limit_price=65_000.0,
            time_in_force=TimeInForce.GTC,
            tif_seconds=300,
        )
        assert intent.order_type == OrderType.LIMIT
        assert intent.limit_price == 65_000.0
        assert intent.time_in_force == TimeInForce.GTC
        assert intent.tif_seconds == 300

    def test_bracket_type(self):
        intent = self._base_intent(order_type=OrderType.BRACKET)
        assert intent.order_type == OrderType.BRACKET

    def test_ioc_fok_enum_values(self):
        assert TimeInForce.IOC.value == "IOC"
        assert TimeInForce.FOK.value == "FOK"

    def test_order_type_enum_values(self):
        assert OrderType.MARKET.value == "market"
        assert OrderType.LIMIT.value == "limit"
        assert OrderType.BRACKET.value == "bracket"


# ===========================================================================
# 2. PendingOrderStore tests
# ===========================================================================

class TestPendingOrderStore:
    def _tmp_store(self) -> PendingOrderStore:
        self._tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(self._tmpdir, "pending.db")
        return PendingOrderStore(db_path)

    def _make_order(self, order_id: str = "ord-1", status: str = "pending", expires_at=None) -> PendingOrder:
        return PendingOrder(
            order_id=order_id,
            symbol="BTCUSDT",
            side="BUY",
            quantity=1.0,
            limit_price=65_000.0,
            instrument_type="crypto",
            broker_name="delta",
            placed_at=int(time.time()),
            expires_at=expires_at,
            status=status,
            exec_key="ek-1",
        )

    def test_upsert_and_retrieve(self):
        store = self._tmp_store()
        order = self._make_order("a")
        store.upsert(order)
        pending = store.get_pending()
        assert len(pending) == 1
        assert pending[0].order_id == "a"

    def test_get_pending_filters_terminal(self):
        store = self._tmp_store()
        store.upsert(self._make_order("open", "pending"))
        store.upsert(self._make_order("closed", "filled"))
        pending = store.get_pending()
        assert len(pending) == 1
        assert pending[0].order_id == "open"

    def test_mark_status(self):
        store = self._tmp_store()
        store.upsert(self._make_order("x"))
        store.mark_status("x", "filled")
        result = store.get_by_id("x")
        assert result.status == "filled"

    def test_get_by_id_missing_returns_none(self):
        store = self._tmp_store()
        assert store.get_by_id("nonexistent") is None

    def test_get_expired_finds_overdue(self):
        store = self._tmp_store()
        expired_ts = int(time.time()) - 10  # 10 s in the past
        future_ts  = int(time.time()) + 3600
        store.upsert(self._make_order("expired", expires_at=expired_ts))
        store.upsert(self._make_order("future",  expires_at=future_ts))
        expired = store.get_expired()
        assert len(expired) == 1
        assert expired[0].order_id == "expired"

    def test_get_expired_no_expiry_not_returned(self):
        store = self._tmp_store()
        store.upsert(self._make_order("noexp", expires_at=None))
        assert store.get_expired() == []

    def test_count_pending(self):
        store = self._tmp_store()
        store.upsert(self._make_order("p1"))
        store.upsert(self._make_order("p2"))
        assert store.count_pending() == 2

    def test_cleanup_terminal(self):
        store = self._tmp_store()
        old_ts = int(time.time()) - 90_000  # 25 h ago
        order = self._make_order("old", "filled")
        # Manually override placed_at via raw SQL connection
        store.upsert(order)
        with sqlite3.connect(store._db_path) as conn:
            conn.execute("UPDATE pending_orders SET placed_at = ? WHERE order_id = 'old'", (old_ts,))
            conn.commit()
        deleted = store.cleanup_terminal(older_than_seconds=86400)
        assert deleted == 1

    def test_upsert_replace_updates_status(self):
        store = self._tmp_store()
        store.upsert(self._make_order("u1", "pending"))
        updated = self._make_order("u1", "filled")
        store.upsert(updated)
        assert store.get_by_id("u1").status == "filled"


# ===========================================================================
# 3. DeltaBroker — place_limit_order / cancel_order / get_order_status
# ===========================================================================

def _make_response(json_data: dict, status_code: int = 200):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data
    mock.headers = {"content-type": "application/json"}
    mock.raise_for_status = MagicMock()
    return mock


class TestDeltaBrokerLimitOrders:
    def _broker(self):
        """Construct DeltaBroker using env vars (matching actual no-arg __init__)."""
        from tradingagents.execution.delta_broker import DeltaBroker
        with patch.dict(os.environ, {
            "DELTA_API_KEY": "K",
            "DELTA_API_SECRET": "S",
            "DELTA_BASE_URL": "https://test.delta.exchange",
        }):
            return DeltaBroker()

    @patch("tradingagents.execution.delta_broker.requests.get")
    @patch("tradingagents.execution.delta_broker.requests.post")
    def test_place_limit_order_success(self, mock_post, mock_get):
        # _resolve_product_id does a GET; mock it to return a product_id
        mock_get.return_value = _make_response({"result": {"id": 1234}})
        mock_post.return_value = _make_response({
            "result": {"id": "42", "state": "open"}
        })
        with patch.dict(os.environ, {
            "DELTA_API_KEY": "K", "DELTA_API_SECRET": "S",
            "DELTA_BASE_URL": "https://test.delta.exchange",
        }):
            from tradingagents.execution.delta_broker import DeltaBroker
            broker = DeltaBroker()
        result = broker.place_limit_order(
            symbol="BTCUSDT", side="BUY", quantity=1, price=64_000
        )
        assert result["order_id"] == "42"
        assert result["status"] == "open"

    @patch("tradingagents.execution.delta_broker.requests.get")
    @patch("tradingagents.execution.delta_broker.requests.post")
    def test_place_limit_order_ioc(self, mock_post, mock_get):
        mock_get.return_value = _make_response({"result": {"id": 9999}})
        mock_post.return_value = _make_response({"result": {"id": "99", "state": "open"}})
        with patch.dict(os.environ, {
            "DELTA_API_KEY": "K", "DELTA_API_SECRET": "S",
            "DELTA_BASE_URL": "https://test.delta.exchange",
        }):
            from tradingagents.execution.delta_broker import DeltaBroker
            broker = DeltaBroker()
        broker.place_limit_order(
            symbol="BTCUSDT", side="SELL", quantity=0.5, price=70_000,
            time_in_force="IOC"
        )
        import json
        payload = json.loads(mock_post.call_args[1]["data"])
        assert payload["time_in_force"] == "ioc"

    @patch("tradingagents.execution.delta_broker.requests.delete")
    def test_cancel_order_success(self, mock_del):
        mock_del.return_value = _make_response({"result": {"state": "cancelled"}})
        with patch.dict(os.environ, {
            "DELTA_API_KEY": "K", "DELTA_API_SECRET": "S",
            "DELTA_BASE_URL": "https://test.delta.exchange",
        }):
            from tradingagents.execution.delta_broker import DeltaBroker
            broker = DeltaBroker()
        result = broker.cancel_order("42")
        assert result["order_id"] == "42"
        assert result["status"] == "cancelled"

    @patch("tradingagents.execution.delta_broker.requests.get")
    def test_get_order_status_filled(self, mock_get):
        mock_get.return_value = _make_response({"result": {"state": "filled"}})
        with patch.dict(os.environ, {
            "DELTA_API_KEY": "K", "DELTA_API_SECRET": "S",
            "DELTA_BASE_URL": "https://test.delta.exchange",
        }):
            from tradingagents.execution.delta_broker import DeltaBroker
            broker = DeltaBroker()
        assert broker.get_order_status("42") == "FILLED"

    @patch("tradingagents.execution.delta_broker.requests.get")
    def test_get_order_status_cancelled(self, mock_get):
        mock_get.return_value = _make_response({"result": {"state": "cancelled"}})
        with patch.dict(os.environ, {
            "DELTA_API_KEY": "K", "DELTA_API_SECRET": "S",
            "DELTA_BASE_URL": "https://test.delta.exchange",
        }):
            from tradingagents.execution.delta_broker import DeltaBroker
            broker = DeltaBroker()
        assert broker.get_order_status("42") == "CANCELLED"

    @patch("tradingagents.execution.delta_broker.requests.get")
    def test_get_order_status_network_error_returns_unknown(self, mock_get):
        mock_get.side_effect = Exception("network down")
        with patch.dict(os.environ, {
            "DELTA_API_KEY": "K", "DELTA_API_SECRET": "S",
            "DELTA_BASE_URL": "https://test.delta.exchange",
        }):
            from tradingagents.execution.delta_broker import DeltaBroker
            broker = DeltaBroker()
        assert broker.get_order_status("42") == "UNKNOWN"


# ===========================================================================
# 4. DhanBroker — place_limit_order / cancel_order / get_order_status
# ===========================================================================

class TestDhanBrokerLimitOrders:
    def _broker(self):
        """Construct DhanBroker using env vars (matching actual no-arg __init__)."""
        from tradingagents.execution.dhan_broker import DhanBroker
        with patch.dict(os.environ, {
            "DHAN_CLIENT_ID": "CL123",
            "DHAN_ACCESS_TOKEN": "tok",
            "DHAN_BASE_URL": "https://api.dhan.co",
        }):
            return DhanBroker()

    def _make_dhan_response(self, json_data, status_code=200):
        return _make_response(json_data, status_code)

    def _env(self):
        return patch.dict(os.environ, {
            "DHAN_CLIENT_ID": "CL123",
            "DHAN_ACCESS_TOKEN": "tok",
            "DHAN_BASE_URL": "https://api.dhan.co",
        })

    @patch("tradingagents.execution.dhan_broker.requests.post")
    def test_place_limit_order_success(self, mock_post):
        mock_post.return_value = _make_response({"orderId": "D1", "orderStatus": "PENDING"})
        with self._env():
            from tradingagents.execution.dhan_broker import DhanBroker
            broker = DhanBroker()
        result = broker.place_limit_order(
            symbol="NIFTY24APR21000CE", side="BUY", quantity=50, price=120.0,
            instrument_type="options", security_id="12345"
        )
        assert result["order_id"] == "D1"
        assert result["status"] == "PENDING"

    @patch("tradingagents.execution.dhan_broker.requests.post")
    def test_place_limit_order_tick_rounding(self, mock_post):
        mock_post.return_value = _make_response({"orderId": "D2", "orderStatus": "PENDING"})
        with self._env():
            from tradingagents.execution.dhan_broker import DhanBroker
            broker = DhanBroker()
        broker.place_limit_order(
            symbol="NIFTY24APR21000CE", side="BUY", quantity=50, price=100.13,
            instrument_type="options", security_id="12345"
        )
        payload = mock_post.call_args[1]["json"]
        # 100.13 rounds to nearest 0.05 = 100.15 (standard rounding)
        assert payload["price"] == pytest.approx(100.15, abs=0.001)

    @patch("tradingagents.execution.dhan_broker.requests.post")
    def test_place_limit_order_no_security_id_raises(self, mock_post):
        with self._env():
            from tradingagents.execution.dhan_broker import DhanBroker
            broker = DhanBroker()
        with pytest.raises(RuntimeError, match="security_id"):
            broker.place_limit_order(
                symbol="NIFTY24APR21000CE", side="BUY", quantity=50, price=120.0,
                instrument_type="equity",  # NOT options → no security_id resolution
            )

    @patch("tradingagents.execution.dhan_broker.requests.delete")
    def test_cancel_order_success(self, mock_del):
        mock_del.return_value = _make_response({"orderStatus": "CANCELLED"})
        with self._env():
            from tradingagents.execution.dhan_broker import DhanBroker
            broker = DhanBroker()
        result = broker.cancel_order("D1")
        assert result["status"] == "CANCELLED"

    @patch("tradingagents.execution.dhan_broker.requests.get")
    def test_get_order_status_filled(self, mock_get):
        mock_get.return_value = _make_response({"orderStatus": "TRADED"})
        with self._env():
            from tradingagents.execution.dhan_broker import DhanBroker
            broker = DhanBroker()
        assert broker.get_order_status("D1") == "FILLED"

    @patch("tradingagents.execution.dhan_broker.requests.get")
    def test_get_order_status_unknown_state(self, mock_get):
        mock_get.return_value = _make_response({"orderStatus": "WEIRD"})
        with self._env():
            from tradingagents.execution.dhan_broker import DhanBroker
            broker = DhanBroker()
        assert broker.get_order_status("D1") == "UNKNOWN"

    @patch("tradingagents.execution.dhan_broker.requests.get")
    def test_get_order_status_error_returns_unknown(self, mock_get):
        mock_get.side_effect = Exception("timeout")
        with self._env():
            from tradingagents.execution.dhan_broker import DhanBroker
            broker = DhanBroker()
        assert broker.get_order_status("D1") == "UNKNOWN"


# ===========================================================================
# 5. Engine (paper mode) — limit order records PendingOrder
# ===========================================================================

def _build_paper_limit_intent(**overrides) -> TradeIntent:
    kwargs = dict(
        symbol="BTCUSDT",
        signal="bullish",
        quantity=1.0,
        instrument_type="crypto",
        order_type=OrderType.LIMIT,
        limit_price=64_000.0,
        time_in_force=TimeInForce.GTC,
        tif_seconds=300,
    )
    kwargs.update(overrides)
    return TradeIntent(**kwargs)


class TestEnginePaperLimitOrder:
    def test_paper_limit_records_filled_pending_order(self, tmp_path):
        """Paper limit orders are immediately 'filled' but still recorded in PendingOrderStore."""
        db_path = str(tmp_path / "pending.db")
        intent = _build_paper_limit_intent()

        with (
            patch.dict(os.environ, {
                "TRADINGAGENTS_ALLOW_LIVE": "false",
                "TRADINGAGENTS_PENDING_ORDERS_DB": db_path,
            }),
            patch("tradingagents.execution.engine.PositionManager") as MockPM,
            patch("tradingagents.execution.engine.GlobalRiskMonitor") as MockGRM,
            patch("tradingagents.execution.engine.resolve_broker") as mock_rb,
            patch("tradingagents.execution.engine.ExecutionPolicy") as MockPolicy,
            patch("tradingagents.execution.engine.DeterministicRiskGate") as MockRG,
            patch("tradingagents.execution.engine.ExecutionIdempotencyManager") as MockDedup,
            patch("tradingagents.execution.engine.DataLivenessMonitor") as MockLM,
            patch("tradingagents.execution.engine.PortfolioCorrelationGuard") as MockCG,
            patch("tradingagents.execution.engine.safe_journal_append"),
            patch("tradingagents.execution.engine.send_notification"),
            patch("tradingagents.execution.engine.MarginValidator"),
        ):
            # Setup mocks
            mock_broker = MagicMock()
            mock_broker.name = "paper"
            mock_rb.return_value = mock_broker

            mock_pm_instance = MagicMock()
            mock_pm_instance.place_order.return_value = {"status": "ok"}
            mock_pm_instance.get_positions.return_value = []
            mock_pm_instance.get_summary.return_value = {"equity": 10_000}
            mock_pm_instance.get_total_position_heat.return_value = 0.0
            MockPM.from_env.return_value = mock_pm_instance

            mock_policy = MagicMock()
            mock_policy.allow_live_trading = False
            mock_policy.max_order_quantity = 1000
            mock_policy.max_order_notional = 1_000_000
            MockPolicy.from_env.return_value = mock_policy

            mock_rg_instance = MagicMock()
            mock_rg_instance.evaluate.return_value = MagicMock(approved=True, warnings=[])
            MockRG.return_value = mock_rg_instance

            mock_dedup = MagicMock()
            mock_dedup.enabled = False
            mock_dedup.find_recent_success.return_value = None
            mock_dedup.is_duplicate.return_value = (False, "")
            mock_dedup.build_execution_key.return_value = "test-exec-key-paper"
            MockDedup.from_env.return_value = mock_dedup

            mock_grm = MagicMock()
            mock_grm.check_heat_gate.return_value = (True, "")
            mock_grm.check_drawdown.return_value = (True, "")
            mock_grm.check_daily_loss.return_value = (True, "")
            mock_grm.check_exposure.return_value = (True, "")
            mock_grm.report_trade_execution = MagicMock()
            mock_grm.update_portfolio_heat = MagicMock()
            MockGRM.get_instance.return_value = mock_grm

            MockLM.from_env.return_value = MagicMock(check=MagicMock(return_value=(True, [])))
            MockCG.from_env.return_value = MagicMock(check=MagicMock(return_value=(True, "")))

            from tradingagents.execution.engine import execute_trade
            result = execute_trade(intent, broker="paper", paper=True)

        # Verify paper fill result
        assert result.status == "simulated_filled"
        assert result.details.get("order_type") == "limit"
        assert result.details.get("limit_price") == 64_000.0

        # Verify PendingOrderStore has a record
        store = PendingOrderStore(db_path)
        by_id = store.get_by_id(f"paper-{result.details['exec_key']}")
        assert by_id is not None
        assert by_id.status == "filled"
        assert by_id.limit_price == 64_000.0


# ===========================================================================
# 6. Engine (live mode) — routes to place_limit_order
# ===========================================================================

class TestEngineLiveLimitOrder:
    def test_live_limit_order_routes_correctly(self, tmp_path):
        db_path = str(tmp_path / "pending.db")
        intent = _build_paper_limit_intent()

        with (
            patch.dict(os.environ, {
                "TRADINGAGENTS_ALLOW_LIVE": "true",
                "TRADINGAGENTS_PENDING_ORDERS_DB": db_path,
            }),
            patch("tradingagents.execution.engine.PositionManager") as MockPM,
            patch("tradingagents.execution.engine.GlobalRiskMonitor") as MockGRM,
            patch("tradingagents.execution.engine.resolve_broker") as mock_rb,
            patch("tradingagents.execution.engine.ExecutionPolicy") as MockPolicy,
            patch("tradingagents.execution.engine.DeterministicRiskGate") as MockRG,
            patch("tradingagents.execution.engine.ExecutionIdempotencyManager") as MockDedup,
            patch("tradingagents.execution.engine.DataLivenessMonitor") as MockLM,
            patch("tradingagents.execution.engine.PortfolioCorrelationGuard") as MockCG,
            patch("tradingagents.execution.engine.safe_journal_append"),
            patch("tradingagents.execution.engine.send_notification"),
            patch("tradingagents.execution.engine.MarginValidator") as MockMV,
        ):
            mock_broker = MagicMock()
            mock_broker.name = "delta"
            mock_broker.place_limit_order.return_value = {
                "order_id": "live-123", "status": "open"
            }
            mock_rb.return_value = mock_broker

            mock_pm_instance = MagicMock()
            mock_pm_instance.get_positions.return_value = []
            mock_pm_instance.get_summary.return_value = {"equity": 10_000}
            mock_pm_instance.get_total_position_heat.return_value = 0.0
            MockPM.from_env.return_value = mock_pm_instance

            mock_policy = MagicMock()
            mock_policy.allow_live_trading = True
            mock_policy.max_order_quantity = 1000
            mock_policy.max_order_notional = 1_000_000
            MockPolicy.from_env.return_value = mock_policy

            mock_rg_instance = MagicMock()
            mock_rg_instance.evaluate.return_value = MagicMock(approved=True, warnings=[])
            MockRG.return_value = mock_rg_instance

            mock_dedup = MagicMock()
            mock_dedup.enabled = False
            mock_dedup.find_recent_success.return_value = None
            mock_dedup.is_duplicate.return_value = (False, "")
            mock_dedup.build_execution_key.return_value = "test-exec-key-live"
            MockDedup.from_env.return_value = mock_dedup

            mock_grm = MagicMock()
            mock_grm.check_heat_gate.return_value = (True, "")
            mock_grm.check_drawdown.return_value = (True, "")
            mock_grm.check_daily_loss.return_value = (True, "")
            mock_grm.check_exposure.return_value = (True, "")
            mock_grm.report_trade_execution = MagicMock()
            mock_grm.update_portfolio_heat = MagicMock()
            MockGRM.get_instance.return_value = mock_grm

            MockLM.from_env.return_value = MagicMock(check=MagicMock(return_value=(True, [])))
            MockCG.from_env.return_value = MagicMock(check=MagicMock(return_value=(True, "")))
            _margin_ok = MagicMock()
            _margin_ok.approved = True
            MockMV.return_value = MagicMock(validate=MagicMock(return_value=_margin_ok))

            from tradingagents.execution.engine import execute_trade
            result = execute_trade(intent, broker="delta", paper=False)

        # Limit order was dispatched
        mock_broker.place_limit_order.assert_called_once()
        call_kwargs = mock_broker.place_limit_order.call_args[1]
        assert call_kwargs["price"] == 64_000.0
        assert call_kwargs["time_in_force"] == "GTC"

        # Result should be pending (not submitted like market)
        assert result.status == "pending"
        assert result.details.get("order_type") == "limit"

        # PendingOrderStore should have an entry
        store = PendingOrderStore(db_path)
        assert store.count_pending() == 1
        entry = store.get_pending()[0]
        assert entry.order_id == "live-123"
        assert entry.status == "pending"


# ===========================================================================
# 7. TIF Watcher — cancel expired orders; optional market fallback
# ===========================================================================

class TestTIFWatcher:
    def _make_pending(self, expires_in: float, order_id="tif-1") -> PendingOrder:
        now = time.time()
        return PendingOrder(
            order_id=order_id,
            symbol="BTCUSDT",
            side="BUY",
            quantity=0.1,
            limit_price=50_000,
            instrument_type="crypto",
            broker_name="delta",
            placed_at=int(now),
            expires_at=int(now + expires_in),
            status="pending",
            exec_key="ek-tif",
        )

    def test_tif_watcher_cancels_when_expired(self, tmp_path):
        db_path = str(tmp_path / "tif_cancel.db")
        store = PendingOrderStore(db_path)
        pending = self._make_pending(expires_in=0.05)   # expires in 50ms
        store.upsert(pending)

        mock_broker = MagicMock()
        mock_broker.get_order_status.return_value = "PENDING"
        mock_broker.cancel_order.return_value = {"order_id": pending.order_id, "status": "CANCELLED"}

        with (
            patch.dict(os.environ, {
                "TRADINGAGENTS_PENDING_ORDERS_DB": db_path,
                "TRADINGAGENTS_LIMIT_FALLBACK_MARKET": "false",
            }),
            patch("tradingagents.execution.engine.safe_journal_append"),
        ):
            from tradingagents.execution.engine import _watch_tif_async
            _watch_tif_async(mock_broker, pending)
            time.sleep(0.5)   # Wait for the daemon thread

        mock_broker.cancel_order.assert_called_once_with(pending.order_id, symbol=pending.symbol)
        stored = store.get_by_id(pending.order_id)
        assert stored.status == "expired"

    def test_tif_watcher_does_not_cancel_if_filled(self, tmp_path):
        db_path = str(tmp_path / "tif_filled.db")
        store = PendingOrderStore(db_path)
        pending = self._make_pending(expires_in=0.05, order_id="filled-order")
        store.upsert(pending)

        mock_broker = MagicMock()
        mock_broker.get_order_status.return_value = "FILLED"

        with (
            patch.dict(os.environ, {"TRADINGAGENTS_PENDING_ORDERS_DB": db_path}),
            patch("tradingagents.execution.engine.safe_journal_append"),
        ):
            from tradingagents.execution.engine import _watch_tif_async
            _watch_tif_async(mock_broker, pending)
            time.sleep(0.5)

        mock_broker.cancel_order.assert_not_called()
        stored = store.get_by_id("filled-order")
        assert stored.status == "filled"

    def test_tif_watcher_market_fallback(self, tmp_path):
        db_path = str(tmp_path / "tif_fallback.db")
        store = PendingOrderStore(db_path)
        pending = self._make_pending(expires_in=0.05, order_id="fallback-order")
        store.upsert(pending)

        mock_broker = MagicMock()
        mock_broker.get_order_status.return_value = "PENDING"
        mock_broker.cancel_order.return_value = {"status": "CANCELLED"}
        mock_broker.place_market_order.return_value = {"order_id": "mkt-1"}

        with (
            patch.dict(os.environ, {
                "TRADINGAGENTS_PENDING_ORDERS_DB": db_path,
                "TRADINGAGENTS_LIMIT_FALLBACK_MARKET": "true",
            }),
            patch("tradingagents.execution.engine.safe_journal_append"),
        ):
            from tradingagents.execution.engine import _watch_tif_async
            _watch_tif_async(mock_broker, pending)
            time.sleep(0.5)

        mock_broker.cancel_order.assert_called_once()
        mock_broker.place_market_order.assert_called_once()
        # Verify market order placed with correct params
        call_kwargs = mock_broker.place_market_order.call_args[1]
        assert call_kwargs["symbol"] == "BTCUSDT"
        assert call_kwargs["side"] == "BUY"

    def test_tif_watcher_cancel_failure_sets_cancel_failed(self, tmp_path):
        db_path = str(tmp_path / "tif_cancelfail.db")
        store = PendingOrderStore(db_path)
        pending = self._make_pending(expires_in=0.05, order_id="cancel-fail")
        store.upsert(pending)

        mock_broker = MagicMock()
        mock_broker.get_order_status.return_value = "PENDING"
        mock_broker.cancel_order.side_effect = Exception("network error")

        with (
            patch.dict(os.environ, {"TRADINGAGENTS_PENDING_ORDERS_DB": db_path}),
            patch("tradingagents.execution.engine.safe_journal_append"),
        ):
            from tradingagents.execution.engine import _watch_tif_async
            _watch_tif_async(mock_broker, pending)
            time.sleep(0.5)

        stored = store.get_by_id("cancel-fail")
        assert stored.status == "cancel_failed"

    def test_tif_watcher_noop_if_no_expires_at(self, tmp_path):
        """Watcher should not start if expires_at is None."""
        mock_broker = MagicMock()

        with patch.dict(os.environ, {}):
            from tradingagents.execution.engine import _watch_tif_async
            pending = PendingOrder(
                order_id="noexp",
                symbol="x", side="BUY", quantity=1, limit_price=100,
                instrument_type="crypto", broker_name="delta",
                placed_at=int(time.time()), expires_at=None,
                status="pending", exec_key=""
            )
            _watch_tif_async(mock_broker, pending)
            time.sleep(0.1)

        mock_broker.get_order_status.assert_not_called()


# ===========================================================================
# 8. Edge / failure paths
# ===========================================================================

class TestEdgeCases:
    def test_pending_order_store_failure_does_not_block_engine(self, tmp_path):
        """If PendingOrderStore raises, live market orders should still succeed."""
        intent = TradeIntent(
            symbol="BTCUSDT", signal="bullish", quantity=0.1, instrument_type="crypto"
        )

        with (
            patch.dict(os.environ, {
                "TRADINGAGENTS_ALLOW_LIVE": "true",
                "TRADINGAGENTS_PENDING_ORDERS_DB": "/invalid/path/pending.db",
            }),
            patch("tradingagents.execution.engine.PositionManager") as MockPM,
            patch("tradingagents.execution.engine.GlobalRiskMonitor") as MockGRM,
            patch("tradingagents.execution.engine.resolve_broker") as mock_rb,
            patch("tradingagents.execution.engine.ExecutionPolicy") as MockPolicy,
            patch("tradingagents.execution.engine.DeterministicRiskGate") as MockRG,
            patch("tradingagents.execution.engine.ExecutionIdempotencyManager") as MockDedup,
            patch("tradingagents.execution.engine.DataLivenessMonitor") as MockLM,
            patch("tradingagents.execution.engine.PortfolioCorrelationGuard") as MockCG,
            patch("tradingagents.execution.engine.safe_journal_append"),
            patch("tradingagents.execution.engine.send_notification"),
            patch("tradingagents.execution.engine.MarginValidator") as MockMV,
            patch("tradingagents.execution.engine._verify_fill_async"),
        ):
            mock_broker = MagicMock()
            mock_broker.name = "delta"
            mock_broker.place_market_order.return_value = {"order_id": "m1"}
            mock_rb.return_value = mock_broker

            mock_pm_instance = MagicMock()
            mock_pm_instance.get_positions.return_value = []
            mock_pm_instance.get_summary.return_value = {"equity": 10_000}
            mock_pm_instance.get_total_position_heat.return_value = 0.0
            MockPM.from_env.return_value = mock_pm_instance

            mock_policy = MagicMock()
            mock_policy.allow_live_trading = True
            mock_policy.max_order_quantity = 1000
            mock_policy.max_order_notional = 1_000_000
            MockPolicy.from_env.return_value = mock_policy

            mock_rg_instance = MagicMock()
            mock_rg_instance.evaluate.return_value = MagicMock(approved=True, warnings=[])
            MockRG.return_value = mock_rg_instance

            mock_dedup = MagicMock()
            mock_dedup.enabled = False
            mock_dedup.find_recent_success.return_value = None
            mock_dedup.is_duplicate.return_value = (False, "")
            mock_dedup.build_execution_key.return_value = "test-exec-key-edge"
            MockDedup.from_env.return_value = mock_dedup

            mock_grm = MagicMock()
            mock_grm.check_heat_gate.return_value = (True, "")
            mock_grm.check_drawdown.return_value = (True, "")
            mock_grm.check_daily_loss.return_value = (True, "")
            mock_grm.check_exposure.return_value = (True, "")
            mock_grm.report_trade_execution = MagicMock()
            mock_grm.update_portfolio_heat = MagicMock()
            MockGRM.get_instance.return_value = mock_grm

            MockLM.from_env.return_value = MagicMock(check=MagicMock(return_value=(True, [])))
            MockCG.from_env.return_value = MagicMock(check=MagicMock(return_value=(True, "")))
            _margin_ok2 = MagicMock()
            _margin_ok2.approved = True
            MockMV.return_value = MagicMock(validate=MagicMock(return_value=_margin_ok2))

            from tradingagents.execution.engine import execute_trade
            result = execute_trade(intent, broker="delta", paper=False)

        # Market order still succeeds despite invalid DB path
        assert result.status == "submitted"
        mock_broker.place_market_order.assert_called_once()

    def test_base_broker_raises_not_implemented_for_limit(self):
        from tradingagents.execution.base import BrokerBase

        class MinimalBroker(BrokerBase):
            name = "minimal"
            def place_market_order(self, *a, **kw): return {}
            def list_positions(self): return []
            def close_symbol_position(self, *a, **kw): return {}
            def cancel_all_orders(self, *a, **kw): return {}

        b = MinimalBroker()
        with pytest.raises(NotImplementedError, match="place_limit_order"):
            b.place_limit_order("X", "BUY", 1, 100)
        with pytest.raises(NotImplementedError, match="cancel_order"):
            b.cancel_order("1")
        with pytest.raises(NotImplementedError, match="get_order_status"):
            b.get_order_status("1")
