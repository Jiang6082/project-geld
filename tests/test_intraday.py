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
from project_geld.strategies.intra_v4 import IntraV4
from project_geld.strategies.intra_v5 import IntraV5
from project_geld.strategies.intra_v6 import IntraV6
from project_geld.strategies.intra_v7 import IntraV7
from project_geld.strategies.intra_v8 import IntraV8
from project_geld.strategies.intra_v9 import IntraV9
from project_geld.strategies.intra_v10 import IntraV10
from project_geld.strategies.intra_v11 import FEATURE_COLUMNS, IntraV11
from project_geld.strategies.intra_v12 import IntraV12
from project_geld.strategies.intra_v13 import IntraV13
from project_geld.strategies.intra_v14 import IntraV14
from project_geld.strategies.intra_v15 import IntraV15


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
    assert swing.strategy.parameters["active_weight"] == 0.25
    assert swing.strategy.parameters["core_weight"] == 0.75
    assert swing.strategy.parameters["regime_enabled"] is True
    assert swing.data.feed == "sip"
    assert swing.paper.enabled
    assert swing.paper.market_data_delay_minutes == 20
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

    intra_v15 = load_config("configs/paper-intra-v15.toml")
    validate_config(intra_v15)
    assert intra_v15.account.credential_profile == "INTRADAY"
    assert intra_v15.strategy.name == "intra_v15"
    assert intra_v15.strategy.parameters["base_signal_time"] == "10:30"
    assert intra_v15.strategy.parameters["base_long_weight"] == 0.05
    assert intra_v15.strategy.parameters["base_short_weight"] == 0.025
    assert intra_v15.strategy.parameters["base_weak_weight"] == 0.005
    # The minimum-trade floor must sit below the weak-signal leg so it executes.
    assert intra_v15.risk.min_trade_pct_equity < 0.005
    assert intra_v15.risk.max_gross_exposure == 0.45
    assert intra_v15.backtest.symbol_slippage_bps["SPY"] == 2.0
    assert intra_v15.paper.enabled
    intra_v4 = load_config("configs/research-intra-v4.toml")
    validate_config(intra_v4)
    assert intra_v4.strategy.name == "intra_v4"
    assert intra_v4.strategy.parameters["top_n"] == 8
    assert intra_v4.risk.max_gross_exposure == 0.80
    assert not intra_v4.paper.enabled
    intra_v5 = load_config("configs/research-intra-v5.toml")
    validate_config(intra_v5)
    assert intra_v5.strategy.name == "intra_v5"
    assert intra_v5.strategy.parameters["confirmation_bars"] == 1
    assert not intra_v5.paper.enabled
    intra_v6 = load_config("configs/research-intra-v6.toml")
    validate_config(intra_v6)
    assert intra_v6.strategy.name == "intra_v6"
    assert intra_v6.strategy.parameters["min_relative_dislocation"] == 0.01
    assert not intra_v6.paper.enabled
    intra_v7 = load_config("configs/research-intra-v7.toml")
    validate_config(intra_v7)
    assert intra_v7.strategy.name == "intra_v7"
    assert intra_v7.backtest.allow_short
    assert not intra_v7.paper.enabled
    intra_v8 = load_config("configs/research-intra-v8.toml")
    validate_config(intra_v8)
    assert intra_v8.strategy.name == "intra_v8"
    assert intra_v8.strategy.parameters["daily_trend_sessions"] == 20
    assert intra_v8.backtest.allow_short
    assert not intra_v8.paper.enabled
    broad_v8 = load_config("configs/research-intra-v8-broad.toml")
    validate_config(broad_v8)
    assert broad_v8.strategy.name == "intra_v8"
    assert len(broad_v8.universe.symbols) == 100
    assert broad_v8.backtest.allow_short
    assert not broad_v8.paper.enabled


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


def test_intra_v4_selects_strongest_eight_relative_winners():
    local_times = ["09:30", "09:45", "10:00", "10:15", "10:30", "15:45"]
    timestamps = [
        pd.Timestamp(f"2026-07-13 {value}", tz="America/New_York").tz_convert("UTC")
        for value in local_times
    ]
    rows = []
    series = {"SPY": [100, 100.1, 100.2, 100.3, 100.4, 100.5]}
    for index in range(10):
        series[f"S{index}"] = [99, 99.5, 100, 101, 102 + index * 0.1, 101]
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
    strategy = IntraV4(require_benchmark_above_vwap=False)
    targets = strategy.generate_targets(pd.DataFrame(rows))
    entry = targets[targets["timestamp"].eq(timestamps[4])]
    selected = entry[entry["target_weight"].gt(0)]
    assert len(selected) == 8
    assert selected["target_weight"].eq(0.10).all()
    assert selected["target_weight"].sum() == pytest.approx(0.80)
    assert set(selected["symbol"]) == {f"S{index}" for index in range(2, 10)}


