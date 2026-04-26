import json

from cli.main import load_strategy_playbook, get_next_governance_run_index


def test_load_strategy_playbook_prefers_playbook_file(tmp_path, monkeypatch):
    symbol = "BTC-USD"
    symbol_key = "BTC_USD"
    target_dir = tmp_path / "strategy_lab_results" / symbol_key
    target_dir.mkdir(parents=True)

    playbook_payload = {
        "symbol": symbol,
        "best_strategy": {"name": "ema_12_34", "family": "ema_crossover", "params": {"fast": 12, "slow": 34}},
    }
    summary_payload = {
        "symbol": symbol,
        "playbook": {"best_strategy": {"name": "summary_only", "family": "rsi_mean_reversion", "params": {}}},
    }

    (target_dir / f"strategy_playbook_{symbol_key}.json").write_text(json.dumps(playbook_payload), encoding="utf-8")
    (target_dir / f"strategy_lab_{symbol_key}.json").write_text(json.dumps(summary_payload), encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    loaded = load_strategy_playbook(symbol)
    assert loaded == playbook_payload


def test_load_strategy_playbook_fallbacks_to_summary(tmp_path, monkeypatch):
    symbol = "ETH-USD"
    symbol_key = "ETH_USD"
    target_dir = tmp_path / "strategy_lab_results" / symbol_key
    target_dir.mkdir(parents=True)

    summary_payload = {
        "symbol": symbol,
        "playbook": {
            "best_strategy": {"name": "donchian_55", "family": "donchian_breakout", "params": {"lookback": 55}}
        },
    }
    (target_dir / f"strategy_lab_{symbol_key}.json").write_text(json.dumps(summary_payload), encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    loaded = load_strategy_playbook(symbol)
    assert loaded == summary_payload["playbook"]


def test_get_next_governance_run_index_defaults_to_one():
    assert get_next_governance_run_index(None) == 1
    assert get_next_governance_run_index({}) == 1
    assert get_next_governance_run_index({"governance": {"lifecycle_run_index": "bad"}}) == 1


def test_get_next_governance_run_index_increments_from_previous_playbook():
    previous = {
        "governance": {
            "lifecycle_run_index": 7,
        }
    }
    assert get_next_governance_run_index(previous) == 8
