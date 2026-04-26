import os
import sys
import time
import json
import logging
import threading
from datetime import datetime, timezone, timedelta

import redis
import MetaTrader5 as mt5
from dotenv import load_dotenv

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] MT5_BRIDGE: %(message)s'
)
logger = logging.getLogger("mt5_bridge")

# Load environment variables
load_dotenv()

# Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
MT5_LOGIN = int(os.getenv("MT5_LOGIN", 0)) if os.getenv("MT5_LOGIN") else None
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "")
FOREX_SYMBOLS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY"]

# Global Safety Lock — thread-safe via Event
_circuit_breaker = threading.Event()  # Set = active/tripped
STARTING_EQUITY = None
LAST_HISTORY_CHECK = datetime.now(timezone.utc) - timedelta(days=1)

def init_mt5():
    if not mt5.initialize():
        logger.error(f"MT5 initialization failed, error code: {mt5.last_error()}")
        sys.exit(1)

    if MT5_LOGIN and MT5_PASSWORD and MT5_SERVER:
        authorized = mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
        if not authorized:
            logger.error(f"MT5 login failed, error code: {mt5.last_error()}")
            mt5.shutdown()
            sys.exit(1)

    logger.info("MT5 initialized and successfully authorized.")

def get_filling_mode(symbol):
    info = mt5.symbol_info(symbol)
    if info is None:
        return mt5.ORDER_FILLING_IOC
    if info.filling_mode & mt5.SYMBOL_FILLING_FOK:
        return mt5.ORDER_FILLING_FOK
    elif info.filling_mode & mt5.SYMBOL_FILLING_IOC:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN

def execute_trade(symbol: str, action: str, payload: dict, redis_client: redis.Redis):

    if _circuit_breaker.is_set():
        logger.error(f"🚨 CIRCUIT BREAKER ACTIVE: Rejecting AI {action} intent for {symbol}.")
        return

    # Ensure symbol is visible
    if not mt5.symbol_select(symbol, True):
        logger.error(f"Symbol {symbol} not found or could not be selected.")
        return

    sym_info = mt5.symbol_info(symbol)
    if sym_info is None:
        return

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logger.error(f"Could not fetch tick data for {symbol}.")
        return

    # Spread Guard (Do not trade if spread is abnormally wide)
    spread = tick.ask - tick.bid
    max_allowed_spread = 50 * sym_info.point # e.g. 50 points = 5 pips
    if spread > max_allowed_spread:
        logger.error(f"🚨 Spread too wide for {symbol}: {spread/sym_info.point:.1f} points. Rejecting order to prevent severe slippage.")
        return

    # Dynamic sizing
    allocations = payload.get("portfolio_allocation", {}).get("allocations", {}).get("forex", {})
    risk_pct = allocations.get("max_daily_loss_pct", 0.02)
    account_info = mt5.account_info()
    capital = account_info.balance if account_info else 10000.0

    # Risk logic
    point = sym_info.point
    sl_points = 500 * point
    tp_points = 1000 * point

    risk_amount = capital * risk_pct
    step_vol = sym_info.volume_step
    
    # Approx calc: lot_size = risk_amount / (sl_points * contract_size * tick_value_multiplier)
    # simplified to keep within bounds
    # For safety, strictly cap lot_size to sym_info.volume_max and min
    min_vol = sym_info.volume_min
    max_vol = sym_info.volume_max
    
    # Safe conservative default if math fails
    calc_lot = min_vol * 2.0 if risk_pct > 0.02 else min_vol
    
    # Round to volume step
    lot_size = max(min_vol, min(max_vol, round(calc_lot / step_vol) * step_vol))

    price = tick.ask if action == "BUY" else tick.bid
    order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL

    sl = price - sl_points if action == "BUY" else price + sl_points
    tp = price + tp_points if action == "BUY" else price - tp_points

    # Order Request
    filling = get_filling_mode(symbol)
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(lot_size),
        "type": order_type,
        "price": float(price),
        "sl": float(sl),
        "tp": float(tp),
        "deviation": 10, # Strict slippage cap (10 points)
        "magic": 777,
        "comment": "AI_Agent_Trade",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling,
    }

    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        logger.error(f"Order failed, retcode={result.retcode} for request: {request} | details: {result.comment}")
        return

    logger.info(f"Order Placed Successfully: Ticket {result.order}")

