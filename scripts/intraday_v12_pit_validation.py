from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
import gc
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

from project_geld.broad_universe import (
    BroadUniverseRules,
    asset_master_frame,
    causal_membership_periods_from_monthly_selections,
    monthly_candidate_rows,
    select_top_liquid,
)
from project_geld.config import load_config
from project_geld.data import AlpacaBarSource, normalize_bars
from project_geld.intraday import label_native_intraday_bar_ends, run_intraday_backtest
from project_geld.strategies.registry import create_strategy


ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data/cache/pit-v12"
OUTPUT = ROOT / "artifacts/research-intra-v12-pit"
PUBLISHED_SELECTIONS = ROOT / "universes/pit-liquid-100-monthly-2019-2026.csv.gz"
PUBLISHED_MEMBERSHIP = ROOT / "universes/pit-liquid-100-membership-2019-2026.json"
START = datetime(2019, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 7, 19, tzinfo=timezone.utc)


def chunks(values: list[str], size: int):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def batch_path(directory: Path, index: int, symbols: list[str]) -> Path:
    digest = sha256("|".join(symbols).encode("utf-8")).hexdigest()[:10]
    return directory / f"batch_{index:04d}_{digest}.csv.gz"


def fetch_with_retries(
    source, symbols, timeframe, start=START, end=END, attempts=7
):
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            return source.fetch(symbols, start, end, timeframe)
        except Exception as caught:
            error = caught
            if attempt + 1 < attempts:
                time.sleep(min(5 * 2**attempt, 60))
    assert error is not None
    raise error


def fetch_batches(
    source,
    symbols: list[str],
    timeframe: str,
    directory: Path,
    batch_size: int,
) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    batches = list(chunks(symbols, batch_size))
    for index, batch in enumerate(batches):
        path = batch_path(directory, index, batch)
        paths.append(path)
        if path.exists():
            print(f"{directory.name} {index + 1}/{len(batches)} cached", flush=True)
            continue
        bars = fetch_with_retries(source, batch, timeframe)
        bars.to_csv(path, index=False, compression="gzip")
        print(
            f"{directory.name} {index + 1}/{len(batches)} fetched {len(bars):,}",
            flush=True,
        )
    return paths


def read_batch(path: Path) -> pd.DataFrame:
    return normalize_bars(pd.read_csv(path))


def build_asset_master() -> pd.DataFrame:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import AssetClass, AssetStatus
    from alpaca.trading.requests import GetAssetsRequest
    from project_geld.credentials import load_alpaca_credentials

    key, secret = load_alpaca_credentials("INTRADAY")
    client = TradingClient(key, secret, paper=True)
    assets = []
    for status in [AssetStatus.ACTIVE, AssetStatus.INACTIVE]:
        assets.extend(
            client.get_all_assets(
                GetAssetsRequest(asset_class=AssetClass.US_EQUITY, status=status)
            )
        )
    return asset_master_frame(assets)


