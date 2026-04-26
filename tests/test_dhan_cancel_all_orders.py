"""Tests for DhanBroker.cancel_all_orders() implementation."""
import json
import pytest
from unittest.mock import MagicMock, patch
import requests

from tradingagents.execution.dhan_broker import DhanBroker


@pytest.fixture
def broker(monkeypatch):
    monkeypatch.setenv("DHAN_CLIENT_ID", "test-client")
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "test-token")
    return DhanBroker()


def _mock_response(data, status_code=200):
    r = MagicMock(spec=requests.Response)
    r.status_code = status_code
    r.ok = status_code < 400
    r.json.return_value = data
    r.headers = {"content-type": "application/json"}
    r.text = json.dumps(data)
    return r


def test_cancel_all_orders_returns_ok_when_no_open_orders(broker):
    empty_response = _mock_response([])
    with patch("tradingagents.execution.dhan_broker.execute_with_resilience", return_value=empty_response):
        result = broker.cancel_all_orders()
    assert result["cancelled"] == 0
    assert result["status"] == "ok"


def test_cancel_all_orders_cancels_pending_orders(broker):
    order_book = [
        {"orderId": "ORD001", "orderStatus": "PENDING", "tradingSymbol": "NIFTY25SEP24500CE"},
        {"orderId": "ORD002", "orderStatus": "TRANSIT", "tradingSymbol": "NIFTY25SEP24600PE"},
    ]
    list_resp = _mock_response(order_book)
    cancel_resp = _mock_response({"status": "CANCELLED"})

    call_count = 0

    def mock_resilience(fn, operation_name=""):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return list_resp
        return cancel_resp

    with patch("tradingagents.execution.dhan_broker.execute_with_resilience", side_effect=mock_resilience):
        result = broker.cancel_all_orders()

    assert result["cancelled"] == 2
    assert result["failed"] == 0
    assert result["status"] == "ok"


def test_cancel_all_orders_filters_by_symbol(broker):
    order_book = [
        {"orderId": "ORD001", "orderStatus": "PENDING", "tradingSymbol": "NIFTY25SEP24500CE"},
        {"orderId": "ORD002", "orderStatus": "PENDING", "tradingSymbol": "NIFTY25SEP24600PE"},
    ]
    list_resp = _mock_response(order_book)
    cancel_resp = _mock_response({"status": "CANCELLED"})

    call_count = 0

    def mock_resilience(fn, operation_name=""):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return list_resp
        return cancel_resp

    with patch("tradingagents.execution.dhan_broker.execute_with_resilience", side_effect=mock_resilience):
        result = broker.cancel_all_orders(symbol="NIFTY25SEP24500CE")

    # Only 1 of the 2 orders matches the symbol filter
    assert result["cancelled"] == 1
    assert result["failed"] == 0


def test_cancel_all_orders_skips_non_cancellable_statuses(broker):
    order_book = [
        {"orderId": "ORD001", "orderStatus": "FILLED", "tradingSymbol": "NIFTY25SEP24500CE"},
        {"orderId": "ORD002", "orderStatus": "CANCELLED", "tradingSymbol": "NIFTY25SEP24600PE"},
    ]
    list_resp = _mock_response(order_book)

    with patch("tradingagents.execution.dhan_broker.execute_with_resilience", return_value=list_resp):
        result = broker.cancel_all_orders()

    assert result["cancelled"] == 0
    assert result["status"] == "ok"


def test_cancel_all_orders_handles_individual_cancel_failure(broker):
    order_book = [
        {"orderId": "ORD001", "orderStatus": "PENDING", "tradingSymbol": "NIFTY25SEP24500CE"},
        {"orderId": "ORD002", "orderStatus": "PENDING", "tradingSymbol": "NIFTY25SEP24600PE"},
    ]
    list_resp = _mock_response(order_book)
    ok_resp = _mock_response({"status": "CANCELLED"})
    fail_resp = MagicMock(spec=requests.Response)
    fail_resp.status_code = 500
    fail_resp.ok = False
    fail_resp.text = "Internal error"
    fail_resp.headers = {"content-type": "text/plain"}

    call_count = 0

    def mock_resilience(fn, operation_name=""):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return list_resp
        if call_count == 2:
            return ok_resp
        raise RuntimeError("Network error on second cancel")

    with patch("tradingagents.execution.dhan_broker.execute_with_resilience", side_effect=mock_resilience):
        result = broker.cancel_all_orders()

    assert result["cancelled"] == 1
    assert result["failed"] == 1
    assert result["status"] == "partial"


def test_cancel_all_orders_handles_order_book_fetch_failure(broker):
    def mock_resilience(fn, operation_name=""):
        raise RuntimeError("API timeout")

    with patch("tradingagents.execution.dhan_broker.execute_with_resilience", side_effect=mock_resilience):
        result = broker.cancel_all_orders()

    assert result["status"] == "error"
    assert result["cancelled"] == 0
    assert "API timeout" in result["error"]
