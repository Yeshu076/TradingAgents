from __future__ import annotations
"""
Module: dhan_option_chain.py
Part of the dataflows subsystem.

This module contains logic for the dataflows operations as part of the broader TradingAgents framework.
"""

import os
from datetime import datetime
from typing import Any, Dict, Optional

import requests

from .option_chain_providers import pick_nearest_future_expiry


OPTION_CHAIN_URL = "https://api.dhan.co/v2/optionchain"
EXPIRY_LIST_URL = "https://api.dhan.co/v2/optionchain/expirylist"


NIFTY_ALIASES = {
    "NIFTY",
    "NIFTY50",
    "NIFTY 50",
    "NIFTYBEES.NS",
    "^NSEI",
}


def _credentials_from_env() -> tuple[str, str]:
    client_id = os.getenv("DHAN_CLIENT_ID", "").strip()
    access_token = os.getenv("DHAN_ACCESS_TOKEN", "").strip()
    if not client_id or not access_token:
        raise RuntimeError(
            "Missing DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN for Dhan option-chain provider"
        )
    return client_id, access_token


def _normalize_underlying(symbol: str) -> tuple[str, int, str]:
    s = symbol.strip().upper()
    if s in NIFTY_ALIASES:
        sec_id = int(os.getenv("DHAN_NIFTY_SECURITY_ID", "13"))
        segment = os.getenv("DHAN_NIFTY_UNDERLYING_SEGMENT", "IDX_I").strip() or "IDX_I"
        return "NIFTY", sec_id, segment

    raise RuntimeError(
        f"Dhan option-chain provider currently supports NIFTY aliases only, got: {symbol}"
    )


def _extract_leg_value(leg: Dict[str, Any], key_candidates: list[str], default: float = 0.0) -> float:
    for key in key_candidates:
        value: Any = None
        if "." in key:
            current: Any = leg
            for part in key.split("."):
                if not isinstance(current, dict) or part not in current:
                    current = None
                    break
                current = current[part]
            value = current
        else:
            value = leg.get(key)

        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return default
    return default


def _flatten_option_rows(raw: Dict[str, Any]) -> list[Dict[str, Any]]:
    data_obj = raw.get("data") if isinstance(raw.get("data"), dict) else None
    if data_obj and isinstance(data_obj.get("oc"), dict):
        rows: list[Dict[str, Any]] = []
        for strike_key, legs in data_obj["oc"].items():
            if not isinstance(legs, dict):
                continue
            rows.append(
                {
                    "strike_price": float(strike_key),
                    "CE": legs.get("ce") or {},
                    "PE": legs.get("pe") or {},
                }
            )
        return rows

    chain = raw.get("data") or raw.get("optionChain") or raw
    if isinstance(chain, list):
        return [row for row in chain if isinstance(row, dict)]

    rows = []
    if isinstance(chain, dict):
        for strike_key, legs in chain.items():
            if not isinstance(legs, dict):
                continue
            rows.append(
                {
                    "strike_price": float(strike_key),
                    "CE": legs.get("CE") or {},
                    "PE": legs.get("PE") or {},
                }
            )
    return rows


def _post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
    resp = requests.post(url, json=payload, headers=headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, dict) else {}


def _fetch_expiry_list(client_id: str, access_token: str, under_security_id: int, under_exchange_segment: str) -> list[str]:
    payload = {
        "UnderlyingScrip": int(under_security_id),
        "UnderlyingSeg": under_exchange_segment,
    }
    headers = {
        "Content-Type": "application/json",
        "access-token": access_token,
        "client-id": str(client_id),
    }
    raw = _post_json(EXPIRY_LIST_URL, payload, headers)
    data = raw.get("data") if isinstance(raw.get("data"), list) else []

    valid = []
    for item in data:
        if isinstance(item, str):
            try:
                datetime.strptime(item, "%Y-%m-%d")
                valid.append(item)
            except ValueError:
                continue
    return sorted(set(valid))


def get_dhan_option_chain_snapshot(symbol: str, expiry: Optional[str] = None, top_n: int = 10) -> str:
    """Fetch Nifty option chain snapshot via Dhan V2 APIs using env credentials."""
    client_id, access_token = _credentials_from_env()
    normalized, under_security_id, under_exchange_segment = _normalize_underlying(symbol)

    expiries = _fetch_expiry_list(client_id, access_token, under_security_id, under_exchange_segment)
    if not expiries:
        raise RuntimeError(f"No option expiries available for {normalized} via Dhan")

    chosen_expiry = expiry or pick_nearest_future_expiry(expiries)
    if not chosen_expiry:
        raise RuntimeError(f"Could not select expiry for {normalized} via Dhan")
    if chosen_expiry not in expiries:
        raise RuntimeError(
            f"Requested expiry {chosen_expiry} not available for {normalized}. "
            f"Available expiries: {', '.join(expiries[:8])}"
        )

    payload = {
        "UnderlyingScrip": int(under_security_id),
        "UnderlyingSeg": under_exchange_segment,
        "Expiry": chosen_expiry,
    }
    headers = {
        "Content-Type": "application/json",
        "access-token": access_token,
        "client-id": str(client_id),
    }
    raw = _post_json(OPTION_CHAIN_URL, payload, headers)
    rows = _flatten_option_rows(raw)

    if not rows:
        raise RuntimeError(f"No option chain rows returned for {normalized} via Dhan")

    parsed = []
    for row in rows:
        strike = float(row.get("strike_price", 0))
        ce = row.get("CE") if isinstance(row.get("CE"), dict) else {}
        pe = row.get("PE") if isinstance(row.get("PE"), dict) else {}

        ce_oi = _extract_leg_value(ce, ["oi", "openInterest"])
        pe_oi = _extract_leg_value(pe, ["oi", "openInterest"])
        total_oi = ce_oi + pe_oi

        parsed.append(
            {
                "strike": strike,
                "call_ltp": _extract_leg_value(ce, ["last_price", "lastPrice", "ltp"]),
                "call_iv": _extract_leg_value(ce, ["implied_volatility", "impliedVolatility", "iv"]),
                "call_oi": ce_oi,
                "put_ltp": _extract_leg_value(pe, ["last_price", "lastPrice", "ltp"]),
                "put_iv": _extract_leg_value(pe, ["implied_volatility", "impliedVolatility", "iv"]),
                "put_oi": pe_oi,
                "total_oi": total_oi,
            }
        )

    top = sorted(parsed, key=lambda x: x["total_oi"], reverse=True)[: max(1, top_n)]
    lines = [
        f"Option Chain Snapshot (dhan) for {normalized} (expiry={chosen_expiry})",
        "Top strikes by total OI:",
    ]

    for item in top:
        lines.append(
            " | ".join(
                [
                    f"Strike={item['strike']:.0f}",
                    f"CE LTP={item['call_ltp']:.2f}",
                    f"CE OI={item['call_oi']:.0f}",
                    f"CE IV={item['call_iv']:.2f}",
                    f"PE LTP={item['put_ltp']:.2f}",
                    f"PE OI={item['put_oi']:.0f}",
                    f"PE IV={item['put_iv']:.2f}",
                    f"Total OI={item['total_oi']:.0f}",
                ]
            )
        )

    return "\n".join(lines)
