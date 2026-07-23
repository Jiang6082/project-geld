import pandas as pd
import pytest

from project_geld.strategies.registry import create_strategy


def test_improved_strategies_carry_patched_versions():
    assert create_strategy("daily_v4", {}).version == "Daily V4.0.4"
    assert create_strategy("intra_v15", {}).version == "Intra V15.0.6"
    # The registry key / name stays stable so configs and state files are intact.
    assert create_strategy("daily_v4", {}).name == "daily_v4"
    assert create_strategy("intra_v15", {}).name == "intra_v15"


@pytest.mark.parametrize("name", ["momentum", "trend", "mean_reversion"])
def test_builtin_strategies_emit_valid_long_only_targets(name, synthetic_bars):
    parameters = {
        "momentum": {"lookback": 30, "volatility_lookback": 10, "top_n": 2},
        "trend": {
            "fast_window": 10,
            "slow_window": 30,
            "momentum_lookback": 20,
            "volatility_lookback": 10,
            "top_n": 2,
        },
        "mean_reversion": {
            "reversal_lookback": 3,
            "regime_window": 20,
            "volatility_lookback": 10,
            "top_n": 2,
        },
    }[name]
    targets = create_strategy(name, parameters).generate_targets(synthetic_bars)
    assert {"timestamp", "symbol", "target_weight", "score"}.issubset(targets)
    assert targets["target_weight"].ge(0).all()
    assert targets.groupby("timestamp")["target_weight"].sum().le(1.000001).all()


def test_momentum_is_causal_under_future_price_change(synthetic_bars):
    strategy = create_strategy(
        "momentum", {"lookback": 30, "volatility_lookback": 10, "top_n": 2}
    )
    dates = sorted(synthetic_bars["timestamp"].unique())
    cutoff = dates[-40]
    original = strategy.generate_targets(synthetic_bars)
    changed = synthetic_bars.copy()
    changed.loc[changed["timestamp"] > cutoff, "close"] *= 5
    rerun = strategy.generate_targets(changed)
    columns = ["timestamp", "symbol", "target_weight", "score"]
    pd.testing.assert_frame_equal(
        original.loc[original["timestamp"] <= cutoff, columns].reset_index(drop=True),
        rerun.loc[rerun["timestamp"] <= cutoff, columns].reset_index(drop=True),
    )


def test_momentum_v2_buffers_and_respects_sector_caps(synthetic_bars):
    symbols = sorted(synthetic_bars["symbol"].unique())
    sectors = {
        symbol: ("sector_a" if index < 3 else "sector_b")
        for index, symbol in enumerate(symbols)
    }
    strategy = create_strategy(
        "momentum_v2",
        {
            "formation_lookback": 30,
            "skip_recent": 5,
            "volatility_lookback": 10,
            "fast_window": 5,
            "slow_window": 20,
            "max_symbols": 2,
            "exit_rank": 4,
            "gross_exposure": 0.8,
            "rebalance_every": 5,
            "max_per_sector": 1,
            "sector_map": sectors,
        },
    )
    targets = strategy.generate_targets(synthetic_bars)
    assert targets.groupby("timestamp")["target_weight"].sum().le(0.800001).all()
    active = targets[targets["target_weight"] > 0].copy()
    active["sector"] = active["symbol"].map(sectors)
    assert active.groupby(["timestamp", "sector"]).size().le(1).all()
    weights = targets.pivot(
        index="timestamp", columns="symbol", values="target_weight"
    )
    for index in range(1, len(weights)):
        if index % 5:
            pd.testing.assert_series_equal(
                weights.iloc[index], weights.iloc[index - 1], check_names=False
            )


