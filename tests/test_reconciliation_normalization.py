"""Tests for reconciliation field-name normalization (GAP-14)."""
import pytest
from tradingagents.execution.reconciliation import StateReconciliationService


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_DB_PATH", str(tmp_path / "test_recon.db"))
    monkeypatch.setenv("TRADINGAGENTS_PAPER_INITIAL_BALANCE", "100000")
    return StateReconciliationService()


class TestNormalizeLivePosition:
    def test_delta_format(self, svc):
        raw = {"product_symbol": "BTCUSD", "size": "1.5", "entry_price": "30000", "side": "buy"}
        norm = svc._normalize_live_position(raw)
        assert norm["symbol"] == "BTCUSD"
        assert norm["quantity"] == 1.5
        assert norm["avg_price"] == 30000.0

    def test_dhan_format_netqty(self, svc):
        raw = {"tradingSymbol": "NIFTY26APR24500CE", "netQty": "50", "averageTradedPrice": "120.5"}
        norm = svc._normalize_live_position(raw)
        assert norm["symbol"] == "NIFTY26APR24500CE"
        assert norm["quantity"] == 50.0
        assert norm["avg_price"] == 120.5

    def test_dhan_format_traded_qty(self, svc):
        raw = {"tradingSymbol": "NIFTY25MAY24600PE", "tradedQuantity": "75", "averageTradedPrice": "85"}
        norm = svc._normalize_live_position(raw)
        assert norm["quantity"] == 75.0

    def test_mt5_format(self, svc):
        raw = {"symbol": "XAUUSD", "volume": "0.1", "price_open": "2350.0", "type": "LONG"}
        norm = svc._normalize_live_position(raw)
        assert norm["symbol"] == "XAUUSD"
        assert norm["quantity"] == 0.1
        assert norm["side"] == "LONG"

    def test_empty_dict_defaults(self, svc):
        norm = svc._normalize_live_position({})
        assert norm["symbol"] == ""
        assert norm["quantity"] == 0.0
        assert norm["avg_price"] == 0.0

    def test_symbol_uppercased(self, svc):
        raw = {"tradingSymbol": "nifty26apr24500ce", "netQty": "25"}
        norm = svc._normalize_live_position(raw)
        assert norm["symbol"] == "NIFTY26APR24500CE"


class TestVerifyAlignment:
    def test_no_ghost_positions_when_all_match(self, svc, caplog):
        import logging
        internal = [{"symbol": "BTCUSD", "quantity": 1.0}]
        live = [{"product_symbol": "BTCUSD", "size": "1.0"}]
        with caplog.at_level(logging.CRITICAL):
            svc._verify_alignment("delta", internal, live)
        assert "RECONCILIATION FATAL" not in caplog.text

    def test_ghost_position_detected(self, svc, caplog):
        import logging
        internal = [{"symbol": "NIFTY26APR24500CE", "quantity": 50.0}]
        live = []  # Broker says flat
        with caplog.at_level(logging.CRITICAL):
            try:
                svc._verify_alignment("dhan", internal, live)
            except Exception:
                pass  # Redis not available — that's fine
        assert "RECONCILIATION FATAL" in caplog.text

    def test_quantity_mismatch_warning(self, svc, caplog):
        import logging
        internal = [{"symbol": "XAUUSD", "quantity": 1.0}]
        # Live shows 0.5 — >5% mismatch
        live = [{"symbol": "XAUUSD", "volume": "0.50", "price_open": "2350"}]
        with caplog.at_level(logging.WARNING):
            svc._verify_alignment("mt5", internal, live)
        assert "RECONCILIATION PARTIAL MISMATCH" in caplog.text

    def test_zero_quantity_internal_positions_skipped(self, svc, caplog):
        import logging
        internal = [{"symbol": "BTCUSD", "quantity": 0}]
        live = []
        with caplog.at_level(logging.CRITICAL):
            svc._verify_alignment("delta", internal, live)
        assert "RECONCILIATION FATAL" not in caplog.text
