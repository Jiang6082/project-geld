"""Metric definitions: Sortino, opening-row returns, and holdout turnover."""

import math

import numpy as np
import pandas as pd
import pytest

from project_geld.candidates.promotion import revalidate_candidate
from project_geld.config import BacktestConfig, RiskConfig
from project_geld.intraday import run_intraday_backtest
from project_geld.metrics import TRADING_DAYS, calculate_metrics
from project_geld.strategies.base import TARGET_COLUMNS


def _curve(returns, start="2024-01-01"):
    equity = 100_000 * np.cumprod(np.r_[1.0, 1.0 + np.asarray(returns)])
    timestamps = pd.date_range(start, periods=len(equity), freq="B", tz="UTC")
    return pd.DataFrame({"timestamp": timestamps, "equity": equity, "benchmark_return": 0.0})


NO_TRADES = pd.DataFrame(columns=["notional", "fees"])


def test_sortino_uses_downside_deviation():
    returns = np.array([0.02, -0.01, 0.015, -0.03, 0.01, 0.005, -0.002])
    metrics = calculate_metrics(_curve(returns), NO_TRADES)
    downside = math.sqrt(np.mean(np.minimum(returns, 0.0) ** 2))
    assert metrics["sortino"] == pytest.approx(returns.mean() / downside * math.sqrt(TRADING_DAYS))


def test_opening_row_is_not_counted_as_a_zero_return():
    returns = np.array([0.01, 0.01, 0.01, -0.005])
    metrics = calculate_metrics(_curve(returns), NO_TRADES)
    expected = returns.mean() / returns.std(ddof=0) * math.sqrt(TRADING_DAYS)
    assert metrics["sharpe"] == pytest.approx(expected)
    assert metrics["annual_volatility"] == pytest.approx(returns.std(ddof=0) * math.sqrt(TRADING_DAYS))


def test_single_valuation_still_produces_finite_metrics():
    metrics = calculate_metrics(_curve([]), NO_TRADES)
    assert metrics["total_return"] == 0.0
    assert metrics["sharpe"] == 0.0


class _AlwaysLong:
    name = "always_long"
    warmup_bars = 0

    def __init__(self, symbol="AAA", weight=1.0):
        self.symbol = symbol
        self.weight = weight

    def generate_targets(self, bars):
        timestamps = sorted(bars["timestamp"].unique())
        return pd.DataFrame(
            [{"timestamp": ts, "symbol": self.symbol, "target_weight": self.weight, "score": 1.0}
             for ts in timestamps],
            columns=TARGET_COLUMNS,
        )


def _intraday_bars():
    rows = []
    for day, drift in [("2026-03-02", 0.01), ("2026-03-03", -0.004)]:
        start = pd.Timestamp(f"{day} 09:45", tz="America/New_York")
        price = 100.0
        for step in range(4):
            stamp = (start + pd.Timedelta(minutes=15 * step)).tz_convert("UTC")
            new_price = price * (1 + drift)
            for symbol in ["AAA", "SPY"]:
                rows.append({"timestamp": stamp, "symbol": symbol, "open": price, "high": max(price, new_price),
                             "low": min(price, new_price), "close": new_price, "volume": 1_000})
            price = new_price
    return pd.DataFrame(rows)


def test_intraday_first_session_return_is_measured():
    bars = _intraday_bars()
    config = BacktestConfig(initial_cash=100_000, slippage_bps=0.0, commission_per_share=0.0)
    risk = RiskConfig(max_position_weight=1.0, max_gross_exposure=1.0)
    result = run_intraday_backtest(bars, _AlwaysLong(), config, risk)
    session_close = result.equity.groupby(
        result.equity["timestamp"].dt.tz_convert("America/New_York").dt.date
    )["equity"].last()
    first_day = session_close.iloc[0] / config.initial_cash - 1
    assert first_day > 0  # the long position gained on the rising first session
    assert result.metrics["total_return"] == pytest.approx(session_close.iloc[-1] / config.initial_cash - 1)


class _WarmupThenTrade:
    """Holds nothing for a long warmup, then flips every bar."""

    name = "warmup_then_trade"
    warmup_bars = 0

    def generate_targets(self, bars):
        timestamps = sorted(bars["timestamp"].unique())
        cutoff = int(len(timestamps) * 0.8)
        rows = []
        for index, ts in enumerate(timestamps):
            weight = 0.0 if index < cutoff else (1.0 if index % 2 else 0.0)
            rows.append({"timestamp": ts, "symbol": "AAA", "target_weight": weight, "score": 1.0})
        return pd.DataFrame(rows, columns=TARGET_COLUMNS)


def test_capacity_gate_judges_holdout_turnover_not_warmup_diluted_turnover():
    timestamps = pd.date_range("2020-01-01", periods=400, freq="B", tz="UTC")
    rng = np.random.default_rng(0)
    rows = []
    for symbol in ["AAA", "SPY"]:
        close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.01, len(timestamps)))
        for ts, price in zip(timestamps, close):
            rows.append({"timestamp": ts, "symbol": symbol, "open": price, "high": price,
                         "low": price, "close": price, "volume": 1_000_000})
    bars = pd.DataFrame(rows)
    config = BacktestConfig(initial_cash=100_000, slippage_bps=1.0, rebalance_every=1)
    risk = RiskConfig(max_position_weight=1.0, max_gross_exposure=1.0)
    verdict = revalidate_candidate(_WarmupThenTrade(), bars, backtest=config, risk=risk,
                                   tradable_symbols=["AAA"], context_symbols=["SPY"])
    gate = next(g for g in verdict["gates"] if g["name"] == "turnover_capacity")
    assert verdict["annual_turnover"] > 2 * verdict["full_period_annual_turnover"]
    assert gate["value"] == pytest.approx(round(verdict["annual_turnover"], 3))
    assert not gate["passed"]


def test_single_bar_first_session_is_not_duplicated():
    rows = []
    sessions = [("2026-03-02", 1, 0.0), ("2026-03-03", 4, 0.01), ("2026-03-04", 4, -0.006)]
    price = 100.0
    for day, bars_in_session, drift in sessions:
        start = pd.Timestamp(f"{day} 09:45", tz="America/New_York")
        for step in range(bars_in_session):
            stamp = (start + pd.Timedelta(minutes=15 * step)).tz_convert("UTC")
            new_price = price * (1 + drift)
            for symbol in ["AAA", "SPY"]:
                rows.append({"timestamp": stamp, "symbol": symbol, "open": price, "high": max(price, new_price),
                             "low": min(price, new_price), "close": new_price, "volume": 1_000})
            price = new_price
    config = BacktestConfig(initial_cash=100_000, slippage_bps=0.0, commission_per_share=0.0)
    risk = RiskConfig(max_position_weight=1.0, max_gross_exposure=1.0)
    result = run_intraday_backtest(pd.DataFrame(rows), _AlwaysLong(), config, risk)
    closes = result.equity.groupby(
        result.equity["timestamp"].dt.tz_convert("America/New_York").dt.date
    )["equity"].last().to_numpy()
    # The one-bar first session ends where it started, so the opening valuation
    # and that session's close are the same row: two real daily returns, no
    # duplicated 0% return.
    returns = closes[1:] / closes[:-1] - 1
    assert result.metrics["annual_volatility"] == pytest.approx(
        returns.std(ddof=0) * math.sqrt(TRADING_DAYS)
    )
