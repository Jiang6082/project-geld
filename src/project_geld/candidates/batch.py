"""Batch OOS re-validation with multiple-testing control.

Per-candidate gates (``promotion.py``) are necessary but NOT sufficient: run
enough candidates and some pass the holdout Sharpe gate by chance. Emberforge
controls false discovery upstream, but Geld's independent confirmation must not
re-introduce the very selection bias it exists to defeat. So when several
candidates are re-validated together, Geld applies a family-wise correction
across the batch: each candidate's holdout Sharpe is turned into a one-sided
p-value, and a Benjamini-Hochberg FDR procedure decides which survive.

A candidate is promoted only if it BOTH passes its own gates AND survives the
batch-level FDR control. This is deliberately stricter than judging candidates in
isolation — that strictness is the point.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from project_geld.candidates.promotion import GatePolicy, revalidate_candidate
from project_geld.candidates.universe import bind_strategy
from project_geld.config import BacktestConfig, RiskConfig

TRADING_DAYS = 252


def sharpe_pvalue(sharpe_annual: float, n_obs: int, periods_per_year: int = TRADING_DAYS) -> float:
    """One-sided p-value that a strategy's true Sharpe is > 0.

    Uses the standard Sharpe t-statistic ``t = SR_per_period * sqrt(n)`` where
    ``SR_per_period = SR_annual / sqrt(periods_per_year)``. Fewer observations or
    a lower Sharpe ⇒ a larger (weaker) p-value. Returns 1.0 when there is not
    enough data to make any claim.
    """
    if n_obs is None or n_obs < 2 or not math.isfinite(sharpe_annual):
        return 1.0
    t = (sharpe_annual / math.sqrt(periods_per_year)) * math.sqrt(n_obs)
    # one-sided survival function of the standard normal.
    return 0.5 * math.erfc(t / math.sqrt(2.0))


def benjamini_hochberg(pvalues: list[float], q: float = 0.10) -> list[bool]:
    """Benjamini-Hochberg FDR. Returns a reject-null mask (True ⇒ significant).

    Controls the expected false-discovery rate at ``q`` across the batch.
    """
    m = len(pvalues)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvalues[i])
    threshold_rank = -1
    for rank, idx in enumerate(order, start=1):
        if pvalues[idx] <= q * rank / m:
            threshold_rank = rank
    reject = [False] * m
    if threshold_rank >= 0:
        for rank, idx in enumerate(order, start=1):
            if rank <= threshold_rank:
                reject[idx] = True
    return reject


def revalidate_batch(
    bundles: list[dict[str, Any]],
    bars: pd.DataFrame,
    *,
    backtest: BacktestConfig,
    risk: RiskConfig,
    benchmark: str = "SPY",
    policy: GatePolicy = GatePolicy(),
    fdr_q: float = 0.10,
    max_symbols: int | None = None,
) -> dict[str, Any]:
    """Re-validate several candidates together and apply FDR across the batch.

    Each bundle is bound to a concrete PIT universe and run through
    :func:`revalidate_candidate` (no parameter search). Candidates that cannot be
    bound or whose own gates fail are rejected up front; the survivors are then
    subjected to Benjamini-Hochberg FDR on their holdout-Sharpe p-values. Only
    candidates that pass gates AND survive FDR are promoted.
    """
    results: list[dict[str, Any]] = []
    for bundle in bundles:
        cid = bundle.get("candidate_id", "candidate")
        try:
            strategy, binding = bind_strategy(bundle, bars, benchmark=benchmark, max_symbols=max_symbols)
        except ValueError as exc:
            results.append({"candidate_id": cid, "gates_passed": False, "verdict": "reject",
                            "reason": f"universe binding failed: {exc}", "p_value": 1.0})
            continue
        verdict = revalidate_candidate(
            strategy, bars, backtest=backtest, risk=risk, benchmark=benchmark,
            tradable_symbols=binding.symbols, context_symbols=[benchmark], policy=policy,
            candidate_id=cid,
        )
        test = verdict["segments"]["test"]
        p = sharpe_pvalue(float(test.get("sharpe") or 0.0), int(test.get("n_obs") or 0))
        gates_passed = verdict["verdict"] == "promote_to_shadow"
        results.append({
            "candidate_id": cid,
            "gates_passed": gates_passed,
            "test_sharpe": test.get("sharpe"),
            "test_n_obs": test.get("n_obs"),
            "p_value": p,
            "reasons": verdict["reasons"],
            "verdict": verdict["verdict"],  # provisional; FDR applied below
        })

    # FDR only over candidates that cleared their own gates (a failed candidate
    # is already rejected; including its large p-value would only relax the
    # threshold for the others).
    eligible = [r for r in results if r["gates_passed"]]
    reject_null = benjamini_hochberg([r["p_value"] for r in eligible], q=fdr_q)
    survivors = {r["candidate_id"] for r, keep in zip(eligible, reject_null) if keep}

    promoted: list[str] = []
    for r in results:
        if r["candidate_id"] in survivors:
            r["verdict"] = "promote_to_shadow"
            r["fdr_survived"] = True
            promoted.append(r["candidate_id"])
        else:
            if r["gates_passed"] and r["candidate_id"] not in survivors:
                r["reasons"] = list(dict.fromkeys([*r.get("reasons", []), "fails_fdr"]))
            r["verdict"] = "reject"
            r["fdr_survived"] = False

    return {
        "n_candidates": len(results),
        "fdr_q": fdr_q,
        "n_gate_pass": len(eligible),
        "n_promoted": len(promoted),
        "promoted": promoted,
        "candidates": results,
        "notes": (
            "Batch OOS confirmation with Benjamini-Hochberg FDR across candidates. "
            "Promotion requires passing per-candidate gates AND surviving FDR. "
            "No parameter search."
        ),
    }


__all__ = ["sharpe_pvalue", "benjamini_hochberg", "revalidate_batch"]
