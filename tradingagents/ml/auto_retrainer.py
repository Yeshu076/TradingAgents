import logging
import asyncio
import json
import os
import sys
import numpy as np
from pathlib import Path
from typing import Dict, Any

try:
    import vectorbt as vbt
except ImportError:
    vbt = None

from tradingagents.strategy_lab.backtest_engine import fetch_ohlcv

logger = logging.getLogger("auto_retrainer")

class StrategyMLRetrainer:
    """
    Periodically auto-retrains model parameters using grid search via 
    VectorBT's highly parallel portfolio optimization.
    """
    def __init__(self, config_output_dir: str = "config/strategies"):
        self.output_dir = Path(config_output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Using a base set of symbols for demonstration
        self.universe = ["AAPL", "NVDA", "BTC-USD"]
        
    def generate_parameter_grid(self) -> Dict[str, np.ndarray]:
        # Defining the parameter space to search (e.g., for an EMA Crossover strategy)
        return {
            "fast_window": np.arange(10, 30, step=2),
            "slow_window": np.arange(40, 100, step=5)
        }

    async def run_optimization_cycle(self):
        logger.info("[ML_RETRAINER] Starting global parameter optimization cycle...")
        
        if vbt is None:
            logger.error("VectorBT not installed. Cannot run optimization.")
            return
            
        optimal_params = {}
        
        for symbol in self.universe:
            logger.info(f"Fetching data and optimizing for {symbol}...")
            try:
                # Fetching the last 90 days to prevent severe overfitting
                data = fetch_ohlcv(symbol, period="90d", interval="1h")
                if data.empty:
                    continue
                    
                close = data['close']
                grid = self.generate_parameter_grid()
                
                # Create MA combinations
                fast_ma = vbt.MA.run(close, window=grid['fast_window'], param_product=True)
                slow_ma = vbt.MA.run(close, window=grid['slow_window'], param_product=True)
                
                # Generate signals
                entries = fast_ma.ma_crossed_above(slow_ma)
                exits = fast_ma.ma_crossed_below(slow_ma)
                
                # Build portfolio combinations
                pf = vbt.Portfolio.from_signals(close, entries, exits, fees=0.001)
                
                # Extract the highest Sharpe Ratio parameters
                sharpe_ratios = pf.sharpe_ratio()
                best_idx = sharpe_ratios.idxmax()
                
                if isinstance(best_idx, tuple) and len(best_idx) == 2:
                    best_fast, best_slow = best_idx
                    best_sharpe = sharpe_ratios[best_idx]
                    
                    logger.info(f"[{symbol}] Optimized params found -> Fast: {best_fast}, Slow: {best_slow} (Expected 90D Sharpe: {best_sharpe:.2f})")
                    
                    optimal_params[symbol] = {
                        "fast_window": int(best_fast),
                        "slow_window": int(best_slow),
                        "expected_sharpe": float(best_sharpe),
                        "last_trained": str(data.index[-1])
                    }
                
            except Exception as e:
                logger.error(f"Failed to optimize {symbol}: {e}")

        # Save config asynchronously
        if optimal_params:
            self._save_config(optimal_params)

    def _save_config(self, params: Dict[str, Any]):
        out_file = self.output_dir / "ema_crossover_optimized.json"
        try:
            with open(out_file, 'w') as f:
                json.dump(params, f, indent=4)
            logger.info(f"[ML_RETRAINER] Hot-swappable strategy config saved to {out_file}")
        except Exception as e:
            logger.error(f"Failed to save optimized parameters: {e}")

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    retrainer = StrategyMLRetrainer()
    asyncio.run(retrainer.run_optimization_cycle())
