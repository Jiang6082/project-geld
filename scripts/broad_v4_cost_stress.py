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


def main() -> None:
    directory = Path("artifacts/research-broad")
    output = directory / "momentum-v4"
    config = load_config("configs/equity-momentum-v2.toml")
    stocks = normalize_bars(pd.read_csv(directory / "selected-bars.csv.gz"))
    factors = normalize_bars(pd.read_csv(directory / "v4-factor-bars.csv.gz"))
    bars = normalize_bars(
        pd.concat(
            [stocks[~stocks["symbol"].isin({"SPY", "IWM", "IWD"})], factors],
            ignore_index=True,
        )
    )
    membership = json.loads(
        (directory / "membership-periods.json").read_text(encoding="utf-8")
    )
    strategy = create_strategy(
        "momentum_v4",
        {
            "core_symbol": "SPY",
            "core_weight": 0.75,
            "active_weight": 0.25,
            "active_name_cap": 0.02,
            "no_trade_band": 0.0025,
            "rebalance_every": 21,
            "active_parameters": {
                "membership_periods": membership,
                "max_symbols": 40,
                "exit_rank": 80,
                "max_pairwise_correlation": 0.85,
                "maximum_annualized_volatility": 0.60,
            },
        },
    )
    risk = replace(
        config.risk, max_gross_exposure=1.0, max_position_weight=0.75
    )
    start = pd.Timestamp("2020-01-01", tz="UTC")
    end = bars["timestamp"].max()
    rows = []
    for slippage in [0.0, 10.0, 25.0, 50.0]:
        if slippage == 10.0:
            existing = pd.read_csv(output / "fixed-validation-comparison.csv")
            row = existing[existing["variant"].eq("core75_active25")].iloc[0]
            metrics = {
                key: float(row[key])
                for key in [
                    "total_return",
                    "cagr",
                    "annual_volatility",
                    "sharpe",
                    "max_drawdown",
                    "annual_turnover",
                ]
            }
        else:
            result = run_backtest(
                bars,
                strategy,
                replace(
                    config.backtest,
                    rebalance_every=21,
                    missing_price_haircut_pct=0.0,
                    slippage_bps=slippage,
                ),
                risk,
                "SPY",
                [*sorted(membership), "SPY"],
                context_symbols=strategy.context_symbols,
            )
            metrics = period_metrics(result, start, end)
        rows.append({"slippage_bps": slippage, **metrics})
        print(f"completed slippage={slippage:g} bps", flush=True)
    stress = pd.DataFrame(rows)
    stress.to_csv(output / "cost-stress.csv", index=False)
    print(
        stress[
            ["slippage_bps", "cagr", "sharpe", "max_drawdown", "annual_turnover"]
        ].to_string(index=False)
    )


if __name__ == "__main__":
    main()