def test_intra_v5_waits_for_a_break_above_the_signal_bar_high():
    local_times = ["09:30", "09:45", "10:00", "10:15", "10:30", "10:45", "11:00", "15:45"]
    timestamps = [
        pd.Timestamp(f"2026-07-13 {value}", tz="America/New_York").tz_convert("UTC")
        for value in local_times
    ]
    rows = []
    series = {
        "SPY": [100, 100.1, 100.2, 100.3, 100.4, 100.5, 100.6, 100.7],
        "RECOVERS": [100, 99, 98, 97, 96, 97, 98, 99],
        "FALLS": [100, 99, 98, 97, 96, 95, 94, 93],
    }
    for symbol, closes in series.items():
        for timestamp, close in zip(timestamps, closes):
            rows.append(
                {
                    "timestamp": timestamp,
                    "symbol": symbol,
                    "open": close,
                    "high": close + (0.2 if symbol != "RECOVERS" else 0.0),
                    "low": close - 0.2,
                    "close": close,
                    "volume": 2_000_000,
                }
            )
    strategy = IntraV5(
        top_n=1,
        gross_exposure=0.5,
        max_position_weight=0.5,
        min_relative_dislocation=0.003,
        require_benchmark_above_vwap=False,
    )
    targets = strategy.generate_targets(pd.DataFrame(rows))
    signal = targets[targets["timestamp"].eq(timestamps[4])]
    confirmation = targets[targets["timestamp"].eq(timestamps[5])]
    flatten = targets[targets["timestamp"].eq(timestamps[7])]
    assert signal["target_weight"].eq(0).all()
    assert confirmation.set_index("symbol").at["RECOVERS", "target_weight"] == 0.5
    assert confirmation.set_index("symbol").at["FALLS", "target_weight"] == 0.0
    assert flatten["target_weight"].eq(0).all()


def test_intra_v6_uses_the_stricter_dislocation_threshold():
    strategy = IntraV6()
    assert strategy.min_relative_dislocation == 0.01
    assert strategy.name == "intra_v6"


def test_intra_v7_waits_for_a_break_below_the_signal_bar_low():
    local_times = ["09:30", "09:45", "10:00", "10:15", "10:30", "10:45", "11:00", "15:45"]
    timestamps = [
        pd.Timestamp(f"2026-07-13 {value}", tz="America/New_York").tz_convert("UTC")
        for value in local_times
    ]
    rows = []
    series = {
        "SPY": [100, 100.1, 100.2, 100.3, 100.4, 100.5, 100.6, 100.7],
        "BREAKS": [100, 99, 98, 97, 96, 95, 94, 93],
        "RECOVERS": [100, 99, 98, 97, 96, 97, 98, 99],
    }
    for symbol, closes in series.items():
        for timestamp, close in zip(timestamps, closes):
            rows.append(
                {
                    "timestamp": timestamp,
                    "symbol": symbol,
                    "open": close,
                    "high": close + 0.2,
                    "low": close,
                    "close": close,
                    "volume": 2_000_000,
                }
            )
    strategy = IntraV7(
        top_n=1,
        gross_exposure=0.1,
        max_position_weight=0.1,
        min_relative_dislocation=0.003,
        require_benchmark_above_vwap=False,
    )
    targets = strategy.generate_targets(pd.DataFrame(rows))
    signal = targets[targets["timestamp"].eq(timestamps[4])]
    confirmation = targets[targets["timestamp"].eq(timestamps[5])]
    flatten = targets[targets["timestamp"].eq(timestamps[7])]
    assert signal["target_weight"].eq(0).all()
    assert confirmation.set_index("symbol").at["BREAKS", "target_weight"] == -0.1
    assert confirmation.set_index("symbol").at["RECOVERS", "target_weight"] == 0.0
    assert flatten["target_weight"].eq(0).all()

    delayed = IntraV7(
        top_n=1,
        gross_exposure=0.1,
        max_position_weight=0.1,
        min_relative_dislocation=0.003,
        require_benchmark_above_vwap=False,
        entry_delay_bars=1,
    ).generate_targets(pd.DataFrame(rows))
    delayed_confirmation = delayed[delayed["timestamp"].eq(timestamps[5])]
    delayed_entry = delayed[delayed["timestamp"].eq(timestamps[6])]
    assert delayed_confirmation["target_weight"].eq(0).all()
    assert delayed_entry.set_index("symbol").at["BREAKS", "target_weight"] == -0.1


