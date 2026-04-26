from __future__ import annotations
"""
Module: engine.py
Part of the execution subsystem.

This module contains logic for the execution operations as part of the broader TradingAgents framework.
"""

import os
import time
from typing import Any, Dict, Optional

from .deduplication import ExecutionIdempotencyManager
from .journal import safe_journal_append
from .margin import MarginValidator
from .models import ExecutionResult, OrderType, PendingOrder, TradeIntent  # F-07
from .pending_orders import PendingOrderStore  # F-07
from .position_manager import PositionManager
from .policy import ExecutionPolicy
from .risk_gate import DeterministicRiskGate
from .router import resolve_broker
from .global_risk import GlobalRiskMonitor
from .liveness import DataLivenessMonitor
from .correlation import PortfolioCorrelationGuard
from .position_sizer import SizerConfig, SizingMode, calculate_position_size  # F-02
from tradingagents.ops.notifier import send_notification


def execute_trade(
    intent: TradeIntent,
    broker: str = "auto",
    paper: bool = True,
    **broker_kwargs,
) -> ExecutionResult:
    allow_duplicates = _to_bool(broker_kwargs.pop("allow_duplicates", False))
    forced_exec_key = str(broker_kwargs.pop("idempotency_key", "")).strip() or None

    signal = intent.signal.upper()
    if signal in {"HOLD", "FLAT", "NONE"}:
        return ExecutionResult(
            broker="none",
            mode="paper" if paper else "live",
            action="skip",
            status="no_trade",
            symbol=intent.symbol,
            side="HOLD",
            quantity=float(intent.quantity),
            details={"reason": f"Signal={signal}"},
        )

    side = "BUY" if signal in {"BUY", "BULLISH", "LONG", "CALL"} else "SELL"

    # F-04: Confidence-Gated Execution — filter low-conviction agent signals
    min_confidence = float(os.environ.get("TRADINGAGENTS_MIN_CONFIDENCE", "0.0"))
    if min_confidence > 0 and intent.confidence < min_confidence:
        safe_journal_append(
            {
                "event": "trade",
                "mode": "paper" if paper else "live",
                "broker": "none",
                "symbol": intent.symbol,
                "instrument_type": intent.instrument_type,
                "signal": intent.signal,
                "quantity": float(intent.quantity),
                "status": "skipped_low_confidence",
                "confidence": intent.confidence,
                "threshold": min_confidence,
                "agent_source": intent.agent_source,
            }
        )
        return ExecutionResult(
            broker="none",
            mode="paper" if paper else "live",
            action="skip",
            status="skipped_low_confidence",
            symbol=intent.symbol,
            side=side,
            quantity=float(intent.quantity),
            details={
                "reason": f"Confidence {intent.confidence:.2f} < threshold {min_confidence:.2f}",
                "confidence": intent.confidence,
                "threshold": min_confidence,
            },
        )

    chosen = resolve_broker(broker, intent.instrument_type, intent.symbol)

    # F-02: Dynamic Position Sizing — compute risk-adjusted quantity before policy/dispatch
    sizer_enabled = _to_bool(os.environ.get("TRADINGAGENTS_SIZING_ENABLED", "false"))
    if sizer_enabled:
        sizer_cfg = SizerConfig.from_env()
        try:
            _pm_equity = PositionManager.from_env()
            equity = _pm_equity.get_portfolio_equity()
        except Exception as _eq_err:
            import logging as _sz_log
            _sz_log.getLogger(__name__).warning(
                "Position sizer: failed to read equity (%s). Using fallback=0.", _eq_err
            )
            equity = 0.0

        atr_value = float(broker_kwargs.pop("atr", 0.0) or 0.0)
        raw_qty = float(intent.quantity)
        sized_qty = calculate_position_size(
            mode=sizer_cfg.mode,
            equity=equity,
            config=sizer_cfg,
            entry=intent.suggested_entry,
            stop_loss=intent.suggested_stop_loss,
            atr=atr_value,
            raw_quantity=raw_qty,
        )

        if sized_qty != raw_qty:
            import logging as _sz_log
            _sz_log.getLogger(__name__).info(
                "Position sizer [%s]: %s raw_qty=%.4f → sized_qty=%.4f (equity=%.2f)",
                sizer_cfg.mode.value, intent.symbol, raw_qty, sized_qty, equity,
            )

        # Mutate intent so all downstream pipeline steps see the sized quantity
        intent.raw_quantity = raw_qty
        intent.sized_quantity = sized_qty
        intent.sizing_mode = sizer_cfg.mode.value
        intent.quantity = sized_qty

    policy = ExecutionPolicy.from_env()
    policy.validate_order(
        symbol=intent.symbol,
        instrument_type=intent.instrument_type,
        quantity=float(intent.quantity),
        is_live=not paper,
        broker_name=chosen.name,
        suggested_entry=intent.suggested_entry,
    )

    risk_gate = DeterministicRiskGate.from_env()
    risk_decision = risk_gate.evaluate(intent=intent, metadata=broker_kwargs)
    if not risk_decision.approved:
        safe_journal_append(
            {
                "event": "trade",
                "mode": "paper" if paper else "live",
                "broker": chosen.name,
                "symbol": intent.symbol,
                "instrument_type": intent.instrument_type,
                "signal": intent.signal,
                "quantity": float(intent.quantity),
                "status": "rejected",
                "reason": risk_decision.rejection_reason,
                "risk_warnings": risk_decision.warnings,
            }
        )
        send_notification(f"⚠️ **Trade Rejected by Risk Gate** ⚠️\nSymbol: {intent.symbol} ({side})\nReason: {risk_decision.rejection_reason}")
        raise RuntimeError(f"Risk gate rejected trade: {risk_decision.rejection_reason}")

    # 1. Check Global Liveness (Stale Feed Prevention)
    # Skip in paper mode unless explicitly enabled — paper trading doesn't require live feeds
    liveness_enabled = _to_bool(os.environ.get("TRADINGAGENTS_LIVENESS_CHECK_ENABLED", "true" if not paper else "false"))
    if liveness_enabled:
        liveness = DataLivenessMonitor.get_instance()
        if not liveness.validate_all_feeds_or_halt():
            raise RuntimeError("Trade execution blocked: Market Data Feeds are STALE. Killswitch engaged.")

    # 2. Check Global Risk Caps (Per-Strategy, Per-Symbol, Max Daily Loss, Portfolio Heat)
    global_risk = GlobalRiskMonitor.get_instance()
    notional = float(intent.quantity) * (intent.suggested_entry or 100.0)
    strategy_source = broker_kwargs.get("strategy", "unknown_strategy")
    # F-05: Compute proposed trade heat for the heat cap gate
    _proposed_heat = 0.0
    if intent.suggested_entry and intent.suggested_stop_loss:
        _proposed_heat = float(intent.quantity) * abs(
            intent.suggested_entry - intent.suggested_stop_loss
        )
    if not global_risk.evaluate_trade_intent(
        strategy_source, intent.symbol, notional, proposed_heat=_proposed_heat
    ):
        raise RuntimeError(f"Global Risk Caps breached for {intent.symbol}. Execution halted.")

    # 3. Portfolio Correlation Guard (GAP-13) — reject if new symbol too correlated with existing holdings
    correlation_enabled = _to_bool(os.environ.get("TRADINGAGENTS_CORRELATION_CHECK_ENABLED", "true"))
    if correlation_enabled:
        try:
            _pm = PositionManager.from_env()
            existing_syms = [p["symbol"] for p in _pm.get_positions()]
            _corr_guard = PortfolioCorrelationGuard(
                max_portfolio_correlation=float(os.environ.get("TRADINGAGENTS_MAX_CORRELATION", "0.75"))
            )
            if not _corr_guard.evaluate_correlation_impact(intent.symbol, existing_syms):
                raise RuntimeError(
                    f"Correlation guard rejected {intent.symbol}: too correlated with current portfolio "
                    f"(existing={existing_syms})."
                )
        except RuntimeError:
            raise
        except Exception as _corr_err:
            import logging as _clog
            _clog.getLogger(__name__).warning(
                "Correlation guard skipped (error fetching data): %s", _corr_err
            )

    mode = "paper" if paper else "live"
    dedupe = ExecutionIdempotencyManager.from_env()
    exec_key = forced_exec_key or dedupe.build_execution_key(
        intent=intent,
        side=side,
        mode=mode,
        broker_name=chosen.name,
    )
    if dedupe.enabled and not allow_duplicates:
        previous = dedupe.find_recent_success(exec_key=exec_key)
        if previous is not None:
            result = ExecutionResult(
                broker=chosen.name,
                mode=mode,
                action="skip",
                status="skipped_duplicate",
                symbol=intent.symbol,
                side=side,
                quantity=float(intent.quantity),
                details={
                    "reason": "Duplicate execution prevented",
                    "exec_key": exec_key,
                    "duplicate_of_ts": previous.get("ts"),
                    "duplicate_of_status": previous.get("status"),
                    "window_seconds": dedupe.window_seconds,
                },
            )
            safe_journal_append(
                {
                    "event": "trade",
                    "mode": mode,
                    "broker": chosen.name,
                    "symbol": intent.symbol,
                    "instrument_type": intent.instrument_type,
                    "signal": intent.signal,
                    "side": side,
                    "quantity": float(intent.quantity),
                    "status": result.status,
                    "exec_key": exec_key,
                    "details": result.details,
                }
            )
            return result

    if paper:
        wallet = PositionManager.from_env()
        # F-07: effective fill price — limit_price wins, then suggested_entry, then 1.0
        _eff_price = (
            intent.limit_price
            or broker_kwargs.get("mark_price")
            or intent.suggested_entry
            or 1.0
        )
        paper_price = float(_eff_price)
        wallet_metadata = {"source": "execute_trade", "risk_warnings": risk_decision.warnings}
        if "strategy_name" in broker_kwargs:
            wallet_metadata["strategy_name"] = broker_kwargs["strategy_name"]

        wallet_result = wallet.place_order(
            symbol=intent.symbol,
            side=side,
            quantity=float(intent.quantity),
            price=paper_price,
            instrument_type=intent.instrument_type,
            metadata=wallet_metadata,
        )

        # F-07: If it was a limit order, record it in PendingOrderStore (paper always fills)
        _paper_order_type = intent.order_type
        try:
            if _paper_order_type == OrderType.LIMIT:
                _pos = PendingOrderStore.from_env()
                _placed_at = int(time.time())
                _expires_at = (_placed_at + int(intent.tif_seconds)) if intent.tif_seconds else None
                _pos.upsert(PendingOrder(
                    order_id=f"paper-{exec_key}",
                    symbol=intent.symbol,
                    side=side,
                    quantity=float(intent.quantity),
                    limit_price=paper_price,
                    instrument_type=intent.instrument_type,
                    broker_name=chosen.name,
                    placed_at=_placed_at,
                    expires_at=_expires_at,
                    status="filled",   # Paper always fills immediately
                    exec_key=exec_key,
                ))
        except Exception:
            pass  # PendingOrderStore failure must never block paper fills

        result = ExecutionResult(
            broker=chosen.name,
            mode="paper",
            action="place_order",
            status="simulated_filled",
            symbol=intent.symbol,
            side=side,
            quantity=float(intent.quantity),
            details={
                "instrument_type": intent.instrument_type,
                "order_type": intent.order_type.value,          # F-07
                "limit_price": paper_price if _paper_order_type == OrderType.LIMIT else None,
                "time_in_force": intent.time_in_force.value,    # F-07
                "suggested_entry": intent.suggested_entry,
                "suggested_stop_loss": intent.suggested_stop_loss,
                "suggested_target": intent.suggested_target,
                "risk_gate": {
                    "approved": risk_decision.approved,
                    "warnings": risk_decision.warnings,
                },
                "paper_fill": wallet_result,
                "exec_key": exec_key,
                "policy": {
                    "max_order_quantity": policy.max_order_quantity,
                    "max_order_notional": policy.max_order_notional,
                    "allow_live_trading": policy.allow_live_trading,
                },
                "timestamp": int(time.time()),
            },
        )
        # GAP-02: Report execution to GlobalRiskMonitor so exposure accumulators work
        try:
            strategy_name = broker_kwargs.get("strategy_name", "default")
            notional = float(intent.quantity) * paper_price
            GlobalRiskMonitor.get_instance().report_trade_execution(
                strategy_name=strategy_name, symbol=intent.symbol, notional_value=notional
            )
        except Exception:
            pass  # Risk reporting must never block paper fills

        # F-05: Persist stop loss + recompute total portfolio heat
        try:
            _pm_heat = PositionManager.from_env()
            if intent.suggested_stop_loss and intent.suggested_stop_loss > 0:
                _pm_heat.set_stop_loss(intent.symbol, float(intent.suggested_stop_loss))
            total_heat = _pm_heat.get_total_position_heat()
            GlobalRiskMonitor.get_instance().update_portfolio_heat(total_heat)
        except Exception as _heat_err:
            import logging as _hlog
            _hlog.getLogger(__name__).warning(
                "F-05: Failed to update portfolio heat after fill (%s). Heat gate may be stale.",
                _heat_err,
            )
        safe_journal_append(
            {
                "event": "trade",
                "mode": "paper",
                "broker": chosen.name,
                "symbol": intent.symbol,
                "instrument_type": intent.instrument_type,
                "signal": intent.signal,
                "side": side,
                "quantity": float(intent.quantity),
                "status": result.status,
                "exec_key": exec_key,
                "order_type": intent.order_type.value,   # F-07
                "details": result.details,
            }
        )
        return result

    # 4. Margin pre-check (live only — no real funds at risk in paper mode)
    if not paper:
        _margin = MarginValidator(chosen)
        _margin_result = _margin.validate(intent)
        if not _margin_result.approved:
            safe_journal_append({
                "event": "trade",
                "mode": "live",
                "broker": chosen.name,
                "symbol": intent.symbol,
                "instrument_type": intent.instrument_type,
                "signal": intent.signal,
                "quantity": float(intent.quantity),
                "status": "rejected",
                "reason": _margin_result.reason,
                "margin_required": _margin_result.required_margin,
                "margin_available": _margin_result.available_buying_power,
            })
            send_notification(
                f"🛑 **Margin Check Failed** — {intent.symbol} ({side})\n"
                f"Required: {_margin_result.required_margin:.2f} | "
                f"Available: {_margin_result.available_buying_power:.2f}\n"
                f"Reason: {_margin_result.reason}"
            )
            raise RuntimeError(f"Margin check failed for {intent.symbol}: {_margin_result.reason}")

    # 5. Slippage guard
    if not paper and intent.suggested_entry is not None:
        try:
            quote = chosen.get_quote(intent.symbol)
            current_price = quote.get("ask") if side == "BUY" else quote.get("bid")
            if current_price:
                slippage = abs(current_price - intent.suggested_entry) / intent.suggested_entry
                max_slip = float(broker_kwargs.get("max_slippage_pct", 0.05))
                if slippage > max_slip:
                    raise RuntimeError(
                        f"Slippage guard triggered. Current {current_price} vs "
                        f"suggested {intent.suggested_entry} (> {max_slip:.1%} slip)"
                    )
        except RuntimeError:
            raise  # Slippage guard MUST propagate — do not swallow
        except NotImplementedError:
            pass  # Broker doesn't implement get_quote — skip slippage guard
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "Slippage guard skipped — could not fetch L1 quote (%s): %s",
                type(e).__name__, e,
            )

    # F-07: Limit order routing for live mode
    _use_limit = intent.order_type == OrderType.LIMIT
    _eff_limit_price = intent.limit_price or intent.suggested_entry

    if _use_limit and _eff_limit_price:
        import logging as _llog
        _llog.getLogger(__name__).info(
            "F-07: Placing LIMIT order — %s %s qty=%s @ %s TIF=%s",
            side, intent.symbol, intent.quantity, _eff_limit_price, intent.time_in_force.value,
        )
        raw = chosen.place_limit_order(
            symbol=intent.symbol,
            side=side,
            quantity=float(intent.quantity),
            price=float(_eff_limit_price),
            instrument_type=intent.instrument_type,
            time_in_force=intent.time_in_force.value,
            **broker_kwargs,
        )
        live_details = raw if isinstance(raw, dict) else {"raw": raw}
        live_details["exec_key"] = exec_key
        live_details["order_type"] = "limit"   # F-07
        live_details["limit_price"] = float(_eff_limit_price)
        live_details["time_in_force"] = intent.time_in_force.value

        # Persist in PendingOrderStore so TIF watcher can cancel if unfilled
        try:
            _ps = PendingOrderStore.from_env()
            _placed_at = int(time.time())
            _expires_at = (_placed_at + int(intent.tif_seconds)) if intent.tif_seconds else None
            _pending = PendingOrder(
                order_id=str(live_details.get("order_id", exec_key)),
                symbol=intent.symbol,
                side=side,
                quantity=float(intent.quantity),
                limit_price=float(_eff_limit_price),
                instrument_type=intent.instrument_type,
                broker_name=chosen.name,
                placed_at=_placed_at,
                expires_at=_expires_at,
                status="pending",
                exec_key=exec_key,
            )
            _ps.upsert(_pending)
            # Start engine-side TIF watcher only if tif_seconds is set
            if intent.tif_seconds and intent.tif_seconds > 0:
                _watch_tif_async(chosen, _pending)
        except Exception as _ps_err:
            import logging as _plog
            _plog.getLogger(__name__).warning(
                "F-07: PendingOrderStore upsert failed (%s) — limit order submitted but TIF won't be enforced.",
                _ps_err,
            )

        result = ExecutionResult(
            broker=chosen.name,
            mode="live",
            action="place_limit_order",
            status="pending",
            symbol=intent.symbol,
            side=side,
            quantity=float(intent.quantity),
            details=live_details,
        )
    elif intent.instrument_type == "options" and intent.suggested_stop_loss is not None and intent.suggested_target is not None:
        raw = chosen.place_bracket_order(
            symbol=intent.symbol,
            side=side,
            quantity=intent.quantity,
            instrument_type=intent.instrument_type,
            stop_loss=intent.suggested_stop_loss,
            target=intent.suggested_target,
            trailing_jump=intent.trailing_jump,
            suggested_entry=intent.suggested_entry,
            **broker_kwargs,
        )
        live_details = raw if isinstance(raw, dict) else {"raw": raw}
        live_details["exec_key"] = exec_key
        result = ExecutionResult(
            broker=chosen.name,
            mode="live",
            action="place_order",
            status="submitted",
            symbol=intent.symbol,
            side=side,
            quantity=float(intent.quantity),
            details=live_details,
        )
    else:
        raw = chosen.place_market_order(
            symbol=intent.symbol,
            side=side,
            quantity=intent.quantity,
            instrument_type=intent.instrument_type,
            suggested_entry=intent.suggested_entry,
            **broker_kwargs,
        )
        live_details = raw if isinstance(raw, dict) else {"raw": raw}
        live_details["exec_key"] = exec_key
        result = ExecutionResult(
            broker=chosen.name,
            mode="live",
            action="place_order",
            status="submitted",
            symbol=intent.symbol,
            side=side,
            quantity=float(intent.quantity),
            details=live_details,
        )

    safe_journal_append(
        {
            "event": "trade",
            "mode": "live",
            "broker": chosen.name,
            "symbol": intent.symbol,
            "instrument_type": intent.instrument_type,
            "signal": intent.signal,
            "side": side,
            "quantity": float(intent.quantity),
            "status": result.status,
            "exec_key": exec_key,
            "order_type": intent.order_type.value,   # F-07
            "details": result.details,
        }
    )
    send_notification(
        f"🚨 **LIVE TRADE SUBMITTED** 🚨\n"
        f"Symbol: {intent.symbol} ({side}) | Type: {intent.order_type.value.upper()}\n"
        f"Quantity: {intent.quantity} | Broker: {chosen.name}"
    )
    # GAP-02: Report execution to GlobalRiskMonitor so exposure accumulators work
    try:
        strategy_name = broker_kwargs.get("strategy_name", "default")
        notional_val = float(intent.quantity) * float(intent.suggested_entry or _eff_limit_price or 1.0)
        GlobalRiskMonitor.get_instance().report_trade_execution(
            strategy_name=strategy_name, symbol=intent.symbol, notional_value=notional_val
        )
    except Exception:
        pass  # Risk reporting must never block live fills

    # Priority 4: Fire-and-forget fill verification (market orders only)
    if not _use_limit:
        _verify_fill_async(broker=chosen, order_id=live_details.get("order_id", ""), result=result)
    return result


