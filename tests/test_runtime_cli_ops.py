from typer.testing import CliRunner
import json

from cli.main import app


runner = CliRunner()


def test_runtime_status_command():
    result = runner.invoke(app, ["runtime-status", "--journal-limit", "2"])
    assert result.exit_code == 0
    assert '"today_executions":' in result.stdout


def test_reset_wallet_requires_yes():
    result = runner.invoke(app, ["reset-wallet"])
    assert result.exit_code == 1
    assert "Wallet reset blocked" in result.stdout


def test_daily_summary_command(tmp_path, monkeypatch):
    journal = tmp_path / "summary.jsonl"
    journal.write_text(
        '{"ts": 1774690000, "event": "trade", "status": "simulated_filled", "symbol": "NIFTY", "mode": "paper"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_FILE", str(journal))
    monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_ROTATE_DAILY", "false")

    result = runner.invoke(app, ["daily-summary", "--day-utc", "2026-03-28"])
    assert result.exit_code == 0
    assert '"date_utc": "2026-03-28"' in result.stdout


def test_governance_report_command_reads_artifacts(tmp_path, monkeypatch):
    symbol_key = "BTC_USD"
    target_dir = tmp_path / "strategy_lab_results" / symbol_key
    target_dir.mkdir(parents=True)

    playbook_payload = {
        "promotion_status": "promoted",
        "promoted_strategy": {"name": "ema_12_34", "family": "ema_crossover", "params": {"fast": 12, "slow": 34}},
        "governance": {"lifecycle_run_index": 5, "cooldown_active": False},
    }
    (target_dir / f"strategy_playbook_{symbol_key}.json").write_text(json.dumps(playbook_payload), encoding="utf-8")
    (target_dir / "autolab_runs.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "run_index": 1,
                        "lifecycle_run_index": 4,
                        "run_at": "2026-03-28T09:00:00Z",
                        "promotion_status": "not_promoted",
                        "promoted_strategy": {},
                        "score": 0.42,
                        "passed_filters": True,
                    }
                ),
                json.dumps(
                    {
                        "run_index": 2,
                        "lifecycle_run_index": 5,
                        "run_at": "2026-03-28T09:30:00Z",
                        "promotion_status": "promoted",
                        "promoted_strategy": {"name": "ema_12_34"},
                        "score": 0.57,
                        "passed_filters": True,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["governance-report", "--symbol", "BTC-USD", "--runs-limit", "1"])
    assert result.exit_code == 0
    assert '"promotion_status": "promoted"' in result.stdout
    assert '"playbook_found": true' in result.stdout
    assert '"recent_runs_count": 1' in result.stdout
    assert '"lifecycle_run_index": 5' in result.stdout


def test_governance_report_command_handles_missing_artifacts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["governance-report", "--symbol", "ETH-USD"])
    assert result.exit_code == 0
    assert '"playbook_found": false' in result.stdout
    assert '"run_log_found": false' in result.stdout
    assert '"artifacts_found": false' in result.stdout


def test_ops_report_command_combines_runtime_daily_and_governance(tmp_path, monkeypatch):
    symbol_key = "BTC_USD"
    target_dir = tmp_path / "strategy_lab_results" / symbol_key
    target_dir.mkdir(parents=True)

    playbook_payload = {
        "promotion_status": "promoted",
        "promoted_strategy": {"name": "ema_12_34", "family": "ema_crossover", "params": {"fast": 12, "slow": 34}},
        "governance": {"lifecycle_run_index": 9, "cooldown_active": False},
    }
    (target_dir / f"strategy_playbook_{symbol_key}.json").write_text(json.dumps(playbook_payload), encoding="utf-8")
    (target_dir / "autolab_runs.jsonl").write_text(
        json.dumps(
            {
                "run_index": 4,
                "lifecycle_run_index": 9,
                "run_at": "2026-03-28T12:00:00Z",
                "promotion_status": "promoted",
                "promoted_strategy": {"name": "ema_12_34"},
                "score": 0.72,
                "passed_filters": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    journal = tmp_path / "ops_summary.jsonl"
    journal.write_text(
        '{"ts": 1774690000, "event": "trade", "status": "simulated_filled", "symbol": "NIFTY", "mode": "paper"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_FILE", str(journal))
    monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_ROTATE_DAILY", "false")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "ops-report",
            "--symbol",
            "BTC-USD",
            "--day-utc",
            "2026-03-28",
            "--journal-limit",
            "1",
            "--runs-limit",
            "1",
        ],
    )
    assert result.exit_code == 0
    assert '"generated_at_utc":' in result.stdout
    assert '"runtime_status":' in result.stdout
    assert '"daily_summary":' in result.stdout
    assert '"governance_report":' in result.stdout
    assert '"promotion_status": "promoted"' in result.stdout


def test_ops_report_rejects_invalid_day_format():
    result = runner.invoke(app, ["ops-report", "--day-utc", "2026/03/28"])
    assert result.exit_code == 2


def test_ops_report_table_output_contains_sections(tmp_path, monkeypatch):
    symbol_key = "BTC_USD"
    target_dir = tmp_path / "strategy_lab_results" / symbol_key
    target_dir.mkdir(parents=True)

    playbook_payload = {
        "promotion_status": "promoted",
        "promoted_strategy": {"name": "ema_12_34", "family": "ema_crossover", "params": {"fast": 12, "slow": 34}},
        "governance": {
            "lifecycle_run_index": 9,
            "cooldown_active": False,
            "execution_drift": {
                "sample_count": 12,
                "fill_rate": 0.75,
                "rejection_ratio": 0.1,
                "blocked_ratio": 0.15,
            },
        },
    }
    (target_dir / f"strategy_playbook_{symbol_key}.json").write_text(json.dumps(playbook_payload), encoding="utf-8")
    (target_dir / "autolab_runs.jsonl").write_text(
        json.dumps(
            {
                "run_index": 4,
                "lifecycle_run_index": 9,
                "run_at": "2026-03-28T12:00:00Z",
                "promotion_status": "promoted",
                "promoted_strategy": {"name": "ema_12_34"},
                "score": 0.72,
                "passed_filters": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    journal = tmp_path / "ops_table.jsonl"
    journal.write_text(
        '{"ts": 1774690000, "event": "trade", "status": "simulated_filled", "symbol": "NIFTY", "mode": "paper"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_FILE", str(journal))
    monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_ROTATE_DAILY", "false")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "ops-report",
            "--symbol",
            "BTC-USD",
            "--day-utc",
            "2026-03-28",
            "--output",
            "table",
        ],
    )
    assert result.exit_code == 0
    assert "Operations Report" in result.stdout
    assert "Recent Governance Runs" in result.stdout
    assert "Paper Wallet" in result.stdout


def test_ops_report_rejects_invalid_output_mode():
    result = runner.invoke(app, ["ops-report", "--output", "yaml"])
    assert result.exit_code == 2
