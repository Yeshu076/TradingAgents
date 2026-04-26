# Configuration Reference

All configuration is done via environment variables. No values are hardcoded.

> **Tip:** Use a `.env` file (excluded from git) and load with `python-dotenv` or `direnv`.

---

## Required for Live Trading

| Variable | Example | Description |
|----------|---------|-------------|
| `DELTA_API_KEY` | `abcdef...` | Delta Exchange API key |
| `DELTA_API_SECRET` | `xxxxxxx` | Delta Exchange API secret |
| `DHAN_CLIENT_ID` | `1234567` | Dhan client/customer ID |
| `DHAN_ACCESS_TOKEN` | `eyJ...` | Dhan JWT access token (refresh daily) |
| `MT5_ACCOUNT` | `12345678` | MetaTrader 5 account number |
| `MT5_PASSWORD` | `secret` | MetaTrader 5 account password |
| `MT5_SERVER` | `ICMarkets-Demo` | MT5 broker server name |

---

## Broker Selection & Mode

| Variable | Default | Description |
|----------|---------|-------------|
| `TRADINGAGENTS_PAPER` | `true` | `true` = paper mode, `false` = live trading |
| `TRADINGAGENTS_ALLOW_LIVE_TRADING` | `false` | Second gate — must be `true` to allow live orders |
| `TRADINGAGENTS_BROKER` | `auto` | Override broker: `delta`, `dhan`, `mt5`, `auto` |
| `TRADINGAGENTS_INSTRUMENT_TYPE` | `spot` | Default instrument type for signal routing |

---

## Database & State

| Variable | Default | Description |
|----------|---------|-------------|
| `TRADINGAGENTS_DB_PATH` | `paper_positions.db` | SQLite file for PositionManager |
| `TRADINGAGENTS_DB_WAL` | `true` | Enable WAL mode for concurrent access |
| `REDIS_HOST` | `localhost` | Redis host (for risk state, liveness, kill switch) |
| `REDIS_PORT` | `6379` | Redis port |
| `REDIS_PASSWORD` | _(none)_ | Redis auth password (optional) |

---

