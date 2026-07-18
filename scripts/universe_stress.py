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
        description="Evaluate selected candidates across reduced universes."
    )
    parser.add_argument("--config", default="config.example.toml")
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default="2026-07-17")
    parser.add_argument("--research-dir", default="artifacts/research")
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
    all_symbols = sorted(bars["symbol"].unique())
    universes = {
        "full": all_symbols,
        "exclude_nvda": [symbol for symbol in all_symbols if symbol != "NVDA"],
        "exclude_nvda_meta": [
            symbol for symbol in all_symbols if symbol not in {"NVDA", "META"}
        ],
        "etfs_only": ["SPY", "QQQ", "IWM"],
    }
    research_dir = Path(args.research_dir)
    rows: list[dict] = []
    for grid_path in sorted(research_dir.glob("*-grid.csv")):
        best = pd.read_csv(grid_path).iloc[0]
        strategy_name = str(best["strategy"])
        parameters = json.loads(best["parameters"])
        for universe_name, symbols in universes.items():
            subset = bars[bars["symbol"].isin(symbols)].copy()
            result = run_backtest(
                subset,
                create_strategy(strategy_name, parameters),
                replace(config.backtest, slippage_bps=args.slippage_bps),
                config.risk,
                config.universe.benchmark,
                symbols,
            )
            rows.append(
                {
                    "strategy": strategy_name,
                    "universe": universe_name,
                    "symbols": ",".join(symbols),
                    "slippage_bps": args.slippage_bps,
                    **result.metrics,
                }
            )
    results = pd.DataFrame(rows).sort_values(["strategy", "universe"])
    output = research_dir / "universe-stress.csv"
    results.to_csv(output, index=False)
    print(
        results[
            [
                "strategy",
                "universe",
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
