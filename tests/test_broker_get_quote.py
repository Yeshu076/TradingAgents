"""Tests for DeltaBroker.get_quote() and DhanBroker.get_quote() (GAP-11)."""
import pytest
import json
from unittest.mock import MagicMock, patch
import requests

from tradingagents.execution.delta_broker import DeltaBroker
from tradingagents.execution.dhan_broker import DhanBroker


def _mock_resp(data, status_code=200):
    r = MagicMock(spec=requests.Response)
    r.status_code = status_code
    r.ok = status_code < 400
    r.json.return_value = data
    r.headers = {"content-type": "application/json"}
    r.text = json.dumps(data)
    return r


@pytest.fixture
def delta(monkeypatch):
    monkeypatch.setenv("DELTA_API_KEY", "test-key")
    monkeypatch.setenv("DELTA_API_SECRET", "test-secret")
    return DeltaBroker()


@pytest.fixture
def dhan(monkeypatch):
    monkeypatch.setenv("DHAN_CLIENT_ID", "test-client")
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "test-token")
    return DhanBroker()


class TestDeltaGetQuote:
    def test_returns_bid_ask_last_from_quotes(self, delta):
        payload = {
            "result": {
                "quotes": {"best_bid": "29900", "best_ask": "30100"},
                "close": "30000",
            }
        }
        resp = _mock_resp(payload)
        with patch("tradingagents.execution.delta_broker.execute_with_resilience", return_value=resp):
            with patch("tradingagents.execution.delta_broker.DeltaBroker._headers", return_value={}):
                quote = delta.get_quote("BTCUSD")
        assert quote["bid"] == pytest.approx(29900.0)
        assert quote["ask"] == pytest.approx(30100.0)
        assert quote["last"] == pytest.approx(30000.0)

    def test_falls_back_to_last_when_bid_ask_missing(self, delta):
        payload = {"result": {"close": "2350.0"}}
        resp = _mock_resp(payload)
        with patch("tradingagents.execution.delta_broker.execute_with_resilience", return_value=resp):
            with patch("tradingagents.execution.delta_broker.DeltaBroker._headers", return_value={}):
                quote = delta.get_quote("XAUUSD")
        assert quote["last"] == pytest.approx(2350.0)
        assert quote["ask"] == pytest.approx(2350.0 * 1.001)
        assert quote["bid"] == pytest.approx(2350.0 * 0.999)

    def test_raises_on_http_error(self, delta):
        resp = _mock_resp({}, status_code=404)
        resp.ok = False
        with patch("tradingagents.execution.delta_broker.execute_with_resilience", return_value=resp):
            with patch("tradingagents.execution.delta_broker.DeltaBroker._headers", return_value={}):
                with pytest.raises(Exception):
                    delta.get_quote("UNKNOWN")


class TestDhanGetQuote:
    def test_returns_bid_ask_from_ltp(self, dhan):
        ltp_val = 185.50
        payload = {
            "data": {
                "NSE_FNO": {
                    "NIFTY26APR24500CE": {"last_price": str(ltp_val)}
                }
            }
        }
        resp = _mock_resp(payload)
        with patch("tradingagents.execution.dhan_broker.execute_with_resilience", return_value=resp):
            quote = dhan.get_quote("NIFTY26APR24500CE")
        assert quote["last"] == pytest.approx(ltp_val)
        spread = ltp_val * 0.001
        assert quote["ask"] == pytest.approx(ltp_val + spread)
        assert quote["bid"] == pytest.approx(ltp_val - spread)

    def test_raises_on_api_failure(self, dhan):
        with patch("tradingagents.execution.dhan_broker.execute_with_resilience",
                   side_effect=RuntimeError("Timeout")):
            with pytest.raises(RuntimeError):
                dhan.get_quote("NIFTY26APR24500CE")
