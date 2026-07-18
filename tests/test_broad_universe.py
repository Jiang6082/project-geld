from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from project_geld.broad_universe import (
    BroadUniverseRules,
    asset_master_frame,
    membership_periods_from_selections,
    monthly_candidate_rows,
    select_top_liquid,
)


def test_asset_master_keeps_common_and_rejects_fund():
    assets = [
        SimpleNamespace(
            symbol="AAA",
            name="Alpha Inc. Common Stock",
            exchange="NASDAQ",
            status="ACTIVE",
            tradable=True,
        ),
        SimpleNamespace(
            symbol="XYZ",
            name="Example Growth ETF",
            exchange="NYSE",
            status="ACTIVE",
            tradable=True,
        ),
    ]
    frame = asset_master_frame(assets).set_index("symbol")
    assert bool(frame.at["AAA", "included"])
    assert not bool(frame.at["XYZ", "included"])


def test_monthly_liquidity_selection_uses_trailing_data(synthetic_bars):
    sessions = pd.DatetimeIndex(sorted(synthetic_bars["timestamp"].unique()))
    month_ends = (
        pd.Series(sessions, index=sessions)
        .groupby(sessions.tz_localize(None).to_period("M"))
        .max()
    )
    rules = BroadUniverseRules(
        top_n=2,
        minimum_price=1,
        minimum_history_sessions=20,
        dollar_volume_window=10,
        minimum_dollar_volume=1,
    )
    candidates = monthly_candidate_rows(
        synthetic_bars, pd.DatetimeIndex(month_ends), rules
    )
    selected = select_top_liquid(candidates, rules)
    assert selected.groupby("timestamp").size().le(2).all()
    assert selected["history_sessions"].ge(20).all()


def test_selections_become_membership_periods():
    sessions = pd.date_range("2024-01-02", "2024-03-29", freq="B", tz="UTC")
    month_ends = pd.DatetimeIndex(
        [
            pd.Timestamp("2024-01-31", tz="UTC"),
            pd.Timestamp("2024-02-29", tz="UTC"),
            pd.Timestamp("2024-03-29", tz="UTC"),
        ]
    )
    selected = pd.DataFrame(
        {
            "timestamp": [month_ends[0], month_ends[1]],
            "symbol": ["AAA", "AAA"],
        }
    )
    periods = membership_periods_from_selections(selected, month_ends, sessions)
    assert periods["AAA"] == [["2024-01-31", "2024-03-28"]]