def test_intra_v8_requires_a_prior_daily_downtrend():
    strategy = IntraV8()
    assert strategy.daily_trend_sessions == 20
    assert strategy.require_below_prior_close
    assert strategy.name == "intra_v8"


def test_intra_v9_requires_unusual_signal_bar_volume():
    strategy = IntraV9()
    assert strategy.relative_volume_sessions == 20
    assert strategy.min_relative_volume == 1.5
    assert strategy.name == "intra_v9"


def test_intra_v10_normalizes_dislocation_by_prior_variability():
    strategy = IntraV10()
    assert strategy.relative_volatility_sessions == 20
    assert strategy.min_dislocation_sigma == 2.0
    assert strategy.name == "intra_v10"


def test_intra_v11_uses_a_causal_rolling_model():
    strategy = IntraV11()
    assert strategy.min_training_sessions == 252
    assert strategy.min_calibration_samples == 12
    assert strategy.prediction_threshold < 0
    assert strategy.name == "intra_v11"


def test_intra_v11_ridge_forecast_uses_training_relationship():
    rows = []
    for value in range(-20, 21):
        row = {column: 0.0 for column in FEATURE_COLUMNS}
        row["relative_dislocation"] = value / 100.0
        row["label"] = -0.5 * row["relative_dislocation"]
        rows.append(row)
    training = pd.DataFrame(rows)
    current = pd.DataFrame(
        [{**{column: 0.0 for column in FEATURE_COLUMNS}, "relative_dislocation": 0.10}],
        index=["TEST"],
    )
    prediction = IntraV11.fit_predict(training, current, 0.01, 1.0, 10.0)
    assert prediction.at["TEST"] < -0.04


def test_intra_v12_requires_quiet_volume_and_a_decisive_break():
    strategy = IntraV12()
    assert strategy.relative_volume_sessions == 20
    assert strategy.max_relative_volume == 1.5
    assert strategy.min_confirmation_break == 0.0025
    assert strategy.name == "intra_v12"


def test_intra_v12_can_delay_entry_without_changing_confirmation():
    strategy = IntraV12(entry_delay_bars=1)
    assert strategy.confirmation_bars == 1
    assert strategy.entry_delay_bars == 1


def test_intra_v12_accepts_point_in_time_membership():
    strategy = IntraV12(
        membership_periods={"AAPL": [["2026-01-01", "2026-12-31"]]}
    )
    assert "AAPL" in strategy.membership_periods
    assert strategy.membership_mask(
        pd.Timestamp("2026-06-01"), ["AAPL", "MSFT"]
    ).to_dict() == {"AAPL": True, "MSFT": False}
    assert not strategy.membership_mask(
        pd.Timestamp("2027-01-01"), ["AAPL"]
    ).at["AAPL"]


def test_intra_v13_enables_causal_risk_and_diversification_controls():
    strategy = IntraV13()
    assert strategy.daily_volatility_sessions == 20
    assert strategy.max_annualized_daily_volatility == 1.50
    assert strategy.min_market_breadth == 0.45
    assert strategy.correlation_lookback_sessions == 60
    assert strategy.max_pairwise_correlation == 0.85


def test_intra_v13_skips_a_highly_correlated_second_candidate():
    strategy = IntraV13(
        top_n=2,
        gross_exposure=0.2,
        correlation_lookback_sessions=4,
        max_pairwise_correlation=0.75,
    )
    returns = pd.DataFrame(
        {
            "AAA": [0.01, -0.02, 0.03, -0.01],
            "BBB": [0.01, -0.02, 0.03, -0.01],
            "CCC": [-0.02, -0.01, 0.01, 0.02],
        },
        index=pd.date_range("2026-01-01", periods=4),
    )
    selected = strategy.diversified_selection(
        [("AAA", 3.0), ("BBB", 2.0), ("CCC", 1.0)],
        returns,
        returns.index[-1],
    )
    assert selected == ["AAA", "CCC"]


