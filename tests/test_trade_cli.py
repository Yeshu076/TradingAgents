from typer.testing import CliRunner
import json
import os
import time
import pytest

from cli.main import app, find_latest_order_intent_file


runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate_execution_state(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_FILE", str(tmp_path / "journal.jsonl"))
    monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_ROTATE_DAILY", "false")
    monkeypatch.setenv("TRADINGAGENTS_PAPER_STATE_FILE", str(tmp_path / "wallet.json"))


def test_trade_cli_paper_place_order():
    result = runner.invoke(
        app,
        [
            "trade",
            "--symbol",
            "NIFTY25SEP24500CE",
            "--instrument-type",
            "options",
            "--signal",
            "BUY",
            "--quantity",
            "1",
        ],
    )
    assert result.exit_code == 0
    assert '"status": "simulated_filled"' in result.stdout


def test_trade_cli_allow_duplicates_flag():
    first = runner.invoke(
        app,
        [
            "trade",
            "--symbol",
            "NIFTY25SEP24500CE",
            "--instrument-type",
            "options",
            "--signal",
            "BUY",
            "--quantity",
            "1",
        ],
    )
    second = runner.invoke(
        app,
        [
            "trade",
            "--symbol",
            "NIFTY25SEP24500CE",
            "--instrument-type",
            "options",
            "--signal",
            "BUY",
            "--quantity",
            "1",
        ],
    )
    third = runner.invoke(
        app,
        [
            "trade",
            "--symbol",
            "NIFTY25SEP24500CE",
            "--instrument-type",
            "options",
            "--signal",
            "BUY",
            "--quantity",
            "1",
            "--allow-duplicates",
        ],
    )

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert third.exit_code == 0
    assert '"status": "simulated_filled"' in first.stdout
    assert '"status": "skipped_duplicate"' in second.stdout
    assert '"status": "simulated_filled"' in third.stdout


def test_trade_cli_positions_path(monkeypatch):
    monkeypatch.setattr(
        "cli.main.list_positions",
        lambda broker, instrument_type, symbol: {"broker": "dhan", "positions": []},
    )

    result = runner.invoke(
        app,
        [
            "trade",
            "--symbol",
            "NIFTY25SEP24500CE",
            "--show-positions",
            "--live",
        ],
    )
    assert result.exit_code == 0
    assert '"positions": []' in result.stdout


def test_find_latest_order_intent_file(tmp_path):
    reports = tmp_path / "reports"
    a = reports / "BTC_1"
    b = reports / "BTC_2"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    (a / "order_intent.json").write_text("{}", encoding="utf-8")
    latest = b / "order_intent.json"
    latest.write_text("{}", encoding="utf-8")
    now = time.time() + 2
    os.utime(latest, (now, now))

    found = find_latest_order_intent_file(reports, ticker="BTC")
    assert found == latest


def test_execute_latest_intent_command(tmp_path, monkeypatch):
    reports = tmp_path / "reports" / "NIFTY_20260328"
    reports.mkdir(parents=True)
    payload = {
        "ticker": "NIFTY25SEP24500CE",
        "instrument_type": "options",
        "signal": "BUY",
    }
    (reports / "order_intent.json").write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "execute-latest-intent",
            "--ticker",
            "NIFTY",
            "--reports-root",
            "reports",
        ],
    )
    assert result.exit_code == 0
    assert '"status": "simulated_filled"' in result.stdout
    assert '"intent_file":' in result.stdout


def test_run_cycles_command(tmp_path, monkeypatch):
    reports = tmp_path / "reports" / "NIFTY_20260328"
    reports.mkdir(parents=True)
    payload = {
        "ticker": "NIFTY25SEP24500CE",
        "instrument_type": "options",
        "signal": "BUY",
        "confidence": 0.9,
        "position_size_pct": 0.05,
    }
    (reports / "order_intent.json").write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "run-cycles",
            "--cycles",
            "1",
            "--ticker",
            "NIFTY",
            "--reports-root",
            "reports",
            "--every-seconds",
            "0.5",
        ],
    )
    assert result.exit_code == 0
    assert "Cycle Runner Complete" in result.stdout


def test_run_cycles_skips_duplicate_when_disallowed(tmp_path, monkeypatch):
    reports = tmp_path / "reports" / "NIFTY_20260328"
    reports.mkdir(parents=True)
    payload = {
        "ticker": "NIFTY25SEP24500CE",
        "instrument_type": "options",
        "signal": "BUY",
        "confidence": 0.9,
        "position_size_pct": 0.05,
    }
    (reports / "order_intent.json").write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "run-cycles",
            "--cycles",
            "2",
            "--ticker",
            "NIFTY",
            "--reports-root",
            "reports",
            "--every-seconds",
            "0.5",
        ],
    )
    assert result.exit_code == 0
    assert "skipped_duplicate" in result.stdout


def test_show_journal_command(tmp_path, monkeypatch):
    journal = tmp_path / "journal.jsonl"
    journal.write_text('{"event":"trade","status":"simulated_filled","symbol":"NIFTY"}\n', encoding="utf-8")
    monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_FILE", str(journal))
    monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_ROTATE_DAILY", "false")

    result = runner.invoke(app, ["show-journal", "--limit", "10"])
    assert result.exit_code == 0
    assert '"count": 1' in result.stdout
