"""Offline shadow tracking for a promoted candidate (no orders, no capital).

The existing ``project_geld.shadow`` cycle is tailored to the intraday short book
(it only acts on negative target weights against live quotes). A promoted
candidate here is a long-only cross-sectional strategy, so its shadow is
different: run the candidate on the latest bars, take the target allocation it
WOULD hold now, mark a hypothetical portfolio to market, and append a snapshot to
a shadow ledger. Nothing is submitted; ``paper_enabled`` is never touched.

Feeding a fresh bar window each session builds a forward, paper-free track record
for a ``shadow``-state candidate before any human decides to enable paper.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from project_geld import provenance
from project_geld.backtest import run_backtest
from project_geld.config import BacktestConfig, RiskConfig
from project_geld.strategies.base import Strategy


def run_candidate_shadow(
    strategy: Strategy,
    bars: pd.DataFrame,
    *,
    backtest: BacktestConfig,
    risk: RiskConfig,
    benchmark: str = "SPY",
    tradable_symbols: list[str] | None = None,
    context_symbols: list[str] | None = None,
    ledger_path: str | Path | None = None,
    candidate_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compute the candidate's latest hypothetical allocation + a perf snapshot.

    Returns a snapshot dict and, when ``ledger_path`` is given, appends it as a
    JSONL row. No orders are ever produced.
    """
    result = run_backtest(
        bars, strategy, backtest, risk, benchmark, tradable_symbols, context_symbols
    )
    targets = result.targets
    last_ts = pd.Timestamp(targets["timestamp"].max())
    latest = targets[targets["timestamp"].eq(last_ts)]
    positions = {
        str(row.symbol): float(row.target_weight)
        for row in latest.itertuples()
        if float(row.target_weight) > 0
    }
    equity = result.equity
    observed = (now or datetime.now(timezone.utc)).isoformat()
    snapshot = {
        "observed_at": observed,
        "candidate_id": candidate_id or getattr(strategy, "candidate_id", getattr(strategy, "name", "candidate")),
        "as_of_bar": last_ts.isoformat(),
        "mode": "shadow",           # research-only; no orders, paper NOT enabled
        "n_positions": len(positions),
        "gross_weight": round(float(sum(positions.values())), 6),
        "equity": float(equity["equity"].iloc[-1]),
        "total_return": float(result.metrics.get("total_return", 0.0)),
        "sharpe": float(result.metrics.get("sharpe", 0.0)),
        "max_drawdown": float(result.metrics.get("max_drawdown", 0.0)),
        "positions": {s: round(w, 6) for s, w in sorted(positions.items())},
    }
    if ledger_path is not None:
        provenance.append_jsonl(ledger_path, snapshot)
    return snapshot


__all__ = ["run_candidate_shadow"]
