# Runbook: TradingAgents Production Operations

---

## 1. Starting the System

### Pre-flight Checklist

Before starting live trading:

- [ ] `.env` file loaded with all required credentials (see [CONFIGURATION.md](CONFIGURATION.md))
- [ ] Redis running: `redis-cli ping` → `PONG`
- [ ] MT5 bridge process running (if trading XAUUSD)
- [ ] Dhan token fresh: `python -c "from tradingagents.ops.daemon import check_dhan_token_health; check_dhan_token_health()"`
- [ ] Paper mode test run successful: `TRADINGAGENTS_PAPER=true python -m tradingagents.cli.main`
- [ ] Kill switch clear: `redis-cli GET SYSTEM_HALT` → `(nil)`

### Start Daemon

```bash
# Foreground (for monitoring)
python -m tradingagents.ops.daemon

# Background (production)
nohup python -m tradingagents.ops.daemon > logs/daemon.log 2>&1 &
```

---

## 2. Scheduled Jobs

| Time (IST) | Job | Purpose |
|------------|-----|---------|
| 09:00 | `check_dhan_token_health` | Alert if Dhan JWT expires within 24h |
| 15:20 | `force_close_nifty_positions` | Emergency EOD close for all Nifty options |
| Daily | `StateReconciliationService` | Ghost position detection across all brokers |
| Continuous | `BrokerSessionManager` | Token refresh (every 5 min check) |

---

## 3. Daily Operations

### Morning (Before Market Open)

1. Verify daemon is running: `pgrep -f "tradingagents.ops.daemon"`
2. Check Redis liveness: `redis-cli ping`
3. Check Dhan token TTL: `redis-cli GET DHAN_TOKEN_EXPIRY`
4. Review overnight journal: `tail -50 trade_decisions.jsonl`

### Market Hours

- Monitor Telegram/Discord alerts for trade notifications and risk warnings
- Check for `fill_verification_failed` alerts — these require manual order status check in broker UI
- Watch for `ghost_position_detected` — means broker has a position not tracked in local DB

### Evening (After Market Close 15:30 IST)

1. Confirm `force_close_nifty_positions` ran successfully (check daemon log around 15:20)
2. Run reconciliation manually if any alerts were triggered:
   ```python
   from tradingagents.execution.reconciliation import StateReconciliationService
   StateReconciliationService().run_reconciliation()
   ```
3. Review daily P&L: `redis-cli GET DAILY_PNL`
4. Rotate Dhan token if expiring within 48h

---

## 4. Incident Response

### Incident: Trade Not Executing

**Symptoms:** Signal generated, no order placed, no error in logs

**Checklist:**
1. Is kill switch active? `redis-cli GET SYSTEM_HALT` — if `1`, see [KILL_SWITCH.md](KILL_SWITCH.md)
2. Is `TRADINGAGENTS_ALLOW_LIVE_TRADING=true`?
3. Did risk gate reject? Search journal: `grep "rejected" trade_decisions.jsonl | tail -5`
4. Did margin check fail? Search: `grep "Margin" trade_decisions.jsonl | tail -5`
5. Did dedup block? Same intent submitted within 5 mins? Check `TRADINGAGENTS_DEDUP_WINDOW_S`

---

### Incident: Ghost Position Detected

**Symptoms:** Reconciliation alert — broker shows position, local DB does not

**Cause:** Order filled at broker but fill verification failed, or local DB was reset

**Steps:**
1. Check broker UI for the position
2. Manually insert into local DB:
   ```python
   from tradingagents.execution.position_manager import PositionManager
   pm = PositionManager.from_env()
   pm.place_order(symbol="BTCUSD", side="BUY", quantity=0.1, price=30000.0, instrument_type="spot")
   ```
3. If position needs to be closed, close via broker UI first, then call `pm.close_symbol("BTCUSD")`

---

### Incident: Dhan Token Expired

**Symptoms:** `401 Unauthorized` from Dhan API, `DHAN_TOKEN_EXPIRED` alert, no Nifty orders

