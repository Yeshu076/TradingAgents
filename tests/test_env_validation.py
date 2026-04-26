"""
tests/test_env_validation.py

F-15: Tests for the centralized environment variable validation system.
"""
import pytest
from tradingagents.config.env_schema import (
    validate_env,
    validate_env_or_warn,
    ENV_REGISTRY,
    _try_parse,
    ValidationIssue,
)


class TestEnvRegistry:
    def test_registry_not_empty(self):
        """Registry should contain all known TRADINGAGENTS_* variables."""
        assert len(ENV_REGISTRY) >= 40

    def test_all_entries_have_required_fields(self):
        """Every entry should have name, type, default, and description.
        
        Note: some legacy vars (e.g. MAX_UNREALIZED_DRAWDOWN_USD, MAX_DAILY_LOSS_USD)
        pre-date the TRADINGAGENTS_ prefix convention but are registered for
        documentation and validation completeness.
        """
        _valid_prefixes = ("TRADINGAGENTS_", "MAX_", "REDIS_", "ALLOCATION_")
        for spec in ENV_REGISTRY:
            assert any(spec.name.startswith(p) for p in _valid_prefixes), (
                f"{spec.name} doesn't start with a recognised prefix {_valid_prefixes}"
            )
            assert spec.var_type in {"str", "int", "float", "bool", "path"}, (
                f"{spec.name} has invalid type {spec.var_type}"
            )
            assert spec.description, f"{spec.name} has empty description"

    def test_no_duplicate_names(self):
        """No two entries should have the same name."""
        names = [s.name for s in ENV_REGISTRY]
        assert len(names) == len(set(names)), f"Duplicate names found: {[n for n in names if names.count(n) > 1]}"


class TestTryParse:
    def test_int_valid(self):
        assert _try_parse("42", "int") is True

    def test_int_invalid(self):
        assert _try_parse("abc", "int") is False

    def test_float_valid(self):
        assert _try_parse("3.14", "float") is True

    def test_float_invalid(self):
        assert _try_parse("not_a_number", "float") is False

    def test_bool_valid(self):
        for val in ["true", "false", "True", "False", "1", "0", "yes", "no"]:
            assert _try_parse(val, "bool") is True, f"Failed for {val}"

    def test_bool_invalid(self):
        assert _try_parse("maybe", "bool") is False

    def test_str_always_valid(self):
        assert _try_parse("anything", "str") is True

    def test_path_always_valid(self):
        assert _try_parse("/some/path", "path") is True


class TestValidateEnv:
    def test_clean_env_no_errors(self, monkeypatch):
        """With no TRADINGAGENTS_* vars set, there should be no errors (all are optional by default)."""
        # Clear any existing TRADINGAGENTS_* vars
        import os
        for key in list(os.environ.keys()):
            if key.startswith("TRADINGAGENTS_"):
                monkeypatch.delenv(key, raising=False)
        issues = validate_env()
        errors = [i for i in issues if i.level == "error"]
        assert len(errors) == 0

    def test_type_mismatch_detected(self, monkeypatch):
        """Setting an int var to a non-integer string should produce an error."""
        monkeypatch.setenv("TRADINGAGENTS_MAX_DAILY_TRADES", "not_a_number")
        issues = validate_env()
        matching = [i for i in issues if i.var_name == "TRADINGAGENTS_MAX_DAILY_TRADES" and i.level == "error"]
        assert len(matching) == 1
        assert "cannot be parsed as int" in matching[0].message

    def test_unknown_var_detected(self, monkeypatch):
        """An unknown TRADINGAGENTS_* var should produce a warning."""
        monkeypatch.setenv("TRADINGAGENTS_XYZZY_NONEXISTENT", "hello")
        issues = validate_env()
        matching = [i for i in issues if i.var_name == "TRADINGAGENTS_XYZZY_NONEXISTENT"]
        assert len(matching) == 1
        assert matching[0].level == "warning"
        assert "typo" in matching[0].message.lower()

    def test_deprecated_alias_detected(self, monkeypatch):
        """A deprecated alias should produce a specific warning."""
        monkeypatch.setenv("TRADINGAGENTS_DEDUP_ENABLED", "true")
        issues = validate_env()
        matching = [i for i in issues if i.var_name == "TRADINGAGENTS_DEDUP_ENABLED"]
        assert len(matching) == 1
        assert matching[0].level == "warning"
        assert "TRADINGAGENTS_EXECUTION_IDEMPOTENCY_ENABLED" in matching[0].message

    def test_bool_invalid_value_detected(self, monkeypatch):
        """A bool var set to 'maybe' should produce an error."""
        monkeypatch.setenv("TRADINGAGENTS_ALLOW_LIVE", "maybe")
        issues = validate_env()
        matching = [i for i in issues if i.var_name == "TRADINGAGENTS_ALLOW_LIVE" and i.level == "error"]
        assert len(matching) == 1

    def test_valid_vars_no_errors(self, monkeypatch):
        """Correctly typed variables should not produce errors."""
        monkeypatch.setenv("TRADINGAGENTS_ALLOW_LIVE", "false")
        monkeypatch.setenv("TRADINGAGENTS_MAX_ORDER_QTY", "25.0")
        monkeypatch.setenv("TRADINGAGENTS_MAX_DAILY_TRADES", "50")
        issues = validate_env()
        errors = [i for i in issues
                  if i.level == "error"
                  and i.var_name in {"TRADINGAGENTS_ALLOW_LIVE", "TRADINGAGENTS_MAX_ORDER_QTY", "TRADINGAGENTS_MAX_DAILY_TRADES"}]
        assert len(errors) == 0


class TestValidateEnvOrWarn:
    def test_raises_on_error(self, monkeypatch):
        """validate_env_or_warn should raise RuntimeError if there are errors."""
        monkeypatch.setenv("TRADINGAGENTS_MAX_DAILY_TRADES", "abc")
        with pytest.raises(RuntimeError, match="Environment validation failed"):
            validate_env_or_warn()

    def test_no_raise_on_clean_env(self, monkeypatch):
        """validate_env_or_warn should not raise with a clean env."""
        import os
        for key in list(os.environ.keys()):
            if key.startswith("TRADINGAGENTS_"):
                monkeypatch.delenv(key, raising=False)
        # Should not raise
        validate_env_or_warn()