def build_universe() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    master = build_asset_master()
    master.to_csv(OUTPUT / "asset-master-active-and-inactive.csv", index=False)
    candidates = sorted(master.loc[master["included"], "symbol"].unique())
    print(
        f"asset records={len(master):,}; common-stock symbols={len(candidates):,}; "
        f"inactive included={len(master[master['included'] & master['status'].eq('INACTIVE')]):,}",
        flush=True,
    )

    source = AlpacaBarSource("sip", "raw", "INTRADAY")
    daily_paths = fetch_batches(
        source, candidates, "1Day", CACHE / "daily-sip-raw", 100
    )
    benchmark = fetch_with_retries(source, ["SPY"], "1Day")
    market_sessions = pd.DatetimeIndex(sorted(benchmark["timestamp"].unique()))
    month_end_sessions = pd.DatetimeIndex(
        pd.Series(market_sessions, index=market_sessions)
        .groupby(market_sessions.tz_localize(None).to_period("M"))
        .max()
        .to_list()
    )
    rules = BroadUniverseRules(
        top_n=100,
        minimum_price=5.0,
        minimum_history_sessions=60,
        dollar_volume_window=20,
        minimum_dollar_volume=10_000_000.0,
    )
    candidate_rows = []
    coverage_rows = []
    for index, path in enumerate(daily_paths):
        bars = read_batch(path)
        candidate_rows.append(
            monthly_candidate_rows(bars, month_end_sessions, rules)
        )
        if len(bars):
            coverage_rows.append(
                bars.groupby("symbol")["timestamp"]
                .agg(first="min", last="max", observations="count")
                .reset_index()
            )
        print(f"rank inputs {index + 1}/{len(daily_paths)}", flush=True)
    eligible = pd.concat(candidate_rows, ignore_index=True)
    selected = select_top_liquid(eligible, rules)
    membership = causal_membership_periods_from_monthly_selections(
        selected, month_end_sessions, market_sessions
    )
    PUBLISHED_SELECTIONS.parent.mkdir(parents=True, exist_ok=True)
    selected.to_csv(PUBLISHED_SELECTIONS, index=False, compression="gzip")
    PUBLISHED_MEMBERSHIP.write_text(
        json.dumps(membership, indent=2, sort_keys=True), encoding="utf-8"
    )
    pd.concat(coverage_rows, ignore_index=True).to_csv(
        OUTPUT / "daily-data-coverage.csv", index=False
    )
    summary = {
        "start": START.isoformat(),
        "end": END.isoformat(),
        "rules": rules.__dict__,
        "asset_master_records": int(len(master)),
        "candidate_symbols": int(len(candidates)),
        "inactive_candidates": int(
            len(master[master["included"] & master["status"].eq("INACTIVE")])
        ),
        "monthly_selections": int(len(selected)),
        "selected_symbol_union": int(len(membership)),
        "first_effective_membership": min(
            period[0] for periods in membership.values() for period in periods
        ),
    }
    (OUTPUT / "universe-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2), flush=True)


def load_membership() -> dict[str, list[list[str | None]]]:
    if not PUBLISHED_MEMBERSHIP.exists():
        raise FileNotFoundError("Run --stage universe before fetching intraday data.")
    return json.loads(PUBLISHED_MEMBERSHIP.read_text(encoding="utf-8"))


def fetch_intraday() -> None:
    membership = load_membership()
    fixed = load_config(ROOT / "configs/research-intra-v12-broad.toml")
    symbols = sorted(
        set(membership) | set(fixed.universe.symbols) | {fixed.universe.benchmark}
    )
    for feed in ["iex", "sip"]:
        directory = CACHE / f"intraday-{feed}-windows"
        directory.mkdir(parents=True, exist_ok=True)
        pending = []
        for symbol in symbols:
            start, end = symbol_window(symbol, membership, set(fixed.universe.symbols))
            path = symbol_window_path(directory, symbol, start, end)
            if not path.exists():
                pending.append((symbol, start, end, path))
        completed = len(symbols) - len(pending)
        print(
            f"intraday-{feed}-windows cached {completed}/{len(symbols)}",
            flush=True,
        )

        def fetch_one(job):
            symbol, start, end, path = job
            source = AlpacaBarSource(feed, "all", "INTRADAY")
            bars = fetch_with_retries(
                source, [symbol], "15Min", start=start, end=end
            )
            bars.to_csv(path, index=False, compression="gzip")
            return symbol

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(fetch_one, job) for job in pending]
            for future in as_completed(futures):
                future.result()
                completed += 1
                if completed % 10 == 0 or completed == len(symbols):
                    print(
                        f"intraday-{feed}-windows {completed}/{len(symbols)}",
                        flush=True,
                    )
    (OUTPUT / "intraday-symbols.json").write_text(
        json.dumps(symbols, indent=2), encoding="utf-8"
    )
    print(f"intraday union={len(symbols):,}", flush=True)


def symbol_window(
    symbol: str,
    membership: dict[str, list[list[str | None]]],
    fixed_symbols: set[str],
) -> tuple[datetime, datetime]:
    if symbol in fixed_symbols or symbol == "SPY":
        return START, END
    periods = membership[symbol]
    first = min(
        pd.Timestamp(period[0]).tz_localize("UTC") for period in periods
    ) - pd.Timedelta("90 days")
    last_values = [
        pd.Timestamp(period[1]).tz_localize("UTC")
        if period[1] is not None
        else pd.Timestamp(END)
        for period in periods
    ]
    last = max(last_values) + pd.Timedelta("2 days")
    start = max(pd.Timestamp(START), first).to_pydatetime()
    end = min(pd.Timestamp(END), last).to_pydatetime()
    return start, end


def symbol_window_path(
    directory: Path, symbol: str, start: datetime, end: datetime
) -> Path:
    return directory / (
        f"{symbol}_{pd.Timestamp(start).strftime('%Y%m%d')}_"
        f"{pd.Timestamp(end).strftime('%Y%m%d')}.csv.gz"
    )


