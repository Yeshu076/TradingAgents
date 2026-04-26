"""
Module: agent_utils.py
Part of the utils subsystem.

This module contains logic for the utils operations as part of the broader TradingAgents framework.
"""
from langchain_core.messages import HumanMessage, RemoveMessage

# Import tools from separate utility files
from tradingagents.agents.utils.core_stock_tools import (
    get_stock_data
)
from tradingagents.agents.utils.technical_indicators_tools import (
    get_indicators
)
from tradingagents.agents.utils.fundamental_data_tools import (
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement
)
from tradingagents.agents.utils.news_data_tools import (
    get_news,
    get_insider_transactions,
    get_global_news
)
from tradingagents.agents.utils.multi_asset_tools import (
    get_market_snapshot,
    get_option_chain_snapshot,
    get_crypto_derivatives_snapshot,
)


def build_instrument_context(
    ticker: str,
    instrument_type: str = "equity",
    instrument_metadata: dict | None = None,
) -> str:
    """Describe the exact instrument so agents preserve symbol semantics by market type."""
    normalized = (instrument_type or "equity").strip().lower()

    common = (
        f"The instrument to analyze is `{ticker}`. "
        "Use this exact symbol in every tool call, report, and recommendation. "
        "Preserve any exchange suffixes exactly as provided."
    )

    context = common

    if normalized == "forex":
        context = (
            common
            + " Treat it as a forex pair and include session-aware context "
            + "(Asia/Europe/US overlap), spread sensitivity, and macro-event risk."
        )

    elif normalized == "crypto":
        context = (
            common
            + " Treat it as a crypto instrument with 24x7 trading context, "
            + "volatility regime awareness, and derivatives metrics where available "
            + "(funding/open-interest/basis)."
        )

    elif normalized == "options":
        context = (
            common
            + " Treat it as an options instrument and include expiry, strike, "
            + "option type, IV/Greeks/OI interpretation, and time-decay impact in analysis."
        )

    metadata = instrument_metadata or {}
    playbook = metadata.get("strategy_playbook")
    if isinstance(playbook, dict):
        promoted_strategy = playbook.get("promoted_strategy", {})
        best_strategy = playbook.get("best_strategy", {})
        promotion_status = str(playbook.get("promotion_status", "")).strip().lower()
        selected_strategy = (
            promoted_strategy
            if promotion_status == "promoted" and isinstance(promoted_strategy, dict) and promoted_strategy
            else best_strategy
        )
        name = selected_strategy.get("name")
        family = selected_strategy.get("family")
        params = selected_strategy.get("params")
        if name and family:
            context += (
                " Strategy-lab playbook is available. "
                + f"Use {name} ({family}) as a baseline hypothesis, then validate or reject it with current data."
            )
        if params:
            context += f" Candidate parameters: {params}."

    live_account_states = metadata.get("live_account_states")
    if live_account_states:
        context += f"\n\nCRITICAL ACCOUNT STATE OVERRIDE:\nThe execution engine reports the following live broker margins and existing open positions: {live_account_states}\n"
        context += "You MUST look at these existing positions. If you already hold a position in this asset, consider HOLDING or CLOSING instead of piling on more risk. If margin_free is near 0, force HOLD/SELL.\n"

    return context


def create_msg_delete():
    def delete_messages(state):
        """Clear messages and add placeholder for Anthropic compatibility"""
        messages = state["messages"]

        # Remove all messages
        removal_operations = [RemoveMessage(id=m.id) for m in messages]

        # Add a minimal placeholder message
        placeholder = HumanMessage(content="Continue")

        return {"messages": removal_operations + [placeholder]}

    return delete_messages


        
