"""
Module: live_pricing.py
Part of the strategy_lab subsystem.

This module contains logic for the strategy_lab operations as part of the broader TradingAgents framework.
"""
import re
from tradingagents.dataflows.delta_exchange import get_delta_crypto_derivatives_snapshot
from tradingagents.dataflows.dhan_option_chain import get_dhan_option_chain_snapshot

def get_live_price(symbol: str, instrument_type: str) -> float:
    try:
        if instrument_type == "crypto":
            snapshot = get_delta_crypto_derivatives_snapshot(symbol)
            # Find "Last Price: <val>"
            match = re.search(r"Last Price:\s+([\d\.]+)", snapshot)
            if match:
                return float(match.group(1))
        elif instrument_type == "options":
            snapshot = get_dhan_option_chain_snapshot(symbol)
            # Find "CE LTP=<val>". Just get the first Strike's CE LTP
            match = re.search(r"CE LTP=([\d\.]+)", snapshot)
            if match:
                return float(match.group(1))
    except Exception as e:
        print(f"Failed to fetch live price for {symbol}: {e}")
        
    # Fallback to yfinance if it fails or another type
    try:
        import yfinance as yf
        
        # Format for Forex matching Yahoo standards
        yf_symbol = symbol
        if ("XAU" in symbol or ("USD" in symbol and "BTC" not in symbol and "ETH" not in symbol)) and "=" not in symbol:
            yf_symbol = symbol + "=X"
            
        # e.g. 'NVDA' or 'BTC-USD'
        ticker = yf.Ticker(yf_symbol)
        todays_data = ticker.history(period='1d')
        return float(todays_data['Close'].iloc[-1])
    except:
        return 1.0