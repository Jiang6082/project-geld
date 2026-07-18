from datetime import datetime, timezone

import pandas as pd
import pytest

from project_geld.config import BacktestConfig, RiskConfig, load_config, validate_config
from project_geld.credentials import alpaca_environment_names, load_alpaca_credentials
from project_geld.intraday import (
    label_native_intraday_bar_ends,
    resample_intraday_bars,
    run_intraday_backtest,
)
from project_geld.strategies.intra_v1 import IntraV1
from project_geld.strategies.intra_v2 import IntraV2
from project_geld.strategies.intra_v3 import IntraV3


def minute_bars() -> pd.DataFrame:
    timestamps = pd.date_range(
        "2026-07-13 13:30", "2026-07-13 14:01", freq="min", tz="UTC"
    )
    rows = []
    for symbol, step in [("SPY", 0.01), ("AAPL", 0.10)]:
        for index, timestamp in enumerate(timestamps):
            close = 100 + step * index
            rows.append(
                {
                    "timestamp": timestamp,
                    "symbol": symbol,
                    "open": close - step,
                    "high": close + 0.05,
                    "low": close - 0.05,
                    "close": close,
                    "volume": 100_000,
                }
            )
    return pd.DataFrame(rows)


def test_profile_credentials_are_isolated(monkeypatch):
    keys, secrets = alpaca_environment_names("intraday")
    assert keys[0] == "ALPACA_INTRADAY_API_KEY"
    assert secrets[0] == "ALPACA_INTRADAY_SECRET_KEY"
    monkeypatch.setenv(keys[0], "paper-key")
    monkeypatch.setenv(secrets[0], "paper-secret")
    assert load_alpaca_credentials("intraday") == ("paper-key", "paper-secret")


def test_separate_account_configs_load():
    swing = load_config("configs/paper-daily-v4.toml")
    intraday = load_config("configs/paper-intra-v1.toml")
    validate_config(swing)
    validate_config(intraday)
    assert swing.account.credential_profile == "SWING"
    assert swing.strategy.parameters["active_weight"] == 0.60
    assert intraday.account.credential_profile == "INTRADAY"
    assert intraday.risk.max_gross_exposure == 0.70
    daily_v5 = load_config("configs/research-daily-v5.toml")
    intra_v2 = load_config("configs/research-intra-v2.toml")
    validate_config(daily_v5)
    validate_config(intra_v2)
    assert daily_v5.strategy.name == "daily_v5"
    assert intra_v2.strategy.name == "intra_v2"
    assert not daily_v5.paper.enabled
    assert not intra_v2.paper.enabled
    intra_v3 = load_config("configs/research-intra-v3.toml")
    validate_config(intra_v3)
    assert intra_v3.strategy.name == "intra_v3"
    assert intra_v3.strategy.parameters["top_n"] == 8
    assert intra_v3.risk.max_gross_exposure == 0.80
    assert not intra_v3.paper.enabled


def test_minute_resampling_labels_bar_end_and_drops_partial_bar():
    bars = resample_intraday_bars(minute_bars(), 15)
    times = bars["timestamp"].drop_duplicates().tolist()
    assert times == [
        pd.Timestamp("2026-07-13 13:45", tz="UTC"),
        pd.Timestamp("2026-07-13 14:00", tz="UTC"),
    ]
    first = bars[
        bars["timestamp"].eq(pd.Timestamp("2026-07-13 13:45", tz="UTC"))
        & bars["symbol"].eq("AAPL")
    ].iloc[0]
    assert first["volume"] == 1_500_000


def test_native_intraday_bars_are_shifted_from_start_to_end():
    native = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2026-07-13 13:30", "2026-07-13 19:45"], utc=True
            ),
            "symbol": ["AAPL", "AAPL"],
            "open": [100, 101],
            "high": [101, 102],
            "low": [99, 100],
            "close": [100.5, 101.5],
            "volume": [1_000_000, 1_000_000],
        }
    )
    labeled = label_native_intraday_bar_ends(native, 15)
    assert labeled["timestamp"].tolist() == [
        pd.Timestamp("2026-07-13 13:45", tz="UTC"),
        pd.Timestamp("2026-07-13 20:00", tz="UTC"),
    ]


