"""Selection-aware Sharpe statistics for candidate re-validation.

Implements the Probabilistic Sharpe Ratio (PSR) and the Deflated Sharpe Ratio
(DSR) of Bailey and Lopez de Prado ("The Deflated Sharpe Ratio: Correcting for
Selection Bias, Backtest Overfitting and Non-Normality", 2014).

PSR is the probability that the true Sharpe exceeds a benchmark Sharpe, given
the sample length and the skewness and kurtosis of the returns. DSR is PSR with
the benchmark set to the Sharpe that the best of ``n_trials`` skill-less
variants would be expected to reach by luck. Selecting the best of many variants
on the same data raises that bar; DSR states how much.

All Sharpe ratios here are per period (not annualized). Like every estimator in
this module, the inputs assume roughly independent observations; serial
correlation still overstates the evidence (see docs/AUDIT_2026-09-13.md).
"""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd

EULER_GAMMA = 0.5772156649015329
_NORMAL = NormalDist()


def sharpe_moments(returns: pd.Series | np.ndarray) -> dict[str, float]:
    """Per-period Sharpe, sample size, skewness and (non-excess) kurtosis."""
    values = np.asarray(pd.Series(returns, dtype=float).dropna(), dtype=float)
    n = int(values.size)
    nan = float("nan")
    if n < 3:
        return {"n": n, "sharpe": nan, "skew": nan, "kurtosis": nan}
    centered = values - values.mean()
    variance = float(np.mean(centered**2))
    if not variance > 0:
        return {"n": n, "sharpe": nan, "skew": nan, "kurtosis": nan}
    std = math.sqrt(variance)
    return {
        "n": n,
        "sharpe": float(values.mean() / std),
        "skew": float(np.mean(centered**3) / std**3),
        "kurtosis": float(np.mean(centered**4) / variance**2),
    }


def sharpe_standard_error(sharpe: float, n: int, skew: float, kurtosis: float) -> float:
    """Standard error of a per-period Sharpe estimate under non-normal returns."""
    if n < 2 or not all(math.isfinite(x) for x in (sharpe, skew, kurtosis)):
        return float("nan")
    radicand = 1.0 - skew * sharpe + (kurtosis - 1.0) / 4.0 * sharpe**2
    if radicand <= 0:
        return float("nan")
    return math.sqrt(radicand / (n - 1))


def probabilistic_sharpe_ratio(
    sharpe: float, n: int, skew: float, kurtosis: float, benchmark_sharpe: float = 0.0
) -> float:
    """P(true per-period Sharpe > ``benchmark_sharpe``)."""
    error = sharpe_standard_error(sharpe, n, skew, kurtosis)
    if not math.isfinite(error) or error <= 0 or not math.isfinite(benchmark_sharpe):
        return float("nan")
    return float(_NORMAL.cdf((sharpe - benchmark_sharpe) / error))


def expected_max_sharpe(n_trials: int, sharpe_variance: float) -> float:
    """Expected maximum per-period Sharpe among ``n_trials`` zero-skill variants."""
    if n_trials <= 1:
        return 0.0
    if not math.isfinite(sharpe_variance) or sharpe_variance < 0:
        return float("nan")
    z = (1.0 - EULER_GAMMA) * _NORMAL.inv_cdf(1.0 - 1.0 / n_trials) + EULER_GAMMA * _NORMAL.inv_cdf(
        1.0 - 1.0 / (n_trials * math.e)
    )
    return math.sqrt(sharpe_variance) * z


def deflated_sharpe_ratio(
    sharpe: float,
    n: int,
    skew: float,
    kurtosis: float,
    n_trials: int,
    sharpe_variance: float | None = None,
) -> dict[str, Any]:
    """DSR for a candidate selected from ``n_trials`` variants.

    ``sharpe_variance`` is the variance of the per-period Sharpe estimates across
    the trials. When it is unknown (a single candidate, or fewer than two
    finite trial Sharpes), the candidate's own sampling variance is used: the
    spread a skill-less strategy's Sharpe would show from noise alone.
    """
    error = sharpe_standard_error(sharpe, n, skew, kurtosis)
    variance_source = "trials"
    if sharpe_variance is None or not math.isfinite(sharpe_variance):
        sharpe_variance = error**2 if math.isfinite(error) else float("nan")
        variance_source = "sampling"
    threshold = expected_max_sharpe(max(int(n_trials), 1), sharpe_variance)
    return {
        "deflated_sharpe": probabilistic_sharpe_ratio(sharpe, n, skew, kurtosis, threshold),
        "expected_max_sharpe": threshold,
        "n_trials": max(int(n_trials), 1),
        "sharpe_variance": sharpe_variance,
        "variance_source": variance_source,
    }


def trial_sharpe_variance(sharpes: list[float]) -> float | None:
    """Sample variance of finite per-period trial Sharpes, or None if < 2."""
    finite = [value for value in sharpes if value is not None and math.isfinite(value)]
    if len(finite) < 2:
        return None
    return float(np.var(finite, ddof=1))


__all__ = [
    "deflated_sharpe_ratio",
    "expected_max_sharpe",
    "probabilistic_sharpe_ratio",
    "sharpe_moments",
    "sharpe_standard_error",
    "trial_sharpe_variance",
]
