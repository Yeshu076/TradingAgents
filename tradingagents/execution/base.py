from __future__ import annotations
"""
Module: base.py
Part of the execution subsystem.

This module contains logic for the execution operations as part of the broader TradingAgents framework.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BrokerBase(ABC):
    name: str = "base"

    @abstractmethod
    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        instrument_type: str,
        **kwargs,
    ) -> Dict[str, Any]:
        raise NotImplementedError

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
        return self.place_market_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            instrument_type=instrument_type,
            stop_loss=stop_loss,
            target=target,
            trailing_jump=trailing_jump,
            **kwargs,
        )

    @abstractmethod
    def list_positions(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def close_symbol_position(self, symbol: str, instrument_type: str = "options", **kwargs) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def cancel_all_orders(self, symbol: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        raise NotImplementedError

    def get_quote(self, symbol: str) -> Dict[str, float]:
        """Returns L1 quote dictionary with at least 'bid' and 'ask' keys."""
        raise NotImplementedError("This broker does not support live quoting yet.")

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
        """F-07: Submit a limit order at the specified price.

        Args:
            symbol: Instrument symbol.
            side: 'BUY' or 'SELL'.
            quantity: Number of units.
            price: Limit price.
            instrument_type: options / spot / crypto / forex / equity.
            time_in_force: DAY | GTC | GTD | IOC | FOK.
            **kwargs: Broker-specific overrides (e.g. product_id, security_id).

        Returns:
            Dict containing at least 'order_id' and 'status'.

        Raises:
            NotImplementedError: If the broker does not implement limit orders.
        """
        raise NotImplementedError(
            f"Broker '{self.name}' does not implement place_limit_order(). "
            "Override this method in the concrete broker class."
        )

    def cancel_order(self, order_id: str, symbol: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """F-07: Cancel a specific open order by order_id.

        Returns:
            Dict with 'order_id' and 'status' keys (at minimum).

        Raises:
            NotImplementedError: If the broker does not support order cancellation.
        """
        raise NotImplementedError(
            f"Broker '{self.name}' does not implement cancel_order(). "
            "Override this method in the concrete broker class."
        )

    def get_order_status(self, order_id: str, **kwargs) -> str:
        """F-07: Return the broker-level status of an order.

        Returns:
            One of: 'FILLED', 'PENDING', 'CANCELLED', 'REJECTED', 'UNKNOWN'.

        Raises:
            NotImplementedError: If the broker does not support order status queries.
        """
        raise NotImplementedError(
            f"Broker '{self.name}' does not implement get_order_status()."
        )

