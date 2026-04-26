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


def test_dhan_market_order_payload(monkeypatch):
    monkeypatch.setenv("DHAN_CLIENT_ID", "cid")
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "token")

    captured = {}

    def _fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _DummyResponse(payload={"orderId": "abc"})

    monkeypatch.setattr("tradingagents.execution.dhan_broker.requests.post", _fake_post)

    broker = DhanBroker()
    result = broker.place_market_order(
        symbol="NIFTY25SEP24500CE",
        side="BUY",
        quantity=2,
        instrument_type="options",
        security_id="123456",
    )

    assert result["orderId"] == "abc"
    assert captured["json"]["transactionType"] == "BUY"
    assert captured["json"]["securityId"] == "123456"
    assert captured["headers"]["client-id"] == "cid"
