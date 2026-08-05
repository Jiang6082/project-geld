"""Causal factor computation over Geld bars.

Mirrors ``emberforge.compute.engine``: evaluate a validated expression tree into
a timestamp-by-symbol score matrix, then apply an explicit, ordered
preprocessing pipeline (coverage mask -> winsorize -> normalize -> neutralize ->
execution lag). Raw factor calculation is kept separate from portfolio
construction on purpose.

The evaluator operates on a ``FieldPanels`` map (one time x symbol DataFrame per
raw field). :func:`panels_from_bars` builds that map from Geld's long-format
bars, deriving ``returns`` causally from ``close`` and passing ``vwap`` through
only when the bar source provides it. Nothing here can look forward.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import causality, operators
from .nodes import Call, Const, Field, Node, fields_used

# Raw fields Geld can materialise from OHLCV bars (a subset of RAW_FIELDS).
# ``returns`` is derived from close; ``vwap`` requires a vwap column in the bars.
_DERIVABLE = frozenset({"open", "high", "low", "close", "volume", "returns", "vwap"})


class FieldUnavailable(ValueError):
    """A raw field the expression needs cannot be built from the given bars."""


@dataclass(frozen=True)
class PreprocessConfig:
    """Mirror of Emberforge's PreprocessConfig defaults."""

    min_coverage: float = 0.5    # min fraction of non-nan symbols per timestamp
    winsorize_p: float | None = 0.02
    normalize: bool = True        # cross-sectional z-score
    neutralize: bool = False      # cross-sectional demean
    execution_lag: int = 0        # extra bars between signal and application


FieldPanels = dict[str, pd.DataFrame]


def panels_from_bars(bars: pd.DataFrame, needed: set[str] | None = None) -> FieldPanels:
    """Build a field -> (time x symbol) panel map from Geld long bars.

    ``bars`` has columns [timestamp, symbol, open, high, low, close, volume] and
    optionally ``vwap``. ``needed`` restricts which fields are materialised; when
    ``None`` all OHLCV-derivable fields are built. ``returns`` is a causal
    close-to-close percentage change.
    """
    fields = _DERIVABLE if needed is None else set(needed)
    unknown = fields - _DERIVABLE
    if unknown:
        raise FieldUnavailable(f"unsupported raw field(s): {sorted(unknown)}")

    def _pivot(column: str) -> pd.DataFrame:
        return bars.pivot(index="timestamp", columns="symbol", values=column).sort_index()

    close = _pivot("close")
    panels: FieldPanels = {}
    for name in ("open", "high", "low", "close", "volume"):
        if name in fields:
            if name not in bars.columns:
                raise FieldUnavailable(f"bars have no {name!r} column")
            panels[name] = _pivot(name)
    if "vwap" in fields:
        if "vwap" not in bars.columns:
            raise FieldUnavailable(
                "expression requires 'vwap' but the bar source has no vwap column"
            )
        panels["vwap"] = _pivot("vwap")
    if "returns" in fields:
        panels["returns"] = close.pct_change(fill_method=None)
    return panels


def evaluate(node: Node, panels: FieldPanels) -> pd.DataFrame:
    """Evaluate a raw expression tree into a score matrix (no preprocessing)."""
    if isinstance(node, Field):
        if node.name not in panels:
            raise FieldUnavailable(f"no panel provided for field {node.name!r}")
        return panels[node.name]
    if isinstance(node, Const):
        return node.value  # scalar; pandas broadcasts it in arithmetic ops
    assert isinstance(node, Call)
    spec = operators.get(node.op)
    evaluated = [evaluate(a, panels) for a in node.args]
    return spec.fn(*evaluated)


def compute_factor(
    node: Node,
    panels: FieldPanels,
    config: PreprocessConfig = PreprocessConfig(),
    eligibility: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Full causal computation: validate -> evaluate -> (universe mask) -> preprocess.

    ``eligibility`` is an optional point-in-time-safe boolean mask (time x symbol).
    Ineligible cells are removed *before* cross-sectional normalization, so
    ranks/z-scores are computed only over eligible names.
    """
    causality.validate(node)
    scores = evaluate(node, panels)
    if np.isscalar(scores):
        raise ValueError("factor reduced to a scalar; needs a field somewhere")

    # Establish a stable (index, columns) frame from the panels involved.
    ref = next(iter(panels.values()))
    scores = scores.reindex(index=ref.index, columns=ref.columns).astype(float)

    if eligibility is not None:
        elig = eligibility.reindex(index=ref.index, columns=ref.columns).fillna(False)
        scores = scores.where(elig.astype(bool))

    # coverage mask: drop rows without enough cross-sectional support.
    coverage = scores.notna().mean(axis=1)
    scores = scores.where(coverage >= config.min_coverage)

    if config.winsorize_p:
        lo = scores.quantile(config.winsorize_p, axis=1)
        hi = scores.quantile(1 - config.winsorize_p, axis=1)
        scores = scores.clip(lower=lo, upper=hi, axis=0)
    if config.normalize:
        mu = scores.mean(axis=1)
        sd = scores.std(axis=1).replace(0.0, np.nan)
        scores = scores.sub(mu, axis=0).div(sd, axis=0)
    if config.neutralize:
        scores = scores.sub(scores.mean(axis=1), axis=0)
    if config.execution_lag:
        scores = scores.shift(config.execution_lag)
    return scores


def required_fields(node: Node) -> set[str]:
    """Raw fields referenced by the expression tree."""
    return fields_used(node)


__all__ = [
    "FieldPanels", "FieldUnavailable", "PreprocessConfig",
    "panels_from_bars", "evaluate", "compute_factor", "required_fields",
]