def test_momentum_v2_respects_point_in_time_membership(synthetic_bars):
    dates = sorted(synthetic_bars["timestamp"].unique())
    start = pd.Timestamp(dates[0]).strftime("%Y-%m-%d")
    cutoff = pd.Timestamp(dates[-20]).strftime("%Y-%m-%d")
    strategy = create_strategy(
        "momentum_v2",
        {
            "formation_lookback": 20,
            "skip_recent": 2,
            "volatility_lookback": 10,
            "fast_window": 5,
            "slow_window": 15,
            "max_symbols": 1,
            "exit_rank": 2,
            "rebalance_every": 1,
            "membership_periods": {"AAA": [[start, cutoff]]},
        },
    )
    targets = strategy.generate_targets(synthetic_bars)
    after = targets[
        (targets["symbol"] == "AAA")
        & (targets["timestamp"] > pd.Timestamp(cutoff, tz="UTC"))
    ]
    assert after["target_weight"].eq(0.0).all()


def test_momentum_v3_is_diversified_and_never_allocates_to_benchmark(
    synthetic_bars,
):
    strategy = create_strategy(
        "momentum_v3",
        {
            "formation_lookback": 30,
            "medium_lookback": 20,
            "skip_recent": 5,
            "beta_lookback": 20,
            "volatility_lookback": 10,
            "correlation_lookback": 20,
            "fast_window": 5,
            "slow_window": 15,
            "max_symbols": 2,
            "exit_rank": 4,
            "max_position_weight": 0.4,
            "maximum_annualized_volatility": 10.0,
            "target_portfolio_volatility": 10.0,
            "max_pairwise_correlation": 1.0,
            "rebalance_every": 5,
            "regime_enabled": False,
            "bullish_exposure": 0.8,
        },
    )
    targets = strategy.generate_targets(synthetic_bars)
    weights = targets.pivot(
        index="timestamp", columns="symbol", values="target_weight"
    )
    assert weights["SPY"].eq(0.0).all()
    assert weights.max(axis=1).le(0.400001).all()
    assert weights.sum(axis=1).le(0.800001).all()
    assert weights.gt(0).sum(axis=1).le(2).all()


def test_momentum_v3_is_causal_under_future_price_change(synthetic_bars):
    parameters = {
        "formation_lookback": 30,
        "medium_lookback": 20,
        "skip_recent": 5,
        "beta_lookback": 20,
        "volatility_lookback": 10,
        "correlation_lookback": 20,
        "fast_window": 5,
        "slow_window": 15,
        "max_symbols": 2,
        "exit_rank": 4,
        "max_position_weight": 0.5,
        "maximum_annualized_volatility": 10.0,
        "target_portfolio_volatility": 10.0,
        "rebalance_every": 5,
    }
    strategy = create_strategy("momentum_v3", parameters)
    dates = sorted(synthetic_bars["timestamp"].unique())
    cutoff = dates[-40]
    original = strategy.generate_targets(synthetic_bars)
    changed = synthetic_bars.copy()
    changed.loc[changed["timestamp"] > cutoff, "close"] *= 5
    rerun = strategy.generate_targets(changed)
    columns = ["timestamp", "symbol", "target_weight", "score"]
    pd.testing.assert_frame_equal(
        original.loc[original["timestamp"] <= cutoff, columns].reset_index(drop=True),
        rerun.loc[rerun["timestamp"] <= cutoff, columns].reset_index(drop=True),
    )


def test_daily_v4_combines_core_and_capped_active_sleeve(synthetic_bars):
    strategy = create_strategy(
        "daily_v4",
        {
            "core_symbol": "SPY",
            "core_weight": 0.6,
            "active_weight": 0.4,
            "active_name_cap": 0.25,
            "no_trade_band": 0.0,
            "rebalance_every": 5,
            "active_parameters": {
                "formation_lookback": 30,
                "medium_lookback": 20,
                "skip_recent": 5,
                "beta_lookback": 20,
                "volatility_lookback": 10,
                "correlation_lookback": 20,
                "fast_window": 5,
                "slow_window": 15,
                "max_symbols": 2,
                "exit_rank": 4,
                "maximum_annualized_volatility": 10.0,
                "max_pairwise_correlation": 1.0,
                "residual_factor_symbols": ["QQQ"],
            },
        },
    )
    targets = strategy.generate_targets(synthetic_bars)
    weights = targets.pivot(
        index="timestamp", columns="symbol", values="target_weight"
    )
    assert weights["SPY"].eq(0.6).all()
    assert weights["QQQ"].eq(0.0).all()
    assert weights.drop(columns=["SPY", "QQQ"]).max(axis=1).le(0.250001).all()
    assert weights.sum(axis=1).le(1.000001).all()
    assert strategy.context_symbols == ["SPY", "QQQ"]


