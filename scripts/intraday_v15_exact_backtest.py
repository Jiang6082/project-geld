from __future__ import annotations

import argparse
import json
from pathlib import Path
import runpy

import pandas as pd
import numpy as np

from project_geld.config import load_config
from project_geld.intraday import run_intraday_backtest
from project_geld.strategies.registry import create_strategy


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/research-intra-v15-improvement/exact-iex"
DEFAULT_START = "2020-07-27"
DEFAULT_END = "2026-07-17 20:00:00"


def summarize(returns: pd.Series) -> dict[str, float]:
    returns = returns.fillna(0.0).astype(float)
    equity = (1.0 + returns).cumprod()
    deviation = float(returns.std(ddof=0))
    drawdown = equity.div(equity.cummax()).sub(1.0)
    return {
        "return": float(equity.iloc[-1] - 1.0) if len(equity) else 0.0,
        "sharpe": (
            float(returns.mean() / deviation * np.sqrt(252.0))
            if deviation > 0
            else 0.0
        ),
        "max_drawdown": float(drawdown.min()) if len(drawdown) else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end, tz="UTC")
    helpers = runpy.run_path(str(ROOT / "scripts/intraday_v13_research.py"))
    bars, membership, _ = helpers["load_research_inputs"]("iex")
    bars = bars[bars["timestamp"].between(start, end, inclusive="both")].copy()
    config = load_config(ROOT / "configs/paper-intra-v15.toml")
    parameters = {
        **config.strategy.parameters,
        "membership_periods": membership,
    }
    strategy = create_strategy(config.strategy.name, parameters)
    result = run_intraday_backtest(
        bars,
        strategy,
        config.backtest,
        config.risk,
        config.universe.benchmark,
        sorted(set(membership) | {strategy.core_symbol}),
        strategy.context_symbols,
    )
    metrics = dict(result.metrics)
    daily = result.equity.groupby("session_date", sort=True).tail(1).copy()
    daily["return"] = daily["equity"].pct_change(fill_method=None).fillna(0.0)
    daily["session"] = pd.to_datetime(daily["session_date"])
    for label, start, end in [
        ("train", "2020-07-27", "2022-12-31"),
        ("validation", "2023-01-01", "2024-12-31"),
        ("test", "2025-01-01", "2026-07-17"),
    ]:
        mask = daily["session"].between(start, end, inclusive="both")
        for key, value in summarize(daily.loc[mask, "return"]).items():
            metrics[f"{label}_{key}"] = value
    trades = result.trades.copy()
    trades["session"] = (
        pd.to_datetime(trades["timestamp"], utc=True)
        .dt.tz_convert(config.backtest.session_timezone)
        .dt.date
    )
    metrics["active_session_rate"] = float(
        trades["session"].nunique() / len(daily) if len(daily) else 0.0
    )
    args.output.mkdir(parents=True, exist_ok=True)
    result.equity.to_csv(args.output / "equity.csv", index=False)
    result.trades.to_csv(args.output / "trades.csv", index=False)
    result.targets.to_csv(args.output / "targets.csv", index=False)
    (args.output / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(metrics, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
