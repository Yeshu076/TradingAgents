"""
Module: position_sizer.py
Part of the execution subsystem.

F-02: Dynamic Position Sizing Engine.

Provides three sizing strategies that map account equity and trade parameters
to a concrete order quantity. This replaces the fixed-lot approach and
normalizes risk-per-trade across all instruments and brokers.

Sizing Modes
------------
fixed
    Uses the quantity already on the TradeIntent unchanged. This is the
    default / backward-compatible mode and produces no change in behaviour.

percent_equity  (recommended for most strategies)
    qty = (equity × risk_pct) / |entry - stop_loss|

    Sizes each trade so that hitting the stop loss costs exactly
    ``risk_pct`` × equity. Requires ``suggested_entry`` and
    ``suggested_stop_loss`` on the TradeIntent. Falls back to ``fixed``
    when either is missing or the stop distance is zero.

volatility_adjusted  (recommended for crypto / FX)
    qty = (equity × risk_pct) / (atr × atr_multiplier)

    Sizes inversely proportional to recent volatility (ATR).  Requires an
    ATR value supplied by the caller.  Falls back to ``percent_equity``
    when an entry/stop pair is available, or ``fixed`` as a final fallback.

Safety guarantees
-----------------
- Quantity is always rounded DOWN to the instrument's tick/lot step.
- Result is clamped to [min_quantity, max_quantity].
- Zero equity → returns min_quantity (never returns 0).
- All sizing functions are pure (no I/O, no side effects) — easy to test.

Environment variables
---------------------
TRADINGAGENTS_SIZING_MODE          fixed | percent_equity | volatility_adjusted
TRADINGAGENTS_RISK_PER_TRADE_PCT   float, default 1.0  (% of equity per trade)
TRADINGAGENTS_ATR_MULTIPLIER       float, default 2.0  (for vol-adjusted mode)
TRADINGAGENTS_MIN_QUANTITY         float, default 0.01
TRADINGAGENTS_MAX_QUANTITY         float, default TRADINGAGENTS_MAX_ORDER_QTY (25)
TRADINGAGENTS_QUANTITY_STEP        float, default 0.01 (lot-step rounding)
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sizing modes
# ---------------------------------------------------------------------------

class SizingMode(str, Enum):
    FIXED = "fixed"
    PERCENT_EQUITY = "percent_equity"
    VOLATILITY_ADJUSTED = "volatility_adjusted"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class SizerConfig:
    """All parameters that control position sizing. Config-driven, no hidden constants."""

    mode: SizingMode = SizingMode.FIXED
    risk_per_trade_pct: float = 1.0       # % of equity to risk per trade
    atr_multiplier: float = 2.0           # multiplier applied to ATR in vol-adj mode
    min_quantity: float = 0.01            # absolute floor
    max_quantity: float = 25.0            # absolute ceiling (matches policy default)
    quantity_step: float = 0.01           # lot-step for rounding

    @staticmethod
    def from_env() -> "SizerConfig":
        mode_raw = os.environ.get("TRADINGAGENTS_SIZING_MODE", "fixed").strip().lower()
        try:
            mode = SizingMode(mode_raw)
        except ValueError:
            logger.warning(
                "SizerConfig: unknown TRADINGAGENTS_SIZING_MODE=%r, defaulting to 'fixed'.",
                mode_raw,
            )
            mode = SizingMode.FIXED

        return SizerConfig(
            mode=mode,
            risk_per_trade_pct=float(os.environ.get("TRADINGAGENTS_RISK_PER_TRADE_PCT", "1.0")),
            atr_multiplier=float(os.environ.get("TRADINGAGENTS_ATR_MULTIPLIER", "2.0")),
            min_quantity=float(os.environ.get("TRADINGAGENTS_MIN_QUANTITY", "0.01")),
            max_quantity=float(os.environ.get("TRADINGAGENTS_MAX_QUANTITY",
                                              os.environ.get("TRADINGAGENTS_MAX_ORDER_QTY", "25.0"))),
            quantity_step=float(os.environ.get("TRADINGAGENTS_QUANTITY_STEP", "0.01")),
        )


# ---------------------------------------------------------------------------
# Pure sizing functions
# ---------------------------------------------------------------------------

def _floor_to_step(value: float, step: float) -> float:
    """Round down value to the nearest multiple of step."""
    if step <= 0:
        return value
    return math.floor(value / step) * step


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def size_fixed(
    raw_quantity: float,
    config: SizerConfig,
) -> float:
    """Return the raw quantity, clamped and stepped. No equity logic applied."""
    stepped = _floor_to_step(raw_quantity, config.quantity_step)
    return _clamp(stepped, config.min_quantity, config.max_quantity)


def size_percent_equity(
    equity: float,
    entry: float,
    stop_loss: float,
    config: SizerConfig,
    fallback_quantity: float = 1.0,
) -> float:
    """
    qty = (equity × risk_pct/100) / |entry - stop_loss|

    Args:
        equity:            Current portfolio equity (cash + open market value).
        entry:             Intended entry price.
        stop_loss:         Stop loss price. Must differ from entry.
        config:            SizerConfig with risk_per_trade_pct, min/max/step.
        fallback_quantity: Used when stop distance is zero or inputs are invalid.

    Returns:
        Sized quantity, floored to step and clamped to [min, max].
    """
    if equity <= 0:
        logger.warning("size_percent_equity: equity=%.2f <= 0, returning min_quantity.", equity)
        return config.min_quantity

    stop_distance = abs(entry - stop_loss)
    if stop_distance < 1e-9:
        logger.warning(
            "size_percent_equity: stop distance ~0 (entry=%.4f, stop=%.4f). "
            "Falling back to fixed quantity=%.4f.",
            entry, stop_loss, fallback_quantity,
        )
        return _clamp(
            _floor_to_step(fallback_quantity, config.quantity_step),
            config.min_quantity,
            config.max_quantity,
        )

    risk_amount = equity * (config.risk_per_trade_pct / 100.0)
    raw_qty = risk_amount / stop_distance
    stepped = _floor_to_step(raw_qty, config.quantity_step)
    clamped = _clamp(stepped, config.min_quantity, config.max_quantity)

    logger.debug(
        "size_percent_equity: equity=%.2f risk=%.2f stop_dist=%.4f "
        "raw_qty=%.4f → sized=%.4f",
        equity, risk_amount, stop_distance, raw_qty, clamped,
    )
    return clamped


def size_volatility_adjusted(
    equity: float,
    atr: float,
    config: SizerConfig,
    entry: Optional[float] = None,
    stop_loss: Optional[float] = None,
    fallback_quantity: float = 1.0,
) -> float:
    """
    qty = (equity × risk_pct/100) / (atr × atr_multiplier)

    Falls back to size_percent_equity when ATR is unavailable/zero but entry
    and stop_loss are provided. Falls back to size_fixed otherwise.

    Args:
        equity:            Current portfolio equity.
        atr:               Average True Range (same unit as price). 0 = unavailable.
        config:            SizerConfig with risk parameters.
        entry:             Optional entry price (used for fallback only).
        stop_loss:         Optional stop loss (used for fallback only).
        fallback_quantity: Final fallback when all else fails.

    Returns:
        Sized quantity, floored to step and clamped to [min, max].
    """
    if equity <= 0:
        logger.warning("size_volatility_adjusted: equity=%.2f <= 0, returning min_quantity.", equity)
        return config.min_quantity

    if atr > 1e-9:
        risk_amount = equity * (config.risk_per_trade_pct / 100.0)
        raw_qty = risk_amount / (atr * config.atr_multiplier)
        stepped = _floor_to_step(raw_qty, config.quantity_step)
        clamped = _clamp(stepped, config.min_quantity, config.max_quantity)
        logger.debug(
            "size_volatility_adjusted: equity=%.2f risk=%.2f atr=%.4f mult=%.2f "
            "raw_qty=%.4f → sized=%.4f",
            equity, risk_amount, atr, config.atr_multiplier, raw_qty, clamped,
        )
        return clamped

    # ATR not available — try percent_equity as fallback
    if entry is not None and stop_loss is not None:
        logger.info(
            "size_volatility_adjusted: ATR=0/unavailable, falling back to percent_equity "
            "(entry=%.4f, stop=%.4f).",
            entry, stop_loss,
        )
        return size_percent_equity(equity, entry, stop_loss, config, fallback_quantity)

    # Final fallback
    logger.warning(
        "size_volatility_adjusted: ATR=0 and no entry/stop. Falling back to fixed=%.4f.",
        fallback_quantity,
    )
    return size_fixed(fallback_quantity, config)


# ---------------------------------------------------------------------------
# High-level entry point
# ---------------------------------------------------------------------------

def calculate_position_size(
    *,
    mode: SizingMode,
    equity: float,
    config: SizerConfig,
    entry: Optional[float] = None,
    stop_loss: Optional[float] = None,
    atr: Optional[float] = None,
    raw_quantity: float = 1.0,
) -> float:
    """
    Dispatch to the appropriate sizing function based on ``mode``.

    This is the single public entry point for the engine.

    Args:
        mode:          Sizing strategy to use.
        equity:        Current portfolio equity from PositionManager.get_portfolio_equity().
        config:        SizerConfig (typically from SizerConfig.from_env()).
        entry:         Suggested entry price (from TradeIntent).
        stop_loss:     Suggested stop loss price (from TradeIntent).
        atr:           Average True Range for the symbol (optional, caller-supplied).
        raw_quantity:  Original quantity from TradeIntent (used for fixed mode and fallbacks).

    Returns:
        Final order quantity ready for broker dispatch.
    """
    if mode == SizingMode.FIXED:
        return size_fixed(raw_quantity, config)

    if mode == SizingMode.PERCENT_EQUITY:
        if entry is not None and stop_loss is not None:
            return size_percent_equity(equity, entry, stop_loss, config, raw_quantity)
        logger.warning(
            "calculate_position_size: PERCENT_EQUITY mode requires entry+stop_loss. "
            "Falling back to fixed."
        )
        return size_fixed(raw_quantity, config)

    if mode == SizingMode.VOLATILITY_ADJUSTED:
        return size_volatility_adjusted(
            equity=equity,
            atr=atr or 0.0,
            config=config,
            entry=entry,
            stop_loss=stop_loss,
            fallback_quantity=raw_quantity,
        )

    # Defensive: unknown mode
    logger.error("calculate_position_size: unknown mode=%r. Falling back to fixed.", mode)
    return size_fixed(raw_quantity, config)
