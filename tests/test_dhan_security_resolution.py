import json

from tradingagents.execution.dhan_broker import DhanBroker


class _DummyResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {"ok": True}
        self.text = json.dumps(self._payload)
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._payload


def test_dhan_auto_resolves_security_id(monkeypatch):
    monkeypatch.setenv("DHAN_CLIENT_ID", "cid")
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "token")

    calls = {"orders": 0}

    def _fake_post(url, json=None, headers=None, timeout=None):
        if url.endswith("/v2/optionchain/expirylist"):
            return _DummyResponse(payload={"data": ["2025-09-04", "2025-09-11"]})
        if url.endswith("/v2/optionchain"):
            return _DummyResponse(
                payload={
                    "data": {
                        "oc": {
                            "24500": {
                                "ce": {"securityId": 987654, "oi": 1000},
                                "pe": {"securityId": 123456, "oi": 1000},
                            }
                        }
                    }
                }
            )
        if url.endswith("/orders"):
            calls["orders"] += 1
            calls["payload"] = json
            return _DummyResponse(payload={"orderId": "oid-1"})
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("tradingagents.execution.dhan_broker.requests.post", _fake_post)

    broker = DhanBroker()
    result = broker.place_market_order(
        symbol="NIFTY25SEP24500CE",
        side="BUY",
        quantity=1,
        instrument_type="options",
    )

    assert result["orderId"] == "oid-1"
    assert calls["orders"] == 1
    assert str(calls["payload"].get("securityId")) == "987654"
