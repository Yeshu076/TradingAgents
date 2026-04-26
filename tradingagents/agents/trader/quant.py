"""
Module: quant.py
Part of the trader subsystem.

This module contains logic for the trader operations as part of the broader TradingAgents framework.
"""
import json
from pathlib import Path
import os
import functools

def create_quant_agent(llm):
    """
    Creates a QuantAgent that outputs vectorbt-compatible Python code for backtesting.
    """
    def quant_node(state, name):
        symbol = state.get("company_of_interest", "BTC-USD")
        market_report = state.get("market_report", "No market data available.")
        
        system_prompt = f"""
You are a highly skilled Quantitative Researcher.
Your goal is to generate Python code using vectorbt to backtest a strategy that would have performed well in the recent market given the data provided.

Use the typical imports:
import vectorbt as vbt
import pandas as pd
import numpy as np
import yfinance as yf

Write code that:
1. Fetches data via yfinance for '{symbol}' over the past '1y'. YFinance dataframe column schema will exactly be: ['Open', 'High', 'Low', 'Close', 'Volume']. Do NOT use adjusted closes, just 'Close'.
2. Uses purely pd.Series.rolling() or ewm() to construct signals natively (do NOT use talib as it breaks on this host). Do not refer to columns that do not exist (like 'Adj Close' or 'open_interest').
3. Cleans signals (e.g., using basic pandas logic to prevent multiple buys) and runs `vbt.Portfolio.from_signals()`
4. IMPORTANT: At the very end of your script, print exactly ONE line of JSON to stdout containing metrics and the very latest signal, formatted exactly like this:
{{"sharpe": 1.5, "win_rate": 0.65, "last_signal": "BUY"}}
Values for last_signal must be "BUY", "SELL", or "HOLD".

Output exclusively clean python code enclosed in ```python markers, no other chatter.
"""
        user_prompt = f"Recent market context for {symbol}:\n{market_report}\n\nDraft the backtest script."
        
        # If there's feedback from a previous failed run, add it
        feedback = state.get("quant_feedback")
        if feedback:
            user_prompt += f"\n\nYOUR PREVIOUS ATTEMPT FAILED. Fix these errors:\n{feedback}"
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        result = llm.invoke(messages)
        code_content = result.content
        
        # safely extract the code from markdown block
        code = code_content
        if "```python" in code_content:
            code = code_content.split("```python")[1].split("```")[0].strip()
            
        script_dir = Path("strategy_lab_results") / "scripts"
        os.makedirs(script_dir, exist_ok=True)
        out_path = script_dir / f"strategy_{symbol.replace('-', '_')}_vbt.py"
        
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(code)
            
        return {
            "messages": [result],
            "quant_proposal": f"Strategy script saved to {out_path}",
            "sender": name
        }

    return functools.partial(quant_node, name="QuantAgent")
