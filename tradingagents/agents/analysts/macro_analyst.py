from langchain_core.messages import HumanMessage
import yfinance as yf
from textwrap import dedent
import functools

def fetch_macro_data():
    """Fetches DXY (US Dollar Index) and US10Y (Treasury Yield)."""
    try:
        # DXY = DX-Y.NYB, US10Y = ^TNX, VIX = ^VIX
        tickers = ["DX-Y.NYB", "^TNX", "^VIX"]
        data = yf.download(tickers, period="5d", progress=False)["Close"]
        
        latest = data.iloc[-1].to_dict()
        five_days_ago = data.iloc[0].to_dict()
        
        return {
            "DXY_Index": {"current": latest.get("DX-Y.NYB"), "5d_ago": five_days_ago.get("DX-Y.NYB")},
            "US_10Y_Yield": {"current": latest.get("^TNX"), "5d_ago": five_days_ago.get("^TNX")},
            "VIX_Volatility": {"current": latest.get("^VIX"), "5d_ago": five_days_ago.get("^VIX")}
        }
    except Exception as e:
        return {"error": str(e)}

def create_macro_analyst_agent(llm):
    def macro_analyst_node(state, name):
        trade_date = state.get("trade_date")
        macro_data = fetch_macro_data()
        
        system_prompt = dedent("""
            You are an expert Macroeconomic Analyst at a quantitative trading firm.
            Your job is to analyze global macro conditions (DXY, US 10-Year Treasury Yields, and VIX)
            and determine the overarching "Market Regime" (e.g., Risk-On, Risk-Off, Inflationary, Deflationary).
            
            How this affects assets:
            - Strong DXY generally hurts Crypto, Forex (XAUUSD), and Emerging Markets (Nifty)
            - Rising US10Y hurts Growth Tech and Crypto.
            - Spiking VIX means sell Premium (Options), cut position sizing, and move to Safety.
            
            Write a dense, concise 2-paragraph macro report summarizing the environment and risk level.
        """)
        
        user_message = f"Data as of {trade_date}:\n{macro_data}\n\nProvide the global Macro Report."
        
        response = llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ])
        
        return {"macro_report": response.content, "sender": name}
        
    return functools.partial(macro_analyst_node, name="Macro Analyst")
