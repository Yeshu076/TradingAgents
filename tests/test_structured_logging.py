"""
tests/test_structured_logging.py

F-06: Tests for structured JSON logging and correlation ID management.
"""
import json
import logging
import pytest

from tradingagents.logging_config import (
    CorrelationContext,
    StructuredJsonFormatter,
    setup_logging,
)


class TestCorrelationContext:
    def test_default_is_none(self):
        CorrelationContext.clear()
        assert CorrelationContext.get() is None

    def test_set_and_get(self):
        with CorrelationContext.set("test-123"):
            assert CorrelationContext.get() == "test-123"
        # After exiting, should revert
        assert CorrelationContext.get() is None

    def test_nested_contexts(self):
        with CorrelationContext.set("outer"):
            assert CorrelationContext.get() == "outer"
            with CorrelationContext.set("inner"):
                assert CorrelationContext.get() == "inner"
            assert CorrelationContext.get() == "outer"
        assert CorrelationContext.get() is None

    def test_auto_generate_id(self):
        with CorrelationContext.set() as cid:
            assert cid is not None
            assert len(cid) == 12
            assert CorrelationContext.get() == cid

    def test_set_raw(self):
        CorrelationContext.set_raw("raw-id")
        assert CorrelationContext.get() == "raw-id"
        CorrelationContext.clear()


class TestStructuredJsonFormatter:
    def _make_record(self, msg="Test message", level=logging.INFO):
        logger = logging.getLogger("test.formatter")
        record = logger.makeRecord(
            name="test.formatter",
            level=level,
            fn="test_file.py",
            lno=42,
            msg=msg,
            args=(),
            exc_info=None,
        )
        return record

    def test_output_is_valid_json(self):
        formatter = StructuredJsonFormatter()
        record = self._make_record()
        output = formatter.format(record)
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_contains_required_fields(self):
        formatter = StructuredJsonFormatter()
        record = self._make_record("hello world")
        parsed = json.loads(formatter.format(record))
        assert parsed["message"] == "hello world"
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "test.formatter"
        assert "timestamp" in parsed
        assert "line" in parsed

    def test_includes_correlation_id(self):
        formatter = StructuredJsonFormatter()
        with CorrelationContext.set("corr-xyz"):
            record = self._make_record()
            parsed = json.loads(formatter.format(record))
        assert parsed["correlation_id"] == "corr-xyz"

    def test_correlation_id_none_when_not_set(self):
        CorrelationContext.clear()
        formatter = StructuredJsonFormatter()
        record = self._make_record()
        parsed = json.loads(formatter.format(record))
        assert parsed["correlation_id"] is None

    def test_includes_exception(self):
        formatter = StructuredJsonFormatter()
        logger = logging.getLogger("test.exc")
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            record = logger.makeRecord(
                name="test.exc", level=logging.ERROR,
                fn="test.py", lno=1, msg="fail",
                args=(), exc_info=sys.exc_info(),
            )
        parsed = json.loads(formatter.format(record))
        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]


class TestSetupLogging:
    def test_text_format(self):
        setup_logging(log_format="text", log_level="DEBUG")
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        assert len(root.handlers) >= 1

    def test_json_format(self):
        setup_logging(log_format="json", log_level="INFO")
        root = logging.getLogger()
        handler = root.handlers[-1]
        assert isinstance(handler.formatter, StructuredJsonFormatter)

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_LOG_FORMAT", "json")
        monkeypatch.setenv("TRADINGAGENTS_LOG_LEVEL", "WARNING")
        setup_logging()
        root = logging.getLogger()
        assert root.level == logging.WARNING
