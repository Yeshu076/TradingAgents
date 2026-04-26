"""
Module: logging_config.py
Part of the tradingagents package.

F-06: Structured JSON logging with correlation ID support.

Provides:
  - StructuredJsonFormatter: JSON log output for production
  - setup_logging(): Configure logging at daemon startup
  - CorrelationContext: Thread-local correlation ID management

Usage:
    from tradingagents.logging_config import setup_logging, CorrelationContext

    setup_logging()  # reads TRADINGAGENTS_LOG_FORMAT env var

    with CorrelationContext.set("cycle-abc-123"):
        logger.info("Processing trade")  # includes correlation_id in JSON output

Environment variables:
    TRADINGAGENTS_LOG_FORMAT   - "json" for structured JSON, "text" for human-readable (default: "text")
    TRADINGAGENTS_LOG_LEVEL    - Python log level name (default: "INFO")
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional


class _CorrelationStorage(threading.local):
    """Thread-local storage for the current correlation ID."""

    def __init__(self) -> None:
        super().__init__()
        self.correlation_id: Optional[str] = None


_storage = _CorrelationStorage()


class CorrelationContext:
    """Manages a correlation ID scoped to the current thread.

    Usage::

        with CorrelationContext.set("my-cycle-id"):
            logger.info("this log has correlation_id=my-cycle-id")
    """

    @staticmethod
    def get() -> Optional[str]:
        """Return the current correlation ID, or None."""
        return _storage.correlation_id

    @staticmethod
    def set(correlation_id: Optional[str] = None) -> "_CorrelationContextManager":
        """Set a correlation ID for the current scope (context manager).

        If no ID is provided, a UUID4 will be generated.
        """
        return _CorrelationContextManager(correlation_id or uuid.uuid4().hex[:12])

    @staticmethod
    def set_raw(correlation_id: str) -> None:
        """Directly set the correlation ID without a context manager."""
        _storage.correlation_id = correlation_id

    @staticmethod
    def clear() -> None:
        """Clear the correlation ID."""
        _storage.correlation_id = None


class _CorrelationContextManager:
    def __init__(self, correlation_id: str) -> None:
        self._new_id = correlation_id
        self._prev_id: Optional[str] = None

    def __enter__(self) -> str:
        self._prev_id = _storage.correlation_id
        _storage.correlation_id = self._new_id
        return self._new_id

    def __exit__(self, *_exc) -> None:
        _storage.correlation_id = self._prev_id


class StructuredJsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects.

    Output schema::

        {
            "timestamp": "2026-04-05T12:34:56.789Z",
            "level": "INFO",
            "logger": "tradingagents.execution.engine",
            "correlation_id": "abc123",
            "message": "Trade executed",
            "module": "engine",
            "func": "execute_trade",
            "line": 42,
            "extra": { ... }
        }
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "correlation_id": _storage.correlation_id,
            "message": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
        }

        # Include exception info if present
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = self.formatException(record.exc_info)

        # Collect extra fields (user-defined attributes beyond standard LogRecord)
        _standard_attrs = {
            "name", "msg", "args", "created", "relativeCreated",
            "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "filename", "module", "pathname", "thread", "threadName",
            "process", "processName", "levelname", "levelno", "message",
            "msecs", "taskName",
        }
        extra = {
            k: v for k, v in record.__dict__.items()
            if k not in _standard_attrs and not k.startswith("_")
        }
        if extra:
            payload["extra"] = extra

        return json.dumps(payload, default=str, ensure_ascii=False)


def setup_logging(
    log_format: Optional[str] = None,
    log_level: Optional[str] = None,
) -> None:
    """Configure root logging based on environment variables.

    Args:
        log_format: "json" or "text". Defaults to TRADINGAGENTS_LOG_FORMAT env var or "text".
        log_level: Python log level. Defaults to TRADINGAGENTS_LOG_LEVEL env var or "INFO".
    """
    fmt = (log_format or os.environ.get("TRADINGAGENTS_LOG_FORMAT", "text")).lower()
    level = (log_level or os.environ.get("TRADINGAGENTS_LOG_LEVEL", "INFO")).upper()

    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))

    # Remove existing handlers to avoid duplicates
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler()

    if fmt == "json":
        handler.setFormatter(StructuredJsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))

    root.addHandler(handler)
