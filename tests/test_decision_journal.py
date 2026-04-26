from tradingagents.execution.journal import DecisionJournal


def test_decision_journal_append_and_tail(tmp_path):
    journal = DecisionJournal(file_path=tmp_path / "trade_decisions.jsonl")
    journal.append({"event": "trade", "status": "simulated_filled", "symbol": "BTCUSD"})
    journal.append({"event": "trade", "status": "rejected", "symbol": "ETHUSD"})

    rows = journal.tail(limit=10)
    assert len(rows) == 2
    assert rows[0]["symbol"] == "BTCUSD"
    assert rows[1]["status"] == "rejected"
