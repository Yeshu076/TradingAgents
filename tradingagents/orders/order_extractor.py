"""
Module: order_extractor.py
Part of the orders subsystem.

This module contains logic for the orders operations as part of the broader TradingAgents framework.
"""
import re
from typing import List, Optional

from .order_intent import OrderIntent


_SIGNAL_REGEX = re.compile(r"\b(BUY|OVERWEIGHT|HOLD|UNDERWEIGHT|SELL)\b", re.IGNORECASE)
_PRICE_REGEX = re.compile(r"(?:\$|USD\s*)?(\d+(?:\.\d+)?)")
_PCT_REGEX = re.compile(r"(\d+(?:\.\d+)?)\s*%")


class OrderIntentExtractor:
    def __init__(self, signal_extractor):
        self.signal_extractor = signal_extractor

    def _extract_signal(self, text: str) -> str:
        llm_signal = (self.signal_extractor(text) or "").strip().upper()
        if llm_signal in {"BUY", "OVERWEIGHT", "HOLD", "UNDERWEIGHT", "SELL"}:
            return llm_signal

        match = _SIGNAL_REGEX.search(text or "")
        if match:
            return match.group(1).upper()

        return "HOLD"

    def _extract_price_triplet(self, trader_plan: str):
        prices = [float(m.group(1)) for m in _PRICE_REGEX.finditer(trader_plan or "")]
        if not prices:
            return None, None, None
        entry = prices[0]
        stop = prices[1] if len(prices) > 1 else None
        target = prices[2] if len(prices) > 2 else None
        return entry, stop, target

    def _extract_position_size_pct(self, trader_plan: str) -> Optional[float]:
        match = _PCT_REGEX.search(trader_plan or "")
        if not match:
            return None
        value = float(match.group(1))
        if value > 1.0:
            value = value / 100.0
        return max(0.0, min(value, 1.0))

    def _extract_horizon(self, text: str) -> Optional[str]:
        lowered = (text or "").lower()
        if "intraday" in lowered:
            return "intraday"
        if "swing" in lowered:
            return "swing"
        if "positional" in lowered or "position" in lowered:
            return "positional"
        return None

    def _compute_confidence(self, signal: str, full_signal: str, trader_plan: str) -> float:
        text = f"{full_signal}\n{trader_plan}".lower()
        strength_words = [
            "high conviction",
            "strong",
            "decisive",
            "clear",
            "robust",
            "confirm",
            "favorable",
        ]
        weak_words = [
            "uncertain",
            "mixed",
            "ambiguous",
            "cautious",
            "wait",
            "monitor",
        ]

        strong_hits = sum(1 for w in strength_words if w in text)
        weak_hits = sum(1 for w in weak_words if w in text)

        base = {
            "BUY": 0.72,
            "OVERWEIGHT": 0.66,
            "HOLD": 0.55,
            "UNDERWEIGHT": 0.62,
            "SELL": 0.70,
        }.get(signal, 0.55)

        score = base + (0.04 * strong_hits) - (0.04 * weak_hits)
        return max(0.0, min(score, 1.0))

    def extract(
        self,
        full_signal: str,
        trader_plan: str = "",
        ticker: str = "",
        instrument_type: str = "equity",
        analyst_teams: Optional[List[str]] = None,
        debate_rounds_used: int = 0,
        research_depth: str = "unknown",
    ) -> OrderIntent:
        signal = self._extract_signal(full_signal)
        entry, stop, target = self._extract_price_triplet(trader_plan)
        size_pct = self._extract_position_size_pct(trader_plan)
        horizon = self._extract_horizon(f"{full_signal}\n{trader_plan}")
        confidence = self._compute_confidence(signal, full_signal, trader_plan)

        return OrderIntent(
            ticker=ticker,
            instrument_type=instrument_type,
            signal=signal,
            confidence=confidence,
            suggested_entry=entry,
            suggested_stop_loss=stop,
            suggested_target=target,
            position_size_pct=size_pct,
            time_horizon=horizon,
            analyst_teams=analyst_teams or [],
            debate_rounds_used=debate_rounds_used,
            research_depth=research_depth,
            final_decision_raw=full_signal or "",
            trader_plan_raw=trader_plan or "",
        )
