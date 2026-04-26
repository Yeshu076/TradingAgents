from __future__ import annotations
"""
Module: bootstrap_env.py
Part of the ops subsystem.

This module contains logic for the ops operations as part of the broader TradingAgents framework.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


LLM_KEYS = [
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "OPENROUTER_API_KEY",
]

CORE_KEYS = [
    "DELTA_REST_BASE_URL",
    "DHAN_CLIENT_ID",
    "DHAN_ACCESS_TOKEN",
    "DHAN_NIFTY_SECURITY_ID",
    "DHAN_NIFTY_UNDERLYING_SEGMENT",
    "ALPHA_VANTAGE_API_KEY",
]


@dataclass
class EnvItem:
    key: str
    value: str
    source: str


@dataclass
class BootstrapResult:
    items: List[EnvItem]
    written_file: Optional[str]
    merged_values: Dict[str, str]


def _load_dotenv_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _write_dotenv_file(path: Path, values: Dict[str, str]) -> None:
    lines = []
    for key in sorted(values.keys()):
        lines.append(f"{key}={values[key]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _extract_dhan_from_config(path: Path) -> Dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    credentials = payload.get("credentials", {}) if isinstance(payload, dict) else {}

    client_id = str(credentials.get("client_id", "")).strip()
    access_token = str(credentials.get("access_token", "")).strip()

    updates: Dict[str, str] = {}
    if client_id:
        updates["DHAN_CLIENT_ID"] = client_id
    if access_token:
        updates["DHAN_ACCESS_TOKEN"] = access_token

    underlying = payload.get("underlying", {}) if isinstance(payload, dict) else {}
    sec_id = str(underlying.get("security_id", "")).strip()
    segment = str(underlying.get("exchange_segment", "")).strip()
    if sec_id:
        updates["DHAN_NIFTY_SECURITY_ID"] = sec_id
    if segment:
        updates["DHAN_NIFTY_UNDERLYING_SEGMENT"] = segment

    return updates


def bootstrap_environment(
    env_file: Path,
    sync_dhan_config: Optional[Path] = None,
    write_env: bool = False,
) -> BootstrapResult:
    file_values = _load_dotenv_file(env_file)

    merged = dict(file_values)
    for key in LLM_KEYS + CORE_KEYS:
        if os.getenv(key):
            merged[key] = os.getenv(key, "")

    sources: Dict[str, str] = {k: "env_file" for k in file_values.keys()}
    for key in LLM_KEYS + CORE_KEYS:
        if os.getenv(key):
            sources[key] = "process_env"

    if sync_dhan_config and sync_dhan_config.exists():
        dhan_updates = _extract_dhan_from_config(sync_dhan_config)
        for key, value in dhan_updates.items():
            if value:
                merged[key] = value
                sources[key] = f"dhan_config:{sync_dhan_config}"

    if not merged.get("DELTA_REST_BASE_URL"):
        merged["DELTA_REST_BASE_URL"] = "https://api.india.delta.exchange"
        sources["DELTA_REST_BASE_URL"] = "default"

    if not merged.get("DHAN_NIFTY_SECURITY_ID"):
        merged["DHAN_NIFTY_SECURITY_ID"] = "13"
        sources["DHAN_NIFTY_SECURITY_ID"] = "default"

    if not merged.get("DHAN_NIFTY_UNDERLYING_SEGMENT"):
        merged["DHAN_NIFTY_UNDERLYING_SEGMENT"] = "IDX_I"
        sources["DHAN_NIFTY_UNDERLYING_SEGMENT"] = "default"

    if write_env:
        _write_dotenv_file(env_file, merged)

    items: List[EnvItem] = []
    for key in LLM_KEYS + CORE_KEYS:
        value = merged.get(key, "")
        items.append(EnvItem(key=key, value=value, source=sources.get(key, "missing")))

    return BootstrapResult(
        items=items,
        written_file=str(env_file) if write_env else None,
        merged_values=merged,
    )
