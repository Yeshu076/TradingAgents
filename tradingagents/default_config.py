"""
Module: default_config.py
Part of the tradingagents subsystem.

This module contains logic for the tradingagents operations as part of the broader TradingAgents framework.
"""
import os

DEFAULT_CONFIG = {
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": os.getenv("TRADINGAGENTS_RESULTS_DIR", "./results"),
    "data_cache_dir": os.path.join(
        os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
        "dataflows/data_cache",
    ),
    # LLM settings
    "llm_provider": "google",
    "deep_think_llm": "gemini-3.1-pro-preview",
    "quick_think_llm": "gemini-3.1-flash-lite-preview",
    "backend_url": "",
    # Market/instrument settings
    # Supported values: equity, forex, crypto, options
    "instrument_type": "equity",
    # Optional metadata for derivatives/instrument-specific routing
    # Example: {"exchange": "NSE", "underlying": "NIFTY", "expiry": "2026-04-30"}
    "instrument_metadata": {},
    # Provider-specific thinking configuration
    "google_thinking_level": None,      # "high", "minimal", etc.
    "openai_reasoning_effort": None,    # "medium", "high", "low"
    "anthropic_effort": None,           # "high", "medium", "low"
    # Debate and discussion settings
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_recur_limit": 100,
    # Data vendor configuration
    # Category-level configuration (default for all tools in category)
    "data_vendors": {
        "core_stock_apis": "yfinance",       # Options: alpha_vantage, yfinance
        "technical_indicators": "yfinance",  # Options: alpha_vantage, yfinance
        "fundamental_data": "yfinance",      # Options: alpha_vantage, yfinance
        "news_data": "yfinance",             # Options: alpha_vantage, yfinance
        "crypto_derivatives": "delta,binance,bybit",  # Preferred fallback chain for crypto derivatives
        "options_chain": "dhan,yfinance",  # Use Dhan for Nifty options, fallback to yfinance
    },
    # Tool-level configuration (takes precedence over category-level)
    "tool_vendors": {
        # Example: "get_stock_data": "alpha_vantage",  # Override category default
    },
}
