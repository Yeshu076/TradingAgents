from __future__ import annotations
"""
Module: healthcheck.py
Part of the ops subsystem.

This module contains logic for the ops operations as part of the broader TradingAgents framework.
"""

import os
import json
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List

from tradingagents.dataflows.delta_exchange import get_delta_crypto_derivatives_snapshot
from tradingagents.dataflows.dhan_option_chain import get_dhan_option_chain_snapshot
from tradingagents.dataflows.multi_asset import get_market_snapshot


@dataclass
class HealthCheckResult:
    name: str
    status: str  # pass | warn | fail
    details: str


def _run_check(name: str, check_fn: Callable[[], str]) -> HealthCheckResult:
    try:
        details = check_fn()
        return HealthCheckResult(name=name, status="pass", details=details)
    except RuntimeError as exc:
        return HealthCheckResult(name=name, status="warn", details=str(exc))
    except Exception as exc:
        return HealthCheckResult(name=name, status="fail", details=str(exc))


def _check_llm_keys() -> str:
    providers = {
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", "").strip(),
        "GOOGLE_API_KEY": os.getenv("GOOGLE_API_KEY", "").strip(),
        "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", "").strip(),
        "XAI_API_KEY": os.getenv("XAI_API_KEY", "").strip(),
        "OPENROUTER_API_KEY": os.getenv("OPENROUTER_API_KEY", "").strip(),
    }
    configured = [key for key, val in providers.items() if val]
    if not configured:
        raise RuntimeError("No LLM provider API key found")
    return f"Configured: {', '.join(configured)}"


def _check_delta() -> str:
    text = get_delta_crypto_derivatives_snapshot("BTC-USD")
    if "Crypto Derivatives Snapshot (delta)" not in text:
        raise RuntimeError("Delta response format mismatch")
    return "Delta crypto derivatives reachable"


def _check_dhan() -> str:
    client_id = os.getenv("DHAN_CLIENT_ID", "").strip()
    access_token = os.getenv("DHAN_ACCESS_TOKEN", "").strip()
    if not client_id or not access_token:
        raise RuntimeError("Missing DHAN_CLIENT_ID or DHAN_ACCESS_TOKEN")

    expiry_note = ""
    try:
        payload_part = access_token.split(".")[1]
        pad = "=" * ((4 - len(payload_part) % 4) % 4)
        decoded = base64.urlsafe_b64decode(payload_part + pad).decode("utf-8")
        token_payload = json.loads(decoded)
        exp_ts = int(token_payload.get("exp", 0))
        if exp_ts:
            now_ts = int(datetime.now(timezone.utc).timestamp())
            if exp_ts < now_ts:
                raise RuntimeError(
                    "Dhan token appears expired based on JWT exp claim"
                )
            expiry_ts = datetime.fromtimestamp(exp_ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
            expiry_note = f" (token exp={expiry_ts})"
    except RuntimeError:
        raise
    except Exception:
        # Non-JWT or parse issues should not block direct API validation path.
        pass

    text = get_dhan_option_chain_snapshot("NIFTY", top_n=1)
    if "Option Chain Snapshot (dhan)" not in text:
        raise RuntimeError("Dhan option chain response format mismatch")
    return "Dhan Nifty option chain reachable" + expiry_note


def _check_forex_snapshot() -> str:
    trade_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    text = get_market_snapshot("EURUSD=X", "forex", trade_date, 30)
    if "Instrument Type: forex" not in text:
        raise RuntimeError("Forex snapshot format mismatch")
    return "Forex snapshot path ready"


def _check_nifty_spot_snapshot() -> str:
    trade_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    text = get_market_snapshot("NIFTY", "options", trade_date, 30)
    if "Data Symbol: ^NSEI" not in text:
        raise RuntimeError("Nifty alias normalization mismatch")
    return "Nifty spot snapshot normalization ready"


def run_production_healthcheck() -> List[HealthCheckResult]:
    checks = [
        ("LLM Provider Keys", _check_llm_keys),
        ("Delta Crypto Provider", _check_delta),
        ("Dhan Nifty Option Chain", _check_dhan),
        ("Forex Snapshot", _check_forex_snapshot),
        ("Nifty Spot Snapshot", _check_nifty_spot_snapshot),
    ]
    return [_run_check(name, fn) for name, fn in checks]
