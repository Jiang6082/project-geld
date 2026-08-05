"""Typed expression-tree nodes for the candidate factor DSL.

This is a faithful, standalone re-implementation of Emberforge's factor DSL node
model (``emberforge.dsl.nodes``). Geld MUST NOT import Emberforge at runtime, so
the grammar/operators are replicated here and pinned by a cross-check test.

Three node kinds only, which keeps parsing, hashing, and safe evaluation simple:

* ``Field``   — a raw market-data field (close, volume, ...).
* ``Const``   — a numeric literal.
* ``Call``    — an operator applied to child expressions.

Arbitrary Python is never part of the execution path; a factor is always one of
these three node types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

Node = Union["Field", "Const", "Call"]

# Mirrors Emberforge's RAW_FIELDS. Geld synthesises ``returns`` from ``close``
# and passes ``vwap`` through only when the bar source provides it.
RAW_FIELDS = frozenset(
    {"open", "high", "low", "close", "volume", "vwap", "returns"}
)


@dataclass(frozen=True)
class Field:
    name: str

    def __post_init__(self) -> None:
        if self.name not in RAW_FIELDS:
            raise ValueError(
                f"unknown raw field {self.name!r}; allowed: {sorted(RAW_FIELDS)}"
            )


@dataclass(frozen=True)
class Const:
    value: float


@dataclass(frozen=True)
class Call:
    op: str
    args: tuple[Node, ...] = field(default_factory=tuple)


def walk(node: Node):
    """Yield every node in the tree, parents before children (pre-order)."""
    yield node
    if isinstance(node, Call):
        for child in node.args:
            yield from walk(child)


def depth(node: Node) -> int:
    if isinstance(node, Call) and node.args:
        return 1 + max(depth(a) for a in node.args)
    return 1


def node_count(node: Node) -> int:
    return sum(1 for _ in walk(node))


def fields_used(node: Node) -> set[str]:
    return {n.name for n in walk(node) if isinstance(n, Field)}


def ops_used(node: Node) -> set[str]:
    return {n.op for n in walk(node) if isinstance(n, Call)}
