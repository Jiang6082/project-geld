"""Static validation: arity, operator legality, complexity, and causality.

Faithful port of ``emberforge.dsl.causality``. The causality checks are
structural (they inspect the tree, not the data) and are the first line of
leakage defence before Geld ever evaluates a candidate expression.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import operators
from .nodes import Call, Const, Node, depth, node_count, walk


class CausalityError(ValueError):
    """Raised when an expression could see the future."""


class ValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Limits:
    max_depth: int = 8
    max_nodes: int = 40


def validate_structure(node: Node, limits: Limits = Limits()) -> None:
    """Validate arity, known operators, window literals, depth and node count."""
    if depth(node) > limits.max_depth:
        raise ValidationError(f"expression too deep: {depth(node)} > {limits.max_depth}")
    if node_count(node) > limits.max_nodes:
        raise ValidationError(f"expression too large: {node_count(node)} > {limits.max_nodes}")
    for n in walk(node):
        if isinstance(n, Call):
            try:
                spec = operators.get(n.op)  # raises on unknown / forbidden
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            if len(n.args) != spec.arity:
                raise ValidationError(
                    f"operator {n.op!r} expects {spec.arity} args, got {len(n.args)}"
                )


def check_causality(node: Node) -> None:
    """Reject structural look-ahead.

    * forbidden "future" operators (handled by :func:`operators.get`);
    * negative or zero integer window/lag literals in time-series operators;
    * non-integer / non-literal where an integer window is required.
    """
    for n in walk(node):
        if not isinstance(n, Call):
            continue
        spec = operators.get(n.op)
        for idx in spec.window_args:
            arg = n.args[idx]
            if not isinstance(arg, Const):
                raise CausalityError(
                    f"{n.op!r} window/lag argument must be a numeric literal, got {arg}"
                )
            if arg.value != int(arg.value):
                raise CausalityError(f"{n.op!r} window/lag must be an integer, got {arg.value}")
            if int(arg.value) <= 0:
                raise CausalityError(
                    f"{n.op!r} window/lag must be strictly positive (a negative shift "
                    f"reads the future); got {int(arg.value)}"
                )


def complexity_score(node: Node) -> int:
    """A simple, deterministic complexity proxy: nodes + 2*depth."""
    return node_count(node) + 2 * depth(node)


def validate(node: Node, limits: Limits = Limits()) -> None:
    """Full static validation pipeline used before any evaluation."""
    validate_structure(node, limits)
    check_causality(node)
