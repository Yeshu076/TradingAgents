import pytest

from tradingagents.execution.resilience import (
    CircuitOpenError,
    ResilienceConfig,
    RetryableHttpError,
    execute_with_resilience,
)


def test_execute_with_resilience_retries_and_succeeds(monkeypatch):
    monkeypatch.setattr("tradingagents.execution.resilience.time.sleep", lambda *_: None)

    state = {"count": 0}

    def _op():
        state["count"] += 1
        if state["count"] < 3:
            raise RetryableHttpError("transient", status_code=503)
        return "ok"

    result = execute_with_resilience(
        _op,
        operation_name="test:retry:success",
        config=ResilienceConfig(max_attempts=3, base_delay_seconds=0.01, max_delay_seconds=0.02),
    )
    assert result == "ok"
    assert state["count"] == 3


def test_circuit_opens_after_threshold(monkeypatch):
    monkeypatch.setattr("tradingagents.execution.resilience.time.sleep", lambda *_: None)

    cfg = ResilienceConfig(
        max_attempts=1,
        base_delay_seconds=0.01,
        max_delay_seconds=0.02,
        circuit_failure_threshold=1,
        circuit_reset_seconds=60,
    )

    def _op():
        raise RetryableHttpError("always fails", status_code=503)

    with pytest.raises(RetryableHttpError):
        execute_with_resilience(_op, operation_name="test:circuit:open", config=cfg)

    with pytest.raises(CircuitOpenError):
        execute_with_resilience(_op, operation_name="test:circuit:open", config=cfg)
