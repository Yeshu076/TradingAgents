import pandas as pd

from tradingagents.strategy_lab.backtest_engine import evaluate_strategy
from tradingagents.strategy_lab.models import StrategySpec
from tradingagents.strategy_lab.orchestrator import StrategyLabOrchestrator
from tradingagents.strategy_lab.governance import PromotionGovernancePolicy, apply_promotion_governance
from tradingagents.agents.utils.agent_utils import build_instrument_context


def _synthetic_data(n=300):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    close = pd.Series(range(100, 100 + n), index=idx, dtype=float)
    return pd.DataFrame({"close": close, "volume": 1_000.0}, index=idx)


def test_evaluate_strategy_outputs_metrics():
    data = _synthetic_data()
    spec = StrategySpec(name="ema_10_30", family="ema_crossover", params={"fast": 10, "slow": 30})
    result = evaluate_strategy(spec, data)

    assert result.spec.name == "ema_10_30"
    assert isinstance(result.score, float)
    assert 0.0 <= result.in_sample_max_drawdown <= 1.0
    assert 0.0 <= result.out_sample_max_drawdown <= 1.0
    assert isinstance(result.passed_filters, bool)
    assert result.robustness_penalty >= 0.0


def test_orchestrator_returns_summary_with_best(tmp_path, monkeypatch):
    data = _synthetic_data()

    def _fake_fetch(symbol, period="2y", interval="1d"):
        return data

    monkeypatch.setattr("tradingagents.strategy_lab.orchestrator.fetch_ohlcv", _fake_fetch)

    orchestrator = StrategyLabOrchestrator(seed=7)
    summary = orchestrator.run(
        symbol="BTC-USD",
        cycles=2,
        population_size=6,
        elite_count=2,
        output_dir=tmp_path,
    )

    assert summary["best_overall"] is not None
    assert len(summary["history"]) == 2
    assert "output_file" in summary
    assert "playbook" in summary
    assert "playbook_file" in summary


def test_build_instrument_context_includes_playbook_hint():
    context = build_instrument_context(
        "BTC-USD",
        "crypto",
        {
            "strategy_playbook": {
                "best_strategy": {
                    "name": "ema_10_50",
                    "family": "ema_crossover",
                    "params": {"fast": 10, "slow": 50},
                }
            }
        },
    )

    assert "ema_10_50" in context
    assert "ema_crossover" in context


def test_playbook_marks_promoted_strategy_when_thresholds_passed():
    orchestrator = StrategyLabOrchestrator(seed=1)
    summary = {
        "symbol": "BTC-USD",
        "instrument_type": "crypto",
        "run_at": "2025-01-01T00:00:00Z",
        "robustness": {"min_trades": 8, "max_out_drawdown": 0.35},
        "best_overall": {
            "spec": {"name": "ema_best", "family": "ema_crossover", "params": {"fast": 10, "slow": 30}},
            "score": 0.91,
        },
        "leaderboard": [
            {
                "spec": {"name": "ema_robust", "family": "ema_crossover", "params": {"fast": 12, "slow": 48}},
                "score": 0.8,
                "out_sample_sharpe": 0.7,
                "out_sample_return": 0.12,
                "out_sample_max_drawdown": 0.18,
                "trades": 24,
                "passed_filters": True,
                "notes": [],
            }
        ],
    }

    playbook = orchestrator._build_playbook(
        summary,
        promotion_policy={
            "min_score": 0.5,
            "min_out_sharpe": 0.3,
            "min_out_return": 0.01,
            "max_out_drawdown": 0.25,
            "min_trades": 12,
        },
    )

    assert playbook["promotion_status"] == "promoted"
    assert playbook["promoted_strategy"]["name"] == "ema_robust"


def test_build_instrument_context_prefers_promoted_strategy():
    context = build_instrument_context(
        "BTC-USD",
        "crypto",
        {
            "strategy_playbook": {
                "promotion_status": "promoted",
                "best_strategy": {
                    "name": "ema_fallback",
                    "family": "ema_crossover",
                    "params": {"fast": 10, "slow": 50},
                },
                "promoted_strategy": {
                    "name": "supertrend_promoted",
                    "family": "supertrend",
                    "params": {"period": 14, "multiplier": 2.5},
                },
            }
        },
    )

    assert "supertrend_promoted" in context
    assert "ema_fallback" not in context


