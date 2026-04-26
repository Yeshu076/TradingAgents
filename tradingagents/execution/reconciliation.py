from __future__ import annotations

import os
import time
import logging
from typing import Dict, Any

from tradingagents.execution.position_manager import PositionManager
from tradingagents.execution.router import ExecutionRouter
from tradingagents.execution.base import BrokerBase

logger = logging.getLogger("reconciliation")

class StateReconciliationService:
    """
    Monitors the gap between what the internal SQLite PositionManager thinks is open,
    and what the live Broker API (Dhan, MT5, Delta) reports as actually open.
    """
    def __init__(self):
        self.wallet = PositionManager.from_env()
        self.router = ExecutionRouter.get_instance()
        self.mismatch_threshold_seconds = 10 

    def reconcile_all_brokers(self):
        """Fetch all paper WAL positions and compare against live routes."""
        internal_positions = self.wallet.get_positions()
        
        # Group internal intents by broker
        routes: Dict[str, list] = {}
        for pos in internal_positions:
            symbol = pos["symbol"]
            inst_type = pos.get("instrument_type", "options")
            broker = self.router.resolve("auto", inst_type, symbol)
            routes.setdefault(broker.name, []).append(pos)
            
        for broker_name, internal_list in routes.items():
            try:
                broker_impl = self.router._instantiate_broker(broker_name)
                # Ensure the broker supports list_positions
                if not hasattr(broker_impl, 'list_positions'):
                    continue
                    
                live_positions = broker_impl.list_positions()
                self._verify_alignment(broker_name, internal_list, live_positions)
            except Exception as e:
                logger.error(f"Failed to reconcile broker {broker_name}: {e}")

    def _normalize_live_position(self, pos: dict) -> dict:
        """
        Normalizes a broker-specific position dict to a standard schema:
        {"symbol": str, "quantity": float, "side": str, "avg_price": float}

        Delta uses: "size", "entry_price", "product_symbol"
        Dhan uses:  "tradedQuantity" / "netQty", "averageTradedPrice", "tradingSymbol"
        MT5 uses:   "volume", "price_open", "symbol"
        """
        symbol = (
            pos.get("symbol") or
            pos.get("trading_symbol") or
            pos.get("tradingSymbol") or
            pos.get("product_symbol") or
            ""
        ).upper()

        quantity = float(
            pos.get("quantity") or
            pos.get("tradedQuantity") or
            pos.get("netQty") or
            pos.get("size") or
            pos.get("volume") or
            0.0
        )

        avg_price = float(
            pos.get("avg_price") or
            pos.get("averageTradedPrice") or
            pos.get("entry_price") or
            pos.get("price_open") or
            0.0
        )

        side_raw = pos.get("side") or pos.get("transaction_type") or pos.get("type") or ""
        side = "LONG" if str(side_raw).upper() in {"BUY", "LONG", "b", "1"} else "SHORT"

        return {"symbol": symbol, "quantity": abs(quantity), "side": side, "avg_price": avg_price}

    def _verify_alignment(self, broker_name: str, internal: list, live: list):
        """Cross-check internal vs broker positions and fire SYSTEM_HALT if ghost positions exist."""
        # Normalize all live positions to a standard schema before comparison
        normalized_live = [self._normalize_live_position(p) for p in live]
        live_map = {
            p["symbol"]: p
            for p in normalized_live
            if p["symbol"] and p["quantity"] > 0
        }

        for ip in internal:
            sym = ip["symbol"].upper()
            qty = ip["quantity"]
            if qty == 0:
                continue

            if sym not in live_map:
                logger.critical(
                    f"🚨 RECONCILIATION FATAL: System thinks we hold {qty:.4f} of {sym} "
                    f"on {broker_name}, but live broker is FLAT!"
                )
                self._fire_circuit_breaker(f"GHOST_POSITION_{broker_name}_{sym}")
            else:
                live_qty = live_map[sym]["quantity"]
                # Warn on significant quantity mismatch (>5% divergence)
                if abs(qty - live_qty) / max(abs(qty), 1e-9) > 0.05:
                    logger.warning(
                        f"⚠️ RECONCILIATION PARTIAL MISMATCH: {sym} on {broker_name} — "
                        f"internal={qty:.4f}, live={live_qty:.4f}"
                    )

    def _fire_circuit_breaker(self, reason: str):
        try:
            import redis as _redis
            import json
            r = _redis.Redis(
                host=os.getenv("REDIS_HOST", "127.0.0.1"),
                port=int(os.getenv("REDIS_PORT", 6379)),
                socket_connect_timeout=3,
            )
            r.publish("SYSTEM_HALT", json.dumps({"reason": reason}))
        except Exception as e:
            logger.error(f"StateReconciliationService: Failed to publish circuit breaker ({reason}): {e}")

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    svc = StateReconciliationService()
    svc.reconcile_all_brokers()
