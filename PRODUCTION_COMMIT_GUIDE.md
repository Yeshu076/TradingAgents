# TradingAgents Production Commit Guide

## Purpose
This document explains what was implemented, how to validate it, and how to prepare a clean GitHub commit for the production-ready multi-asset upgrade.

## Scope Of Changes

### 1. Multi-asset runtime support
- Instrument-aware flow for equity, forex, crypto, and options.
- Propagation and agent state now carry:
  - `instrument_type`
  - `instrument_metadata`

### 2. Crypto derivatives provider hardening
- Added Delta Exchange provider integration (primary), with Binance/Bybit fallback chain.
- Routing order (default):
  - `delta,binance,bybit`

### 3. Nifty options provider hardening
- Added Dhan option-chain provider integration (primary) with yfinance fallback.
- Routing order (default):
  - `dhan,yfinance`
- Nifty alias normalization for snapshot path (`NIFTY -> ^NSEI`).

### 4. Structured order intent pipeline
- Added extraction + validation layer for final decisions.
- Generates confidence, warnings, and consistency metadata.

### 5. Autonomous strategy lab
- Added strategy generation/mutation/backtesting loop.
- Added robustness gating and penalties.
- Added strategy playbook output and JSONL run history.

### 6. Production operations commands
- `tradingagents healthcheck`
- `tradingagents bootstrap`

## Commands To Validate Before Commit

```powershell
# 1) Tests
.\.venv\Scripts\pytest.exe -q

# 2) Bootstrap from Dhan_Bot config (optional but recommended)
.\.venv\Scripts\tradingagents.exe bootstrap --sync-dhan-from "C:\Users\Yeshw\Downloads\Dhan_Bot\dhan-trading-bot\config\config.json" --write-env --run-healthcheck

# 3) Strict production gate (fails on warn/fail)
.\.venv\Scripts\tradingagents.exe healthcheck --strict

# 4) Crypto autolab smoke
.\.venv\Scripts\tradingagents.exe autolab --symbol BTC-USD --instrument-type crypto --period 3mo --interval 1d --population 4 --cycles 1 --elites 2 --min-trades 2 --max-runs 1

# 5) Forex autolab smoke
.\.venv\Scripts\tradingagents.exe autolab --symbol EURUSD=X --instrument-type forex --period 6mo --interval 1d --population 4 --cycles 1 --elites 2 --min-trades 1 --max-runs 1
```

## Current Known External Blocker
- Nifty option-chain live call depends on a valid Dhan token.
- If token is expired/invalid, healthcheck shows WARN/FAIL and Dhan endpoints return 401.
- This is an external credential validity issue, not routing/integration logic.

## Commit Hygiene Checklist
- [ ] Ensure `.env` is not committed.
- [ ] Confirm no secrets in tracked files.
- [ ] Ensure generated runtime outputs are ignored (`eval_results/`, `strategy_lab_results/`, `reports/`).
- [ ] Ensure `.env.example` contains placeholders only.
- [ ] Run tests and healthcheck.

## Suggested Commit Grouping

### Commit A: Core features
- Multi-asset propagation/state
- Provider routing
- Strategy lab
- Order intent

### Commit B: Ops and docs
- Healthcheck/bootstrap commands
- README updates
- `PRODUCTION_COMMIT_GUIDE.md`
- `.env.example` template
- `.gitignore` runtime-output exclusions

## Suggested Commit Message

```text
feat: productionize TradingAgents for crypto/forex/nifty options with provider routing, healthcheck, bootstrap, and strategy lab
```
