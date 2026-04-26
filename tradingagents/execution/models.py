from __future__ import annotations
"""
Module: models.py
Part of the execution subsystem.

This module contains logic for the execution operations as part of the broader TradingAgents framework.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# F-07: Order type enumerations
# ---------------------------------------------------------------------------

class OrderType(str, Enum):
    """Supported order types routed through the execution engine."""
    MARKET  = "market"
    LIMIT   = "limit"
    BRACKET = "bracket"


class TimeInForce(str, Enum):
    """Time-in-force semantics for limit and bracket orders."""
    DAY = "DAY"   # Cancel at end of trading session
    GTC = "GTC"   # Good-till-cancelled (persist across sessions)
    GTD = "GTD"   # Good-till-date (requires expiry timestamp)
    IOC = "IOC"   # Immediate-or-cancel: fill what is available, cancel rest
    FOK = "FOK"   # Fill-or-kill: all-or-nothing, cancel if not fully filled


# ---------------------------------------------------------------------------
# Core intent / result models
# ---------------------------------------------------------------------------

@dataclass
class TradeIntent:
    symbol: str
    instrument_type: str
    signal: str
    quantity: float
    suggested_entry: Optional[float] = None
    suggested_stop_loss: Optional[float] = None
    suggested_target: Optional[float] = None
    trailing_jump: float = 0.0
    confidence: float = 1.0          # Multi-agent consensus weight (0.0 to 1.0)
    agent_source: str = "unknown"    # Which agent generated this intent
    # F-02: populated by position sizer — None means sizing was not applied
    raw_quantity: Optional[float] = None       # original agent-requested quantity
    sized_quantity: Optional[float] = None     # risk-adjusted quantity (post-sizer)
    sizing_mode: Optional[str] = None          # which mode was used
    # F-07: limit order fields
    order_type: OrderType = OrderType.MARKET   # MARKET | LIMIT | BRACKET
    time_in_force: TimeInForce = TimeInForce.DAY  # DAY | GTC | GTD | IOC | FOK
    limit_price: Optional[float] = None        # Explicit limit price; falls back to suggested_entry
    tif_seconds: Optional[int] = None          # Engine-managed TIF: cancel after N seconds if unfilled

    @staticmethod
    def from_order_intent(payload: Dict[str, Any], quantity: float = 1.0) -> "TradeIntent":
        raw_ot = str(payload.get("order_type", "market")).strip().lower()
        try:
            ot = OrderType(raw_ot)
        except ValueError:
            ot = OrderType.MARKET

        raw_tif = str(payload.get("time_in_force", "DAY")).strip().upper()
        try:
            tif = TimeInForce(raw_tif)
        except ValueError:
            tif = TimeInForce.DAY

        tif_seconds_raw = payload.get("tif_seconds")
        tif_seconds = int(tif_seconds_raw) if tif_seconds_raw is not None else None

        return TradeIntent(
            symbol=str(payload.get("ticker", "")).strip(),
            instrument_type=str(payload.get("instrument_type", "options")).strip().lower(),
            signal=str(payload.get("signal", "HOLD")).strip().upper(),
            quantity=float(quantity),
            suggested_entry=_to_float(payload.get("suggested_entry")),
            suggested_stop_loss=_to_float(payload.get("suggested_stop_loss")),
            suggested_target=_to_float(payload.get("suggested_target")),
            confidence=_to_float(payload.get("confidence", 1.0)) or 1.0,
            agent_source=str(payload.get("agent_source", "unknown")),
            order_type=ot,
            time_in_force=tif,
            limit_price=_to_float(payload.get("limit_price")),
            tif_seconds=tif_seconds,
        )


@dataclass
class PendingOrder:
    """F-07: Represents a live limit order awaiting fill, stored in PendingOrderStore."""
    order_id: str
    symbol: str
    side: str
    quantity: float
    limit_price: float
    instrument_type: str
    broker_name: str
    placed_at: int                     # Unix epoch seconds
    expires_at: Optional[int] = None   # Unix epoch seconds; None = no engine-side expiry
    status: str = "pending"            # pending | filled | cancelled | expired | failed
    exec_key: str = ""


@dataclass
class ExecutionResult:
    broker: str
    mode: str
    action: str
    status: str
    symbol: str
    side: str
    quantity: float
    details: Dict[str, Any]


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
