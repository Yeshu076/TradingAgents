import os
import argparse
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from tradingagents.ops.daemon import start_daemon

def main():
    parser = argparse.ArgumentParser(description="TradingAgents Daemon Entrypoint")
    parser.add_argument("--symbols", type=str, required=True, help="Comma separated list of symbols")
    parser.add_argument("--cron", type=str, default="0 * * * *", help="Legacy cron, not used directly by market-aware scheduler but required by function spec")
    parser.add_argument("--debug", action="store_true")
    
    args = parser.parse_args()
    
    symbols = [s.strip() for s in args.symbols.split(",")]
    
    # We load default analysts (or configure them)
    analysts = ["market", "social", "news", "fundamentals"]
    
    print(f"Starting DAEMON for symbols: {symbols}")
    start_daemon(symbols=symbols, cron_expr=args.cron, analysts=analysts, debug=args.debug)

if __name__ == "__main__":
    main()
