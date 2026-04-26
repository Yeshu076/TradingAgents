import pytest

from tradingagents.config import validate_runtime_environment


def test_runtime_environment_validation_rejects_invalid_confidence(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_RISK_MIN_CONFIDENCE", "1.5")

    with pytest.raises(RuntimeError):
        validate_runtime_environment()


def test_runtime_environment_validation_allows_defaults(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_RISK_MIN_CONFIDENCE", raising=False)
    monkeypatch.delenv("TRADINGAGENTS_RETRY_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("TRADINGAGENTS_ALLOWED_INSTRUMENTS", raising=False)

    validate_runtime_environment()