def test_build_instrument_context_ignores_promoted_when_not_promoted_status():
    context = build_instrument_context(
        "BTC-USD",
        "crypto",
        {
            "strategy_playbook": {
                "promotion_status": "demoted_drift",
                "best_strategy": {
                    "name": "ema_fallback",
                    "family": "ema_crossover",
                    "params": {"fast": 10, "slow": 50},
                },
                "promoted_strategy": {
                    "name": "supertrend_promoted",
                    "family": "supertrend",
                    "params": {"period": 14, "multiplier": 2.5},
                },
            }
        },
    )

    assert "ema_fallback" in context


def test_governance_demotes_promoted_strategy_when_drift_is_poor():
    playbook = {
        "promotion_status": "promoted",
        "promoted_strategy": {"name": "ema_20_50", "family": "ema_crossover", "params": {"fast": 20, "slow": 50}},
        "best_strategy": {"name": "ema_20_50", "family": "ema_crossover", "params": {"fast": 20, "slow": 50}},
    }
    previous = {
        "promotion_status": "promoted",
        "promoted_strategy": {"name": "ema_20_50", "family": "ema_crossover", "params": {"fast": 20, "slow": 50}},
        "governance": {"cooldown_until_run": 1},
    }
    rows = [
        {"event": "trade", "status": "rejected"},
        {"event": "trade", "status": "rejected"},
        {"event": "trade", "status": "blocked: policy"},
        {"event": "trade", "status": "blocked: policy"},
        {"event": "trade", "status": "simulated_filled"},
    ]

    governed = apply_promotion_governance(
        playbook,
        previous_playbook=previous,
        run_index=5,
        recent_rows=rows,
        policy=PromotionGovernancePolicy(
            cooldown_runs=1,
            drift_lookback_trades=10,
            drift_min_samples=5,
            drift_min_fill_rate=0.5,
            drift_max_rejection_ratio=0.4,
            drift_max_blocked_ratio=0.3,
        ),
    )

    assert governed["promotion_status"] == "demoted_drift"
    assert governed["promoted_strategy"] == {}


def test_governance_respects_cooldown_before_demotion():
    playbook = {
        "promotion_status": "promoted",
        "promoted_strategy": {"name": "ema_20_50", "family": "ema_crossover", "params": {"fast": 20, "slow": 50}},
        "best_strategy": {"name": "ema_20_50", "family": "ema_crossover", "params": {"fast": 20, "slow": 50}},
    }
    previous = {
        "promotion_status": "not_promoted",
        "promoted_strategy": {},
    }
    rows = [
        {"event": "trade", "status": "rejected"},
        {"event": "trade", "status": "rejected"},
        {"event": "trade", "status": "rejected"},
        {"event": "trade", "status": "blocked: policy"},
        {"event": "trade", "status": "rejected"},
    ]

    governed = apply_promotion_governance(
        playbook,
        previous_playbook=previous,
        run_index=1,
        recent_rows=rows,
        policy=PromotionGovernancePolicy(
            cooldown_runs=3,
            drift_lookback_trades=10,
            drift_min_samples=5,
            drift_min_fill_rate=0.5,
            drift_max_rejection_ratio=0.3,
            drift_max_blocked_ratio=0.2,
        ),
    )

    assert governed["promotion_status"] == "promoted"
    assert governed["governance"]["cooldown_active"] is True


def test_governance_tracks_lifecycle_run_index():
    playbook = {
        "promotion_status": "not_promoted",
        "promoted_strategy": {},
        "best_strategy": {"name": "ema_20_50", "family": "ema_crossover", "params": {"fast": 20, "slow": 50}},
    }

    governed = apply_promotion_governance(
        playbook,
        previous_playbook={"governance": {"lifecycle_run_index": 9}},
        run_index=10,
        recent_rows=[],
        policy=PromotionGovernancePolicy(),
    )

    assert governed["governance"]["lifecycle_run_index"] == 10
