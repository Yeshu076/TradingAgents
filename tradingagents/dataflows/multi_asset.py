"""
Module: multi_asset.py
Part of the dataflows subsystem.

This module contains logic for the dataflows operations as part of the broader TradingAgents framework.
"""
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf

from .interface import route_to_vendor
from .option_chain_providers import get_yfinance_option_chain_snapshot


_NIFTY_ALIASES = {"NIFTY", "NIFTY50", "NIFTY 50", "NIFTYBEES.NS", "^NSEI"}


def _normalize_snapshot_symbol(symbol: str, instrument_type: str) -> str:
    if instrument_type == "options" and symbol.strip().upper() in _NIFTY_ALIASES:
        return "^NSEI"
    return symbol


def _fmt_num(value) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.4f}"
    except Exception:
        return str(value)


def get_market_snapshot(
    symbol: str,
    instrument_type: str,
    trade_date: str,
    look_back_days: int = 30,
) -> str:
    """Return a compact market snapshot for forex/crypto/options/equity symbols."""
    end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=look_back_days + 10)

    normalized_symbol = _normalize_snapshot_symbol(symbol, instrument_type)
    data = yf.Ticker(normalized_symbol).history(
        start=start_dt.strftime("%Y-%m-%d"),
        end=(end_dt + timedelta(days=1)).strftime("%Y-%m-%d"),
    )

    if data.empty:
        return f"No market snapshot data found for {symbol} up to {trade_date}."

    data = data.sort_index()
    close = data["Close"].dropna()
    if close.empty:
        return f"No close price data found for {symbol}."

    last_close = close.iloc[-1]
    first_close = close.iloc[max(0, len(close) - min(len(close), look_back_days))]
    pct_change = ((last_close / first_close) - 1.0) * 100 if first_close else 0.0

    high_lookback = data["High"].tail(look_back_days).max() if "High" in data.columns else None
    low_lookback = data["Low"].tail(look_back_days).min() if "Low" in data.columns else None
    avg_volume = data["Volume"].tail(look_back_days).mean() if "Volume" in data.columns else None

    snapshot = [
        f"Instrument Type: {instrument_type}",
        f"Symbol: {symbol}",
        f"Data Symbol: {normalized_symbol}",
        f"As Of: {trade_date}",
        f"Last Close: {_fmt_num(last_close)}",
        f"{look_back_days}D Change: {_fmt_num(pct_change)}%",
        f"{look_back_days}D High: {_fmt_num(high_lookback)}",
        f"{look_back_days}D Low: {_fmt_num(low_lookback)}",
        f"{look_back_days}D Avg Volume: {_fmt_num(avg_volume)}",
    ]

    if instrument_type == "forex":
        snapshot.append("Note: Include spread/session and macro-event risk in interpretation.")
    elif instrument_type == "crypto":
        snapshot.append("Note: Include 24x7 volatility regime and derivatives context where available.")
    elif instrument_type == "options":
        snapshot.append("Note: Combine with option-chain/IV/Greeks/OI data before final decision.")

    return "\n".join(snapshot)


def get_option_chain_snapshot(
    symbol: str,
    expiry: Optional[str] = None,
    top_n: int = 10,
) -> str:
    """Return top open-interest calls and puts for a symbol's option chain.

    This is a lightweight bootstrap implementation using yfinance.
    """
    try:
        return route_to_vendor("get_option_chain_snapshot", symbol, expiry, top_n)
    except Exception as route_error:
        # Last-resort fallback keeps behavior available when vendor config is missing.
        try:
            return get_yfinance_option_chain_snapshot(symbol, expiry, top_n)
        except Exception as exc:
            if symbol.strip().upper() in _NIFTY_ALIASES:
                return (
                    f"No option expiries available for {symbol}. "
                    f"Dhan/yfinance attempts failed. "
                    f"Configure DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN for production Nifty options. "
                    f"Details: {route_error} | fallback={exc}"
                )
            return f"No option expiries available for {symbol}. ({exc})"


def get_crypto_derivatives_snapshot(symbol: str) -> str:
    """Return crypto derivatives metrics through configured vendor routing."""
    return route_to_vendor("get_crypto_derivatives_snapshot", symbol)
