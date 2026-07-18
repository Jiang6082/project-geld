from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path

import pandas as pd

from project_geld.backtest import run_backtest
from project_geld.config import load_config
from project_geld.data import normalize_bars
from project_geld.research import MembershipAllocation, period_metrics
from project_geld.strategies.registry import create_strategy


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stress missing-price exit assumptions for the broad universe."
    )
    parser.add_argument("--config", default="configs/equity-momentum-v2.toml")
    parser.add_argument("--research-dir", default="artifacts/research-broad")
    parser.add_argument("--haircuts", default="0,0.25")
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
    parameters = {
        **config.strategy.parameters,
        "sector_map": {},
        "max_per_sector": int(config.strategy.parameters.get("max_symbols", 5)),
        "membership_periods": membership,
    }
    rows = []
    for haircut in [float(value) for value in args.haircuts.split(",")]:
        backtest = replace(config.backtest, missing_price_haircut_pct=haircut)
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
        for label, result in [
            ("broad_momentum_v2", momentum),
            ("broad_equal_weight_75pct", equal_weight),
        ]:
            rows.append(
                {
                    "missing_price_haircut_pct": haircut,
                    "label": label,
                    **period_metrics(result, start, end),
                    "forced_exits": int(
                        result.trades["exit_reason"]
                        .eq("missing_price_forced_exit")
                        .sum()
                    ),
                }
            )
        print(f"completed haircut={haircut:.0%}", flush=True)
    results = pd.DataFrame(rows)
    results.to_csv(directory / "missing-price-exit-stress.csv", index=False)
    print(
        results[
            [
                "missing_price_haircut_pct",
                "label",
                "cagr",
                "sharpe",
                "max_drawdown",
                "forced_exits",
            ]
        ].to_string(index=False)
    )


if __name__ == "__main__":
    main()
