"""Kill-switch robustness: late fills, unreadable logs, and manual reset."""

import pandas as pd
import pytest

from project_geld.atomicio import atomic_write_dataframe_csv
from project_geld.paper import (
    implementation_shortfall,
    load_shortfall_history,
    read_kill_switch_reset,
    record_kill_switch_reset,
    refresh_shortfall_history,
    shortfall_kill_switch_active,
    trailing_shortfall_bps,
    unresolved_shortfall_since,
)


def _planned(client_order_id="a", side="buy", reference=500.0, quantity=10.0):
    return pd.DataFrame(
        [
            {
                "symbol": "SPY",
                "side": side,
                "quantity": quantity,
                "reference_price": reference,
                "limit_price": reference * 1.0002,
                "client_order_id": client_order_id,
            }
        ]
    )


def _activity(client_order_id="a", filled=10.0, price=500.5):
    return pd.DataFrame(
        [
            {
                "client_order_id": client_order_id,
                "filled_quantity": filled,
                "filled_average_price": price if filled else None,
            }
        ]
    )


def test_late_fill_replaces_provisional_missed_status(tmp_path):
    # Measured immediately after submission: nothing filled yet.
    first = implementation_shortfall(_planned(), _activity(filled=0.0))
    first.insert(0, "decision_bar", "2026-11-13T15:30:00+00:00")
    assert bool(first.loc[0, "missed"]) is True

    # Persist and reload, as the paper loop does between cycles.
    path = tmp_path / "implementation_shortfall.csv"
    atomic_write_dataframe_csv(first, path, index=False)
    history = load_shortfall_history(path)

    refreshed = refresh_shortfall_history(history, _activity(filled=10.0, price=500.5))
    assert bool(refreshed.loc[0, "missed"]) is False
    assert refreshed.loc[0, "fill_rate"] == pytest.approx(1.0)
    assert refreshed.loc[0, "shortfall_bps"] == pytest.approx(10.0)
    # Now that the fill counts, the switch sees the 10 bps cost.
    assert shortfall_kill_switch_active(refreshed, "SPY")


def test_refresh_leaves_rows_without_new_activity_untouched():
    history = implementation_shortfall(_planned("a"), _activity("a", 10.0, 500.1))
    refreshed = refresh_shortfall_history(history, _activity("other", 5.0, 1.0))
    pd.testing.assert_frame_equal(refreshed.reset_index(drop=True), history.reset_index(drop=True))
    assert refresh_shortfall_history(history, pd.DataFrame()) is history


def test_unresolved_rows_set_the_activity_lookback():
    history = pd.DataFrame(
        [
            {"decision_bar": "2026-11-12T19:45:00+00:00", "missed": True, "fill_rate": 0.0},
            {"decision_bar": "2026-11-13T15:30:00+00:00", "missed": False, "fill_rate": 1.0},
            {"decision_bar": "2026-10-01T15:30:00+00:00", "missed": True, "fill_rate": 0.0},
        ]
    )
    now = pd.Timestamp("2026-11-13T16:00:00Z")
    assert unresolved_shortfall_since(history, now) == pd.Timestamp("2026-11-12T19:45:00Z")
    assert unresolved_shortfall_since(history.iloc[[1]], now) is None
    assert unresolved_shortfall_since(pd.DataFrame(), now) is None


@pytest.mark.parametrize("content", ["", 'symbol,shortfall_bps\n"SPY,3.0\n'])
def test_unreadable_shortfall_log_is_set_aside_not_fatal(tmp_path, content):
    # An empty file (EmptyDataError) or a truncated quote (ParserError) used to
    # abort the trading cycle before planning.
    path = tmp_path / "implementation_shortfall.csv"
    path.write_text(content, encoding="utf-8")
    history = load_shortfall_history(path)
    assert history.empty
    assert not path.exists()
    assert len(list(tmp_path.glob("implementation_shortfall.csv.corrupt-*"))) == 1
    assert not shortfall_kill_switch_active(history, "SPY")


