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
import math
from typing import Any

import pandas as pd

from project_geld.backtest import run_backtest
from project_geld.candidates.stats import (
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
    sharpe_moments,
    sharpe_standard_error,
)
from project_geld.config import BacktestConfig, RiskConfig
from project_geld.experiments import _period_metrics
from project_geld.models import BacktestResult
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
    max_annual_turnover: float = 50.0     # capacity proxy (holdout turnover)
    require_stressed_positive: bool = True  # still net-positive under cost stress
    stress_slippage_bps: float = 15.0     # slippage used for the stress pass
    min_test_alpha: float = 0.0           # annualized alpha vs benchmark on holdout
    max_abs_beta: float = 1.50            # reject closet-index / leveraged-beta exposure
    min_test_observations: int = 30       # refuse very short confirmation windows
    min_deflated_sharpe: float = 0.95     # P(true holdout Sharpe > best-of-N luck); applied when N > 1

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
    if not (math.isfinite(train) and math.isfinite(val) and train > 0 and val > 0 and train + val < 1):
        raise ValueError("train and val fractions must be positive and sum to less than 1")
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
        "holdout annual turnover (capacity proxy; warmup periods excluded)",
    ))
    test_alpha = float(test.get("annual_alpha", float("nan")))
    gates.append(Gate(
        "oos_alpha", test_alpha > policy.min_test_alpha,
        round(test_alpha, 6), policy.min_test_alpha,
        "holdout alpha net of benchmark — edge is not just market beta",
    ))
    test_beta = float(test.get("beta", float("nan")))
    gates.append(Gate(
        "beta_sanity", abs(test_beta) <= policy.max_abs_beta,
        round(test_beta, 4), policy.max_abs_beta,
        "holdout beta within bounds — not closet-index / leveraged-beta exposure",
    ))
    if policy.require_stressed_positive:
        stressed_return = test_stressed.get("total_return", float("nan")) if test_stressed else float("nan")
        gates.append(Gate(
            "cost_stress", stressed_return > 0.0,
            round(stressed_return, 6), 0.0,
            f"still net-positive on holdout at {policy.stress_slippage_bps} bps slippage",
        ))
    # Infinity can pass ordinary comparisons; absent/non-finite evidence never
    # passes a promotion gate. Serialize it as null, not non-standard JSON NaN.
    return [replace(g, passed=False, value=None) if g.value is None or not math.isfinite(g.value) else g
            for g in gates]


