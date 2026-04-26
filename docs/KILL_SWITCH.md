# Kill Switch — Manual Procedure

> **This document is for use during a live trading incident.**  
> Read the full procedure before a crisis, not during one.

---

## What the Kill Switch Does

When activated, the system:
1. Sets `SYSTEM_HALT=1` in Redis — all `execute_trade()` calls check this before placing orders
2. Calls `cancel_all_orders()` on every active broker (Delta, Dhan, MT5)
3. Logs a `kill_switch_activated` audit event to the decision journal
4. Sends a PagerDuty + Telegram alert

The kill switch does **not**:
- Close existing open positions (use `force_close_nifty_positions()` for Nifty options)
- Stop the daemon scheduler (send `SIGTERM` to the process for that)

---

## Automated Triggers

The kill switch fires automatically when:

| Condition | Threshold env var | Default |
|-----------|-------------------|---------|
| Daily P&L loss exceeds cap | `MAX_DAILY_LOSS_USD` | $1,000 |
| Stale data feed detected | N/A — always active in live mode | — |
| Redis `SYSTEM_HALT` key set externally | — | — |

---

## Manual Activation

### Option 1: Redis (fastest — applies within 1 trade cycle)

```bash
redis-cli SET SYSTEM_HALT 1
```

All `execute_trade()` calls will block immediately. The daemon's liveness check will also engage.

### Option 2: Python script

```python
import redis, os
r = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"), port=int(os.getenv("REDIS_PORT", 6379)))
r.set("SYSTEM_HALT", "1")
print("Kill switch SET")
```

### Option 3: Via the daemon (cancel all + halt)

```bash
# Send the daemon a forced shutdown; it will cancel all orders gracefully
pkill -f "tradingagents.ops.daemon"

# Then set Redis halt to prevent any stale process from submitting orders
redis-cli SET SYSTEM_HALT 1
```

---

## Manual Cancellation per Broker

If the kill switch doesn't reach a broker (network partition, broker offline):

### Delta Exchange

```python
from tradingagents.execution.delta_broker import DeltaBroker
b = DeltaBroker()
cancelled = b.cancel_all_orders()
print(f"Cancelled {len(cancelled)} Delta orders")
```

### Dhan (Nifty Options)

```python
from tradingagents.execution.dhan_broker import DhanBroker
b = DhanBroker()
cancelled = b.cancel_all_orders()
print(f"Cancelled {len(cancelled)} Dhan orders")
```

### MT5 (XAUUSD)

```python
from tradingagents.execution.mt5_broker import MT5Broker
b = MT5Broker()
cancelled = b.cancel_all_orders()
print(f"Cancelled {len(cancelled)} MT5 orders")
```

---

## Force-Close All Nifty Positions (EOD Emergency)

```python
from tradingagents.ops.daemon import force_close_nifty_positions
force_close_nifty_positions()
```

This is also scheduled automatically at **15:20 IST** daily.

---

## Deactivating the Kill Switch

Only deactivate after confirming the root cause is resolved.

```bash
redis-cli DEL SYSTEM_HALT
# Verify no halt:
redis-cli GET SYSTEM_HALT  # should return (nil)
```

Then restart the daemon:

```bash
python -m tradingagents.ops.daemon
```

---

## Post-Incident Checklist

- [ ] All open orders cancelled on all 3 brokers
- [ ] Positions reconciled against broker account pages manually
- [ ] `audit_trail/trade_decisions.jsonl` reviewed for last N transactions
- [ ] Redis kill-switch key removed
- [ ] Root cause documented
- [ ] `MAX_DAILY_LOSS_USD` or position limits tightened if needed
- [ ] Paper trading re-run to validate fix before going live again
