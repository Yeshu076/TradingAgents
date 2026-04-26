"""
Module: portfolio_monitor.py
Part of the strategy_lab subsystem.

This module contains logic for the strategy_lab operations as part of the broader TradingAgents framework.
"""
import json
import logging
import os
import shutil
from pathlib import Path

from tradingagents.ops.notifier import send_notification
from tradingagents.execution.engine import close_symbol_position
from tradingagents.execution.position_manager import PositionManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _load_orders_from_sqlite() -> list:
    """Load all orders from the SQLite PositionManager database."""
    pm = PositionManager.from_env()
    try:
        with pm._get_conn() as conn:
            cursor = conn.execute(
                "SELECT ts, symbol, side, quantity, price, instrument_type, metadata_json FROM orders ORDER BY ts"
            )
            rows = cursor.fetchall()
            orders = []
            for row in rows:
                metadata = {}
                try:
                    metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
                except (json.JSONDecodeError, TypeError):
                    pass
                orders.append({
                    "ts": row["ts"],
                    "symbol": row["symbol"],
                    "side": row["side"],
                    "quantity": row["quantity"],
                    "price": row["price"],
                    "instrument_type": row["instrument_type"],
                    "metadata": metadata,
                })
            return orders
    except Exception as e:
        logger.error(f"Failed to load orders from SQLite: {e}")
        return []


def reconcile_strategy_pnl(orders: list) -> tuple[dict, dict]:
    """Calculates approximate PnL and open positions for each strategy using the order log."""
    strategy_pnl = {}
    strategy_positions = {}
    strategy_inventory = {}

    for order in orders:
        metadata = order.get("metadata", {})
        strategy = metadata.get("strategy_name")
        if not strategy:
            continue

        qty = float(order["quantity"])
        price = float(order["price"])
        side = order["side"]
        symbol = order.get("symbol", "")

        if strategy not in strategy_positions:
            strategy_positions[strategy] = 0.0
            strategy_pnl[strategy] = 0.0
            strategy_inventory[strategy] = {}

        if symbol not in strategy_inventory[strategy]:
            strategy_inventory[strategy][symbol] = 0.0

        if side == "BUY":
            strategy_pnl[strategy] -= (qty * price)
            strategy_positions[strategy] += qty
            strategy_inventory[strategy][symbol] += qty
        else:  # SELL
            strategy_pnl[strategy] += (qty * price)
            strategy_positions[strategy] -= qty
            strategy_inventory[strategy][symbol] -= qty

    for strat in list(strategy_inventory.keys()):
        for sym in list(strategy_inventory[strat].keys()):
            if abs(strategy_inventory[strat][sym]) < 1e-5:
                del strategy_inventory[strat][sym]

    return strategy_pnl, strategy_inventory


def evaluate_and_demote_strategies(max_loss_amount: float = 200.0):
    """
    Checks paper wallet for strategy specific performance.
    Demotes strictly losing strategies from 'approved' to 'quarantined'.
    """
    try:
        orders = _load_orders_from_sqlite()
        if not orders:
            return

        strat_pnl, strat_inventory = reconcile_strategy_pnl(orders)

        approved_dir = Path("strategy_lab_results") / "approved_scripts"
        quarantine_dir = Path("strategy_lab_results") / "quarantined_scripts"
        quarantine_dir.mkdir(exist_ok=True, parents=True)

        for strategy, pnl in strat_pnl.items():
            if pnl < -max_loss_amount:
                logger.warning(f"Strategy {strategy} has exceeded max loss. PNL: {pnl}. Quarantining.")

                script_path = approved_dir / strategy
                if script_path.exists():
                    shutil.move(str(script_path), str(quarantine_dir / strategy))

                    open_symbols = list(strat_inventory.get(strategy, {}).keys())
                    for sym in open_symbols:
                        try:
                            logger.info(f"Liquidating orphaned position for {sym} from {strategy}")
                            close_symbol_position(symbol=sym, paper=True, broker="auto")
                        except Exception as close_err:
                            logger.error(f"Failed to close orphaned position {sym}: {close_err}")

                    msg = (
                        f"☠️ **Strategy Kill-Switch Activated** ☠️\n"
                        f"Strategy: {strategy}\n"
                        f"Current Net Cash: {pnl:.2f}\n"
                        f"Action: Moved to Quarantine/Liquidated."
                    )
                    send_notification(msg)

    except Exception as e:
        logger.error(f"Failed to run strategy monitor: {e}")
