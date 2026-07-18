from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import time

import pandas as pd
from dotenv import load_dotenv

from project_geld.backtest import run_backtest
from project_geld.broad_universe import (
    BroadUniverseRules,
    asset_master_frame,
    membership_periods_from_selections,
    monthly_candidate_rows,
    select_top_liquid,
)
from project_geld.config import load_config, validate_config
from project_geld.data import AlpacaBarSource, normalize_bars
from project_geld.research import MembershipAllocation, StaticAllocation, period_metrics
from project_geld.strategies.registry import create_strategy


def utc_date(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def chunks(values: list[str], size: int):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def batch_path(directory: Path, index: int, symbols: list[str]) -> Path:
    digest = sha256("|".join(symbols).encode("utf-8")).hexdigest()[:10]
    return directory / f"batch_{index:04d}_{digest}.csv.gz"


def fetch_with_retries(source, symbols, start, end, attempts=3):
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            return source.fetch(symbols, start, end)
        except Exception as caught:
            error = caught
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    assert error is not None
    raise error


def read_bars(path: Path) -> pd.DataFrame:
    return normalize_bars(pd.read_csv(path))


def save_compact(result, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    result.equity.to_csv(directory / "equity.csv", index=False)
    result.trades.to_csv(directory / "trades.csv", index=False)
    (directory / "metrics.json").write_text(
        json.dumps(result.metrics, indent=2, sort_keys=True), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Point-in-time broad US-equity Momentum V2 research."
    )
    parser.add_argument("--config", default="configs/equity-momentum-v2.toml")
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default="2026-07-16")
    parser.add_argument("--top-n", type=int, default=500)
    parser.add_argument("--minimum-price", type=float, default=5.0)
    parser.add_argument("--minimum-dollar-volume", type=float, default=20_000_000)
    parser.add_argument("--history-sessions", type=int, default=252)
    parser.add_argument("--dollar-volume-window", type=int, default=60)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-assets", type=int)
    parser.add_argument("--cache-dir", default="data/broad-universe")
    parser.add_argument("--output", default="artifacts/research-broad")
    args = parser.parse_args()

    config = load_config(args.config)
    validate_config(config)
    rules = BroadUniverseRules(
        top_n=args.top_n,
        minimum_price=args.minimum_price,
        minimum_history_sessions=args.history_sessions,
        dollar_volume_window=args.dollar_volume_window,
        minimum_dollar_volume=args.minimum_dollar_volume,
    )
    start = utc_date(args.start)
    end = utc_date(args.end)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    cache = Path(args.cache_dir) / f"sip_{args.start.replace('-', '')}_{args.end.replace('-', '')}"
    batch_directory = cache / "batches"
    batch_directory.mkdir(parents=True, exist_ok=True)

    load_dotenv()
    api_key = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
    secret_key = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
    if not api_key or not secret_key:
        raise RuntimeError("Alpaca credentials are missing.")
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import AssetClass
    from alpaca.trading.requests import GetAssetsRequest

    trading = TradingClient(api_key, secret_key, paper=True)
    assets = trading.get_all_assets(GetAssetsRequest(asset_class=AssetClass.US_EQUITY))
    master = asset_master_frame(assets)
    master.to_csv(output / "asset-master.csv", index=False)
    eligible_assets = master[master["included"]].copy()
    symbols = sorted(eligible_assets["symbol"].unique())
    if args.max_assets is not None:
        symbols = symbols[: args.max_assets]
    eligible_assets[eligible_assets["symbol"].isin(symbols)].to_csv(
        output / "asset-candidates.csv", index=False
    )
    print(
        f"asset master={len(master):,}; common-stock candidates={len(symbols):,}",
        flush=True,
    )

    source = AlpacaBarSource("sip", config.data.adjustment)
    benchmark_path = cache / "benchmark_SPY.csv.gz"
    if not benchmark_path.exists():
        benchmark = fetch_with_retries(source, [config.universe.benchmark], start, end)
        benchmark.to_csv(benchmark_path, index=False, compression="gzip")
    benchmark = read_bars(benchmark_path)
    market_sessions = pd.DatetimeIndex(
        sorted(benchmark["timestamp"].drop_duplicates())
    )
    month_end_sessions = pd.DatetimeIndex(
        pd.Series(market_sessions, index=market_sessions)
        .groupby(market_sessions.tz_localize(None).to_period("M"))
        .max()
        .to_list()
    )

    paths: list[Path] = []
    symbol_batches = list(chunks(symbols, args.batch_size))
    for index, symbol_batch in enumerate(symbol_batches):
        path = batch_path(batch_directory, index, symbol_batch)
        paths.append(path)
        if path.exists():
            print(
                f"data batch {index + 1}/{len(symbol_batches)} cached ({len(symbol_batch)} symbols)",
                flush=True,
            )
            continue
        bars = fetch_with_retries(source, symbol_batch, start, end)
        bars.to_csv(path, index=False, compression="gzip")
        print(
            f"data batch {index + 1}/{len(symbol_batches)} fetched: {len(bars):,} bars",
            flush=True,
        )

    candidate_frames = []
    coverage_rows = []
    for index, path in enumerate(paths):
        bars = read_bars(path)
        candidate_frames.append(monthly_candidate_rows(bars, month_end_sessions, rules))
        if len(bars):
            coverage = bars.groupby("symbol")["timestamp"].agg(
                first="min", last="max", observations="count"
            )
            coverage_rows.append(coverage.reset_index())
        print(f"eligibility batch {index + 1}/{len(paths)}", flush=True)
    candidates = pd.concat(candidate_frames, ignore_index=True)
    candidates.to_csv(
        output / "monthly-eligible-candidates.csv.gz",
        index=False,
        compression="gzip",
    )
    selected = select_top_liquid(candidates, rules)
    selected.to_csv(output / "monthly-selected-universe.csv.gz", index=False, compression="gzip")
    membership = membership_periods_from_selections(
        selected, month_end_sessions, market_sessions
    )
    (output / "membership-periods.json").write_text(
        json.dumps(membership, indent=2, sort_keys=True), encoding="utf-8"
    )
    coverage = pd.concat(coverage_rows, ignore_index=True)
    coverage.to_csv(output / "data-coverage.csv", index=False)

    selected_symbols = sorted(membership)
    selected_frames = [benchmark]
    for index, path in enumerate(paths):
        bars = read_bars(path)
        subset = bars[bars["symbol"].isin(selected_symbols)]
        if len(subset):
            selected_frames.append(subset)
        print(f"assemble batch {index + 1}/{len(paths)}", flush=True)
    selected_bars = normalize_bars(pd.concat(selected_frames, ignore_index=True))
    selected_bars.to_csv(
        output / "selected-bars.csv.gz", index=False, compression="gzip"
    )
    print(
        f"selected union={len(selected_symbols):,}; bars={len(selected_bars):,}",
        flush=True,
    )

    parameters = {
        **config.strategy.parameters,
        "sector_map": {},
        "max_per_sector": int(config.strategy.parameters.get("max_symbols", 5)),
        "membership_periods": membership,
    }
    momentum = run_backtest(
        selected_bars,
        create_strategy(config.strategy.name, parameters),
        config.backtest,
        config.risk,
        config.universe.benchmark,
        selected_symbols,
    )
    equal_weight = run_backtest(
        selected_bars,
        MembershipAllocation(membership, gross_exposure=0.75),
        replace(config.backtest, rebalance_every=21),
        config.risk,
        config.universe.benchmark,
        selected_symbols,
    )
    benchmark_risk = replace(
        config.risk, max_gross_exposure=1.0, max_position_weight=1.0
    )
    spy_75 = run_backtest(
        benchmark,
        StaticAllocation(gross_exposure=0.75),
        replace(config.backtest, rebalance_every=21),
        benchmark_risk,
        config.universe.benchmark,
        [config.universe.benchmark],
    )
    spy_100 = run_backtest(
        benchmark,
        StaticAllocation(gross_exposure=1.0),
        replace(config.backtest, rebalance_every=100_000),
        benchmark_risk,
        config.universe.benchmark,
        [config.universe.benchmark],
    )
    evaluation_start = pd.to_datetime(selected["timestamp"], utc=True).min()
    evaluation_end = market_sessions.max()
    compared = {
        "broad_momentum_v2": momentum,
        "broad_equal_weight_75pct": equal_weight,
        "SPY_75pct_monthly": spy_75,
        "SPY_100pct_buy_hold": spy_100,
    }
    comparison_rows = [
        {"label": label, **period_metrics(result, evaluation_start, evaluation_end)}
        for label, result in compared.items()
    ]
    comparison = pd.DataFrame(comparison_rows)
    comparison.to_csv(output / "comparison.csv", index=False)
    annual_rows = []
    for year in range(evaluation_start.year, evaluation_end.year + 1):
        year_start = max(evaluation_start, pd.Timestamp(f"{year}-01-01", tz="UTC"))
        year_end = min(evaluation_end, pd.Timestamp(f"{year}-12-31", tz="UTC"))
        for label, result in compared.items():
            annual_rows.append(
                {
                    "year": year,
                    "label": label,
                    **period_metrics(result, year_start, year_end),
                }
            )
    pd.DataFrame(annual_rows).to_csv(output / "annual-comparison.csv", index=False)
    save_compact(momentum, output / "momentum-v2")
    save_compact(equal_weight, output / "equal-weight")
    summary = {
        "rules": asdict(rules),
        "asset_master_records": int(len(master)),
        "asset_candidates": int(len(symbols)),
        "selected_symbol_union": int(len(selected_symbols)),
        "first_universe_date": str(evaluation_start),
        "last_session": str(evaluation_end),
        "market_sessions": int(len(market_sessions)),
    }
    (output / "run-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(
        comparison[["label", "cagr", "sharpe", "max_drawdown"]].to_string(index=False),
        flush=True,
    )
    print(f"Saved: {output.resolve()}", flush=True)


if __name__ == "__main__":
    main()
