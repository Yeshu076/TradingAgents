from tradingagents.execution.policy import ExecutionPolicy


def test_policy_blocks_live_when_disabled():
    policy = ExecutionPolicy(
        max_order_quantity=10,
        max_order_notional=100000,
        enforce_market_hours=False,
        allow_live_trading=False,
        allowed_instruments=("options", "spot"),
    )

    try:
        policy.validate_order(
            symbol="NIFTY25SEP24500CE",
            instrument_type="options",
            quantity=1,
            is_live=True,
            broker_name="dhan",
            suggested_entry=100,
        )
    except RuntimeError as exc:
        assert "TRADINGAGENTS_ALLOW_LIVE" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError for blocked live trading")


def test_policy_quantity_and_notional_limits():
    policy = ExecutionPolicy(
        max_order_quantity=2,
        max_order_notional=1000,
        enforce_market_hours=False,
        allow_live_trading=True,
        allowed_instruments=("options",),
    )

    try:
        policy.validate_order(
            symbol="NIFTY25SEP24500CE",
            instrument_type="options",
            quantity=3,
            is_live=False,
            broker_name="dhan",
            suggested_entry=100,
        )
    except RuntimeError as exc:
        assert "exceeds policy max" in str(exc)
    else:
        raise AssertionError("Expected quantity limit failure")

    try:
        policy.validate_order(
            symbol="NIFTY25SEP24500CE",
            instrument_type="options",
            quantity=2,
            is_live=False,
            broker_name="dhan",
            suggested_entry=600,
        )
    except RuntimeError as exc:
        assert "Estimated notional" in str(exc)
    else:
        raise AssertionError("Expected notional limit failure")
