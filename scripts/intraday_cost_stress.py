from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import pandas as pd

from project_geld.config import load_config, validate_config
from project_geld.data import normalize_bars
from project_geld.intraday import label_native_intraday_bar_ends, run_intraday_backtest
from project_geld.strategies.registry import create_strategy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run an intraday strategy over cached native bars at several costs."
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--bars", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--slippage-bps", default="0,2,4,8")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    validate_config(config)
    strategy = create_strategy(config.strategy.name, config.strategy.parameters)
    native = normalize_bars(pd.read_csv(args.bars))
    bars = label_native_intraday_bar_ends(
        native, config.intraday.bar_minutes, config.backtest.session_timezone
    )
    context = list(dict.fromkeys(strategy.context_symbols))
    tradables = [
        symbol for symbol in config.universe.symbols if symbol not in set(context)
    ]
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    annual_rows: list[dict] = []
    for slippage in [float(value) for value in args.slippage_bps.split(",")]:
        result = run_intraday_backtest(
            bars,
            strategy,
            replace(config.backtest, slippage_bps=slippage),
            config.risk,
            config.universe.benchmark,
            tradables,
            context,
        )
        rows.append({"slippage_bps": slippage, **result.metrics})
        equity = result.equity.copy()
        benchmark_daily = equity.groupby("session_date")["benchmark_return"].apply(
            lambda values: float((1.0 + values).prod() - 1.0)
        )
        daily = equity.groupby("session_date", sort=True).tail(1).copy()
        daily["strategy_return"] = daily["equity"].pct_change(
            fill_method=None
        ).fillna(0.0)
        daily["benchmark_daily_return"] = daily["session_date"].map(benchmark_daily)
        daily["year"] = pd.to_datetime(daily["session_date"]).dt.year
        for year, group in daily.groupby("year", sort=True):
            annual_rows.append(
                {
                    "slippage_bps": slippage,
                    "year": int(year),
                    "strategy_return": float(
                        (1.0 + group["strategy_return"]).prod() - 1.0
                    ),
                    "benchmark_return": float(
                        (1.0 + group["benchmark_daily_return"]).prod() - 1.0
                    ),
                }
            )
        print(f"completed slippage={slippage:g} bps", flush=True)
    stress = pd.DataFrame(rows)
    stress.to_csv(output / "cost-stress.csv", index=False)
    pd.DataFrame(annual_rows).to_csv(output / "annual-results.csv", index=False)
    columns = [
        "slippage_bps",
        "total_return",
        "cagr",
        "sharpe",
        "max_drawdown",
        "annual_turnover",
        "orders",
    ]
    print(stress[columns].to_string(index=False))


if __name__ == "__main__":
    main()
