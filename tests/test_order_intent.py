from tradingagents.graph.signal_processing import SignalProcessor


class _DummyLLM:
    def invoke(self, messages):
        class _Resp:
            content = "BUY"

        return _Resp()


def test_extract_order_intent_basics():
    sp = SignalProcessor(_DummyLLM())
    intent = sp.extract_order_intent(
        full_signal="Rating: BUY with strong momentum and robust confirmation.",
        trader_plan="Entry 62000 Stop 59000 Target 68000 size 20% swing",
        ticker="BTC-USD",
        instrument_type="crypto",
        analyst_teams=["market", "news"],
        debate_rounds_used=3,
        research_depth="1",
        final_state={
            "trader_investment_plan": "BUY with momentum",
            "final_trade_decision": "BUY and accumulate",
        },
    )

    assert intent.signal == "BUY"
    assert intent.ticker == "BTC-USD"
    assert intent.instrument_type == "crypto"
    assert intent.suggested_entry == 62000.0
    assert intent.suggested_stop_loss == 59000.0
    assert intent.suggested_target == 68000.0
    assert intent.position_size_pct == 0.2
    assert intent.time_horizon == "swing"


def test_extract_order_intent_regex_fallback():
    class _BadLLM:
        def invoke(self, messages):
            class _Resp:
                content = "NOISE"

            return _Resp()

    sp = SignalProcessor(_BadLLM())
    intent = sp.extract_order_intent(
        full_signal="Final decision is UNDERWEIGHT due to risk.",
        trader_plan="Reduce exposure by 10%.",
        ticker="ETH-USD",
        instrument_type="crypto",
    )
    assert intent.signal == "UNDERWEIGHT"
