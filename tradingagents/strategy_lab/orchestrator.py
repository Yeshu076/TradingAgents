from __future__ import annotations
"""
Module: orchestrator.py
Part of the strategy_lab subsystem.

This module contains logic for the strategy_lab operations as part of the broader TradingAgents framework.
"""

import json
import random
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .backtest_engine import evaluate_strategy, fetch_ohlcv
from .models import StrategyResult, StrategySpec
from .strategy_factory import initial_population, mutate_population


class StrategyLabOrchestrator:
    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)

    def run(
        self,
        symbol: str,
        instrument_type: str = "crypto",
        cycles: int = 5,
        population_size: int = 12,
        elite_count: int = 4,
        period: str = "2y",
        interval: str = "1d",
        fee_bps: float = 5.0,
        min_trades: int = 8,
        max_out_drawdown: float = 0.35,
        min_out_sharpe: float = -0.25,
        min_out_return: float = -0.20,
        promotion_min_score: float = 0.0,
        promotion_min_out_sharpe: float = 0.25,
        promotion_min_out_return: float = 0.0,
        promotion_max_out_drawdown: float = 0.25,
        promotion_min_trades: int = 12,
        output_dir: Optional[Path] = None,
    ) -> Dict:
        data = fetch_ohlcv(symbol, period=period, interval=interval)
        population = initial_population(population_size, self.rng)

        history: List[Dict] = []
        best_overall: Optional[StrategyResult] = None

        for cycle in range(1, cycles + 1):
            cycle_results: List[StrategyResult] = []
            for spec in population:
                result = evaluate_strategy(
                    spec,
                    data,
                    fee_bps=fee_bps,
                    min_trades=min_trades,
                    max_out_drawdown=max_out_drawdown,
                    min_out_sharpe=min_out_sharpe,
                    min_out_return=min_out_return,
                )
                cycle_results.append(result)
                if best_overall is None or result.score > best_overall.score:
                    best_overall = result

            cycle_results.sort(key=lambda r: r.score, reverse=True)
            elites = [r.spec for r in cycle_results[:elite_count]]

            history.append(
                {
                    "cycle": cycle,
                    "best": self._serialize_result(cycle_results[0]),
                    "top": [self._serialize_result(r) for r in cycle_results[: min(5, len(cycle_results))]],
                }
            )

            population = mutate_population(elites, population_size, self.rng)

        leaderboard = sorted(
            [self._deserialize_result(item["best"]) for item in history],
            key=lambda x: x.score,
            reverse=True,
        )

        summary = {
            "symbol": symbol,
            "instrument_type": instrument_type,
            "run_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "cycles": cycles,
            "population_size": population_size,
            "elite_count": elite_count,
            "period": period,
            "interval": interval,
            "fee_bps": fee_bps,
            "robustness": {
                "min_trades": min_trades,
                "max_out_drawdown": max_out_drawdown,
                "min_out_sharpe": min_out_sharpe,
                "min_out_return": min_out_return,
            },
            "best_overall": self._serialize_result(best_overall) if best_overall else None,
            "history": history,
            "leaderboard": [self._serialize_result(r) for r in leaderboard],
        }

        promotion_policy = {
            "min_score": promotion_min_score,
            "min_out_sharpe": promotion_min_out_sharpe,
            "min_out_return": promotion_min_out_return,
            "max_out_drawdown": promotion_max_out_drawdown,
            "min_trades": promotion_min_trades,
        }

        playbook = self._build_playbook(summary, promotion_policy=promotion_policy)
        summary["playbook"] = playbook

        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            out_file = output_dir / f"strategy_lab_{symbol.replace('/', '_').replace('-', '_')}.json"
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2)
            summary["output_file"] = str(out_file)

            playbook_file = output_dir / f"strategy_playbook_{symbol.replace('/', '_').replace('-', '_')}.json"
            with open(playbook_file, "w", encoding="utf-8") as f:
                json.dump(playbook, f, indent=2)
            summary["playbook_file"] = str(playbook_file)

        return summary

    def _build_playbook(self, summary: Dict, promotion_policy: Optional[Dict] = None) -> Dict:
        leaderboard = summary.get("leaderboard", [])
        robust_candidates = [item for item in leaderboard if item.get("passed_filters")]
        best_entry = summary.get("best_overall") or {}
        policy = promotion_policy or {
            "min_score": 0.0,
            "min_out_sharpe": 0.25,
            "min_out_return": 0.0,
            "max_out_drawdown": 0.25,
            "min_trades": 12,
        }

        promoted_entry = None
        for item in robust_candidates:
            if (
                float(item.get("score", 0.0)) >= float(policy.get("min_score", 0.0))
                and float(item.get("out_sample_sharpe", 0.0)) >= float(policy.get("min_out_sharpe", 0.0))
                and float(item.get("out_sample_return", 0.0)) >= float(policy.get("min_out_return", 0.0))
                and float(item.get("out_sample_max_drawdown", 1.0)) <= float(policy.get("max_out_drawdown", 0.25))
                and int(item.get("trades", 0)) >= int(policy.get("min_trades", 12))
            ):
                promoted_entry = item
                break

        promotion_status = "promoted" if promoted_entry else "not_promoted"
        promotion_reason = (
            "No robust candidate met promotion thresholds"
            if promoted_entry is None
            else "Top robust candidate satisfied promotion thresholds"
        )

        return {
            "symbol": summary.get("symbol"),
            "instrument_type": summary.get("instrument_type"),
            "generated_at": summary.get("run_at"),
            "robustness": summary.get("robustness", {}),
            "best_strategy": best_entry.get("spec", {}),
            "best_score": best_entry.get("score", 0.0),
            "promotion_status": promotion_status,
            "promotion_reason": promotion_reason,
            "promotion_policy": policy,
            "promoted_strategy": (promoted_entry or {}).get("spec", {}),
            "promoted_metrics": {
                "score": (promoted_entry or {}).get("score", 0.0),
                "out_sample_sharpe": (promoted_entry or {}).get("out_sample_sharpe", 0.0),
                "out_sample_return": (promoted_entry or {}).get("out_sample_return", 0.0),
                "out_sample_max_drawdown": (promoted_entry or {}).get("out_sample_max_drawdown", 0.0),
                "trades": (promoted_entry or {}).get("trades", 0),
            },
            "top_robust_candidates": [
                {
                    "spec": item.get("spec", {}),
                    "score": item.get("score", 0.0),
                    "out_sample_sharpe": item.get("out_sample_sharpe", 0.0),
                    "out_sample_return": item.get("out_sample_return", 0.0),
                    "out_sample_max_drawdown": item.get("out_sample_max_drawdown", 0.0),
                    "trades": item.get("trades", 0),
                    "notes": item.get("notes", []),
                }
                for item in robust_candidates[:3]
            ],
            "recommended_guardrails": {
                "prefer_only_passed_filters": True,
                "require_min_trades": summary.get("robustness", {}).get("min_trades", 8),
                "require_non_extreme_drawdown": summary.get("robustness", {}).get("max_out_drawdown", 0.35),
            },
        }

    def _serialize_result(self, result: StrategyResult) -> Dict:
        if result is None:
            return {}
        payload = asdict(result)
        payload["spec"] = asdict(result.spec)
        return payload

    def _deserialize_result(self, payload: Dict) -> StrategyResult:
        spec_data = payload["spec"]
        spec = StrategySpec(name=spec_data["name"], family=spec_data["family"], params=spec_data["params"])
        return StrategyResult(
            spec=spec,
            score=payload["score"],
            in_sample_return=payload["in_sample_return"],
            in_sample_sharpe=payload["in_sample_sharpe"],
            in_sample_max_drawdown=payload["in_sample_max_drawdown"],
            out_sample_return=payload["out_sample_return"],
            out_sample_sharpe=payload["out_sample_sharpe"],
            out_sample_max_drawdown=payload["out_sample_max_drawdown"],
            stability=payload["stability"],
            trades=payload["trades"],
            passed_filters=payload.get("passed_filters", True),
            robustness_penalty=payload.get("robustness_penalty", 0.0),
            overfit_gap=payload.get("overfit_gap", 0.0),
            notes=payload.get("notes", []),
        )
