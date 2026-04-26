# Portfolio Heat Monitor — TradingAgents

**Feature:** F-05  
**Status:** ✅ Production-ready  
**Modules:** `execution/position_manager.py`, `execution/global_risk.py`, `execution/engine.py`

---

## Overview

The Portfolio Heat Monitor extends the risk control system with a **portfolio-wide dollar-risk cap**. Unlike per-symbol exposure limits which prevent concentration in a single instrument, the heat monitor prevents the *total open risk* across ALL positions from exceeding a configurable threshold.

**Heat** = the dollar amount that would be lost if every open position hit its stop loss simultaneously.

```
heat(position) = abs(quantity) × |avg_price − stop_loss|
total_heat = Σ heat(position) over all open positions
```

If no stop loss is recorded, a fallback formula is applied:
```
heat(position) = abs(quantity) × avg_price × HEAT_FALLBACK_PCT / 100
```

---

## Why it matters

| Scenario | Per-Symbol Cap | Heat Monitor |
|----------|---------------|--------------|
| 1 position at max exposure | ✅ Catches | ✅ Catches |
| 5 positions each at 20% of max | ✅ Allows | ✅ Allows |
| 20 positions each at 80% of cap | ✅ Allows each | 🛑 Blocks any new one |

Per-symbol caps alone are necessary but **not sufficient** for a production trading system. The heat monitor is the final safety layer.

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `TRADINGAGENTS_MAX_PORTFOLIO_HEAT_USD` | `0` | Max total open postion heat in USD. **0 = disabled.** |
| `TRADINGAGENTS_HEAT_FALLBACK_PCT` | `2.0` | % of notional used as heat when no stop loss is set |

### Recommended production config

```env
TRADINGAGENTS_MAX_PORTFOLIO_HEAT_USD=10000   # Lose max $10k if all stops hit
TRADINGAGENTS_HEAT_FALLBACK_PCT=2.0          # 2% of notional for positions without stop
```

---

## Database Schema

The `positions` table now carries a `stop_loss` column:

```sql
ALTER TABLE positions ADD COLUMN stop_loss REAL NOT NULL DEFAULT 0.0;
```

This migration is **non-destructive** — applied automatically via `_init_db()`.

---

## API

### `PositionManager`

```python
# Get dollar risk for a single position
pm.get_position_heat("BTC") → float

# Get total dollar risk across ALL open positions
pm.get_total_position_heat() → float

# Persist a stop loss (call after opening/modifying a trade)
pm.set_stop_loss("BTC", stop_loss=49_000.0)
```

### `GlobalRiskMonitor`

```python
# Read current heat (from last update)
grm.portfolio_heat → float

# Update after each fill (called automatically by engine)
grm.update_portfolio_heat(total_heat=pm.get_total_position_heat())

# Heat gate is built into evaluate_trade_intent()
grm.evaluate_trade_intent(
    strategy_name="crypto_agent",
    symbol="BTC",
    notional_value=50_000.0,
    proposed_heat=1_000.0,   # ← F-05 kwarg
)
```

---

## Pipeline Position

```
execute_trade()
    │
    ├── Signal HOLD/FLAT → skip
    ├── F-04: Confidence Gate
    ├── Broker resolution
    ├── F-02: Position Sizing           ← sets intent.quantity
    ├── Policy validation (qty/notional)
    │
    ├── GlobalRiskMonitor.evaluate_trade_intent(
    │       ...,
    │       proposed_heat = qty × |entry − stop|   ← F-05
    │   )
    │       ├── Kill switch check
    │       ├── Max daily loss check
    │       ├── F-01: Unrealized drawdown check
    │       ├── F-05: Portfolio heat cap check  ← NEW
    │       ├── Per-symbol exposure check
    │       └── Per-strategy drawdown check
    │
    ├── DeterministicRiskGate
    ├── Liveness check
    ├── Correlation guard
    ├── Deduplication
    └── Paper fill / Live dispatch
            │
            └── After fill:
                ├── pm.set_stop_loss(symbol, stop)    ← F-05: persist stop
                ├── heat = pm.get_total_position_heat()
                └── grm.update_portfolio_heat(heat)   ← F-05: update GRM
```

---

## Safety Guarantees

1. **Disabled by default** (`TRADINGAGENTS_MAX_PORTFOLIO_HEAT_USD=0`) — zero behavior change for existing deployments
2. **No stop loss → fallback heat** — positions without a stop are never treated as zero-risk
3. **Closed positions excluded** — heat auto-recalculates from remaining open positions
4. **Non-blocking error handling** — if heat update fails post-fill, a warning is logged but the fill is NOT rolled back
5. **Redis-persisted** — heat state survives process restarts; won't reset mid-session with open positions
6. **Exactly-at-cap allowed** — the gate uses `>` not `>=`, so a trade that hits exactly the cap is permitted

---

## Testing

```bash
python -m pytest tests/test_portfolio_heat.py -v
# 30 tests, ~5 minutes
```

| Test Group | Count | Coverage |
|-----------|-------|----------|
| `TestStopLossColumn` | 5 | Schema migration, set/get/overwrite |
| `TestGetPositionHeat` | 7 | With stop, short side, fallback, closed, unknown, fractional qty |
| `TestGetTotalPositionHeat` | 5 | Single, multi-position, empty, closed, mixed fallback |
| `TestGRMPortfolioHeat` | 10 | Default zero, update, clamp negative, gate disabled/enabled/at-cap/just-over, zero-proposed, accumulation |
| `TestEngineHeatIntegration` | 3 | Stop persisted after fill, heat updated after fill, gate blocks overheated trade |
