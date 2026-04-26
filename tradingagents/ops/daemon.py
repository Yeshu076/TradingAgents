"""
Module: daemon.py
Part of the ops subsystem.

This module contains logic for the ops operations as part of the broader TradingAgents framework.
"""
import os
import time
import logging
import pytz

logger = logging.getLogger(__name__)
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from tradingagents.execution.global_risk import GlobalRiskMonitor

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.strategy_lab.dynamic_runner import run_approved_strategies
from tradingagents.strategy_lab.quant_orchestrator import run_quant_cycle
from tradingagents.strategy_lab.portfolio_monitor import evaluate_and_demote_strategies
from tradingagents.llm_clients.factory import create_llm_client


def run_agent_analysis(symbol: str, analysts: list, debug: bool = False, instrument_type: str = "equity"):
    """
    Executes the multi-agent graph for a specific symbol based on Market Hours.
    """
    now_utc = datetime.now(pytz.utc)
    
    # --- Timezone & Market Hours Check ---
    if instrument_type == "equity": # NIFTY / NSE
        ist = pytz.timezone('Asia/Kolkata')
        now_ist = now_utc.astimezone(ist)
        # Nifty cash hours 9:15 to 15:30 IST Mon-Fri
        if now_ist.weekday() >= 5 or now_ist.hour < 9 or now_ist.hour >= 16:
            logger.info(f"Skipping {symbol} analysis: Outside Nifty Market hours.")
            return

    elif instrument_type == "forex":
        est = pytz.timezone('US/Eastern')
        now_est = now_utc.astimezone(est)
        # Forex 24/5 - Target volatile NY/London Overlap (8AM - 4PM EST)
        if now_est.weekday() >= 5 or now_est.hour < 8 or now_est.hour > 16:
            logger.info(f"Skipping {symbol} analysis: Outside Forex Peak Volatility hours.")
            return
            
    # Crypto runs regardless of time.
    
    date_str = now_utc.strftime("%Y-%m-%d")
    logger.info(f"Starting scheduled analysis for {symbol} ({instrument_type}) on {date_str}")
    try:
        from copy import deepcopy
        conf = deepcopy(DEFAULT_CONFIG)
        conf["instrument_type"] = instrument_type
        
        ta = TradingAgentsGraph(
            selected_analysts=analysts,
            debug=debug,
            config=conf
        )
        # Propagation handles report generation and saves order_intent.json     
        final_state, decision = ta.propagate(symbol, date_str, instrument_type=instrument_type)
        
        # Publish Intent to Execution Bots via Redis Bus
        try:
            import json, redis, re
            redis_client = redis.Redis(
                host=os.getenv("REDIS_HOST", "localhost"), 
                port=int(os.getenv("REDIS_PORT", 6379)), 
                password=os.getenv("REDIS_PASSWORD"),
                decode_responses=True
            )
            
            # The decision string might contain JSON block
            action = "HOLD"
            rationale = "No rationale provided."
            quant_params = {}
            if isinstance(decision, str):
                json_match = re.search(r'```json\s*(.*?)\s*```', decision, re.DOTALL)
                if json_match:
                    try:
                        parsed = json.loads(json_match.group(1))
                        action = parsed.get("recommendation", "HOLD")
                        rationale = parsed.get("rationale", rationale)
                        quant_params = parsed.get("quant_params", {})
                    except Exception as parse_err:
                        logger.warning(f"Failed to parse decision JSON: {parse_err}")
                elif "HOLD" in decision: action = "HOLD"
                elif "BUY" in decision: action = "BUY"
                elif "SELL" in decision: action = "SELL"
                
            payload = {
                "symbol": symbol,
                "action": action,
                "rationale": rationale,
                "quant_params": quant_params,
                "portfolio_allocation": final_state.get("portfolio_allocation", {}),
                "timestamp": datetime.now().isoformat()
            }
            logger.info(f"Publishing execution intent to TRADING_INTENTS: {payload}")
            redis_client.publish("TRADING_INTENTS", json.dumps(payload))
        except Exception as pub_e:
            logger.error(f"Error publishing intent to Redis: {pub_e}")
            
        logger.info(f"Finished scheduled analysis for {symbol}.")
    except Exception as e:
        logger.error(f"Error during scheduled analysis for {symbol}: {e}")

