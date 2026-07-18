from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import timedelta
import gc
import json
from pathlib import Path

import pandas as pd

from project_geld.backtest import run_backtest
from project_geld.config import load_config
from project_geld.data import AlpacaBarSource, normalize_bars
from project_geld.research import StaticAllocation, period_metrics
from project_geld.strategies.registry import create_strategy


VARIANTS: dict[str, dict] = {
    "core75_active25": {
        "core_weight": 0.75,
        "active_weight": 0.25,
        "residual_factor_symbols": [],
    },
    "core60_active40": {
        "core_weight": 0.60,
        "active_weight": 0.40,
        "residual_factor_symbols": [],
    },
    "core60_multifactor40": {
        "core_weight": 0.60,
        "active_weight": 0.40,
        "residual_factor_symbols": ["IWM", "IWD"],
    },
}


def selection_score(metrics: dict[str, float]) -> float:
    return (
        metrics["sharpe"]
        + metrics["cagr"]
        + 0.50 * metrics["max_drawdown"]
        - 0.01 * metrics["annual_turnover"]
    )


def load_factor_bars(
    directory: Path, start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    path = directory / "v4-factor-bars.csv.gz"
    if path.exists():
        return normalize_bars(pd.read_csv(path))
    factors = AlpacaBarSource(feed="sip", adjustment="all").fetch(
        ["SPY", "IWM", "IWD"],
        start.to_pydatetime(),
        (end + timedelta(days=1)).to_pydatetime(),
    )
    if set(factors["symbol"]) != {"SPY", "IWM", "IWD"}:
        missing = {"SPY", "IWM", "IWD"} - set(factors["symbol"])
        raise RuntimeError("Missing V4 factor bars for: " + ", ".join(sorted(missing)))
    factors.to_csv(path, index=False, compression="gzip")
    return factors


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Core-plus-alpha Momentum V4 broad-universe diagnostics."
    )
    parser.add_argument("--config", default="configs/equity-momentum-v2.toml")
    parser.add_argument("--research-dir", default="artifacts/research-broad")
    parser.add_argument("--train-start", default="2017-01-01")
    parser.add_argument("--train-end", default="2019-12-31")
    parser.add_argument("--validation-start", default="2020-01-01")
    args = parser.parse_args()

    config = load_config(args.config)
    directory = Path(args.research_dir)
    output = directory / "momentum-v4"
    output.mkdir(parents=True, exist_ok=True)
    stock_bars = normalize_bars(pd.read_csv(directory / "selected-bars.csv.gz"))
    factor_bars = load_factor_bars(
        directory, stock_bars["timestamp"].min(), stock_bars["timestamp"].max()
    )
    bars = normalize_bars(
        pd.concat(
            [
                stock_bars[~stock_bars["symbol"].isin({"SPY", "IWM", "IWD"})],
                factor_bars,
            ],
            ignore_index=True,
        )
    )
    membership = json.loads(
        (directory / "membership-periods.json").read_text(encoding="utf-8")
    )
    stocks = sorted(membership)
    benchmark = "SPY"
    tradables = [*stocks, benchmark]
    train_start = pd.Timestamp(args.train_start, tz="UTC")
    train_end = pd.Timestamp(args.train_end, tz="UTC")
    validation_start = pd.Timestamp(args.validation_start, tz="UTC")
    validation_end = bars["timestamp"].max()
    backtest = replace(
        config.backtest,
        rebalance_every=21,
        missing_price_haircut_pct=0.0,
    )
    risk = replace(
        config.risk,
        max_gross_exposure=1.0,
        max_position_weight=0.75,
    )
    active_base = {
        "membership_periods": membership,
        "max_symbols": 40,
        "exit_rank": 80,
        "max_pairwise_correlation": 0.85,
        "maximum_annualized_volatility": 0.60,
    }

    rows: list[dict] = []
    for label, variant in VARIANTS.items():
        parameters = {
            "core_symbol": benchmark,
            "core_weight": variant["core_weight"],
            "active_weight": variant["active_weight"],
            "active_name_cap": 0.02,
            "no_trade_band": 0.0025,
            "rebalance_every": 21,
            "active_parameters": {
                **active_base,
                "residual_factor_symbols": variant["residual_factor_symbols"],
            },
        }
        strategy = create_strategy("momentum_v4", parameters)
        result = run_backtest(
            bars,
            strategy,
            backtest,
            risk,
            benchmark,
            tradables,
            context_symbols=strategy.context_symbols,
        )
        variant_directory = output / label
        variant_directory.mkdir(parents=True, exist_ok=True)
        result.equity.to_csv(variant_directory / "equity.csv.gz", index=False)
        result.trades.to_csv(variant_directory / "trades.csv.gz", index=False)
        for period, start, end in [
            ("training", train_start, train_end),
            ("fixed_validation", validation_start, validation_end),
        ]:
            metrics = period_metrics(result, start, end)
            window = result.equity[
                result.equity["timestamp"].between(start, end, inclusive="both")
            ]
            rows.append(
                {
                    "variant": label,
                    "period": period,
                    **metrics,
                    "average_gross_exposure": float(window["gross_exposure"].mean()),
                }
            )
        print(f"completed {label}", flush=True)
        del result
        gc.collect()

    metrics = pd.DataFrame(rows)
    training = metrics[metrics["period"].eq("training")].copy()
    training["selection_score"] = training.apply(
        lambda row: selection_score(row.to_dict()), axis=1
    )
    selected = str(training.sort_values("selection_score", ascending=False).iloc[0]["variant"])
    metrics["selected_on_training"] = metrics["variant"].eq(selected)
    metrics.to_csv(output / "variant-metrics.csv", index=False)
    training.sort_values("selection_score", ascending=False).to_csv(
        output / "training-selection.csv", index=False
    )

    spy = run_backtest(
        bars[bars["symbol"].eq(benchmark)],
        StaticAllocation(gross_exposure=1.0),
        replace(backtest, rebalance_every=100_000),
        replace(risk, max_position_weight=1.0),
        benchmark,
        [benchmark],
    )
    comparison = metrics[metrics["period"].eq("fixed_validation")].copy()
    comparison["label"] = "momentum_v4_" + comparison["variant"]
    spy_row = {
        "label": "SPY_100pct_buy_hold",
        "variant": "benchmark",
        "period": "fixed_validation",
        **period_metrics(spy, validation_start, validation_end),
        "average_gross_exposure": 1.0,
        "selected_on_training": False,
    }
    comparison = pd.concat([comparison, pd.DataFrame([spy_row])], ignore_index=True)
    comparison.to_csv(output / "fixed-validation-comparison.csv", index=False)
    summary = {
        "selected_variant_on_2017_2019": selected,
        "variants_declared_before_run": list(VARIANTS),
        "selection_period": [args.train_start, args.train_end],
        "diagnostic_period": [args.validation_start, str(validation_end.date())],
        "selection_objective": "sharpe + cagr + 0.5*max_drawdown - 0.01*annual_turnover",
        "warning": (
            "The diagnostic period has been inspected in prior research. These results "
            "cannot be treated as fresh out-of-sample evidence."
        ),
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(f"selected on training: {selected}", flush=True)
    print(
        comparison[
            ["label", "cagr", "sharpe", "max_drawdown", "annual_turnover"]
        ].to_string(index=False),
        flush=True,
    )


if __name__ == "__main__":
    main()
