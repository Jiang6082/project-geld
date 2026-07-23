"""Daily pre-market review brief for the isolated paper accounts.

Advisory only: reads each account's logged performance and produces a one-page
morning brief with a drawdown circuit-breaker verdict (OK / CAUTION / HALT) plus
the Intra V15 implementation-shortfall + kill-switch reading. It reads local log
files only -- no live API calls, no orders, nothing that can touch a broker.

Run pre-market (a scheduled task can do this automatically); then, in a Claude
session, layer the market-posture / discipline skills on top (see DAILY_REVIEW.md).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

from project_geld.config import load_config
from project_geld.paper import shortfall_kill_switch_active, trailing_shortfall_bps

ROOT = Path(__file__).resolve().parents[1]

# Circuit-breaker thresholds (advisory). Daily limit comes from each config's
# risk.max_daily_loss_pct; these are the account-level drawdown guards.
CAUTION_DRAWDOWN = -0.05
HALT_DRAWDOWN = -0.10
STREAK_CAUTION = 3

ACCOUNTS = [
    ("Daily V4 (swing)", "configs/paper-daily-v4.toml", "artifacts/paper-daily-v4"),
    ("Intra V15 (intraday)", "configs/paper-intra-v15.toml", "artifacts/paper-intra-v15"),
]


def _verdict(perf: pd.DataFrame, daily_limit: float) -> tuple[str, list[str], dict]:
    perf = perf.sort_values("observed_at")
    equity = pd.to_numeric(perf["equity"], errors="coerce").dropna()
    if len(equity) < 1:
        return "NO DATA", ["no equity history logged yet"], {}
    daily_ret = float(pd.to_numeric(perf["daily_return"], errors="coerce").fillna(0.0).iloc[-1])
    cum_ret = float(pd.to_numeric(perf["cumulative_return"], errors="coerce").fillna(0.0).iloc[-1])
    drawdown = float(equity.iloc[-1] / equity.cummax().iloc[-1] - 1.0)
    returns = pd.to_numeric(perf["daily_return"], errors="coerce").fillna(0.0)
    streak = 0
    for r in reversed(returns.tolist()):
        if r < 0:
            streak += 1
        else:
            break
    reasons: list[str] = []
    verdict = "OK"
    if daily_ret <= -abs(daily_limit):
        verdict = "HALT"
        reasons.append(f"today's return {daily_ret:.2%} breached the {daily_limit:.2%} daily guard")
    if drawdown <= HALT_DRAWDOWN:
        verdict = "HALT"
        reasons.append(f"drawdown {drawdown:.2%} at/below halt limit {HALT_DRAWDOWN:.0%}")
    if verdict != "HALT":
        if drawdown <= CAUTION_DRAWDOWN:
            verdict = "CAUTION"
            reasons.append(f"drawdown {drawdown:.2%} below caution {CAUTION_DRAWDOWN:.0%}")
        if streak >= STREAK_CAUTION:
            verdict = "CAUTION"
            reasons.append(f"{streak} consecutive down sessions")
    if not reasons:
        reasons.append("within all drawdown/daily limits")
    stats = {
        "equity": float(equity.iloc[-1]),
        "daily_return": daily_ret,
        "cumulative_return": cum_ret,
        "drawdown": drawdown,
        "down_streak": streak,
    }
    return verdict, reasons, stats


def _shortfall_line(art_dir: Path, core: str) -> str:
    path = art_dir / "implementation_shortfall.csv"
    if not path.exists():
        return "shortfall: no fills logged yet"
    hist = pd.read_csv(path)
    avg = trailing_shortfall_bps(hist, core)
    killed = shortfall_kill_switch_active(hist, core)
    avg_txt = f"{avg:.2f} bps" if avg == avg else "n/a"
    return (
        f"base-sleeve trailing shortfall: {avg_txt} "
        f"({'KILL-SWITCH ACTIVE - base sleeve flat' if killed else 'within 2 bps gate'})"
    )


def _market_breadth(art_dir: Path) -> str | None:
    """Run the keyless market-breadth-analyzer skill (if installed) and return a
    one-line posture. Advisory context only; failures degrade gracefully."""
    script = (
        Path.home() / ".claude" / "skills" / "market-breadth-analyzer"
        / "scripts" / "market_breadth_analyzer.py"
    )
    if not script.exists():
        return None
    out = art_dir / "breadth"
    out.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [sys.executable, str(script), "--output-dir", str(out)],
            cwd=str(script.parent), capture_output=True, text=True, timeout=120,
        )
    except Exception:
        return None
    text = result.stdout

    def grab(pat: str) -> str | None:
        m = re.search(pat, text)
        return m.group(1).strip() if m else None

    score = grab(r"Composite Score:\s*([\d.]+)")
    if not score:
        return None
    zone = grab(r"Health Zone:\s*(.+)")
    exposure = grab(r"Equity Exposure:\s*(.+)")
    return (
        f"market breadth **{score}/100** ({zone or '?'}); "
        f"suggested equity exposure {exposure or '?'}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="artifacts/morning-review")
    args = parser.parse_args()
    today = pd.Timestamp.now(tz="America/New_York")
    lines = [f"# Pre-Market Review - {today:%Y-%m-%d %H:%M %Z}", ""]
    for label, cfg_path, art in ACCOUNTS:
        art_dir = ROOT / art
        lines.append(f"## {label}")
        perf_path = art_dir / "performance.csv"
        try:
            daily_limit = load_config(ROOT / cfg_path).risk.max_daily_loss_pct
        except Exception:
            daily_limit = 0.02
        if not perf_path.exists():
            lines += [f"- verdict: **NO DATA** (no `{perf_path.name}` yet)", ""]
            continue
        perf = pd.read_csv(perf_path)
        verdict, reasons, s = _verdict(perf, daily_limit)
        if s:
            lines.append(
                f"- equity ${s['equity']:,.0f} | day {s['daily_return']:+.2%} | "
                f"cum {s['cumulative_return']:+.2%} | drawdown {s['drawdown']:+.2%} | "
                f"down-streak {s['down_streak']}"
            )
        lines.append(f"- circuit-breaker verdict: **{verdict}** - " + "; ".join(reasons))
        if "intra" in art:
            core = getattr(
                __import__("project_geld.strategies.registry", fromlist=["create_strategy"])
                .create_strategy(load_config(ROOT / cfg_path).strategy.name,
                                 load_config(ROOT / cfg_path).strategy.parameters),
                "core_symbol", "SPY",
            )
            lines.append(f"- {_shortfall_line(art_dir, str(core))}")
        lines.append("")
    breadth = _market_breadth(ROOT / args.output)
    lines.append("## Market posture")
    lines.append(f"- {breadth}" if breadth else "- market breadth: unavailable")
    lines.append(
        "- macro regime: skipped (needs a paid FMP tier; free tier lacks the "
        "ETF histories the detector requires)."
    )
    lines.append("")
    lines += [
        "## Next (in a Claude session)",
        "- Breadth posture above is auto-included (keyless). `macro-regime-detector` / "
        "`exposure-coach` need a paid FMP tier (free tier lacks the ETF histories).",
        "- Run `pre-trade-discipline-gate` / `drawdown-circuit-breaker` before any "
        "manual action. All advisory.",
        "",
    ]
    brief = "\n".join(lines)
    out = ROOT / args.output
    out.mkdir(parents=True, exist_ok=True)
    (out / f"review-{today:%Y%m%d}.md").write_text(brief, encoding="utf-8")
    (out / "latest.md").write_text(brief, encoding="utf-8")
    print(brief)


if __name__ == "__main__":
    main()
