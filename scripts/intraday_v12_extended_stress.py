from __future__ import annotations

import gc
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from project_geld.config import load_config
from project_geld.data import CsvBarSource
from project_geld.intraday import label_native_intraday_bar_ends, run_intraday_backtest
from project_geld.strategies.registry import create_strategy


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/cache/intraday-broad/bars_5a7c6e23e090de92.csv"
OUTPUT = ROOT / "artifacts/research-intra-v12-stress/extended_results.csv"


def summarize(name: str, result) -> dict[str, float | str]:
    trades = result.trades.copy()
    if len(trades):
        trades["session"] = (
            pd.to_datetime(trades["timestamp"], utc=True)
            .dt.tz_convert("America/New_York")
            .dt.date
        )
        positions = trades.groupby(["session", "symbol"]).ngroups
        sessions = trades["session"].nunique()
        symbols = trades["symbol"].nunique()
    else:
        positions = sessions = symbols = 0
    return {
        "variant": name,
        "total_return": result.metrics["total_return"],
        "sharpe": result.metrics["sharpe"],
        "max_drawdown": result.metrics["max_drawdown"],
        "annual_turnover": result.metrics["annual_turnover"],
        "orders": result.metrics["orders"],
        "positions": positions,
        "sessions": sessions,
        "symbols": symbols,
    }


def run_case(config, bars, name: str, overrides: dict, symbols: list[str]):
    strategy = create_strategy(
        "intra_v12", {**config.strategy.parameters, **overrides}
    )
    result = run_intraday_backtest(
        bars,
        strategy,
        config.backtest,
        config.risk,
        config.universe.benchmark,
        symbols,
        strategy.context_symbols,
    )
    row = summarize(name, result)
    print(pd.DataFrame([row]).to_string(index=False), flush=True)
    return row


def main() -> None:
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
    cases = [
        ("signal_1015", {"signal_time": "10:15"}),
        ("signal_1045", {"signal_time": "10:45"}),
        ("signal_1100", {"signal_time": "11:00"}),
        ("exit_1200", {"flatten_at": "12:00"}),
        ("exit_1330", {"flatten_at": "13:30"}),
    ]
    rows = []
    for name, overrides in cases:
        rows.append(
            run_case(config, bars, name, overrides, config.universe.symbols)
        )
        gc.collect()

    excluded = {"IREN", "AAOI", "RKLB"}
    reduced_symbols = [
        symbol for symbol in config.universe.symbols if symbol not in excluded
    ]
    reduced_bars = bars[
        bars["symbol"].isin([*reduced_symbols, config.universe.benchmark])
    ].copy()
    rows.append(
        run_case(
            config,
            reduced_bars,
            "exclude_top3_contributor_symbols",
            {},
            reduced_symbols,
        )
    )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUTPUT, index=False)


if __name__ == "__main__":
    main()