def run_strategy_generator(symbol: str):
    """
    Triggers the autonomous quant generator to write a new strategy.
    """
    logger.info(f"Triggering asynchronous Quant Strategy logic for {symbol}")
    try:
        client = create_llm_client(
            provider=DEFAULT_CONFIG["llm_provider"],
            model=DEFAULT_CONFIG["deep_think_llm"],
            base_url=DEFAULT_CONFIG.get("backend_url"),
        )
        llm = client.get_llm()
        # Seed it with an arbitrary market context. In a real scenario, we'd pass news here.
        market_context = f"Analyze recent price action and volume for {symbol}. Try to implement a breakout or mean reversion system."
        run_quant_cycle(llm, symbol, market_context, max_retries=3)
    except Exception as e:
        logger.error(f"Error generating quant strategy: {e}")

def run_live_execution_loop():
    """
    Evaluates currently approved VectorBT strategies and fires Execution events.
    """
    logger.info("Executing active approved strategies...")
    try:
        evaluate_and_demote_strategies(max_loss_amount=200.0) # Self-healing Kill Switch
        run_approved_strategies()
    except Exception as e:
        logger.error(f"Error in dynamic strategy runner: {e}")


def force_close_nifty_positions():
    """
    GAP-21/28: Forced EOD close of all open Nifty (Dhan) positions.
    Runs at 15:20 IST daily to prevent overnight options exposure.
    Theta decay on held options can cause significant loss if held overnight.
    """
    ist = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(ist)
    if now_ist.weekday() >= 5:
        logger.info("force_close_nifty_positions: Weekend — skipping.")
        return

    logger.warning("⏰ 15:20 IST: Initiating forced close of all open Nifty positions.")
    try:
        from tradingagents.execution.router import ExecutionRouter
        from tradingagents.execution.position_manager import PositionManager

        pm = PositionManager.from_env()
        router = ExecutionRouter.get_instance()
        open_positions = [p for p in pm.get_positions() if abs(p.get("quantity", 0)) > 0]
        nifty_positions = [
            p for p in open_positions
            if "NIFTY" in p["symbol"].upper() or p.get("instrument_type", "") == "options"
        ]

        if not nifty_positions:
            logger.info("force_close_nifty_positions: No open Nifty positions found.")
            return

        dhan = router.resolve("auto", "options", "NIFTY")
        closed, failed = 0, 0
        for pos in nifty_positions:
            symbol = pos["symbol"]
            qty = abs(pos["quantity"])
            try:
                dhan.close_symbol_position(symbol, instrument_type="options")
                pm.close_symbol(symbol)
                closed += 1
                logger.info(f"✅ Force-closed: {symbol} qty={qty}")
            except Exception as ce:
                failed += 1
                logger.error(f"❌ Failed to force-close {symbol}: {ce}")

        logger.warning(
            f"force_close_nifty_positions complete — closed={closed}, failed={failed}"
        )
        if failed > 0:
            try:
                from tradingagents.ops.notifier import send_notification
                send_notification(
                    f"⚠️ EOD CLOSE PARTIAL FAILURE\n"
                    f"closed={closed}, failed={failed}\n"
                    f"Manual intervention required for unclosed positions."
                )
            except Exception:
                pass
    except Exception as e:
        logger.error(f"force_close_nifty_positions: Unexpected error: {e}")