def assemble_feed(
    feed: str,
    symbols: list[str],
    membership: dict[str, list[list[str | None]]],
    fixed_symbols: set[str],
) -> pd.DataFrame:
    directory = CACHE / f"intraday-{feed}-windows"
    paths = []
    for symbol in symbols:
        start, end = symbol_window(symbol, membership, fixed_symbols)
        paths.append(symbol_window_path(directory, symbol, start, end))
    frames = [read_batch(path) for path in paths]
    bars = normalize_bars(pd.concat(frames, ignore_index=True))
    return label_native_intraday_bar_ends(bars, 15, "America/New_York")


def compact_result(label: str, result) -> dict[str, float | str]:
    directory = OUTPUT / label
    directory.mkdir(parents=True, exist_ok=True)
    result.equity.to_csv(directory / "equity.csv", index=False)
    result.trades.to_csv(directory / "trades.csv", index=False)
    entries = result.targets[result.targets["target_weight"].lt(0)].drop_duplicates(
        ["timestamp", "symbol"]
    )
    entries.to_csv(directory / "active-targets.csv", index=False)
    (directory / "metrics.json").write_text(
        json.dumps(result.metrics, indent=2, sort_keys=True), encoding="utf-8"
    )
    trades = result.trades.copy()
    if len(trades):
        trades["session"] = (
            pd.to_datetime(trades["timestamp"], utc=True)
            .dt.tz_convert("America/New_York")
            .dt.date
        )
        positions = trades.groupby(["session", "symbol"]).ngroups
        sessions = trades["session"].nunique()
        traded_symbols = trades["symbol"].nunique()
        position_summary = (
            trades.assign(
                cash_flow=np.where(
                    trades["side"].eq("sell"), trades["notional"], -trades["notional"]
                )
                - trades["fees"]
            )
            .groupby(["session", "symbol"], as_index=False)
            .agg(
                net_pnl=("cash_flow", "sum"),
                orders=("side", "size"),
                gross_notional=("notional", "sum"),
            )
        )
    else:
        positions = sessions = traded_symbols = 0
        position_summary = pd.DataFrame(
            columns=["session", "symbol", "net_pnl", "orders", "gross_notional"]
        )
    position_summary.to_csv(directory / "positions.csv", index=False)

    equity = result.equity.copy()
    equity["year"] = pd.to_datetime(equity["timestamp"], utc=True).dt.year
    year_end = equity.groupby("year")["equity"].last()
    prior = year_end.shift(1)
    if len(prior):
        prior.iloc[0] = float(equity["equity"].iloc[0])
    calendar = pd.DataFrame(
        {
            "year": year_end.index,
            "strategy_return": year_end.div(prior).sub(1).to_numpy(),
            "benchmark_return": equity.groupby("year")["benchmark_return"]
            .apply(lambda values: (1.0 + values).prod() - 1.0)
            .to_numpy(),
        }
    )
    calendar.to_csv(directory / "calendar-returns.csv", index=False)
    return {
        "label": label,
        **result.metrics,
        "positions": positions,
        "trade_sessions": sessions,
        "traded_symbols": traded_symbols,
    }


def continuous_feed_start(bars: pd.DataFrame, benchmark: str) -> pd.Timestamp:
    dates = pd.Series(
        pd.to_datetime(
            bars.loc[bars["symbol"].eq(benchmark), "timestamp"], utc=True
        )
        .dt.tz_convert("America/New_York")
        .dt.normalize()
        .unique()
    ).sort_values(ignore_index=True)
    if dates.empty:
        raise ValueError(f"No {benchmark} observations available.")
    large_gaps = dates.diff().dt.days.gt(10)
    if large_gaps.any():
        return pd.Timestamp(dates.loc[large_gaps].iloc[-1]).tz_convert("UTC")
    return pd.Timestamp(dates.iloc[0]).tz_convert("UTC")


def run_arm(
    label: str,
    bars: pd.DataFrame,
    tradables: list[str],
    membership: dict[str, list[list[str | None]]],
    config,
    slippage_bps: float | None = None,
) -> dict[str, float | str]:
    wanted = set(tradables) | {config.universe.benchmark}
    subset = bars[bars["symbol"].isin(wanted)].copy()
    parameters = {**config.strategy.parameters, "membership_periods": membership}
    strategy = create_strategy("intra_v12", parameters)
    backtest = (
        config.backtest
        if slippage_bps is None
        else replace(config.backtest, slippage_bps=slippage_bps)
    )
    result = run_intraday_backtest(
        subset,
        strategy,
        backtest,
        config.risk,
        config.universe.benchmark,
        tradables,
        strategy.context_symbols,
    )
    row = compact_result(label, result)
    print(
        f"{label}: return={row['total_return']:.4%}; "
        f"sharpe={row['sharpe']:.3f}; positions={row['positions']}",
        flush=True,
    )
    del result, strategy, subset
    gc.collect()
    return row


