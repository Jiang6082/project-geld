"""Independent out-of-sample re-validation of an Emberforge candidate.

This is Geld's INDEPENDENT confirmation step. Emberforge already controlled for
false discovery (deflated Sharpe, FDR, PBO). Geld does NOT trust those numbers:
it re-runs the candidate strategy through its OWN backtester, on its OWN
point-in-time data, splits the timeline into train / validation / test, stresses
transaction costs, and applies fixed promotion gates to the TEST (holdout)
segment.

Crucially there is NO parameter search here. The strategy's parameters come
straight from the bundle; we run it once (plus one cost-stressed pass) and judge
the result. Tuning anything to the holdout would reintroduce exactly the
selection bias Emberforge exists to defeat, so we don't.

Verdict is machine-readable: ``promote_to_shadow`` (all gates pass) or
``reject`` (any gate fails), with every gate's value, threshold and pass/fail.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

import pandas as pd

from project_geld.backtest import run_backtest
from project_geld.config import BacktestConfig, RiskConfig
from project_geld.experiments import _period_metrics
from project_geld.strategies.base import Strategy


@dataclass(frozen=True)
class GatePolicy:
    """Fixed thresholds for OOS promotion. See docs/CANDIDATE_PROMOTION.md.

    Judged AFTER costs, on the TEST (holdout) segment, with a stress pass at
    higher slippage. Thresholds are deliberately modest — the point is to reject
    candidates that don't survive independent confirmation, not to re-select the
    best. Sign consistency across val+test guards against a lucky single split.
    """

    min_test_return: float = 0.0          # net-of-cost total return on holdout
    min_test_sharpe: float = 0.30         # risk-adjusted holdout performance
    min_stability_sharpe: float = 0.0     # min(val, test) Sharpe — sign consistency
    max_drawdown_limit: float = 0.35      # holdout max drawdown magnitude
    max_annual_turnover: float = 50.0     # capacity proxy (full-period turnover)
    require_stressed_positive: bool = True  # still net-positive under cost stress
    stress_slippage_bps: float = 15.0     # slippage used for the stress pass

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Gate:
    name: str
    passed: bool
    value: float | None
    threshold: float | None
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _split_dates(dates: pd.Index, train: float, val: float) -> dict[str, tuple]:
    n = len(dates)
    if n < 3:
        raise ValueError("need at least 3 sessions to split train/val/test.")
    i1 = min(max(int(n * train), 1), n - 2)
    i2 = min(max(int(n * (train + val)), i1 + 1), n - 1)
    return {
        "train": (dates[0], dates[i1 - 1]),
        "val": (dates[i1], dates[i2 - 1]),
        "test": (dates[i2], dates[-1]),
    }


def evaluate_gates(
    *,
    test: dict[str, float],
    val: dict[str, float],
    test_stressed: dict[str, float] | None,
    annual_turnover: float,
    policy: GatePolicy,
) -> list[Gate]:
    """Pure gate evaluation over already-computed segment metrics."""
    gates: list[Gate] = []
    gates.append(Gate(
        "net_return_positive", test["total_return"] > policy.min_test_return,
        round(test["total_return"], 6), policy.min_test_return,
        "net-of-cost total return on the holdout (test) segment",
    ))
    gates.append(Gate(
        "oos_sharpe", test["sharpe"] >= policy.min_test_sharpe,
        round(test["sharpe"], 4), policy.min_test_sharpe,
        "holdout Sharpe after costs",
    ))
    stability = min(val["sharpe"], test["sharpe"])
    gates.append(Gate(
        "stability", stability >= policy.min_stability_sharpe,
        round(stability, 4), policy.min_stability_sharpe,
        "min(validation, test) Sharpe — sign consistency across splits",
    ))
    gates.append(Gate(
        "max_drawdown", test["max_drawdown"] >= -policy.max_drawdown_limit,
        round(test["max_drawdown"], 4), -policy.max_drawdown_limit,
        "holdout max drawdown within the allowed magnitude",
    ))
    gates.append(Gate(
        "turnover_capacity", annual_turnover <= policy.max_annual_turnover,
        round(annual_turnover, 3), policy.max_annual_turnover,
        "full-period annual turnover (capacity proxy)",
    ))
    if policy.require_stressed_positive and test_stressed is not None:
        gates.append(Gate(
            "cost_stress", test_stressed["total_return"] > 0.0,
            round(test_stressed["total_return"], 6), 0.0,
            f"still net-positive on holdout at {policy.stress_slippage_bps} bps slippage",
        ))
    return gates


def revalidate_candidate(
    strategy: Strategy,
    bars: pd.DataFrame,
    *,
    backtest: BacktestConfig,
    risk: RiskConfig,
    benchmark: str = "SPY",
    tradable_symbols: list[str] | None = None,
    context_symbols: list[str] | None = None,
    policy: GatePolicy = GatePolicy(),
    train_fraction: float = 0.6,
    val_fraction: float = 0.2,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """Run the candidate through Geld's backtester OOS and return a verdict dict.

    No parameter search: the strategy is run exactly as configured, once at
    baseline cost and once at stressed cost.
    """
    dates = pd.Index(sorted(pd.to_datetime(bars["timestamp"], utc=True).unique()))
    splits = _split_dates(dates, train_fraction, val_fraction)

    baseline = run_backtest(
        bars, strategy, backtest, risk, benchmark, tradable_symbols, context_symbols
    )
    seg = {name: _period_metrics(baseline, start, end) for name, (start, end) in splits.items()}

    test_stressed_metrics = None
    if policy.require_stressed_positive:
        stressed_cfg = replace(backtest, slippage_bps=policy.stress_slippage_bps)
        stressed = run_backtest(
            bars, strategy, stressed_cfg, risk, benchmark, tradable_symbols, context_symbols
        )
        test_start, test_end = splits["test"]
        test_stressed_metrics = _period_metrics(stressed, test_start, test_end)

    annual_turnover = float(baseline.metrics.get("annual_turnover", 0.0))
    gates = evaluate_gates(
        test=seg["test"], val=seg["val"], test_stressed=test_stressed_metrics,
        annual_turnover=annual_turnover, policy=policy,
    )
    verdict = "promote_to_shadow" if all(g.passed for g in gates) else "reject"

    return {
        "candidate_id": candidate_id or getattr(strategy, "candidate_id", getattr(strategy, "name", "candidate")),
        "verdict": verdict,
        "reasons": [g.name for g in gates if not g.passed] or ["all gates passed"],
        "gates": [g.to_dict() for g in gates],
        "segments": {
            "train": _segment_summary(splits["train"], seg["train"]),
            "val": _segment_summary(splits["val"], seg["val"]),
            "test": _segment_summary(splits["test"], seg["test"]),
        },
        "test_stressed": test_stressed_metrics,
        "annual_turnover": annual_turnover,
        "full_metrics": baseline.metrics,
        "policy": policy.to_dict(),
        "notes": (
            "Independent OOS confirmation only; NO parameter search. Judged after "
            "costs on the held-out test segment with a cost-stress pass."
        ),
    }


def _segment_summary(window: tuple, metrics: dict[str, float]) -> dict[str, Any]:
    start, end = window
    return {
        "start": str(start),
        "end": str(end),
        "total_return": metrics.get("total_return"),
        "sharpe": metrics.get("sharpe"),
        "max_drawdown": metrics.get("max_drawdown"),
    }


__all__ = ["GatePolicy", "Gate", "evaluate_gates", "revalidate_candidate"]
