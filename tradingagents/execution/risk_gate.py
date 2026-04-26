from __future__ import annotations
"""
Module: risk_gate.py
Part of the execution subsystem.

This module contains logic for the execution operations as part of the broader TradingAgents framework.
"""

import os
from dataclasses import dataclass
from typing import Any, Dict

from .models import TradeIntent
from .global_risk import GlobalRiskMonitor


@dataclass
class RiskGateDecision:
    approved: bool
    warnings: list[str]
    rejection_reason: str | None = None


@dataclass
class DeterministicRiskGate:
    min_confidence: float = 0.4
    min_risk_reward: float = 1.2
    max_position_size_pct: float = 0.15

    @staticmethod
    def from_env() -> "DeterministicRiskGate":
        return DeterministicRiskGate(
            min_confidence=float(os.getenv("TRADINGAGENTS_RISK_MIN_CONFIDENCE", "0.40")),
            min_risk_reward=float(os.getenv("TRADINGAGENTS_RISK_MIN_RR", "1.20")),
            max_position_size_pct=float(os.getenv("TRADINGAGENTS_RISK_MAX_POSITION_PCT", "0.15")),
        )

    def evaluate(self, intent: TradeIntent, metadata: Dict[str, Any] | None = None) -> RiskGateDecision:
        warnings: list[str] = []
        md = metadata or {}

        # 1. Global Circuit Breaker Check
        global_risk = GlobalRiskMonitor.get_instance()
        if not global_risk.is_globally_safe():
            return RiskGateDecision(
                approved=False,
                warnings=warnings,
                rejection_reason="Global circuit breaker triggered (PANIC state). Max global drawdown exceeded.",
            )

        confidence = _to_float(md.get("confidence"))
        if confidence is not None and confidence < self.min_confidence:
            return RiskGateDecision(
                approved=False,
                warnings=warnings,
                rejection_reason=f"confidence {confidence:.2f} below min {self.min_confidence:.2f}",
            )

        size_pct = _to_float(md.get("position_size_pct"))
        if size_pct is not None and size_pct > self.max_position_size_pct:
            return RiskGateDecision(
                approved=False,
                warnings=warnings,
                rejection_reason=f"position_size_pct {size_pct:.2f} exceeds max {self.max_position_size_pct:.2f}",
            )

        entry = intent.suggested_entry
        stop = intent.suggested_stop_loss
        target = intent.suggested_target
        if entry is not None and stop is not None and target is not None:
            risk = abs(entry - stop)
            reward = abs(target - entry)
            if risk > 0:
                rr = reward / risk
                if rr < self.min_risk_reward:
                    return RiskGateDecision(
                        approved=False,
                        warnings=warnings,
                        rejection_reason=f"risk-reward {rr:.2f} below min {self.min_risk_reward:.2f}",
                    )
            else:
                warnings.append("zero risk distance (entry equals stop)")
        else:
            warnings.append("missing entry/stop/target for strict risk-reward validation")

        return RiskGateDecision(approved=True, warnings=warnings)


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None