def _segment_returns(result: BacktestResult, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """Per-period returns inside a segment, measured from the prior valuation."""
    equity = result.equity
    inside = equity[equity["timestamp"].between(start, end, inclusive="both")]
    prior = equity[equity["timestamp"] < start].tail(1)
    values = pd.concat([prior, inside])["equity"].astype(float)
    return values.pct_change(fill_method=None).iloc[1:].dropna()


def discovery_window(bundle: dict[str, Any]) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """The bundle's declared discovery date range, or None when absent."""
    window = bundle.get("discovery_window")
    if not isinstance(window, dict):
        return None
    start = pd.Timestamp(window["start"])
    end = pd.Timestamp(window["end"])
    start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    end = end.tz_localize("UTC") if end.tzinfo is None else end.tz_convert("UTC")
    if _is_date_only(window["end"]):
        # A date-only end includes that whole day; daily bars are stamped after
        # midnight UTC, so midnight would let the last discovery day leak.
        end = end + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    return start, end


def _is_date_only(value: Any) -> bool:
    text = str(value).strip()
    return len(text) == 10 and text[4] == "-" and text[7] == "-"


def declared_trials(bundle: dict[str, Any]) -> int:
    """Variants the bundle says its discovery search evaluated (1 if undeclared)."""
    value = bundle.get("research_trials")
    return int(value) if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else 1


def deflated_sharpe_gate(
    test_summary: dict[str, Any],
    n_trials: int,
    policy: GatePolicy,
    sharpe_variance: float | None = None,
) -> tuple[Gate, dict[str, Any]]:
    """Gate the holdout Sharpe against the best-of-``n_trials`` luck threshold.

    The trial-Sharpe variance is floored at the candidate's own sampling
    variance: a handful of near-identical candidates in one batch would
    otherwise shrink the luck threshold toward zero.
    """
    sharpe = float(test_summary.get("sharpe_per_period", float("nan")))
    n = int(test_summary.get("n_returns") or 0)
    skew = float(test_summary.get("skew", float("nan")))
    kurtosis = float(test_summary.get("kurtosis", float("nan")))
    sampling = sharpe_standard_error(sharpe, n, skew, kurtosis) ** 2
    if sharpe_variance is not None and math.isfinite(sharpe_variance) and math.isfinite(sampling):
        sharpe_variance = max(sharpe_variance, sampling)
    result = deflated_sharpe_ratio(sharpe, n, skew, kurtosis, n_trials, sharpe_variance)
    value = result["deflated_sharpe"]
    gate = Gate(
        "deflated_sharpe",
        bool(math.isfinite(value) and value >= policy.min_deflated_sharpe),
        round(value, 6) if math.isfinite(value) else None,
        policy.min_deflated_sharpe,
        f"probability the holdout Sharpe beats the best of {result['n_trials']} "
        "skill-less variants (Bailey & Lopez de Prado deflated Sharpe)",
    )
    return gate, result


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
    n_trials: int = 1,
    discovery: tuple[pd.Timestamp, pd.Timestamp] | None = None,
    discovery_trials: int = 1,
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
    n_obs = {
        name: int(baseline.equity["timestamp"].between(start, end, inclusive="both").sum())
        for name, (start, end) in splits.items()
    }

    test_stressed_metrics = None
    if policy.require_stressed_positive:
        stressed_cfg = replace(
            backtest, slippage_bps=max(backtest.slippage_bps, policy.stress_slippage_bps),
            symbol_slippage_bps={symbol: max(bps, policy.stress_slippage_bps)
                                 for symbol, bps in backtest.symbol_slippage_bps.items()},
        )
        stressed = run_backtest(
            bars, strategy, stressed_cfg, risk, benchmark, tradable_symbols, context_symbols
        )
        test_start, test_end = splits["test"]
        test_stressed_metrics = _period_metrics(stressed, test_start, test_end)

    # Judge capacity on the holdout. Full-period turnover includes the warmup,
    # when the strategy holds nothing, which dilutes turnover and makes the
    # capacity gate easier to pass.
    annual_turnover = float(seg["test"].get("annual_turnover", float("nan")))
    gates = evaluate_gates(
        test=seg["test"], val=seg["val"], test_stressed=test_stressed_metrics,
        annual_turnover=annual_turnover, policy=policy,
    )
    gates.append(Gate("test_sample_size", n_obs["test"] >= policy.min_test_observations,
                      n_obs["test"], policy.min_test_observations,
                      "minimum number of held-out observations"))
    benchmark_rows = bars[bars["symbol"].astype(str).str.upper().eq(benchmark.upper())].copy()
    benchmark_rows["timestamp"] = pd.to_datetime(benchmark_rows["timestamp"], utc=True)
    benchmark_dates = pd.Index(benchmark_rows.loc[
        pd.to_numeric(benchmark_rows["close"], errors="coerce").map(
            lambda x: math.isfinite(x) and x > 0), "timestamp"].unique())
    benchmark_complete = dates.isin(benchmark_dates).all()
    gates.append(Gate("benchmark_data", bool(benchmark_complete), float(benchmark_complete), 1.0,
                      "observed benchmark prices required at every valuation; zero-filled missing benchmark is not alpha evidence"))
    test_start, test_end = splits["test"]
    moments = sharpe_moments(_segment_returns(baseline, test_start, test_end))
    test_summary = _segment_summary(splits["test"], seg["test"], n_obs["test"])
    test_summary.update(
        {
            "n_returns": moments["n"],
            "sharpe_per_period": moments["sharpe"],
            "skew": moments["skew"],
            "kurtosis": moments["kurtosis"],
            "probabilistic_sharpe": probabilistic_sharpe_ratio(
                moments["sharpe"], moments["n"], moments["skew"], moments["kurtosis"]
            ),
        }
    )
    # Trials that bear on the holdout Sharpe: every evaluation on this holdout
    # (n_trials), plus the discovery search unless the holdout provably starts
    # after the discovery data, in which case discovery selection cannot have
    # seen it.
    holdout_independent = discovery is not None and test_start > discovery[1]
    effective_trials = (
        max(int(n_trials), 1)
        if holdout_independent
        else max(int(n_trials), int(discovery_trials), 1)
    )
    deflation = None
    if effective_trials > 1:
        gate, deflation = deflated_sharpe_gate(test_summary, effective_trials, policy)
        gates.append(gate)

    warnings: list[str] = []
    if discovery is None:
        warnings.append(
            "bundle declares no discovery_window; independence of the holdout from "
            "the discovery search is unverified"
        )
    else:
        discovery_end = discovery[1]
        gates.append(Gate(
            "holdout_after_discovery", bool(test_start > discovery_end),
            float(test_start > discovery_end), 1.0,
            f"holdout starts {test_start.date()} after the discovery window ends "
            f"{discovery_end.date()}",
        ))
        val_start = splits["val"][0]
        if val_start <= discovery_end:
            warnings.append(
                "validation segment overlaps the discovery window; only the test "
                "segment is independent"
            )
    if effective_trials <= 1:
        warnings.append(
            "deflated Sharpe not gated: a single trial was declared; pass the number "
            "of evaluations on this holdout (--trials) or the discovery search size "
            "(bundle research_trials) to apply it"
        )
    verdict = "promote_to_shadow" if all(g.passed for g in gates) else "reject"

    return {
        "candidate_id": candidate_id or getattr(strategy, "candidate_id", getattr(strategy, "name", "candidate")),
        "verdict": verdict,
        "reasons": [g.name for g in gates if not g.passed] or ["all gates passed"],
        "gates": [g.to_dict() for g in gates],
        "segments": {
            "train": _segment_summary(splits["train"], seg["train"], n_obs["train"]),
            "val": _segment_summary(splits["val"], seg["val"], n_obs["val"]),
            "test": test_summary,
        },
        "deflated_sharpe": deflation,
        "n_trials": effective_trials,
        "holdout_independent_of_discovery": holdout_independent,
        "discovery_window": (
            None if discovery is None
            else {"start": discovery[0].isoformat(), "end": discovery[1].isoformat()}
        ),
        "warnings": warnings,
        "test_stressed": test_stressed_metrics,
        "annual_turnover": annual_turnover,
        "full_period_annual_turnover": float(baseline.metrics.get("annual_turnover", 0.0)),
        "full_metrics": baseline.metrics,
        "policy": policy.to_dict(),
        "notes": (
            "Independent OOS confirmation only; NO parameter search. Judged after "
            "costs on the held-out test segment with a cost-stress pass."
        ),
    }


def _segment_summary(window: tuple, metrics: dict[str, float], n_obs: int = 0) -> dict[str, Any]:
    start, end = window
    return {
        "start": str(start),
        "end": str(end),
        "n_obs": int(n_obs),
        "total_return": metrics.get("total_return"),
        "sharpe": metrics.get("sharpe"),
        "max_drawdown": metrics.get("max_drawdown"),
        "annual_alpha": metrics.get("annual_alpha"),
        "beta": metrics.get("beta"),
    }


__all__ = [
    "GatePolicy",
    "Gate",
    "declared_trials",
    "deflated_sharpe_gate",
    "discovery_window",
    "evaluate_gates",
    "revalidate_candidate",
]
