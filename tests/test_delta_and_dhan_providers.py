import os

from tradingagents.dataflows.delta_exchange import (
    get_delta_crypto_derivatives_snapshot,
    normalize_delta_symbol,
)
from tradingagents.dataflows.dhan_option_chain import get_dhan_option_chain_snapshot


class _MockResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status={self.status_code}")

    def json(self):
        return self._data


def test_normalize_delta_symbol_variants():
    assert normalize_delta_symbol("BTC-USD") == "BTCUSD"
    assert normalize_delta_symbol("ETH/USDT") == "ETHUSD"
    assert normalize_delta_symbol("SOLUSD") == "SOLUSD"


def test_delta_snapshot_parsing(monkeypatch):
    def _fake_get(url, params=None, timeout=15):
        if url.endswith("/v2/products/BTCUSD"):
            return _MockResponse({"result": {"id": 84, "symbol": "BTCUSD"}})
        if url.endswith("/v2/tickers"):
            return _MockResponse(
                {
                    "result": [
                        {
                            "product_id": 84,
                            "symbol": "BTCUSD",
                            "mark_price": "62001.5",
                            "last_price": "61990.0",
                            "open_interest": "12345",
                            "funding_rate": "0.0002",
                        }
                    ]
                }
            )
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("tradingagents.dataflows.delta_exchange.requests.get", _fake_get)

    text = get_delta_crypto_derivatives_snapshot("BTC-USD")
    assert "Crypto Derivatives Snapshot (delta)" in text
    assert "Symbol: BTCUSD" in text
    assert "Funding Rate:" in text
    assert "Open Interest:" in text


def test_dhan_option_chain_snapshot_parsing(monkeypatch):
    monkeypatch.setenv("DHAN_CLIENT_ID", "test_client")
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "test_token")

    def _fake_post(url, json=None, headers=None, timeout=20):
        if url.endswith("/optionchain/expirylist"):
            return _MockResponse({"data": ["2026-04-02", "2026-04-09"]})
        if url.endswith("/optionchain"):
            return _MockResponse(
                {
                    "data": {
                        "oc": {
                            "22500": {
                                "ce": {"ltp": 120.5, "oi": 10000, "iv": 12.3},
                                "pe": {"ltp": 130.0, "oi": 9500, "iv": 13.1},
                            },
                            "22600": {
                                "ce": {"ltp": 95.0, "oi": 14000, "iv": 11.2},
                                "pe": {"ltp": 160.0, "oi": 12500, "iv": 14.0},
                            },
                        }
                    }
                }
            )
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("tradingagents.dataflows.dhan_option_chain.requests.post", _fake_post)

    text = get_dhan_option_chain_snapshot("NIFTY", top_n=1)
    assert "Option Chain Snapshot (dhan) for NIFTY" in text
    assert "Top strikes by total OI" in text
    assert "Strike=" in text
