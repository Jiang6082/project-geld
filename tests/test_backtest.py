from dataclasses import replace

import pandas as pd

from project_geld.backtest import run_backtest
from project_geld.config import BacktestConfig, RiskConfig


class AlwaysLong:
    name = "always_long"
    warmup_bars = 0

    def generate_targets(self, bars):
        targets = bars[["timestamp", "symbol"]].copy()
        targets["target_weight"] = 1.0
        targets["score"] = 1.0
        return targets


class RequiresBenchmarkContext:
    name = "requires_benchmark_context"
    warmup_bars = 0

    def generate_targets(self, bars):
        assert "SPY" in set(bars["symbol"])
        targets = bars[["timestamp", "symbol"]].copy()
        targets["target_weight"] = 0.5
        targets["score"] = 1.0
        return targets


def simple_bars():
    dates = pd.date_range("2024-01-02", periods=5, freq="B", tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": dates,
            "symbol": "SPY",
            "open": [100, 110, 111, 112, 113],
            "high": [101, 112, 113, 114, 115],
            "low": [99, 109, 110, 111, 112],
            "close": [100, 111, 112, 113, 114],
            "volume": 1_000_000,
        }
    )


def test_signal_executes_at_next_bar_open():
    result = run_backtest(
        simple_bars(),
        AlwaysLong(),
        BacktestConfig(initial_cash=10_000, slippage_bps=0, rebalance_every=1),
        RiskConfig(),
    )
    first_trade = result.trades.iloc[0]
    assert first_trade["signal_timestamp"] == pd.Timestamp("2024-01-02", tz="UTC")
    assert first_trade["timestamp"] == pd.Timestamp("2024-01-03", tz="UTC")
    assert first_trade["fill_price"] == 110


def test_slippage_reduces_ending_equity():
    base = BacktestConfig(initial_cash=10_000, slippage_bps=0, rebalance_every=1)
    no_cost = run_backtest(simple_bars(), AlwaysLong(), base, RiskConfig())
    costly = run_backtest(
        simple_bars(), AlwaysLong(), replace(base, slippage_bps=100), RiskConfig()
    )
    assert costly.equity.iloc[-1]["equity"] < no_cost.equity.iloc[-1]["equity"]


def test_position_weight_cap_is_applied():
    result = run_backtest(
        simple_bars(),
        AlwaysLong(),
        BacktestConfig(initial_cash=10_000, slippage_bps=0, rebalance_every=1),
        RiskConfig(max_position_weight=0.25),
    )
    assert result.trades.iloc[0]["notional"] <= 2_500.01


def test_symbol_specific_position_weight_cap_is_applied():
    spy = simple_bars()
    aapl = spy.copy()
    aapl["symbol"] = "AAPL"
    result = run_backtest(
        pd.concat([spy, aapl], ignore_index=True),
        AlwaysLong(),
        BacktestConfig(initial_cash=10_000, slippage_bps=0, rebalance_every=1),
        RiskConfig(
            max_position_weight=0.10,
            symbol_position_weight_limits={"SPY": 0.50},
        ),
    )
    first = result.trades[result.trades["timestamp"].eq(result.trades["timestamp"].min())]
    notionals = first.set_index("symbol")["notional"]
    assert notionals["SPY"] <= 5_000.01
    assert notionals["AAPL"] <= 1_000.01


def test_benchmark_can_be_excluded_from_tradable_universe():
    spy = simple_bars()
    aapl = spy.copy()
    aapl["symbol"] = "AAPL"
    bars = pd.concat([spy, aapl], ignore_index=True)
    result = run_backtest(
        bars,
        AlwaysLong(),
        BacktestConfig(initial_cash=10_000, slippage_bps=0, rebalance_every=1),
        RiskConfig(),
        benchmark="SPY",
        tradable_symbols=["AAPL"],
    )
    assert set(result.trades["symbol"]) == {"AAPL"}


def test_context_symbol_is_visible_to_strategy_but_cannot_be_traded():
    spy = simple_bars()
    aapl = spy.copy()
    aapl["symbol"] = "AAPL"
    result = run_backtest(
        pd.concat([spy, aapl], ignore_index=True),
        RequiresBenchmarkContext(),
        BacktestConfig(initial_cash=10_000, slippage_bps=0, rebalance_every=1),
        RiskConfig(),
        benchmark="SPY",
        tradable_symbols=["AAPL"],
        context_symbols=["SPY"],
    )
    assert set(result.trades["symbol"]) == {"AAPL"}
    assert result.targets.loc[
        result.targets["symbol"].eq("SPY"), "target_weight"
    ].eq(0.0).all()


def test_missing_symbol_is_forced_out_with_conservative_haircut():
    spy = simple_bars()
    disappearing = spy.iloc[:2].copy()
    disappearing["symbol"] = "OLD"
    bars = pd.concat([spy, disappearing], ignore_index=True)
    result = run_backtest(
        bars,
        AlwaysLong(),
        BacktestConfig(
            initial_cash=10_000,
            slippage_bps=0,
            rebalance_every=1,
            missing_price_exit_sessions=2,
            missing_price_haircut_pct=0.25,
        ),
        RiskConfig(max_position_weight=0.5),
        benchmark="SPY",
        tradable_symbols=["OLD"],
    )
    forced = result.trades[
        result.trades["exit_reason"] == "missing_price_forced_exit"
    ]
    assert len(forced) == 1
    assert forced.iloc[0]["fill_price"] == 111 * 0.75