def test_intra_v14_selects_a_daily_market_neutral_book_and_flattens():
    clocks = ["09:45", "10:00", "10:15", "10:30", "15:45"]
    changes = {
        "AAA": 0.020,
        "BBB": 0.010,
        "CCC": -0.010,
        "DDD": -0.020,
        "SPY": 0.000,
    }
    rows = []
    for index, clock in enumerate(clocks):
        timestamp = pd.Timestamp(
            f"2026-07-13 {clock}", tz="America/New_York"
        ).tz_convert("UTC")
        for symbol, change in changes.items():
            close = 100.0 * (1.0 + change * index)
            rows.append(
                {
                    "timestamp": timestamp,
                    "symbol": symbol,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1_000_000,
                }
            )
    strategy = IntraV14(
        lookback_bars=3,
        names_per_side=2,
        gross_exposure=0.4,
        max_position_weight=0.1,
        min_cumulative_dollar_volume=0,
    )
    targets = strategy.generate_targets(pd.DataFrame(rows))
    signal = targets[
        targets["timestamp"].eq(
            pd.Timestamp("2026-07-13 10:30", tz="America/New_York").tz_convert(
                "UTC"
            )
        )
    ].set_index("symbol")["target_weight"]
    assert signal.to_dict() == {
        "AAA": 0.1,
        "BBB": 0.1,
        "CCC": -0.1,
        "DDD": -0.1,
    }
    flattened = targets[
        targets["timestamp"].eq(
            pd.Timestamp("2026-07-13 15:45", tz="America/New_York").tz_convert(
                "UTC"
            )
        )
    ]
    assert flattened["target_weight"].eq(0).all()


def test_intra_v14_validates_direction():
    strategy = IntraV14(direction="reversal")
    assert strategy.direction == "reversal"
    with pytest.raises(ValueError, match="direction"):
        IntraV14(direction="random")


def test_intra_v15_adds_a_daily_spy_sleeve_and_flattens():
    clocks = ["09:45", "10:00", "10:15", "10:30", "15:30", "15:45"]
    rows = []
    for index, clock in enumerate(clocks):
        timestamp = pd.Timestamp(
            f"2026-07-13 {clock}", tz="America/New_York"
        ).tz_convert("UTC")
        for symbol in ["SPY", "AAPL"]:
            close = 100.0 + (index if symbol == "SPY" else 0.0)
            rows.append(
                {
                    "timestamp": timestamp,
                    "symbol": symbol,
                    "open": 100.0,
                    "high": close,
                    "low": 100.0,
                    "close": close,
                    "volume": 1_000_000,
                }
            )
    strategy = IntraV15(
        min_bar_dollar_volume=0,
        min_relative_dislocation=1.0,
        daily_trend_sessions=0,
        relative_volume_sessions=0,
        daily_volatility_sessions=0,
        min_market_breadth=0,
        correlation_lookback_sessions=0,
    )
    targets = strategy.generate_targets(pd.DataFrame(rows))
    spy = targets[targets["symbol"].eq("SPY")].set_index("timestamp")
    assert spy.at[
        pd.Timestamp("2026-07-13 10:30", tz="America/New_York").tz_convert("UTC"),
        "target_weight",
    ] == pytest.approx(0.05)
    assert spy.at[
        pd.Timestamp("2026-07-13 15:30", tz="America/New_York").tz_convert("UTC"),
        "target_weight",
    ] == 0.0


def test_intra_v15_requires_benchmark_core_and_valid_base_weight():
    with pytest.raises(ValueError, match="core_symbol"):
        IntraV15(core_symbol="QQQ")
    with pytest.raises(ValueError, match="base_weight"):
        IntraV15(base_weight=0)
    with pytest.raises(ValueError, match="base_min_signal_bps"):
        IntraV15(base_min_signal_bps=-1)
    with pytest.raises(ValueError, match="base_signal_time"):
        IntraV15(base_signal_time="15:30")


@pytest.mark.parametrize(
    ("signal_close", "expected_weight"),
    [(99.97, -0.005), (99.90, -0.025)],
)
def test_intra_v15_scales_weak_and_strong_short_signals(
    signal_close, expected_weight
):
    rows = []
    for clock in ["09:45", "10:00", "10:15", "10:30", "15:30", "15:45"]:
        timestamp = pd.Timestamp(
            f"2026-07-13 {clock}", tz="America/New_York"
        ).tz_convert("UTC")
        for symbol in ["SPY", "AAPL"]:
            close = signal_close if symbol == "SPY" and clock == "10:30" else 100.0
            rows.append(
                {
                    "timestamp": timestamp,
                    "symbol": symbol,
                    "open": 100.0,
                    "high": max(100.0, close),
                    "low": min(100.0, close),
                    "close": close,
                    "volume": 1_000_000,
                }
            )
    strategy = IntraV15(
        min_bar_dollar_volume=0,
        min_relative_dislocation=1.0,
        daily_trend_sessions=0,
        relative_volume_sessions=0,
        daily_volatility_sessions=0,
        min_market_breadth=0,
        correlation_lookback_sessions=0,
    )
    targets = strategy.generate_targets(pd.DataFrame(rows))
    row = targets[
        targets["symbol"].eq("SPY")
        & targets["timestamp"].eq(
            pd.Timestamp("2026-07-13 10:30", tz="America/New_York").tz_convert(
                "UTC"
            )
        )
    ].iloc[0]
    assert row["target_weight"] == pytest.approx(expected_weight)


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
