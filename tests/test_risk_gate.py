from tradingagents.execution.models import TradeIntent
from tradingagents.execution.risk_gate import DeterministicRiskGate


def test_risk_gate_rejects_low_rr():
    gate = DeterministicRiskGate(min_confidence=0.4, min_risk_reward=2.0, max_position_size_pct=0.2)
    intent = TradeIntent(
        symbol="BTCUSD",
        instrument_type="spot",
        signal="BUY",
        quantity=1,
        suggested_entry=100,
        suggested_stop_loss=95,
        suggested_target=105,
    )

    decision = gate.evaluate(intent, metadata={"confidence": 0.9, "position_size_pct": 0.1})
    assert decision.approved is False
    assert "risk-reward" in (decision.rejection_reason or "")


def test_risk_gate_rejects_size_pct():
    gate = DeterministicRiskGate(min_confidence=0.4, min_risk_reward=1.0, max_position_size_pct=0.05)
    intent = TradeIntent(symbol="NIFTY", instrument_type="options", signal="BUY", quantity=1)
    decision = gate.evaluate(intent, metadata={"confidence": 0.8, "position_size_pct": 0.2})
    assert decision.approved is False
    assert "position_size_pct" in (decision.rejection_reason or "")
