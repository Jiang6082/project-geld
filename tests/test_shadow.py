import json

import pandas as pd

from project_geld.shadow import (
    ShadowAvailability,
    ShadowQuote,
    run_shadow_cycle,
)


def target_history(values):
    times = pd.date_range("2026-07-20 14:45", periods=len(values), freq="15min", tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": times,
            "symbol": "AAPL",
            "target_weight": values,
            "score": 1.0,
        }
    )


def test_shadow_cycle_delays_execution_and_never_creates_orders(tmp_path):
    state = tmp_path / "state.json"
    events = tmp_path / "events.csv"
    availability = {"AAPL": ShadowAvailability(True, True, "easy_to_borrow")}
    quote = {"AAPL": ShadowQuote(99.99, 100.01, pd.Timestamp("2026-07-20 15:00", tz="UTC"))}

    first = run_shadow_cycle(
        target_history([-0.1]), {"AAPL": 100}, quote, availability,
        state, events, observed_at=pd.Timestamp("2026-07-20 14:46", tz="UTC"),
    )
    assert first.empty
    assert json.loads(state.read_text())["pending_targets"] == {"AAPL": -0.1}

    second = run_shadow_cycle(
        target_history([-0.1, -0.1]), {"AAPL": 100}, quote, availability,
        state, events, observed_at=pd.Timestamp("2026-07-20 15:01", tz="UTC"),
    )
    assert second.iloc[0]["action"] == "entry"
    assert second.iloc[0]["fill_price"] == 99.99
    assert "AAPL" in json.loads(state.read_text())["positions"]

    cover_quote = {"AAPL": ShadowQuote(99.48, 99.50, pd.Timestamp("2026-07-20 15:15", tz="UTC"))}
    third = run_shadow_cycle(
        target_history([-0.1, -0.1, 0.0]), {"AAPL": 99.5}, cover_quote,
        availability, state, events,
        observed_at=pd.Timestamp("2026-07-20 15:16", tz="UTC"),
    )
    assert third.empty
    fourth = run_shadow_cycle(
        target_history([-0.1, -0.1, 0.0, 0.0]), {"AAPL": 99.5}, cover_quote,
        availability, state, events,
        observed_at=pd.Timestamp("2026-07-20 15:31", tz="UTC"),
    )
    assert fourth.iloc[0]["action"] == "exit"
    assert fourth.iloc[0]["pnl"] > 0
    assert not json.loads(state.read_text())["positions"]
    assert run_shadow_cycle(
        target_history([-0.1, -0.1, 0.0, 0.0]), {"AAPL": 99.5}, cover_quote,
        availability, state, events,
    ).empty
    assert set(pd.read_csv(events)["action"]) == {"entry", "exit"}


def test_shadow_blocks_non_easy_to_borrow_entry(tmp_path):
    state = tmp_path / "state.json"
    events = tmp_path / "events.csv"
    quote = {"AAPL": ShadowQuote(100, 100.02, pd.Timestamp.now(tz="UTC"))}
    blocked = {"AAPL": ShadowAvailability(True, False, "hard_to_borrow")}
    run_shadow_cycle(target_history([-0.1]), {"AAPL": 100}, quote, blocked, state, events)
    result = run_shadow_cycle(
        target_history([-0.1, -0.1]), {"AAPL": 100}, quote, blocked, state, events
    )
    assert result.iloc[0]["action"] == "blocked_entry"
    assert result.iloc[0]["reason"] == "not_easy_to_borrow"
