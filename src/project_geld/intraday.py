from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pandas as pd

from project_geld.backtest import run_backtest
from project_geld.config import BacktestConfig, RiskConfig
from project_geld.data import BAR_COLUMNS, normalize_bars
from project_geld.metrics import calculate_metrics
from project_geld.models import BacktestResult
from project_geld.strategies.base import Strategy


def resample_intraday_bars(
    bars: pd.DataFrame,
    minutes: int = 15,
    timezone: str = "America/New_York",
    regular_hours_only: bool = True,
    drop_incomplete: bool = True,
) -> pd.DataFrame:
    """Aggregate one-minute bars and label each bar with its ending timestamp."""
    if minutes not in {1, 5, 10, 15, 30, 60}:
        raise ValueError("minutes must be one of 1, 5, 10, 15, 30, 60.")
    frame = normalize_bars(bars)
    if frame.empty:
        return frame
    local = frame["timestamp"].dt.tz_convert(timezone)
    minute_of_day = local.dt.hour * 60 + local.dt.minute
    if regular_hours_only:
        frame = frame[minute_of_day.between(570, 959)].copy()
        local = frame["timestamp"].dt.tz_convert(timezone)
        minute_of_day = local.dt.hour * 60 + local.dt.minute
    frame["session_date"] = local.dt.date
    offset = minute_of_day - 570
    frame["bucket"] = ((offset // minutes) + 1) * minutes
    session_start = local.dt.normalize() + pd.offsets.Minute(570)
    frame["bar_end"] = session_start + pd.to_timedelta(frame["bucket"], unit="m")

    if drop_incomplete:
        latest_local = local.max()
        latest_start = latest_local.normalize() + pd.offsets.Minute(570)
        completed_offset = max(int((latest_local - latest_start).total_seconds() // 60), 0)
        completed_boundary = latest_start + pd.Timedelta(
            (completed_offset // minutes) * minutes, unit="m"
        )
        frame = frame[frame["bar_end"].le(completed_boundary)]

    if frame.empty:
        return pd.DataFrame(columns=BAR_COLUMNS)
    grouped = frame.groupby(["symbol", "session_date", "bar_end"], sort=True)
    result = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).reset_index()
    result["timestamp"] = result["bar_end"].dt.tz_convert("UTC")
    return normalize_bars(result[BAR_COLUMNS])


def run_intraday_backtest(
    bars: pd.DataFrame,
    strategy: Strategy,
    config: BacktestConfig,
    risk: RiskConfig,
    benchmark: str = "SPY",
    tradable_symbols: list[str] | None = None,
    context_symbols: list[str] | None = None,
) -> BacktestResult:
    intraday_config = replace(
        config, rebalance_every=1, force_flat_at_session_end=True
    )
    raw = run_backtest(
        bars,
        strategy,
        intraday_config,
        risk,
        benchmark,
        tradable_symbols,
        context_symbols,
    )
    equity = raw.equity.copy()
    equity["session_date"] = (
        pd.to_datetime(equity["timestamp"], utc=True)
        .dt.tz_convert(config.session_timezone)
        .dt.date
    )
    daily = equity.groupby("session_date", sort=True).tail(1).copy()
    benchmark_daily = equity.groupby("session_date")["benchmark_return"].apply(
        lambda values: float(np.prod(1.0 + values) - 1.0)
    )
    daily["benchmark_return"] = daily["session_date"].map(benchmark_daily)
    daily["daily_return"] = daily["equity"].pct_change(fill_method=None).fillna(0.0)
    metrics = calculate_metrics(daily, raw.trades)
    return BacktestResult(
        equity=equity,
        trades=raw.trades,
        targets=raw.targets,
        metrics=metrics,
    )


def intraday_cycle_due(state_file: Path, latest_bar: pd.Timestamp) -> bool:
    if not state_file.exists():
        return True
    state = json.loads(state_file.read_text(encoding="utf-8"))
    return pd.Timestamp(state["latest_bar"]) < pd.Timestamp(latest_bar)


def mark_intraday_cycle(state_file: Path, latest_bar: pd.Timestamp) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps({"latest_bar": pd.Timestamp(latest_bar).isoformat()}, indent=2),
        encoding="utf-8",
    )
