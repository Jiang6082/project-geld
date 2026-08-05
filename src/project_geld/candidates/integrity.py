"""Independent integrity verification of a candidate bundle's signal.

Beyond schema validation, Geld re-derives the factor's canonical form and hash
from the shipped ``signal_spec.expression`` and compares them to the bundle's
``code_hash`` — the same "recompute the hash from scratch" check Emberforge's own
validator performs. A declarative spec that was tampered with (expression edited
but hash left stale, or vice-versa) is caught here even if a coarse file
checksum were regenerated. Bundle hashes may be a prefix of the full 32-hex
digest, so comparison is prefix-aware.
"""

from __future__ import annotations

from typing import Any

from project_geld.candidates.dsl import canonical_string, factor_hash, parse, validate
from project_geld.candidates.dsl.causality import CausalityError, ValidationError
from project_geld.candidates.dsl.parser import ParseError


def _hash_matches(computed: str, declared: str) -> bool:
    if not declared:
        return False
    a, b = computed.lower(), declared.lower()
    return a == b or a.startswith(b) or b.startswith(a)


def verify_bundle_integrity(bundle: dict[str, Any]) -> dict[str, Any]:
    """Recompute canonical form + hash and compare to the shipped ``code_hash``.

    Returns a per-check report; ``ok`` is True only when the expression parses,
    is causal, and its recomputed hash matches the declared ``code_hash``.
    """
    problems: list[str] = []
    signal = bundle.get("signal_spec") or {}
    expression = signal.get("expression") if isinstance(signal, dict) else None
    declared = str(bundle.get("code_hash", "") or "")

    if signal.get("kind") != "expression" or not isinstance(expression, str):
        return {
            "ok": False, "expression_causal": False, "hash_matches": False,
            "canonical_expression": None, "computed_hash": None,
            "declared_hash": declared,
            "problems": ["signal_spec is not a verifiable expression"],
        }

    expression_causal = False
    canonical = None
    computed = None
    try:
        node = parse(expression)
        validate(node)  # structural + causality
        expression_causal = True
        canonical = canonical_string(node)
        computed = factor_hash(node)
    except (ParseError, ValidationError, CausalityError) as exc:
        problems.append(f"expression rejected: {exc}")

    hash_matches = bool(computed) and _hash_matches(computed, declared)
    if computed and not declared:
        problems.append("bundle has no code_hash to verify against")
    elif computed and not hash_matches:
        problems.append(
            f"code_hash mismatch: declared {declared!r} vs recomputed {computed!r} "
            f"for canonical {canonical!r}"
        )

    return {
        "ok": expression_causal and hash_matches,
        "expression_causal": expression_causal,
        "hash_matches": hash_matches,
        "canonical_expression": canonical,
        "computed_hash": computed,
        "declared_hash": declared,
        "problems": problems,
    }


__all__ = ["verify_bundle_integrity"]
