from __future__ import annotations
"""
Module: dhan_broker.py
Part of the execution subsystem.

This module contains logic for the execution operations as part of the broader TradingAgents framework.
"""

import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from .base import BrokerBase
from .resilience import execute_with_resilience, raise_for_http_status


class DhanBroker(BrokerBase):
    name = "dhan"

    def __init__(self) -> None:
        self.client_id = os.getenv("DHAN_CLIENT_ID", "").strip()
        self.access_token = os.getenv("DHAN_ACCESS_TOKEN", "").strip()
        self.base_url = os.getenv("DHAN_BASE_URL", "https://api.dhan.co").rstrip("/")
        self.timeout = int(os.getenv("TRADINGAGENTS_HTTP_TIMEOUT_SECONDS", "15"))

    def _headers(self) -> Dict[str, str]:
        if not self.client_id or not self.access_token:
            raise RuntimeError("Missing DHAN_CLIENT_ID or DHAN_ACCESS_TOKEN")
        return {
            "client-id": self.client_id,
            "access-token": self.access_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        instrument_type: str,
        **kwargs,
    ) -> Dict[str, Any]:
        exchange_segment = kwargs.get("exchange_segment") or ("NSE_FNO" if instrument_type == "options" else "NSE_EQ")
        product_type = kwargs.get("product_type", "INTRADAY")
        security_id = str(kwargs.get("security_id", "")).strip()
        if not security_id and instrument_type == "options":
            security_id = self._resolve_security_id_from_symbol(symbol)
        if not security_id:
            raise RuntimeError("Dhan execution requires security_id (option chain security ID)")

        payload = {
            "dhanClientId": self.client_id,
            "transactionType": "BUY" if side.upper() == "BUY" else "SELL",
            "exchangeSegment": exchange_segment,
            "productType": product_type,
            "orderType": "MARKET",
            "validity": "DAY",
            "securityId": security_id,
            "quantity": int(quantity),
            "price": 0,
            "triggerPrice": 0,
            "afterMarketOrder": False,
            "boProfitValue": 0,
            "boStopLossValue": 0,
            "correlationId": kwargs.get("correlation_id", f"ta-{symbol}-{side}-{int(quantity)}"),
        }

        # Apply structural slippage guard (convert to LIMIT if entry price is provided)
        suggested_entry = kwargs.get("suggested_entry")
        if suggested_entry:
            suggested_entry_val = float(suggested_entry)
            max_slip = float(kwargs.get("max_slippage_pct", 0.05))
            limit_price = suggested_entry_val * (1 + max_slip) if side.upper() == "BUY" else suggested_entry_val * (1 - max_slip)
            
            # Floor out floating points for NSE tick sizes (typically 0.05)
            limit_price = round(limit_price / 0.05) * 0.05
            
            payload["orderType"] = "LIMIT"
            payload["price"] = limit_price

        r = execute_with_resilience(
            lambda: requests.post(
                f"{self.base_url}/orders",
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
            ),
            operation_name="dhan:orders:post",
        )
        _raise_for_status(r)
        return r.json() if _is_json_response(r) else {"raw": r.text}

    def place_bracket_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        instrument_type: str,
        stop_loss: Optional[float] = None,
        target: Optional[float] = None,
        trailing_jump: float = 0.0,
        **kwargs,
    ) -> Dict[str, Any]:
        exchange_segment = kwargs.get("exchange_segment") or ("NSE_FNO" if instrument_type == "options" else "NSE_EQ")
        product_type = kwargs.get("product_type", "MARGIN")
        security_id = str(kwargs.get("security_id", "")).strip()
        if not security_id and instrument_type == "options":
            security_id = self._resolve_security_id_from_symbol(symbol)
        if not security_id:
            raise RuntimeError("Dhan bracket execution requires security_id")

        if stop_loss is None or target is None:
            return self.place_market_order(symbol, side, quantity, instrument_type, **kwargs)

        payload = {
            "dhanClientId": self.client_id,
            "transactionType": "BUY" if side.upper() == "BUY" else "SELL",
            "exchangeSegment": exchange_segment,
            "productType": product_type,
            "orderType": "MARKET",
            "validity": "DAY",
            "securityId": security_id,
            "quantity": int(quantity),
            "price": 0,
            "triggerPrice": 0,
            "afterMarketOrder": False,
            "boProfitValue": float(target),
            "boStopLossValue": float(stop_loss),
            "trailingJump": float(trailing_jump),
            "correlationId": kwargs.get("correlation_id", f"ta-bo-{symbol}-{side}-{int(quantity)}"),
        }

        # Structure slippage guard
        suggested_entry = kwargs.get("suggested_entry")
        if suggested_entry:
            suggested_entry_val = float(suggested_entry)
            max_slip = float(kwargs.get("max_slippage_pct", 0.05))
            limit_price = suggested_entry_val * (1 + max_slip) if side.upper() == "BUY" else suggested_entry_val * (1 - max_slip)
            limit_price = round(limit_price / 0.05) * 0.05
            
            payload["orderType"] = "LIMIT"
            payload["price"] = limit_price
        r = execute_with_resilience(
            lambda: requests.post(
                f"{self.base_url}/orders",
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
            ),
            operation_name="dhan:orders:bracket",
        )
        _raise_for_status(r)
        return r.json() if _is_json_response(r) else {"raw": r.text}

    def list_positions(self) -> List[Dict[str, Any]]:
        r = execute_with_resilience(
            lambda: requests.get(
                f"{self.base_url}/positions",
                headers=self._headers(),
                timeout=self.timeout,
            ),
            operation_name="dhan:positions:get",
        )
        _raise_for_status(r)
        data = r.json() if _is_json_response(r) else []
        if isinstance(data, dict):
            return data.get("data", []) if isinstance(data.get("data"), list) else [data]
        return data if isinstance(data, list) else []

    def close_symbol_position(self, symbol: str, instrument_type: str = "options", **kwargs) -> Dict[str, Any]:
        security_id = str(kwargs.get("security_id", "")).strip()
        net_qty = float(kwargs.get("net_quantity", 0))
        if not security_id or net_qty == 0:
            raise RuntimeError("close_symbol_position requires security_id and non-zero net_quantity")
        side = "SELL" if net_qty > 0 else "BUY"
        return self.place_market_order(
            symbol=symbol,
            side=side,
            quantity=abs(net_qty),
            instrument_type=instrument_type,
            **kwargs,
        )

    def cancel_all_orders(self, symbol: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """
        Fetches all open orders from Dhan and cancels each one (PENDING / TRANSIT).
        Filters by symbol if provided.  Each cancellation is isolated so a single
        failure does not abort the rest.
        Returns a summary dict with counts.
        """
        import logging as _logging
        _log = _logging.getLogger(__name__)

        # 1. Fetch order book
        try:
            r = execute_with_resilience(
                lambda: requests.get(
                    f"{self.base_url}/orders",
                    headers=self._headers(),
                    timeout=self.timeout,
                ),
                operation_name="dhan:orders:list",
            )
            _raise_for_status(r)
            order_book = r.json() if _is_json_response(r) else []
            if isinstance(order_book, dict):
                order_book = order_book.get("data", []) or []
        except Exception as e:
            _log.error(f"DhanBroker.cancel_all_orders: Failed to fetch order book: {e}")
            return {"status": "error", "error": str(e), "cancelled": 0, "failed": 0}

        # 2. Filter to open orders
        CANCELLABLE_STATUSES = {"PENDING", "TRANSIT", "PARTIALLY_FILLED"}
        to_cancel = [
            o for o in order_book
            if isinstance(o, dict)
            and o.get("orderStatus", "").upper() in CANCELLABLE_STATUSES
            and (symbol is None or o.get("tradingSymbol", "") == symbol)
        ]

        if not to_cancel:
            _log.info(f"DhanBroker.cancel_all_orders: No open orders to cancel (symbol={symbol}).")
            return {"status": "ok", "cancelled": 0, "failed": 0, "skipped": len(order_book)}

        # 3. Cancel each individually
        cancelled, failed, errors = 0, 0, []
        for order in to_cancel:
            order_id = order.get("orderId") or order.get("order_id", "")
            if not order_id:
                failed += 1
                errors.append({"order": order, "error": "missing orderId"})
                continue
            try:
                del_r = execute_with_resilience(
                    lambda oid=order_id: requests.delete(
                        f"{self.base_url}/orders/{oid}",
                        headers=self._headers(),
                        timeout=self.timeout,
                    ),
                    operation_name=f"dhan:orders:cancel:{order_id}",
                )
                _raise_for_status(del_r)
                cancelled += 1
                _log.info(f"DhanBroker: Cancelled order {order_id} ({order.get('tradingSymbol', '')})")
            except Exception as ce:
                failed += 1
                errors.append({"order_id": order_id, "error": str(ce)})
                _log.error(f"DhanBroker: Failed to cancel order {order_id}: {ce}")

        _log.warning(
            f"DhanBroker.cancel_all_orders complete — "
            f"cancelled={cancelled}, failed={failed}, total_open={len(to_cancel)}"
        )
        return {
            "status": "partial" if failed else "ok",
            "cancelled": cancelled,
            "failed": failed,
            "errors": errors,
        }

    def get_quote(self, symbol: str) -> Dict[str, float]:
        """
        Fetches the Last Traded Price for a given symbol from Dhan's LTP feed.
        Because Dhan's REST API does not return full L1 bid/ask in a single call,
        we derive bid/ask from the LTP with a conservative ±0.1% spread.
        Returns: {"bid": float, "ask": float, "last": float}
        """
        import logging as _qlog
        _log = _qlog.getLogger(__name__)
        try:
            payload = {"NSE_FNO": [symbol]}
            r = execute_with_resilience(
                lambda: requests.post(
                    f"{self.base_url}/marketfeed/ltp",
                    json=payload,
                    headers=self._headers(),
                    timeout=self.timeout,
                ),
                operation_name="dhan:marketfeed:ltp",
            )
            _raise_for_status(r)
            body = r.json() if _is_json_response(r) else {}
            # Dhan response: {"data": {"NSE_FNO": {"SYMBOL": {"last_price": ...}}}}
            data = body.get("data", {}) if isinstance(body, dict) else {}
            segment = data.get("NSE_FNO", {})
            ticker = segment.get(symbol, {}) if isinstance(segment, dict) else {}
            ltp = float(ticker.get("last_price") or ticker.get("ltp") or 0.0)
        except Exception as e:
            _log.warning(f"DhanBroker.get_quote({symbol}) failed: {e}. Raising for slippage guard.")
            raise

        spread = ltp * 0.001  # 0.1% conservative spread
        return {"bid": max(0.0, ltp - spread), "ask": ltp + spread, "last": ltp}

    def _resolve_security_id_from_symbol(self, symbol: str) -> str:

        parsed = _parse_nifty_option_symbol(symbol)
        if not parsed:
            raise RuntimeError(
                "Could not auto-resolve Dhan security_id: unrecognized option symbol format. "
                "Provide --security-id explicitly."
            )

        expiry = parsed["expiry"]
        strike = parsed["strike"]
        option_side = parsed["option_side"]

        expiry = self._resolve_listed_expiry(expiry)

        chain_payload = {
            "UnderlyingScrip": int(os.getenv("DHAN_NIFTY_SECURITY_ID", "13")),
            "UnderlyingSeg": os.getenv("DHAN_NIFTY_UNDERLYING_SEGMENT", "IDX_I"),
            "Expiry": expiry,
        }
        chain_resp = execute_with_resilience(
            lambda: requests.post(
                f"{self.base_url}/v2/optionchain",
                json=chain_payload,
                headers=self._headers(),
                timeout=self.timeout,
            ),
            operation_name="dhan:optionchain:post",
        )
        _raise_for_status(chain_resp)
        payload = chain_resp.json() if _is_json_response(chain_resp) else {}

        rows = _extract_option_chain_rows(payload)
        if not rows:
            raise RuntimeError(f"No option chain rows returned for expiry {expiry} while resolving security_id")

        leg_key = "ce" if option_side == "CE" else "pe"
        for row in rows:
            row_strike = _to_float(row.get("strike"))
            if row_strike is None or abs(row_strike - strike) > 0.1:
                continue
            leg = row.get(leg_key, {}) if isinstance(row.get(leg_key), dict) else {}
            for key in ("security_id", "securityId", "sec_id", "scripId", "securityid"):
                if leg.get(key):
                    return str(leg[key])

        raise RuntimeError(
            f"Could not resolve Dhan security_id for {symbol} (expiry={expiry}, strike={strike}, side={option_side}). "
            "Provide --security-id explicitly."
        )

    def _resolve_listed_expiry(self, desired_expiry: str) -> str:
        payload = {
            "UnderlyingScrip": int(os.getenv("DHAN_NIFTY_SECURITY_ID", "13")),
            "UnderlyingSeg": os.getenv("DHAN_NIFTY_UNDERLYING_SEGMENT", "IDX_I"),
        }
        resp = execute_with_resilience(
            lambda: requests.post(
                f"{self.base_url}/v2/optionchain/expirylist",
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
            ),
            operation_name="dhan:optionchain:expirylist",
        )
        _raise_for_status(resp)
        body = resp.json() if _is_json_response(resp) else {}
        expiries = body.get("data", []) if isinstance(body, dict) else []
        if not isinstance(expiries, list) or not expiries:
            return desired_expiry

        # Prefer same month/year inferred from symbol; fallback to nearest available.
        try:
            desired = datetime.strptime(desired_expiry, "%Y-%m-%d")
            same_month = []
            for item in expiries:
                if not isinstance(item, str):
                    continue
                try:
                    dt = datetime.strptime(item, "%Y-%m-%d")
                except ValueError:
                    continue
                if dt.year == desired.year and dt.month == desired.month:
                    same_month.append(item)
            if same_month:
                return sorted(same_month)[0]
        except ValueError:
            pass

        valid = sorted(item for item in expiries if isinstance(item, str))
        return valid[0] if valid else desired_expiry

    # ------------------------------------------------------------------
    # F-07: Limit order support
    # ------------------------------------------------------------------

    def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        instrument_type: str = "options",
        time_in_force: str = "DAY",
        **kwargs,
    ) -> Dict[str, Any]:
        """Submit a limit order to Dhan at a specific price.

        Tick-size rounding (0.05) applied for NSE instruments automatically.
        """
        exchange_segment = kwargs.get("exchange_segment") or (
            "NSE_FNO" if instrument_type == "options" else "NSE_EQ"
        )
        product_type = kwargs.get("product_type", "INTRADAY")
        security_id = str(kwargs.get("security_id", "")).strip()
        if not security_id and instrument_type == "options":
            security_id = self._resolve_security_id_from_symbol(symbol)
        if not security_id:
            raise RuntimeError("Dhan limit order requires security_id")

        # Dhan TIF: DAY | IOC (no GTC for options; default DAY)
        tif_map = {"DAY": "DAY", "IOC": "IOC", "GTC": "DAY", "GTD": "DAY", "FOK": "IOC"}
        dhan_tif = tif_map.get(time_in_force.upper(), "DAY")

        # NSE tick-size rounding (0.05 for most NSE instruments)
        tick = float(kwargs.get("tick_size", 0.05))
        limit_price = round(round(float(price) / tick) * tick, 2)

        payload = {
            "dhanClientId": self.client_id,
            "transactionType": "BUY" if side.upper() == "BUY" else "SELL",
            "exchangeSegment": exchange_segment,
            "productType": product_type,
            "orderType": "LIMIT",
            "validity": dhan_tif,
            "securityId": security_id,
            "quantity": int(quantity),
            "price": limit_price,
            "triggerPrice": 0,
            "afterMarketOrder": False,
            "boProfitValue": 0,
            "boStopLossValue": 0,
            "correlationId": kwargs.get(
                "correlation_id", f"ta-lmt-{symbol}-{side}-{int(quantity)}"
            ),
        }
        r = execute_with_resilience(
            lambda: requests.post(
                f"{self.base_url}/orders",
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
            ),
            operation_name="dhan:orders:limit",
        )
        _raise_for_status(r)
        resp = r.json() if _is_json_response(r) else {"raw": r.text}
        order_id = str(resp.get("orderId") or resp.get("order_id") or "")
        return {"order_id": order_id, "status": resp.get("orderStatus", "PENDING"), "raw": resp}

    def cancel_order(self, order_id: str, symbol: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """Cancel a single open order by Dhan orderId."""
        r = execute_with_resilience(
            lambda: requests.delete(
                f"{self.base_url}/orders/{order_id}",
                headers=self._headers(),
                timeout=self.timeout,
            ),
            operation_name="dhan:orders:cancel",
        )
        _raise_for_status(r)
        resp = r.json() if _is_json_response(r) else {"raw": r.text}
        return {"order_id": order_id, "status": resp.get("orderStatus", "CANCELLED"), "raw": resp}

    def get_order_status(self, order_id: str, **kwargs) -> str:
        """Return normalised order status for a given orderId.

        Returns one of: FILLED | PENDING | CANCELLED | REJECTED | UNKNOWN
        """
        try:
            r = execute_with_resilience(
                lambda: requests.get(
                    f"{self.base_url}/orders/{order_id}",
                    headers=self._headers(),
                    timeout=self.timeout,
                ),
                operation_name="dhan:orders:get",
            )
            _raise_for_status(r)
            resp = r.json() if _is_json_response(r) else {}
            state = str(resp.get("orderStatus", "")).upper()
        except Exception as e:
            import logging as _log
            _log.getLogger(__name__).warning("dhan:get_order_status(%s) failed: %s", order_id, e)
            return "UNKNOWN"

        _status_map = {
            "TRADED":    "FILLED",
            "COMPLETE":  "FILLED",
            "FILLED":    "FILLED",
            "CANCELLED": "CANCELLED",
            "REJECTED":  "REJECTED",
            "TRANSIT":   "PENDING",
            "PENDING":   "PENDING",
            "OPEN":      "PENDING",
        }
        return _status_map.get(state, "UNKNOWN")


def _raise_for_status(response: requests.Response) -> None:
    raise_for_http_status(response, provider_name="Dhan")


def _is_json_response(response: requests.Response) -> bool:
    ctype = response.headers.get("content-type", "")
    return "application/json" in ctype.lower()


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_option_chain_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    oc = data.get("oc") if isinstance(data, dict) else None
    if isinstance(oc, dict):
        rows: List[Dict[str, Any]] = []
        for strike_key, legs in oc.items():
            if not isinstance(legs, dict):
                continue
            rows.append({"strike": strike_key, "ce": legs.get("ce") or {}, "pe": legs.get("pe") or {}})
        return rows
    return []


def _parse_nifty_option_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    text = (symbol or "").strip().upper()
    match = re.search(r"(\d+)(CE|PE)$", text)
    if not match:
        return None

    strike = float(match.group(1))
    option_side = match.group(2)
    root = text[: match.start(1)]

    m = re.search(r"(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)", root)
    if not m:
        return None

    yy = int(m.group(1))
    mon = m.group(2)
    year = 2000 + yy
    month_map = {
        "JAN": 1,
        "FEB": 2,
        "MAR": 3,
        "APR": 4,
        "MAY": 5,
        "JUN": 6,
        "JUL": 7,
        "AUG": 8,
        "SEP": 9,
        "OCT": 10,
        "NOV": 11,
        "DEC": 12,
    }
    month = month_map[mon]

    # Seed expiry to year-month; exact listed expiry is resolved from expirylist.
    expiry = datetime(year, month, 1).strftime("%Y-%m-%d")
    return {"strike": strike, "option_side": option_side, "expiry": expiry}

