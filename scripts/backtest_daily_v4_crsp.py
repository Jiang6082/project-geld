"""Out-of-sample Daily V4 backtest on a CRSP daily export.

Usage (after exporting CRSP daily stock data to CSV via WRDS):

    .venv\\Scripts\\python.exe scripts\\backtest_daily_v4_crsp.py \\
        --crsp-csv path\\to\\crsp_daily.csv --benchmark SPY \\
        --start 1990-01-01 --end 2015-12-31

The export must include the benchmark ticker (SPY, or an S&P 500 proxy) and the
standard CRSP daily columns. This mirrors the live Daily V4.0.4 configuration
(75/25, regime-aware, benchmark-aware) so the result is a genuine independent,
survivorship-bias-free test rather than more tuning on the 2016-2026 sample.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from project_geld.backtest import run_backtest
from project_geld.config import BacktestConfig, RiskConfig
from project_geld.crsp import CrspIngestConfig, load_crsp_daily, monthly_pit_universe
from project_geld.research import period_metrics
from project_geld.strategies.registry import create_strategy


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crsp-csv", required=True)
    parser.add_argument("--benchmark", default="SPY")
    parser.add_argument("--symbol-from", choices=["ticker", "permno"], default="ticker")
    parser.add_argument("--top-n", type=int, default=500)
    parser.add_argument("--min-price", type=float, default=5.0)
    parser.add_argument("--min-dollar-volume", type=float, default=20_000_000.0)
    parser.add_argument("--start", default="1990-01-01")
    parser.add_argument("--end", default="2015-12-31")
    parser.add_argument("--slippage-bps", type=float, default=10.0)
    parser.add_argument("--output", default="artifacts/research-crsp-daily-v4")
    args = parser.parse_args()

    benchmark = args.benchmark.upper()
    raw = pd.read_csv(args.crsp_csv)
    bars = load_crsp_daily(raw, CrspIngestConfig(symbol_from=args.symbol_from))
    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end, tz="UTC")
    bars = bars[bars["timestamp"].between(start, end, inclusive="both")].copy()
    if benchmark not in set(bars["symbol"]):
        raise SystemExit(
            f"Benchmark {benchmark} not found in the CRSP export; include it or pass --benchmark."
        )

    membership = monthly_pit_universe(
        bars,
        top_n=args.top_n,
        minimum_price=args.min_price,
        minimum_dollar_volume=args.min_dollar_volume,
        benchmark=benchmark,
    )
    stocks = sorted(membership)
    if not stocks:
        raise SystemExit("No symbols entered the point-in-time universe; loosen the filters.")
    tradables = [*stocks, benchmark]

    backtest = BacktestConfig(
        slippage_bps=args.slippage_bps,
        rebalance_every=21,
        missing_price_haircut_pct=0.0,
    )
    risk = RiskConfig(max_gross_exposure=1.0, max_position_weight=0.75)
    # Mirror the live Daily V4.0.4 configuration.
    strategy = create_strategy(
        "daily_v4",
        {
            "core_symbol": benchmark,
            "core_weight": 0.75,
            "active_weight": 0.25,
            "active_name_cap": 0.03,
            "no_trade_band": 0.0025,
            "rebalance_every": 21,
            "regime_enabled": True,
            "active_parameters": {
                "membership_periods": membership,
                "max_symbols": 40,
                "exit_rank": 80,
                "max_pairwise_correlation": 0.85,
                "maximum_annualized_volatility": 0.60,
                "weighting_method": "benchmark_aware",
                "residual_factor_symbols": [],
            },
        },
    )
    result = run_backtest(
        bars, strategy, backtest, risk, benchmark, tradables,
        context_symbols=strategy.context_symbols,
    )

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    result.equity.to_csv(output / "equity.csv.gz", index=False)
    (output / "membership-periods.json").write_text(json.dumps(membership), encoding="utf-8")

    full = period_metrics(result, start, end)
    print(f"CRSP Daily V4 out-of-sample {args.start}..{args.end}")
    print(f"  symbols in PIT universe: {len(stocks)} | sessions: {len(result.equity)}")
    print(
        f"  total_return={full['total_return']:.3f} cagr={full['cagr']:.4f} "
        f"sharpe={full['sharpe']:.3f} max_dd={full['max_drawdown']:.3f} "
        f"turnover={full['annual_turnover']:.2f}"
    )
    # Decade slices for stability, including regimes Alpaca never saw (2000, 2008).
    for label, s, e in [
        ("pre-2000", args.start, "1999-12-31"),
        ("2000-2009", "2000-01-01", "2009-12-31"),
        ("2010-2015", "2010-01-01", "2015-12-31"),
    ]:
        m = period_metrics(result, s, e)
        if m["total_return"] != 0.0:
            print(
                f"  {label}: total={m['total_return']:.3f} sharpe={m['sharpe']:.3f} "
                f"max_dd={m['max_drawdown']:.3f}"
            )
    print(f"Artifacts: {output.resolve()}")


if __name__ == "__main__":
    main()
