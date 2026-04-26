import pytest
from tradingagents.execution.models import TradeIntent
from tradingagents.execution.arbitrator import ExecutionArbitrator

@pytest.fixture
def arbitrator():
    arb = ExecutionArbitrator()
    arb.min_confidence_threshold = 0.60
    return arb

def test_consensus_arbitrator_passes_high_conviction(arbitrator):
    intent1 = TradeIntent(symbol="AAPL", instrument_type="equity", signal="BUY", quantity=10, confidence=0.9, agent_source="Quant")
    intent2 = TradeIntent(symbol="AAPL", instrument_type="equity", signal="BUY", quantity=5, confidence=0.8, agent_source="News")
    
    arbitrator.submit_intent(intent1)
    arbitrator.submit_intent(intent2)
    
    results = arbitrator.flush_and_net_intents()
    assert len(results) == 1
    assert results[0].symbol == "AAPL"
    assert results[0].signal == "BUY"
    assert results[0].quantity == 15
    assert results[0].confidence == 1.0 # (0.9+0.8)/1.7 = 1.0

def test_consensus_arbitrator_drops_conflicting_deadlock(arbitrator):
    intent1 = TradeIntent(symbol="TSLA", instrument_type="equity", signal="BUY", quantity=10, confidence=0.8, agent_source="Quant")
    intent2 = TradeIntent(symbol="TSLA", instrument_type="equity", signal="SELL", quantity=10, confidence=0.7, agent_source="Fundamentals")
    
    arbitrator.submit_intent(intent1)
    arbitrator.submit_intent(intent2)
    
    results = arbitrator.flush_and_net_intents()
    # Net Conviction = |0.8 - 0.7| / 1.5 = 0.06 < 0.60 threshold
    assert len(results) == 0

def test_consensus_arbitrator_resolves_clear_majority(arbitrator):
    intent1 = TradeIntent(symbol="NVDA", instrument_type="equity", signal="BUY", quantity=20, confidence=0.9, agent_source="Quant")
    intent2 = TradeIntent(symbol="NVDA", instrument_type="equity", signal="BUY", quantity=10, confidence=0.9, agent_source="News")
    intent3 = TradeIntent(symbol="NVDA", instrument_type="equity", signal="SELL", quantity=5, confidence=0.4, agent_source="Contrarian")
    
    arbitrator.submit_intent(intent1)
    arbitrator.submit_intent(intent2)
    arbitrator.submit_intent(intent3)
    
    results = arbitrator.flush_and_net_intents()
    # Bull = 1.8, Bear = 0.4. Total = 2.2
    # Net Conv = |1.8 - 0.4| / 2.2 = 1.4 / 2.2 = 0.636 > 0.60 (Passes)
    # Net Qty = 20 + 10 - 5 = 25 BUY
    assert len(results) == 1
    assert results[0].symbol == "NVDA"
    assert results[0].signal == "BUY"
    assert results[0].quantity == 25
    assert round(results[0].confidence, 2) == 0.82 # 1.8 / 2.2

def test_consensus_arbitrator_washes_perfect_offset(arbitrator):
    intent1 = TradeIntent(symbol="BTC", instrument_type="crypto", signal="BUY", quantity=1, confidence=0.9, agent_source="BotA")
    intent2 = TradeIntent(symbol="BTC", instrument_type="crypto", signal="SELL", quantity=1, confidence=0.9, agent_source="BotB")
    
    arbitrator.submit_intent(intent1)
    arbitrator.submit_intent(intent2)
    
    results = arbitrator.flush_and_net_intents()
    assert len(results) == 0

