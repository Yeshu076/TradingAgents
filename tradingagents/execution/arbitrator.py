from __future__ import annotations
"""
Module: arbitrator.py
Intercepts trade intents across multiple agents to resolve conflicts via confidence-weighted consensus,
prevent wash trades, and filter out low-conviction market noise.
"""
import time
import os
import logging
import threading
from typing import List, Dict
from collections import defaultdict

from .models import TradeIntent

logger = logging.getLogger("arbitrator")

class ExecutionArbitrator:
    """
    Buffers incoming TradeIntents over a flush window.
    Calculates the Net Confidence Ratio per symbol.
    If the net consensus is too weak (agents disagreeing), the intents are dropped.
    If a clear majority exists, the net quantity is placed in the direction of the consensus.
    """
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        # Time to aggregate signals from various async agents before taking action
        self.buffer_ms = int(os.getenv("ARBITRATOR_BUFFER_MS", "2000"))  # 2 second window
        self.min_confidence_threshold = float(os.getenv("MIN_CONSENSUS_THRESHOLD", "0.60")) # Minimum 60% agreement required
        
        self._intent_buffer: List[TradeIntent] = []
        self._last_flush_time = time.time()
        self._flush_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> 'ExecutionArbitrator':
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def submit_intent(self, intent: TradeIntent) -> None:
        """Submit an intent to the matching buffer."""
        with self._flush_lock:
            self._intent_buffer.append(intent)

    def flush_and_net_intents(self) -> List[TradeIntent]:
        """
        Calculates confidence-weighted consensus. 
        Drops conflicting signals that result in a low net conviction.
        Returns the netted list of resolved, high-conviction TradeIntents.
        """
        with self._flush_lock:
            if not self._intent_buffer:
                return []

            # Group by symbol & instrument_type
            symbol_groups: Dict[str, List[TradeIntent]] = defaultdict(list)

            for req in self._intent_buffer:
                key = f"{req.symbol}_{req.instrument_type}"
                symbol_groups[key].append(req)

            self._intent_buffer.clear()
            self._last_flush_time = time.time()

            resolved_intents = []
            
            for key, intents in symbol_groups.items():
                bullish_weight = 0.0
                bearish_weight = 0.0
                net_qty = 0.0
                
                base_intent = intents[0] # To carry over entry/stop limits
                total_votes = len(intents)

                for _int in intents:
                    side = _int.signal.upper()
                    if side in {"BUY", "LONG", "BULLISH", "CALL"}:
                        bullish_weight += _int.confidence
                        net_qty += _int.quantity
                    elif side in {"SELL", "SHORT", "BEARISH", "PUT"}:
                        bearish_weight += _int.confidence
                        net_qty -= _int.quantity

                total_weight = bullish_weight + bearish_weight
                if total_weight == 0:
                    continue
                    
                net_conviction = abs(bullish_weight - bearish_weight) / total_weight

                if net_conviction < self.min_confidence_threshold:
                    logger.warning(f"⚖️ [ARBITRATOR] {key}: Deadlock. BullWeight={bullish_weight:.2f}, BearWeight={bearish_weight:.2f}. "
                                   f"Conviction {net_conviction:.2f} < Min {self.min_confidence_threshold}. Dropping trade.")
                    continue

                if abs(net_qty) < 1e-9:
                    logger.info(f"⚖️ [ARBITRATOR] {key}: Quantities perfectly offset. Wash trade prevented. Dropping.")
                    continue 

                winning_side = "BUY" if net_qty > 0 else "SELL"
                winning_confidence = (bullish_weight if net_qty > 0 else bearish_weight) / total_weight
                
                logger.info(f"🟢 [ARBITRATOR] {key}: Consensus Reached -> {winning_side} {abs(net_qty)} (Conviction: {winning_confidence:.2f})")

                new_intent = TradeIntent(
                    symbol=base_intent.symbol,
                    instrument_type=base_intent.instrument_type,
                    signal=winning_side,
                    quantity=abs(net_qty),
                    suggested_entry=base_intent.suggested_entry,
                    suggested_stop_loss=base_intent.suggested_stop_loss,        
                    suggested_target=base_intent.suggested_target,
                    trailing_jump=base_intent.trailing_jump,
                    confidence=winning_confidence,
                    agent_source="consensus_arbitrator"
                )
                resolved_intents.append(new_intent)

            return resolved_intents


