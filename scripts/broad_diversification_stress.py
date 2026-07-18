from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path

import pandas as pd

from project_geld.backtest import run_backtest
from project_geld.config import load_config
from project_geld.data import normalize_bars
from project_geld.research import period_metrics
from project_geld.strategies.registry import create_strategy


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test Momentum V2 holding counts on the broad universe."
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
    start = pd.to_datetime(selected["timestamp"], utc=True).min()
    end = bars["timestamp"].max()
    rows = []
    for holdings in [int(value) for value in args.holding_counts.split(",")]:
        parameters = {
            **config.strategy.parameters,
            "max_symbols": holdings,
            "exit_rank": max(holdings * 2, 10),
            "sector_map": {},
            "max_per_sector": holdings,
            "membership_periods": membership,
        }
        result = run_backtest(
            bars,
            create_strategy(config.strategy.name, parameters),
            replace(config.backtest, missing_price_haircut_pct=0.0),
            config.risk,
            config.universe.benchmark,
            symbols,
        )
        rows.append(
            {
                "holdings": holdings,
                "exit_rank": parameters["exit_rank"],
                **period_metrics(result, start, end),
            }
        )
        print(f"completed holdings={holdings}", flush=True)
    results = pd.DataFrame(rows)
    results.to_csv(directory / "diversification-stress.csv", index=False)
    print(
        results[
            ["holdings", "cagr", "sharpe", "max_drawdown", "annual_turnover"]
        ].to_string(index=False)
    )


if __name__ == "__main__":
    main()
