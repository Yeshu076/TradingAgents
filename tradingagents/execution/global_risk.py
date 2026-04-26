from __future__ import annotations
import json
import logging
import os
import threading
import time
from datetime import date
from typing import Dict, Optional

try:
    import redis
except ImportError:
    redis = None

logger = logging.getLogger(__name__)

_REDIS_KEY_DAILY_PNL = "grm:daily_pnl"
_REDIS_KEY_SYMBOL_EXPOSURE = "grm:symbol_exposure"
_REDIS_KEY_STRATEGY_PNL = "grm:strategy_daily_pnl"
_REDIS_KEY_DATE = "grm:state_date"
_REDIS_KEY_UNREALIZED_PNL = "grm:unrealized_pnl"   # F-01: MTM unrealized total
_REDIS_KEY_PORTFOLIO_HEAT = "grm:portfolio_heat"   # F-05: total open position heat


class GlobalRiskMonitor:
    """
    Monitors global drawdown, per-strategy limits, per-symbol exposure caps,
    and daily loss guards. Evaluates if a trade violates system-wide constraints.

    State is persisted to Redis so process restarts don't bypass daily loss limits.
    When Redis is unavailable, falls back gracefully to in-memory state (with a warning).
    """
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self.host = os.getenv("REDIS_HOST", "localhost")
        self.port = int(os.getenv("REDIS_PORT", 6379))
        self.max_global_drawdown_pct = float(os.getenv("MAX_GLOBAL_DRAWDOWN_PCT", "3.0"))

        # Risk Caps
        self.max_daily_loss_usd = float(os.getenv("MAX_DAILY_LOSS_USD", "1000.0"))
        self.max_symbol_exposure_usd = float(os.getenv("MAX_SYMBOL_EXPOSURE_USD", "5000.0"))
        self.max_strategy_drawdown_pct = float(os.getenv("MAX_STRATEGY_DD_PCT", "5.0"))
        # F-01: Unrealized PnL drawdown gate (0 = disabled)
        self.max_unrealized_drawdown_usd = float(os.getenv("MAX_UNREALIZED_DRAWDOWN_USD", "0"))
        # F-05: Portfolio heat cap (0 = disabled)
        self.max_portfolio_heat_usd = float(os.getenv("TRADINGAGENTS_MAX_PORTFOLIO_HEAT_USD", "0"))

        self.total_starting_balance = float(os.getenv("TRADINGAGENTS_STARTING_BALANCE", "0"))
        if self.total_starting_balance <= 0:
            try:
                from .position_manager import PositionManager
                self.total_starting_balance = PositionManager.from_env().get_cash()
            except Exception:
                self.total_starting_balance = float(os.getenv("TRADINGAGENTS_PAPER_INITIAL_BALANCE", "1000000"))
        self.current_balances: Dict[str, float] = {}

        # In-memory fallback state (used when Redis is unavailable)
        self._mem_daily_pnl: float = 0.0
        self._mem_symbol_exposure: Dict[str, float] = {}
        self._mem_strategy_daily_pnl: Dict[str, float] = {}
        self._mem_unrealized_pnl: float = 0.0   # F-01: total unrealized from MTM
        self._mem_portfolio_heat: float = 0.0   # F-05: total open position heat

        self.client: Optional[redis.Redis] = None
        if redis:
            try:
                self.client = redis.Redis(
                    host=self.host,
                    port=self.port,
                    db=0,
                    decode_responses=True,
                    socket_connect_timeout=3,
                )
                self.client.ping()
                self._load_state_from_redis()
            except Exception as e:
                logger.warning(
                    f"GlobalRiskMonitor: Redis unavailable ({e}). "
                    "Falling back to in-memory state — daily loss limits will reset on process restart."
                )
                self.client = None

    # ------------------------------------------------------------------
    # State Persistence
    # ------------------------------------------------------------------

    def _today(self) -> str:
        return date.today().isoformat()

    def _load_state_from_redis(self) -> None:
        """Load persisted risk state from Redis. Reset if it's a new trading day."""
        if not self.client:
            return
        try:
            stored_date = self.client.get(_REDIS_KEY_DATE)
            today = self._today()
            if stored_date != today:
                # New day — reset accumulators
                logger.info(f"GlobalRiskMonitor: New trading day ({today}). Resetting daily risk state.")
                self._flush_state_to_redis(reset=True)
                return

            raw_pnl = self.client.get(_REDIS_KEY_DAILY_PNL)
            self._mem_daily_pnl = float(raw_pnl) if raw_pnl else 0.0

            raw_exposure = self.client.get(_REDIS_KEY_SYMBOL_EXPOSURE)
            self._mem_symbol_exposure = json.loads(raw_exposure) if raw_exposure else {}

            raw_strat = self.client.get(_REDIS_KEY_STRATEGY_PNL)
            self._mem_strategy_daily_pnl = json.loads(raw_strat) if raw_strat else {}

            raw_unrealized = self.client.get(_REDIS_KEY_UNREALIZED_PNL)   # F-01
            self._mem_unrealized_pnl = float(raw_unrealized) if raw_unrealized else 0.0

            raw_heat = self.client.get(_REDIS_KEY_PORTFOLIO_HEAT)          # F-05
            self._mem_portfolio_heat = float(raw_heat) if raw_heat else 0.0

            logger.info(
                f"GlobalRiskMonitor: Loaded state from Redis — "
                f"daily_pnl={self._mem_daily_pnl:.2f}, "
                f"unrealized_pnl={self._mem_unrealized_pnl:.2f}, "
                f"symbols_tracked={len(self._mem_symbol_exposure)}"
            )
        except Exception as e:
            logger.warning(f"GlobalRiskMonitor: Failed to load state from Redis ({e}). Starting fresh.")

    def _flush_state_to_redis(self, reset: bool = False) -> None:
        """Persist current in-memory risk state to Redis."""
        if not self.client:
            return
        try:
            today = self._today()
            if reset:
                self._mem_daily_pnl = 0.0
                self._mem_symbol_exposure = {}
                self._mem_strategy_daily_pnl = {}

            pipe = self.client.pipeline()
            pipe.set(_REDIS_KEY_DATE, today)
            pipe.set(_REDIS_KEY_DAILY_PNL, str(self._mem_daily_pnl))
            pipe.set(_REDIS_KEY_SYMBOL_EXPOSURE, json.dumps(self._mem_symbol_exposure))
            pipe.set(_REDIS_KEY_STRATEGY_PNL, json.dumps(self._mem_strategy_daily_pnl))
            pipe.set(_REDIS_KEY_UNREALIZED_PNL, str(self._mem_unrealized_pnl))   # F-01
            pipe.set(_REDIS_KEY_PORTFOLIO_HEAT, str(self._mem_portfolio_heat))    # F-05
            # Expire at end of day (max 26h to cover DST edge cases)
            for key in [
                _REDIS_KEY_DATE, _REDIS_KEY_DAILY_PNL,
                _REDIS_KEY_SYMBOL_EXPOSURE, _REDIS_KEY_STRATEGY_PNL,
                _REDIS_KEY_UNREALIZED_PNL, _REDIS_KEY_PORTFOLIO_HEAT,
            ]:
                pipe.expire(key, 93600)
            pipe.execute()
        except Exception as e:
            logger.warning(f"GlobalRiskMonitor: Failed to persist state to Redis ({e}).")

    # ------------------------------------------------------------------
    # State Accessors (read from in-memory, flushed to Redis)
    # ------------------------------------------------------------------

    @property
    def daily_pnl(self) -> float:
        return self._mem_daily_pnl

    @property
    def symbol_exposure(self) -> Dict[str, float]:
        return self._mem_symbol_exposure

    @property
    def strategy_daily_pnl(self) -> Dict[str, float]:
        return self._mem_strategy_daily_pnl

    @property
    def unrealized_pnl(self) -> float:
        """F-01: Total unrealized PnL across all open positions (set by MarkToMarketService)."""
        return self._mem_unrealized_pnl

    @property
    def portfolio_heat(self) -> float:
        """F-05: Total dollar risk of all open positions (updated by execution engine)."""
        return self._mem_portfolio_heat

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> GlobalRiskMonitor:
        with cls._lock:
            if cls._instance is None:
                cls._instance = GlobalRiskMonitor()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Force recreation of the singleton (useful in tests)."""
        with cls._lock:
            cls._instance = None

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def evaluate_trade_intent(self, strategy_name: str, symbol: str, notional_value: float, **kwargs) -> bool:
        """
        Gatekeeper function. Returns False if the trade exceeds max authorized bounds.
        """
        # 1. Block globally if execution is blocked by KillSwitch
        if self.client:
            try:
                if self.client.get("EXECUTION_BLOCKED") == "1":
                    logger.error("\U0001f6d1 Trade rejected: SYSTEM IS IN SAFE MODE (EXECUTION_BLOCKED=1).")
                    return False
            except Exception as e:
                logger.warning(
                    f"GlobalRiskMonitor: Redis unavailable during evaluate_trade_intent ({e}). "
                    "Proceeding without kill-switch check."
                )

        # 2. Max Daily Loss Guard
        if self._mem_daily_pnl <= -self.max_daily_loss_usd:
            logger.error(f"\U0001f6d1 Trade rejected: Global Max Daily Loss (-{self.max_daily_loss_usd}) breached!")
            self._trigger_killswitch("MAX_DAILY_LOSS_BREACH")
            return False

        # 3. F-01: Unrealized Drawdown Guard
        if self.max_unrealized_drawdown_usd > 0:
            if self._mem_unrealized_pnl <= -self.max_unrealized_drawdown_usd:
                logger.error(
                    "\U0001f6d1 Trade rejected: Unrealized drawdown %.2f exceeds limit -%.2f.",
                    self._mem_unrealized_pnl, self.max_unrealized_drawdown_usd,
                )
                return False

        # 4. F-05: Portfolio Heat Cap — block if existing heat + proposed heat exceeds limit
        if self.max_portfolio_heat_usd > 0:
            proposed_heat = kwargs.get("proposed_heat", 0.0)
            projected_heat = self._mem_portfolio_heat + proposed_heat
            if projected_heat > self.max_portfolio_heat_usd:
                logger.warning(
                    "\u26a0\ufe0f Trade rejected: Portfolio heat %.2f + proposed %.2f = %.2f "
                    "exceeds cap %.2f.",
                    self._mem_portfolio_heat, proposed_heat, projected_heat,
                    self.max_portfolio_heat_usd,
                )
                return False

        # 5. Per-Symbol Exposure Cap
        current_symbol_exposure = self._mem_symbol_exposure.get(symbol, 0.0)
        projected_exposure = current_symbol_exposure + notional_value
        if projected_exposure > self.max_symbol_exposure_usd:
            logger.warning(
                f"\u26a0\ufe0f Trade rejected: Symbol {symbol} exposure {projected_exposure:.2f} "
                f"exceeds cap {self.max_symbol_exposure_usd:.2f}."
            )
            return False

        # 6. Per-Strategy Drawdown Guard
        strat_pnl = self._mem_strategy_daily_pnl.get(strategy_name, 0.0)
        allocated_capital = float(os.getenv(f"ALLOCATION_{strategy_name.upper()}", "10000.0"))
        strat_dd_pct = (strat_pnl / allocated_capital) * 100 if allocated_capital > 0 else 0
        if strat_dd_pct <= -self.max_strategy_drawdown_pct:
            logger.warning(
                f"\u26a0\ufe0f Trade rejected: Strategy {strategy_name} hit max drawdown ({strat_dd_pct:.2f}%)."
            )
            return False

        return True

    def report_trade_execution(self, strategy_name: str, symbol: str, notional_value: float) -> None:
        """Updates running exposure tallies after successful fill. Persists to Redis."""
        self._mem_symbol_exposure[symbol] = self._mem_symbol_exposure.get(symbol, 0.0) + notional_value
        self._flush_state_to_redis()

    def report_closed_pnl(self, strategy_name: str, symbol: str, notional_freed: float, realized_pnl: float) -> None:
        """Updates PnL and frees exposure capacity. Persists to Redis."""
        self._mem_symbol_exposure[symbol] = max(0.0, self._mem_symbol_exposure.get(symbol, 0.0) - notional_freed)
        self._mem_daily_pnl += realized_pnl
        self._mem_strategy_daily_pnl[strategy_name] = (
            self._mem_strategy_daily_pnl.get(strategy_name, 0.0) + realized_pnl
        )
        self._flush_state_to_redis()

    def update_unrealized_pnl(self, total_unrealized: float) -> None:
        """F-01: Called by MarkToMarketService with the sum of all position unrealized PnL."""
        self._mem_unrealized_pnl = total_unrealized
        self._flush_state_to_redis()
        logger.debug("GlobalRiskMonitor: unrealized_pnl updated to %.2f", total_unrealized)

    def update_portfolio_heat(self, total_heat: float) -> None:
        """F-05: Update the total portfolio heat from PositionManager.get_total_position_heat().

        Called by the execution engine after each successful fill (paper or live)
        so the heat accumulator reflects the current risk profile of all open
        positions. Persisted to Redis so restarts don't corrupt the gate.
        """
        self._mem_portfolio_heat = max(0.0, total_heat)
        self._flush_state_to_redis()
        logger.debug("GlobalRiskMonitor: portfolio_heat updated to %.2f", total_heat)

    def is_globally_safe(self) -> bool:
        """Returns False if the global risk state indicates trading should stop."""
        if self.client:
            try:
                if self.client.get("EXECUTION_BLOCKED") == "1":
                    return False
            except Exception:
                pass  # If Redis is down, don't block on that alone

        if self._mem_daily_pnl <= -self.max_daily_loss_usd:
            return False

        # F-01: Also halt if unrealized drawdown exceeds limit
        if self.max_unrealized_drawdown_usd > 0:
            if self._mem_unrealized_pnl <= -self.max_unrealized_drawdown_usd:
                logger.warning(
                    "is_globally_safe: unrealized drawdown %.2f exceeds limit -%.2f.",
                    self._mem_unrealized_pnl, self.max_unrealized_drawdown_usd,
                )
                return False

        return True

    def _trigger_killswitch(self, reason: str) -> None:
        if self.client:
            try:
                self.client.publish("SYSTEM_HALT", json.dumps({"reason": reason}))
            except Exception as e:
                logger.error(f"GlobalRiskMonitor: Failed to publish killswitch event: {e}")
