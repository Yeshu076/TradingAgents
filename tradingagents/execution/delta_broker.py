from __future__ import annotations
"""
Module: delta_broker.py
Part of the execution subsystem.

This module contains logic for the execution operations as part of the broader TradingAgents framework.
"""

import hmac
import json
import os
import time
from hashlib import sha256
from typing import Any, Dict, List, Optional

import requests

from .base import BrokerBase
from .resilience import execute_with_resilience, raise_for_http_status


class DeltaBroker(BrokerBase):
    name = "delta"

    def __init__(self) -> None:
        self.api_key = os.getenv("DELTA_API_KEY", "").strip()
        self.api_secret = os.getenv("DELTA_API_SECRET", "").strip()
        self.base_url = os.getenv(
            "DELTA_BASE_URL",
            os.getenv("DELTA_REST_BASE_URL", "https://api.india.delta.exchange"),
        ).rstrip("/")
        self.timeout = int(os.getenv("TRADINGAGENTS_HTTP_TIMEOUT_SECONDS", "15"))

    def _headers(self, method: str, path: str, body: str) -> Dict[str, str]:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Missing DELTA_API_KEY or DELTA_API_SECRET")

        timestamp = str(int(time.time()))
        signature_data = method + timestamp + path + body
        signature = hmac.new(
            bytes(self.api_secret, "utf-8"),
            bytes(signature_data, "utf-8"),
            sha256,
        ).hexdigest()

        return {
            "api-key": self.api_key,
            "timestamp": timestamp,
            "signature": signature,
            "Content-Type": "application/json",
        }

    def _resolve_product_id(self, symbol: str, explicit_product_id: Optional[int] = None) -> int:
        if explicit_product_id:
            return int(explicit_product_id)

        normalized = symbol.upper().replace("/", "").replace("-", "")
        r = execute_with_resilience(
            lambda: requests.get(f"{self.base_url}/v2/products/{normalized}", timeout=self.timeout),
            operation_name="delta:products:get",
        )
        raise_for_http_status(r, provider_name="Delta")
        payload = r.json()
        result = payload.get("result", {}) if isinstance(payload, dict) else {}
        product_id = result.get("id")
        if not product_id:
            raise RuntimeError(f"Delta product ID not found for {symbol}")
        return int(product_id)

    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        instrument_type: str,
        **kwargs,
    ) -> Dict[str, Any]:
        product_id = self._resolve_product_id(symbol, kwargs.get("product_id"))
        path = "/v2/orders"
        payload = {
            "product_id": product_id,
            "size": int(quantity),
            "side": "buy" if side.upper() == "BUY" else "sell",
            "order_type": "market_order",
        }

        # Apply structural slippage guard (convert to LIMIT if entry price is provided)
        suggested_entry = kwargs.get("suggested_entry")
        if suggested_entry:
            suggested_entry_val = float(suggested_entry)
            max_slip = float(kwargs.get("max_slippage_pct", 0.05))
            limit_price = suggested_entry_val * (1 + max_slip) if side.upper() == "BUY" else suggested_entry_val * (1 - max_slip)
            
            payload["order_type"] = "limit_order"
            payload["limit_price"] = str(round(limit_price, 2))

        body = json.dumps(payload, separators=(",", ":"))
        headers = self._headers("POST", path, body)
        r = execute_with_resilience(
            lambda: requests.post(
                f"{self.base_url}{path}",
                data=body,
                headers=headers,
                timeout=self.timeout,
            ),
            operation_name="delta:orders:post",
        )
        raise_for_http_status(r, provider_name="Delta")
        return r.json()

    def list_positions(self) -> List[Dict[str, Any]]:
        path = "/v2/positions/margined"
        headers = self._headers("GET", path, "")
        r = execute_with_resilience(
            lambda: requests.get(f"{self.base_url}{path}", headers=headers, timeout=self.timeout),
            operation_name="delta:positions:get",
        )
        raise_for_http_status(r, provider_name="Delta")
        payload = r.json() if "application/json" in r.headers.get("content-type", "") else {}
        result = payload.get("result", []) if isinstance(payload, dict) else []
        return result if isinstance(result, list) else []

    def close_symbol_position(self, symbol: str, instrument_type: str = "spot", **kwargs) -> Dict[str, Any]:
        product_id = self._resolve_product_id(symbol, kwargs.get("product_id"))
        path = "/v2/positions/close"
        payload = {"product_id": product_id}

        body = json.dumps(payload, separators=(",", ":"))
        headers = self._headers("POST", path, body)
        r = execute_with_resilience(
            lambda: requests.post(
                f"{self.base_url}{path}",
                data=body,
                headers=headers,
                timeout=self.timeout,
            ),
            operation_name="delta:positions:close",
        )
        raise_for_http_status(r, provider_name="Delta")
        return r.json()

    def cancel_all_orders(self, symbol: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        path = "/v2/orders/cancel/all"
        payload: Dict[str, Any] = {}
        if symbol:
            payload["product_id"] = self._resolve_product_id(symbol, kwargs.get("product_id"))

        body = json.dumps(payload, separators=(",", ":"))
        headers = self._headers("DELETE", path, body)
        r = execute_with_resilience(
            lambda: requests.delete(
                f"{self.base_url}{path}",
                data=body,
                headers=headers,
                timeout=self.timeout,
            ),
            operation_name="delta:orders:cancel_all",
        )
        raise_for_http_status(r, provider_name="Delta")
        return r.json()

    def get_quote(self, symbol: str) -> Dict[str, float]:
        """
        Fetches the L1 quote (bid/ask/last) from Delta Exchange ticker endpoint.
        Used by engine.py slippage guard before live order placement.
        Returns: {"bid": float, "ask": float, "last": float}
        """
        normalized = symbol.upper().replace("/", "").replace("-", "")
        path = f"/v2/tickers/{normalized}"
        headers = self._headers("GET", path, "")
        r = execute_with_resilience(
            lambda: requests.get(
                f"{self.base_url}{path}",
                headers=headers,
                timeout=self.timeout,
            ),
            operation_name="delta:tickers:get",
        )
        raise_for_http_status(r, provider_name="Delta")
        payload = r.json()
        result = payload.get("result", {}) if isinstance(payload, dict) else {}
        quotes = result.get("quotes", result) if isinstance(result, dict) else {}

        def _f(key: str) -> float:
            val = quotes.get(key) or result.get(key) or 0.0
            try:
                return float(val)
            except (TypeError, ValueError):
                return 0.0

        bid = _f("best_bid") or _f("bid")
        ask = _f("best_ask") or _f("ask")
        last = _f("close") or _f("last_price") or _f("last")
        # Fall back to mid-price if bid/ask unavailable
        if ask == 0.0 and last > 0:
            ask = last * 1.001
        if bid == 0.0 and last > 0:
            bid = last * 0.999
        return {"bid": bid, "ask": ask, "last": last}

    # ------------------------------------------------------------------
    # F-07: Limit order support
    # ------------------------------------------------------------------

    def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        instrument_type: str = "spot",
        time_in_force: str = "DAY",
        **kwargs,
    ) -> Dict[str, Any]:
        """Submit a limit order to Delta Exchange at the specified price."""
        product_id = self._resolve_product_id(symbol, kwargs.get("product_id"))
        path = "/v2/orders"
        # Delta TIF mapping: GTT = Good-till-time, GTC = Good-till-cancelled
        tif_map = {"DAY": "gtc", "GTC": "gtc", "GTD": "gtc", "IOC": "ioc", "FOK": "fok"}
        delta_tif = tif_map.get(time_in_force.upper(), "gtc")
        payload: Dict[str, Any] = {
            "product_id": product_id,
            "size": int(quantity),
            "side": "buy" if side.upper() == "BUY" else "sell",
            "order_type": "limit_order",
            "limit_price": str(round(float(price), 2)),
            "time_in_force": delta_tif,
        }
        if kwargs.get("post_only"):
            payload["post_only"] = True
        body = json.dumps(payload, separators=(",", ":"))
        headers = self._headers("POST", path, body)
        r = execute_with_resilience(
            lambda: requests.post(
                f"{self.base_url}{path}",
                data=body,
                headers=headers,
                timeout=self.timeout,
            ),
            operation_name="delta:orders:limit",
        )
        raise_for_http_status(r, provider_name="Delta")
        resp = r.json()
        result = resp.get("result", resp) if isinstance(resp, dict) else resp
        # Normalise the response to a consistent shape
        order_id = str(result.get("id") or result.get("order_id") or "")
        return {"order_id": order_id, "status": result.get("state", "open"), "raw": result}

    def cancel_order(self, order_id: str, symbol: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """Cancel a single open order on Delta Exchange."""
        path = f"/v2/orders/{order_id}"
        body = ""
        headers = self._headers("DELETE", path, body)
        r = execute_with_resilience(
            lambda: requests.delete(
                f"{self.base_url}{path}",
                headers=headers,
                timeout=self.timeout,
            ),
            operation_name="delta:orders:cancel",
        )
        raise_for_http_status(r, provider_name="Delta")
        resp = r.json()
        result = resp.get("result", resp) if isinstance(resp, dict) else resp
        return {"order_id": order_id, "status": result.get("state", "cancelled"), "raw": result}

    def get_order_status(self, order_id: str, **kwargs) -> str:
        """Return normalised order status for a given order_id.

        Returns one of: FILLED | PENDING | CANCELLED | REJECTED | UNKNOWN
        """
        path = f"/v2/orders/{order_id}"
        headers = self._headers("GET", path, "")
        try:
            r = execute_with_resilience(
                lambda: requests.get(
                    f"{self.base_url}{path}",
                    headers=headers,
                    timeout=self.timeout,
                ),
                operation_name="delta:orders:get",
            )
            raise_for_http_status(r, provider_name="Delta")
            resp = r.json()
            result = resp.get("result", resp) if isinstance(resp, dict) else resp
            state = str(result.get("state", "")).lower()
        except Exception as e:
            import logging as _log
            _log.getLogger(__name__).warning("delta:get_order_status(%s) failed: %s", order_id, e)
            return "UNKNOWN"

        _status_map = {
            "filled": "FILLED",
            "closed": "FILLED",
            "cancelled": "CANCELLED",
            "canceled": "CANCELLED",
            "rejected": "REJECTED",
            "open": "PENDING",
            "pending": "PENDING",
        }
        return _status_map.get(state, "UNKNOWN")

