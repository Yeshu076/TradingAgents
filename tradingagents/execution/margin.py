"""
Module: margin.py
Part of the execution subsystem.

MarginValidator — synchronous implementation.

Previously async (dead code in a sync codebase). Rewritten as a plain
synchronous class so it can be called from execute_trade() directly.

GAP-12 fix: All async def / await removed. Broker calls are assumed
synchronous (matching BrokerBase ABC). get_buying_power() and
get_latest_price() are optional capability checks — the validator
degrades gracefully when brokers don't implement them.
"""

import logging
import os
from typing import Any, Optional

from tradingagents.execution.models import TradeIntent

logger = logging.getLogger("tradingagents.execution.margin")

_ENV_MARGIN_ENABLED = "TRADINGAGENTS_MARGIN_CHECK_ENABLED"   # default true
_ENV_MARGIN_BUFFER  = "TRADINGAGENTS_MARGIN_BUFFER_PCT"       # default 10 (%)


class MarginValidationResult:
    """Result of a margin pre-check."""
    def __init__(self, approved: bool, reason: str, required: float = 0.0, available: float = 0.0):
        self.approved = approved
        self.reason = reason
        self.required_margin = required
        self.available_buying_power = available

    def __repr__(self) -> str:
        return (
            f"MarginValidationResult(approved={self.approved}, "
            f"required={self.required_margin:.2f}, available={self.available_buying_power:.2f}, "
            f"reason={self.reason!r})"
        )


class MarginValidator:
    """
    Pre-trade safety layer. Queries the broker for available buying power
    and ensures the intended execution quantity won't trigger an
    'Insufficient Funds' rejection.

    Synchronous — safe to call from execute_trade() without an event loop.

    Usage:
        validator = MarginValidator(chosen_broker)
        result = validator.validate(intent)
        if not result.approved:
            raise RuntimeError(result.reason)
    """

    def __init__(self, broker_client: Any):
        self.broker = broker_client
        self._enabled = (
            os.environ.get(_ENV_MARGIN_ENABLED, "true").strip().lower() != "false"
        )
        # Require this much headroom above the calculated margin requirement
        self._buffer_pct = float(os.environ.get(_ENV_MARGIN_BUFFER, "10")) / 100.0

    def validate(self, intent: TradeIntent) -> MarginValidationResult:
        """
        Synchronously validates that sufficient buying power exists for intent.

        Returns:
            MarginValidationResult with approved=True if safe to proceed.
            approved=True when margin checking is disabled or broker doesn't
            support get_buying_power() — optimistic fallback.
        """
        if not self._enabled:
            return MarginValidationResult(True, "margin_check_disabled")

        # Optimistic fallback if broker doesn't expose get_buying_power
        if not hasattr(self.broker, "get_buying_power"):
            logger.debug(
                "MarginValidator: broker %s has no get_buying_power() — skipping check.",
                getattr(self.broker, "name", "unknown"),
            )
            return MarginValidationResult(True, "broker_unsupported")

        try:
            available_bp = float(self.broker.get_buying_power())
        except Exception as e:
            logger.warning("MarginValidator: could not fetch buying power: %s — skipping.", e)
            return MarginValidationResult(True, "buying_power_fetch_failed")

        # Resolve estimated entry price
        estimated_price = intent.suggested_entry or self._fetch_current_price(intent.symbol)
        if not estimated_price:
            logger.warning(
                "MarginValidator: could not resolve price for %s — blocking as safety measure.",
                intent.symbol,
            )
            return MarginValidationResult(
                False,
                f"price_unavailable:{intent.symbol}",
                required=0.0,
                available=available_bp,
            )

        # Simple notional margin (no leverage); instrument-specific margin
        # requirements would be fetched from exchange margin API in a full implementation.
        raw_required = float(intent.quantity) * float(estimated_price)
        required_with_buffer = raw_required * (1.0 + self._buffer_pct)

        if required_with_buffer > available_bp:
            logger.error(
                "🛑 MarginValidator: REJECTED %s — needs %.2f (with %.0f%% buffer) but only %.2f available.",
                intent.symbol, required_with_buffer, self._buffer_pct * 100, available_bp,
            )
            return MarginValidationResult(
                False,
                f"insufficient_margin:{intent.symbol}",
                required=required_with_buffer,
                available=available_bp,
            )

        logger.debug(
            "MarginValidator: %s OK — required=%.2f, available=%.2f.",
            intent.symbol, required_with_buffer, available_bp,
        )
        return MarginValidationResult(
            True,
            "approved",
            required=required_with_buffer,
            available=available_bp,
        )

    def _fetch_current_price(self, symbol: str) -> Optional[float]:
        """Fetch current mid-price from broker using get_quote() or get_latest_price()."""
        try:
            if hasattr(self.broker, "get_quote"):
                quote = self.broker.get_quote(symbol)
                mid = (float(quote.get("bid", 0) or 0) + float(quote.get("ask", 0) or 0)) / 2
                return mid if mid > 0 else None
            if hasattr(self.broker, "get_latest_price"):
                val = self.broker.get_latest_price(symbol)
                return float(val) if val else None
        except Exception as e:
            logger.debug("MarginValidator: price fetch for %s failed: %s", symbol, e)
        return None
