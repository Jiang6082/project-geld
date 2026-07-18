from __future__ import annotations

import argparse
from dataclasses import replace
import gc
import json
from pathlib import Path

import pandas as pd

from project_geld.backtest import run_backtest
from project_geld.config import load_config
from project_geld.data import normalize_bars
from project_geld.research import StaticAllocation, period_metrics
from project_geld.strategies.registry import create_strategy


VARIANTS: dict[str, dict] = {
    "balanced_40": {
        "max_symbols": 40,
        "exit_rank": 80,
        "max_pairwise_correlation": 0.85,
        "maximum_annualized_volatility": 0.60,
        "regime_enabled": True,
    },
    "balanced_30": {
        "max_symbols": 30,
        "exit_rank": 60,
        "max_pairwise_correlation": 0.85,
        "maximum_annualized_volatility": 0.60,
        "regime_enabled": True,
    },
    "strict_correlation_40": {
        "max_symbols": 40,
        "exit_rank": 80,
        "max_pairwise_correlation": 0.70,
        "maximum_annualized_volatility": 0.60,
        "regime_enabled": True,
    },
    "always_on_40": {
        "max_symbols": 40,
        "exit_rank": 80,
        "max_pairwise_correlation": 0.85,
        "maximum_annualized_volatility": 0.60,
        "regime_enabled": False,
    },
}


def selection_score(metrics: dict[str, float]) -> float:
    """Predeclared training objective: reward quality, penalize drawdown and churn."""

    return (
        metrics["sharpe"]
        + metrics["cagr"]
        + 0.50 * metrics["max_drawdown"]
        - 0.01 * metrics["annual_turnover"]
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train-select and fixed-split validation for Momentum V3."
    )
    parser.add_argument("--config", default="configs/equity-momentum-v2.toml")
    parser.add_argument("--research-dir", default="artifacts/research-broad")
    parser.add_argument("--train-start", default="2017-01-01")
    parser.add_argument("--train-end", default="2019-12-31")
    parser.add_argument("--validation-start", default="2020-01-01")
    args = parser.parse_args()

    config = load_config(args.config)
    directory = Path(args.research_dir)
    output = directory / "momentum-v3"
    output.mkdir(parents=True, exist_ok=True)
    bars = normalize_bars(pd.read_csv(directory / "selected-bars.csv.gz"))
    membership = json.loads(
        (directory / "membership-periods.json").read_text(encoding="utf-8")
    )
    symbols = sorted(membership)
    benchmark = config.universe.benchmark
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
        max_position_weight=0.04,
    )

    rows: list[dict] = []
    for label, variant in VARIANTS.items():
        parameters = {
            "benchmark_symbol": benchmark,
            "membership_periods": membership,
            "rebalance_every": 21,
            "max_position_weight": 0.04,
            **variant,
        }
        result = run_backtest(
            bars,
            create_strategy("momentum_v3", parameters),
            backtest,
            risk,
            benchmark,
            symbols,
            context_symbols=[benchmark],
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

    spy_bars = bars[bars["symbol"].eq(benchmark)]
    spy_risk = replace(risk, max_position_weight=1.0)
    spy_75 = run_backtest(
        spy_bars,
        StaticAllocation(gross_exposure=0.75),
        replace(backtest, rebalance_every=100_000),
        spy_risk,
        benchmark,
        [benchmark],
    )
    selected_metrics = metrics[
        metrics["variant"].eq(selected)
        & metrics["period"].eq("fixed_validation")
    ].iloc[0].to_dict()
    comparison_rows = [
        {"label": f"momentum_v3_selected_{selected}", **selected_metrics},
        {
            "label": "SPY_75pct_buy_hold",
            **period_metrics(spy_75, validation_start, validation_end),
        },
    ]
    prior_path = directory / "fixed-holdings-validation.csv"
    if prior_path.exists():
        prior = pd.read_csv(prior_path)
        comparison_rows.extend(prior.to_dict("records"))
    comparison = pd.DataFrame(comparison_rows)
    comparison.to_csv(output / "fixed-validation-comparison.csv", index=False)
    summary = {
        "selected_variant": selected,
        "selection_period": [args.train_start, args.train_end],
        "fixed_validation_period": [args.validation_start, str(validation_end.date())],
        "selection_objective": "sharpe + cagr + 0.5*max_drawdown - 0.01*annual_turnover",
        "variants_declared_before_run": list(VARIANTS),
        "note": (
            "The validation period is a fixed split, but it is pseudo-out-of-sample "
            "because earlier V2 research already inspected these years."
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
