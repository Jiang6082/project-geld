from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pandas as pd

from project_geld.backtest import run_backtest
from project_geld.config import load_config
from project_geld.data import normalize_bars
from project_geld.research import period_metrics
from project_geld.strategies.registry import create_strategy


ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "artifacts/research-broad"
OUTPUT = ROOT / "artifacts/daily-v4-readiness"


def main() -> None:
    config = load_config(ROOT / "configs/paper-daily-v4.toml")
    stock_bars = normalize_bars(
        pd.read_csv(RESEARCH / "selected-bars.csv.gz")
    )
    factor_bars = normalize_bars(
        pd.read_csv(RESEARCH / "v4-factor-bars.csv.gz")
    )
    bars = normalize_bars(
        pd.concat(
            [
                stock_bars[
                    ~stock_bars["symbol"].isin({"SPY", "IWM", "IWD"})
                ],
                factor_bars,
            ],
            ignore_index=True,
        )
    )
    membership = json.loads(
        (RESEARCH / "membership-periods.json").read_text(encoding="utf-8")
    )
    parameters = {
        **config.strategy.parameters,
        "active_parameters": {
            **config.strategy.parameters["active_parameters"],
            "membership_periods": membership,
        },
    }
    strategy = create_strategy(config.strategy.name, parameters)
    tradables = [*sorted(membership), config.universe.benchmark]
    rows: list[dict] = []
    for slippage_bps in [8.0, 10.0, 16.0, 24.0]:
        result = run_backtest(
            bars,
            strategy,
            replace(config.backtest, slippage_bps=slippage_bps),
            config.risk,
            config.universe.benchmark,
            tradables,
            context_symbols=strategy.context_symbols,
        )
        for period, start, end in [
            ("training", "2017-01-01", "2019-12-31"),
            ("diagnostic", "2020-01-01", str(bars["timestamp"].max().date())),
        ]:
            metrics = period_metrics(result, start, end)
            rows.append(
                {
                    "slippage_bps": slippage_bps,
                    "period": period,
                    **metrics,
                }
            )
        print(
            f"completed Daily V4 at {slippage_bps:g} bps",
            flush=True,
        )

    OUTPUT.mkdir(parents=True, exist_ok=True)
    results = pd.DataFrame(rows)
    results.to_csv(OUTPUT / "cost-results.csv", index=False)
    print(
        results[
            [
                "slippage_bps",
                "period",
                "cagr",
                "sharpe",
                "max_drawdown",
                "annual_alpha",
                "annual_turnover",
            ]
        ].to_string(index=False)
    )


if __name__ == "__main__":
    main()
