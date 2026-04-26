from __future__ import annotations
"""
Module: delta_exchange.py
Part of the dataflows subsystem.

This module contains logic for the dataflows operations as part of the broader TradingAgents framework.
"""

from typing import Any, Dict, Optional

import requests


DEFAULT_DELTA_BASE = "https://api.india.delta.exchange"


def _fmt_float(value: Any, precision: int = 6) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.{precision}f}"
    except Exception:
        return str(value)


def normalize_delta_symbol(symbol: str) -> str:
    """Normalize common crypto symbols to Delta perpetual-style symbols (e.g., BTCUSD)."""
    raw = symbol.strip().upper().replace("/", "-")
    if raw.endswith("USDT"):
        raw = raw[:-4] + "USD"
    if raw.endswith("-USD"):
        return raw.replace("-", "")
    if raw.endswith("USD") and "-" not in raw:
        return raw
    if "-" in raw:
        base, quote = raw.split("-", 1)
        quote = "USD" if quote in ("USD", "USDT") else quote
        return f"{base}{quote}"
    return raw + "USD"


def _get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    payload = resp.json()
    return payload.get("result", payload) if isinstance(payload, dict) else payload


def get_delta_crypto_derivatives_snapshot(symbol: str, base_url: str = DEFAULT_DELTA_BASE) -> str:
    """Fetch Delta Exchange crypto derivatives snapshot using public REST endpoints."""
    pair = normalize_delta_symbol(symbol)
    base_url = base_url.rstrip("/")

    product = _get_json(f"{base_url}/v2/products/{pair}")
    product_id = product.get("id") if isinstance(product, dict) else None

    tickers = _get_json(
        f"{base_url}/v2/tickers",
        params={"contract_types": "perpetual_futures"},
    )

    selected = None
    if isinstance(tickers, list):
        for row in tickers:
            if not isinstance(row, dict):
                continue
            if product_id is not None and row.get("product_id") == product_id:
                selected = row
                break
            if str(row.get("symbol", "")).upper() == pair:
                selected = row
                break

    if not selected:
        raise RuntimeError(f"No Delta ticker row found for {pair}")

    mark_price = selected.get("mark_price")
    last_price = selected.get("last_price")
    oi = selected.get("open_interest")
    funding_rate = selected.get("funding_rate")
    basis = selected.get("basis")

    if basis in (None, ""):
        try:
            mark = float(mark_price)
            spot = float(last_price)
            basis = ((mark / spot) - 1.0) * 100 if spot else None
        except Exception:
            basis = None

    return (
        f"Crypto Derivatives Snapshot (delta)\n"
        f"Symbol: {pair}\n"
        f"Product ID: {product_id if product_id is not None else 'N/A'}\n"
        f"Mark Price: {_fmt_float(mark_price, 4)}\n"
        f"Last Price: {_fmt_float(last_price, 4)}\n"
        f"Funding Rate: {_fmt_float(funding_rate, 8)}\n"
        f"Open Interest: {_fmt_float(oi, 2)}\n"
        f"Basis: {_fmt_float(basis, 4)}%"
    )
