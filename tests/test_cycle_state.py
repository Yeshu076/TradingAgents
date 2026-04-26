from tradingagents.execution.cycle_state import CycleStateStore


def test_cycle_state_save_load_and_resume(tmp_path):
    store = CycleStateStore(tmp_path / "cycle_state.json")
    context = "reports::NIFTY::auto::True::1.0"

    assert store.resume_start_cycle(context_key=context, total_cycles=5) == 1

    store.mark_cycle_success(
        context_key=context,
        cycle_idx=2,
        status="simulated_filled",
        last_fingerprint="fp-2",
        total_cycles=5,
    )

    loaded = store.load()
    assert loaded["last_completed_cycle"] == 2
    assert loaded["last_status"] == "simulated_filled"
    assert store.resume_start_cycle(context_key=context, total_cycles=5) == 3


def test_cycle_state_context_mismatch_starts_fresh(tmp_path):
    store = CycleStateStore(tmp_path / "cycle_state.json")
    store.mark_cycle_success(
        context_key="context-a",
        cycle_idx=4,
        status="ok",
        last_fingerprint="fp",
        total_cycles=10,
    )

    assert store.resume_start_cycle(context_key="context-b", total_cycles=10) == 1
