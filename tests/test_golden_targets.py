"""Frozen target snapshots for the strategies that run on paper accounts.

Intra V15 inherits from V13 -> V12 -> V8 -> V7 and Daily V4 wraps Momentum V3,
so an edit to a research-only parent can silently change what a paper account
trades. These tests run the deployed TOML parameters on fixed synthetic data and
compare every target-weight change to a stored snapshot.

If a change is intentional: bump the strategy's ``version``, then regenerate

    GELD_REGENERATE_GOLDEN=1 pytest tests/test_golden_targets.py

and commit the updated files under ``tests/fixtures/golden/`` with the change.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tomllib

import numpy as np
import pandas as pd
import pytest

from project_geld.data import SyntheticBarSource
from project_geld.paper import align_rebalance_to_latest_bar
from project_geld.strategies.registry import create_strategy


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "golden"
REGENERATE = os.getenv("GELD_REGENERATE_GOLDEN") == "1"
CHANGE_TOLERANCE = 1e-10
WEIGHT_TOLERANCE = 1e-9


def _deployed_strategy(config_name: str, paper: bool = False):
    config = tomllib.loads((ROOT / "configs" / config_name).read_text(encoding="utf-8"))
    strategy = create_strategy(config["strategy"]["name"], config["strategy"]["parameters"])
    # Paper planning re-anchors the rebalance schedule at the latest bar.
    return align_rebalance_to_latest_bar(strategy) if paper else strategy


def _daily_bars() -> pd.DataFrame:
    symbols = ["SPY"] + [f"S{index:02d}" for index in range(48)]
    return SyntheticBarSource(seed=3).fetch(
        symbols,
        datetime(2021, 1, 1, tzinfo=timezone.utc),
        datetime(2023, 12, 29, tzinfo=timezone.utc),
    )


def _intraday_bars(n_symbols: int = 30, sessions: int = 90, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2026-01-05", periods=sessions)
    rows = []
    for symbol in ["SPY"] + [f"T{index:02d}" for index in range(n_symbols)]:
        price = 50 + 10 * rng.random()
        for day in days:
            price *= 1 + rng.normal(0, 0.008)
            start = pd.Timestamp(f"{day.date()} 09:45", tz="America/New_York")
            for step in range(26):
                stamp = (start + pd.Timedelta(minutes=15 * step)).tz_convert("UTC")
                open_ = price
                price = price * (1 + rng.normal(0, 0.0015 if symbol == "SPY" else 0.005))
                rows.append(
                    (stamp, symbol, open_, max(open_, price) * 1.001,
                     min(open_, price) * 0.999, price, float(rng.integers(20_000, 200_000)))
                )
    return pd.DataFrame(
        rows, columns=["timestamp", "symbol", "open", "high", "low", "close", "volume"]
    )


def _weight_changes(targets: pd.DataFrame) -> pd.DataFrame:
    """Every (timestamp, symbol) whose target weight differs from the prior bar."""
    wide = (
        targets.pivot(index="timestamp", columns="symbol", values="target_weight")
        .sort_index()
        .fillna(0.0)
    )
    changed = wide.sub(wide.shift(1).fillna(0.0)).abs().gt(CHANGE_TOLERANCE)
    changes = wide.where(changed).stack().rename("target_weight").reset_index()
    changes["timestamp"] = pd.to_datetime(changes["timestamp"], utc=True).map(
        lambda stamp: stamp.isoformat()
    )
    return changes.sort_values(["timestamp", "symbol"]).reset_index(drop=True)


CASES = {
    # Backtest schedule (start-anchored) and the paper planner's schedule.
    "daily_v4": ("paper-daily-v4.toml", _daily_bars, False),
    "daily_v4_paper": ("paper-daily-v4.toml", _daily_bars, True),
    "intra_v15": ("paper-intra-v15.toml", _intraday_bars, True),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_deployed_strategy_targets_match_frozen_snapshot(case):
    config_name, make_bars, paper = CASES[case]
    strategy = _deployed_strategy(config_name, paper)
    changes = _weight_changes(strategy.generate_targets(make_bars()))
    snapshot_path = FIXTURES / f"{case}_target_changes.csv"
    meta_path = FIXTURES / f"{case}_meta.json"

    if REGENERATE:
        FIXTURES.mkdir(parents=True, exist_ok=True)
        changes.to_csv(snapshot_path, index=False, float_format="%.12g")
        meta_path.write_text(
            json.dumps({"strategy": strategy.name, "version": strategy.version,
                        "config": f"configs/{config_name}", "changes": len(changes)}, indent=2)
            + "\n",
            encoding="utf-8",
        )
        pytest.skip(f"regenerated {snapshot_path.name}")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    expected = pd.read_csv(snapshot_path)
    assert strategy.version == meta["version"], (
        f"{strategy.name} is now {strategy.version!r} but the snapshot was frozen at "
        f"{meta['version']!r}; regenerate the golden fixtures for the new version."
    )
    message = (
        f"{strategy.version} targets changed for configs/{config_name}. If this is "
        "intentional, bump the strategy version and regenerate (see module docstring)."
    )
    keys = ["timestamp", "symbol"]
    actual_keys = list(changes[keys].itertuples(index=False, name=None))
    expected_keys = list(expected[keys].itertuples(index=False, name=None))
    assert actual_keys == expected_keys, message
    np.testing.assert_allclose(
        changes["target_weight"].to_numpy(),
        expected["target_weight"].to_numpy(),
        rtol=0,
        atol=WEIGHT_TOLERANCE,
        err_msg=message,
    )


def test_snapshots_exercise_both_sleeves():
    """Guard the guard: an empty snapshot would freeze nothing."""
    daily = pd.read_csv(FIXTURES / "daily_v4_target_changes.csv")
    paper = pd.read_csv(FIXTURES / "daily_v4_paper_target_changes.csv")
    intraday = pd.read_csv(FIXTURES / "intra_v15_target_changes.csv")
    assert daily["symbol"].ne("SPY").sum() > 50
    assert paper["symbol"].ne("SPY").sum() > 50
    # The two schedules rebalance on different bars, so they must differ.
    assert not daily[["timestamp", "symbol"]].equals(paper[["timestamp", "symbol"]])
    assert intraday["symbol"].eq("SPY").sum() > 50
    assert intraday["symbol"].ne("SPY").sum() >= 10