def _verify_fill_async(broker, order_id: str, result: ExecutionResult) -> None:
    """Spawns a background thread to verify that a submitted order was actually filled.
    Logs a warning if the order is not filled within the poll window — prevents silent position drift.
    """
    import threading
    import logging as _flog

    fill_log = _flog.getLogger("fill_verifier")

    if not order_id:
        fill_log.warning(
            f"fill_verifier: No order_id returned for {result.symbol} — cannot verify fill status."
        )
        return

    poll_interval = int(os.environ.get("TRADINGAGENTS_FILL_POLL_INTERVAL_S", "5"))
    max_polls = int(os.environ.get("TRADINGAGENTS_FILL_MAX_POLLS", "6"))  # default: 30s total

    def _poll():
        for attempt in range(1, max_polls + 1):
            time.sleep(poll_interval)
            try:
                orders = broker.list_positions()  # best-effort proxy — broker should expose order status
                # Check if our order_id appears as filled in positions
                matched = any(
                    str(p.get("order_id", "")) == str(order_id)
                    or str(p.get("orderId", "")) == str(order_id)
                    for p in orders
                )
                if matched:
                    fill_log.info(
                        f"fill_verifier: Order {order_id} ({result.symbol}) confirmed in positions "
                        f"after {attempt * poll_interval}s."
                    )
                    return
            except Exception as e:
                fill_log.warning(f"fill_verifier: poll {attempt}/{max_polls} failed for {order_id}: {e}")

        fill_log.warning(
            f"fill_verifier: Order {order_id} ({result.symbol} {result.side} {result.quantity}) "
            f"NOT confirmed in positions after {max_polls * poll_interval}s — possible unfilled or ghost trade."
        )
        safe_journal_append({
            "event": "fill_verification_failed",
            "order_id": order_id,
            "symbol": result.symbol,
            "side": result.side,
            "quantity": result.quantity,
            "broker": result.broker,
        })

    t = threading.Thread(target=_poll, name=f"fill-verify-{order_id}", daemon=True)
    t.start()


