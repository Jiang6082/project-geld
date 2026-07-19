from __future__ import annotations

import argparse
from dataclasses import replace
import gc
import json
from pathlib import Path
import runpy

import pandas as pd

from project_geld.config import load_config
from project_geld.intraday import run_intraday_backtest
from project_geld.strategies.registry import create_strategy


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/research-intra-v13-pit-sip"
V12_OUTPUT = ROOT / "artifacts/research-intra-v12-pit"
COMMON_START = pd.Timestamp("2020-07-27", tz="UTC")
COMMON_END = pd.Timestamp("2026-07-17 20:00:00", tz="UTC")


def load_research_inputs(
    feed: str, matched_period: bool = True
) -> tuple[pd.DataFrame, dict, object]:
    helpers = runpy.run_path(str(ROOT / "scripts/intraday_v12_pit_validation.py"))
    membership = helpers["load_membership"]()
    v12_config = load_config(ROOT / "configs/research-intra-v12-broad.toml")
    config = load_config(ROOT / "configs/research-intra-v13-pit-sip.toml")
    symbols = json.loads(
        (V12_OUTPUT / "intraday-symbols.json").read_text(encoding="utf-8")
    )
    bars = helpers["assemble_feed"](
        feed,
        symbols,
        membership,
        set(v12_config.universe.symbols),
    )
    if matched_period:
        bars = bars[
            bars["timestamp"].between(COMMON_START, COMMON_END, inclusive="both")
        ].copy()
    bars = bars[
        bars["symbol"].isin(set(membership) | {config.universe.benchmark})
    ].copy()
    return bars, membership, config


