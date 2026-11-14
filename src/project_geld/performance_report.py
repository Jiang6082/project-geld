"""Recent-performance and health report for the paper accounts.

Run it where the paper runners have been writing their artifacts:

    geld performance-report                 # paper logs and health only (offline)
    geld performance-report --backtest      # plus recent backtest replays (Alpaca keys)

It writes ``report.md`` and ``report.json`` to ``artifacts/performance-report``.
Every section is independent: a missing log or a failed download is reported
in place rather than stopping the report.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import tomllib
from typing import Any, Callable

import numpy as np
import pandas as pd

from project_geld.logs import get_logger

log = get_logger(__name__)

TRADING_DAYS = 252


@dataclass(frozen=True)
class AccountSpec:
    key: str
    label: str
    config: Path
    output: Path
    intraday: bool


def default_accounts(root: Path = Path(".")) -> list[AccountSpec]:
    return [
        AccountSpec("daily_v4", "Daily V4", root / "configs/paper-daily-v4.toml",
                    root / "artifacts/paper-daily-v4", intraday=False),
        AccountSpec("intra_v15", "Intra V15", root / "configs/paper-intra-v15.toml",
                    root / "artifacts/paper-intra-v15", intraday=True),
    ]


# ---------------------------------------------------------------------------
# paper logs
# ---------------------------------------------------------------------------
def summarize_paper_log(path: Path, now: pd.Timestamp | None = None) -> dict[str, Any]:
    """Equity, return, drawdown and freshness from a performance.csv log."""
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if not path.exists():
        return {"available": False, "reason": f"{path} not found"}
    frame = pd.read_csv(path)
    if frame.empty or "equity" not in frame.columns:
        return {"available": False, "reason": f"{path} has no equity rows"}
    frame["observed_at"] = pd.to_datetime(frame["observed_at"], utc=True)
    frame = frame.sort_values("observed_at")
    daily = frame.groupby("session_date", sort=True).tail(1)
    equity = daily["equity"].astype(float).to_numpy()
    baseline = float(frame["baseline_equity"].iloc[0]) if "baseline_equity" in frame else equity[0]
    curve = np.r_[baseline, equity]
    returns = curve[1:] / curve[:-1] - 1
    peak = np.maximum.accumulate(curve)
    volatility = float(np.std(returns, ddof=0) * math.sqrt(TRADING_DAYS)) if len(returns) > 1 else float("nan")
    mean = float(np.mean(returns)) if len(returns) else float("nan")
    last_seen = frame["observed_at"].iloc[-1]
    return {
        "available": True,
        "first_session": str(daily["session_date"].iloc[0]),
        "last_session": str(daily["session_date"].iloc[-1]),
        "sessions_logged": int(len(daily)),
        "baseline_equity": baseline,
        "latest_equity": float(equity[-1]),
        "total_return": float(equity[-1] / baseline - 1) if baseline > 0 else float("nan"),
        "max_drawdown": float((curve / peak - 1).min()),
        "annual_volatility": volatility,
        "sharpe": float(mean / np.std(returns, ddof=0) * math.sqrt(TRADING_DAYS))
        if len(returns) > 1 and np.std(returns, ddof=0) > 0 else float("nan"),
        "last_observed_at": last_seen.isoformat(),
        "days_since_last_observation": float((now - last_seen).total_seconds() / 86_400),
    }


def benchmark_return(closes: pd.Series, first_session: str, last_session: str) -> float:
    """Close-to-close benchmark return from the session before ``first_session``."""
    if closes.empty:
        return float("nan")
    dates = pd.to_datetime(pd.Series(closes.index)).dt.strftime("%Y-%m-%d").to_numpy()
    series = pd.Series(closes.to_numpy(dtype=float), index=dates)
    before = series[series.index < first_session]
    start = float(before.iloc[-1]) if len(before) else float(series[series.index >= first_session].iloc[0])
    end_series = series[series.index <= last_session]
    if end_series.empty:
        return float("nan")
    return float(end_series.iloc[-1] / start - 1)


# ---------------------------------------------------------------------------
# operational health
# ---------------------------------------------------------------------------
def _universe_as_of(path: Path) -> str | None:
    if not path.is_file():
        return None
    frame = pd.read_csv(path)
    if "timestamp" not in frame.columns or frame.empty:
        return None
    return pd.to_datetime(frame["timestamp"], utc=True).max().isoformat()


def _source_universe(config_path: Path) -> Path | None:
    if not config_path.exists():
        return None
    raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    name = raw.get("universe", {}).get("symbols_file")
    if not name:
        return None
    for candidate in (Path(name), config_path.parent / name):
        if candidate.is_file():
            return candidate
    return config_path.parent / name


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, ValueError):
        return None


def _log_findings(output: Path, max_files: int = 10) -> dict[str, Any]:
    patterns = {
        "universe_stale": "days old; refresh it",
        "cycle_failed": "failed with exit code",
        "kill_switch": "Kill-switch latched",
        "fatal": "Fatal runner error",
    }
    counts = {key: 0 for key in patterns}
    latest: dict[str, str] = {}
    files = sorted(output.glob("runner-*.log"))[-max_files:]
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            for key, needle in patterns.items():
                if needle in line:
                    counts[key] += 1
                    latest[key] = line.strip()[:240]
    return {"log_files_scanned": len(files), "counts": counts, "latest": latest}


def account_health(spec: AccountSpec, max_age_days: int = 45,
                   now: pd.Timestamp | None = None) -> dict[str, Any]:
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    runtime_universe = spec.output / "universe.csv"
    source_universe = _source_universe(spec.config)
    in_use = runtime_universe if runtime_universe.is_file() else source_universe
    as_of = _universe_as_of(in_use) if in_use else None
    age = (now - pd.Timestamp(as_of)).days if as_of else None
    health: dict[str, Any] = {
        "universe_file": None if in_use is None else str(in_use),
        "universe_as_of": as_of,
        "universe_age_days": age,
        "planning_blocked_by_stale_universe": bool(age is not None and age > max_age_days),
        "heartbeat": _read_json(spec.output / "runner-heartbeat.json"),
        "logs": _log_findings(spec.output),
    }
    if not spec.intraday:
        state_file = None
        if spec.config.exists():
            raw = tomllib.loads(spec.config.read_text(encoding="utf-8"))
            state_file = raw.get("paper", {}).get("state_file")
        health["rebalance_state"] = _read_json(Path(state_file)) if state_file else None
    else:
        latch = _read_json(spec.output / "kill_switch_latch.json")
        reset = _read_json(spec.output / "kill_switch_reset.json")
        health["kill_switch_latched"] = bool(
            latch and (not reset or pd.Timestamp(reset["reset_at"]) <= pd.Timestamp(latch["latched_at"]))
        )
        health["shortfall"] = shortfall_summary(spec.output / "implementation_shortfall.csv")
    return health


def shortfall_summary(path: Path, symbol: str = "SPY", lookback: int = 20) -> dict[str, Any]:
    if not path.exists():
        return {"available": False}
    try:
        frame = pd.read_csv(path)
    except Exception as error:
        return {"available": False, "reason": f"unreadable: {error}"}
    if frame.empty:
        return {"available": False}
    missed = frame["missed"].astype(str).str.lower().eq("true") if "missed" in frame else pd.Series(False, index=frame.index)
    filled = frame[~missed]
    core = filled[filled["symbol"].astype(str).str.upper().eq(symbol)]
    recent = pd.to_numeric(core["shortfall_bps"], errors="coerce").dropna().tail(lookback)
    return {
        "available": True,
        "orders": int(len(frame)),
        "missed_orders": int(missed.sum()),
        "mean_fill_rate": float(pd.to_numeric(frame.get("fill_rate"), errors="coerce").mean()),
        "all_fills_mean_bps": float(pd.to_numeric(filled["shortfall_bps"], errors="coerce").mean()),
        f"{symbol.lower()}_trailing_mean_bps": float(recent.mean()) if len(recent) else float("nan"),
        f"{symbol.lower()}_fills_counted": int(len(recent)),
    }


# ---------------------------------------------------------------------------
# recent backtest replay
# ---------------------------------------------------------------------------
def _window_metrics(result: Any, benchmark_closes: pd.Series, start: pd.Timestamp) -> dict[str, Any]:
    from project_geld.experiments import _period_metrics

    end = pd.Timestamp(result.equity["timestamp"].max())
    metrics = _period_metrics(result, start, end)
    closes = benchmark_closes[benchmark_closes.index >= start - pd.Timedelta(days=7)]
    before = closes[closes.index < start]
    after = closes[closes.index >= start]
    bench = float(after.iloc[-1] / (before.iloc[-1] if len(before) else after.iloc[0]) - 1) if len(after) else float("nan")
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "total_return": metrics.get("total_return"),
        "sharpe": metrics.get("sharpe"),
        "max_drawdown": metrics.get("max_drawdown"),
        "orders": metrics.get("orders"),
        "benchmark_return": bench,
        "excess_return": (metrics.get("total_return") or 0.0) - bench if math.isfinite(bench) else float("nan"),
    }


def _windows(now: pd.Timestamp, since: pd.Timestamp | None) -> dict[str, pd.Timestamp]:
    windows = {
        "last_3_months": now - pd.DateOffset(months=3),
        "year_to_date": pd.Timestamp(year=now.year, month=1, day=1, tz="UTC"),
        "last_12_months": now - pd.DateOffset(months=12),
    }
    if since is not None:
        windows["since_paper_start"] = since
    return windows


def backtest_recent(
    spec: AccountSpec,
    source_factory: Callable[[Any], Any],
    *,
    now: pd.Timestamp | None = None,
    since: pd.Timestamp | None = None,
    intraday_days: int = 120,
) -> dict[str, Any]:
    """Replay the deployed configuration over recent data with its own costs.

    Daily V4 uses today's 500-stock universe for the whole replay, so it carries
    survivorship bias (names that dropped out are missing); treat it as an
    optimistic sanity check, not evidence.
    """
    from project_geld.backtest import run_backtest
    from project_geld.config import load_config
    from project_geld.data import completed_daily_bars
    from project_geld.intraday import label_native_intraday_bar_ends, run_intraday_backtest
    from project_geld.strategies.registry import create_strategy

    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    runtime = spec.output / "runtime-config.toml"
    config_path = runtime if runtime.exists() else spec.config
    config = load_config(config_path)
    strategy = create_strategy(config.strategy.name, config.strategy.parameters)
    context = list(dict.fromkeys(getattr(strategy, "context_symbols", [])))
    core = getattr(strategy, "core_symbol", None)
    managed = list(dict.fromkeys([*config.universe.symbols, *([str(core).upper()] if core else [])]))
    symbols = list(dict.fromkeys([*config.universe.data_symbols, *context]))
    source = source_factory(config)
    windows = _windows(now, since)
    # Free Alpaca plans restrict the most recent 15 minutes of SIP data.
    data_end = now - pd.Timedelta(minutes=20)
    if spec.intraday:
        start = now - pd.Timedelta(days=intraday_days + 120)
        raw = source.fetch(symbols, start.to_pydatetime(), data_end.to_pydatetime(),
                           f"{config.intraday.bar_minutes}Min")
        bars = label_native_intraday_bar_ends(raw, config.intraday.bar_minutes, config.backtest.session_timezone)
        bars = bars[bars["timestamp"] <= data_end]  # drop a still-forming bar
        result = run_intraday_backtest(bars, strategy, config.backtest, config.risk,
                                       config.universe.benchmark, managed, context_symbols=context)
        windows = {key: value for key, value in windows.items() if value >= now - pd.Timedelta(days=intraday_days)}
        windows[f"last_{intraday_days}_days"] = now - pd.Timedelta(days=intraday_days)
    else:
        start = min(windows.values()) - pd.Timedelta(days=430)
        bars = completed_daily_bars(
            source.fetch(symbols, start.to_pydatetime(), data_end.to_pydatetime(), "1Day"),
            as_of=now,
            timezone_name=config.backtest.session_timezone,
        )
        result = run_backtest(bars, strategy, config.backtest, config.risk,
                              config.universe.benchmark, managed, context_symbols=context)
    benchmark = config.universe.benchmark.upper()
    closes = bars[bars["symbol"].eq(benchmark)].set_index("timestamp")["close"].sort_index()
    return {
        "config": str(config_path),
        "strategy_version": getattr(strategy, "version", strategy.name),
        "universe_symbols": len(config.universe.symbols),
        "slippage_bps": config.backtest.slippage_bps,
        "windows": {name: _window_metrics(result, closes, start_at) for name, start_at in windows.items()},
        "caveats": (
            "Intraday replay on IEX native bars; paper fills differ."
            if spec.intraday else
            "Today's universe applied to the past: survivorship-biased and optimistic."
        ),
    }


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def _pct(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    return "n/a" if not math.isfinite(number) else f"{number:+.2%}"


def _num(value: Any, digits: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    return "n/a" if not math.isfinite(number) else f"{number:.{digits}f}"


def render_markdown(report: dict[str, Any]) -> str:
    lines = [f"# Paper performance report — {report['generated_at'][:10]}", ""]
    for key, account in report["accounts"].items():
        lines += [f"## {account['label']}", ""]
        paper = account.get("paper", {})
        if paper.get("available"):
            lines += [
                f"- Paper log: {paper['first_session']} to {paper['last_session']} "
                f"({paper['sessions_logged']} sessions), last update "
                f"{_num(paper['days_since_last_observation'], 1)} days ago",
                f"- Equity {paper['baseline_equity']:,.0f} -> {paper['latest_equity']:,.0f}: "
                f"return {_pct(paper['total_return'])}, max drawdown {_pct(paper['max_drawdown'])}, "
                f"Sharpe {_num(paper['sharpe'])}",
            ]
            if "benchmark_return" in paper:
                lines.append(
                    f"- SPY over the same sessions: {_pct(paper['benchmark_return'])} "
                    f"(excess {_pct(paper['excess_return'])})"
                )
        else:
            lines.append(f"- Paper log unavailable: {paper.get('reason', 'unknown')}")
        health = account.get("health", {})
        if health:
            age = health.get("universe_age_days")
            status = "BLOCKED (stale)" if health.get("planning_blocked_by_stale_universe") else "ok"
            lines.append(f"- Universe: as of {health.get('universe_as_of') or 'unknown'} "
                         f"({age if age is not None else '?'} days) — {status}")
            beat = health.get("heartbeat") or {}
            if beat:
                lines.append(f"- Runner heartbeat: {beat.get('phase')} at {beat.get('updated_at')}")
            counts = health.get("logs", {}).get("counts", {})
            if any(counts.values()):
                lines.append("- Runner log findings: " + ", ".join(f"{k}={v}" for k, v in counts.items() if v))
            if "kill_switch_latched" in health:
                lines.append(f"- Kill-switch latched: {health['kill_switch_latched']}")
            shortfall = health.get("shortfall") or {}
            if shortfall.get("available"):
                lines.append(
                    f"- Shortfall: {shortfall['orders']} orders, {shortfall['missed_orders']} missed, "
                    f"SPY trailing {_num(shortfall.get('spy_trailing_mean_bps'))} bps "
                    f"(gate 2 bps), fill rate {_num(shortfall.get('mean_fill_rate'))}"
                )
        replay = account.get("backtest")
        if replay:
            if "error" in replay:
                lines.append(f"- Backtest replay failed: {replay['error']}")
            else:
                lines += ["", f"Backtest replay ({replay['strategy_version']}, {replay['slippage_bps']} bps):", "",
                          "| Window | Return | SPY | Excess | Sharpe | Max DD | Orders |",
                          "|---|---:|---:|---:|---:|---:|---:|"]
                for name, row in replay["windows"].items():
                    lines.append(
                        f"| {name.replace('_', ' ')} | {_pct(row['total_return'])} | {_pct(row['benchmark_return'])} | "
                        f"{_pct(row['excess_return'])} | {_num(row['sharpe'])} | {_pct(row['max_drawdown'])} | "
                        f"{_num(row['orders'], 0)} |"
                    )
                lines += ["", f"_{replay['caveats']}_"]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_report(
    accounts: list[AccountSpec],
    *,
    backtest: bool = False,
    source_factory: Callable[[Any], Any] | None = None,
    benchmark_closes: Callable[[str, str], pd.Series] | None = None,
    intraday_days: int = 120,
    now: pd.Timestamp | None = None,
) -> dict[str, Any]:
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    report: dict[str, Any] = {"generated_at": now.isoformat(), "accounts": {}}
    for spec in accounts:
        entry: dict[str, Any] = {"label": spec.label}
        try:
            entry["paper"] = summarize_paper_log(spec.output / "performance.csv", now)
        except Exception as error:
            entry["paper"] = {"available": False, "reason": f"{type(error).__name__}: {error}"}
        paper = entry["paper"]
        if paper.get("available") and benchmark_closes is not None:
            try:
                closes = benchmark_closes(paper["first_session"], paper["last_session"])
                paper["benchmark_return"] = benchmark_return(closes, paper["first_session"], paper["last_session"])
                paper["excess_return"] = paper["total_return"] - paper["benchmark_return"]
            except Exception as error:
                paper["benchmark_error"] = f"{type(error).__name__}: {error}"
        try:
            entry["health"] = account_health(spec, now=now)
        except Exception as error:
            entry["health"] = {"error": f"{type(error).__name__}: {error}"}
        if backtest and source_factory is not None:
            try:
                since = None
                if entry["paper"].get("available"):
                    since = pd.Timestamp(entry["paper"]["first_session"], tz="UTC")
                entry["backtest"] = backtest_recent(spec, source_factory, now=now, since=since,
                                                    intraday_days=intraday_days)
            except Exception as error:
                log.warning("%s replay failed: %s", spec.label, error)
                entry["backtest"] = {"error": f"{type(error).__name__}: {error}"}
        report["accounts"][spec.key] = entry
    return report


def alpaca_sources(feed_override: str | None = None) -> tuple[Callable, Callable]:
    """Bar source factory and SPY close lookup backed by Alpaca (needs keys)."""
    from project_geld.data import AlpacaBarSource

    def factory(config):
        return AlpacaBarSource(feed_override or config.data.feed, config.data.adjustment,
                               config.account.credential_profile)

    def spy_closes(first: str, last: str) -> pd.Series:
        source = None
        for profile in ("SWING", "INTRADAY", ""):
            try:
                source = AlpacaBarSource("sip", "all", profile)
                break
            except RuntimeError:
                continue
        if source is None:
            raise RuntimeError("No Alpaca credentials found for the SPY benchmark.")
        start = datetime.fromisoformat(first).replace(tzinfo=timezone.utc) - timedelta(days=10)
        end = datetime.now(timezone.utc) - timedelta(minutes=20)
        bars = source.fetch(["SPY"], start, end)
        series = bars.set_index("timestamp")["close"].sort_index()
        series.index = series.index.tz_convert("America/New_York").date
        return series

    return factory, spy_closes


def write_report(report: dict[str, Any], output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    markdown = output / "report.md"
    markdown.write_text(render_markdown(report), encoding="utf-8")
    return markdown
