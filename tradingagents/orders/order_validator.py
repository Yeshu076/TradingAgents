"""
Module: order_validator.py
Part of the orders subsystem.

This module contains logic for the orders operations as part of the broader TradingAgents framework.
"""
from .order_intent import OrderIntent


class OrderIntentValidator:
    def validate(self, intent: OrderIntent, final_state=None) -> OrderIntent:
        warnings = list(intent.validation_warnings)

        plan = (intent.trader_plan_raw or "").lower()
        if intent.signal in ("BUY", "OVERWEIGHT") and "sell" in plan and "buy" not in plan:
            warnings.append("Signal-plan mismatch: bullish signal but trader plan emphasizes sell.")
        if intent.signal in ("SELL", "UNDERWEIGHT") and "buy" in plan and "sell" not in plan:
            warnings.append("Signal-plan mismatch: bearish signal but trader plan emphasizes buy.")

        if intent.instrument_type == "crypto":
            raw = (intent.final_decision_raw or "").lower()
            if "funding" not in raw and "open interest" not in raw and "basis" not in raw:
                warnings.append("Crypto warning: final decision does not explicitly reference funding/OI/basis context.")

        consistency = self._compute_consistency(final_state)
        intent.consistency_score = consistency
        intent.validation_warnings = warnings
        return intent

    def _compute_consistency(self, final_state) -> float:
        if not final_state:
            return 0.5

        trader = (final_state.get("trader_investment_plan") or "").lower()
        final_decision = (final_state.get("final_trade_decision") or "").lower()

        bullish_words = ["buy", "overweight", "long", "accumulate"]
        bearish_words = ["sell", "underweight", "short", "reduce"]

        trader_bull = sum(1 for w in bullish_words if w in trader)
        trader_bear = sum(1 for w in bearish_words if w in trader)
        final_bull = sum(1 for w in bullish_words if w in final_decision)
        final_bear = sum(1 for w in bearish_words if w in final_decision)

        align = 0
        if trader_bull >= trader_bear and final_bull >= final_bear:
            align = 1
        elif trader_bear > trader_bull and final_bear > final_bull:
            align = 1

        return 0.75 if align else 0.4
