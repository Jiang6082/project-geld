from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path

import pandas as pd

from project_geld.backtest import run_backtest
from project_geld.config import load_config
from project_geld.data import normalize_bars
from project_geld.metrics import calculate_metrics
from project_geld.research import MembershipAllocation, StaticAllocation, period_metrics
from project_geld.strategies.registry import create_strategy


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rolling holding-count selection on the broad universe."
    )
    parser.add_argument("--config", default="configs/equity-momentum-v2.toml")
    parser.add_argument("--research-dir", default="artifacts/research-broad")
    parser.add_argument("--holding-counts", default="5,10,20,40")
    args = parser.parse_args()

    config = load_config(args.config)
    directory = Path(args.research_dir)
    bars = normalize_bars(pd.read_csv(directory / "selected-bars.csv.gz"))
    membership = json.loads(
        (directory / "membership-periods.json").read_text(encoding="utf-8")
    )
    symbols = sorted(membership)
    selected = pd.read_csv(directory / "monthly-selected-universe.csv.gz")
    universe_start = pd.to_datetime(selected["timestamp"], utc=True).min()
    end = bars["timestamp"].max()
    backtest = replace(config.backtest, missing_price_haircut_pct=0.0)

    results = {}
    for holdings in [int(value) for value in args.holding_counts.split(",")]:
        parameters = {
            **config.strategy.parameters,
            "max_symbols": holdings,
            "exit_rank": max(holdings * 2, 10),
            "sector_map": {},
            "max_per_sector": holdings,
            "membership_periods": membership,
        }
        results[holdings] = run_backtest(
            bars,
            create_strategy(config.strategy.name, parameters),
            backtest,
            config.risk,
            config.universe.benchmark,
            symbols,
        )
        print(f"candidate complete: holdings={holdings}", flush=True)

    fold_rows = []
    stitched_rows = []
    for test_year in range(2020, end.year + 1):
        train_start = pd.Timestamp(f"{test_year - 3}-01-01", tz="UTC")
        train_end = pd.Timestamp(f"{test_year - 1}-12-31", tz="UTC")
        test_start = pd.Timestamp(f"{test_year}-01-01", tz="UTC")
        test_end = min(pd.Timestamp(f"{test_year}-12-31", tz="UTC"), end)
        choices = []
        for holdings, result in results.items():
            train = period_metrics(result, train_start, train_end)
            objective = (
                train["sharpe"]
                - 0.25 * abs(train["max_drawdown"])
                - 0.01 * train["annual_turnover"]
            )
            choices.append((objective, holdings, train))
        objective, holdings, train = max(choices, key=lambda item: item[0])
        result = results[holdings]
        test = period_metrics(result, test_start, test_end)
        fold_rows.append(
            {
                "test_year": test_year,
                "train_start": train_start,
                "train_end": train_end,
                "selected_holdings": holdings,
                "selection_objective": objective,
                "train_sharpe": train["sharpe"],
                "train_max_drawdown": train["max_drawdown"],
                "test_return": test["total_return"],
                "test_sharpe": test["sharpe"],
                "test_max_drawdown": test["max_drawdown"],
            }
        )
        fold = result.equity[
            result.equity["timestamp"].between(test_start, test_end)
        ][["timestamp", "daily_return", "benchmark_return"]].copy()
        fold["selected_holdings"] = holdings
        stitched_rows.append(fold)
    folds = pd.DataFrame(fold_rows)
    folds.to_csv(directory / "holding-count-walk-forward-folds.csv", index=False)
    stitched = pd.concat(stitched_rows, ignore_index=True).sort_values("timestamp")
    stitched["equity"] = config.backtest.initial_cash * (
        1 + stitched["daily_return"]
    ).cumprod()
    stitched.to_csv(directory / "holding-count-walk-forward-equity.csv", index=False)
    walk_metrics = calculate_metrics(stitched, pd.DataFrame())
    (directory / "holding-count-walk-forward-metrics.json").write_text(
        json.dumps(walk_metrics, indent=2, sort_keys=True), encoding="utf-8"
    )

    equal_weight = run_backtest(
        bars,
        MembershipAllocation(membership, gross_exposure=0.75),
        replace(backtest, rebalance_every=21),
        config.risk,
        config.universe.benchmark,
        symbols,
    )
    spy_bars = bars[bars["symbol"].eq(config.universe.benchmark)]
    benchmark_risk = replace(
        config.risk, max_gross_exposure=1.0, max_position_weight=1.0
    )
    spy_75 = run_backtest(
        spy_bars,
        StaticAllocation(gross_exposure=0.75),
        replace(backtest, rebalance_every=21),
        benchmark_risk,
        config.universe.benchmark,
        [config.universe.benchmark],
    )
    spy_100 = run_backtest(
        spy_bars,
        StaticAllocation(gross_exposure=1.0),
        replace(backtest, rebalance_every=100_000),
        benchmark_risk,
        config.universe.benchmark,
        [config.universe.benchmark],
    )
    walk_start = stitched["timestamp"].min()
    walk_end = stitched["timestamp"].max()
    comparison = pd.DataFrame(
        [
            {"label": "walk_forward_momentum", **walk_metrics},
            {
                "label": "broad_equal_weight_75pct",
                **period_metrics(equal_weight, walk_start, walk_end),
            },
            {
                "label": "SPY_75pct_monthly",
                **period_metrics(spy_75, walk_start, walk_end),
            },
            {
                "label": "SPY_100pct_buy_hold",
                **period_metrics(spy_100, walk_start, walk_end),
            },
        ]
    )
    comparison.to_csv(directory / "holding-count-walk-forward-comparison.csv", index=False)
    print(folds[["test_year", "selected_holdings", "test_return", "test_sharpe"]].to_string(index=False))
    print(comparison[["label", "cagr", "sharpe", "max_drawdown"]].to_string(index=False))


if __name__ == "__main__":
    main()
