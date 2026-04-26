from __future__ import annotations
"""
Module: shutdown_handler.py
Part of the core subsystem.

This module contains logic for the core operations as part of the broader TradingAgents framework.
"""

import signal
import threading
from types import FrameType
from typing import Callable, Dict


class GracefulShutdownManager:
    def __init__(self) -> None:
        self._requested = threading.Event()
        self._reason = ""
        self._prev_handlers: Dict[int, Callable] = {}

    @property
    def shutdown_requested(self) -> bool:
        return self._requested.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    def request_shutdown(self, reason: str = "manual") -> None:
        self._reason = reason
        self._requested.set()

    def setup_signal_handlers(self) -> None:
        def _handler(signum: int, _frame: FrameType | None) -> None:
            self.request_shutdown(reason=f"signal_{signum}")

        for sig_name in ("SIGINT", "SIGTERM"):
            if not hasattr(signal, sig_name):
                continue
            sig = getattr(signal, sig_name)
            self._prev_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, _handler)

    def restore_signal_handlers(self) -> None:
        for sig, handler in self._prev_handlers.items():
            signal.signal(sig, handler)
        self._prev_handlers.clear()
