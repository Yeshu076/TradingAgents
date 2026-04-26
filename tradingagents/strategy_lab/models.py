"""
Module: models.py
Part of the strategy_lab subsystem.

This module contains logic for the strategy_lab operations as part of the broader TradingAgents framework.
"""
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class StrategySpec:
    name: str
    family: str
    params: Dict[str, float]


@dataclass
class StrategyResult:
    spec: StrategySpec
    score: float
    in_sample_return: float
    in_sample_sharpe: float
    in_sample_max_drawdown: float
    out_sample_return: float
    out_sample_sharpe: float
    out_sample_max_drawdown: float
    stability: float
    trades: int
    passed_filters: bool = True
    robustness_penalty: float = 0.0
    overfit_gap: float = 0.0
    notes: List[str] = field(default_factory=list)
