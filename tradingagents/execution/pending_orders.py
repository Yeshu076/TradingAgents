from __future__ import annotations
"""
Module: pending_orders.py
Part of the execution subsystem.

F-07: Persistent store for live limit orders awaiting fill.

Tracks every limit order that has been submitted but not yet confirmed filled,
cancelled, or expired. The engine polls this store to enforce engine-managed
time-in-force (tif_seconds) and to trigger cancel + market-fallback logic.

Design principles:
- SQLite for zero-dependency persistence (same pattern as PositionManager)
- All writes are explicit; no background threads in this module
- Thread-safe via SQLite WAL mode
- Closed/terminal orders are retained for audit; use `cleanup_terminal` to prune
"""

import logging
import os
import sqlite3
import time
from typing import List, Optional

from .models import PendingOrder

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = os.path.join(
    os.path.expanduser("~"), ".tradingagents", "pending_orders.db"
)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS pending_orders (
    order_id        TEXT PRIMARY KEY,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    quantity        REAL NOT NULL,
    limit_price     REAL NOT NULL,
    instrument_type TEXT NOT NULL,
    broker_name     TEXT NOT NULL,
    placed_at       INTEGER NOT NULL,
    expires_at      INTEGER,
    status          TEXT NOT NULL DEFAULT 'pending',
    exec_key        TEXT NOT NULL DEFAULT ''
);
"""


class PendingOrderStore:
    """Persistent store for limit orders awaiting broker fill confirmation.

    Usage:
        store = PendingOrderStore.from_env()
        store.upsert(pending_order)
        orders = store.get_pending()
        store.mark_status(order_id, "filled")
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    @classmethod
    def from_env(cls) -> "PendingOrderStore":
        db_path = os.environ.get(
            "TRADINGAGENTS_PENDING_ORDERS_DB",
            _DEFAULT_DB_PATH,
        )
        return cls(db_path)

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(_CREATE_TABLE)
            conn.commit()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def upsert(self, order: PendingOrder) -> None:
        """Insert or replace a pending order record."""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO pending_orders
                  (order_id, symbol, side, quantity, limit_price,
                   instrument_type, broker_name, placed_at, expires_at,
                   status, exec_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order.order_id,
                    order.symbol,
                    order.side,
                    order.quantity,
                    order.limit_price,
                    order.instrument_type,
                    order.broker_name,
                    order.placed_at,
                    order.expires_at,
                    order.status,
                    order.exec_key,
                ),
            )
            conn.commit()
        logger.debug(
            "PendingOrderStore: upserted order_id=%s symbol=%s status=%s",
            order.order_id, order.symbol, order.status,
        )

    def mark_status(self, order_id: str, status: str) -> None:
        """Update the status of an order in the store."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE pending_orders SET status = ? WHERE order_id = ?",
                (status, order_id),
            )
            conn.commit()
        logger.debug("PendingOrderStore: order_id=%s → status=%s", order_id, status)

    def cleanup_terminal(self, older_than_seconds: int = 86400) -> int:
        """Remove terminal orders (filled/cancelled/expired/failed) older than N seconds.

        Returns the number of rows deleted.
        """
        cutoff = int(time.time()) - older_than_seconds
        terminal = ("filled", "cancelled", "expired", "failed")
        with self._conn() as conn:
            cur = conn.execute(
                f"DELETE FROM pending_orders WHERE status IN ({','.join('?'*len(terminal))}) AND placed_at < ?",
                (*terminal, cutoff),
            )
            conn.commit()
            deleted = cur.rowcount
        logger.info("PendingOrderStore: cleaned up %d terminal orders.", deleted)
        return deleted

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_pending(self) -> List[PendingOrder]:
        """Return all orders currently in 'pending' status."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM pending_orders WHERE status = 'pending'"
            ).fetchall()
        return [self._row_to_order(r) for r in rows]

    def get_expired(self, now: Optional[int] = None) -> List[PendingOrder]:
        """Return pending orders whose engine-side TIF has elapsed."""
        now = now if now is not None else int(time.time())
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM pending_orders
                WHERE status = 'pending'
                  AND expires_at IS NOT NULL
                  AND expires_at <= ?
                """,
                (now,),
            ).fetchall()
        return [self._row_to_order(r) for r in rows]

    def get_by_id(self, order_id: str) -> Optional[PendingOrder]:
        """Fetch a single order by order_id; returns None if not found."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM pending_orders WHERE order_id = ?", (order_id,)
            ).fetchone()
        return self._row_to_order(row) if row else None

    def count_pending(self) -> int:
        """Return the count of orders currently in pending status."""
        with self._conn() as conn:
            result = conn.execute(
                "SELECT COUNT(*) FROM pending_orders WHERE status = 'pending'"
            ).fetchone()
        return result[0] if result else 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_order(row: sqlite3.Row) -> PendingOrder:
        return PendingOrder(
            order_id=row["order_id"],
            symbol=row["symbol"],
            side=row["side"],
            quantity=float(row["quantity"]),
            limit_price=float(row["limit_price"]),
            instrument_type=row["instrument_type"],
            broker_name=row["broker_name"],
            placed_at=int(row["placed_at"]),
            expires_at=int(row["expires_at"]) if row["expires_at"] is not None else None,
            status=row["status"],
            exec_key=row["exec_key"] or "",
        )
