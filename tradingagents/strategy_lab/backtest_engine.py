from __future__ import annotations
"""
Module: backtest_engine.py
Part of the strategy_lab subsystem.
Upgraded to use VectorBT for institutional-grade portfolio metrics.
"""

from dataclasses import asdict
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf
import vectorbt as vbt

from .models import StrategyResult, StrategySpec

def fetch_ohlcv(symbol: str, period: str = "2y", interval: str = "1d") -> pd.DataFrame:
    data = yf.Ticker(symbol).history(period=period, interval=interval)
    if data.empty:
        raise ValueError(f"No OHLCV data returned for {symbol} ({period}, {interval})")
    data = data.sort_index()
    data.columns = [c.lower().replace(" ", "_") for c in data.columns]
    if "close" not in data.columns:
        raise ValueError("Close column missing from fetched data")
    
    # Clean datetime index for vbt
    if data.index.tz is not None:
        data.index = data.index.tz_convert(None)
    return data

def _signals_from_spec(close: pd.Series, spec: StrategySpec) -> tuple[pd.Series, pd.Series]:      
    """Returns boolean Series -> (entries, exits)"""
    family = spec.family
    p = spec.params
    
    entries = pd.Series(False, index=close.index)
    exits = pd.Series(False, index=close.index)

    if family == "ema_crossover":
        fast = int(p.get("fast", 10))
        slow = int(p.get("slow", 20))
        fast_ema = close.ewm(span=fast, adjust=False).mean()
        slow_ema = close.ewm(span=slow, adjust=False).mean()
        
        entries = (fast_ema > slow_ema) & (fast_ema.shift(1) <= slow_ema.shift(1))
        exits = (fast_ema < slow_ema) & (fast_ema.shift(1) >= slow_ema.shift(1))

    elif family == "rsi_mean_reversion":
        period = int(p.get("period", 14))
        low = float(p.get("oversold", 30))
        high = float(p.get("overbought", 70))
        
        delta = close.diff()
        gain = delta.clip(lower=0.0).rolling(period).mean()
        loss = -delta.clip(upper=0.0).rolling(period).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        
        entries = (rsi < low) & (rsi.shift(1) >= low)
        exits = (rsi > high) & (rsi.shift(1) <= high)

    elif family == "donchian_breakout":
        lookback = int(p.get("lookback", 20))
        upper = close.shift(1).rolling(lookback).max()
        lower = close.shift(1).rolling(lookback).min()
        
        entries = close > upper
        exits = close < lower

    else:
        raise ValueError(f"Unsupported strategy family: {family}")
        
    return entries.fillna(False), exits.fillna(False)

def simulate_pf(close: pd.Series, entries: pd.Series, exits: pd.Series, fee_bps: float) -> vbt.Portfolio:
    return vbt.Portfolio.from_signals(
        close=close,
        entries=entries,
        exits=exits,
        fees=fee_bps / 10000.0,
        fixed_fees=0,
        init_cash=100000.0,
        freq="1D"  # Assuming daily for now
    )

def evaluate_strategy(
    spec: StrategySpec,
    data: pd.DataFrame,
    train_fraction: float = 0.7,
    fee_bps: float = 5.0,
    annualization: int = 252,
    min_trades: int = 8,
    max_out_drawdown: float = 0.35,
    min_out_sharpe: float = -0.25,
    min_out_return: float = -0.20,
) -> StrategyResult:
    close = data["close"].astype(float)
    entries, exits = _signals_from_spec(close, spec)
    
    split_idx = max(2, int(len(close) * train_fraction))
    
    # In Sample
    pf_in = simulate_pf(close.iloc[:split_idx], entries.iloc[:split_idx], exits.iloc[:split_idx], fee_bps)
    # Out of Sample
    pf_out = simulate_pf(close.iloc[split_idx:], entries.iloc[split_idx:], exits.iloc[split_idx:], fee_bps)

    in_total = float(pf_in.total_return())
    out_total = float(pf_out.total_return())
    in_sharpe = float(pf_in.sharpe_ratio()) if not pd.isna(pf_in.sharpe_ratio()) else 0.0
    out_sharpe = float(pf_out.sharpe_ratio()) if not pd.isna(pf_out.sharpe_ratio()) else 0.0
    in_dd = float(abs(pf_in.max_drawdown())) if not pd.isna(pf_in.max_drawdown()) else 0.0
    out_dd = float(abs(pf_out.max_drawdown())) if not pd.isna(pf_out.max_drawdown()) else 0.0
    trades = int(pf_in.trades.count() + pf_out.trades.count())

    stability = 1.0 - min(1.0, abs(in_total - out_total))
    overfit_gap = max(0.0, in_total - out_total)
    
    robustness_penalty = 0.0
    passed_filters = True
    notes = []

    score = (
        0.45 * out_sharpe
        + 0.25 * out_total
        - 0.20 * out_dd
        + 0.10 * stability
    )

    if trades < min_trades:
        passed_filters = False
        robustness_penalty += min(0.35, (min_trades - trades) / max(1.0, float(min_trades)))
        notes.append("Low trade count; potential overfit")
    if out_dd > max_out_drawdown:
        passed_filters = False
        robustness_penalty += min(0.40, (out_dd - max_out_drawdown) / max(0.05, max_out_drawdown))
        notes.append("High drawdown in out-of-sample")
    if out_sharpe < min_out_sharpe:
        passed_filters = False
        robustness_penalty += min(0.30, (min_out_sharpe - out_sharpe) * 0.30)        
        notes.append("Out-of-sample Sharpe below threshold")
    if out_total < min_out_return:
        passed_filters = False
        robustness_penalty += min(0.30, (min_out_return - out_total) * 0.80)
        notes.append("Out-of-sample return below threshold")
    if overfit_gap > 0.12:
        passed_filters = False
        robustness_penalty += min(0.35, overfit_gap)
        notes.append("Large in/out performance gap suggests overfitting")       

    score -= robustness_penalty

    return StrategyResult(
        spec=spec,
        score=float(score),
        in_sample_return=in_total,
        in_sample_sharpe=in_sharpe,
        in_sample_max_drawdown=in_dd,
        out_sample_return=out_total,
        out_sample_sharpe=out_sharpe,
        out_sample_max_drawdown=out_dd,
        stability=stability,
        trades=trades,
        passed_filters=passed_filters,
        robustness_penalty=float(robustness_penalty),
        overfit_gap=float(overfit_gap),
        notes=notes,
    )


