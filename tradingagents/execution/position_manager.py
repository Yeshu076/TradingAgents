"""
Module: position_manager.py
Robust SQLite-backed position and order management.
Replaces the flat-file paper wallet to prevent concurrency bugs.
"""
import sqlite3
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, asdict

@dataclass
class SQLitePosition:
    symbol: str
    instrument_type: str
    quantity: float
    avg_price: float
    realized_pnl: float

class PositionManager:
    def __init__(self, db_path: Path, initial_balance: float = 1_000_000.0): 
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initial_balance = initial_balance
        self._init_db()

    @staticmethod
    def from_env() -> "PositionManager":
        db_file = Path(os.getenv("TRADINGAGENTS_SQLITE_STATE_FILE", "portfolio.db"))
        initial = float(os.getenv("TRADINGAGENTS_PAPER_INITIAL_BALANCE", "1000000"))
        return PositionManager(db_path=db_file, initial_balance=initial)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            isolation_level="IMMEDIATE",
            timeout=10.0
        )
        conn.row_factory = sqlite3.Row
        # Enable Write-Ahead Logging for concurrency
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS positions (
                    symbol TEXT PRIMARY KEY,
                    instrument_type TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    avg_price REAL NOT NULL,
                    realized_pnl REAL NOT NULL DEFAULT 0.0,
                    last_price REAL NOT NULL DEFAULT 0.0,
                    unrealized_pnl REAL NOT NULL DEFAULT 0.0,
                    mtm_updated_ts INTEGER NOT NULL DEFAULT 0,
                    stop_loss REAL NOT NULL DEFAULT 0.0
                )
            ''')
            # Non-destructive migrations for existing databases
            for col, definition in [
                ("last_price",      "REAL NOT NULL DEFAULT 0.0"),
                ("unrealized_pnl",  "REAL NOT NULL DEFAULT 0.0"),
                ("mtm_updated_ts",  "INTEGER NOT NULL DEFAULT 0"),
                ("stop_loss",       "REAL NOT NULL DEFAULT 0.0"),  # F-05
            ]:
                try:
                    conn.execute(f"ALTER TABLE positions ADD COLUMN {col} {definition}")
                except Exception:
                    pass  # Column already exists — safe to ignore
            conn.execute('''
                CREATE TABLE IF NOT EXISTS account (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    cash REAL NOT NULL
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    price REAL NOT NULL,
                    instrument_type TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                )
            ''')
            # Initialize account balance if missing
            cursor = conn.execute("SELECT cash FROM account WHERE id = 1")
            if not cursor.fetchone():
                conn.execute("INSERT INTO account (id, cash) VALUES (1, ?)", (self.initial_balance,))

    def get_cash(self) -> float:
        with self._get_conn() as conn:
            row = conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()
            return row["cash"] if row else self.initial_balance

    def get_positions(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT * FROM positions WHERE abs(quantity) > 1e-9")
            return [dict(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # F-01: Mark-to-Market API
    # ------------------------------------------------------------------

    def update_mark_to_market(
        self,
        symbol: str,
        last_price: float,
    ) -> None:
        """Update the stored last price and recompute unrealized PnL for a symbol.

        Called by MarkToMarketService after each successful broker quote.
        Thread-safe via SQLite WAL + IMMEDIATE isolation.
        """
        if last_price <= 0:
            return  # Reject bad quotes silently — retain stale price
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT quantity, avg_price FROM positions WHERE symbol = ?",
                (symbol,),
            ).fetchone()
            if row is None or abs(row["quantity"]) < 1e-9:
                return  # Position closed — nothing to mark
            qty = row["quantity"]
            avg_p = row["avg_price"]
            unrealized = qty * (last_price - avg_p)
            conn.execute(
                """
                UPDATE positions
                SET last_price = ?, unrealized_pnl = ?, mtm_updated_ts = ?
                WHERE symbol = ?
                """,
                (last_price, unrealized, int(time.time()), symbol),
            )

    def get_portfolio_equity(self) -> float:
        """Return total portfolio equity: cash + market value of all open positions.

        Uses stored last_price for each position; falls back to avg_price when
        last_price is 0 (not yet quoted).
        """
        cash = self.get_cash()
        positions = self.get_positions()
        market_value = sum(
            pos["quantity"] * (pos.get("last_price") or pos["avg_price"])
            for pos in positions
        )
        return cash + market_value

    def get_total_unrealized_pnl(self) -> float:
        """Return sum of unrealized PnL across all open positions."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT SUM(unrealized_pnl) FROM positions WHERE abs(quantity) > 1e-9"
            ).fetchone()
            return float(row[0] or 0.0)

    # ------------------------------------------------------------------
    # F-05: Portfolio Heat API
    # ------------------------------------------------------------------

    def get_position_heat(self, symbol: str) -> float:
        """Return the dollar risk attributed to a single open position.

        Heat = abs(quantity) × |avg_price - stop_loss|

        Falls back to abs(quantity × avg_price) × fallback_heat_pct when no
        stop loss is stored (controlled by TRADINGAGENTS_HEAT_FALLBACK_PCT).
        This prevents zero-heat positions from bypassing the portfolio heat cap.

        Returns 0.0 when the position is closed (quantity ≈ 0).
        """
        import os
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT quantity, avg_price, stop_loss FROM positions WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        if row is None or abs(row["quantity"]) < 1e-9:
            return 0.0
        qty = abs(row["quantity"])
        avg_p = row["avg_price"]
        stop = row["stop_loss"]
        if stop > 1e-9:
            return qty * abs(avg_p - stop)
        # Fallback: treat a configurable % of position notional as heat
        fallback_pct = float(os.getenv("TRADINGAGENTS_HEAT_FALLBACK_PCT", "2.0"))
        return qty * avg_p * (fallback_pct / 100.0)

    def get_total_position_heat(self) -> float:
        """Return the total dollar risk (heat) across ALL open positions.

        Calls get_position_heat() per symbol so the per-position fallback logic
        is applied consistently.

        This is the metric checked against MAX_PORTFOLIO_HEAT_USD before each
        new trade is admitted to the execution pipeline.
        """
        positions = self.get_positions()
        return sum(self.get_position_heat(pos["symbol"]) for pos in positions)

    def set_stop_loss(self, symbol: str, stop_loss: float) -> None:
        """Update the stored stop loss for an open position.

        Called by the execution engine when a TradeIntent carries a
        suggested_stop_loss. The stop is recorded so portfolio heat can be
        computed accurately without needing broker connectivity.
        """
        if stop_loss <= 0:
            return
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE positions SET stop_loss = ? WHERE symbol = ?",
                (stop_loss, symbol),
            )

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        instrument_type: str,
        metadata: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        qty = float(quantity)
        if qty <= 0:
            raise RuntimeError("Quantity must be > 0")

        execution_price = float(price) if price and float(price) > 0 else 1.0   
        signed_delta = qty if side.upper() == "BUY" else -qty

        with self._get_conn() as conn:
            # 1. Update Cash
            conn.execute("UPDATE account SET cash = cash - ? WHERE id = 1", (signed_delta * execution_price,))
            row = conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()
            new_cash = row["cash"]

            # 2. Add to orders log
            conn.execute('''
                INSERT INTO orders (ts, symbol, side, quantity, price, instrument_type, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                int(time.time()), symbol, side.upper(), qty, execution_price, instrument_type, json.dumps(metadata or {})
            ))

            # 3. Modify Position
            cursor = conn.execute("SELECT * FROM positions WHERE symbol = ?", (symbol,))
            pos_row = cursor.fetchone()
            
            if not pos_row:
                new_qty = signed_delta
                avg_p = execution_price
                realized = 0.0
                conn.execute('''
                    INSERT INTO positions (symbol, instrument_type, quantity, avg_price, realized_pnl)
                    VALUES (?, ?, ?, ?, ?)
                ''', (symbol, instrument_type, new_qty, avg_p, realized))
            else:
                old_qty = pos_row["quantity"]
                old_avg = pos_row["avg_price"]
                realized = pos_row["realized_pnl"]

                # Same logic as LocalPaperWallet
                if old_qty == 0 or ((old_qty > 0 and signed_delta > 0) or (old_qty < 0 and signed_delta < 0)):
                    new_qty = old_qty + signed_delta
                    total_abs = abs(old_qty) + abs(signed_delta)
                    new_avg = (((abs(old_qty) * old_avg) + (abs(signed_delta) * execution_price)) / total_abs) if total_abs > 0 else 0
                else:
                    close_qty = min(abs(old_qty), abs(signed_delta))
                    sign = 1.0 if old_qty > 0 else -1.0
                    realized += close_qty * (execution_price - old_avg) * sign
                    new_qty = old_qty + signed_delta
                    if new_qty == 0:
                        new_avg = 0.0
                    elif ((new_qty > 0 and signed_delta > 0) or (new_qty < 0 and signed_delta < 0)):
                        new_avg = execution_price
                    else:
                        new_avg = old_avg

                conn.execute('''
                    UPDATE positions
                    SET quantity = ?, avg_price = ?, realized_pnl = ?
                    WHERE symbol = ?
                ''', (new_qty, new_avg, realized, symbol))

            # 4. Read back mutated row
            final_pos = dict(conn.execute("SELECT * FROM positions WHERE symbol = ?", (symbol,)).fetchone())

        return {
            "order": {"symbol": symbol, "side": side.upper(), "quantity": qty, "price": execution_price, "metadata": metadata or {}},
            "wallet_cash": new_cash,
            "final_position": final_pos
        }

    def close_symbol(self, symbol: str, mark_price: float | None = None) -> Dict[str, Any]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT quantity, avg_price, instrument_type FROM positions WHERE symbol = ?", (symbol,)).fetchone()
            if not row or abs(row["quantity"]) < 1e-9:
                return {"status": "no_position", "symbol": symbol}
            
            qty = abs(row["quantity"])
            side = "SELL" if row["quantity"] > 0 else "BUY"
            px = mark_price if mark_price and mark_price > 0 else (row["avg_price"] if row["avg_price"] > 0 else 1.0)
            inst = row["instrument_type"]
            
        return self.place_order(symbol, side, qty, px, inst, metadata={"action": "close_symbol"})

    def reset(self):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM positions")
            conn.execute("DELETE FROM orders")
            conn.execute("UPDATE account SET cash = ? WHERE id = 1", (self.initial_balance,))

    def get_summary(self, mark_prices: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """Return a full portfolio snapshot.

        mark_prices (optional): override dict {symbol: last_price}. When not
        supplied, the stored ``last_price`` from MTM polling is used; falls back
        to ``avg_price`` if the position has never been quoted.
        """
        overrides = mark_prices or {}
        cash = self.get_cash()
        positions = self.get_positions()

        unrealized = 0.0
        realized = 0.0
        equity = cash

        for pos in positions:
            sym = pos["symbol"]
            qty = pos["quantity"]
            avg_p = pos["avg_price"]
            realized += pos["realized_pnl"]

            # Priority: caller override → stored MTM price → avg_price
            stored_last = pos.get("last_price") or 0.0
            px = float(
                overrides.get(sym)
                or (stored_last if stored_last > 0 else None)
                or (avg_p if avg_p > 0 else 0.0)
            )
            unrealized += qty * (px - avg_p)
            equity += qty * px

        with self._get_conn() as conn:
            orders_count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]

        return {
            "cash": cash,
            "equity": equity,
            "unrealized_pnl": unrealized,
            "open_positions": len(positions),
            "orders_count": orders_count,
            "realized_pnl_open_positions": realized,
            "state_file": str(self.db_path),
        }



