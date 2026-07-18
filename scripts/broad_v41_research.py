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
from project_geld.research import period_metrics
from project_geld.strategies.registry import create_strategy


VARIANTS = {
    "price_control": {
        "core_weight": 0.75,
        "active_weight": 0.25,
        "price_score_weight": 1.0,
        "quality_score_weight": 0.0,
        "earnings_score_weight": 0.0,
        "weighting_method": "inverse_downside_volatility",
    },
    "price_benchmark_aware": {
        "core_weight": 0.75,
        "active_weight": 0.25,
        "price_score_weight": 1.0,
        "quality_score_weight": 0.0,
        "earnings_score_weight": 0.0,
        "weighting_method": "benchmark_aware",
    },
    "defensive_price": {
        "core_weight": 0.60,
        "active_weight": 0.25,
        "price_score_weight": 1.0,
        "quality_score_weight": 0.0,
        "earnings_score_weight": 0.0,
        "weighting_method": "benchmark_aware",
    },
    "price_quality": {
        "core_weight": 0.75,
        "active_weight": 0.25,
        "price_score_weight": 0.70,
        "quality_score_weight": 0.30,
        "earnings_score_weight": 0.0,
        "weighting_method": "benchmark_aware",
    },
    "price_earnings": {
        "core_weight": 0.75,
        "active_weight": 0.25,
        "price_score_weight": 0.80,
        "quality_score_weight": 0.0,
        "earnings_score_weight": 0.20,
        "weighting_method": "benchmark_aware",
    },
    "full_v41": {
        "core_weight": 0.75,
        "active_weight": 0.25,
        "price_score_weight": 0.50,
        "quality_score_weight": 0.30,
        "earnings_score_weight": 0.20,
        "weighting_method": "benchmark_aware",
    },
    "defensive_full_v41": {
        "core_weight": 0.60,
        "active_weight": 0.25,
        "price_score_weight": 0.50,
        "quality_score_weight": 0.30,
        "earnings_score_weight": 0.20,
        "weighting_method": "benchmark_aware",
    },
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Momentum V4.1 component ablation.")
    parser.add_argument(
        "--price-only",
        action="store_true",
        help="Run variants that do not require SEC features.",
    )
    args = parser.parse_args()
    directory = Path("artifacts/research-broad")
    output = directory / "momentum-v41"
    output.mkdir(parents=True, exist_ok=True)
    feature_path = directory / "sec-fundamental-features.csv.gz"
    if not args.price_only and not feature_path.exists():
        raise RuntimeError(
            "SEC features are missing; run scripts/fetch_sec_fundamentals.py first."
        )
    config = load_config("configs/equity-momentum-v2.toml")
    bars = normalize_bars(pd.read_csv(directory / "selected-bars.csv.gz"))
    membership = json.loads(
        (directory / "membership-periods.json").read_text(encoding="utf-8")
    )
    stocks = sorted(membership)
    backtest = replace(
        config.backtest, rebalance_every=21, missing_price_haircut_pct=0.0
    )
    risk = replace(
        config.risk, max_gross_exposure=1.0, max_position_weight=0.75
    )
    rows = []
    variants = (
        {
            label: variant
            for label, variant in VARIANTS.items()
            if not variant["quality_score_weight"]
            and not variant["earnings_score_weight"]
        }
        if args.price_only
        else VARIANTS
    )
    for label, variant in variants.items():
        active = {
            "membership_periods": membership,
            "max_symbols": 40,
            "exit_rank": 80,
            "max_pairwise_correlation": 0.85,
            "maximum_annualized_volatility": 0.60,
            "price_score_weight": variant["price_score_weight"],
            "quality_score_weight": variant["quality_score_weight"],
            "earnings_score_weight": variant["earnings_score_weight"],
            "weighting_method": variant["weighting_method"],
        }
        if variant["quality_score_weight"] or variant["earnings_score_weight"]:
            active["external_features_file"] = str(feature_path)
        strategy = create_strategy(
            "daily_v4",
            {
                "core_symbol": "SPY",
                "core_weight": variant["core_weight"],
                "active_weight": variant["active_weight"],
                "active_name_cap": 0.02,
                "no_trade_band": 0.0025,
                "rebalance_every": 21,
                "active_parameters": active,
            },
        )
        result = run_backtest(
            bars,
            strategy,
            backtest,
            risk,
            "SPY",
            [*stocks, "SPY"],
            context_symbols=strategy.context_symbols,
        )
        for period, start, end in [
            ("training", "2017-01-01", "2019-12-31"),
            ("diagnostic", "2020-01-01", str(bars["timestamp"].max().date())),
        ]:
            rows.append(
                {
                    "variant": label,
                    "period": period,
                    **period_metrics(result, start, end),
                }
            )
        variant_dir = output / label
        variant_dir.mkdir(parents=True, exist_ok=True)
        result.equity.to_csv(variant_dir / "equity.csv.gz", index=False)
        result.trades.to_csv(variant_dir / "trades.csv.gz", index=False)
        print(f"completed {label}", flush=True)
        del result
        gc.collect()
    metrics = pd.DataFrame(rows)
    metrics.to_csv(output / "ablation-metrics.csv", index=False)
    print(
        metrics[
            metrics["period"].eq("diagnostic")
        ][["variant", "cagr", "sharpe", "max_drawdown", "annual_turnover"]].to_string(index=False)
    )


if __name__ == "__main__":
    main()
