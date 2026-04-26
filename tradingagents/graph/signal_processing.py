"""
Module: signal_processing.py
Part of the graph subsystem.

This module contains logic for the graph operations as part of the broader TradingAgents framework.
"""
# TradingAgents/graph/signal_processing.py

from typing import List, Optional

from langchain_openai import ChatOpenAI

from tradingagents.orders import OrderIntentExtractor, OrderIntentValidator


class SignalProcessor:
    """Processes trading signals to extract actionable decisions."""

    def __init__(self, quick_thinking_llm: ChatOpenAI):
        """Initialize with an LLM for processing."""
        self.quick_thinking_llm = quick_thinking_llm
        self.order_intent_extractor = OrderIntentExtractor(self.process_signal)
        self.order_intent_validator = OrderIntentValidator()

    def process_signal(self, full_signal: str) -> str:
        """
        Process a full trading signal to extract the core decision.

        Args:
            full_signal: Complete trading signal text

        Returns:
            Extracted rating (BUY, OVERWEIGHT, HOLD, UNDERWEIGHT, or SELL)
        """
        messages = [
            (
                "system",
                "You are an efficient assistant that extracts the trading decision from analyst reports. "
                "Extract the rating as exactly one of: BUY, OVERWEIGHT, HOLD, UNDERWEIGHT, SELL. "
                "Output only the single rating word, nothing else.",
            ),
            ("human", full_signal),
        ]

        response = self.quick_thinking_llm.invoke(messages).content
        return (response or "HOLD").strip().upper()

    def extract_order_intent(
        self,
        full_signal: str,
        trader_plan: str = "",
        ticker: str = "",
        instrument_type: str = "equity",
        analyst_teams: Optional[List[str]] = None,
        debate_rounds_used: int = 0,
        research_depth: str = "unknown",
        final_state: Optional[dict] = None,
    ):
        """Extract structured order intent while keeping string signal compatibility elsewhere."""
        intent = self.order_intent_extractor.extract(
            full_signal=full_signal,
            trader_plan=trader_plan,
            ticker=ticker,
            instrument_type=instrument_type,
            analyst_teams=analyst_teams,
            debate_rounds_used=debate_rounds_used,
            research_depth=research_depth,
        )
        return self.order_intent_validator.validate(intent, final_state=final_state)
