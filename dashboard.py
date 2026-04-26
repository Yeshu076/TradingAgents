import streamlit as st
import redis
import json
import os
import pandas as pd
from datetime import datetime

from tradingagents.ops.parity_tracker import ParityTracker

# Setup Redis connection
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

@st.cache_resource
def get_redis_client():
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=os.getenv("REDIS_PASSWORD"),
        decode_responses=True
    )

r = get_redis_client()

st.set_page_config(page_title="TradingAgents Observability", layout="wide", page_icon="📈")

st.title("🤖 TradingAgents Observability Dashboard")
st.markdown("Real-time telemetry and capital allocation across autonomous execution pods.")

# Dynamic metrics tabs
tab1, tab2, tab3, tab4 = st.tabs(["Telemetry", "Arbitrator & Intents", "Slippage & Parity", "ML Retraining"])

with tab1:
    col1, col2, col3 = st.columns(3)

    def fetch_telemetry(bot_name):
        data = r.get(f"TELEMETRY_{bot_name}")
        if data:
            return json.loads(data)
        return None

    bots = ["dhan", "delta", "forex"]
    cols = [col1, col2, col3]

    for i, bot in enumerate(bots):
        with cols[i]:
            st.subheader(f"{bot.upper()} Bot")
            telemetry = fetch_telemetry(bot)
            if telemetry:
                status = telemetry.get("status", "unknown")
                st.metric("Status", status.upper(), delta="Active" if status == "running" else "Down")

                pnl = telemetry.get("daily_pnl", 0.0)
                st.metric("Daily PnL", f"₹{pnl}" if bot=="dhan" else f"${pnl}", delta=f"{pnl}")

                pos = telemetry.get("open_positions", 0)
                st.metric("Open Positions", pos)

                st.json(telemetry.get("risk_metrics", {}))
            else:
                st.warning("No telemetry available. Bot offline or disconnected.")  

    st.divider()

    st.subheader("Global Risk Monitor")
    global_risk = r.get("GLOBAL_RISK_STATE")
    if global_risk:
        risk_data = json.loads(global_risk)
        if risk_data.get("global_drawdown_breached"):
            st.error(f"🚨 CIRCUIT BREAKER TRIGGERED: GLOBAL DRAWDOWN BREACHED! ({risk_data.get('total_drawdown_pct', 0)}%)")
        else:
            st.success(f"System Normal. Current Network Drawdown: {risk_data.get('total_drawdown_pct', 0)}%")
            
        correlations = risk_data.get("portfolio_correlations", {})
        if correlations:
            st.markdown("**Portfolio Correlations**")
            st.json(correlations)
    else:
        st.info("Global risk state uninitialized.")

with tab2:
    st.subheader("Latest Agent Intents & Arbitrator State")
    c1, c2, c3 = st.columns(3)

    with c1:
        st.markdown("**Nifty Options (Dhan)**")
        intent_dhan = r.get("AGENT_INTENTS_DHAN")
        if intent_dhan: st.json(json.loads(intent_dhan))
        else: st.info("No intents pending.")

    with c2:
        st.markdown("**Crypto (Delta)**")
        intent_delta = r.get("AGENT_INTENTS_DELTA")
        if intent_delta: st.json(json.loads(intent_delta))
        else: st.info("No intents pending.")

    with c3:
        st.markdown("**Forex (MT5)**")
        intent_forex = r.get("AGENT_INTENTS_FOREX")
        if intent_forex: st.json(json.loads(intent_forex))
        else: st.info("No intents pending.")
        
    st.divider()
    st.markdown("### Consensus Arbitrator Ledger")
    arbitration_ledger = r.get("ARBITRATOR_LEDGER")
    if arbitration_ledger:
        st.dataframe(pd.DataFrame(json.loads(arbitration_ledger)))
    else:
        st.info("Wait for Arbitrator to publish consensus resolution blocks.")

with tab3:
    st.subheader("Live vs Backtest Slippage Parity")
    st.markdown("Detecting silent execution decay through invisible slippage vs theoretical models.")
    tracker = ParityTracker()
    try:
        df = tracker.load_filled_trades()
        if not df.empty:
            st.dataframe(df.tail(20)) # Show last 20 filled trades
            
            # Simple UI to check theoretical drift for loaded symbols
            symbols = df["symbol"].unique()
            c_sym = st.selectbox("Select Symbol for Parity Analysis", list(symbols))
            if st.button("Calculate Theoretical Drift"):
                res = tracker.analyze_slippage_decay(c_sym)
                if res.get("parity_status") == "DRIFT_WARNING":
                    st.warning(f"Drift detected for {c_sym}: {res}")
                else:
                    st.success(f"{c_sym} Parity is stable: {res}")
        else:
            st.info("No execution journal logs found.")
    except Exception as e:
        st.error(f"Could not load parity tracker: {e}")

with tab4:
    st.subheader("VectorBT Parameter Auto-Retraining")
    st.markdown("Grid search optimal strategy params derived from the latest market regime.")
    
    config_path = "config/strategies/ema_crossover_optimized.json"
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            ml_config = json.load(f)
        st.json(ml_config)
    else:
        st.info("No retrained parameter artifact found yet. The Auto-Retrainer runs asynchronously.")

st.divider()

col_refresh, col_kill = st.columns([1, 1])

with col_refresh:
    if st.button("🔄 Refresh Data", use_container_width=True):
        st.rerun()

with col_kill:
    if st.button("🚨 HALT TRADING & FLATTEN ALL (KILL SWITCH)", type="primary", use_container_width=True):
        r.publish("SYSTEM_HALT", json.dumps({"command": "FLATTEN_ALL", "reason": "KILL SWITCH TRIPPED BY USER DASHBOARD", "timestamp": str(datetime.now())}))
        r.set("GLOBAL_RISK_STATE", json.dumps({"global_drawdown_breached": True, "total_drawdown_pct": 100, "reason": "MANUAL HALT"}))
        st.error("SYSTEM HALT ISSUED! All bots instructed to lock and flatten.")
        
        # We fire a message to the event bus; EmergencyKillSwitch daemon will pick it up and trigger PagerDuty
