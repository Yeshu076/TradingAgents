from tradingagents.dataflows import crypto_derivatives
from tradingagents.dataflows.config import get_config, set_config
from tradingagents.dataflows.interface import route_to_vendor


class _MockResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


def test_normalize_crypto_symbol_variants():
    assert crypto_derivatives.normalize_crypto_symbol("BTC-USD") == "BTCUSDT"
    assert crypto_derivatives.normalize_crypto_symbol("ETH/USDT") == "ETHUSDT"
    assert crypto_derivatives.normalize_crypto_symbol("SOLUSDT") == "SOLUSDT"


def test_binance_crypto_derivatives_snapshot_parsing(monkeypatch):
    def _fake_get(url, params=None, timeout=15):
        if "premiumIndex" in url:
            return _MockResponse(
                {
                    "markPrice": "62000.0",
                    "indexPrice": "61800.0",
                    "lastFundingRate": "0.00010000",
                    "nextFundingTime": 1710000000000,
                }
            )
        if "openInterest" in url:
            return _MockResponse({"openInterest": "123456.78"})
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(crypto_derivatives.requests, "get", _fake_get)
    text = crypto_derivatives.get_binance_crypto_derivatives_snapshot("BTC-USD")

    assert "Crypto Derivatives Snapshot (binance)" in text
    assert "Symbol: BTCUSDT" in text
    assert "Last Funding Rate:" in text
    assert "Open Interest (contracts):" in text
    assert "Mark-Index Basis:" in text


def test_route_to_vendor_fallback_from_binance_to_bybit(monkeypatch):
    original = get_config()
    try:
        updated = original.copy()
        data_vendors = dict(updated.get("data_vendors", {}))
        data_vendors["crypto_derivatives"] = "binance,bybit"
        updated["data_vendors"] = data_vendors
        set_config(updated)

        def _binance_fail(symbol):
            raise RuntimeError("binance down")

        def _bybit_ok(symbol):
            return f"bybit snapshot for {symbol}"

        monkeypatch.setattr(
            "tradingagents.dataflows.interface.get_binance_crypto_derivatives_snapshot",
            _binance_fail,
        )
        monkeypatch.setattr(
            "tradingagents.dataflows.interface.get_bybit_crypto_derivatives_snapshot",
            _bybit_ok,
        )

        # Rebind entries in VENDOR_METHODS through module reference
        from tradingagents.dataflows import interface

        interface.VENDOR_METHODS["get_crypto_derivatives_snapshot"]["binance"] = _binance_fail
        interface.VENDOR_METHODS["get_crypto_derivatives_snapshot"]["bybit"] = _bybit_ok

        result = route_to_vendor("get_crypto_derivatives_snapshot", "BTC-USD")
        assert result == "bybit snapshot for BTC-USD"
    finally:
        set_config(original)