def _watch_tif_async(broker, pending: "PendingOrder") -> None:
    """F-07: Engine-managed TIF watcher.

    Spawns a daemon thread that:
    1. Waits until pending.expires_at
    2. Queries broker for fill status
    3. If still PENDING: cancels the broker order
    4. Optionally falls back to a market order (if env TRADINGAGENTS_LIMIT_FALLBACK_MARKET=1)
    5. Marks the PendingOrderStore record as 'expired' or 'cancelled'
    """
    import threading
    import logging as _wlog

    watch_log = _wlog.getLogger("tif_watcher")

    if not pending.expires_at:
        return

    def _watch():
        sleep_secs = max(0.0, pending.expires_at - time.time())
        time.sleep(sleep_secs)

        try:
            _ps = PendingOrderStore.from_env()
            current = _ps.get_by_id(pending.order_id)
            if current is None or current.status != "pending":
                # Already filled or cancelled — nothing to do
                return

            # Query broker for definitive fill status
            broker_status = "UNKNOWN"
            try:
                broker_status = broker.get_order_status(pending.order_id)
            except Exception as _bse:
                watch_log.warning("TIF watcher: get_order_status(%s) error: %s", pending.order_id, _bse)

            if broker_status == "FILLED":
                _ps.mark_status(pending.order_id, "filled")
                watch_log.info("TIF watcher: order %s filled before expiry.", pending.order_id)
                return

            # Not filled — cancel it
            watch_log.warning(
                "TIF watcher: order %s (%s %s @ %s) expired unfilled. Cancelling.",
                pending.order_id, pending.side, pending.symbol, pending.limit_price,
            )
            try:
                broker.cancel_order(pending.order_id, symbol=pending.symbol)
                _ps.mark_status(pending.order_id, "expired")
                safe_journal_append({
                    "event": "limit_order_expired",
                    "order_id": pending.order_id,
                    "symbol": pending.symbol,
                    "side": pending.side,
                    "limit_price": pending.limit_price,
                    "broker": pending.broker_name,
                })
            except Exception as _ce:
                watch_log.error("TIF watcher: cancel_order(%s) failed: %s", pending.order_id, _ce)
                _ps.mark_status(pending.order_id, "cancel_failed")
                return

            # Optional market-order fallback
            fallback = os.environ.get("TRADINGAGENTS_LIMIT_FALLBACK_MARKET", "0").strip().lower()
            if fallback in ("1", "true", "yes"):
                watch_log.warning(
                    "TIF watcher: LIMIT_FALLBACK_MARKET enabled — placing market order for %s %s qty=%s",
                    pending.side, pending.symbol, pending.quantity,
                )
                try:
                    broker.place_market_order(
                        symbol=pending.symbol,
                        side=pending.side,
                        quantity=pending.quantity,
                        instrument_type=pending.instrument_type,
                    )
                    safe_journal_append({
                        "event": "limit_fallback_market",
                        "order_id": pending.order_id,
                        "symbol": pending.symbol,
                        "side": pending.side,
                        "broker": pending.broker_name,
                    })
                except Exception as _moe:
                    watch_log.error(
                        "TIF watcher: market fallback order failed for %s: %s", pending.order_id, _moe
                    )

        except Exception as _outer:
            watch_log.error("TIF watcher: unexpected error for %s: %s", pending.order_id, _outer)

    t = threading.Thread(target=_watch, name=f"tif-watch-{pending.order_id}", daemon=True)
    t.start()