**Steps:**
1. Log into Dhan web portal and generate a new token
2. Update `.env`: `DHAN_ACCESS_TOKEN=<new_token>`
3. Restart daemon: `pkill -f "tradingagents.ops.daemon" && python -m tradingagents.ops.daemon`
4. Verify: `python -c "from tradingagents.ops.daemon import check_dhan_token_health; check_dhan_token_health()"`

---

### Incident: Max Daily Loss Hit

**Symptoms:** `MAX_DAILY_LOSS_BREACHED` alert, kill switch auto-activated

**Steps:**
1. Verify: `redis-cli GET SYSTEM_HALT` → `1`
2. Review today's journal: `grep "trade" trade_decisions.jsonl | python -m json.tool | grep pnl`
3. Assess positions — close any open risk manually via broker UI
4. Update `MAX_DAILY_LOSS_USD` if needed
5. Deactivate kill switch only when safe: `redis-cli DEL SYSTEM_HALT`
6. Do NOT restart live trading today; run in paper mode to validate system state

---

### Incident: Redis Down

**Symptoms:** Risk caps not enforced, liveness checks bypassed, kill switch unreachable

**Impact:** System degrades gracefully — risk monitors log warnings but execution continues

**Steps:**
1. Restart Redis: `redis-server --daemonize yes`
2. Check RedisError logs in daemon output
3. If Redis stays down, consider halting the daemon manually as a safety measure:
   ```bash
   pkill -f "tradingagents.ops.daemon"
   ```

---

## 5. Logs and Audit Trail

| Log | Location | Contents |
|-----|----------|---------|
| Trade audit | `trade_decisions.jsonl` | Every trade attempt, result, rejection reason |
| Daemon log | `logs/daemon.log` | Scheduler events, token health, errors |
| Fill verification | `trade_decisions.jsonl` | `fill_verification_failed` events |
| Reconciliation | `logs/reconciliation.log` | Ghost positions, quantity mismatches |

**Search for all failures today:**
```bash
grep "failed\|rejected\|error" trade_decisions.jsonl | python -m json.tool
```

---

## 6. Paper-to-Live Migration Checklist

> Complete every item in order. Do not skip any step.

- [ ] Run `python -m pytest tests/ -q` — all tests passing
- [ ] Paper mode live for ≥5 trading days with stable P&L
- [ ] Reconciliation shows no ghost positions in paper mode
- [ ] Dhan, Delta, MT5 API credentials tested individually:
  ```bash
  python -c "from tradingagents.execution.delta_broker import DeltaBroker; print(DeltaBroker().list_positions())"
  python -c "from tradingagents.execution.dhan_broker import DhanBroker; print(DhanBroker().list_positions())"
  ```
- [ ] Kill switch tested manually (`redis-cli SET SYSTEM_HALT 1`, verify orders blocked, `DEL SYSTEM_HALT`)
- [ ] `MAX_DAILY_LOSS_USD` set to conservative value (≤ 1% of capital)
- [ ] Telegram/PagerDuty alerts verified working
- [ ] `TRADINGAGENTS_PAPER=false` and `TRADINGAGENTS_ALLOW_LIVE_TRADING=true` in `.env`
- [ ] Start with minimum position sizes for first week
- [ ] Engineer on-call for first 3 live trading days

---

## 7. Rollback Procedure

If live trading causes unexpected issues:

```bash
# 1. Activate kill switch immediately
redis-cli SET SYSTEM_HALT 1

# 2. Stop daemon
pkill -f "tradingagents.ops.daemon"

# 3. Cancel all open orders manually via broker UIs

# 4. Switch back to paper mode
sed -i 's/TRADINGAGENTS_PAPER=false/TRADINGAGENTS_PAPER=true/' .env
sed -i 's/TRADINGAGENTS_ALLOW_LIVE_TRADING=true/TRADINGAGENTS_ALLOW_LIVE_TRADING=false/' .env

# 5. Restart in paper mode
python -m tradingagents.ops.daemon
```
