from tradingagents.execution.engine import execute_trade, close_symbol_position, cancel_all_orders
from tradingagents.execution.models import TradeIntent


def test_execute_trade_paper_mode_options_routes_to_dhan(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_FILE", str(tmp_path / "journal.jsonl"))
    monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_ROTATE_DAILY", "false")
    monkeypatch.setenv("TRADINGAGENTS_PAPER_STATE_FILE", str(tmp_path / "wallet.json"))

    intent = TradeIntent(
        symbol="NIFTY25SEP24500CE",
        instrument_type="options",
        signal="BUY",
        quantity=2,
        suggested_stop_loss=20,
        suggested_target=45,
    )
    result = execute_trade(intent=intent, broker="auto", paper=True)
    assert result.mode == "paper"
    assert result.status == "simulated_filled"
    assert result.broker == "dhan"
    assert result.side == "BUY"


def test_execute_trade_hold_signal_skips_order(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_FILE", str(tmp_path / "journal_hold.jsonl"))
    monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_ROTATE_DAILY", "false")
    monkeypatch.setenv("TRADINGAGENTS_PAPER_STATE_FILE", str(tmp_path / "wallet_hold.json"))

    intent = TradeIntent(symbol="BTCUSD", instrument_type="spot", signal="HOLD", quantity=1)
    result = execute_trade(intent=intent, broker="auto", paper=True)
    assert result.action == "skip"
    assert result.status == "no_trade"


def test_close_and_cancel_are_simulated_in_paper_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_FILE", str(tmp_path / "journal_cc.jsonl"))
    monkeypatch.setenv("TRADINGAGENTS_DECISION_JOURNAL_ROTATE_DAILY", "false")
    monkeypatch.setenv("TRADINGAGENTS_PAPER_STATE_FILE", str(tmp_path / "wallet_cc.json"))

    close_result = close_symbol_position(
        symbol="BTCUSD",
        broker="delta",
        instrument_type="spot",
        paper=True,
    )
    assert close_result["status"] == "simulated_filled"
    assert close_result["broker"] == "delta"

    cancel_result = cancel_all_orders(
        broker="dhan",
        instrument_type="options",
        symbol="NIFTY25SEP24500CE",
        paper=True,
    )
    assert cancel_result["status"] == "simulated"
    assert cancel_result["broker"] == "dhan"
