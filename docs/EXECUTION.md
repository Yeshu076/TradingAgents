# Execution Engine

The Execution engine (`tradingagents/execution/`) provides the deterministic mapping layer between the LLM's non-deterministic `TradeIntent` models and the physical broker REST APIs.

---

## Key Components

### `engine.py: execute_trade()`
The primary entrypoint. It receives a `TradeIntent`, decides where it should be executed based on the instrument type (`broker="auto"`), and enforces sanity rules.

### `router.py` & Brokers
Matches the `instrument_type` (e.g., `options`, `crypto`, `forex`) to the respective instantiated broker class.
- **DhanBroker** — executes options using the Dhan V2 HQ APIs.
- **DeltaBroker** — executes crypto derivatives using the Delta Exchange V2 APIs.
- **MT5ForexBroker** — executes forex (XAUUSD) via the MetaTrader 5 terminal.
- **RedisPublisherBroker** — pub-sub bridge for architectures where broker bots run in separate processes.

### Guardrails
1. **`risk_gate.py` (DeterministicRiskGate)**  
   Validates confidence, risk-reward ratio, and position size against configured minimums.
2. **`deduplication.py` (ExecutionIdempotencyManager)**  
   Maintains a rolling window time-lock on identical executions to prevent duplicate orders.
3. **`policy.py` (ExecutionPolicy)**  
   Enforces maximum daily trade counts, notional values, market hours, and live/paper mode.
4. **`global_risk.py` (GlobalRiskMonitor)**  
   System-wide daily loss guard, per-symbol exposure cap, and per-strategy drawdown guard.

### Local Paper Wallet
All paper-mode executions run against `PositionManager` (`position_manager.py`). It uses SQLite with WAL mode for concurrency safety and tracks orders, positions, realized PnL, and cash.

---

## Emergency Kill Switch

### Overview

`EmergencyKillSwitch` (`killswitch.py`) is the **last line of defence** for real-money trading. When triggered, it:

1. Sets the `EXECUTION_BLOCKED=1` flag in Redis (blocks all subsequent `GlobalRiskMonitor.evaluate_trade_intent()` calls).
2. Iterates every open position in the paper wallet, resolves its broker, and calls `cancel_all_orders()` on each unique broker.
3. Marks the system as halted (`is_halted() → True`).
4. Fires a `send_critical_alert()` notification (PagerDuty / Discord / Telegram).

### Trigger Paths

| Trigger | How |
|---------|-----|
| Redis `SYSTEM_HALT` pub-sub | Automatic; `listen_for_fatal_events()` runs in a background daemon thread |
| `GlobalRiskMonitor` daily-loss breach | `_trigger_killswitch()` publishes to `SYSTEM_HALT` channel |
| `DataLivenessMonitor` stale feed | publishes to `SYSTEM_HALT` channel |
| Programmatic | `ks.trigger_manual("REASON")` from anywhere in code |

### Resilience Guarantees

- **Redis-optional**: if Redis is unavailable at startup, the kill switch logs a warning and degrades gracefully. `trigger_manual()` still works and `_execute_emergency_shutdown()` still runs.
- **Synchronous**: all broker `cancel_all_orders()` calls are _synchronous blocking_ (no `async/await`). This ensures the shutdown procedure completes even if the event loop is not running.
- **Idempotent**: calling trigger a second time is a no-op — `_halt_event` is already set and the method returns early without issuing duplicate broker calls.
- **Broker-failure tolerant**: if a broker's `cancel_all_orders()` raises an exception or returns `not_supported`, the kill switch logs the failure, increments `failed_count`, and continues with the remaining brokers.

### Usage

#### Daemon Integration (recommended)
```python
# ops/daemon.py
from tradingagents.execution.killswitch import start_killswitch_thread

ks = start_killswitch_thread()  # arms listener in daemon thread
```

#### Manual Trigger (programmatic / testing)
```python
from tradingagents.execution.killswitch import EmergencyKillSwitch

ks = EmergencyKillSwitch()
ks.trigger_manual("MAX_DRAWDOWN_BREACHED")
assert ks.is_halted()
```

### Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `REDIS_HOST` | `127.0.0.1` | Redis server hostname |
| `REDIS_PORT` | `6379` | Redis server port |
| `REDIS_PASSWORD` | _(empty)_ | Redis auth password (optional) |
| `KILLSWITCH_CANCEL_TIMEOUT_SECONDS` | `10` | Max seconds per broker `cancel_all_orders()` call |

### Known Limitations

1. **Dhan `cancel_all_orders` is not yet fully implemented** — it returns `{"status": "not_supported"}`. The kill switch handles this gracefully (logs + increments `failed_count`) but Dhan bracket orders will _not_ be cancelled automatically. Implement individual order cancellation for Dhan as the next priority.
2. **MT5 only cancels _pending_ (limit/stop) orders** — market orders fill instantly in MT5 and cannot be cancelled. Use `close_symbol_position()` to flatten open positions.
3. **paper wallet state only** — the kill switch reads positions from the SQLite `PositionManager`. In live mode, the live broker may have positions not reflected in the paper wallet (e.g. manually placed orders). Run `StateReconciliationService` regularly to close this gap.

---

## Broker Adapter: MT5ForexBroker

`MT5ForexBroker` (`forex_broker.py`) fully implements the `BrokerBase` ABC:

| Method | Behaviour |
|--------|-----------|
| `place_market_order()` | Sends an immediate-fill market order via `mt5.order_send()` |
| `list_positions()` | Returns all open MT5 positions as dicts |
| `close_symbol_position()` | Flattens the given symbol by sending a counter-direction market order for each open position |
| `cancel_all_orders()` | Cancels all pending (limit/stop) MT5 orders, optionally filtered by symbol |
| `fetch_positions()` | Alias for `list_positions()` (legacy compatibility) |
| `fetch_order_status()` | Checks MT5 deal history to determine fill status |

If the MT5 terminal is not running or the package is not installed, all methods return a safe error dict rather than raising `NotImplementedError`.