from __future__ import annotations
"""
Module: cycle_state.py
Part of the execution subsystem.

This module contains logic for the execution operations as part of the broader TradingAgents framework.
"""

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


@dataclass
class CycleStateStore:
    state_file: Path

    def load(self) -> Dict[str, Any]:
        if not self.state_file.exists():
            return {}
        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def save(self, payload: Dict[str, Any]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        data = dict(payload)
        data["updated_ts"] = int(time.time())
        data.setdefault("version", 1)
        self.state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def reset(self) -> None:
        if self.state_file.exists():
            self.state_file.unlink()

    def resume_start_cycle(self, context_key: str, total_cycles: int) -> int:
        state = self.load()
        if not state:
            return 1
        if state.get("context_key") != context_key:
            return 1
        last_completed = _to_int(state.get("last_completed_cycle"), default=0)
        start = max(1, last_completed + 1)
        if start > total_cycles:
            return total_cycles + 1
        return start

    def mark_cycle_success(
        self,
        context_key: str,
        cycle_idx: int,
        status: str,
        last_fingerprint: str,
        total_cycles: int,
    ) -> None:
        self.save(
            {
                "context_key": context_key,
                "last_completed_cycle": int(cycle_idx),
                "last_status": status,
                "last_fingerprint": last_fingerprint,
                "total_cycles": int(total_cycles),
                "last_error": "",
            }
        )

    def mark_cycle_failure(
        self,
        context_key: str,
        cycle_idx: int,
        error: str,
        total_cycles: int,
    ) -> None:
        state = self.load()
        self.save(
            {
                "context_key": context_key,
                "last_completed_cycle": _to_int(state.get("last_completed_cycle"), default=max(0, cycle_idx - 1)),
                "last_status": "failed",
                "last_fingerprint": str(state.get("last_fingerprint", "")),
                "total_cycles": int(total_cycles),
                "last_error": error,
            }
        )

    def mark_interrupted(
        self,
        context_key: str,
        last_completed_cycle: int,
        total_cycles: int,
        reason: str,
    ) -> None:
        state = self.load()
        self.save(
            {
                "context_key": context_key,
                "last_completed_cycle": int(last_completed_cycle),
                "last_status": "interrupted",
                "last_fingerprint": str(state.get("last_fingerprint", "")),
                "total_cycles": int(total_cycles),
                "last_error": reason,
            }
        )


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default

