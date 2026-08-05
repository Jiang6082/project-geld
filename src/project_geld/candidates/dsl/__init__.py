"""Standalone, causal re-implementation of Emberforge's factor DSL.

Geld MUST NOT import Emberforge at runtime; this package replicates the parser,
operator whitelist, structural causality checks, and evaluation semantics so a
candidate bundle's ``signal_spec.expression`` can be turned into a tradeable
signal that matches upstream numbers (pinned by a cross-check test). No
``eval``/``exec`` is ever used — only the three typed node kinds.
"""

from __future__ import annotations

from .causality import CausalityError, Limits, ValidationError, complexity_score, validate
from .evaluator import (
    FieldPanels,
    FieldUnavailable,
    PreprocessConfig,
    compute_factor,
    evaluate,
    panels_from_bars,
    required_fields,
)
from .nodes import RAW_FIELDS, Call, Const, Field, Node
from .parser import ParseError, parse

__all__ = [
    "parse", "ParseError",
    "Node", "Field", "Const", "Call", "RAW_FIELDS",
    "validate", "ValidationError", "CausalityError", "Limits", "complexity_score",
    "evaluate", "compute_factor", "panels_from_bars", "required_fields",
    "PreprocessConfig", "FieldPanels", "FieldUnavailable",
]
