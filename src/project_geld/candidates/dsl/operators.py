"""Operator registry and causal implementations (port of Emberforge's DSL).

Every operator declares:

* ``kind``       — "ts" (time-series), "cs" (cross-sectional) or "arith".
* ``arity``      — number of expression arguments.
* ``window_args``— positional indexes of integer-window/lag literals, which must
  be strictly positive integers. A negative lag is the classic look-ahead bug,
  so it is rejected structurally.
* ``commutative``— used only for parity with Emberforge's canonicalisation.
* ``fn``         — a pure function over pandas objects.

The implementations here are kept byte-identical to
``emberforge.dsl.operators`` so Geld computes the SAME numbers as the upstream
research system; ``tests/test_candidate_evaluator.py`` cross-checks this against
Emberforge when it is importable. Data convention: a "panel" is a
``pandas.DataFrame`` indexed by timestamp (ascending) with one column per symbol.
Time-series operators act along the index; cross-sectional operators act within
each row (axis=1); arithmetic is elementwise. Nothing here can look forward.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

# Operator names that intentionally imply peeking into the future. They are
# never implemented; referencing one is a hard leakage rejection.
FORBIDDEN_FUTURE = frozenset(
    {"ts_lead", "lead", "future_return", "forward_return", "shift_neg"}
)


@dataclass(frozen=True)
class OpSpec:
    name: str
    kind: str  # "ts" | "cs" | "arith"
    arity: int
    fn: Callable
    window_args: tuple[int, ...] = ()
    commutative: bool = False


REGISTRY: dict[str, OpSpec] = {}


def _reg(spec: OpSpec) -> None:
    REGISTRY[spec.name] = spec


def _safe_divide(a, b):
    out = a / b
    return out.replace([np.inf, -np.inf], np.nan)


def _rolling_rank(df: pd.DataFrame, n: int) -> pd.DataFrame:
    # rank of the *last* value within each trailing window, in [0, 1].
    def _last_rank(x: np.ndarray) -> float:
        return (x <= x[-1]).sum() / len(x)

    return df.rolling(n, min_periods=n).apply(_last_rank, raw=True)


# --- time-series operators (trailing windows / non-negative shift) -----------
_reg(OpSpec("ts_delay", "ts", 2, lambda x, n: x.shift(int(n)), window_args=(1,)))
_reg(OpSpec("ts_delta", "ts", 2, lambda x, n: x - x.shift(int(n)), window_args=(1,)))
_reg(OpSpec("ts_returns", "ts", 2, lambda x, n: x / x.shift(int(n)) - 1.0, window_args=(1,)))
_reg(OpSpec("ts_mean", "ts", 2, lambda x, n: x.rolling(int(n), min_periods=int(n)).mean(), window_args=(1,)))
_reg(OpSpec("ts_std", "ts", 2, lambda x, n: x.rolling(int(n), min_periods=int(n)).std(), window_args=(1,)))
_reg(OpSpec("ts_min", "ts", 2, lambda x, n: x.rolling(int(n), min_periods=int(n)).min(), window_args=(1,)))
_reg(OpSpec("ts_max", "ts", 2, lambda x, n: x.rolling(int(n), min_periods=int(n)).max(), window_args=(1,)))
_reg(OpSpec("ts_rank", "ts", 2, lambda x, n: _rolling_rank(x, int(n)), window_args=(1,)))
_reg(OpSpec("ts_ewm", "ts", 2, lambda x, n: x.ewm(span=int(n), min_periods=int(n)).mean(), window_args=(1,)))
_reg(OpSpec("ts_corr", "ts", 3, lambda x, y, n: x.rolling(int(n), min_periods=int(n)).corr(y), window_args=(2,)))
_reg(OpSpec("ts_cov", "ts", 3, lambda x, y, n: x.rolling(int(n), min_periods=int(n)).cov(y), window_args=(2,)))
_reg(OpSpec(
    "ts_downside_std", "ts", 2,
    lambda x, n: x.where(x < 0, 0.0).rolling(int(n), min_periods=int(n)).std(),
    window_args=(1,),
))


# --- cross-sectional operators (act within each timestamp row) ---------------
def _cs_zscore(df: pd.DataFrame) -> pd.DataFrame:
    mu = df.mean(axis=1)
    sd = df.std(axis=1).replace(0.0, np.nan)
    return df.sub(mu, axis=0).div(sd, axis=0)


def _cs_winsor(df: pd.DataFrame, p: float) -> pd.DataFrame:
    lo = df.quantile(p, axis=1)
    hi = df.quantile(1 - p, axis=1)
    return df.clip(lower=lo, upper=hi, axis=0)


_reg(OpSpec("cs_rank", "cs", 1, lambda x: x.rank(axis=1)))
_reg(OpSpec("cs_percentile", "cs", 1, lambda x: x.rank(axis=1, pct=True)))
_reg(OpSpec("cs_zscore", "cs", 1, _cs_zscore))
_reg(OpSpec("cs_demean", "cs", 1, lambda x: x.sub(x.mean(axis=1), axis=0)))
_reg(OpSpec("cs_neutralize", "cs", 1, lambda x: x.sub(x.mean(axis=1), axis=0)))
_reg(OpSpec("cs_winsor", "cs", 2, _cs_winsor))


# --- arithmetic operators (elementwise) --------------------------------------
_reg(OpSpec("add", "arith", 2, lambda a, b: a + b, commutative=True))
_reg(OpSpec("subtract", "arith", 2, lambda a, b: a - b))
_reg(OpSpec("multiply", "arith", 2, lambda a, b: a * b, commutative=True))
_reg(OpSpec("divide", "arith", 2, _safe_divide))
_reg(OpSpec("signed_power", "arith", 2, lambda a, p: np.sign(a) * (a.abs() ** float(p))))
_reg(OpSpec("abs", "arith", 1, lambda a: a.abs()))
_reg(OpSpec("neg", "arith", 1, lambda a: -a))
_reg(OpSpec("min", "arith", 2, lambda a, b: np.minimum(a, b), commutative=True))
_reg(OpSpec("max", "arith", 2, lambda a, b: np.maximum(a, b), commutative=True))
_reg(OpSpec("clip", "arith", 3, lambda a, lo, hi: a.clip(lower=float(lo), upper=float(hi))))


def get(name: str) -> OpSpec:
    if name in FORBIDDEN_FUTURE:
        raise ValueError(f"operator {name!r} is forbidden (look-ahead / future data)")
    if name not in REGISTRY:
        raise ValueError(f"unknown operator {name!r}")
    return REGISTRY[name]
