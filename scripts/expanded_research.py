from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd

from project_geld.backtest import run_backtest, save_result
from project_geld.config import load_config, validate_config
from project_geld.data import AlpacaBarSource, CachedBarSource
from project_geld.metrics import calculate_metrics
from project_geld.research import (
    MembershipAllocation,
    StaticAllocation,
    one_at_a_time_variants,
    period_metrics,
)
from project_geld.strategies.registry import create_strategy


FROZEN_2016_DOW = [
    "AXP", "AAPL", "BA", "CAT", "CSCO", "CVX", "DD", "DIS", "GE", "GS",
    "HD", "IBM", "INTC", "JNJ", "JPM", "KO", "MCD", "MMM", "MRK", "MSFT",
    "NKE", "PFE", "PG", "TRV", "UNH", "RTX", "V", "VZ", "WMT", "XOM",
]

DOW_SECTORS = {
    "AXP": "financials", "AAPL": "technology", "BA": "industrials",
    "CAT": "industrials", "CSCO": "technology", "CVX": "energy",
    "DD": "materials", "DIS": "communication", "GE": "industrials",
    "GS": "financials", "HD": "consumer_discretionary", "IBM": "technology",
    "INTC": "technology", "JNJ": "health_care", "JPM": "financials",
    "KO": "consumer_staples", "MCD": "consumer_discretionary",
    "MMM": "industrials", "MRK": "health_care", "MSFT": "technology",
    "NKE": "consumer_discretionary", "PFE": "health_care",
    "PG": "consumer_staples", "TRV": "financials", "UNH": "health_care",
    "RTX": "industrials", "V": "financials", "VZ": "communication",
    "WMT": "consumer_staples", "XOM": "energy", "WBA": "consumer_staples",
    "DOW": "materials", "CRM": "technology", "AMGN": "health_care",
    "HON": "industrials", "AMZN": "consumer_discretionary",
    "NVDA": "technology", "SHW": "materials",
}

DYNAMIC_DOW_MEMBERSHIP: dict[str, list[list[str | None]]] = {
    symbol: [["2016-01-01", None]] for symbol in FROZEN_2016_DOW
}
DYNAMIC_DOW_MEMBERSHIP.update(
    {
        "GE": [["2016-01-01", "2018-06-25"]],
        "WBA": [["2018-06-26", "2024-02-25"]],
        "DD": [["2016-01-01", "2019-04-01"]],
        "DOW": [["2019-04-02", "2024-11-07"]],
        "XOM": [["2016-01-01", "2020-08-30"]],
        "PFE": [["2016-01-01", "2020-08-30"]],
        "RTX": [["2016-01-01", "2020-08-30"]],
        "CRM": [["2020-08-31", None]],
        "AMGN": [["2020-08-31", None]],
        "HON": [["2020-08-31", None]],
        "AMZN": [["2024-02-26", None]],
        "INTC": [["2016-01-01", "2024-11-07"]],
        "NVDA": [["2024-11-08", None]],
        "SHW": [["2024-11-08", None]],
    }
)

SECTOR_ETFS = ["XLK", "XLC", "XLY", "XLP", "XLF", "XLV", "XLI", "XLE", "XLB", "XLU", "XLRE"]


