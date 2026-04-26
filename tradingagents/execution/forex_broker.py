from __future__ import annotations
"""
Module: forex_broker.py
MetaTrader 5 broker integration for Forex (XAUUSD).

Implements BrokerBase ABC fully:
  - place_market_order()      — send a market order via MT5
  - list_positions()          — list all open MT5 positions
  - close_symbol_position()   — flatten a specific symbol position
  - cancel_all_orders()       — cancel all pending MT5 orders for a symbol
  - fetch_positions()         — alias for list_positions() (legacy)
  - fetch_order_status()      — check fill status from MT5 deal history
"""

from typing import Any, Dict, List, Optional
import logging
from .base import BrokerBase

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

logger = logging.getLogger(__name__)

class MT5ForexBroker(BrokerBase):
    name: str = "mt5_forex"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if mt5 is None:
            logger.warning("MetaTrader5 package is not installed. MT5 broker disabled.")
            self.initialized = False
            return
            
        if not mt5.initialize():
            logger.error(f"MT5 initialize() failed, error code: {mt5.last_error()}")
            self.initialized = False
        else:
            self.initialized = True

    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        instrument_type: str = "forex",
        **kwargs,
    ) -> Dict[str, Any]:
        if not self.initialized:
            raise RuntimeError("MT5 not initialized — cannot place order")

        # Ensure symbol is selected in Market Watch
        if not mt5.symbol_select(symbol, True):
            logger.error(f"Failed to select {symbol}")
            raise RuntimeError(f"MT5 symbol not found or could not be selected: {symbol}")

        order_type = mt5.ORDER_TYPE_BUY if side.upper() == "BUY" else mt5.ORDER_TYPE_SELL
        
        # Get latest tick for current price
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"MT5 failed to get price for {symbol}")
            
        price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(quantity),
            "type": order_type,
            "price": price,
            "deviation": kwargs.get("deviation", 20),
            "magic": kwargs.get("magic", 234000),
            "comment": "TradingAgents Auto",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        if "sl" in kwargs and kwargs["sl"]:
            request["sl"] = float(kwargs["sl"])
        if "tp" in kwargs and kwargs["tp"]:
            request["tp"] = float(kwargs["tp"])

        result = mt5.order_send(request)
        
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Order failed: {result.comment}")
            raise RuntimeError(f"MT5 order failed: {result.comment} (retcode={result.retcode})")

        return {
            "order_id": str(result.order),
            "status": "FILLED",
            "filled_quantity": result.volume,
            "average_price": result.price,
            "symbol": symbol,
            "broker": self.name
        }

    # ------------------------------------------------------------------
    # Required BrokerBase ABC implementations
    # ------------------------------------------------------------------

    def list_positions(self) -> List[Dict[str, Any]]:
        """Return all open MT5 positions as a list of dicts."""
        return self.fetch_positions()

    def close_symbol_position(
        self, symbol: str, instrument_type: str = "forex", **kwargs
    ) -> Dict[str, Any]:
        """
        Close (flatten) all open MT5 positions for the given symbol by
        sending a market order in the opposite direction.

        Returns the result dict from place_market_order(), or an error
        dict if no position is found or MT5 is not initialised.
        """
        if not self.initialized:
            return {"status": "FAILED", "error": "MT5 not initialized"}

        if mt5 is None:
            return {"status": "FAILED", "error": "MetaTrader5 package not installed"}

        positions = mt5.positions_get(symbol=symbol)
        if not positions:
            logger.info("close_symbol_position: no open position for %s", symbol)
            return {"status": "no_position", "symbol": symbol}

        results = []
        for pos in positions:
            close_side = "SELL" if pos.type == mt5.POSITION_TYPE_BUY else "BUY"
            logger.info(
                "Closing MT5 position: symbol=%s ticket=%d volume=%s side=%s",
                symbol, pos.ticket, pos.volume, close_side,
            )
            result = self.place_market_order(
                symbol=symbol,
                side=close_side,
                quantity=pos.volume,
                instrument_type=instrument_type,
                # Pass the ticket so MT5 can match the specific position.
                position=pos.ticket,
            )
            results.append(result)

        failed = [r for r in results if r.get("status") == "FAILED"]
        return {
            "symbol": symbol,
            "status": "FAILED" if failed else "OK",
            "closed_positions": len(results),
            "failures": len(failed),
            "results": results,
        }

    def cancel_all_orders(
        self, symbol: Optional[str] = None, **kwargs
    ) -> Dict[str, Any]:
        """
        Cancel all pending (non-filled) MT5 orders, optionally filtered
        by symbol.

        MT5 "pending orders" are limit/stop orders that have not yet been
        triggered. Market orders fill immediately and cannot be cancelled.

        Returns a summary dict with cancelled_count and failed_count.
        """
        if not self.initialized:
            return {
                "status": "FAILED",
                "error": "MT5 not initialized",
                "cancelled_count": 0,
                "failed_count": 0,
            }

        if mt5 is None:
            return {
                "status": "FAILED",
                "error": "MetaTrader5 package not installed",
                "cancelled_count": 0,
                "failed_count": 0,
            }

        # Fetch all pending orders (limit/stop, not yet triggered).
        if symbol:
            orders = mt5.orders_get(symbol=symbol)
        else:
            orders = mt5.orders_get()

        if orders is None or len(orders) == 0:
            logger.info(
                "cancel_all_orders: no pending orders found%s.",
                f" for {symbol}" if symbol else "",
            )
            return {
                "status": "OK",
                "cancelled_count": 0,
                "failed_count": 0,
                "message": "No pending orders to cancel.",
            }

        cancelled = 0
        failed = 0

        for order in orders:
            request = {
                "action": mt5.TRADE_ACTION_REMOVE,
                "order": order.ticket,
            }
            result = mt5.order_send(request)
            if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(
                    "Cancelled pending order ticket=%d symbol=%s",
                    order.ticket, order.symbol,
                )
                cancelled += 1
            else:
                retcode = result.retcode if result else -1
                logger.error(
                    "Failed to cancel pending order ticket=%d symbol=%s retcode=%d",
                    order.ticket, order.symbol, retcode,
                )
                failed += 1

        return {
            "status": "OK" if failed == 0 else "PARTIAL",
            "cancelled_count": cancelled,
            "failed_count": failed,
        }

    # ------------------------------------------------------------------
    # Legacy / supplementary helpers
    # ------------------------------------------------------------------

    def fetch_positions(self) -> List[Dict[str, Any]]:
        """Return all open MT5 positions. Aliased by list_positions()."""
        if not self.initialized:
            return []

        positions = mt5.positions_get()
        if positions is None:
            return []

        pos_list = []
        for p in positions:
            pos_list.append({
                "symbol": p.symbol,
                "ticket": p.ticket,
                "type": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
                "volume": p.volume,
                "open_price": p.price_open,
                "sl": p.sl,
                "tp": p.tp,
                "profit": p.profit,
            })
        return pos_list

    def fetch_order_status(self, order_id: str) -> Dict[str, Any]:
        """Check MT5 deal history to determine if an order has been filled."""
        if not self.initialized:
            return {"order_id": order_id, "status": "UNKNOWN"}

        try:
            deals = mt5.history_deals_get(ticket=int(order_id))
            if deals and len(deals) > 0:
                return {"order_id": order_id, "status": "FILLED"}
        except ValueError:
            pass

        return {"order_id": order_id, "status": "UNKNOWN"}

    # ------------------------------------------------------------------
    # F-07: Limit order support
    # ------------------------------------------------------------------

    def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        instrument_type: str = "forex",
        time_in_force: str = "GTC",
        **kwargs,
    ) -> Dict[str, Any]:
        """Submit a pending limit order to MT5.

        MT5 limit orders become active when price reaches the limit level.
        Uses TRADE_ACTION_PENDING with ORDER_TYPE_BUY_LIMIT / ORDER_TYPE_SELL_LIMIT.
        """
        if not self.initialized:
            raise RuntimeError("MT5 not initialized — cannot place limit order")
        if mt5 is None:
            raise RuntimeError("MetaTrader5 package not installed")

        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"MT5 symbol not found or could not be selected: {symbol}")

        tif_map = {
            "GTC": mt5.ORDER_TIME_GTC,
            "DAY": mt5.ORDER_TIME_DAY,
            "GTD": mt5.ORDER_TIME_SPECIFIED,
            "IOC": mt5.ORDER_TIME_DAY,   # MT5 has no IOC for pending; use DAY
            "FOK": mt5.ORDER_TIME_DAY,
        }
        mt5_tif = tif_map.get(time_in_force.upper(), mt5.ORDER_TIME_GTC)

        order_type = (
            mt5.ORDER_TYPE_BUY_LIMIT if side.upper() == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT
        )

        request = {
            "action":      mt5.TRADE_ACTION_PENDING,
            "symbol":      symbol,
            "volume":      float(quantity),
            "type":        order_type,
            "price":       float(price),
            "deviation":   kwargs.get("deviation", 20),
            "magic":       kwargs.get("magic", 234000),
            "comment":     "TradingAgents Limit",
            "type_time":   mt5_tif,
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }
        if kwargs.get("sl"):
            request["sl"] = float(kwargs["sl"])
        if kwargs.get("tp"):
            request["tp"] = float(kwargs["tp"])
        if time_in_force.upper() == "GTD" and kwargs.get("expiry_time"):
            request["expiration"] = kwargs["expiry_time"]

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else -1
            comment = result.comment if result else "no result"
            raise RuntimeError(
                f"MT5 limit order failed: {comment} (retcode={retcode})"
            )

        return {
            "order_id": str(result.order),
            "status": "PENDING",
            "symbol": symbol,
            "limit_price": float(price),
            "broker": self.name,
        }

    def cancel_order(self, order_id: str, symbol: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """Cancel a single pending MT5 order by ticket ID."""
        if not self.initialized or mt5 is None:
            raise RuntimeError("MT5 not initialized — cannot cancel order")

        request = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": int(order_id),
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else -1
            comment = result.comment if result else "no result"
            logger.error("MT5 cancel failed: order_id=%s retcode=%d %s", order_id, retcode, comment)
            return {"order_id": order_id, "status": "FAILED", "error": comment}

        return {"order_id": order_id, "status": "CANCELLED"}

    def get_order_status(self, order_id: str, **kwargs) -> str:
        """Return normalised status for a pending or completed MT5 order.

        Returns one of: FILLED | PENDING | CANCELLED | REJECTED | UNKNOWN
        """
        if not self.initialized or mt5 is None:
            return "UNKNOWN"
        try:
            ticket = int(order_id)
            # Check if still in the pending orders list
            pending = mt5.orders_get(ticket=ticket)
            if pending and len(pending) > 0:
                return "PENDING"
            # Check deal history for a fill
            deals = mt5.history_deals_get(ticket=ticket)
            if deals and len(deals) > 0:
                return "FILLED"
            # Check order history (cancelled/rejected)
            history = mt5.history_orders_get(ticket=ticket)
            if history and len(history) > 0:
                state = history[0].state
                if state == mt5.ORDER_STATE_CANCELED:
                    return "CANCELLED"
                if state in (mt5.ORDER_STATE_REJECTED, mt5.ORDER_STATE_EXPIRED):
                    return "REJECTED"
                if state == mt5.ORDER_STATE_FILLED:
                    return "FILLED"
        except Exception as e:
            logger.warning("get_order_status(%s) failed: %s", order_id, e)
        return "UNKNOWN"

