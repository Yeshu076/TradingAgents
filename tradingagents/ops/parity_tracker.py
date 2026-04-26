import json
import logging
from pathlib import Path
from typing import Dict, List

import pandas as pd
from tradingagents.strategy_lab.backtest_engine import fetch_ohlcv

logger = logging.getLogger("parity_tracker")

class ParityTracker:
    """
    Reads the execution journal to measure the delta between theoretical backtest PnL 
    and actual live/paper execution. Helps identify silent strategy decay due to 
    invisible slippage or execution latency.
    """
    def __init__(self, journal_path: str = "logs/execution_journal.jsonl"):
        self.journal_path = Path(journal_path)

    def load_filled_trades(self) -> pd.DataFrame:
        if not self.journal_path.exists():
            return pd.DataFrame()
            
        records = []
        with open(self.journal_path, 'r') as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    # Support multiple formats; standard is "status": "simulated_filled" / "FILLED"
                    status = data.get("status", "").upper()
                    if "FILL" in status:
                        timestamp = data.get("timestamp") or data.get("ts")
                        records.append({
                            "timestamp": pd.to_datetime(timestamp),
                            "symbol": data.get("symbol"),
                            "side": data.get("side"),
                            "quantity": data.get("quantity"),
                            "price": data.get("price", 0.0),
                            "action": data.get("action"),
                            "mode": data.get("mode")
                        })
                except Exception:
                    pass
                    
        df = pd.DataFrame(records)
        if not df.empty:
            df = df.set_index("timestamp").sort_index()
        return df

    def analyze_slippage_decay(self, symbol: str) -> Dict[str, float]:
        """
        Simulates theoretical entry at the closing price of the journal's minute bar
        vs the actual recorded price.
        """
        df = self.load_filled_trades()
        if df.empty:
            return {"error": "No execution records found."}
            
        df_sym = df[df["symbol"] == symbol]
        if df_sym.empty:
            return {"error": f"No filled trades for {symbol}."}
            
        # Normally you would fetch minute-level data here. We mock with 1d for simplicity 
        # unless minute data is explicitly provided by the infrastructure.
        try:
            market_data = fetch_ohlcv(symbol, period="7d", interval="1m")
        except Exception as e:
            return {"error": f"Failed to fetch market data: {e}"}

        drift_deltas = []
        avg_price = 0.0
        
        for idx, row in df_sym.iterrows():
            if idx.tz is not None:
                idx = idx.tz_convert(None)
                
            # Nearest minute candle
            nearest_idx = market_data.index.get_indexer([idx], method="nearest")[0]
            if nearest_idx >= 0:
                expected_close = market_data['close'].iloc[nearest_idx]
                actual_price = row['price']
                
                if actual_price > 0:
                    drift = abs(expected_close - actual_price) / expected_close
                    drift_deltas.append(drift)
        
        if not drift_deltas:
            return {"status": "insufficient_data"}
            
        mean_drift = sum(drift_deltas) / len(drift_deltas)
        max_drift = max(drift_deltas)
        
        return {
            "symbol": symbol,
            "mean_slippage_pct": mean_drift * 100,
            "max_slippage_pct": max_drift * 100,
            "trade_count": len(drift_deltas),
            "parity_status": "OK" if mean_drift < 0.005 else "DRIFT_WARNING"
        }

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    tracker = ParityTracker()
    res = tracker.analyze_slippage_decay("AAPL")
    print("Parity Check Result:", res)


