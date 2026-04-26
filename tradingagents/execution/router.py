from __future__ import annotations
"""
Module: router.py
Part of the execution subsystem.

This module contains logic for the execution operations as part of the broader TradingAgents framework.
"""

import os
import json
from pathlib import Path
from typing import Dict, Any

from .base import BrokerBase
from .delta_broker import DeltaBroker
from .dhan_broker import DhanBroker
from .forex_broker import MT5ForexBroker
from .redis_broker import RedisPublisherBroker

# Default routing layout if no config file is found
DEFAULT_ROUTING = {
    "default": "delta",
    "by_instrument": {
        "forex": "mt5_forex",
        "options": "dhan",
        "crypto": "delta"
    },
    "by_symbol_substring": {
        "XAUUSD": "mt5_forex",
        "EURUSD": "mt5_forex",
        "NIFTY": "dhan",
        "^": "dhan",
        "BTC": "delta",
        "ETH": "delta"
    }
}

class ExecutionRouter:
    _instance = None
    
    def __init__(self):
        self.config = DEFAULT_ROUTING.copy()
        self._broker_cache: Dict[str, BrokerBase] = {}
        config_path = os.getenv("TRADINGAGENTS_ROUTING_CONFIG")
        if config_path and Path(config_path).exists():
            try:
                self.config = json.loads(Path(config_path).read_text())
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("Failed to load routing config: %s. Using default.", e)

    @classmethod
    def get_instance(cls) -> 'ExecutionRouter':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _instantiate_broker(self, name: str) -> BrokerBase:
        name = name.lower()
        if name in self._broker_cache:
            return self._broker_cache[name]

        if name == "dhan":
            broker = DhanBroker()
        elif name == "mt5_forex":
            broker = MT5ForexBroker()
        elif name == "delta":
            broker = DeltaBroker()
        elif name == "redis":
            broker = RedisPublisherBroker()
        else:
            broker = DeltaBroker()

        self._broker_cache[name] = broker
        return broker

    def shutdown_all(self) -> None:
        """Cleanup all cached broker instances."""
        self._broker_cache.clear()

    def resolve(self, broker: str, instrument_type: str, symbol: str) -> BrokerBase:
        broker_normalized = (broker or "auto").strip().lower()
        instrument_type = (instrument_type or "options").strip().lower()
        symbol_up = (symbol or "").upper()

        # Architecture Override
        if os.getenv("EXECUTION_MODE", "").lower() == "pubsub":
            return self._instantiate_broker("redis")

        # Explicit Request Override
        if broker_normalized in {"dhan", "delta", "mt5_forex", "redis"}:
            return self._instantiate_broker(broker_normalized)

        # 1. Check Substring Match
        for sub, target in self.config.get("by_symbol_substring", {}).items():
            if sub.upper() in symbol_up or symbol_up.startswith(sub.upper()):
                return self._instantiate_broker(target)

        # 2. Check Instrument Type Match
        target = self.config.get("by_instrument", {}).get(instrument_type)
        if target:
            return self._instantiate_broker(target)

        # 3. Fallback
        return self._instantiate_broker(self.config.get("default", "delta"))

def resolve_broker(broker: str, instrument_type: str, symbol: str) -> BrokerBase:
    return ExecutionRouter.get_instance().resolve(broker, instrument_type, symbol)

