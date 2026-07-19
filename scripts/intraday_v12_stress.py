from __future__ import annotations

import gc
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from project_geld.config import load_config
from project_geld.data import CsvBarSource
from project_geld.intraday import label_native_intraday_bar_ends, run_intraday_backtest
from project_geld.strategies.registry import create_strategy


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/cache/intraday-broad/bars_5a7c6e23e090de92.csv"
OUTPUT = ROOT / "artifacts/research-intra-v12-stress/results.csv"


def summarize(name: str, result) -> dict[str, float | str]:
    trades = result.trades.copy()
    if len(trades):
        local = pd.to_datetime(trades["timestamp"], utc=True).dt.tz_convert(
            "America/New_York"
        )
        trades["session"] = local.dt.date
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


def main() -> None:
    config = load_config(ROOT / "configs/research-intra-v12-broad.toml")
    source = CsvBarSource(DATA)
    start = datetime(2019, 1, 3, tzinfo=timezone.utc)
    end = datetime(2026, 7, 18, tzinfo=timezone.utc)
    bars = source.fetch(config.universe.data_symbols, start, end, "15Min")
    bars = label_native_intraday_bar_ends(
        bars, config.intraday.bar_minutes, config.backtest.session_timezone
    )
    base = dict(config.strategy.parameters)
    variants = [
        ("volume_cap_1_25", {"max_relative_volume": 1.25}),
        ("volume_cap_2_00", {"max_relative_volume": 2.00}),
        ("break_depth_0_10pct", {"min_confirmation_break": 0.0010}),
        ("break_depth_0_50pct", {"min_confirmation_break": 0.0050}),
        ("entry_delayed_15m", {"entry_delay_bars": 1}),
        ("exit_at_1500", {"flatten_at": "15:00"}),
    ]
    rows: list[dict[str, float | str]] = []
    baseline_metrics = json.loads(
        (ROOT / "artifacts/research-intra-v12-broad-2019-2026/metrics.json").read_text(
            encoding="utf-8"
        )
    )
    baseline_trades = pd.read_csv(
        ROOT / "artifacts/research-intra-v12-broad-2019-2026/trades.csv"
    )
    class BaselineResult:
        metrics = baseline_metrics
        trades = baseline_trades

    rows.append(summarize("baseline", BaselineResult()))
    for name, overrides in variants:
        strategy = create_strategy("intra_v12", {**base, **overrides})
        result = run_intraday_backtest(
            bars,
            strategy,
            config.backtest,
            config.risk,
            config.universe.benchmark,
            config.universe.symbols,
            strategy.context_symbols,
        )
        rows.append(summarize(name, result))
        print(pd.DataFrame([rows[-1]]).to_string(index=False), flush=True)
        del result, strategy
        gc.collect()

    original = load_config(ROOT / "configs/research-intra-v8.toml")
    original_symbols = list(
        dict.fromkeys([*original.universe.symbols, original.universe.benchmark])
    )
    original_bars = bars[bars["symbol"].isin(original_symbols)].copy()
    strategy = create_strategy("intra_v12", base)
    result = run_intraday_backtest(
        original_bars,
        strategy,
        config.backtest,
        config.risk,
        config.universe.benchmark,
        original.universe.symbols,
        strategy.context_symbols,
    )
    rows.append(summarize("original_21_universe", result))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUTPUT, index=False)
    print(pd.DataFrame(rows).to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
