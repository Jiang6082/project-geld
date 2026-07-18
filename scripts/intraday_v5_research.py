from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import pandas as pd

from project_geld.config import load_config, validate_config
from project_geld.data import normalize_bars
from project_geld.intraday import label_native_intraday_bar_ends, run_intraday_backtest
from project_geld.strategies.intra_v5 import IntraV5


VARIANTS = {
    "baseline": {},
    "above_vwap": {"require_above_vwap_after_recovery": True},
    "one_percent": {"min_relative_dislocation": 0.01},
    "one_percent_above_vwap": {
        "min_relative_dislocation": 0.01,
        "require_above_vwap_after_recovery": True,
    },
    "two_bar_confirmation": {"confirmation_bars": 2},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Small, predeclared Intra V5 robustness grid.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--bars", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--slippage-bps", default="0,8")
    parser.add_argument(
        "--variants",
        default=",".join(VARIANTS),
        help="Comma-separated subset of the predeclared variant names.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    validate_config(config)
    native = normalize_bars(pd.read_csv(args.bars))
    bars = label_native_intraday_bar_ends(
        native, config.intraday.bar_minutes, config.backtest.session_timezone
    )
    context = [config.universe.benchmark]
    tradables = [symbol for symbol in config.universe.symbols if symbol not in context]
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    base_parameters = dict(config.strategy.parameters)
    requested = [value.strip() for value in args.variants.split(",")]
    unknown = set(requested) - set(VARIANTS)
    if unknown:
        raise ValueError(f"Unknown variants: {', '.join(sorted(unknown))}")
    for label in requested:
        changes = VARIANTS[label]
        strategy = IntraV5(**{**base_parameters, **changes})
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
            rows.append(
                {
                    "variant": label,
                    "slippage_bps": slippage,
                    **changes,
                    **result.metrics,
                }
            )
            print(f"completed {label} at {slippage:g} bps", flush=True)
    results = pd.DataFrame(rows)
    results.to_csv(output / "variant-results.csv", index=False)
    columns = [
        "variant",
        "slippage_bps",
        "total_return",
        "cagr",
        "sharpe",
        "max_drawdown",
        "annual_turnover",
        "orders",
    ]
    print(results[columns].to_string(index=False))


if __name__ == "__main__":
    main()
