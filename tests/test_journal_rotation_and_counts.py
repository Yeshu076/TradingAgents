import json
from pathlib import Path

from tradingagents.execution.journal import DecisionJournal


def test_journal_daily_resolution_and_count(tmp_path):
    journal = DecisionJournal(
        file_path=tmp_path / "decisions.jsonl",
        rotate_daily=True,
        max_bytes=10_000,
        max_roll_files=3,
    )
    journal.append({"status": "simulated_filled", "event": "trade"})
    journal.append({"status": "submitted", "event": "trade"})

    count = journal.count_today(statuses={"simulated_filled", "submitted"})
    assert count == 2


def test_journal_rollover_on_size(tmp_path):
    journal = DecisionJournal(
        file_path=tmp_path / "roll.jsonl",
        rotate_daily=False,
        max_bytes=40,
        max_roll_files=3,
    )

    journal.append({"status": "simulated_filled", "event": "trade", "note": "x" * 100})
    journal.append({"status": "simulated_filled", "event": "trade", "note": "y" * 100})

    base = tmp_path / "roll.jsonl"
    rolled = tmp_path / "roll.1.jsonl"
    assert base.exists()
    assert rolled.exists()
