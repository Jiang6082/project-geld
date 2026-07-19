from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from project_geld.config import load_config
from project_geld.data import CsvBarSource
from project_geld.intraday import label_native_intraday_bar_ends, run_intraday_backtest
from project_geld.strategies.registry import create_strategy


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/cache/intraday-broad/bars_5a7c6e23e090de92.csv"
OUTPUT = ROOT / "artifacts/research-intra-v12-stress/universe_results.csv"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seasoned-only", action="store_true")
    args = parser.parse_args()
    config = load_config(ROOT / "configs/research-intra-v12-broad.toml")
    bars = CsvBarSource(DATA).fetch(
        config.universe.data_symbols,
        datetime(2019, 1, 3, tzinfo=timezone.utc),
        datetime(2026, 7, 18, tzinfo=timezone.utc),
        "15Min",
    )
    bars = label_native_intraday_bar_ends(
        bars, config.intraday.bar_minutes, config.backtest.session_timezone
    )
    if args.seasoned_only:
        first_bars = bars.groupby("symbol")["timestamp"].min()
        cutoff = pd.Timestamp("2020-08-01", tz="UTC")
        seasoned = [
            symbol
            for symbol in config.universe.symbols
            if symbol in first_bars and first_bars[symbol] <= cutoff
        ]
        subsets = {"history_at_common_2020_start": seasoned}
    else:
        subsets = {
            "liquidity_alternating_a": config.universe.symbols[::2],
            "liquidity_alternating_b": config.universe.symbols[1::2],
        }
    rows = []
    for name, symbols in subsets.items():
        subset_bars = bars[
            bars["symbol"].isin([*symbols, config.universe.benchmark])
        ].copy()
        strategy = create_strategy("intra_v12", config.strategy.parameters)
        result = run_intraday_backtest(
            subset_bars,
            strategy,
            config.backtest,
            config.risk,
            config.universe.benchmark,
            symbols,
            strategy.context_symbols,
        )
        trades = result.trades.copy()
        if len(trades):
            trades["session"] = (
                pd.to_datetime(trades["timestamp"], utc=True)
                .dt.tz_convert(config.backtest.session_timezone)
                .dt.date
            )
            positions = trades.groupby(["session", "symbol"]).ngroups
            sessions = trades["session"].nunique()
            traded_symbols = trades["symbol"].nunique()
        else:
            positions = sessions = traded_symbols = 0
        row = {
            "universe": name,
            "universe_size": len(symbols),
            "total_return": result.metrics["total_return"],
            "sharpe": result.metrics["sharpe"],
            "max_drawdown": result.metrics["max_drawdown"],
            "orders": result.metrics["orders"],
            "positions": positions,
            "sessions": sessions,
            "traded_symbols": traded_symbols,
        }
        rows.append(row)
        print(pd.DataFrame([row]).to_string(index=False), flush=True)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    output = OUTPUT.with_name(
        "seasoned_universe_results.csv" if args.seasoned_only else OUTPUT.name
    )
    pd.DataFrame(rows).to_csv(output, index=False)


if __name__ == "__main__":
    main()
