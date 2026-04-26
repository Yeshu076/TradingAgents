"""
Module: env_schema.py
Part of the config subsystem.

F-15: Centralized registry of all TRADINGAGENTS_* environment variables.
Provides startup validation, typo detection, and type checking.

Usage:
    from tradingagents.config.env_schema import validate_env, ENV_REGISTRY
    issues = validate_env()
    for issue in issues:
        print(f"[{issue.level}] {issue.message}")
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class EnvVarSpec:
    """Specification for a single environment variable."""

    name: str
    var_type: str  # "str", "int", "float", "bool", "path"
    default: str
    description: str
    required: bool = False
    module: str = ""
    deprecated_aliases: List[str] = field(default_factory=list)


@dataclass
class ValidationIssue:
    """A single validation issue found during env validation."""

    level: str  # "error", "warning", "info"
    var_name: str
    message: str


# ---------------------------------------------------------------------------
# Authoritative Registry
# ---------------------------------------------------------------------------

ENV_REGISTRY: List[EnvVarSpec] = [
    # ── Execution Policy ──
    EnvVarSpec("TRADINGAGENTS_ALLOW_LIVE", "bool", "false", "Enable live (real-money) trading", module="execution.policy"),
    EnvVarSpec("TRADINGAGENTS_ENFORCE_MARKET_HOURS", "bool", "true", "Block trades outside market hours", module="execution.policy"),
    EnvVarSpec("TRADINGAGENTS_MAX_ORDER_QTY", "float", "25", "Maximum order quantity per trade", module="execution.policy"),
    EnvVarSpec("TRADINGAGENTS_MAX_ORDER_NOTIONAL", "float", "500000", "Maximum notional value per trade (USD)", module="execution.policy"),
    EnvVarSpec("TRADINGAGENTS_MAX_DAILY_TRADES", "int", "50", "Maximum trades per day", module="execution.policy"),
    EnvVarSpec("TRADINGAGENTS_MAX_DAILY_LOSS_PCT", "float", "2.0", "Maximum daily loss as percentage", module="execution.policy"),
    EnvVarSpec("TRADINGAGENTS_MAX_DAILY_LOSS_CURRENCY", "float", "500.0", "Maximum daily loss in currency", module="execution.policy"),
    EnvVarSpec("TRADINGAGENTS_MAX_OPEN_POSITIONS", "int", "20", "Maximum concurrent open positions", module="execution.policy"),

    # ── Confidence Gate (F-04) ──
    EnvVarSpec("TRADINGAGENTS_MIN_CONFIDENCE", "float", "0.0", "Minimum agent confidence to execute (0.0 = disabled)", module="execution.engine"),

    # ── Deduplication / Idempotency ──
    EnvVarSpec("TRADINGAGENTS_EXECUTION_IDEMPOTENCY_ENABLED", "bool", "true", "Enable execution deduplication", module="execution.deduplication"),
    EnvVarSpec("TRADINGAGENTS_EXECUTION_IDEMPOTENCY_WINDOW_SECONDS", "int", "3600", "Dedup lookback window in seconds", module="execution.deduplication"),
    EnvVarSpec("TRADINGAGENTS_EXECUTION_IDEMPOTENCY_SCAN_LIMIT", "int", "1000", "Max journal entries to scan for dedup", module="execution.deduplication"),

    # ── Journal ──
    EnvVarSpec("TRADINGAGENTS_DECISION_JOURNAL_FILE", "path", "trade_decisions.jsonl", "Path to decision journal JSONL file", module="execution.journal"),
    EnvVarSpec("TRADINGAGENTS_DECISION_JOURNAL_MAX_BYTES", "int", "5000000", "Max journal file size before rotation", module="execution.journal"),
    EnvVarSpec("TRADINGAGENTS_DECISION_JOURNAL_MAX_ROLL_FILES", "int", "5", "Max rotated journal backup files", module="execution.journal"),

    # ── Position Manager ──
    EnvVarSpec("TRADINGAGENTS_SQLITE_STATE_FILE", "path", "portfolio.db", "Path to SQLite portfolio state DB", module="execution.position_manager"),
    EnvVarSpec("TRADINGAGENTS_PAPER_INITIAL_BALANCE", "float", "1000000", "Initial paper trading balance", module="execution.position_manager"),
    EnvVarSpec("TRADINGAGENTS_STARTING_BALANCE", "float", "0", "Starting balance for risk calculations (0 = auto-detect)", module="execution.global_risk"),

    # ── Liveness / Correlation ──
    EnvVarSpec("TRADINGAGENTS_LIVENESS_CHECK_ENABLED", "bool", "true", "Enable data feed liveness checks", module="execution.engine"),
    EnvVarSpec("TRADINGAGENTS_CORRELATION_CHECK_ENABLED", "bool", "true", "Enable portfolio correlation guard", module="execution.engine"),
    EnvVarSpec("TRADINGAGENTS_MAX_CORRELATION", "float", "0.75", "Max allowed portfolio correlation (0-1)", module="execution.engine"),

    # ── Margin ──
    EnvVarSpec("TRADINGAGENTS_MARGIN_CHECK_ENABLED", "bool", "true", "Enable margin pre-check on live trades", module="execution.margin"),
    EnvVarSpec("TRADINGAGENTS_MARGIN_BUFFER_PCT", "float", "10", "Margin safety buffer percentage", module="execution.margin"),

    # ── Resilience ──
    EnvVarSpec("TRADINGAGENTS_RETRY_MAX_ATTEMPTS", "int", "3", "Max broker API retry attempts", module="execution.resilience"),
    EnvVarSpec("TRADINGAGENTS_RETRY_BASE_DELAY_SECONDS", "float", "0.25", "Base delay between retries", module="execution.resilience"),
    EnvVarSpec("TRADINGAGENTS_RETRY_MAX_DELAY_SECONDS", "float", "2.0", "Max delay between retries", module="execution.resilience"),
    EnvVarSpec("TRADINGAGENTS_RETRY_JITTER_RATIO", "float", "0.15", "Jitter ratio for retry backoff (0-1)", module="execution.resilience"),
    EnvVarSpec("TRADINGAGENTS_CIRCUIT_FAILURE_THRESHOLD", "int", "3", "Failures before circuit breaker trips", module="execution.resilience"),
    EnvVarSpec("TRADINGAGENTS_CIRCUIT_RESET_SECONDS", "int", "60", "Seconds before circuit breaker resets", module="execution.resilience"),

    # ── Fill Verification ──
    EnvVarSpec("TRADINGAGENTS_FILL_POLL_INTERVAL_S", "int", "5", "Seconds between fill verification polls", module="execution.engine"),
    EnvVarSpec("TRADINGAGENTS_FILL_MAX_POLLS", "int", "6", "Max fill verification poll attempts", module="execution.engine"),

    # ── HTTP / Networking ──
    EnvVarSpec("TRADINGAGENTS_HTTP_TIMEOUT_SECONDS", "int", "15", "HTTP timeout for broker API calls", module="execution"),
    EnvVarSpec("TRADINGAGENTS_ROUTING_CONFIG", "path", "", "Path to JSON broker routing config file", module="execution.router"),

    # ── LLM / Agent Safety (GAP-23) ──
    EnvVarSpec("TRADINGAGENTS_LLM_TIMEOUT_S", "int", "60", "Per-LLM request timeout in seconds", module="graph.trading_graph"),
    EnvVarSpec("TRADINGAGENTS_MAX_LLM_COST_USD", "float", "2.00", "Max LLM cost per agent cycle (USD)", module="graph.trading_graph"),
    EnvVarSpec("TRADINGAGENTS_CYCLE_TIMEOUT_S", "int", "600", "Max agent cycle duration (seconds)", module="graph.trading_graph"),

    # ── Risk Gate ──
    EnvVarSpec("TRADINGAGENTS_RISK_MIN_CONFIDENCE", "float", "0.40", "Risk gate minimum confidence threshold", module="execution.risk_gate"),
    EnvVarSpec("TRADINGAGENTS_RISK_MIN_RR", "float", "1.20", "Risk gate minimum risk-reward ratio", module="execution.risk_gate"),
    EnvVarSpec("TRADINGAGENTS_RISK_MAX_POSITION_PCT", "float", "0.15", "Risk gate max position size percentage", module="execution.risk_gate"),

    # ── Strategy Lab / Governance ──
    EnvVarSpec("TRADINGAGENTS_PROMOTION_COOLDOWN_RUNS", "int", "2", "Runs before newly promoted strategy can be demoted", module="strategy_lab.governance"),
    EnvVarSpec("TRADINGAGENTS_PROMOTION_DRIFT_LOOKBACK_TRADES", "int", "30", "Trades to look back for drift detection", module="strategy_lab.governance"),
    EnvVarSpec("TRADINGAGENTS_PROMOTION_DRIFT_MIN_SAMPLES", "int", "8", "Min samples for drift evaluation", module="strategy_lab.governance"),
    EnvVarSpec("TRADINGAGENTS_PROMOTION_DRIFT_MIN_FILL_RATE", "float", "0.35", "Min fill rate before drift demotion", module="strategy_lab.governance"),
    EnvVarSpec("TRADINGAGENTS_PROMOTION_DRIFT_MAX_REJECTION_RATIO", "float", "0.55", "Max rejection ratio before drift demotion", module="strategy_lab.governance"),
    EnvVarSpec("TRADINGAGENTS_PROMOTION_DRIFT_MAX_BLOCKED_RATIO", "float", "0.35", "Max blocked ratio before drift demotion", module="strategy_lab.governance"),

    # ── Mark-to-Market Service (F-01) ──
    EnvVarSpec("TRADINGAGENTS_MTM_ENABLED", "bool", "true", "Enable the Mark-to-Market background polling service", module="execution.mtm_service"),
    EnvVarSpec("TRADINGAGENTS_MTM_POLL_INTERVAL_S", "int", "30", "Seconds between MTM price polls", module="execution.mtm_service"),
    EnvVarSpec("TRADINGAGENTS_MTM_MAX_STALE_S", "int", "120", "Max seconds before MTM price is considered stale", module="execution.mtm_service"),
    EnvVarSpec("MAX_UNREALIZED_DRAWDOWN_USD", "float", "0", "Max unrealized drawdown before new trades are blocked (0 = disabled)", module="execution.global_risk"),

    # ── Position Sizing (F-02) ──
    EnvVarSpec("TRADINGAGENTS_SIZING_ENABLED", "bool", "false", "Enable dynamic position sizing (false = use raw agent quantity)", module="execution.position_sizer"),
    EnvVarSpec("TRADINGAGENTS_SIZING_MODE", "str", "fixed", "Sizing mode: fixed | percent_equity | volatility_adjusted", module="execution.position_sizer"),
    EnvVarSpec("TRADINGAGENTS_RISK_PER_TRADE_PCT", "float", "1.0", "Percentage of equity to risk per trade (used in percent_equity and volatility_adjusted modes)", module="execution.position_sizer"),
    EnvVarSpec("TRADINGAGENTS_ATR_MULTIPLIER", "float", "2.0", "ATR multiplier used in volatility_adjusted sizing mode", module="execution.position_sizer"),
    EnvVarSpec("TRADINGAGENTS_MIN_QUANTITY", "float", "0.01", "Minimum allowed order quantity after sizing", module="execution.position_sizer"),
    EnvVarSpec("TRADINGAGENTS_QUANTITY_STEP", "float", "0.01", "Lot step — sized quantity rounded down to nearest multiple", module="execution.position_sizer"),

    # ── Portfolio Heat Monitor (F-05) ──
    EnvVarSpec("TRADINGAGENTS_MAX_PORTFOLIO_HEAT_USD", "float", "0", "Max total open position heat in USD before new trades are blocked (0 = disabled)", module="execution.global_risk"),
    EnvVarSpec("TRADINGAGENTS_HEAT_FALLBACK_PCT", "float", "2.0", "Fallback heat % of notional applied when a position has no stop loss set", module="execution.position_manager"),

    # ── Limit Order Support (F-07) ──
    EnvVarSpec("TRADINGAGENTS_PENDING_ORDERS_DB", "path", "~/.tradingagents/pending_orders.db", "SQLite path for PendingOrderStore (F-07 limit order tracking)", module="execution.pending_orders"),
    EnvVarSpec("TRADINGAGENTS_LIMIT_FALLBACK_MARKET", "bool", "false", "If true, place a market order when a limit order expires unfilled (F-07 TIF watcher)", module="execution.engine"),
    EnvVarSpec("TRADINGAGENTS_LIMIT_FALLBACK_TIMEOUT_S", "int", "300", "Seconds to wait for limit fill before the TIF watcher cancels; 0 = honour intent.tif_seconds only (F-07)", module="execution.engine"),


    # ── Miscellaneous ──
    EnvVarSpec("TRADINGAGENTS_RESULTS_DIR", "path", "./results", "Directory for evaluation results", module="default_config"),
    EnvVarSpec("TRADINGAGENTS_ALLOWED_INSTRUMENTS", "str", "options,spot,crypto,forex,equity", "Comma-separated list of allowed instrument types", module="config.validation"),
]

# Build lookup for fast access
_REGISTRY_MAP: Dict[str, EnvVarSpec] = {spec.name: spec for spec in ENV_REGISTRY}

# Known deprecated aliases: old_name → canonical_name
_DEPRECATED_ALIASES: Dict[str, str] = {
    "TRADINGAGENTS_DEDUP_ENABLED": "TRADINGAGENTS_EXECUTION_IDEMPOTENCY_ENABLED",
    "TRADINGAGENTS_SLIPPAGE_PCT": "max_slippage_pct (broker kwarg)",
    "TRADINGAGENTS_DB_PATH": "TRADINGAGENTS_SQLITE_STATE_FILE",
}


def _try_parse(value: str, var_type: str) -> bool:
    """Check if value can be parsed as the expected type."""
    try:
        if var_type == "int":
            int(value)
        elif var_type == "float":
            float(value)
        elif var_type == "bool":
            if value.lower() not in {"true", "false", "1", "0", "yes", "no"}:
                return False
        # str and path always pass
        return True
    except (ValueError, TypeError):
        return False


def validate_env(strict: bool = False) -> List[ValidationIssue]:
    """
    Validate all TRADINGAGENTS_* environment variables.

    Args:
        strict: If True, treat warnings as errors.

    Returns:
        List of validation issues found.
    """
    issues: List[ValidationIssue] = []

    # 1. Check all registered variables for type correctness
    for spec in ENV_REGISTRY:
        value = os.environ.get(spec.name)
        if value is None:
            if spec.required:
                issues.append(ValidationIssue(
                    level="error",
                    var_name=spec.name,
                    message=f"Required variable {spec.name} is not set. "
                            f"Description: {spec.description}. Used by: {spec.module}",
                ))
            continue

        if not _try_parse(value, spec.var_type):
            issues.append(ValidationIssue(
                level="error",
                var_name=spec.name,
                message=f"{spec.name}={value!r} cannot be parsed as {spec.var_type}. "
                        f"Expected type: {spec.var_type}. Default: {spec.default}",
            ))

    # 2. Detect unknown TRADINGAGENTS_* variables (likely typos)
    for key in os.environ:
        if not key.startswith("TRADINGAGENTS_"):
            continue
        if key not in _REGISTRY_MAP:
            # Check if it's a deprecated alias
            if key in _DEPRECATED_ALIASES:
                canonical = _DEPRECATED_ALIASES[key]
                issues.append(ValidationIssue(
                    level="warning",
                    var_name=key,
                    message=f"Deprecated variable {key} is set. "
                            f"Use {canonical} instead.",
                ))
            else:
                issues.append(ValidationIssue(
                    level="warning",
                    var_name=key,
                    message=f"Unknown variable {key} is set. This may be a typo. "
                            f"Check docs/CONFIGURATION.md for valid variable names.",
                ))

    return issues


def validate_env_or_warn() -> None:
    """Run validation and log issues. Called at daemon startup."""
    issues = validate_env()
    if not issues:
        logger.info("Environment validation passed — all %d TRADINGAGENTS_* variables OK.", len(ENV_REGISTRY))
        return

    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]

    for issue in warnings:
        logger.warning("ENV VALIDATION: [%s] %s", issue.var_name, issue.message)

    for issue in errors:
        logger.error("ENV VALIDATION: [%s] %s", issue.var_name, issue.message)

    if errors:
        raise RuntimeError(
            f"Environment validation failed with {len(errors)} error(s). "
            f"Fix them before starting the daemon. "
            f"First error: {errors[0].message}"
        )


def get_registry_map() -> Dict[str, EnvVarSpec]:
    """Return the full registry as a dict for documentation generation."""
    return dict(_REGISTRY_MAP)
