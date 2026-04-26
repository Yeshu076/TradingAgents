from __future__ import annotations
"""
Module: governance.py
Part of the strategy_lab subsystem.

This module contains logic for the strategy_lab operations as part of the broader TradingAgents framework.
"""

import copy
import os
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class PromotionGovernancePolicy:
    cooldown_runs: int = 2
    drift_lookback_trades: int = 30
    drift_min_samples: int = 8
    drift_min_fill_rate: float = 0.35
    drift_max_rejection_ratio: float = 0.55
    drift_max_blocked_ratio: float = 0.35

    @staticmethod
    def from_env() -> "PromotionGovernancePolicy":
        return PromotionGovernancePolicy(
            cooldown_runs=max(0, int(os.getenv("TRADINGAGENTS_PROMOTION_COOLDOWN_RUNS", "2"))),
            drift_lookback_trades=max(5, int(os.getenv("TRADINGAGENTS_PROMOTION_DRIFT_LOOKBACK_TRADES", "30"))),
            drift_min_samples=max(1, int(os.getenv("TRADINGAGENTS_PROMOTION_DRIFT_MIN_SAMPLES", "8"))),
            drift_min_fill_rate=max(0.0, min(1.0, float(os.getenv("TRADINGAGENTS_PROMOTION_DRIFT_MIN_FILL_RATE", "0.35")))),
            drift_max_rejection_ratio=max(0.0, min(1.0, float(os.getenv("TRADINGAGENTS_PROMOTION_DRIFT_MAX_REJECTION_RATIO", "0.55")))),
            drift_max_blocked_ratio=max(0.0, min(1.0, float(os.getenv("TRADINGAGENTS_PROMOTION_DRIFT_MAX_BLOCKED_RATIO", "0.35")))),
        )


def apply_promotion_governance(
    playbook: Dict[str, Any],
    *,
    previous_playbook: Dict[str, Any] | None,
    run_index: int,
    recent_rows: List[Dict[str, Any]],
    policy: PromotionGovernancePolicy,
) -> Dict[str, Any]:
    out = copy.deepcopy(playbook)
    prev = previous_playbook or {}

    governance = out.get("governance", {})
    if not isinstance(governance, dict):
        governance = {}

    prev_governance = prev.get("governance", {})
    if not isinstance(prev_governance, dict):
        prev_governance = {}

    governance["lifecycle_run_index"] = int(run_index)

    currently_promoted = _is_promoted(out)
    previously_promoted = _is_promoted(prev)

    # Start cooldown whenever promotion first becomes active.
    cooldown_until_run = _to_int(governance.get("cooldown_until_run"), default=0)
    if currently_promoted and not previously_promoted:
        governance["last_promotion_run_index"] = run_index
        cooldown_until_run = run_index + policy.cooldown_runs

    if cooldown_until_run <= 0:
        cooldown_until_run = _to_int(prev_governance.get("cooldown_until_run"), default=0)

    cooldown_active = currently_promoted and run_index <= cooldown_until_run

    drift = _compute_execution_drift(
        recent_rows=recent_rows,
        lookback=policy.drift_lookback_trades,
    )
    governance["cooldown_until_run"] = cooldown_until_run
    governance["cooldown_active"] = cooldown_active
    governance["execution_drift"] = drift
    governance["policy"] = {
        "cooldown_runs": policy.cooldown_runs,
        "drift_lookback_trades": policy.drift_lookback_trades,
        "drift_min_samples": policy.drift_min_samples,
        "drift_min_fill_rate": policy.drift_min_fill_rate,
        "drift_max_rejection_ratio": policy.drift_max_rejection_ratio,
        "drift_max_blocked_ratio": policy.drift_max_blocked_ratio,
    }

    if currently_promoted and (not cooldown_active):
        sample_count = int(drift.get("sample_count", 0))
        fill_rate = float(drift.get("fill_rate", 0.0))
        rejection_ratio = float(drift.get("rejection_ratio", 0.0))
        blocked_ratio = float(drift.get("blocked_ratio", 0.0))

        demote_reasons: List[str] = []
        if sample_count >= policy.drift_min_samples:
            if fill_rate < policy.drift_min_fill_rate:
                demote_reasons.append(
                    f"fill_rate {fill_rate:.2f} below {policy.drift_min_fill_rate:.2f}"
                )
            if rejection_ratio > policy.drift_max_rejection_ratio:
                demote_reasons.append(
                    f"rejection_ratio {rejection_ratio:.2f} above {policy.drift_max_rejection_ratio:.2f}"
                )
            if blocked_ratio > policy.drift_max_blocked_ratio:
                demote_reasons.append(
                    f"blocked_ratio {blocked_ratio:.2f} above {policy.drift_max_blocked_ratio:.2f}"
                )

        if demote_reasons:
            out["promotion_status"] = "demoted_drift"
            out["promotion_reason"] = "Demoted by execution drift governance: " + " | ".join(demote_reasons)
            out["promoted_strategy"] = {}
            out["promoted_metrics"] = {
                "score": 0.0,
                "out_sample_sharpe": 0.0,
                "out_sample_return": 0.0,
                "out_sample_max_drawdown": 0.0,
                "trades": 0,
            }
            governance["last_demotion_run_index"] = run_index
            governance["last_demotion_reason"] = out["promotion_reason"]

    out["governance"] = governance
    return out


def _is_promoted(playbook: Dict[str, Any]) -> bool:
    if not isinstance(playbook, dict):
        return False
    status = str(playbook.get("promotion_status", "")).strip().lower()
    promoted_strategy = playbook.get("promoted_strategy")
    return status == "promoted" and isinstance(promoted_strategy, dict) and bool(promoted_strategy)


def _compute_execution_drift(recent_rows: List[Dict[str, Any]], lookback: int) -> Dict[str, Any]:
    rows = [r for r in recent_rows if isinstance(r, dict)]
    rows = rows[-max(1, lookback):]

    fills = 0
    rejected = 0
    blocked = 0
    considered = 0

    for row in rows:
        if str(row.get("event", "")).strip().lower() != "trade":
            continue
        status = str(row.get("status", "")).strip().lower()
        if status in {"simulated_filled", "submitted"}:
            fills += 1
            considered += 1
        elif status == "rejected":
            rejected += 1
            considered += 1
        elif status.startswith("blocked"):
            blocked += 1
            considered += 1

    fill_rate = (fills / considered) if considered else 0.0
    rejection_ratio = (rejected / considered) if considered else 0.0
    blocked_ratio = (blocked / considered) if considered else 0.0

    return {
        "sample_count": considered,
        "fills": fills,
        "rejected": rejected,
        "blocked": blocked,
        "fill_rate": fill_rate,
        "rejection_ratio": rejection_ratio,
        "blocked_ratio": blocked_ratio,
    }


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