def position_frame(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(
            columns=["session", "symbol", "net_pnl", "orders", "gross_notional"]
        )
    frame = trades.copy()
    frame["session"] = (
        pd.to_datetime(frame["timestamp"], utc=True)
        .dt.tz_convert("America/New_York")
        .dt.date
    )
    frame["cash_flow"] = frame["notional"].where(
        frame["side"].eq("sell"), -frame["notional"]
    ) - frame["fees"]
    return (
        frame.groupby(["session", "symbol"], as_index=False)
        .agg(
            net_pnl=("cash_flow", "sum"),
            orders=("side", "size"),
            gross_notional=("notional", "sum"),
        )
    )


def run_variant(
    label: str,
    bars: pd.DataFrame,
    membership: dict,
    config,
    overrides: dict,
    feed: str = "sip",
    slippage_bps: float | None = None,
) -> dict:
    parameters = {
        **config.strategy.parameters,
        **overrides,
        "membership_periods": membership,
    }
    strategy = create_strategy("intra_v13", parameters)
    backtest = (
        config.backtest
        if slippage_bps is None
        else replace(config.backtest, slippage_bps=slippage_bps)
    )
    tradables = sorted(membership)
    result = run_intraday_backtest(
        bars,
        strategy,
        backtest,
        config.risk,
        config.universe.benchmark,
        tradables,
        strategy.context_symbols,
    )
    directory = OUTPUT / label
    directory.mkdir(parents=True, exist_ok=True)
    result.equity.to_csv(directory / "equity.csv", index=False)
    result.trades.to_csv(directory / "trades.csv", index=False)
    positions = position_frame(result.trades)
    positions.to_csv(directory / "positions.csv", index=False)
    (directory / "metrics.json").write_text(
        json.dumps(result.metrics, indent=2, sort_keys=True), encoding="utf-8"
    )
    row = {
        "label": label,
        "feed": feed,
        "slippage_bps": backtest.slippage_bps,
        **result.metrics,
        "positions": len(positions),
        "trade_sessions": positions["session"].nunique(),
        "traded_symbols": positions["symbol"].nunique(),
        "win_rate": positions["net_pnl"].gt(0).mean() if len(positions) else 0.0,
    }
    print(
        f"{label}: return={row['total_return']:.4%}; "
        f"sharpe={row['sharpe']:.3f}; maxdd={row['max_drawdown']:.3%}; "
        f"positions={row['positions']}",
        flush=True,
    )
    del result, strategy, positions
    gc.collect()
    return row


def run_ablations() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    bars, membership, config = load_research_inputs("sip")
    disabled = {
        "daily_volatility_sessions": 0,
        "max_annualized_daily_volatility": 0.0,
        "min_market_breadth": 0.0,
        "correlation_lookback_sessions": 0,
        "max_pairwise_correlation": 1.0,
    }
    variants = [
        ("v12_control", disabled),
        (
            "volatility_only",
            {
                **disabled,
                "daily_volatility_sessions": 20,
                "max_annualized_daily_volatility": 1.0,
            },
        ),
        ("breadth_only", {**disabled, "min_market_breadth": 0.50}),
        (
            "correlation_only",
            {
                **disabled,
                "correlation_lookback_sessions": 60,
                "max_pairwise_correlation": 0.75,
            },
        ),
        (
            "v13_balanced_draft",
            {
                "max_annualized_daily_volatility": 1.0,
                "min_market_breadth": 0.50,
                "max_pairwise_correlation": 0.75,
            },
        ),
        ("v13_final", {}),
    ]
    rows = [
        run_variant(label, bars, membership, config, overrides)
        for label, overrides in variants
    ]
    pd.DataFrame(rows).to_csv(OUTPUT / "ablation-results.csv", index=False)


def run_robustness() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    sip_bars, membership, config = load_research_inputs("sip")
    rows = []
    for slippage_bps in [16.0, 24.0]:
        rows.append(
            run_variant(
                f"v13_sip_cost_{int(slippage_bps)}bps",
                sip_bars,
                membership,
                config,
                {},
                slippage_bps=slippage_bps,
            )
        )
    rows.append(
        run_variant(
            "v13_balanced_neighborhood",
            sip_bars,
            membership,
            config,
            {
                "max_annualized_daily_volatility": 1.0,
                "min_market_breadth": 0.50,
                "max_pairwise_correlation": 0.75,
            },
        )
    )
    rows.append(
        run_variant(
            "v13_strict_neighborhood",
            sip_bars,
            membership,
            config,
            {
                "max_annualized_daily_volatility": 0.75,
                "min_market_breadth": 0.55,
                "max_pairwise_correlation": 0.65,
            },
        )
    )
    del sip_bars
    gc.collect()

    iex_bars, membership, config = load_research_inputs("iex")
    rows.append(
        run_variant(
            "v13_iex",
            iex_bars,
            membership,
            config,
            {},
            feed="iex",
        )
    )
    del iex_bars
    gc.collect()

    extended_bars, membership, config = load_research_inputs(
        "sip", matched_period=False
    )
    rows.append(
        run_variant(
            "v13_sip_extended",
            extended_bars,
            membership,
            config,
            {},
        )
    )
    pd.DataFrame(rows).to_csv(OUTPUT / "robustness-results.csv", index=False)


def run_final_candidate() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    relaxed = {
        "max_annualized_daily_volatility": 1.50,
        "min_market_breadth": 0.45,
        "max_pairwise_correlation": 0.85,
    }
    sip_bars, membership, config = load_research_inputs("sip")
    rows = []
    for slippage_bps in [16.0, 24.0]:
        rows.append(
            run_variant(
                f"v13_final_sip_cost_{int(slippage_bps)}bps",
                sip_bars,
                membership,
                config,
                relaxed,
                slippage_bps=slippage_bps,
            )
        )
    del sip_bars
    gc.collect()

    iex_bars, membership, config = load_research_inputs("iex")
    rows.append(
        run_variant(
            "v13_final_iex",
            iex_bars,
            membership,
            config,
            relaxed,
            feed="iex",
        )
    )
    del iex_bars
    gc.collect()

    extended_bars, membership, config = load_research_inputs(
        "sip", matched_period=False
    )
    rows.append(
        run_variant(
            "v13_final_sip_extended",
            extended_bars,
            membership,
            config,
            relaxed,
        )
    )
    pd.DataFrame(rows).to_csv(
        OUTPUT / "final-candidate-results.csv", index=False
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage",
        choices=["ablations", "robustness", "final", "all"],
        default="ablations",
    )
    args = parser.parse_args()
    if args.stage in {"ablations", "all"}:
        run_ablations()
    if args.stage in {"robustness", "all"}:
        run_robustness()
    if args.stage in {"final", "all"}:
        run_final_candidate()


if __name__ == "__main__":
    main()
