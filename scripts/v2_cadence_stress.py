from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from project_geld.backtest import run_backtest, save_result
from project_geld.config import load_config, validate_config
from project_geld.data import AlpacaBarSource, CachedBarSource
from project_geld.strategies.registry import create_strategy


def utc_date(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest Momentum V2 at matched signal/trading cadences."
    )
    parser.add_argument("--config", default="configs/equity-momentum-v2.toml")
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default="2026-07-17")
    parser.add_argument("--cadences", default="5,10,21")
    parser.add_argument("--output", default="artifacts/research-v2")
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
    output = Path(args.output)
    rows: list[dict] = []
    for cadence in [int(item) for item in args.cadences.split(",")]:
        parameters = {**config.strategy.parameters, "rebalance_every": cadence}
        strategy = create_strategy(config.strategy.name, parameters)
        result = run_backtest(
            bars,
            strategy,
            replace(config.backtest, rebalance_every=cadence),
            config.risk,
            config.universe.benchmark,
            config.universe.symbols,
        )
        save_result(result, output / f"cadence-{cadence}")
        rows.append(
            {
                "rebalance_sessions": cadence,
                **result.metrics,
            }
        )
    results = pd.DataFrame(rows).sort_values("rebalance_sessions")
    results.to_csv(output / "cadence-summary.csv", index=False)
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