def test_intraday_strategy_enters_after_start_and_flattens():
    local_times = ["09:45", "10:00", "10:15", "10:30", "15:45", "16:00"]
    timestamps = [
        pd.Timestamp(f"2026-07-13 {value}", tz="America/New_York").tz_convert("UTC")
        for value in local_times
    ]
    rows = []
    for symbol, closes in {
        "SPY": [100, 100.1, 100.2, 100.3, 100.4, 100.5],
        "AAPL": [100, 101, 103, 104, 105, 106],
    }.items():
        for timestamp, close in zip(timestamps, closes):
            rows.append(
                {
                    "timestamp": timestamp,
                    "symbol": symbol,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 2_000_000,
                }
            )
    strategy = IntraV1(
        lookback_bars=2,
        top_n=1,
        gross_exposure=0.5,
        max_position_weight=0.5,
        require_benchmark_above_vwap=False,
    )
    targets = strategy.generate_targets(pd.DataFrame(rows))
    aapl = targets[targets["symbol"].eq("AAPL")].set_index("timestamp")
    assert aapl.loc[timestamps[2], "target_weight"] == 0.5
    assert aapl.loc[timestamps[4], "target_weight"] == 0.0


def test_intra_v2_waits_for_recovery_and_enters_once():
    local_times = ["09:30", "09:45", "10:00", "10:15", "10:30", "10:45", "15:45"]
    timestamps = [
        pd.Timestamp(f"2026-07-13 {value}", tz="America/New_York").tz_convert("UTC")
        for value in local_times
    ]
    rows = []
    for symbol, closes in {
        "SPY": [100, 100.1, 100.2, 100.3, 100.4, 100.5, 100.6],
        "AAPL": [100, 99, 98, 97, 97.5, 98, 99],
    }.items():
        for timestamp, close in zip(timestamps, closes):
            rows.append(
                {
                    "timestamp": timestamp,
                    "symbol": symbol,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 2_000_000,
                }
            )
    strategy = IntraV2(
        top_n=1,
        gross_exposure=0.5,
        max_position_weight=0.5,
        min_relative_dislocation=0.003,
    )
    targets = strategy.generate_targets(pd.DataFrame(rows))
    aapl = targets[targets["symbol"].eq("AAPL")].set_index("timestamp")
    assert aapl.loc[timestamps[4], "target_weight"] == 0.5
    assert aapl.loc[timestamps[5], "target_weight"] == 0.5
    assert aapl.loc[timestamps[6], "target_weight"] == 0.0


def test_intra_v3_ranks_and_caps_eight_qualifying_names():
    local_times = ["09:30", "09:45", "10:00", "10:15", "10:30", "15:45"]
    timestamps = [
        pd.Timestamp(f"2026-07-13 {value}", tz="America/New_York").tz_convert("UTC")
        for value in local_times
    ]
    rows = []
    series = {"SPY": [100, 100.1, 100.2, 100.3, 100.4, 100.5]}
    for index in range(10):
        series[f"S{index}"] = [101, 100.5, 100, 99, 98 - index * 0.1, 99]
    for symbol, closes in series.items():
        for timestamp, close in zip(timestamps, closes):
            rows.append(
                {
                    "timestamp": timestamp,
                    "symbol": symbol,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 2_000_000,
                }
            )
    strategy = IntraV3()
    targets = strategy.generate_targets(pd.DataFrame(rows))
    entry = targets[targets["timestamp"].eq(timestamps[4])]
    selected = entry[entry["target_weight"].gt(0)]
    assert len(selected) == 8
    assert selected["target_weight"].eq(0.10).all()
    assert selected["target_weight"].sum() == pytest.approx(0.80)
    assert set(selected["symbol"]) == {f"S{index}" for index in range(2, 10)}


class AlwaysIntradayLong:
    name = "always_intraday_long"
    warmup_bars = 0

    def generate_targets(self, bars):
        targets = bars[bars["symbol"].eq("AAPL")][["timestamp", "symbol"]].copy()
        targets["target_weight"] = 0.5
        targets["score"] = 1.0
        return targets


def test_intraday_backtest_forces_positions_flat_each_session():
    rows = []
    for day in ["2026-07-13", "2026-07-14"]:
        for clock, price in [("10:00", 100), ("10:15", 101), ("16:00", 102)]:
            timestamp = pd.Timestamp(
                f"{day} {clock}", tz="America/New_York"
            ).tz_convert("UTC")
            for symbol in ["AAPL", "SPY"]:
                rows.append(
                    {
                        "timestamp": timestamp,
                        "symbol": symbol,
                        "open": price,
                        "high": price,
                        "low": price,
                        "close": price,
                        "volume": 1_000_000,
                    }
                )
    result = run_intraday_backtest(
        pd.DataFrame(rows),
        AlwaysIntradayLong(),
        BacktestConfig(initial_cash=10_000, slippage_bps=0),
        RiskConfig(max_position_weight=0.5, min_trade_notional=1),
        tradable_symbols=["AAPL"],
        context_symbols=["SPY"],
    )
    forced = result.trades[result.trades["exit_reason"].eq("intraday_session_end")]
    assert len(forced) == 2
    assert result.equity.groupby("session_date").tail(1)["gross_exposure"].eq(0).all()
