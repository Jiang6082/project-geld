from __future__ import annotations

import pandas as pd
import pytest

from project_geld.research import (
    MembershipAllocation,
    StaticAllocation,
    one_at_a_time_variants,
)


def test_static_allocation_sums_to_requested_exposure(synthetic_bars):
    targets = StaticAllocation(gross_exposure=0.75).generate_targets(synthetic_bars)
    totals = targets.groupby("timestamp")["target_weight"].sum()
    assert totals.to_numpy() == pytest.approx([0.75] * len(totals))


def test_stability_variants_are_unique_and_include_base():
    base = {
        "formation_lookback": 252,
        "skip_recent": 21,
        "volatility_lookback": 60,
        "max_symbols": 5,
        "exit_rank": 10,
        "rebalance_every": 10,
    }
    variants = one_at_a_time_variants(base)
    assert variants[0][0] == "base"
    assert len({label for label, _ in variants}) == len(variants)


def test_membership_allocation_removes_expired_symbol(synthetic_bars):
    dates = sorted(synthetic_bars["timestamp"].unique())
    cutoff = dates[-10]
    strategy = MembershipAllocation(
        membership_periods={
            "AAA": [[str(pd.Timestamp(dates[0]).date()), str(pd.Timestamp(cutoff).date())]],
            "BBB": [[str(pd.Timestamp(dates[0]).date()), None]],
        }
    )
    targets = strategy.generate_targets(synthetic_bars)
    expired = targets[
        (targets["symbol"] == "AAA") & (targets["timestamp"] > cutoff)
    ]
    assert expired["target_weight"].eq(0.0).all()
