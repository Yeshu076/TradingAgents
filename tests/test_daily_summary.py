import json
from datetime import datetime, timezone

from tradingagents.execution.journal import DecisionJournal


def test_daily_summary_counts_and_top_symbols(tmp_path):
    journal = DecisionJournal(
        file_path=tmp_path / "decisions.jsonl",
        rotate_daily=False,
        max_bytes=1_000_000,
        max_roll_files=3,
    )

    now_ts = int(datetime.now(timezone.utc).timestamp())
    journal.append(
        {
            "ts": now_ts,
            "event": "trade",
            "status": "simulated_filled",
            "symbol": "NIFTY25SEP24500CE",
            "signal": "BUY",
            "mode": "paper",
            "details": {
                "paper_fill": {
                    "wallet": {"cash": 999000, "equity": 1000000}
                }
            },
        }
    )
    journal.append(
        {
            "ts": now_ts,
            "event": "trade",
            "status": "rejected",
            "symbol": "NIFTY25SEP24500PE",
            "signal": "SELL",
            "mode": "paper",
            "reason": "risk-reward 1.0 below min 1.2",
        }
    )

    summary = journal.summarize_day()
    assert summary["executed_count"] == 1
    assert summary["rejected_count"] == 1
    assert summary["status_counts"]["simulated_filled"] == 1
    assert summary["status_counts"]["rejected"] == 1
    assert len(summary["top_symbols"]) >= 1
    assert summary["latest_wallet"]["cash"] == 999000
