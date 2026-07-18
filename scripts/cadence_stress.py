from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path

import pandas as pd

from project_geld.backtest import run_backtest
from project_geld.config import load_config, validate_config
from project_geld.data import AlpacaBarSource, CachedBarSource
from project_geld.strategies.registry import create_strategy


def utc_date(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stress-test a selected strategy across rebalance cadences."
    )
    parser.add_argument("--config", default="config.example.toml")
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default="2026-07-17")
    parser.add_argument("--research-dir", default="artifacts/research")
    parser.add_argument("--strategy", default="momentum")
    parser.add_argument("--cadences", default="5,10,21,42")
    parser.add_argument("--slippage-bps", type=float, default=10.0)
    args = parser.parse_args()

    config = load_config(args.config)
    validate_config(config)
    source = CachedBarSource(
        AlpacaBarSource(config.data.feed, config.data.adjustment),
        config.data.cache_dir,
    )
    bars = source.fetch(
        config.universe.data_symbols, utc_date(args.start), utc_date(args.end)
    )
    research_dir = Path(args.research_dir)
    grid_path = research_dir / f"{args.strategy.replace('_', '-')}-grid.csv"
    best = pd.read_csv(grid_path).iloc[0]
    parameters = json.loads(best["parameters"])
    rows: list[dict] = []
    for cadence in [int(item) for item in args.cadences.split(",")]:
        result = run_backtest(
            bars,
            create_strategy(args.strategy, parameters),
            replace(
                config.backtest,
                rebalance_every=cadence,
                slippage_bps=args.slippage_bps,
            ),
            config.risk,
            config.universe.benchmark,
            config.universe.symbols,
        )
        rows.append(
            {
                "strategy": args.strategy,
                "parameters": json.dumps(parameters, sort_keys=True),
                "rebalance_sessions": cadence,
                "slippage_bps": args.slippage_bps,
                **result.metrics,
            }
        )
    results = pd.DataFrame(rows).sort_values("rebalance_sessions")
    output = research_dir / f"{args.strategy}-cadence-stress.csv"
    results.to_csv(output, index=False)
    print(
        results[
            [
                "rebalance_sessions",
                "cagr",
                "sharpe",
                "max_drawdown",
                "annual_alpha",
                "annual_turnover",
            ]
        ].to_string(index=False)
    )
    print(f"Saved: {output.resolve()}")


if __name__ == "__main__":
    main()
