import json

import pytest

from tradingagents.execution.engine import execute_trade
from tradingagents.execution.models import TradeIntent


def test_execute_trade_logs_rejection_to_journal(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_FILE", str(tmp_path / "journal.jsonl"))
    monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_ROTATE_DAILY", "false")
    monkeypatch.setenv("TRADINGAGENTS_RISK_MIN_CONFIDENCE", "0.95")

    intent = TradeIntent(symbol="NIFTY25SEP24500CE", instrument_type="options", signal="BUY", quantity=1)

    with pytest.raises(RuntimeError):
        execute_trade(
            intent=intent,
            broker="auto",
            paper=True,
            confidence=0.5,
            position_size_pct=0.05,
        )

    lines = (tmp_path / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 1
    row = json.loads(lines[-1])
    assert row["status"] == "rejected"
    assert row["event"] == "trade"


def test_execute_trade_logs_success_to_journal(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_FILE", str(tmp_path / "journal_ok.jsonl"))
    monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_ROTATE_DAILY", "false")
    monkeypatch.setenv("TRADINGAGENTS_PAPER_STATE_FILE", str(tmp_path / "wallet.json"))

    intent = TradeIntent(symbol="NIFTY25SEP24500CE", instrument_type="options", signal="BUY", quantity=1)
    result = execute_trade(
        intent=intent,
        broker="auto",
        paper=True,
        confidence=0.9,
        position_size_pct=0.05,
    )

    assert result.status == "simulated_filled"

    lines = (tmp_path / "journal_ok.jsonl").read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[-1])
    assert row["status"] == "simulated_filled"
    assert row["event"] == "trade"
