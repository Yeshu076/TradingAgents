# Position Sizing — TradingAgents

**Feature:** F-02  
**Status:** ✅ Production-ready  
**Module:** `tradingagents/execution/position_sizer.py`

---

## Overview

The position sizing engine normalizes risk-per-trade across all instruments and
brokers. Instead of sending the agent's raw lot count to the broker, the engine
computes an order quantity based on:

- Current portfolio equity (from mark-to-market, F-01)
- Configured risk percentage per trade
- Trade-specific stop-loss distance or asset volatility (ATR)

Sizing is **opt-in and backward-compatible.** It is disabled by default
(`TRADINGAGENTS_SIZING_ENABLED=false`). When disabled, the system behaves
exactly as before — the quantity on the `TradeIntent` is used unchanged.

---

## Sizing Modes

### `fixed` (default)

```
sized_qty = clamp(floor(raw_qty, step), min, max)
```

Passes the agent's quantity through, applying only rounding and clamping.
Use this when your strategy already sets explicit lot sizes.

### `percent_equity`

```
sized_qty = (equity × risk_pct/100) / |entry − stop_loss|
```

**Example:** 1% of ₹10,00,000 equity with a ₹500 stop distance = 20 units.

The trade is sized so that **hitting the stop costs exactly `risk_pct` × equity**.
Falls back to `fixed` when entry or stop-loss is missing, or when stop distance is zero.

**Best for:** Equity, options, and FX strategies with well-defined stop levels.

### `volatility_adjusted`

```
sized_qty = (equity × risk_pct/100) / (ATR × atr_multiplier)
```

**Example:** 1% of $100,000 equity with ATR=5 and multiplier=2 = 100 units.

Sizes inversely proportional to recent volatility. High-volatility assets get
smaller positions automatically, stabilizing P&L volatility across the portfolio.

Falls back to `percent_equity` if ATR is unavailable but entry/stop are present.
Falls back to `fixed` as a final backstop.

**Best for:** Crypto and FX where volatility varies significantly day-to-day.

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `TRADINGAGENTS_SIZING_ENABLED` | `false` | Enable the sizer. `false` = pass-through mode |
| `TRADINGAGENTS_SIZING_MODE` | `fixed` | `fixed` \| `percent_equity` \| `volatility_adjusted` |
| `TRADINGAGENTS_RISK_PER_TRADE_PCT` | `1.0` | % of equity to risk per trade |
| `TRADINGAGENTS_ATR_MULTIPLIER` | `2.0` | ATR multiplier (volatility_adjusted mode) |
| `TRADINGAGENTS_MIN_QUANTITY` | `0.01` | Floor on any sized quantity |
| `TRADINGAGENTS_MAX_QUANTITY` | `25.0` | Ceiling (falls back to `TRADINGAGENTS_MAX_ORDER_QTY`) |
| `TRADINGAGENTS_QUANTITY_STEP` | `0.01` | Lot-step rounding (all sized quantities floored to this) |

### Recommended starting config (percent_equity)

```env
TRADINGAGENTS_SIZING_ENABLED=true
TRADINGAGENTS_SIZING_MODE=percent_equity
TRADINGAGENTS_RISK_PER_TRADE_PCT=1.0
TRADINGAGENTS_MIN_QUANTITY=0.01
TRADINGAGENTS_MAX_QUANTITY=25.0
TRADINGAGENTS_QUANTITY_STEP=0.01
```

### ATR supply (volatility_adjusted)

Pass ATR as a `broker_kwargs` key when calling `execute_trade`:

```python
execute_trade(intent, broker="auto", paper=True, atr=float(current_atr))
```

The ATR value is consumed by the sizer and removed from `broker_kwargs` before
broker dispatch.

---

## Execution Pipeline Position

```
execute_trade()
    │
    ├── HOLD/FLAT signal → skip
    ├── F-04: Confidence Gate
    ├── Broker resolution
    ├── F-02: Position Sizing  ◄── HERE (before policy + risk checks)
    ├── Policy validation (max_order_quantity enforced AFTER sizing)
    ├── DeterministicRiskGate
    ├── Liveness check
    ├── GlobalRiskMonitor (notional based on sized qty)
    ├── Correlation guard
    ├── Deduplication
    └── Paper fill / Live dispatch
```

> **Design note:** Sizing runs *before* policy validation so that the
> `max_order_quantity` guard in `ExecutionPolicy` sees the risk-adjusted
> quantity. This prevents the policy from silently allowing an oversized
> position when the sizer is in pass-through mode for edge cases.

---

## Audit Trail

After sizing, the `TradeIntent` carries three new fields visible in the journal:

| Field | Description |
|-------|-------------|
| `raw_quantity` | Agent's original requested quantity |
| `sized_quantity` | Risk-adjusted quantity sent to broker |
| `sizing_mode` | Which mode was used (`fixed`/`percent_equity`/`volatility_adjusted`) |

---

## Safety Guarantees

1. **Zero equity → min_quantity** — always returns a minimal legal order
2. **Zero stop distance → fixed fallback** — never divides by zero
3. **Zero ATR → percent_equity or fixed fallback** — no division by zero
4. **Unknown mode → fixed fallback + error log** — defensive against config errors
5. **Step rounding always floors** — never rounds up into a larger position
6. **Min/max clamping** — enforced after every calculation path
7. **Disabled by default** — no silent behavior change for existing deployments

---

## Testing

```bash
python -m pytest tests/test_position_sizer.py -v
# 38 tests, ~50 seconds
```

Test coverage includes:
- All three sizing modes with multiple parameter combinations
- Edge cases: zero equity, zero stop distance, zero ATR, negative quantities
- Fallback chains: vol_adj → pct_equity → fixed
- `SizerConfig.from_env()` with all env vars
- `TradeIntent` new fields
- Engine integration (disabled, enabled, quantity assertion)
