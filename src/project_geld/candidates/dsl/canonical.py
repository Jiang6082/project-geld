"""Canonicalization + hashing (port of Emberforge's ``dsl.canonical``).

Lets Geld INDEPENDENTLY recompute a candidate's canonical expression and hash
and compare them to what the bundle shipped — catching a tampered declarative
spec even if a coarse file checksum were regenerated. Commutative operators sort
their arguments so algebraically-identical trees hash identically.
"""

from __future__ import annotations

import hashlib

from . import operators
from .nodes import Call, Const, Field, Node


def _fmt_const(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return repr(value)


def canonicalize(node: Node) -> Node:
    if isinstance(node, (Field, Const)):
        return node
    assert isinstance(node, Call)
    args = tuple(canonicalize(a) for a in node.args)
    spec = operators.REGISTRY.get(node.op)
    if spec is not None and spec.commutative:
        args = tuple(sorted(args, key=to_string))
    return Call(node.op, args)


def to_string(node: Node) -> str:
    if isinstance(node, Field):
        return node.name
    if isinstance(node, Const):
        return _fmt_const(node.value)
    assert isinstance(node, Call)
    inner = ",".join(to_string(a) for a in node.args)
    return f"{node.op}({inner})"


def canonical_string(node: Node) -> str:
    return to_string(canonicalize(node))


def factor_hash(node: Node) -> str:
    """Stable SHA-256 (first 16 bytes, hex) of the canonical expression."""
    digest = hashlib.sha256(canonical_string(node).encode("utf-8")).hexdigest()
    return digest[:32]