def utc_date(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def metric_row(label: str, metrics: dict[str, float], **extra: Any) -> dict[str, Any]:
    return {"label": label, **extra, **metrics}


def strategy_result(bars, config, symbols, parameters, slippage_bps=10.0):
    cadence = int(parameters.get("rebalance_every", config.backtest.rebalance_every))
    return run_backtest(
        bars,
        create_strategy(config.strategy.name, parameters),
        replace(
            config.backtest,
            rebalance_every=cadence,
            slippage_bps=slippage_bps,
        ),
        config.risk,
        config.universe.benchmark,
        symbols,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Expanded Momentum V2 robustness research.")
    parser.add_argument("--config", default="configs/equity-momentum-v2.toml")
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default="2026-07-16")
    parser.add_argument("--output", default="artifacts/research-expanded")
    args = parser.parse_args()

    config = load_config(args.config)
    validate_config(config)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    dynamic_symbols = sorted(DYNAMIC_DOW_MEMBERSHIP)
    all_symbols = list(
        dict.fromkeys(
            config.universe.symbols
            + FROZEN_2016_DOW
            + dynamic_symbols
            + SECTOR_ETFS
            + [config.universe.benchmark]
        )
    )
    source = CachedBarSource(
        AlpacaBarSource("sip", config.data.adjustment), config.data.cache_dir
    )
    bars = source.fetch(all_symbols, utc_date(args.start), utc_date(args.end))
    if bars.empty:
        raise RuntimeError("Alpaca returned no SIP bars.")

    sessions = bars[bars["symbol"] == config.universe.benchmark]["timestamp"].nunique()
    dates = pd.DatetimeIndex(sorted(bars["timestamp"].unique()))
    common_warmup_sessions = 316
    evaluation_start = dates[min(common_warmup_sessions, len(dates) - 2)]
    evaluation_end = dates.max()
    coverage = (
        bars.groupby("symbol")["timestamp"]
        .agg(first="min", last="max", observations="count")
        .reset_index()
    )
    coverage["coverage_fraction"] = coverage["observations"] / sessions
    coverage.to_csv(output / "data-coverage.csv", index=False)

    base_parameters = dict(config.strategy.parameters)
    variants = one_at_a_time_variants(base_parameters)
    variant_results: dict[str, Any] = {}
    stability_rows: list[dict[str, Any]] = []
    for label, parameters in variants:
        result = strategy_result(
            bars, config, config.universe.symbols, parameters, slippage_bps=10.0
        )
        variant_results[label] = result
        evaluated = period_metrics(result, evaluation_start, evaluation_end)
        stability_rows.append(
            metric_row(label, evaluated, parameters=json.dumps(parameters, sort_keys=True))
        )
        print(f"parameter stability: {label}")
    stability = pd.DataFrame(stability_rows).sort_values("sharpe", ascending=False)
    stability.to_csv(output / "parameter-stability.csv", index=False)
    save_result(variant_results["base"], output / "long-history-base")

    cost_rows = []
    for slippage in [0.0, 5.0, 10.0, 25.0, 50.0, 100.0]:
        result = strategy_result(
            bars,
            config,
            config.universe.symbols,
            base_parameters,
            slippage_bps=slippage,
        )
        cost_rows.append(
            metric_row(
                f"{slippage:g} bps",
                period_metrics(result, evaluation_start, evaluation_end),
                slippage_bps=slippage,
            )
        )
    pd.DataFrame(cost_rows).to_csv(output / "cost-stress.csv", index=False)

    baseline_rows = []
    benchmark_risk = replace(
        config.risk, max_gross_exposure=1.0, max_position_weight=1.0
    )
    spy_75 = run_backtest(
        bars,
        StaticAllocation(gross_exposure=0.75),
        replace(config.backtest, rebalance_every=21, slippage_bps=10.0),
        benchmark_risk,
        config.universe.benchmark,
        [config.universe.benchmark],
    )
    spy_100 = run_backtest(
        bars,
        StaticAllocation(gross_exposure=1.0),
        replace(config.backtest, rebalance_every=100_000, slippage_bps=10.0),
        benchmark_risk,
        config.universe.benchmark,
        [config.universe.benchmark],
    )
    equal_weight = run_backtest(
        bars,
        StaticAllocation(gross_exposure=0.75),
        replace(config.backtest, rebalance_every=21, slippage_bps=10.0),
        config.risk,
        config.universe.benchmark,
        config.universe.symbols,
    )
    baseline_rows.extend(
        [
            metric_row(
                "momentum_v2",
                period_metrics(variant_results["base"], evaluation_start, evaluation_end),
            ),
            metric_row(
                "SPY_75pct_monthly",
                period_metrics(spy_75, evaluation_start, evaluation_end),
            ),
            metric_row(
                "SPY_100pct_buy_hold",
                period_metrics(spy_100, evaluation_start, evaluation_end),
            ),
            metric_row(
                "current_basket_equal_weight_75pct",
                period_metrics(equal_weight, evaluation_start, evaluation_end),
            ),
        ]
    )
    baselines = pd.DataFrame(baseline_rows)
    baselines.to_csv(output / "baseline-comparison.csv", index=False)

    regime_rows = []
    regimes = {
        "pre_covid_2016_2019": ("2016-01-01", "2019-12-31"),
        "covid_2020_2021": ("2020-01-01", "2021-12-31"),
        "bear_2022": ("2022-01-01", "2022-12-31"),
        "recent_2023_2026": ("2023-01-01", args.end),
    }
    compared = {
        "momentum_v2": variant_results["base"],
        "SPY_75pct_monthly": spy_75,
        "SPY_100pct_buy_hold": spy_100,
        "current_basket_equal_weight_75pct": equal_weight,
    }
    for regime, (start, end) in regimes.items():
        regime_start = max(pd.Timestamp(start, tz="UTC"), evaluation_start)
        for label, result in compared.items():
            regime_rows.append(
                metric_row(label, period_metrics(result, regime_start, end), regime=regime)
            )
    pd.DataFrame(regime_rows).to_csv(output / "regime-comparison.csv", index=False)

    universe_specs = [
        ("current_34", config.universe.symbols, base_parameters),
        (
            "current_without_NVDA_META_AVGO",
            [s for s in config.universe.symbols if s not in {"NVDA", "META", "AVGO"}],
            base_parameters,
        ),
        (
            "frozen_Jan_2016_Dow_proxy",
            FROZEN_2016_DOW,
            {**base_parameters, "sector_map": DOW_SECTORS},
        ),
        (
            "point_in_time_Dow_proxy",
            dynamic_symbols,
            {
                **base_parameters,
                "sector_map": DOW_SECTORS,
                "membership_periods": DYNAMIC_DOW_MEMBERSHIP,
            },
        ),
        (
            "sector_ETFs",
            SECTOR_ETFS,
            {
                **base_parameters,
                "sector_map": {symbol: symbol for symbol in SECTOR_ETFS},
            },
        ),
    ]
    universe_rows = []
    for label, symbols, parameters in universe_specs:
        result = strategy_result(bars, config, symbols, parameters, slippage_bps=10.0)
        universe_rows.append(
            metric_row(
                f"{label}:momentum_v2",
                period_metrics(result, evaluation_start, evaluation_end),
                universe=label,
                approach="momentum_v2",
                symbols=len(symbols),
            )
        )
        allocation = (
            MembershipAllocation(DYNAMIC_DOW_MEMBERSHIP, gross_exposure=0.75)
            if label == "point_in_time_Dow_proxy"
            else StaticAllocation(gross_exposure=0.75)
        )
        equal_result = run_backtest(
            bars,
            allocation,
            replace(config.backtest, rebalance_every=21, slippage_bps=10.0),
            config.risk,
            config.universe.benchmark,
            symbols,
        )
        universe_rows.append(
            metric_row(
                f"{label}:equal_weight",
                period_metrics(equal_result, evaluation_start, evaluation_end),
                universe=label,
                approach="equal_weight",
                symbols=len(symbols),
            )
        )
        print(f"universe stress: {label}")
    pd.DataFrame(universe_rows).to_csv(output / "universe-stress.csv", index=False)

    membership_rows = [
        {"symbol": symbol, "start": start, "end": end}
        for symbol, periods in sorted(DYNAMIC_DOW_MEMBERSHIP.items())
        for start, end in periods
    ]
    pd.DataFrame(membership_rows).to_csv(output / "dow-membership-proxy.csv", index=False)

    first_year = max(dates.min().year + 3, 2019)
    last_year = dates.max().year
    fold_rows = []
    stitched_rows = []
    for test_year in range(first_year, last_year + 1):
        train_start = pd.Timestamp(f"{test_year - 3}-01-01", tz="UTC")
        train_end = pd.Timestamp(f"{test_year - 1}-12-31", tz="UTC")
        test_start = pd.Timestamp(f"{test_year}-01-01", tz="UTC")
        test_end = min(pd.Timestamp(f"{test_year}-12-31", tz="UTC"), dates.max())
        choices = []
        for label, _ in variants:
            train = period_metrics(variant_results[label], train_start, train_end)
            objective = (
                train["sharpe"]
                - 0.25 * abs(train["max_drawdown"])
                - 0.01 * train["annual_turnover"]
            )
            choices.append((objective, label, train))
        objective, selected, train = max(choices, key=lambda item: item[0])
        selected_result = variant_results[selected]
        test = period_metrics(selected_result, test_start, test_end)
        fold_rows.append(
            {
                "test_year": test_year,
                "train_start": train_start,
                "train_end": train_end,
                "test_start": test_start,
                "test_end": test_end,
                "selected_variant": selected,
                "selection_objective": objective,
                "train_sharpe": train["sharpe"],
                "train_max_drawdown": train["max_drawdown"],
                "test_return": test["total_return"],
                "test_sharpe": test["sharpe"],
                "test_max_drawdown": test["max_drawdown"],
            }
        )
        fold_equity = selected_result.equity[
            selected_result.equity["timestamp"].between(test_start, test_end)
        ][["timestamp", "daily_return", "benchmark_return"]].copy()
        fold_equity["selected_variant"] = selected
        stitched_rows.append(fold_equity)
    folds = pd.DataFrame(fold_rows)
    folds.to_csv(output / "walk-forward-folds.csv", index=False)
    stitched = pd.concat(stitched_rows, ignore_index=True).sort_values("timestamp")
    stitched["equity"] = config.backtest.initial_cash * (1 + stitched["daily_return"]).cumprod()
    stitched["cash"] = float("nan")
    stitched["gross_exposure"] = float("nan")
    stitched.to_csv(output / "walk-forward-equity.csv", index=False)
    walk_metrics = calculate_metrics(stitched, pd.DataFrame())
    (output / "walk-forward-metrics.json").write_text(
        json.dumps(walk_metrics, indent=2, sort_keys=True), encoding="utf-8"
    )
    walk_start = stitched["timestamp"].min()
    walk_end = stitched["timestamp"].max()
    pd.DataFrame(
        [
            metric_row("walk_forward_selected", walk_metrics),
            metric_row("SPY_75pct_monthly", period_metrics(spy_75, walk_start, walk_end)),
            metric_row("SPY_100pct_buy_hold", period_metrics(spy_100, walk_start, walk_end)),
            metric_row(
                "current_basket_equal_weight_75pct",
                period_metrics(equal_weight, walk_start, walk_end),
            ),
        ]
    ).to_csv(output / "walk-forward-baseline-comparison.csv", index=False)

    summary = {
        "requested_start": args.start,
        "actual_start": str(bars["timestamp"].min()),
        "actual_end": str(bars["timestamp"].max()),
        "sessions": int(sessions),
        "symbols_requested": len(all_symbols),
        "evaluation_start": str(evaluation_start),
        "base_metrics": period_metrics(
            variant_results["base"], evaluation_start, evaluation_end
        ),
        "walk_forward_metrics": walk_metrics,
    }
    (output / "run-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(baselines[["label", "cagr", "sharpe", "max_drawdown"]].to_string(index=False))
    print(f"Saved: {output.resolve()}")


if __name__ == "__main__":
    main()
