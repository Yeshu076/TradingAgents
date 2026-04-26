from __future__ import annotations
"""
Module: resilience.py
Part of the execution subsystem.

This module contains logic for the execution operations as part of the broader TradingAgents framework.
"""

import os
import random
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, TypeVar

import requests


T = TypeVar("T")


class RetryableHttpError(RuntimeError):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = int(status_code)


class CircuitOpenError(RuntimeError):
    pass


@dataclass
class ResilienceConfig:
    max_attempts: int = 3
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 2.0
    jitter_ratio: float = 0.15
    circuit_failure_threshold: int = 3
    circuit_reset_seconds: int = 60

    @staticmethod
    def from_env() -> "ResilienceConfig":
        return ResilienceConfig(
            max_attempts=max(1, int(os.getenv("TRADINGAGENTS_RETRY_MAX_ATTEMPTS", "3"))),
            base_delay_seconds=max(0.01, float(os.getenv("TRADINGAGENTS_RETRY_BASE_DELAY_SECONDS", "0.25"))),
            max_delay_seconds=max(0.05, float(os.getenv("TRADINGAGENTS_RETRY_MAX_DELAY_SECONDS", "2.0"))),
            jitter_ratio=max(0.0, min(1.0, float(os.getenv("TRADINGAGENTS_RETRY_JITTER_RATIO", "0.15")))),
            circuit_failure_threshold=max(1, int(os.getenv("TRADINGAGENTS_CIRCUIT_FAILURE_THRESHOLD", "3"))),
            circuit_reset_seconds=max(1, int(os.getenv("TRADINGAGENTS_CIRCUIT_RESET_SECONDS", "60"))),
        )


@dataclass
class _CircuitState:
    failures: int = 0
    open_until_ts: float = 0.0


_CIRCUITS: Dict[str, _CircuitState] = {}
_CIRCUITS_LOCK = threading.Lock()


def execute_with_resilience(
    operation: Callable[[], T],
    operation_name: str,
    config: ResilienceConfig | None = None,
) -> T:
    cfg = config or ResilienceConfig.from_env()

    if _is_circuit_open(operation_name):
        raise CircuitOpenError(
            f"Circuit open for {operation_name}. Try again after cooldown."
        )

    last_exc: Exception | None = None
    for attempt in range(1, cfg.max_attempts + 1):
        try:
            result = operation()
            _record_success(operation_name)
            return result
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            retryable = _is_retryable_exception(exc)
            if (not retryable) or attempt >= cfg.max_attempts:
                _record_failure(
                    operation_name,
                    threshold=cfg.circuit_failure_threshold,
                    reset_seconds=cfg.circuit_reset_seconds,
                )
                raise

            delay = _compute_backoff_seconds(cfg=cfg, attempt=attempt)
            time.sleep(delay)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"Operation failed without exception: {operation_name}")


def raise_for_http_status(response: requests.Response, provider_name: str) -> None:
    status = int(response.status_code)
    if status in {408, 409, 425, 429} or 500 <= status <= 599:
        raise RetryableHttpError(
            f"{provider_name} transient HTTP error {status}: {response.text}",
            status_code=status,
        )
    if status >= 400:
        raise RuntimeError(f"{provider_name} API error {status}: {response.text}")


def _is_retryable_exception(exc: Exception) -> bool:
    if isinstance(exc, CircuitOpenError):
        return False
    if isinstance(exc, RetryableHttpError):
        return True
    if isinstance(exc, requests.RequestException):
        return True
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, ConnectionError):
        return True
    return False


def _compute_backoff_seconds(cfg: ResilienceConfig, attempt: int) -> float:
    raw = min(cfg.max_delay_seconds, cfg.base_delay_seconds * (2 ** max(0, attempt - 1)))
    jitter_band = raw * cfg.jitter_ratio
    if jitter_band <= 0:
        return raw
    return max(0.01, raw + random.uniform(-jitter_band, jitter_band))


def _is_circuit_open(operation_name: str) -> bool:
    now = time.time()
    with _CIRCUITS_LOCK:
        state = _CIRCUITS.get(operation_name)
        if state is None:
            return False
        return state.open_until_ts > now


def _record_success(operation_name: str) -> None:
    with _CIRCUITS_LOCK:
        _CIRCUITS[operation_name] = _CircuitState(failures=0, open_until_ts=0.0)


def _record_failure(operation_name: str, threshold: int, reset_seconds: int) -> None:
    now = time.time()
    with _CIRCUITS_LOCK:
        state = _CIRCUITS.get(operation_name) or _CircuitState()
        state.failures += 1
        if state.failures >= threshold:
            state.open_until_ts = now + reset_seconds
            state.failures = 0
        _CIRCUITS[operation_name] = state

