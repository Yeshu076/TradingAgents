"""
Module: multi_asset_tools.py
Part of the utils subsystem.

This module contains logic for the utils operations as part of the broader TradingAgents framework.
"""
from typing import Annotated, Optional

from langchain_core.tools import tool

from tradingagents.dataflows.multi_asset import (
    get_crypto_derivatives_snapshot as get_crypto_derivatives_snapshot_impl,
    get_market_snapshot as get_market_snapshot_impl,
    get_option_chain_snapshot as get_option_chain_snapshot_impl,
)


@tool
def get_market_snapshot(
    symbol: Annotated[str, "Instrument symbol (e.g., BTC-USD, EURUSD=X, NIFTY)"] ,
    instrument_type: Annotated[str, "Instrument class: equity, forex, crypto, options"],
    trade_date: Annotated[str, "Analysis date in YYYY-MM-DD format"],
    look_back_days: Annotated[int, "Lookback window in days"] = 30,
) -> str:
    """Get a compact market snapshot tailored by instrument class."""
    return get_market_snapshot_impl(symbol, instrument_type, trade_date, look_back_days)


@tool
def get_option_chain_snapshot(
    symbol: Annotated[str, "Underlying symbol for option chain lookup"],
    expiry: Annotated[Optional[str], "Expiry in YYYY-MM-DD format (optional)"] = None,
    top_n: Annotated[int, "Top contracts by open interest"] = 10,
) -> str:
    """Get option chain snapshot with top call/put open-interest strikes."""
    return get_option_chain_snapshot_impl(symbol, expiry, top_n)


@tool
def get_crypto_derivatives_snapshot(
    symbol: Annotated[str, "Crypto symbol (e.g., BTCUSDT, ETHUSDT)"]
) -> str:
    """Get crypto derivatives metrics snapshot (bootstrap placeholder)."""
    return get_crypto_derivatives_snapshot_impl(symbol)
