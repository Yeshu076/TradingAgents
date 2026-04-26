import os
import json
import time
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.agents.utils.memory import FinancialSituationMemory

def run_simulation():
    print("="*60)
    print("🚀 STARTING END-TO-END TRADING FIRM SIMULATION 🚀")
    print("="*60)
    
    # 1. Setup Custom Config
    config = DEFAULT_CONFIG.copy()
    config["max_debate_rounds"] = 1
    config["instrument_type"] = "equity"
    
    config["data_vendors"] = {
        "core_stock_apis": "yfinance",
        "technical_indicators": "yfinance",
        "fundamental_data": "yfinance",
        "news_data": "yfinance",
    }
    
    print("\n[INIT] Instantiating TradingAgentsGraph with all sub-agents (Macro, Capital Allocator, Analysts, Trader)...")
    ta = TradingAgentsGraph(debug=True, config=config)
    
    # 2. Mocking an execution flow for an asset
    symbols_to_test = [
        ("AAPL", "equity")
    ]
    
    for symbol, inst_type in symbols_to_test:
        print(f"\n[{symbol}] === Initiating Multi-Agent Analysis Cycle ===")
        ta.config["instrument_type"] = inst_type
        
        # Inject some mock telemetry
        ta.latest_state = {
            "instrument_metadata": {
                "bot_telemetry": {
                    "dhan_nifty": {"pnl": 1500, "drawdown": 0.01},
                    "delta_crypto": {"pnl": -500, "drawdown": 0.05},
                    "forex_mt5": {"pnl": 300, "drawdown": 0.02}
                }
            }
        }
        
        # Forward Propagate
        date_str = datetime.now().strftime("%Y-%m-%d")
        print(f"[{symbol}] Running propagation for {date_str}...")
        
        # Capture time to see how long Graph takes
        start_time = time.time()
        final_state, decision = ta.propagate(symbol, date_str, instrument_type=inst_type)
        elapsed = time.time() - start_time
        
        print(f"\n[{symbol}] === Analysis Cycle Completed in {elapsed:.2f}s ===")
        print(f"[{symbol}] FINAL OUTCOME:\n", decision)
        
        print(f"\n[{symbol}] Capital Allocation Decision:")
        print(json.dumps(final_state.get("portfolio_allocation", {}), indent=2))
        
        # 3. Simulate RAG Journaling
        print(f"\n[{symbol}] === Simulating Live Trade Settlement & Machine Learning ===")
        print(f"[{symbol}] Fake trade executed based on intent. 2 hours later, trade hits Take Profit...")
        
        # Provide a positive PnL to reinforce the neural memory
        mock_pnl = 1450.0  # $1450 profit
        
        print(f"[{symbol}] Adding RAG Reflection (outcome: {mock_pnl}) into BM25 Persistent Memory...")
        ta.reflect_and_remember(mock_pnl)
        
        print(f"[{symbol}] Examining Local DataStore:")
        fsm = ta.memory # Use the graph's memory instance directly
        if hasattr(fsm, 'documents'):
            print(f"[{symbol}] Total Journal Entries in JSON RAG DB: {len(fsm.documents)}")
            if len(fsm.documents) > 0:
                print(f"[{symbol}] Most recent entry snippets:")
                print(str(fsm.documents[-1])[:200] + "...")
        print("="*60)

if __name__ == "__main__":
    run_simulation()
