from __future__ import annotations
"""
Module: policy.py
Part of the execution subsystem.

This module contains logic for the execution operations as part of the broader TradingAgents framework.
"""

import os
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from .journal import count_today_executions
from .position_manager import PositionManager


@dataclass
class ExecutionPolicy:
    max_order_quantity: float = 25.0
    max_order_notional: float = 500000.0
    enforce_market_hours: bool = True
    allow_live_trading: bool = False
    max_daily_trades: int = 50
    allowed_instruments: tuple[str, ...] = ("options", "spot", "crypto", "forex", "equity")
    max_daily_loss_pct: float = 2.0
    max_daily_loss_currency: float = 500.0
    max_open_positions: int = 20

    @staticmethod
    def from_env() -> "ExecutionPolicy":
        allowed = os.getenv(
            "TRADINGAGENTS_ALLOWED_INSTRUMENTS",
            "options,spot,crypto,forex,equity",
        )
        allowed_tuple = tuple(part.strip().lower() for part in allowed.split(",") if part.strip())
        return ExecutionPolicy(
            max_order_quantity=float(os.getenv("TRADINGAGENTS_MAX_ORDER_QTY", "25")),
            max_order_notional=float(os.getenv("TRADINGAGENTS_MAX_ORDER_NOTIONAL", "500000")),
            enforce_market_hours=_env_bool("TRADINGAGENTS_ENFORCE_MARKET_HOURS", True),
            allow_live_trading=_env_bool("TRADINGAGENTS_ALLOW_LIVE", False),
            max_daily_trades=int(os.getenv("TRADINGAGENTS_MAX_DAILY_TRADES", "50")),
            allowed_instruments=allowed_tuple or ("options", "spot", "crypto", "forex", "equity"),
            max_daily_loss_pct=float(os.getenv("TRADINGAGENTS_MAX_DAILY_LOSS_PCT", "2.0")),
            max_daily_loss_currency=float(os.getenv("TRADINGAGENTS_MAX_DAILY_LOSS_CURRENCY", "500.0")),
            max_open_positions=int(os.getenv("TRADINGAGENTS_MAX_OPEN_POSITIONS", "20")),
        )

    def validate_order(
        self,
        *,
        symbol: str,
        instrument_type: str,
        quantity: float,
        is_live: bool,
        broker_name: str,
        suggested_entry: float | None,
        agent_pnl: float | None = None,
        agent_start_equity: float | None = None,
    ) -> None:
        instrument = (instrument_type or "").strip().lower()
        if instrument not in self.allowed_instruments:
            raise RuntimeError(
                f"Instrument '{instrument}' blocked by policy. Allowed: {', '.join(self.allowed_instruments)}"
            )

        if quantity <= 0:
            raise RuntimeError("Quantity must be greater than zero")
        if quantity > self.max_order_quantity:
            raise RuntimeError(
                f"Quantity {quantity} exceeds policy max {self.max_order_quantity}."
            )

        if suggested_entry is not None:
            estimated_notional = abs(float(quantity) * float(suggested_entry))
            if estimated_notional > self.max_order_notional:
                raise RuntimeError(
                    f"Estimated notional {estimated_notional:.2f} exceeds policy max {self.max_order_notional:.2f}."
                )

        if is_live and not self.allow_live_trading:
            raise RuntimeError(
                "Live trading is blocked by policy. Set TRADINGAGENTS_ALLOW_LIVE=true to enable."
            )

        today_count = count_today_executions(statuses={"simulated_filled", "submitted"})
        if self.max_daily_trades > 0 and today_count >= self.max_daily_trades:
            raise RuntimeError(
                f"Daily trade limit reached ({today_count}/{self.max_daily_trades})."
            )
        if agent_pnl is not None and agent_pnl < 0:
            if abs(agent_pnl) >= self.max_daily_loss_currency:
                raise RuntimeError(
                    f"Agent daily loss limit breached. Loss: {abs(agent_pnl):.2f}, Limit: {self.max_daily_loss_currency:.2f}"
                )
            if agent_start_equity is not None and agent_start_equity > 0:
                loss_pct = (abs(agent_pnl) / agent_start_equity) * 100.0
                if loss_pct >= self.max_daily_loss_pct:
                    raise RuntimeError(
                        f"Agent daily loss percentage breached. Loss: {loss_pct:.2f}%, Limit: {self.max_daily_loss_pct:.2f}%"
                    )
        if is_live and self.enforce_market_hours and self._requires_nse_hours(symbol, instrument, broker_name):
            self._validate_nse_market_window()

        # GAP-20: Max open positions guard
        if self.max_open_positions > 0:
            try:
                pm = PositionManager.from_env()
                open_count = len(pm.get_positions())
                if open_count >= self.max_open_positions:
                    raise RuntimeError(
                        f"Max open positions reached ({open_count}/{self.max_open_positions}). "
                        f"Close existing positions before opening new ones."
                    )
            except RuntimeError:
                raise
            except Exception:
                pass  # Don't block trade if position count query fails

    @staticmethod
    def _requires_nse_hours(symbol: str, instrument_type: str, broker_name: str) -> bool:
        symbol_up = (symbol or "").upper()
        if broker_name == "dhan":
            return True
        if instrument_type == "options" and "NIFTY" in symbol_up:
            return True
        return False

    @staticmethod
    def _validate_nse_market_window() -> None:
        ist = ZoneInfo("Asia/Kolkata")
        now = datetime.now(ist)
        if now.weekday() >= 5:
            raise RuntimeError("NSE market-hours policy: weekend trading is blocked.")

        market_open = time(9, 15)
        market_close = time(15, 30)
        if not (market_open <= now.time() <= market_close):
            raise RuntimeError("NSE market-hours policy: allowed only between 09:15 and 15:30 IST.")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}