def test_missing_log_is_empty_history(tmp_path):
    assert load_shortfall_history(tmp_path / "absent.csv").empty


def test_kill_switch_latches_until_manual_reset(tmp_path):
    history = pd.DataFrame(
        [
            {"decision_bar": "2026-11-10T15:30:00+00:00", "symbol": "SPY", "shortfall_bps": 6.0, "missed": False},
            {"decision_bar": "2026-11-11T15:30:00+00:00", "symbol": "SPY", "shortfall_bps": 5.0, "missed": False},
        ]
    )
    assert shortfall_kill_switch_active(history, "SPY")
    assert read_kill_switch_reset(tmp_path) is None

    reset_at = record_kill_switch_reset(tmp_path, pd.Timestamp("2026-11-12T00:00:00Z"))
    assert read_kill_switch_reset(tmp_path) == reset_at
    assert not shortfall_kill_switch_active(history, "SPY", since=reset_at)
    assert pd.isna(trailing_shortfall_bps(history, "SPY", since=reset_at))

    # New post-reset evidence counts again.
    after = pd.concat(
        [history, pd.DataFrame([{"decision_bar": "2026-11-13T15:30:00+00:00", "symbol": "SPY", "shortfall_bps": 1.0, "missed": False}])],
        ignore_index=True,
    )
    assert trailing_shortfall_bps(after, "SPY", since=reset_at) == pytest.approx(1.0)


def test_missed_flags_read_back_as_strings_are_still_excluded():
    history = pd.DataFrame(
        [
            {"symbol": "SPY", "shortfall_bps": 1.0, "missed": "False"},
            {"symbol": "SPY", "shortfall_bps": 90.0, "missed": "True"},
        ]
    )
    assert trailing_shortfall_bps(history, "SPY") == pytest.approx(1.0)


def _rows(*values, start="2026-11-10"):
    stamps = pd.date_range(start, periods=len(values), freq="D", tz="UTC")
    return pd.DataFrame(
        [{"decision_bar": stamp.isoformat(), "symbol": "SPY", "shortfall_bps": value, "missed": False}
         for stamp, value in zip(stamps, values)]
    )


def test_latched_switch_ignores_later_favourable_fills(tmp_path):
    from project_geld.paper import evaluate_kill_switch, kill_switch_latched

    breached = _rows(*([2.5] * 20))
    assert evaluate_kill_switch(tmp_path, breached, "SPY")
    assert kill_switch_latched(tmp_path)
    # The sleeve's own flattening fill (-10 bps) pulls the trailing mean below
    # the gate; recomputing alone would re-enable the sleeve and let it flap.
    after_exit = pd.concat([breached, _rows(-10.0, start="2026-12-01")], ignore_index=True)
    assert trailing_shortfall_bps(after_exit, "SPY") < 2.0
    assert evaluate_kill_switch(tmp_path, after_exit, "SPY")
    # A quarantined/empty log cannot release it either.
    assert evaluate_kill_switch(tmp_path, pd.DataFrame(), "SPY")


def test_reset_releases_the_latch_and_ignores_old_rows(tmp_path):
    from project_geld.paper import evaluate_kill_switch, kill_switch_latched, record_kill_switch_latch

    record_kill_switch_latch(tmp_path, 3.0, pd.Timestamp("2026-11-20T00:00:00Z"))
    record_kill_switch_reset(tmp_path, pd.Timestamp("2026-11-21T00:00:00Z"))
    assert not kill_switch_latched(tmp_path)
    assert not evaluate_kill_switch(tmp_path, _rows(*([5.0] * 5)), "SPY")  # all pre-reset
    fresh = _rows(4.0, start="2026-11-22")
    assert evaluate_kill_switch(tmp_path, fresh, "SPY", now=pd.Timestamp("2026-11-22T16:00:00Z"))
    assert kill_switch_latched(tmp_path)
