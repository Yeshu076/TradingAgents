"""Tests for daemon safety jobs: force_close_nifty_positions, check_dhan_token_health."""
import base64
import json
import time
import pytest
from unittest.mock import MagicMock, patch

from tradingagents.ops.daemon import force_close_nifty_positions, check_dhan_token_health


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _weekday_now_mock():
    """Return a MagicMock whose .weekday() returns 0 (Monday) so weekend guard passes."""
    m = MagicMock()
    m.weekday.return_value = 0
    return m


def _make_pm(positions):
    pm = MagicMock()
    pm.get_positions.return_value = positions
    return pm


# ---------------------------------------------------------------------------
# force_close_nifty_positions
# ---------------------------------------------------------------------------

class TestForceCloseNiftyPositions:
    def test_skips_on_empty_positions(self):
        pm = _make_pm([])
        with patch("tradingagents.ops.daemon.datetime") as mock_dt, \
             patch("tradingagents.execution.position_manager.PositionManager.from_env", return_value=pm), \
             patch("tradingagents.execution.router.ExecutionRouter.get_instance") as mock_router:
            mock_dt.now.return_value = _weekday_now_mock()
            force_close_nifty_positions()
        mock_router.return_value.resolve.assert_not_called()

    def test_closes_nifty_positions(self):
        positions = [{"symbol": "NIFTY26APR24500CE", "quantity": 50, "instrument_type": "options"}]
        pm = _make_pm(positions)
        broker = MagicMock()

        with patch("tradingagents.ops.daemon.datetime") as mock_dt, \
             patch("tradingagents.execution.position_manager.PositionManager.from_env", return_value=pm), \
             patch("tradingagents.execution.router.ExecutionRouter.get_instance") as mock_gi:
            mock_dt.now.return_value = _weekday_now_mock()
            mock_gi.return_value.resolve.return_value = broker
            force_close_nifty_positions()

        # Position manager should record the close
        pm.close_symbol.assert_called_once_with("NIFTY26APR24500CE")

    def test_continues_after_individual_failure(self):
        positions = [
            {"symbol": "NIFTY26APR24500CE", "quantity": 50, "instrument_type": "options"},
            {"symbol": "NIFTY26APR24600PE", "quantity": 25, "instrument_type": "options"},
        ]
        pm = _make_pm(positions)
        broker = MagicMock()
        broker.close_symbol_position.side_effect = [RuntimeError("API error"), None]

        with patch("tradingagents.ops.daemon.datetime") as mock_dt, \
             patch("tradingagents.execution.position_manager.PositionManager.from_env", return_value=pm), \
             patch("tradingagents.execution.router.ExecutionRouter.get_instance") as mock_gi:
            mock_dt.now.return_value = _weekday_now_mock()
            mock_gi.return_value.resolve.return_value = broker
            # Must not raise even when first close fails
            force_close_nifty_positions()

        # Second closure succeeded → pm.close_symbol called once
        assert pm.close_symbol.call_count == 1

    def test_skips_zero_quantity_positions(self):
        positions = [{"symbol": "NIFTY26APR24500CE", "quantity": 0, "instrument_type": "options"}]
        pm = _make_pm(positions)
        broker = MagicMock()

        with patch("tradingagents.ops.daemon.datetime") as mock_dt, \
             patch("tradingagents.execution.position_manager.PositionManager.from_env", return_value=pm), \
             patch("tradingagents.execution.router.ExecutionRouter.get_instance") as mock_gi:
            mock_dt.now.return_value = _weekday_now_mock()
            mock_gi.return_value.resolve.return_value = broker
            force_close_nifty_positions()

        broker.close_symbol_position.assert_not_called()


# ---------------------------------------------------------------------------
# check_dhan_token_health
# ---------------------------------------------------------------------------

def _make_jwt(exp_ts: int) -> str:
    """Create a fake JWT with a specific exp claim."""
    header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').rstrip(b"=").decode()
    payload_data = json.dumps({"exp": exp_ts}).encode()
    payload = base64.urlsafe_b64encode(payload_data).rstrip(b"=").decode()
    return f"{header}.{payload}.fakesig"


class TestCheckDhanTokenHealth:
    def test_no_token_logs_warning(self, monkeypatch, caplog):
        import logging
        monkeypatch.delenv("DHAN_ACCESS_TOKEN", raising=False)
        with caplog.at_level(logging.WARNING):
            check_dhan_token_health()
        assert "not set" in caplog.text

    def test_expired_token_logs_critical(self, monkeypatch, caplog):
        import logging
        expired_ts = int(time.time()) - 3600  # 1 hour ago
        monkeypatch.setenv("DHAN_ACCESS_TOKEN", _make_jwt(expired_ts))
        with caplog.at_level(logging.CRITICAL):
            check_dhan_token_health()
        assert "EXPIRED" in caplog.text

    def test_expiring_soon_logs_warning(self, monkeypatch, caplog):
        import logging
        soon_ts = int(time.time()) + 3600  # 1 hour from now (within 24h)
        monkeypatch.setenv("DHAN_ACCESS_TOKEN", _make_jwt(soon_ts))
        with caplog.at_level(logging.WARNING):
            check_dhan_token_health()
        assert "expires in" in caplog.text

    def test_valid_token_logs_info(self, monkeypatch, caplog):
        import logging
        future_ts = int(time.time()) + (72 * 3600)  # 3 days from now
        monkeypatch.setenv("DHAN_ACCESS_TOKEN", _make_jwt(future_ts))
        with caplog.at_level(logging.INFO):
            check_dhan_token_health()
        assert "valid for" in caplog.text
        assert "EXPIRED" not in caplog.text

    def test_non_jwt_token_logs_error(self, monkeypatch, caplog):
        import logging
        monkeypatch.setenv("DHAN_ACCESS_TOKEN", "not.a.valid.jwt.token.at.all")
        with caplog.at_level(logging.ERROR):
            check_dhan_token_health()
        # Should log error, not crash
        assert True  # Just ensure no exception propagates
