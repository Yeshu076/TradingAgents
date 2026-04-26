"""
Module: __init__.py
Part of the execution subsystem.

This module contains logic for the execution operations as part of the broader TradingAgents framework.
"""
from .engine import (
    TradeIntent,
    ExecutionResult,
    execute_trade,
    list_positions,
    close_symbol_position,
    cancel_all_orders,
    get_paper_wallet_snapshot,
)
from .policy import ExecutionPolicy
from .risk_gate import DeterministicRiskGate
from .position_manager import PositionManager
from .deduplication import ExecutionIdempotencyManager
from .journal import DecisionJournal, read_journal_tail, count_today_executions, get_daily_summary
from .arbitrator import ExecutionArbitrator

__all__ = [
    "TradeIntent",
    "ExecutionResult",
    "execute_trade",
    "list_positions",
    "close_symbol_position",
    "cancel_all_orders",
    "get_paper_wallet_snapshot",
    "ExecutionPolicy",
    "DeterministicRiskGate",
    "PositionManager",
    "ExecutionIdempotencyManager",
    "DecisionJournal",
    "read_journal_tail",
    "count_today_executions",
    "get_daily_summary",
    "ExecutionArbitrator"
]

