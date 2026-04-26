"""
Module: dynamic_runner.py
Part of the strategy_lab subsystem.

This module contains logic for the strategy_lab operations as part of the broader TradingAgents framework.
"""
import os
import json
import logging
import subprocess
from pathlib import Path

from tradingagents.execution import execute_trade, TradeIntent
from tradingagents.execution.position_manager import PositionManager
from tradingagents.strategy_lab.live_pricing import get_live_price

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_approved_strategies():
    """
    Finds all approved scripts, executes them on live recent data, 
    parses the JSON last_signal, and dispatches to the execution engine.
    """
    approved_dir = Path("strategy_lab_results") / "approved_scripts"
    
    if not approved_dir.exists():
        logger.info("No approved strategies found to execute.")
        return

    for script_file in approved_dir.glob("*.py"):
        logger.info(f"Running approved strategy: {script_file.name}")
        
        try:
            # We assume script inherently fetches the latest data via yfinance internally
            process = subprocess.run(
                ["python", str(script_file)],
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if process.returncode != 0:
                logger.error(f"Strategy {script_file.name} failed: {process.stderr}")
                continue
                
            # Parse the metrics from stdout
            lines = process.stdout.strip().split("\n")
            json_metrics = None
            for line in reversed(lines):
                try:
                    if line.startswith("{") and line.endswith("}"):
                        json_metrics = json.loads(line)
                        break
                except Exception:
                    pass
            
            if not json_metrics:
                logger.error(f"Cannot parse JSON from {script_file.name}. Stdout: {process.stdout}")
                continue
                
            # Evaluate Last Signal
            last_signal = json_metrics.get("last_signal", "HOLD").upper()
            logger.info(f"Strategy {script_file.name} generated signal: {last_signal}")
            
            if last_signal in ["BUY", "SELL"]:
                # Assume symbol is parsed from the filename (e.g. strategy_BTC_USD_approved.py -> BTC-USD)
                parts = script_file.name.replace(".py", "").split("_")
                symbol = f"{parts[1]}-{parts[2]}" if len(parts) >= 3 else "BTC-USD"
                
                # Determine instrument type based on naming convention
                if "XAU" in symbol or "USD" in symbol and "BTC" not in symbol and "ETH" not in symbol:
                    instrument_type = "forex"
                elif "BTC" in symbol or "ETH" in symbol or "SOL" in symbol:
                    instrument_type = "crypto"
                elif "NIFTY" in symbol:
                    instrument_type = "options"
                else:
                    instrument_type = "equity"
                risk_pct = float(os.getenv("TRADINGAGENTS_RISK_MAX_POSITION_PCT", "0.05"))
                # Fetch live price and paper wallet state
                try:
                    current_price = get_live_price(symbol, instrument_type)
                except Exception:
                    current_price = 0.0
                if not current_price or current_price <= 0:
                    logger.warning(f"Could not fetch live price for {symbol}. Skipping execution.")
                    continue

                wallet = PositionManager.from_env()
                cash = wallet.get_cash()
                # Calculate what risk_pct of equity is
                max_capital_for_trade = cash * risk_pct
                
                # Derive quantity
                target_quantity = max_capital_for_trade / current_price if current_price > 0 else 1.0
                # Sanity fallback / floor
                target_quantity = max(0.001, round(target_quantity, 3))

                # Create default TradeIntent. Let the execution engine determine risk sizing.
                intent = TradeIntent(
                    symbol=symbol,
                    signal=last_signal,
                    quantity=target_quantity,
                    instrument_type=instrument_type,
                    suggested_entry=current_price
                )

                logger.info(f"Executing intent for {symbol}: {last_signal}")
                execute_trade(
                    intent=intent,
                    broker="auto",
                    paper=True,
                    strategy_name=script_file.name
                )

        except Exception as e:
            logger.error(f"Exception while running {script_file.name}: {e}")