"""
Module: trader.py
Part of the trader subsystem.

This module contains logic for the trader operations as part of the broader TradingAgents framework.
"""
import functools
import time
import json
from pydantic import BaseModel, Field

from tradingagents.agents.utils.agent_utils import build_instrument_context
from tradingagents.agents.utils.vectorbt_sandbox import run_auto_quant

class QuantParams(BaseModel):
    indicator: str = Field(description="The technical indicator to use (e.g., EMA, SMA, RSI)")
    fast_period: int = Field(description="Fast period window")
    slow_period: int = Field(description="Slow period window")

class TraderDecision(BaseModel):
    recommendation: str = Field(description="Must be BUY, SELL, or HOLD")
    rationale: str = Field(description="Short reasoning for the decision")
    quant_params: QuantParams = Field(description="Technical parameters for Auto-Quant validation")

def create_trader(llm, memory):
    def trader_node(state, name):
        company_name = state["company_of_interest"]
        instrument_context = build_instrument_context(
            company_name,
            state.get("instrument_type", "equity"),
            state.get("instrument_metadata", {}),
        )
        investment_plan = state["investment_plan"]
        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]

        curr_situation = f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}\n\n{fundamentals_report}"
        past_memories = memory.get_memories(curr_situation, n_matches=2)

        past_memory_str = ""
        if past_memories:
            for i, rec in enumerate(past_memories, 1):
                past_memory_str += f"- Past Decision: {rec['recommendation']}\n- Outcome & PnL: {rec.get('outcome', 'Unknown')}\n\n"
        else:
            past_memory_str = "No past memories found."

        context = {
            "role": "user",
            "content": f"Based on a comprehensive analysis by a team of analysts, here is an investment plan tailored for {company_name}. {instrument_context} This plan incorporates insights from current technical market trends, macroeconomic indicators, and social media sentiment. Use this plan as a foundation for evaluating your next trading decision.\n\nProposed Investment Plan: {investment_plan}\n\nLeverage these insights to make an informed and strategic decision.",     
        }

        messages = [
            {
                "role": "system",
                "content": f"You are a trading agent analyzing market data to make investment decisions. Based on your analysis, provide a specific recommendation to buy, sell, or hold.\nApply lessons from past decisions to strengthen your analysis. Here are reflections from similar situations you traded in and the lessons learned: {past_memory_str}",
            },
            context,
        ]

        structured_llm = llm.with_structured_output(TraderDecision)
        
        try:
            # Replaces the old regex loop with pure structured parsing
            decision: TraderDecision = structured_llm.invoke(messages)
            rec = decision.recommendation.upper()
            
            content = f"Recommendation: {rec}\nRationale: {decision.rationale}\n"
            
            q = decision.quant_params
            if q and rec in ["BUY", "SELL"]:
                direction = "longonly" if rec == "BUY" else "shortonly"
                quant_res = run_auto_quant(
                    symbol=company_name,
                    indicator=q.indicator,
                    fast_period=q.fast_period,
                    slow_period=q.slow_period,
                    direction=direction
                )

                if quant_res.get("status") == "failed":
                    content += f"[AUTO-QUANT REJECTION] Strategy {q.indicator} failed validation: {quant_res.get('reason')}. Forcing HOLD."
                    decision.recommendation = "HOLD"
                elif quant_res.get("status") == "passed":
                    content += f"[AUTO-QUANT APPROVED] Sharpe: {quant_res.get('sharpe_ratio')}, Max DD: {quant_res.get('max_drawdown_pct')}%"
            
        except Exception as e:
            # Fallback formatting if structured extraction completely fails
            content = f"FINAL TRANSACTION PROPOSAL: HOLD\n[ERROR] Structured parsing failed: {e}"

        return {
            "trader_investment_plan": content,
            "sender": name,
        }

    return functools.partial(trader_node, name="Trader")