def test_v4_external_features_are_filing_dated_and_benchmark_aware(
    synthetic_bars, tmp_path
):
    symbols = [symbol for symbol in synthetic_bars["symbol"].unique() if symbol != "SPY"]
    rows = []
    for index, symbol in enumerate(symbols):
        rows.append(
            {
                "available_at": "2023-03-01",
                "symbol": symbol,
                "gross_profitability": 0.1 + index * 0.02,
                "cash_profitability": 0.08 + index * 0.01,
                "accruals": 0.02 - index * 0.001,
                "leverage": 0.5 - index * 0.03,
                "share_growth": 0.01,
                "revenue_growth": 0.05 + index * 0.02,
                "earnings_growth": 0.04 + index * 0.03,
            }
        )
    path = tmp_path / "features.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    strategy = create_strategy(
        "daily_v4",
        {
            "core_weight": 0.75,
            "active_weight": 0.25,
            "active_name_cap": 0.15,
            "rebalance_every": 5,
            "active_parameters": {
                "formation_lookback": 30,
                "medium_lookback": 20,
                "skip_recent": 5,
                "beta_lookback": 20,
                "volatility_lookback": 10,
                "correlation_lookback": 20,
                "fast_window": 5,
                "slow_window": 15,
                "max_symbols": 2,
                "exit_rank": 4,
                "maximum_annualized_volatility": 10.0,
                "external_features_file": str(path),
                "price_score_weight": 0.5,
                "quality_score_weight": 0.3,
                "earnings_score_weight": 0.2,
                "weighting_method": "benchmark_aware",
            },
        },
    )
    targets = strategy.generate_targets(synthetic_bars)
    weights = targets.pivot(index="timestamp", columns="symbol", values="target_weight")
    assert weights["SPY"].eq(0.75).all()
    assert weights.drop(columns="SPY").sum(axis=1).le(0.250001).all()
    assert weights.drop(columns="SPY").max(axis=1).le(0.150001).all()


def test_daily_v5_reduces_exposure_below_benchmark_trend(synthetic_bars):
    strategy = create_strategy(
        "daily_v5",
        {
            "core_weight": 0.4,
            "active_weight": 0.6,
            "active_name_cap": 0.3,
            "rebalance_every": 5,
            "core_trend_window": 15,
            "core_volatility_window": 5,
            "core_target_volatility": 10.0,
            "bearish_core_multiplier": 0.5,
            "bearish_active_multiplier": 0.5,
            "active_parameters": {
                "formation_lookback": 30,
                "medium_lookback": 20,
                "skip_recent": 5,
                "beta_lookback": 20,
                "volatility_lookback": 10,
                "correlation_lookback": 20,
                "fast_window": 5,
                "slow_window": 15,
                "max_symbols": 2,
                "exit_rank": 4,
                "maximum_annualized_volatility": 10.0,
                "max_pairwise_correlation": 1.0,
            },
        },
    )
    targets = strategy.generate_targets(synthetic_bars)
    weights = targets.pivot(index="timestamp", columns="symbol", values="target_weight")
    spy = synthetic_bars[synthetic_bars["symbol"].eq("SPY")].set_index("timestamp")["close"]
    bearish = spy.lt(spy.rolling(15).mean()).reindex(weights.index).fillna(False)
    assert bearish.any()
    assert weights.loc[bearish, "SPY"].eq(0.2).all()
    assert weights.sum(axis=1).le(1.000001).all()
