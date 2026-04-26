from __future__ import annotations
"""
Module: journal.py
Part of the execution subsystem.

This module contains logic for the execution operations as part of the broader TradingAgents framework.
"""

import json
import os
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class DecisionJournal:
    file_path: Path
    rotate_daily: bool = True
    max_bytes: int = 5_000_000
    max_roll_files: int = 5

    @staticmethod
    def from_env() -> "DecisionJournal":
        path = Path(os.getenv("TRADINGAGENTS_DECISION_JOURNAL_FILE", "trade_decisions.jsonl"))
        rotate_daily = _env_bool("TRADINGAGENTS_DECISION_JOURNAL_ROTATE_DAILY", True)
        max_bytes = int(os.getenv("TRADINGAGENTS_DECISION_JOURNAL_MAX_BYTES", "5000000"))
        max_roll_files = int(os.getenv("TRADINGAGENTS_DECISION_JOURNAL_MAX_ROLL_FILES", "5"))
        return DecisionJournal(
            file_path=path,
            rotate_daily=rotate_daily,
            max_bytes=max(0, max_bytes),
            max_roll_files=max(1, max_roll_files),
        )

    def append(self, entry: Dict[str, Any]) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(entry)
        payload.setdefault("ts", int(time.time()))
        target = self._resolve_active_path(ts=int(payload["ts"]))
        self._ensure_size_rollover(target)
        with target.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=True) + "\n")

    def tail(self, limit: int = 50) -> List[Dict[str, Any]]:
        if limit <= 0:
            return []
        path = self._resolve_active_path()
        if not path.exists():
            return []

        lines = path.read_text(encoding="utf-8").splitlines()
        out: List[Dict[str, Any]] = []
        for raw in lines[-limit:]:
            row = raw.strip()
            if not row:
                continue
            try:
                value = json.loads(row)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                out.append(value)
        return out

    def summarize_day(self, day_utc: str | None = None, limit_scan_lines: int = 100_000) -> Dict[str, Any]:
        if day_utc is None:
            now_utc = datetime.now(timezone.utc)
            day_key = now_utc.strftime("%Y%m%d")
            day_iso = now_utc.strftime("%Y-%m-%d")
        else:
            parsed = datetime.strptime(day_utc, "%Y-%m-%d")
            day_key = parsed.strftime("%Y%m%d")
            day_iso = parsed.strftime("%Y-%m-%d")

        rows = self._read_rows_for_day(day_key=day_key, limit_scan_lines=limit_scan_lines)

        event_counts: Dict[str, int] = {}
        status_counts: Dict[str, int] = {}
        symbol_counts: Dict[str, int] = {}
        signal_counts: Dict[str, int] = {}
        mode_counts: Dict[str, int] = {}
        reject_counts: Dict[str, int] = {}

        executed = 0
        rejected = 0
        blocked = 0
        latest_wallet: Dict[str, Any] | None = None

        for item in rows:
            event = str(item.get("event", "unknown")).strip() or "unknown"
            status = str(item.get("status", "unknown")).strip() or "unknown"
            symbol = str(item.get("symbol", "")).strip()
            signal = str(item.get("signal", "")).strip().upper()
            mode = str(item.get("mode", "")).strip() or "unknown"

            event_counts[event] = event_counts.get(event, 0) + 1
            status_counts[status] = status_counts.get(status, 0) + 1
            mode_counts[mode] = mode_counts.get(mode, 0) + 1

            if symbol:
                symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1
            if signal:
                signal_counts[signal] = signal_counts.get(signal, 0) + 1

            if status in {"simulated_filled", "submitted"}:
                executed += 1
            elif status == "rejected":
                rejected += 1
            elif status.startswith("blocked"):
                blocked += 1

            reason = str(item.get("reason", "")).strip() or str(item.get("rejection_reason", "")).strip()
            if reason:
                reject_counts[reason] = reject_counts.get(reason, 0) + 1

            wallet = None
            if isinstance(item.get("wallet"), dict):
                wallet = item.get("wallet")
            elif isinstance(item.get("details"), dict):
                details = item["details"]
                if isinstance(details.get("paper_fill"), dict):
                    fill = details.get("paper_fill")
                    if isinstance(fill.get("wallet"), dict):
                        wallet = fill.get("wallet")
            if isinstance(wallet, dict):
                latest_wallet = wallet

        top_symbols = sorted(symbol_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
        top_rejections = sorted(reject_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]

        return {
            "date_utc": day_iso,
            "total_rows": len(rows),
            "executed_count": executed,
            "rejected_count": rejected,
            "blocked_count": blocked,
            "event_counts": event_counts,
            "status_counts": status_counts,
            "mode_counts": mode_counts,
            "signal_counts": signal_counts,
            "top_symbols": [{"symbol": s, "count": c} for s, c in top_symbols],
            "top_rejection_reasons": [{"reason": r, "count": c} for r, c in top_rejections],
            "latest_wallet": latest_wallet or {},
        }

    def count_today(
        self,
        statuses: set[str] | None = None,
        limit_scan_lines: int = 10_000,
    ) -> int:
        path = self._resolve_active_path()
        if not path.exists():
            return 0

        rows = path.read_text(encoding="utf-8").splitlines()
        rows = rows[-limit_scan_lines:]
        count = 0
        day_key = datetime.now(timezone.utc).strftime("%Y%m%d")
        for raw in rows:
            text = raw.strip()
            if not text:
                continue
            try:
                item = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue

            ts = item.get("ts")
            if ts is None:
                continue
            try:
                row_day = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y%m%d")
            except (TypeError, ValueError, OSError):
                continue
            if row_day != day_key:
                continue

            if statuses is not None:
                row_status = str(item.get("status", "")).strip()
                if row_status not in statuses:
                    continue
            count += 1
        return count

    def _resolve_active_path(self, ts: int | None = None) -> Path:
        if not self.rotate_daily:
            return self.file_path

        ts_val = int(ts) if ts is not None else int(time.time())
        day = datetime.fromtimestamp(ts_val, tz=timezone.utc).strftime("%Y%m%d")
        suffix = self.file_path.suffix or ".jsonl"
        stem = self.file_path.stem
        return self.file_path.with_name(f"{stem}_{day}{suffix}")

    def _resolve_day_base_path(self, day_key: str) -> Path:
        if not self.rotate_daily:
            return self.file_path
        suffix = self.file_path.suffix or ".jsonl"
        stem = self.file_path.stem
        return self.file_path.with_name(f"{stem}_{day_key}{suffix}")

    def _read_rows_for_day(self, day_key: str, limit_scan_lines: int) -> List[Dict[str, Any]]:
        base = self._resolve_day_base_path(day_key)
        candidate_paths = [base]
        for idx in range(1, self.max_roll_files + 1):
            candidate_paths.append(base.with_name(f"{base.stem}.{idx}{base.suffix}"))

        raw_lines: List[str] = []
        for path in candidate_paths:
            if not path.exists():
                continue
            raw_lines.extend(path.read_text(encoding="utf-8").splitlines())

        if limit_scan_lines > 0:
            raw_lines = raw_lines[-limit_scan_lines:]

        out: List[Dict[str, Any]] = []
        for raw in raw_lines:
            text = raw.strip()
            if not text:
                continue
            try:
                item = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue

            ts = item.get("ts")
            if ts is None:
                continue
            try:
                row_day = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y%m%d")
            except (TypeError, ValueError, OSError):
                continue
            if row_day == day_key:
                out.append(item)
        return out

    def _ensure_size_rollover(self, path: Path) -> None:
        if self.max_bytes <= 0 or not path.exists():
            return
        if path.stat().st_size < self.max_bytes:
            return

        for idx in range(self.max_roll_files - 1, 0, -1):
            prev = path.with_name(f"{path.stem}.{idx}{path.suffix}")
            nxt = path.with_name(f"{path.stem}.{idx + 1}{path.suffix}")
            if prev.exists():
                if idx + 1 > self.max_roll_files:
                    prev.unlink(missing_ok=True)
                else:
                    prev.replace(nxt)

        rolled = path.with_name(f"{path.stem}.1{path.suffix}")
        path.replace(rolled)


def safe_journal_append(entry: Dict[str, Any]) -> None:
    try:
        DecisionJournal.from_env().append(entry)
    except Exception:
        # Journal failures should never block trading flow.
        return


def read_journal_tail(limit: int = 50) -> List[Dict[str, Any]]:
    return DecisionJournal.from_env().tail(limit=limit)


def count_today_executions(statuses: set[str] | None = None) -> int:
    return DecisionJournal.from_env().count_today(statuses=statuses)


def get_daily_summary(day_utc: str | None = None, limit_scan_lines: int = 100_000) -> Dict[str, Any]:
    return DecisionJournal.from_env().summarize_day(day_utc=day_utc, limit_scan_lines=limit_scan_lines)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}