## Risk Management

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_DAILY_LOSS_USD` | `1000.0` | Global max daily loss — triggers kill switch when breached |
| `MAX_SYMBOL_EXPOSURE_USD` | `5000.0` | Max notional per symbol across all strategies |
| `MAX_STRATEGY_DD_PCT` | `5.0` | Max per-strategy daily drawdown % |
| `ALLOCATION_{STRATEGY}` | `10000.0` | Capital allocated per strategy (e.g. `ALLOCATION_NIFTY_TREND`) |
| `TRADINGAGENTS_MAX_OPEN_POSITIONS` | `20` | Max concurrent open positions across all brokers |
| `TRADINGAGENTS_MAX_ORDER_QUANTITY` | `100` | Max quantity per single order |
| `TRADINGAGENTS_MAX_ORDER_NOTIONAL` | `50000` | Max notional (price × qty) per order |

---

## Execution Engine

| Variable | Default | Description |
|----------|---------|-------------|
| `TRADINGAGENTS_LIVENESS_CHECK_ENABLED` | `true` (live) / `false` (paper) | Validate data feed liveness before execution |
| `TRADINGAGENTS_CORRELATION_CHECK_ENABLED` | `true` | Portfolio correlation guard |
| `TRADINGAGENTS_MAX_CORRELATION` | `0.75` | Max allowed avg pairwise correlation |
| `TRADINGAGENTS_MARGIN_CHECK_ENABLED` | `true` | Pre-trade margin validation via broker API |
| `TRADINGAGENTS_MARGIN_BUFFER_PCT` | `10` | % headroom on top of raw margin requirement |
| `TRADINGAGENTS_DEDUP_ENABLED` | `true` | Hash-based duplicate order prevention |
| `TRADINGAGENTS_DEDUP_WINDOW_S` | `300` | Dedup window (seconds) |
| `TRADINGAGENTS_STARTING_BALANCE` | `0` | Starting balance for drawdown % (pulled from PositionManager if 0) |

---

## Fill Verification

| Variable | Default | Description |
|----------|---------|-------------|
| `TRADINGAGENTS_FILL_POLL_INTERVAL_S` | `5` | Seconds between fill verification polls (live only) |
| `TRADINGAGENTS_FILL_MAX_POLLS` | `6` | Max polls before logging `fill_verification_failed` |

---

## Order Chasing

| Variable | Default | Description |
|----------|---------|-------------|
| `TRADINGAGENTS_ORDER_CHASE_ENABLED` | `true` | Enable limit order chasing |
| `TRADINGAGENTS_CHASE_INTERVAL_S` | `0.5` | Seconds between chase ticks |
| `TRADINGAGENTS_CHASE_MAX_TICKS` | `10` | Max cancel-replace iterations before abandonment |

---

## Session Manager

| Variable | Default | Description |
|----------|---------|-------------|
| `TRADINGAGENTS_SESSION_MANAGER_ENABLED` | `true` | Enable background token refresh daemon |
| `TRADINGAGENTS_SESSION_CHECK_INTERVAL_S` | `300` | Seconds between token expiry checks |
| `TRADINGAGENTS_SESSION_REFRESH_WINDOW_S` | `1800` | Refresh when token TTL < this value (seconds) |

---

## LLM Graph (Agent Orchestration)

| Variable | Default | Description |
|----------|---------|-------------|
| `TRADINGAGENTS_LLM_TIMEOUT_S` | `60` | Per-LLM-call request timeout (seconds) |
| `TRADINGAGENTS_CYCLE_TIMEOUT_S` | `600` | Max time for full agent cycle before HOLD fallback (10 min) |
| `TRADINGAGENTS_MAX_LLM_COST_USD` | `2.00` | Future: max USD spend per cycle |
| `OPENAI_API_KEY` | _(required for OpenAI)_ | |
| `ANTHROPIC_API_KEY` | _(required for Anthropic)_ | |
| `GOOGLE_API_KEY` | _(required for Gemini)_ | |

---

## Notifications

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | _(none)_ | Telegram bot token for alerts |
| `TELEGRAM_CHAT_ID` | _(none)_ | Telegram chat/channel ID |
| `DISCORD_WEBHOOK_URL` | _(none)_ | Discord webhook for notifications |
| `PAGERDUTY_ROUTING_KEY` | _(none)_ | PagerDuty routing key for critical alerts |

---

## Daemon Scheduler

| Variable | Default | Description |
|----------|---------|-------------|
| `TRADINGAGENTS_EOD_CLOSE_TIME` | `15:20` | IST time for Nifty forced EOD close |
| `TRADINGAGENTS_TOKEN_CHECK_TIME` | `09:00` | IST time for Dhan JWT health check |

---

## Minimum Production `.env` Template

```ini
# === BROKER CREDENTIALS ===
DELTA_API_KEY=your_key_here
DELTA_API_SECRET=your_secret_here
DHAN_CLIENT_ID=your_client_id
DHAN_ACCESS_TOKEN=your_jwt_token
MT5_ACCOUNT=your_account_number
MT5_PASSWORD=your_password
MT5_SERVER=YourBroker-Live

# === LLM ===
OPENAI_API_KEY=sk-...

# === MODE ===
TRADINGAGENTS_PAPER=false
TRADINGAGENTS_ALLOW_LIVE_TRADING=true

# === RISK ===
MAX_DAILY_LOSS_USD=500.0
MAX_SYMBOL_EXPOSURE_USD=3000.0
TRADINGAGENTS_MAX_OPEN_POSITIONS=10

# === INFRA ===
REDIS_HOST=localhost
REDIS_PORT=6379

# === ALERTS ===
TELEGRAM_BOT_TOKEN=123456:ABC-DEF
TELEGRAM_CHAT_ID=-100123456789
PAGERDUTY_ROUTING_KEY=your_routing_key
```
