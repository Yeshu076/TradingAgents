from pydantic import BaseModel, Field
from tradingagents.agents.utils.agent_utils import build_instrument_context

class PortfolioDecision(BaseModel):
    rating: str = Field(description="Must be strictly one of: BUY, OVERWEIGHT, HOLD, UNDERWEIGHT, SELL")
    suggested_entry: float = Field(description="Safe entry limit price or 0.0 if market order acceptable")
    stop_loss: float = Field(description="Calculated Stop Loss or 0.0")
    take_profit: float = Field(description="Calculated Take Profit or 0.0")
    position_pct: float = Field(description="Percent of portfolio capital to allocate (0.0 to 100.0, mostly 1-5%)")
    executive_summary: str = Field(description="Concise action plan covering entry strategy, position sizing, key risk levels.")
    investment_thesis: str = Field(description="Detailed reasoning anchored in the analysts' debate.")

def create_portfolio_manager(llm, memory):
    def portfolio_manager_node(state) -> dict:

        instrument_context = build_instrument_context(
            state["company_of_interest"],
            state.get("instrument_type", "equity"),
            state.get("instrument_metadata", {}),
        )

        history = state["risk_debate_state"]["history"]
        risk_debate_state = state["risk_debate_state"]
        market_research_report = state["market_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]
        sentiment_report = state["sentiment_report"]
        trader_plan = state.get("trader_investment_plan", state.get("investment_plan", ""))

        curr_situation = f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}\n\n{fundamentals_report}"
        past_memories = memory.get_memories(curr_situation, n_matches=2)

        past_memory_str = ""
        if past_memories:
            for i, rec in enumerate(past_memories, 1):
                past_memory_str += rec.get("recommendation", "") + "\n\n"

        prompt = f'''As the Portfolio Manager, synthesize the risk analysts' debate and deliver the final trading decision.

{instrument_context}

**Context:**
- Trader's proposed plan: **{trader_plan}**
- Lessons from past decisions: **{past_memory_str}**

**Risk Analysts Debate History:**
{history}

Be decisive. You must determine exact numerical boundary limits for execution (entry, SL, TP).'''

        structured_llm = llm.with_structured_output(PortfolioDecision)
        
        try:
            decision: PortfolioDecision = structured_llm.invoke(prompt)
            formatted_decision = (
                f"Rating: {decision.rating}\n"
                f"Entry: \n"
                f"StopLoss: \n"
                f"Target: \n"
                f"Allocation: {decision.position_pct}%\n\n"
                f"Executive Summary: {decision.executive_summary}\n\n"
                f"Investment Thesis: {decision.investment_thesis}"
            )
        except Exception as e:
            formatted_decision = f"Rating: HOLD\nEntry: \nStopLoss: \nTarget: \nAllocation: 0%\nExecutive Summary: System Error [{e}]\nInvestment Thesis: Error occurred."

        new_risk_debate_state = {
            "judge_decision": formatted_decision,
            "history": risk_debate_state["history"],
            "aggressive_history": risk_debate_state["aggressive_history"],
            "conservative_history": risk_debate_state["conservative_history"],
            "neutral_history": risk_debate_state["neutral_history"],
            "latest_speaker": "Judge",
            "current_aggressive_response": risk_debate_state["current_aggressive_response"],
            "current_conservative_response": risk_debate_state["current_conservative_response"],
            "current_neutral_response": risk_debate_state["current_neutral_response"],
            "count": risk_debate_state["count"],
        }

        return {
            "risk_debate_state": new_risk_debate_state,
            "final_trade_decision": formatted_decision,
        }

    return portfolio_manager_node
