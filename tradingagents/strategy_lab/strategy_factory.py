from __future__ import annotations
"""
Module: strategy_factory.py
Part of the strategy_lab subsystem.

This module contains logic for the strategy_lab operations as part of the broader TradingAgents framework.
"""

import random
from typing import List

from .models import StrategySpec


def initial_population(size: int, rng: random.Random) -> List[StrategySpec]:
    families = ["ema_crossover", "rsi_mean_reversion", "donchian_breakout"]
    population: List[StrategySpec] = []

    for _ in range(size):
        fam = rng.choice(families)
        population.append(_random_spec(fam, rng))

    return population


def mutate_population(elites: List[StrategySpec], size: int, rng: random.Random) -> List[StrategySpec]:
    if not elites:
        return initial_population(size, rng)

    out: List[StrategySpec] = []
    while len(out) < size:
        base = rng.choice(elites)
        out.append(_mutate_spec(base, rng))
    return out


def _random_spec(family: str, rng: random.Random) -> StrategySpec:
    if family == "ema_crossover":
        fast = rng.randint(5, 30)
        slow = rng.randint(max(fast + 5, 20), 120)
        return StrategySpec(name=f"ema_{fast}_{slow}", family=family, params={"fast": fast, "slow": slow})

    if family == "rsi_mean_reversion":
        period = rng.randint(7, 30)
        oversold = rng.randint(15, 35)
        overbought = rng.randint(65, 85)
        return StrategySpec(
            name=f"rsi_{period}_{oversold}_{overbought}",
            family=family,
            params={"period": period, "oversold": oversold, "overbought": overbought},
        )

    lookback = rng.randint(10, 80)
    return StrategySpec(name=f"donchian_{lookback}", family="donchian_breakout", params={"lookback": lookback})


def _mutate_spec(spec: StrategySpec, rng: random.Random) -> StrategySpec:
    p = dict(spec.params)
    family = spec.family

    if family == "ema_crossover":
        p["fast"] = int(max(3, p["fast"] + rng.randint(-3, 3)))
        p["slow"] = int(max(p["fast"] + 3, p["slow"] + rng.randint(-10, 10)))
        return StrategySpec(name=f"ema_{p['fast']}_{p['slow']}", family=family, params=p)

    if family == "rsi_mean_reversion":
        p["period"] = int(max(5, p["period"] + rng.randint(-3, 3)))
        p["oversold"] = int(min(45, max(5, p["oversold"] + rng.randint(-5, 5))))
        p["overbought"] = int(min(95, max(55, p["overbought"] + rng.randint(-5, 5))))
        if p["oversold"] >= p["overbought"]:
            p["oversold"] = max(5, p["overbought"] - 10)
        return StrategySpec(name=f"rsi_{p['period']}_{p['oversold']}_{p['overbought']}", family=family, params=p)

    p["lookback"] = int(max(5, p["lookback"] + rng.randint(-8, 8)))
    return StrategySpec(name=f"donchian_{p['lookback']}", family=family, params=p)
