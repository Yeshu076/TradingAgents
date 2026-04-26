from tradingagents.execution.position_manager import PositionManager


def test_position_manager_place_and_persist(tmp_path):
    db_file = tmp_path / "portfolio.db"
    pm = PositionManager(db_path=db_file, initial_balance=1000)

    result = pm.place_order(
        symbol="BTCUSD",
        side="BUY",
        quantity=2,
        price=100,
        instrument_type="spot",
    )

    assert result["wallet_cash"] == 800
    assert db_file.exists()

    reloaded = PositionManager(db_path=db_file, initial_balance=1000)
    positions = reloaded.get_positions()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "BTCUSD"


def test_position_manager_close_symbol(tmp_path):
    db_file = tmp_path / "wallet.db"
    pm = PositionManager(db_path=db_file, initial_balance=1000)
    pm.place_order("NIFTY", "BUY", 1, 100, "options")
    close_result = pm.close_symbol("NIFTY", mark_price=120)

    # After closing: bought at 100, sold at 120 -> profit of 20 -> cash = 1000 - 100 + 120 = 1020
    assert close_result["wallet_cash"] == 1020
    assert pm.get_positions() == []  # position closed (qty = 0, filtered out)
