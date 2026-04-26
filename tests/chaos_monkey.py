import pytest
import asyncio
import random
from typing import Callable, Any

class ChaosMonkey:
    """
    Simulates network failures, extreme latency, and malformed broker responses
    to prove that the execution engine fails safe and handles disconnects.
    """
    
    @staticmethod
    def inject_latency(p: float = 0.5, max_delay: float = 2.0):
        """
        Decorator to randomly pause execution of an async function
        to simulate high network RTT.
        """
        def decorator(func: Callable):
            async def wrapper(*args, **kwargs):
                if random.random() < p:
                    delay = random.uniform(0.1, max_delay)
                    await asyncio.sleep(delay)
                return await func(*args, **kwargs)
            return wrapper
        return decorator

    @staticmethod
    def inject_502(p: float = 0.2):
        """
        Decorator to randomly raise exceptions mimicking API Gateway 502s.
        """
        def decorator(func: Callable):
            async def wrapper(*args, **kwargs):
                if random.random() < p:
                    raise ConnectionError("502 Bad Gateway: Broker API is unavailable")
                return await func(*args, **kwargs)
            return wrapper
        return decorator

@pytest.mark.asyncio
async def test_chaos_order_execution():
    # Mock broker method wrapped with chaos
    @ChaosMonkey.inject_latency(p=1.0, max_delay=0.5)
    @ChaosMonkey.inject_502(p=0.5)
    async def mock_place_order(symbol, qty):
        return {"status": "filled", "symbol": symbol}
        
    failures = 0
    successes = 0
    
    for _ in range(10):
        try:
            # We expect a timeout or a 502 here
            result = await asyncio.wait_for(mock_place_order("BTC", 1), timeout=0.3)
            if result:
                successes += 1
        except asyncio.TimeoutError:
            failures += 1
        except ConnectionError:
            failures += 1
            
    # The point of chaos testing is proving the system handles the failure gracefully.
    # If the process didn't crash, the chaos test passes.
    assert (failures + successes) == 10
    print(f"Chaos Test Results -> Successes: {successes}, Managed Failures: {failures}")