def list_positions(broker: str = "auto", instrument_type: str = "options", symbol: str = "") -> Dict[str, Any]:
    chosen = resolve_broker(broker, instrument_type, symbol)
    return {"broker": chosen.name, "positions": chosen.list_positions()}


def close_symbol_position(
    symbol: str,
    broker: str = "auto",
    instrument_type: str = "options",
    paper: bool = True,
    **broker_kwargs,
) -> Dict[str, Any]:
    chosen = resolve_broker(broker, instrument_type, symbol)
    if paper:
        wallet = PositionManager.from_env()
        closed = wallet.close_symbol(symbol=symbol, mark_price=broker_kwargs.get("mark_price"))
        result = {
            "broker": chosen.name,
            "mode": "paper",
            "action": "close_symbol",
            "status": "simulated_filled",
            "symbol": symbol,
            "details": closed,
        }
        safe_journal_append({"event": "close_symbol", **result})
        return result

    result = {
        "broker": chosen.name,
        "mode": "live",
        "action": "close_symbol",
        "status": "submitted",
        "symbol": symbol,
        "details": chosen.close_symbol_position(symbol=symbol, instrument_type=instrument_type, **broker_kwargs),
    }
    safe_journal_append({"event": "close_symbol", **result})
    return result


def cancel_all_orders(
    broker: str = "auto",
    instrument_type: str = "options",
    symbol: Optional[str] = None,
    paper: bool = True,
    **broker_kwargs,
) -> Dict[str, Any]:
    chosen = resolve_broker(broker, instrument_type, symbol or "")
    if paper:
        wallet = PositionManager.from_env()
        result = {
            "broker": chosen.name,
            "mode": "paper",
            "action": "cancel_all",
            "status": "simulated",
            "symbol": symbol,
            "wallet": wallet.get_summary(),
        }
        safe_journal_append({"event": "cancel_all", **result})
        return result

    result = {
        "broker": chosen.name,
        "mode": "live",
        "action": "cancel_all",
        "status": "submitted",
        "symbol": symbol,
        "details": chosen.cancel_all_orders(symbol=symbol, **broker_kwargs),
    }
    safe_journal_append({"event": "cancel_all", **result})
    return result


def get_paper_wallet_snapshot() -> Dict[str, Any]:
    wallet = PositionManager.from_env()
    return {
        "positions": wallet.get_positions(),
        "summary": wallet.get_summary(),
    }


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}

