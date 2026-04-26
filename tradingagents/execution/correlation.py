import logging
from typing import List, Dict
import pandas as pd
import numpy as np

# Assuming we can fetch historical data to calculate true correlation
try:
    from tradingagents.strategy_lab.backtest_engine import fetch_ohlcv
except ImportError:
    fetch_ohlcv = None

logger = logging.getLogger("correlation_engine")

class PortfolioCorrelationGuard:
    """
    Prevents the multi-agent system from taking highly correlated 
    directional bets (e.g., buying 5 different tech stocks simultaneously)
    which would bypass individual symbol risk limits and expose the portfolio
    to catastrophic sector-wide drawdown.
    """
    def __init__(self, max_portfolio_correlation: float = 0.75):
        self.max_correlation = max_portfolio_correlation
        self.returns_cache: Dict[str, pd.Series] = {}

    def _get_returns(self, symbol: str) -> pd.Series:
        if symbol not in self.returns_cache:
            try:
                if fetch_ohlcv is not None:
                    # Fetch last 30 days of data
                    df = fetch_ohlcv(symbol, period="30d", interval="1d")
                    ret = df['close'].pct_change().dropna()
                    self.returns_cache[symbol] = ret
                else:
                    self.returns_cache[symbol] = pd.Series(dtype=float)
            except Exception as e:
                logger.warning(f"Failed to fetch correlation data for {symbol}: {e}")
                self.returns_cache[symbol] = pd.Series(dtype=float)
        
        return self.returns_cache[symbol]

    def evaluate_correlation_impact(self, new_symbol: str, existing_symbols: List[str]) -> bool:
        """
        Returns True if the trade is SAFE (correlation below threshold).
        Returns False if the trade is REJECTED (exceeds max correlation limit).
        """
        if not existing_symbols:
            return True # First position in portfolio, no correlation risk
            
        new_returns = self._get_returns(new_symbol)
        if new_returns.empty:
            # If we don't have data, we fail open cautiously but log it
            logger.debug(f"Correlation data missing for {new_symbol}, passing by default.")
            return True

        correlations = []
        for open_sym in existing_symbols:
            open_returns = self._get_returns(open_sym)
            if open_returns.empty:
                continue
                
            # Align the two series by index
            aligned = pd.concat([new_returns, open_returns], axis=1).dropna()
            if len(aligned) > 5: # Need minimum data points
                corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
                if not np.isnan(corr):
                    correlations.append(corr)

        if not correlations:
            return True

        # Check average correlation against the existing portfolio
        avg_corr = sum(correlations) / len(correlations)
        max_corr = max(correlations)
        
        logger.info(f"[CORRELATION] {new_symbol} vs Portfolio -> Avg: {avg_corr:.2f}, Max Pair: {max_corr:.2f}")

        # If it correlates highly with the overall portfolio direction, reject it
        if avg_corr > self.max_correlation:
            logger.error(f"🛑 Correlation Guard Rejected {new_symbol}: Avg correlation {avg_corr:.2f} > limit {self.max_correlation}")
            return False
            
        return True

