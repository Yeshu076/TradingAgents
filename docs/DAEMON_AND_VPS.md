# Daemon and VPS Deployment

The `ops/` module handles all headless 24/7 behaviors necessary for continuous unattended execution on a remote server.

## Daemon Design
The daemon runs on `APScheduler`, maintaining three distinct cyclical schedules:
1. **Market Analysis Phase**: Runs on a configurable cron (e.g., hourly). Wakes up the multi-agent graph to analyze the live market data, synthesize the news, and build human-readable reports.
2. **Quant Generator Phase**: Runs nightly (e.g., 2:00 AM). Wakes up the `QuantAgent` specifically to generate, backtest, and sandbox new `vectorbt` strategies and move the profitable ones to `approved_scripts/`.
3. **Live Execution Phase**: Runs consistently (e.g., every 15 minutes). The daemon polls all scripts in `approved_scripts/`, feeds them the real-time Delta/Dhan APIs, sizes the quantities with `portfolio_monitor.py`, and transmits the trades.

## VPS Deployment Strategy
Because `vectorbt`, `numba`, and older numeric Python packages crash violently in `Python 3.14+`, we encapsulate the daemon in a strict `python:3.10-slim` Docker container.

### Step-by-Step
1. Copy `.env.example` -> `.env` and fill the keys.
2. Provide `DISCORD_WEBHOOK_URL` and `TELEGRAM_BOT_TOKEN`.
3. Launch with `docker-compose up --build -d`.
4. The system natively detaches from the CLI and pipes all internal logs up to standard out, while executing autonomously under the hood.