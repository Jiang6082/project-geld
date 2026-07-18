from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path

import pandas as pd

from project_geld.backtest import run_backtest
from project_geld.config import load_config
from project_geld.data import normalize_bars
from project_geld.research import MembershipAllocation, StaticAllocation, period_metrics
from project_geld.strategies.registry import create_strategy


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Locked holding-count validation on the broad universe."
    )
    parser.add_argument("--config", default="configs/equity-momentum-v2.toml")
    parser.add_argument("--research-dir", default="artifacts/research-broad")
    parser.add_argument("--holdings", type=int, default=40)
    parser.add_argument("--validation-start", default="2020-01-01")
    args = parser.parse_args()

    config = load_config(args.config)
    directory = Path(args.research_dir)
    bars = normalize_bars(pd.read_csv(directory / "selected-bars.csv.gz"))
    membership = json.loads(
        (directory / "membership-periods.json").read_text(encoding="utf-8")
    )
    symbols = sorted(membership)
    backtest = replace(config.backtest, missing_price_haircut_pct=0.0)
    parameters = {
        **config.strategy.parameters,
        "max_symbols": args.holdings,
        "exit_rank": max(args.holdings * 2, 10),
        "sector_map": {},
        "max_per_sector": args.holdings,
        "membership_periods": membership,
    }
    momentum = run_backtest(
        bars,
        create_strategy(config.strategy.name, parameters),
        backtest,
        config.risk,
        config.universe.benchmark,
        symbols,
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
    spy = run_backtest(
        spy_bars,
        StaticAllocation(gross_exposure=1.0),
        replace(backtest, rebalance_every=100_000),
        benchmark_risk,
        config.universe.benchmark,
        [config.universe.benchmark],
    )
    start = pd.Timestamp(args.validation_start, tz="UTC")
    end = bars["timestamp"].max()
    comparison = pd.DataFrame(
        [
            {
                "label": f"broad_momentum_fixed_{args.holdings}",
                **period_metrics(momentum, start, end),
            },
            {
                "label": "broad_equal_weight_75pct",
                **period_metrics(equal_weight, start, end),
            },
            {
                "label": "SPY_100pct_buy_hold",
                **period_metrics(spy, start, end),
            },
        ]
    )
    comparison.to_csv(directory / "fixed-holdings-validation.csv", index=False)
    print(comparison[["label", "cagr", "sharpe", "max_drawdown"]].to_string(index=False))


if __name__ == "__main__":
    main()
