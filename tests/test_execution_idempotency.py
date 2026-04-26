import json

from tradingagents.execution.engine import execute_trade
from tradingagents.execution.models import TradeIntent
from tradingagents.execution.position_manager import PositionManager


def test_duplicate_execution_is_skipped_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_FILE", str(tmp_path / "journal.jsonl"))
    monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_ROTATE_DAILY", "false")
    monkeypatch.setenv("TRADINGAGENTS_SQLITE_STATE_FILE", str(tmp_path / "portfolio.db"))

    intent = TradeIntent(symbol="NIFTY25SEP24500CE", instrument_type="options", signal="BUY", quantity=1)

    first = execute_trade(intent=intent, paper=True, mark_price=100.0)
    second = execute_trade(intent=intent, paper=True, mark_price=100.0)

    assert first.status == "simulated_filled"
    assert second.status == "skipped_duplicate"
    assert second.action == "skip"

    wallet = PositionManager.from_env()
    summary = wallet.get_summary()
    assert int(summary["orders_count"]) == 1

    rows = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
    statuses = [row.get("status") for row in rows if row.get("event") == "trade"]
    assert statuses.count("simulated_filled") == 1
    assert statuses.count("skipped_duplicate") == 1


def test_duplicate_check_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_FILE", str(tmp_path / "journal_no_dedupe.jsonl"))
    monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_ROTATE_DAILY", "false")
    monkeypatch.setenv("TRADINGAGENTS_SQLITE_STATE_FILE", str(tmp_path / "wallet_no_dedupe.db"))
    monkeypatch.setenv("TRADINGAGENTS_EXECUTION_IDEMPOTENCY_ENABLED", "false")

    intent = TradeIntent(symbol="BTCUSD", instrument_type="spot", signal="BUY", quantity=1)

    first = execute_trade(intent=intent, paper=True, mark_price=10.0)
    second = execute_trade(intent=intent, paper=True, mark_price=10.0)

    assert first.status == "simulated_filled"
    assert second.status == "simulated_filled"

    wallet = PositionManager.from_env()
    summary = wallet.get_summary()
    assert int(summary["orders_count"]) == 2
