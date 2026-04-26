import yfinance as yf
import pandas as pd
import numpy as np
import vectorbt as vbt
from datetime import datetime, timedelta

def run_auto_quant(symbol: str, indicator: str, fast_period: int, slow_period: int, direction: str = "longonly"):
    """
    Runs a fast inline VectorBT backtest on the last 90 days of daily data.
    indicator: 'EMA', 'SMA', 'RSI'
    direction: 'longonly', 'shortonly', 'both'
    """
    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=90)
        
        # Clean symbol for yfinance
        yf_symbol = symbol
        if "XAUUSD" in symbol:
            yf_symbol = "GC=F"
        elif "BTC" in symbol:
            yf_symbol = "BTC-USD"
        elif "ETH" in symbol:
            yf_symbol = "ETH-USD"
            
        df = yf.download(yf_symbol, start=start_date.strftime("%Y-%m-%d"), end=end_date.strftime("%Y-%m-%d"), progress=False)
        if df.empty:
            return {"status": "error", "message": f"No data downloaded for {yf_symbol}"}
            
        close = df['Close'].squeeze()
        
        indicator = indicator.upper()
        
        if indicator in ["EMA", "SMA"]:
            if indicator == "EMA":
                fast_ma = close.ewm(span=fast_period, adjust=False).mean()
                slow_ma = close.ewm(span=slow_period, adjust=False).mean()
            else:
                fast_ma = close.rolling(window=fast_period).mean()
                slow_ma = close.rolling(window=slow_period).mean()
                
            entries = (fast_ma > slow_ma) & (fast_ma.shift(1) <= slow_ma.shift(1))
            exits = (fast_ma < slow_ma) & (fast_ma.shift(1) >= slow_ma.shift(1))
            
        elif indicator == "RSI":
            delta = close.diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=fast_period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=fast_period).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            
            # RSI strategy: Buy when crossing above slow_period (e.g. 30), sell when crossing below 100-slow_period
            entries = (rsi > slow_period) & (rsi.shift(1) <= slow_period)
            exits = (rsi < 100 - slow_period) & (rsi.shift(1) >= 100 - slow_period)
        else:
            return {"status": "error", "message": f"Unsupported indicator: {indicator}"}
            
        pf = vbt.Portfolio.from_signals(
            close, entries, exits,
            init_cash=10000, 
            fees=0.001,
            direction=direction,
            freq="1D"
        )
        
        sharpe = float(pf.sharpe_ratio())
        max_dd = float(pf.max_drawdown()) * 100
        win_rate = float(pf.trades.win_rate()) * 100 if pf.trades.count() > 0 else 0.0
        
        status = "passed"
        reason = ""
        
        # We explicitly lower the threshold for 90 days to test the feature.
        # Strict validation: Sharpe > 0.5 and DD < 15%
        if np.isnan(sharpe) or sharpe < 0.5:
            status = "failed"
            reason = f"Poor Sharpe Ratio: {sharpe:.2f} < 0.5"
        elif abs(max_dd) > 15.0:
            status = "failed"
            reason = f"Drawdown too high: {abs(max_dd):.2f}% > 15%"
            
        return {
            "status": status,
            "sharpe_ratio": round(sharpe, 2) if not np.isnan(sharpe) else 0.0,
            "max_drawdown_pct": round(abs(max_dd), 2) if not np.isnan(max_dd) else 0.0,
            "win_rate_pct": round(win_rate, 2),
            "reason": reason
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