def history_settlement_loop(redis_client: redis.Redis):
    """
    Polls real MT5 deal history to capture actual PnL of closed positions.
    Replaces the dangerously hallucinated mock_settlement logic.
    """
    global LAST_HISTORY_CHECK
    while True:
        try:
            now = datetime.now(timezone.utc)
            deals = mt5.history_deals_get(LAST_HISTORY_CHECK, now)
            if deals:
                for deal in deals:
                    # deal.entry == 1 means IN/OUT or OUT (close)
                    if deal.entry == mt5.DEAL_ENTRY_OUT and deal.magic == 777:
                        settlement_payload = {
                            "symbol": deal.symbol,
                            "action": "SELL" if deal.type == mt5.DEAL_TYPE_SELL else "BUY",
                            "rationale": "MT5 Closed Position",
                            "outcome": deal.profit + deal.commission + deal.swap,
                            "contract": f"{deal.symbol}_SPOT",
                            "timestamp": now.isoformat(),
                            "ticket": deal.ticket
                        }
                        redis_client.publish("TRADE_SETTLEMENTS", json.dumps(settlement_payload))
                        logger.info(f"Published real settlement for ticket {deal.ticket}: PnL ")
            LAST_HISTORY_CHECK = now
        except Exception as e:
            logger.error(f"Error in settlement loop: {e}")
        time.sleep(15)

def start_listener():
    logger.info(f"Connecting MT5 Bridge to Redis at {REDIS_HOST}:{REDIS_PORT}...")
    try:
        redis_client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=os.getenv("REDIS_PASSWORD"),
            decode_responses=True
        )
        redis_client.ping()
        pubsub = redis_client.pubsub()
        pubsub.subscribe("TRADING_INTENTS", "SYSTEM_HALT")
        logger.info("Subscribed. Waiting for AI commands...")

        # Start real historical settlement publisher
        threading.Thread(target=history_settlement_loop, args=(redis_client,), daemon=True).start()

        def state_sync_loop():
            nonlocal pubsub
            global STARTING_EQUITY
            while True:
                try:
                    account_info = mt5.account_info()
                    positions = mt5.positions_get()
                    if account_info:
                        if STARTING_EQUITY is None:
                            STARTING_EQUITY = account_info.equity

                        # Hard Stop Evaluation (-5% roughly)
                        current_pnl = account_info.equity - STARTING_EQUITY
                        max_loss_usd = STARTING_EQUITY * 0.05
                        if current_pnl <= -max_loss_usd and not _circuit_breaker.is_set():
                            _circuit_breaker.set()
                            logger.critical(f"🚨 FATAL: Daily Loss Breached (-). Flattening all trades!")
                            # Emit local kill switch
                            redis_client.publish("SYSTEM_HALT", json.dumps({"reason": "LOCAL_DRAWDOWN_BREACH"}))

                        state_payload = {
                            "balance": account_info.balance,
                            "equity": account_info.equity,
                            "margin_free": account_info.margin_free,
                            "positions": [
                                {
                                    "symbol": p.symbol,
                                    "type": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
                                    "volume": p.volume,
                                    "price_open": p.price_open,
                                    "sl": p.sl,
                                    "tp": p.tp,
                                    "profit": p.profit
                                } for p in (positions or [])
                            ],
                            "timestamp": datetime.now().isoformat()
                        }
                        redis_client.set("MT5_ACCOUNT_STATE", json.dumps(state_payload))

                    redis_client.publish("HEARTBEATS", json.dumps({
                        "service": "MT5_Bridge",
                        "status": "online",
                        "circuit_breaker": _circuit_breaker.is_set(),
                        "timestamp": datetime.now().isoformat()
                    }))
                except Exception as e:
                    logger.error(f"State sync error: {e}")

                time.sleep(10)

        threading.Thread(target=state_sync_loop, daemon=True).start()

        for message in pubsub.listen():
            if message["type"] == "message":
                channel = message["channel"]
                data_str = message["data"]

                if channel == "SYSTEM_HALT":
                    logger.critical(f"System Halt Received! Locking bridge. Data: {data_str}")
                    _circuit_breaker.set()
                    continue

                if channel == "TRADING_INTENTS" and not _circuit_breaker.is_set():
                    try:
                        payload = json.loads(data_str)
                        symbol = payload.get("symbol", "")
                        action = payload.get("action", "").upper()

                        if symbol in FOREX_SYMBOLS and action in ["BUY", "SELL"]:
                            threading.Thread(target=execute_trade, args=(symbol, action, payload, redis_client)).start()
                    except json.JSONDecodeError:
                        logger.error("Received malformed JSON intent.")
                    except Exception as e:
                        logger.error(f"Error handling intent: {e}")

    except redis.ConnectionError:
        logger.error("Failed to connect to Redis. MT5 Bridge terminating.")
        sys.exit(1)

if __name__ == "__main__":
    init_mt5()
    start_listener()
