import json

from tradingagents.execution.delta_broker import DeltaBroker


class _DummyResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {"result": {"id": 101}}
        self.text = json.dumps(self._payload)
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._payload


def test_delta_market_order_sign_and_payload(monkeypatch):
    monkeypatch.setenv("DELTA_API_KEY", "k")
    monkeypatch.setenv("DELTA_API_SECRET", "s")

    calls = {"post": 0}

    def _fake_get(url, timeout=None, headers=None):
        if url.endswith("/v2/products/BTCUSD"):
            return _DummyResponse(payload={"result": {"id": 777}})
        if url.endswith("/v2/positions/margined"):
            return _DummyResponse(payload={"result": []})
        return _DummyResponse()

    def _fake_post(url, data=None, headers=None, timeout=None):
        calls["post"] += 1
        calls["headers"] = headers
        calls["data"] = data
        return _DummyResponse(payload={"success": True})

    monkeypatch.setattr("tradingagents.execution.delta_broker.requests.get", _fake_get)
    monkeypatch.setattr("tradingagents.execution.delta_broker.requests.post", _fake_post)

    broker = DeltaBroker()
    result = broker.place_market_order(
        symbol="BTC-USD",
        side="BUY",
        quantity=1,
        instrument_type="spot",
    )

    assert result["success"] is True
    assert calls["post"] == 1
    assert "signature" in calls["headers"]
    assert "api-key" in calls["headers"]
