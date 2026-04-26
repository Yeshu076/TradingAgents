from textwrap import dedent
from langchain_core.messages import HumanMessage
import json
import functools

def create_capital_allocator_agent(llm):
    """
    Looks at Bot Telemetry, Macro Report, and determines capital allocation.
    Outputs a structured JSON of capital risk directives for the execution bots.
    """
    def capital_allocator_node(state, name):
        macro_report = state.get("macro_report", "No macro report.")
        bot_telemetry = state.get("instrument_metadata", {}).get("bot_telemetry", {})
        live_account_states = state.get("instrument_metadata", {}).get("live_account_states", {})

        system_prompt = dedent("""
            You are the Chief Risk & Capital Allocator across a multi-bot autonomous firm.
            You manage 3 high-frequency execution bots:
            - Dhan_Bot (Nifty Options)
            - Delta_Algo (Crypto Derivatives)
            - Forex_Algo (XAUUSD/EURUSD)

            You have received the Macro Analyst's regime report, the live Bot Telemetry (current PnL, drawdowns),
            and LIVE margin constraints / open positions!

            Based on the Macro Regime and Bot performance, decide on max risk allocation.
            CRITICAL HEDGING RULE: Implement cross-asset hedging logic. For example:
            - If DXY (US Dollar) is strong/rising -> Reduce risk in EURUSD AND Crypto.
            - If VIX is spiking -> Reduce Equity (Nifty) and Crypto risk, move capital to XAUUSD (Gold).

            STATE-AWARENESS: If a bot has low or negative free margin, you MUST strictly command it to exit or halt instead of opening new size.

            Output EXACTLY ONE valid JSON block representing your directive.    
            Example format:
            {
                "thoughts": "String explaining your reasoning, including any cross-asset hedging you are applying natively.",
                "allocations": {
                    "dhan": {"max_daily_loss_pct": 0.05, "status": "active"},   
                    "delta": {"max_daily_loss_pct": 0.05, "status": "active"},  
                    "forex": {"max_daily_loss_pct": 0.02, "status": "active"}   
                },
                "global_risk_limit_pct": 0.03
            }
        """)

        user_message = f"MACRO REPORT:\n{macro_report}\n\nLIVE BOT TELEMETRY:\n{json.dumps(bot_telemetry, indent=2)}\n\nLIVE MARGIN/POSITIONS:\n{json.dumps(live_account_states, indent=2)}\n\nOutput JSON strictly enclosed in ```json ... ``` blocks."

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]

        import re
        max_retries = 3
        parsed_successfully = False
        parsed_data = {}

        for attempt in range(max_retries):
            response = llm.invoke(messages)
            content = response.content

            json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
            if json_match:
                try:
                    parsed_data = json.loads(json_match.group(1))
                    parsed_successfully = True
                    break
                except json.JSONDecodeError:
                    pass
            elif "{" in content:
                # Fallback to direct json loads if no markdown codeblock
                try:
                    # try to extract whatever looks like json
                    start = content.find('{')
                    end = content.rfind('}') + 1
                    parsed_data = json.loads(content[start:end])
                    parsed_successfully = True
                    break
                except json.JSONDecodeError:
                    pass

            if attempt < max_retries - 1:
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user", 
                    "content": "Your response did not contain a valid JSON block. Please provide EXACTLY ONE valid JSON block representing your directive, wrapped in ```json and ```."
                })

        if not parsed_successfully:
            # Panic fallback
            parsed_data = {
                "thoughts": "Failed to parse allocations from LLM. Deploying emergency strict limits.",
                "allocations": {
                    "dhan": {"max_daily_loss_pct": 0.01, "status": "paused"},
                    "delta": {"max_daily_loss_pct": 0.01, "status": "paused"},
                    "forex": {"max_daily_loss_pct": 0.01, "status": "paused"}
                },
                "global_risk_limit_pct": 0.01
            }

        return {"portfolio_allocation": parsed_data, "sender": name}
        
    return functools.partial(capital_allocator_node, name="Capital Allocator")
