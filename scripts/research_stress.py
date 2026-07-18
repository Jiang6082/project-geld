from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
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
        description="Stress-test the best saved grid candidate at multiple slippage levels."
    )
    parser.add_argument("--config", default="config.example.toml")
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default="2026-07-17")
    parser.add_argument("--research-dir", default="artifacts/research")
    parser.add_argument("--slippage-bps", default="5,10,20")
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
    slippage_levels = [float(item) for item in args.slippage_bps.split(",")]
    summary: list[dict] = []

    for grid_path in sorted(research_dir.glob("*-grid.csv")):
        grid = pd.read_csv(grid_path)
        best = grid.iloc[0]
        strategy_name = str(best["strategy"])
        parameters = json.loads(best["parameters"])
        strategy = create_strategy(strategy_name, parameters)
        for slippage_bps in slippage_levels:
            backtest_config = replace(
                config.backtest, slippage_bps=slippage_bps
            )
            result = run_backtest(
                bars,
                strategy,
                backtest_config,
                config.risk,
                config.universe.benchmark,
                config.universe.symbols,
            )
            summary.append(
                {
                    "strategy": strategy_name,
                    "parameters": json.dumps(parameters, sort_keys=True),
                    "slippage_bps": slippage_bps,
                    **result.metrics,
                }
            )
            if slippage_bps == slippage_levels[0]:
                save_result(result, research_dir / "selected" / strategy_name)

    summary_frame = pd.DataFrame(summary).sort_values(
        ["slippage_bps", "sharpe"], ascending=[True, False]
    )
    output = research_dir / "cost-stress.csv"
    summary_frame.to_csv(output, index=False)
    print(
        summary_frame[
            [
                "strategy",
                "slippage_bps",
                "cagr",
                "sharpe",
                "max_drawdown",
                "excess_return",
                "annual_turnover",
            ]
        ].to_string(index=False)
    )
    print(f"Saved: {output.resolve()}")


if __name__ == "__main__":
    main()
