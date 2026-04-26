"""
Module: order_intent.py
Part of the orders subsystem.

This module contains logic for the orders operations as part of the broader TradingAgents framework.
"""
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


SignalLiteral = Literal["BUY", "OVERWEIGHT", "HOLD", "UNDERWEIGHT", "SELL"]
HorizonLiteral = Literal["intraday", "swing", "positional"]


class OrderIntent(BaseModel):
    ticker: str
    instrument_type: str = "equity"
    signal: SignalLiteral
    confidence: float = Field(ge=0.0, le=1.0)

    suggested_entry: Optional[float] = None
    suggested_stop_loss: Optional[float] = None
    suggested_target: Optional[float] = None
    position_size_pct: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    time_horizon: Optional[HorizonLiteral] = None

    analyst_teams: List[str] = Field(default_factory=list)
    debate_rounds_used: int = 0
    research_depth: str = "unknown"

    validation_warnings: List[str] = Field(default_factory=list)
    consistency_score: float = Field(default=0.5, ge=0.0, le=1.0)

    final_decision_raw: str = ""
    trader_plan_raw: str = ""