def write_position_diagnostics() -> None:
    label = "pit_liquid_100_sip"
    positions = pd.read_csv(OUTPUT / label / "positions.csv")
    positions["year"] = pd.to_datetime(positions["session"]).dt.year
    annual = (
        positions.groupby("year", as_index=False)
        .agg(
            positions=("symbol", "size"),
            net_pnl=("net_pnl", "sum"),
            win_rate=("net_pnl", lambda values: values.gt(0).mean()),
        )
    )
    annual.to_csv(OUTPUT / "pit-sip-position-results-by-year.csv", index=False)

    session_returns = (
        positions.groupby("session")["net_pnl"].sum().to_numpy()
        / 100_000.0
    )
    rng = np.random.default_rng(20260719)
    bootstrapped = rng.choice(
        session_returns,
        size=(100_000, len(session_returns)),
        replace=True,
    ).sum(axis=1)
    total_pnl = float(positions["net_pnl"].sum())
    symbol_pnl = positions.groupby("symbol")["net_pnl"].sum().sort_values(
        ascending=False
    )
    diagnostics = {
        "positions": int(len(positions)),
        "trade_sessions": int(positions["session"].nunique()),
        "win_rate": float(positions["net_pnl"].gt(0).mean()),
        "top_position_share_of_net_pnl": float(
            positions["net_pnl"].max() / total_pnl
        ),
        "top_3_position_share_of_net_pnl": float(
            positions.nlargest(3, "net_pnl")["net_pnl"].sum() / total_pnl
        ),
        "top_3_symbol_share_of_net_pnl": float(symbol_pnl.head(3).sum() / total_pnl),
        "return_without_riot_and_mara": float(
            positions.loc[
                ~positions["symbol"].isin(["RIOT", "MARA"]), "net_pnl"
            ].sum()
            / 100_000.0
        ),
        "session_bootstrap_samples": 100_000,
        "session_bootstrap_positive_frequency": float((bootstrapped > 0).mean()),
        "session_bootstrap_return_p025": float(np.quantile(bootstrapped, 0.025)),
        "session_bootstrap_return_p50": float(np.quantile(bootstrapped, 0.50)),
        "session_bootstrap_return_p975": float(np.quantile(bootstrapped, 0.975)),
    }
    (OUTPUT / "pit-sip-position-diagnostics.json").write_text(
        json.dumps(diagnostics, indent=2, sort_keys=True), encoding="utf-8"
    )


