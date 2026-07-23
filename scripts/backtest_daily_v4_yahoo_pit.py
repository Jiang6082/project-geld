"""Free out-of-sample Daily V4 backtest on point-in-time S&P 500 membership.

A no-WRDS alternative to scripts/backtest_daily_v4_crsp.py. It builds a monthly
point-in-time universe from a public historical-S&P-500-constituents dataset and
pulls adjusted daily prices from Yahoo, then runs the live Daily V4.0.4 config.

Requires internet. NOT survivorship-bias-free: Yahoo drops many delisted/acquired
tickers, so coverage is partial (typically ~60-65% of members) and the result is
somewhat optimistic since the missing names skew toward failures. It is a
robustness check, not proof; CRSP (backtest_daily_v4_crsp.py) remains the
rigorous path. The script reports coverage so the bias is visible.

Usage:
    .venv\\Scripts\\python.exe scripts\\backtest_daily_v4_yahoo_pit.py \\
        --start 2007-01-01 --end 2015-12-31
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from pathlib import Path

import pandas as pd

from project_geld.backtest import run_backtest
from project_geld.config import BacktestConfig, RiskConfig
from project_geld.data import normalize_bars
from project_geld.research import StaticAllocation, period_metrics
from project_geld.strategies.registry import create_strategy

CONSTITUENTS_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "S%26P%20500%20Historical%20Components%20%26%20Changes.csv"
)


def _base_ticker(raw: str) -> str:
    return re.sub(r"-\d{6}$", "", raw).replace(".", "-").upper().strip()


def _fetch_yahoo(symbol: str, p1: int, p2: int) -> list[dict]:
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?period1={p1}&period2={p2}&interval=1d"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=25) as response:
        payload = json.load(response)
    result = payload["chart"]["result"][0]
    stamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    adj = result["indicators"]["adjclose"][0]["adjclose"]
    rows = []
    for i, stamp in enumerate(stamps):
        o, h, l, c = quote["open"][i], quote["high"][i], quote["low"][i], quote["close"][i]
        v, a = quote["volume"][i], adj[i]
        if None in (o, h, l, c, a) or not c:
            continue
        ratio = a / c
        ts = (
            pd.Timestamp(stamp, unit="s", tz="UTC")
            .tz_convert("America/New_York")
            .normalize()
            .tz_convert("UTC")
        )
        rows.append({"timestamp": ts, "symbol": symbol, "open": o * ratio,
                     "high": h * ratio, "low": l * ratio, "close": a, "volume": v or 0})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2007-01-01")
    parser.add_argument("--end", default="2015-12-31")
    parser.add_argument("--constituents", help="Local constituents CSV (else download).")
    parser.add_argument("--output", default="artifacts/research-yahoo-pit-daily-v4")
    args = parser.parse_args()

    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    p1 = int(start.timestamp()) - 365 * 86_400  # ~1y warmup
    p2 = int(end.timestamp()) + 2 * 86_400

    if args.constituents:
        hist = pd.read_csv(args.constituents)
    else:
        hist = pd.read_csv(CONSTITUENTS_URL)
    hist["date"] = pd.to_datetime(hist["date"])
    months = pd.date_range(start.replace(day=1), end, freq="MS")
    month_members: dict[pd.Timestamp, set[str]] = {}
    for m in months:
        row = hist[hist["date"] <= m].tail(1)
        if row.empty:
            continue
        month_members[m] = {
            _base_ticker(t) for t in str(row.iloc[0]["tickers"]).split(",") if t
        }
    universe = sorted(set().union(*month_members.values()))
    print(f"PIT members union {args.start}..{args.end}: {len(universe)} tickers", flush=True)

    rows: list[dict] = []
    got: set[str] = set()
    for i, sym in enumerate(["SPY"] + universe, 1):
        try:
            data = _fetch_yahoo(sym, p1, p2)
            if data:
                rows += data
                got.add(sym)
        except Exception:
            pass
        if i % 50 == 0:
            print(f"  fetched {i}/{len(universe) + 1} ok={len(got)}", flush=True)
        time.sleep(0.15)
    coverage = len(got & set(universe))
    print(f"coverage: {coverage}/{len(universe)} members had Yahoo data", flush=True)
    bars = normalize_bars(pd.DataFrame(rows))

    membership: dict[str, list[list[str]]] = {}
    for m in months:
        ms = m.strftime("%Y-%m-%d")
        me = (m + pd.offsets.MonthEnd(0)).strftime("%Y-%m-%d")
        for t in (month_members.get(m, set()) & got) - {"SPY"}:
            membership.setdefault(t, []).append([ms, me])
    stocks = sorted(membership)
    tradables = [*stocks, "SPY"]
    avg_members = sum(len(month_members[m] & got) for m in months) / max(len(months), 1)
    print(f"avg investable members/month: {avg_members:.0f}", flush=True)

    backtest = BacktestConfig(slippage_bps=10.0, rebalance_every=21, missing_price_haircut_pct=0.0)
    risk = RiskConfig(max_gross_exposure=1.0, max_position_weight=0.75)
    strategy = create_strategy("daily_v4", {
        "core_symbol": "SPY", "core_weight": 0.75, "active_weight": 0.25,
        "active_name_cap": 0.03, "no_trade_band": 0.0025, "rebalance_every": 21,
        "regime_enabled": True,
        "active_parameters": {
            "membership_periods": membership, "max_symbols": 40, "exit_rank": 80,
            "max_pairwise_correlation": 0.85, "maximum_annualized_volatility": 0.60,
            "weighting_method": "benchmark_aware", "residual_factor_symbols": [],
        },
    })
    result = run_backtest(bars, strategy, backtest, risk, "SPY", tradables,
                          context_symbols=strategy.context_symbols)
    spy = run_backtest(
        bars[bars["symbol"] == "SPY"], StaticAllocation(gross_exposure=1.0),
        BacktestConfig(slippage_bps=10.0, rebalance_every=100_000),
        RiskConfig(max_position_weight=1.0), "SPY", ["SPY"],
    )
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    result.equity.to_csv(out / "equity.csv.gz", index=False)

    print(f"\ncoverage {coverage}/{len(universe)} (~{100*coverage//max(len(universe),1)}%) "
          f"-- partial; result is somewhat optimistic (see docstring)")
    mid = "2010-01-01"
    slices = [("full", args.start, args.end)]
    if pd.Timestamp(args.start) < pd.Timestamp(mid) < pd.Timestamp(args.end):
        slices += [(f"{start.year}-2009", args.start, "2009-12-31"),
                   (f"2010-{end.year}", mid, args.end)]
    print(f"{'period':14} {'V4_tot':>8} {'V4_shrp':>8} {'V4_dd':>7} | {'SPY_tot':>8} {'SPY_dd':>7}")
    for name, s, e in slices:
        m = period_metrics(result, s, e)
        b = period_metrics(spy, s, e)
        if m["total_return"] != 0.0:
            print(f"{name:14} {m['total_return']:8.3f} {m['sharpe']:8.3f} "
                  f"{m['max_drawdown']:7.3f} | {b['total_return']:8.3f} {b['max_drawdown']:7.3f}")
    print(f"Artifacts: {out.resolve()}")


if __name__ == "__main__":
    main()
