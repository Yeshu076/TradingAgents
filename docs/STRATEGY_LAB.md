# Strategy Lab

The Strategy Lab (`tradingagents/strategy_lab/`) encapsulates the iterative, autonomous AI loop that turns English market reports into executable, rigorously checked Python strategies.

## Workflow

1. **`quant_orchestrator.py`**
   - Woken up by the Daemon.
   - Triggers the `QuantAgent`.
   - The Quant agent is prompted to output a raw Python file relying strictly on `vectorbt` and `pandas`.
   - The Orchestrator saves the script to `strategy_lab_results/scripts/` and runs it in a sandboxed `subprocess`.
   - It captures `stdout`/`stderr` JSON dictionaries containing the script's `Sharpe Ratio`, `Win Rate`, and `Max Drawdown`.
   - **Feedback Loop**: If the script crashes, throws syntax errors, or returns a Sharpe < 1.0, the stack trace is fed back into the `QuantAgent` up to 3 times to self-heal the code.
   - **Promotion**: Scripts that pass >1.0 Sharpe are copied to `strategy_lab_results/approved_scripts/`.

2. **`dynamic_runner.py` & `live_pricing.py`**
   - Polls the `approved_scripts/` directory every 15 minutes.
   - Fetches live bid/ask prices from Dhan/Delta.
   - Feeds live data to the promoted strategy.
   - If the strategy yields a `"BUY"` or `"SELL"`, dynamically sizes the trade using current portfolio boundaries and pipelines it directly to `execute_trade`.

3. **`portfolio_monitor.py` (Kill-Switch)**
   - Autonomously iterates over historical trades on the Paper/Live wallets.
   - Reconciles the PnL strictly to the strategy that issued it.
   - If a promoted strategy begins failing in live/forward testing and reaches `-X` USD Drawdown, the monitor instantly demotes it, rips it out of the `approved_scripts/` directory, and issues a Discord webhook alert.