def check_dhan_token_health():
    """
    GAP-19: Checks Dhan JWT token expiry and fires an alert if it expires within 24 hours.
    Runs daily at 09:00 IST — before market open so the team can refresh before trading starts.
    """
    import base64, json as _json
    token = os.getenv("DHAN_ACCESS_TOKEN", "")
    if not token:
        logger.warning("check_dhan_token_health: DHAN_ACCESS_TOKEN not set.")
        return
    try:
        # JWT payload is the second segment (base64url-encoded)
        parts = token.split(".")
        if len(parts) < 2:
            raise ValueError("Token is not a valid JWT (expected 3 parts)")

        # Add padding to make it valid base64
        padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload_bytes = base64.urlsafe_b64decode(padded)
        payload = _json.loads(payload_bytes)
        exp = payload.get("exp")

        if exp is None:
            logger.warning("check_dhan_token_health: Token has no 'exp' claim — cannot check expiry.")
            return

        now_ts = datetime.now(pytz.utc).timestamp()
        ttl_hours = (exp - now_ts) / 3600

        if ttl_hours <= 0:
            msg = "🚨 DHAN TOKEN EXPIRED — all Nifty orders will fail with HTTP 401!"
            logger.critical(msg)
        elif ttl_hours <= 24:
            msg = f"⚠️ Dhan token expires in {ttl_hours:.1f}h — please refresh BEFORE market open."
            logger.warning(msg)
        else:
            logger.info(f"check_dhan_token_health: Token valid for {ttl_hours:.1f}h.")
            return

        try:
            from tradingagents.ops.notifier import send_notification
            send_notification(msg)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"check_dhan_token_health: Failed to decode token: {e}")


def start_daemon(symbols: list, cron_expr: str, analysts: list, debug: bool = False):

    """
    Starts an APScheduler daemon that runs the agent workflow based on market-aware rhythms.
    """

    # Start Global Risk Monitor immediately to listen on the message bus        
    try:
        global_risk = GlobalRiskMonitor.get_instance()
        logger.info("Global Risk Monitor instance created successfully.")
    except Exception as e:
        logger.error(f"Could not initialize GlobalRiskMonitor: {e}")

    scheduler = BackgroundScheduler()

    def guess_instrument(sym):
        s = sym.upper()
        if "NIFTY" in s or ".NS" in s:
            return "equity"
        elif "USD" in s and "BTC" not in s and "ETH" not in s and "SOL" not in s:
            if s in ["XAUUSD", "EURUSD", "GBPUSD", "JPYUSD"]:
                return "forex"
            return "forex"
        else:
            return "crypto"

    # 1. Schedule deep market analysis (Multi-Agent Team)
    for symbol in symbols:
        inst_type = guess_instrument(symbol)
        
        # Instead of fixed cron expressions, let's run a recurring interval.
        # The run_agent_analysis function internally blocks execution if outside market hours.
        # We run the check every 15 minutes.
        trigger = IntervalTrigger(minutes=15)

        job_func = lambda sym=symbol, itype=inst_type: run_agent_analysis(
            symbol=sym,
            analysts=analysts,
            debug=debug,
            instrument_type=itype
        )
        scheduler.add_job(job_func, trigger=trigger, name=f"agent_analysis_{symbol}")
        logger.info(f"Scheduled agent analysis for {symbol} ({inst_type}) every 15 minutes.")

        # 2. Schedule strategy generator to run overnight (e.g., 2 AM)
        quant_trigger = CronTrigger(hour=2, minute=0)
        scheduler.add_job(lambda sym=symbol: run_strategy_generator(sym), trigger=quant_trigger, name=f"quant_gen_{symbol}")

    # 3. Schedule the execution engine for active strategies (every 15 minutes)
    exec_trigger = CronTrigger(minute="*/15")
    scheduler.add_job(run_live_execution_loop, trigger=exec_trigger, name="live_execution")

    # 4. GAP-21/28: Force-close all Nifty (Dhan) positions at 15:20 IST daily
    ist_tz = pytz.timezone("Asia/Kolkata")
    nifty_close_trigger = CronTrigger(hour=15, minute=20, timezone=ist_tz)
    scheduler.add_job(
        force_close_nifty_positions,
        trigger=nifty_close_trigger,
        name="nifty_eod_close",
    )
    logger.info("Scheduled Nifty EOD forced close at 15:20 IST daily.")

    # 5. GAP-19: Dhan token expiry health check at 09:00 IST daily (before market open)
    token_check_trigger = CronTrigger(hour=9, minute=0, timezone=ist_tz)
    scheduler.add_job(
        check_dhan_token_health,
        trigger=token_check_trigger,
        name="dhan_token_health",
    )
    logger.info("Scheduled Dhan token health check at 09:00 IST daily.")

    scheduler.start()
    logger.info("Daemon started. Active Jobs:")

    # Using generic print or log (send_notification doesn't exist natively so we leave it if it's there or pass)
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping daemon...")
        scheduler.shutdown()