def run_validation() -> None:
    membership = load_membership()
    config = load_config(ROOT / "configs/research-intra-v12-broad.toml")
    symbols = json.loads((OUTPUT / "intraday-symbols.json").read_text(encoding="utf-8"))
    all_bars: dict[str, pd.DataFrame] = {}
    coverage = []
    for feed in ["iex", "sip"]:
        bars = assemble_feed(
            feed, symbols, membership, set(config.universe.symbols)
        )
        all_bars[feed] = bars
        feed_coverage = (
            bars.groupby("symbol")["timestamp"]
            .agg(first="min", last="max", observations="count")
            .reset_index()
        )
        feed_coverage.insert(0, "feed", feed)
        coverage.append(feed_coverage)
        print(f"assembled {feed}: {len(bars):,} bars", flush=True)
    pd.concat(coverage, ignore_index=True).to_csv(
        OUTPUT / "intraday-coverage.csv", index=False
    )

    starts = {
        feed: continuous_feed_start(bars, config.universe.benchmark)
        for feed, bars in all_bars.items()
    }
    ends = {
        feed: pd.to_datetime(
            bars.loc[
                bars["symbol"].eq(config.universe.benchmark), "timestamp"
            ],
            utc=True,
        ).max()
        for feed, bars in all_bars.items()
    }
    common_start = max(starts.values())
    common_end = min(ends.values())
    common_bars = {
        feed: bars[
            bars["timestamp"].between(common_start, common_end, inclusive="both")
        ].copy()
        for feed, bars in all_bars.items()
    }
    period = {
        "native_starts": {feed: str(value) for feed, value in starts.items()},
        "native_ends": {feed: str(value) for feed, value in ends.items()},
        "common_start": str(common_start),
        "common_end": str(common_end),
    }
    (OUTPUT / "comparison-period.json").write_text(
        json.dumps(period, indent=2, sort_keys=True), encoding="utf-8"
    )

    agreement_columns = ["timestamp", "symbol", "close", "volume"]
    common = common_bars["iex"][agreement_columns].merge(
        common_bars["sip"][agreement_columns],
        on=["timestamp", "symbol"],
        suffixes=("_iex", "_sip"),
    )
    close_mid = (common["close_iex"] + common["close_sip"]) / 2.0
    close_difference_bps = (
        (common["close_iex"] - common["close_sip"]).abs() / close_mid * 10_000
    )
    volume_ratio = common["volume_iex"].div(common["volume_sip"].replace(0, np.nan))
    agreement = {
        "common_bars": int(len(common)),
        "iex_only_bars": int(
            len(common_bars["iex"])
            - len(common[["timestamp", "symbol"]].drop_duplicates())
        ),
        "sip_only_bars": int(
            len(common_bars["sip"])
            - len(common[["timestamp", "symbol"]].drop_duplicates())
        ),
        "median_close_difference_bps": float(close_difference_bps.median()),
        "p95_close_difference_bps": float(close_difference_bps.quantile(0.95)),
        "p99_close_difference_bps": float(close_difference_bps.quantile(0.99)),
        "median_iex_to_sip_volume": float(volume_ratio.median()),
    }
    (OUTPUT / "feed-agreement.json").write_text(
        json.dumps(agreement, indent=2, sort_keys=True), encoding="utf-8"
    )
    del common
    gc.collect()

    rows = []
    pit_symbols = sorted(membership)
    for feed in ["iex", "sip"]:
        bars = common_bars[feed]
        for universe_name, tradables, periods in [
            ("fixed_2026_100", config.universe.symbols, {}),
            ("pit_liquid_100", pit_symbols, membership),
        ]:
            label = f"{universe_name}_{feed}"
            rows.append(run_arm(label, bars, tradables, periods, config))
    comparison = pd.DataFrame(rows)
    comparison.to_csv(OUTPUT / "four-way-comparison.csv", index=False)

    extended = run_arm(
        "pit_liquid_100_sip_extended",
        all_bars["sip"],
        pit_symbols,
        membership,
        config,
    )
    pd.DataFrame([extended]).to_csv(
        OUTPUT / "extended-sip-comparison.csv", index=False
    )

    cost_rows = [
        {
            **comparison.loc[
                comparison["label"].eq("pit_liquid_100_sip")
            ].iloc[0].to_dict(),
            "slippage_bps": config.backtest.slippage_bps,
        }
    ]
    for slippage_bps in [16.0, 24.0]:
        label = f"pit_liquid_100_sip_cost_{int(slippage_bps)}bps"
        row = run_arm(
            label,
            common_bars["sip"],
            pit_symbols,
            membership,
            config,
            slippage_bps=slippage_bps,
        )
        cost_rows.append({**row, "slippage_bps": slippage_bps})
    pd.DataFrame(cost_rows).to_csv(OUTPUT / "cost-stress.csv", index=False)

    signal_sets = {}
    for label in comparison["label"]:
        entries = pd.read_csv(OUTPUT / label / "active-targets.csv")
        signal_sets[label] = set(zip(entries["timestamp"], entries["symbol"]))
    overlap_rows = []
    for universe_name in ["fixed_2026_100", "pit_liquid_100"]:
        iex = signal_sets[f"{universe_name}_iex"]
        sip = signal_sets[f"{universe_name}_sip"]
        overlap_rows.append(
            {
                "universe": universe_name,
                "iex_active_bar_symbols": len(iex),
                "sip_active_bar_symbols": len(sip),
                "intersection": len(iex & sip),
                "jaccard": len(iex & sip) / len(iex | sip) if iex | sip else np.nan,
            }
        )
    pd.DataFrame(overlap_rows).to_csv(OUTPUT / "signal-overlap.csv", index=False)
    write_position_diagnostics()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage", choices=["universe", "data", "validate", "all"], default="all"
    )
    args = parser.parse_args()
    if args.stage in {"universe", "all"}:
        build_universe()
    if args.stage in {"data", "all"}:
        fetch_intraday()
    if args.stage in {"validate", "all"}:
        run_validation()


if __name__ == "__main__":
    main()
