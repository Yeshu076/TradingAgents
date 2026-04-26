import os
import json
import redis
import time
from tradingagents.agents.utils.memory import FinancialSituationMemory

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

# Initialize the Chroma DB memory instance for the Trader
trader_memory = FinancialSituationMemory("trader_memory")

def start_rag_ingestion():
    r = redis.Redis(
        host=REDIS_HOST, 
        port=REDIS_PORT, 
        password=os.getenv("REDIS_PASSWORD"),
        decode_responses=True
    )
    pubsub = r.pubsub()
    pubsub.subscribe("TRADE_SETTLEMENTS")
    
    print("🤖 VectorDB RAG Ingestion Service Started. Listening for closed trades on TRADE_SETTLEMENTS...")
    
    for message in pubsub.listen():
        if message["type"] == "message":
            try:
                settlement = json.loads(message["data"])
                bot = settlement.get("bot", "unknown")
                pnl = settlement.get("pnl", 0.0)
                macro_state = settlement.get("macro_state", "Unknown market state")
                agent_thesis = settlement.get("thesis", "No thesis provided")
                
                # We stitch the macro state as the "Situation"
                situation = f"Bot: {bot}\nMacro State: {macro_state}\nHindsight: PnL was {pnl}"
                
                # The recommendation is what the agent did
                recommendation = agent_thesis
                
                # The outcome is the numeric truth
                outcome = f"Trade closed with PnL of {pnl}"
                
                trader_memory.add_situations(
                    situations_and_advice=[(situation, recommendation)],
                    trade_outcomes=[outcome]
                )
                
                print(f"📖 RAG Ingestion: Logged {bot} trade into VectorDB. PnL: {pnl}")
            except Exception as e:
                print(f"RAG Error: {str(e)}")

if __name__ == "__main__":
    start_rag_ingestion()
