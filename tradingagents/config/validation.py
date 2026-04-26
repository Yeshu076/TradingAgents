from __future__ import annotations
"""
Module: validation.py
Part of the config subsystem.

This module contains logic for the config operations as part of the broader TradingAgents framework.
"""

import os
from typing import List


_ALLOWED_INSTRUMENTS = {"options", "spot", "crypto", "forex", "equity"}


def validate_runtime_environment() -> None:
    errors: List[str] = []

    _require_float("TRADINGAGENTS_MAX_ORDER_QTY", 25.0, gt=0.0, errors=errors)
    _require_float("TRADINGAGENTS_MAX_ORDER_NOTIONAL", 500000.0, gt=0.0, errors=errors)
    _require_int("TRADINGAGENTS_MAX_DAILY_TRADES", 50, ge=1, errors=errors)

    _require_float("TRADINGAGENTS_RISK_MIN_CONFIDENCE", 0.40, ge=0.0, le=1.0, errors=errors)
    _require_float("TRADINGAGENTS_RISK_MIN_RR", 1.20, gt=0.0, errors=errors)
    _require_float("TRADINGAGENTS_RISK_MAX_POSITION_PCT", 0.15, ge=0.0, le=1.0, errors=errors)

    _require_int("TRADINGAGENTS_EXECUTION_IDEMPOTENCY_WINDOW_SECONDS", 3600, ge=1, errors=errors)
    _require_int("TRADINGAGENTS_EXECUTION_IDEMPOTENCY_SCAN_LIMIT", 1000, ge=10, errors=errors)

    _require_int("TRADINGAGENTS_RETRY_MAX_ATTEMPTS", 3, ge=1, errors=errors)
    _require_float("TRADINGAGENTS_RETRY_BASE_DELAY_SECONDS", 0.25, ge=0.01, errors=errors)
    _require_float("TRADINGAGENTS_RETRY_MAX_DELAY_SECONDS", 2.0, ge=0.05, errors=errors)
    _require_float("TRADINGAGENTS_RETRY_JITTER_RATIO", 0.15, ge=0.0, le=1.0, errors=errors)
    _require_int("TRADINGAGENTS_CIRCUIT_FAILURE_THRESHOLD", 3, ge=1, errors=errors)
    _require_int("TRADINGAGENTS_CIRCUIT_RESET_SECONDS", 60, ge=1, errors=errors)

    _require_int("TRADINGAGENTS_HTTP_TIMEOUT_SECONDS", 15, ge=1, errors=errors)

    instruments_raw = os.getenv("TRADINGAGENTS_ALLOWED_INSTRUMENTS", "options,spot,crypto,forex,equity")
    instruments = {part.strip().lower() for part in instruments_raw.split(",") if part.strip()}
    if not instruments:
        errors.append("TRADINGAGENTS_ALLOWED_INSTRUMENTS must not be empty")
    unknown = sorted(instruments - _ALLOWED_INSTRUMENTS)
    if unknown:
        errors.append(
            "TRADINGAGENTS_ALLOWED_INSTRUMENTS contains unsupported values: " + ", ".join(unknown)
        )

    if errors:
        raise RuntimeError("Runtime environment validation failed: " + " | ".join(errors))


def _require_int(name: str, default: int, *, ge: int | None = None, errors: List[str]) -> int | None:
    raw = os.getenv(name)
    value = default if raw is None else raw
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        errors.append(f"{name} must be an integer")
        return None
    if ge is not None and parsed < ge:
        errors.append(f"{name} must be >= {ge}")
    return parsed


def _require_float(
    name: str,
    default: float,
    *,
    ge: float | None = None,
    gt: float | None = None,
    le: float | None = None,
    lt: float | None = None,
    errors: List[str],
) -> float | None:
    raw = os.getenv(name)
    value = default if raw is None else raw
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        errors.append(f"{name} must be a float")
        return None
    if ge is not None and parsed < ge:
        errors.append(f"{name} must be >= {ge}")
    if gt is not None and parsed <= gt:
        errors.append(f"{name} must be > {gt}")
    if le is not None and parsed > le:
        errors.append(f"{name} must be <= {le}")
    if lt is not None and parsed >= lt:
        errors.append(f"{name} must be < {lt}")
    return parsed
