"""
Module: trading_graph.py
Part of the graph subsystem.

This module contains logic for the graph operations as part of the broader TradingAgents framework.
"""
# TradingAgents/graph/trading_graph.py

import os
import time
import threading
from pathlib import Path
import json
from datetime import date
from typing import Dict, Any, Tuple, List, Optional

from langgraph.prebuilt import ToolNode

from tradingagents.llm_clients import create_llm_client

from tradingagents.agents import *
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.agents.utils.memory import FinancialSituationMemory
from tradingagents.agents.utils.agent_states import (
    AgentState,
    InvestDebateState,
    RiskDebateState,
)
from tradingagents.dataflows.config import set_config

# Import the new abstract tool methods from agent_utils
from tradingagents.agents.utils.agent_utils import (
    get_stock_data,
    get_indicators,
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement,
    get_news,
    get_insider_transactions,
    get_global_news
)

from .conditional_logic import ConditionalLogic
from .setup import GraphSetup
from .propagation import Propagator
from .reflection import Reflector
from .signal_processing import SignalProcessor


class TradingAgentsGraph:
    """Main class that orchestrates the trading agents framework."""

    def __init__(
        self,
        selected_analysts=["market", "social", "news", "fundamentals"],
        debug=False,
        config: Dict[str, Any] = None,
        callbacks: Optional[List] = None,
    ):
        """Initialize the trading agents graph and components.

        Args:
            selected_analysts: List of analyst types to include
            debug: Whether to run in debug mode
            config: Configuration dictionary. If None, uses default config
            callbacks: Optional list of callback handlers (e.g., for tracking LLM/tool stats)
        """
        self.debug = debug
        self.config = config or DEFAULT_CONFIG
        self.callbacks = callbacks or []

        # Update the interface's config
        set_config(self.config)

        # Create necessary directories
        os.makedirs(
            os.path.join(self.config["project_dir"], "dataflows/data_cache"),
            exist_ok=True,
        )

        # Initialize LLMs with provider-specific thinking configuration
        llm_kwargs = self._get_provider_kwargs()

        # GAP-23: LLM request timeout — prevents hung agent nodes
        _timeout_s = int(os.environ.get("TRADINGAGENTS_LLM_TIMEOUT_S", "60"))
        llm_kwargs["request_timeout"] = _timeout_s

        # Add callbacks to kwargs if provided (passed to LLM constructor)
        if self.callbacks:
            llm_kwargs["callbacks"] = self.callbacks

        deep_client = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["deep_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )
        quick_client = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["quick_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )

        self.deep_thinking_llm = deep_client.get_llm()
        self.quick_thinking_llm = quick_client.get_llm()
        
        # Initialize memories
        self.bull_memory = FinancialSituationMemory("bull_memory", self.config)
        self.bear_memory = FinancialSituationMemory("bear_memory", self.config)
        self.trader_memory = FinancialSituationMemory("trader_memory", self.config)
        self.invest_judge_memory = FinancialSituationMemory("invest_judge_memory", self.config)
        self.portfolio_manager_memory = FinancialSituationMemory("portfolio_manager_memory", self.config)

        # Create tool nodes
        self.tool_nodes = self._create_tool_nodes()

        # Initialize components
        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=self.config["max_debate_rounds"],
            max_risk_discuss_rounds=self.config["max_risk_discuss_rounds"],
        )
        self.graph_setup = GraphSetup(
            self.quick_thinking_llm,
            self.deep_thinking_llm,
            self.tool_nodes,
            self.bull_memory,
            self.bear_memory,
            self.trader_memory,
            self.invest_judge_memory,
            self.portfolio_manager_memory,
            self.conditional_logic,
        )

        self.propagator = Propagator()
        self.reflector = Reflector(self.quick_thinking_llm)
        self.signal_processor = SignalProcessor(self.quick_thinking_llm)

        # State tracking
        self.curr_state = None
        self.ticker = None
        self.log_states_dict = {}  # date to full state dict

        # Set up the graph
        self.graph = self.graph_setup.setup_graph(selected_analysts)

    def _get_provider_kwargs(self) -> Dict[str, Any]:
        """Get provider-specific kwargs for LLM client creation."""
        kwargs = {}
        provider = self.config.get("llm_provider", "").lower()

        if provider == "google":
            thinking_level = self.config.get("google_thinking_level")
            if thinking_level:
                kwargs["thinking_level"] = thinking_level

        elif provider == "openai":
            reasoning_effort = self.config.get("openai_reasoning_effort")
            if reasoning_effort:
                kwargs["reasoning_effort"] = reasoning_effort

        elif provider == "anthropic":
            effort = self.config.get("anthropic_effort")
            if effort:
                kwargs["effort"] = effort

        return kwargs

    def _create_tool_nodes(self) -> Dict[str, ToolNode]:
        """Create tool nodes for different data sources using abstract methods."""
        return {
            "market": ToolNode(
                [
                    # Core stock data tools
                    get_stock_data,
                    # Technical indicators
                    get_indicators,
                ]
            ),
            "social": ToolNode(
                [
                    # News tools for social media analysis
                    get_news,
                ]
            ),
            "news": ToolNode(
                [
                    # News and insider information
                    get_news,
                    get_global_news,
                    get_insider_transactions,
                ]
            ),
            "fundamentals": ToolNode(
                [
                    # Fundamental analysis tools
                    get_fundamentals,
                    get_balance_sheet,
                    get_cashflow,
                    get_income_statement,
                ]
            ),
        }

    def propagate(self, company_name, trade_date, instrument_type: Optional[str] = None, instrument_metadata: Optional[Dict[str, Any]] = None):
        """Run the trading agents graph for an instrument on a specific date."""

        self.ticker = company_name

        # GAP-23: Per-cycle cost guard
        max_cost_usd = float(os.environ.get("TRADINGAGENTS_MAX_LLM_COST_USD", "2.00"))
        cycle_timeout_s = int(os.environ.get("TRADINGAGENTS_CYCLE_TIMEOUT_S", "600"))  # 10 min default

        # Initialize state
        resolved_instrument_type = instrument_type or self.config.get("instrument_type", "equity")
        resolved_instrument_metadata = instrument_metadata or self.config.get("instrument_metadata", {})
        
        # --- Inject Bot Telemetry ---
        try:
            import redis
            import json
            import os as _os
            r = redis.Redis(
                host=_os.getenv("REDIS_HOST", "localhost"), 
                port=int(_os.getenv("REDIS_PORT", 6379)), 
                password=_os.getenv("REDIS_PASSWORD"),
                decode_responses=True
            )
            bot_telemetry = {}
            for bot_key in ["TELEMETRY_dhan", "TELEMETRY_delta", "TELEMETRY_forex"]:
                data = r.get(bot_key)
                if data:
                    bot_telemetry[bot_key.split('_')[1]] = json.loads(data)
            
            account_states = {}
            for state_key in ["DHAN_ACCOUNT_STATE", "DELTA_ACCOUNT_STATE", "MT5_ACCOUNT_STATE"]:
                data = r.get(state_key)
                if data:
                    account_states[state_key] = json.loads(data)

            resolved_instrument_metadata["bot_telemetry"] = bot_telemetry
            resolved_instrument_metadata["live_account_states"] = account_states
        except Exception:
            pass
        # ----------------------------

        init_agent_state = self.propagator.create_initial_state(
            company_name,
            trade_date,
            instrument_type=resolved_instrument_type,
            instrument_metadata=resolved_instrument_metadata,
        )
        args = self.propagator.get_graph_args()

        # GAP-23: Graph invocation with cycle-level timeout
        import logging as _log
        _logger = _log.getLogger(__name__)
        _result_holder: Dict[str, Any] = {}
        _exc_holder: List[Exception] = []

        def _run_graph():
            try:
                if self.debug:
                    trace = []
                    for chunk in self.graph.stream(init_agent_state, **args):
                        if chunk.get("messages"):
                            chunk["messages"][-1].pretty_print()
                            trace.append(chunk)
                    _result_holder["state"] = trace[-1] if trace else init_agent_state
                else:
                    _result_holder["state"] = self.graph.invoke(init_agent_state, **args)
            except Exception as _e:
                _exc_holder.append(_e)

        _t = threading.Thread(target=_run_graph, name=f"graph-{company_name}-{trade_date}", daemon=True)
        _start = time.time()
        _t.start()
        _t.join(timeout=cycle_timeout_s)

        if _t.is_alive():
            _logger.error(
                "[TIMEOUT] LLM graph cycle for %s exceeded %ds — returning HOLD.",
                company_name, cycle_timeout_s,
            )
            # Return a minimal safe state with HOLD signal
            _safe_state = dict(init_agent_state)
            _safe_state.update({
                "final_trade_decision": "HOLD",
                "trader_investment_plan": "Cycle timed out — holding as safety measure.",
                "investment_plan": "",
                "order_intent": {"signal": "HOLD", "ticker": company_name},
            })
            self.curr_state = _safe_state
            self._log_state(trade_date, _safe_state)
            return _safe_state, "HOLD"

        if _exc_holder:
            raise _exc_holder[0]

        elapsed = time.time() - _start
        _logger.info("[GRAPH] %s cycle completed in %.1fs.", company_name, elapsed)

        final_state = _result_holder.get("state", init_agent_state)
        # Store current state for reflection
        self.curr_state = final_state

        # Attach structured order intent (non-breaking addition).
        order_intent = self.signal_processor.extract_order_intent(
            full_signal=final_state.get("final_trade_decision", ""),
            trader_plan=final_state.get("trader_investment_plan", ""),
            ticker=final_state.get("company_of_interest", ""),
            instrument_type=final_state.get("instrument_type", self.config.get("instrument_type", "equity")),
            analyst_teams=["market", "social", "news", "fundamentals"],
            debate_rounds_used=final_state.get("risk_debate_state", {}).get("count", 0),
            research_depth=str(self.config.get("max_debate_rounds", "unknown")),
            final_state=final_state,
        )
        final_state["order_intent"] = order_intent.model_dump()

        # Log state
        self._log_state(trade_date, final_state)

        # Return decision and processed signal
        return final_state, order_intent.signal

    def _log_state(self, trade_date, final_state):
        """Log the final state to a JSON file."""
        self.log_states_dict[str(trade_date)] = {
            "company_of_interest": final_state["company_of_interest"],
            "trade_date": final_state["trade_date"],
            "instrument_type": final_state.get("instrument_type", self.config.get("instrument_type", "equity")),
            "instrument_metadata": final_state.get("instrument_metadata", self.config.get("instrument_metadata", {})),
            "market_report": final_state["market_report"],
            "sentiment_report": final_state["sentiment_report"],
            "news_report": final_state["news_report"],
            "fundamentals_report": final_state["fundamentals_report"],
            "investment_debate_state": {
                "bull_history": final_state["investment_debate_state"]["bull_history"],
                "bear_history": final_state["investment_debate_state"]["bear_history"],
                "history": final_state["investment_debate_state"]["history"],
                "current_response": final_state["investment_debate_state"][
                    "current_response"
                ],
                "judge_decision": final_state["investment_debate_state"][
                    "judge_decision"
                ],
            },
            "trader_investment_decision": final_state["trader_investment_plan"],
            "risk_debate_state": {
                "aggressive_history": final_state["risk_debate_state"]["aggressive_history"],
                "conservative_history": final_state["risk_debate_state"]["conservative_history"],
                "neutral_history": final_state["risk_debate_state"]["neutral_history"],
                "history": final_state["risk_debate_state"]["history"],
                "judge_decision": final_state["risk_debate_state"]["judge_decision"],
            },
            "investment_plan": final_state["investment_plan"],
            "final_trade_decision": final_state["final_trade_decision"],
            "order_intent": final_state.get("order_intent", {}),
        }

        # Save to file
        directory = Path(f"eval_results/{self.ticker}/TradingAgentsStrategy_logs/")
        directory.mkdir(parents=True, exist_ok=True)

        with open(
            f"eval_results/{self.ticker}/TradingAgentsStrategy_logs/full_states_log_{trade_date}.json",
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(self.log_states_dict, f, indent=4)

    def reflect_and_remember(self, returns_losses):
        """Reflect on decisions and update memory based on returns."""
        self.reflector.reflect_bull_researcher(
            self.curr_state, returns_losses, self.bull_memory
        )
        self.reflector.reflect_bear_researcher(
            self.curr_state, returns_losses, self.bear_memory
        )
        self.reflector.reflect_trader(
            self.curr_state, returns_losses, self.trader_memory
        )
        self.reflector.reflect_invest_judge(
            self.curr_state, returns_losses, self.invest_judge_memory
        )
        self.reflector.reflect_portfolio_manager(
            self.curr_state, returns_losses, self.portfolio_manager_memory
        )

    def process_signal(self, full_signal):
        """Process a signal to extract the core decision."""
        return self.signal_processor.process_signal(full_signal)
