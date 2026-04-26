from tradingagents.ops.healthcheck import run_production_healthcheck


def test_healthcheck_returns_expected_check_set(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")

    # Avoid network calls in this unit test by patching internal check functions.
    monkeypatch.setattr(
        "tradingagents.ops.healthcheck._check_delta",
        lambda: "Delta crypto derivatives reachable",
    )
    monkeypatch.setattr(
        "tradingagents.ops.healthcheck._check_dhan",
        lambda: "Dhan Nifty option chain reachable",
    )
    monkeypatch.setattr(
        "tradingagents.ops.healthcheck._check_forex_snapshot",
        lambda: "Forex snapshot path ready",
    )
    monkeypatch.setattr(
        "tradingagents.ops.healthcheck._check_nifty_spot_snapshot",
        lambda: "Nifty spot snapshot normalization ready",
    )

    results = run_production_healthcheck()

    names = [item.name for item in results]
    assert "LLM Provider Keys" in names
    assert "Delta Crypto Provider" in names
    assert "Dhan Nifty Option Chain" in names
    assert "Forex Snapshot" in names
    assert "Nifty Spot Snapshot" in names

    statuses = {item.status for item in results}
    assert "fail" not in statuses
