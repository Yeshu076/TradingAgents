from __future__ import annotations
"""
Module: deduplication.py
Part of the execution subsystem.

This module contains logic for the execution operations as part of the broader TradingAgents framework.
"""

import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .journal import read_journal_tail
from .models import TradeIntent


@dataclass
class ExecutionIdempotencyManager:
    enabled: bool = True
    window_seconds: int = 3600
    scan_limit: int = 1000

    @staticmethod
    def from_env() -> "ExecutionIdempotencyManager":
        return ExecutionIdempotencyManager(
            enabled=_env_bool("TRADINGAGENTS_EXECUTION_IDEMPOTENCY_ENABLED", True),
            window_seconds=max(1, int(os.getenv("TRADINGAGENTS_EXECUTION_IDEMPOTENCY_WINDOW_SECONDS", "3600"))),
            scan_limit=max(10, int(os.getenv("TRADINGAGENTS_EXECUTION_IDEMPOTENCY_SCAN_LIMIT", "1000"))),
        )

    def build_execution_key(
        self,
        intent: TradeIntent,
        side: str,
        mode: str,
        broker_name: str,
    ) -> str:
        payload = {
            "symbol": intent.symbol.strip().upper(),
            "instrument_type": intent.instrument_type.strip().lower(),
            "signal": intent.signal.strip().upper(),
            "side": side.strip().upper(),
            "quantity": _normalize_float(intent.quantity),
            "entry": _normalize_float(intent.suggested_entry),
            "stop": _normalize_float(intent.suggested_stop_loss),
            "target": _normalize_float(intent.suggested_target),
            "mode": mode.strip().lower(),
            "broker": broker_name.strip().lower(),
        }
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]

    def find_recent_success(self, exec_key: str) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        now_ts = int(time.time())
        rows = read_journal_tail(limit=self.scan_limit)
        for row in reversed(rows):
            if not isinstance(row, dict):
                continue
            if str(row.get("event", "")).strip() != "trade":
                continue
            if str(row.get("exec_key", "")).strip() != exec_key:
                continue

            status = str(row.get("status", "")).strip()
            if status not in {"simulated_filled", "submitted"}:
                continue

            ts = _to_int(row.get("ts"))
            if ts is None:
                continue
            if 0 <= now_ts - ts <= self.window_seconds:
                return row
        return None


def _to_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return round(float(value), 8)
    except (TypeError, ValueError):
        return None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}

