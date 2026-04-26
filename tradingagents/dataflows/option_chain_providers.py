from __future__ import annotations
"""
Module: option_chain_providers.py
Part of the dataflows subsystem.

This module contains logic for the dataflows operations as part of the broader TradingAgents framework.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

import yfinance as yf


def get_yfinance_option_chain_snapshot(
    symbol: str,
    expiry: Optional[str] = None,
    top_n: int = 10,
) -> str:
    """Return top open-interest calls and puts for a symbol option chain using yfinance."""
    ticker = yf.Ticker(symbol)
    expiries = list(ticker.options or [])
    if not expiries:
        raise RuntimeError(f"No option expiries available for {symbol} via yfinance")

    selected_expiry = expiry or expiries[0]
    if selected_expiry not in expiries:
        raise RuntimeError(
            f"Requested expiry {selected_expiry} not available for {symbol}. "
            f"Available expiries: {', '.join(expiries[:8])}"
        )

    chain = ticker.option_chain(selected_expiry)
    calls = chain.calls.sort_values(by="openInterest", ascending=False).head(top_n)
    puts = chain.puts.sort_values(by="openInterest", ascending=False).head(top_n)

    call_cols = [
        c
        for c in ["strike", "lastPrice", "impliedVolatility", "openInterest", "volume"]
        if c in calls.columns
    ]
    put_cols = [
        c
        for c in ["strike", "lastPrice", "impliedVolatility", "openInterest", "volume"]
        if c in puts.columns
    ]

    calls_table = calls[call_cols].to_string(index=False) if not calls.empty else "No calls data"
    puts_table = puts[put_cols].to_string(index=False) if not puts.empty else "No puts data"

    return (
        f"Option Chain Snapshot (yfinance) for {symbol} (expiry={selected_expiry})\n\n"
        f"Top Calls by Open Interest:\n{calls_table}\n\n"
        f"Top Puts by Open Interest:\n{puts_table}"
    )


def pick_nearest_future_expiry(expiries: list[str]) -> Optional[str]:
    """Pick nearest expiry >= today from YYYY-MM-DD list, fallback to first sorted value."""
    if not expiries:
        return None

    valid = []
    for item in expiries:
        try:
            valid.append(datetime.strptime(item, "%Y-%m-%d").date())
        except ValueError:
            continue

    if not valid:
        return None

    valid = sorted(valid)
    today = datetime.now(timezone.utc).date()

    for dt in valid:
        if dt >= today:
            return dt.strftime("%Y-%m-%d")

    return valid[0].strftime("%Y-%m-%d")
