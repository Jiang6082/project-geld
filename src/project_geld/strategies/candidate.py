"""Turn a validated Emberforge candidate bundle into a tradeable Geld strategy.

This closes the gap the importer leaves open: the importer only VALIDATES and
QUARANTINES a bundle; :class:`CandidateStrategy` actually evaluates the bundle's
declarative ``signal_spec.expression`` into a cross-sectional score and then into
target weights, honouring the bundle's ``frequency``/``lookback``,
``preprocessing`` (winsorize + cross-sectional z-score) and
``portfolio_construction``.

Safety guarantees:
  * The expression is parsed into a typed AST and evaluated with a whitelisted,
    CAUSAL operator set over pandas — never ``eval``/``exec`` on bundle strings.
  * Geld does not import Emberforge; the DSL is replicated under
    ``project_geld.candidates.dsl`` and pinned by a cross-check test.
  * The strategy emits target weights only (paper-only); it never places orders.
  * Default construction is LONG-ONLY because Geld's paper account cannot short;
    the bottom-quantile short leg is available behind ``long_short=True`` and is
    documented as not exercised by the paper harness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from project_geld.candidates.dsl import (
    Node,
    PreprocessConfig,
    compute_factor,
    panels_from_bars,
    parse,
    required_fields,
    validate,
)
from project_geld.strategies.base import TARGET_COLUMNS


def _preprocess_from_bundle(preprocessing: Any) -> PreprocessConfig:
    """Map a bundle ``preprocessing`` block to a :class:`PreprocessConfig`.

    Accepts Emberforge's settings-object form ``{"winsorize": bool,
    "cross_sectional_zscore": bool}`` or an ordered list of step objects with a
    ``"kind"``/``"op"`` name. Unknown shapes fall back to the causal defaults
    (winsorize + z-score), matching the upstream compute engine.
    """
    winsorize = True
    zscore = True
    if isinstance(preprocessing, dict):
        winsorize = bool(preprocessing.get("winsorize", True))
        zscore = bool(preprocessing.get("cross_sectional_zscore", True))
    elif isinstance(preprocessing, list):
        names = {
            str(step.get("kind") or step.get("op") or "").lower()
            for step in preprocessing
            if isinstance(step, dict)
        }
        if names:
            winsorize = any("winsor" in n for n in names)
            zscore = any("zscore" in n or "znorm" in n for n in names)
    return PreprocessConfig(
        winsorize_p=0.02 if winsorize else None,
        normalize=zscore,
    )


@dataclass(frozen=True)
class CandidateStrategy:
    """A :class:`~project_geld.strategies.base.Strategy` built from a bundle.

    Prefer :meth:`from_bundle` / :func:`strategy_from_quarantine` over the raw
    constructor so the expression and preprocessing come straight from a
    validated bundle.
    """

    expression: str
    lookback: int = 1
    expected_sign: int = 0
    quantiles: int = 5
    long_quantile: str = "top"
    gross_exposure: float = 1.0
    max_position_weight: float = 1.0
    long_short: bool = False
    preprocessing: Any = None
    candidate_id: str = "candidate"
    name: str = "candidate"

    def __post_init__(self) -> None:
        if self.lookback < 1:
            raise ValueError("lookback must be a positive integer.")
        if self.quantiles < 1:
            raise ValueError("quantiles must be at least 1.")
        if self.long_quantile not in {"top", "bottom"}:
            raise ValueError("long_quantile must be 'top' or 'bottom'.")
        if not 0 < self.gross_exposure <= 1:
            raise ValueError("gross_exposure must be in (0, 1].")
        if not 0 < self.max_position_weight <= 1:
            raise ValueError("max_position_weight must be in (0, 1].")
        # Parse + validate now so an unusable candidate fails fast (never eval()).
        node = parse(self.expression)
        validate(node)
        object.__setattr__(self, "_node", node)
        object.__setattr__(self, "_preprocess", _preprocess_from_bundle(self.preprocessing))

    # -- construction from a bundle ------------------------------------------
    @classmethod
    def from_bundle(cls, bundle: dict[str, Any], **overrides: Any) -> "CandidateStrategy":
        signal = bundle.get("signal_spec") or {}
        if signal.get("kind") != "expression" or not isinstance(signal.get("expression"), str):
            raise ValueError("bundle signal_spec must be kind='expression' with a string expression.")
        params = signal.get("parameters") or {}
        pc = bundle.get("portfolio_construction") or {}
        kwargs: dict[str, Any] = {
            "expression": signal["expression"],
            "lookback": max(1, int(bundle.get("lookback", 1) or 1)),
            "expected_sign": int(params.get("expected_sign", 0) or 0),
            "quantiles": int(pc.get("quantiles", 5) or 5),
            "long_quantile": str(pc.get("long_quantile", "top") or "top"),
            "gross_exposure": min(1.0, float(pc.get("gross_exposure", 1.0) or 1.0)),
            "preprocessing": bundle.get("preprocessing"),
            "candidate_id": str(bundle.get("candidate_id") or "candidate"),
            "name": f"candidate:{bundle.get('candidate_id') or 'candidate'}",
        }
        kwargs.update(overrides)
        return cls(**kwargs)

    @property
    def node(self) -> Node:
        return self._node  # type: ignore[attr-defined]

    @property
    def warmup_bars(self) -> int:
        return self.lookback + 1

    @property
    def required_fields(self) -> set[str]:
        return required_fields(self._node)  # type: ignore[attr-defined]

    # -- signal generation ----------------------------------------------------
    def scores(self, bars: pd.DataFrame, eligibility: pd.DataFrame | None = None) -> pd.DataFrame:
        """Causal, preprocessed, sign-oriented cross-sectional score panel."""
        needed = self.required_fields
        panels = panels_from_bars(bars, needed=needed)
        score = compute_factor(self._node, panels, self._preprocess, eligibility)  # type: ignore[attr-defined]
        sign = -1.0 if self.expected_sign < 0 else 1.0
        return score * sign

    def generate_targets(
        self, bars: pd.DataFrame, eligibility: pd.DataFrame | None = None
    ) -> pd.DataFrame:
        score = self.scores(bars, eligibility)
        columns = list(score.columns)
        records: list[dict] = []
        for timestamp in score.index:
            weights = self._weights_for_row(score.loc[timestamp])
            for symbol in columns:
                value = score.at[timestamp, symbol]
                records.append(
                    {
                        "timestamp": timestamp,
                        "symbol": symbol,
                        "target_weight": float(weights.get(symbol, 0.0)),
                        "score": float(value) if pd.notna(value) else float("nan"),
                    }
                )
        return pd.DataFrame.from_records(records, columns=TARGET_COLUMNS)

    def _weights_for_row(self, row: pd.Series) -> dict[str, float]:
        clean = row.replace([np.inf, -np.inf], np.nan).dropna()
        n = len(clean)
        if n == 0:
            return {}
        k = max(1, n // self.quantiles)
        ascending = self.long_quantile == "bottom"
        ranked = clean.sort_values(ascending=ascending)
        longs = list(ranked.index[:k])
        weights: dict[str, float] = {}
        if self.long_short:
            shorts = list(ranked.index[-k:])
            shorts = [s for s in shorts if s not in set(longs)]
            long_w = min(self.gross_exposure / 2 / k, self.max_position_weight)
            short_w = min(self.gross_exposure / 2 / max(len(shorts), 1), self.max_position_weight)
            for symbol in longs:
                weights[symbol] = long_w
            for symbol in shorts:
                weights[symbol] = -short_w
        else:
            long_w = min(self.gross_exposure / k, self.max_position_weight)
            for symbol in longs:
                weights[symbol] = long_w
        return weights


def strategy_from_quarantine(record: dict[str, Any], **overrides: Any) -> CandidateStrategy:
    """Build a runnable strategy from a quarantined-bundle record.

    ``record`` is the JSON written by ``candidates.importer.import_bundle`` (it
    wraps the validated bundle under a ``"bundle"`` key). Passing the raw bundle
    dict directly also works.
    """
    bundle = record.get("bundle", record) if isinstance(record, dict) else record
    return CandidateStrategy.from_bundle(bundle, **overrides)


__all__ = ["CandidateStrategy", "strategy_from_quarantine"]
