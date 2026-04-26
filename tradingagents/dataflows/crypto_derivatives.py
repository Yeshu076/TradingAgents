"""
Module: crypto_derivatives.py
Part of the dataflows subsystem.

This module contains logic for the dataflows operations as part of the broader TradingAgents framework.
"""
from typing import Dict, Tuple

import requests

from .delta_exchange import get_delta_crypto_derivatives_snapshot


BINANCE_PREMIUM_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"
BINANCE_OPEN_INTEREST_URL = "https://fapi.binance.com/fapi/v1/openInterest"
BYBIT_TICKERS_URL = "https://api.bybit.com/v5/market/tickers"


def normalize_crypto_symbol(symbol: str) -> str:
    """Normalize common crypto symbol forms to exchange-style perp symbols.

    Examples:
    - BTC-USD -> BTCUSDT
    - ETH/USD -> ETHUSDT
    - BTCUSDT -> BTCUSDT
    """
    s = symbol.strip().upper().replace("/", "-")
    if s.endswith("-USDT"):
        return s.replace("-", "")
    if s.endswith("USDT"):
        return s
    if s.endswith("-USD"):
        return s.replace("-USD", "USDT")
    if "-" in s:
        base, quote = s.split("-", 1)
        if quote in ("USD", "USDT"):
            return f"{base}USDT"
    return f"{s}USDT"


def _fmt_float(value, precision: int = 6) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.{precision}f}"
    except Exception:
        return str(value)


def _safe_get_json(url: str, params: Dict[str, str]) -> Dict:
    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    return response.json()


def get_binance_crypto_derivatives_snapshot(symbol: str) -> str:
    """Fetch funding/open-interest/mark-index basis snapshot from Binance USDT-M futures."""
    pair = normalize_crypto_symbol(symbol)
    premium = _safe_get_json(BINANCE_PREMIUM_URL, {"symbol": pair})
    open_interest = _safe_get_json(BINANCE_OPEN_INTEREST_URL, {"symbol": pair})

    mark_price = premium.get("markPrice")
    index_price = premium.get("indexPrice")
    last_funding_rate = premium.get("lastFundingRate")
    next_funding_time = premium.get("nextFundingTime")
    oi_value = open_interest.get("openInterest")

    basis_pct = None
    try:
        if mark_price is not None and index_price is not None and float(index_price) != 0:
            basis_pct = (float(mark_price) / float(index_price) - 1.0) * 100
    except Exception:
        basis_pct = None

    return (
        f"Crypto Derivatives Snapshot (binance)\n"
        f"Symbol: {pair}\n"
        f"Mark Price: {_fmt_float(mark_price, 4)}\n"
        f"Index Price: {_fmt_float(index_price, 4)}\n"
        f"Last Funding Rate: {_fmt_float(last_funding_rate, 8)}\n"
        f"Open Interest (contracts): {_fmt_float(oi_value, 2)}\n"
        f"Mark-Index Basis: {_fmt_float(basis_pct, 4)}%\n"
        f"Next Funding Time (ms epoch): {next_funding_time if next_funding_time is not None else 'N/A'}"
    )


def get_bybit_crypto_derivatives_snapshot(symbol: str) -> str:
    """Fetch funding/open-interest/mark-index basis snapshot from Bybit linear futures."""
    pair = normalize_crypto_symbol(symbol)
    payload = _safe_get_json(BYBIT_TICKERS_URL, {"category": "linear", "symbol": pair})

    result = payload.get("result", {})
    rows = result.get("list", [])
    if not rows:
        raise RuntimeError(f"Bybit returned no ticker rows for {pair}")
    row = rows[0]

    mark_price = row.get("markPrice")
    index_price = row.get("indexPrice")
    funding_rate = row.get("fundingRate")
    oi_value = row.get("openInterest")
    basis_rate = row.get("basisRate")

    basis_pct = basis_rate
    if basis_pct in (None, ""):
        try:
            if mark_price is not None and index_price is not None and float(index_price) != 0:
                basis_pct = (float(mark_price) / float(index_price) - 1.0) * 100
        except Exception:
            basis_pct = None

    return (
        f"Crypto Derivatives Snapshot (bybit)\n"
        f"Symbol: {pair}\n"
        f"Mark Price: {_fmt_float(mark_price, 4)}\n"
        f"Index Price: {_fmt_float(index_price, 4)}\n"
        f"Funding Rate: {_fmt_float(funding_rate, 8)}\n"
        f"Open Interest: {_fmt_float(oi_value, 2)}\n"
        f"Basis: {_fmt_float(basis_pct, 4)}%"
    )


def get_crypto_derivatives_snapshot_with_vendor(symbol: str, vendor: str) -> str:
    """Vendor dispatch for crypto derivatives snapshots."""
    vendor_lower = vendor.strip().lower()
    if vendor_lower == "delta":
        return get_delta_crypto_derivatives_snapshot(symbol)
    if vendor_lower == "binance":
        return get_binance_crypto_derivatives_snapshot(symbol)
    if vendor_lower == "bybit":
        return get_bybit_crypto_derivatives_snapshot(symbol)
    raise ValueError(f"Unsupported crypto derivatives vendor: {vendor}")
