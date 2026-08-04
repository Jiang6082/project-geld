"""Offline validator for candidate bundles (candidate_bundle_v1).

Project Geld receives APPROVED factor/strategy candidates from the separate
Emberforge research project as offline, versioned JSON bundles (produced under
``emberforge/runtime/pipeline/geld_bundles/``). This validator enforces the
contract before any bundle is accepted.

Design constraints:
  * Fully offline. No network, no code execution.
  * A bundle is DATA ONLY. It must not carry executable Python; the validator
    rejects any field that looks like embedded code, and rejects unknown
    top-level / signal_spec keys so there is nowhere to hide one.
  * Validation is required before import (see importer.py). A valid bundle NEVER
    enables paper trading and NEVER touches deployed strategies or configs.
  * Dependency-free (no jsonschema import), mirroring schemas/candidate_bundle_v1.schema.json.

Accepts the shapes the source project actually emits: ``preprocessing`` may be an
ordered array of step objects OR a settings object; ``universe_assumptions`` may
be a structured object OR a list of symbol strings. These optional fields are
type-checked so genuinely malformed shapes are rejected rather than ignored.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "candidate_bundle_v1"

_ALLOWED_INPUTS = {"open", "high", "low", "close", "volume", "vwap"}
_ALLOWED_FREQ = {"1Min", "5Min", "15Min", "1Day"}
_ALLOWED_APPROVAL = {"draft", "approved", "rejected"}
_ALLOWED_SIGNAL_KIND = {"expression", "reference"}

_REQUIRED = [
    "bundle_schema_version", "candidate_id", "name", "source_project_version",
    "signal_spec", "economic_hypothesis", "required_inputs", "frequency",
    "lookback", "approval_status", "created_at",
]

_ALLOWED_TOP_LEVEL = set(_REQUIRED) | {
    "universe_assumptions", "preprocessing", "portfolio_construction",
    "evaluation_summary", "data_fingerprint", "code_hash",
}
_ALLOWED_SIGNAL_KEYS = {"kind", "expression", "reference", "parameters"}

# Token-exact denylist (defense in depth). Matched against key-name TOKENS split
# on non-alphanumerics, so 'evaluation' or 'code_hash' never trip 'eval'/'code'.
_FORBIDDEN_TOKENS = {
    "python", "pickle", "lambda", "exec", "eval", "callable",
    "entrypoint", "shell", "script", "command", "reduce",
}


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    candidate_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "candidate_id": self.candidate_id,
            "errors": self.errors, "warnings": self.warnings,
        }


def load_bundle(path: str | Path) -> dict[str, Any]:
    """Load a bundle strictly as JSON. Never evaluates code; rejects non-objects."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Candidate bundle must be a JSON object.")
    return data


def validate_bundle(bundle: dict[str, Any]) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    # --- Unknown top-level / signal keys (no place to hide a code field) ----
    for key in bundle:
        if key not in _ALLOWED_TOP_LEVEL:
            errors.append(f"unknown top-level field not permitted by the contract: '{key}'")
    signal = bundle.get("signal_spec")
    if isinstance(signal, dict):
        for key in signal:
            if key not in _ALLOWED_SIGNAL_KEYS:
                errors.append(f"unknown signal_spec field not permitted: '{key}'")

    # --- Hard reject embedded-code key names --------------------------------
    for leaf in _leaf_keys(bundle):
        hit = set(_tokenize(leaf)) & _FORBIDDEN_TOKENS
        if hit:
            errors.append(f"forbidden key implies executable content: '{leaf}' ({sorted(hit)})")

    # --- Version & required -------------------------------------------------
    if bundle.get("bundle_schema_version") != SCHEMA_VERSION:
        errors.append(
            f"bundle_schema_version must be '{SCHEMA_VERSION}', got {bundle.get('bundle_schema_version')!r}"
        )
    for key in _REQUIRED:
        if key not in bundle:
            errors.append(f"missing required field: {key}")

    # --- Typed fields -------------------------------------------------------
    for key in ("candidate_id", "name", "source_project_version", "economic_hypothesis", "created_at"):
        if key in bundle and (not isinstance(bundle[key], str) or not bundle[key].strip()):
            errors.append(f"{key} must be a non-empty string")

    if isinstance(signal, dict):
        kind = signal.get("kind")
        if kind not in _ALLOWED_SIGNAL_KIND:
            errors.append(f"signal_spec.kind must be one of {sorted(_ALLOWED_SIGNAL_KIND)}")
        if kind == "expression" and not isinstance(signal.get("expression"), str):
            errors.append("signal_spec.kind=expression requires a string 'expression'")
        if kind == "reference" and not isinstance(signal.get("reference"), str):
            errors.append("signal_spec.kind=reference requires a string 'reference'")
    elif "signal_spec" in bundle:
        errors.append("signal_spec must be an object")

    inputs = bundle.get("required_inputs")
    if isinstance(inputs, list):
        if not inputs:
            errors.append("required_inputs must not be empty")
        bad = [x for x in inputs if x not in _ALLOWED_INPUTS]
        if bad:
            errors.append(f"required_inputs has unsupported fields: {bad}")
    elif "required_inputs" in bundle:
        errors.append("required_inputs must be an array")

    if "frequency" in bundle and bundle.get("frequency") not in _ALLOWED_FREQ:
        errors.append(f"frequency must be one of {sorted(_ALLOWED_FREQ)}")

    lookback = bundle.get("lookback")
    if "lookback" in bundle and (not isinstance(lookback, int) or isinstance(lookback, bool) or lookback < 1):
        errors.append("lookback must be a positive integer")

    approval = bundle.get("approval_status")
    if "approval_status" in bundle and approval not in _ALLOWED_APPROVAL:
        errors.append(f"approval_status must be one of {sorted(_ALLOWED_APPROVAL)}")

    # --- Optional structured fields (both source-project shapes accepted) ----
    prep = bundle.get("preprocessing")
    if "preprocessing" in bundle:
        if not isinstance(prep, (list, dict)):
            errors.append("preprocessing must be an array of step objects or a settings object")
        elif isinstance(prep, list) and not all(isinstance(x, dict) for x in prep):
            errors.append("preprocessing array items must be objects")

    universe = bundle.get("universe_assumptions")
    if "universe_assumptions" in bundle:
        if not isinstance(universe, (dict, list)):
            errors.append("universe_assumptions must be an object or a list of symbols")
        elif isinstance(universe, list) and not all(isinstance(x, str) for x in universe):
            errors.append("universe_assumptions list items must be symbol strings")

    for key in ("portfolio_construction", "evaluation_summary"):
        if key in bundle and not isinstance(bundle[key], dict):
            errors.append(f"{key} must be an object")
    for key in ("data_fingerprint", "code_hash"):
        if key in bundle and not isinstance(bundle[key], str):
            errors.append(f"{key} must be a string")

    # --- Advisory -----------------------------------------------------------
    if approval != "approved":
        warnings.append(
            f"approval_status is '{approval}': valid but not approved; it stays research-only regardless of import."
        )
    if not bundle.get("data_fingerprint"):
        warnings.append("data_fingerprint is missing; provenance comparison will be weaker.")

    candidate_id = bundle.get("candidate_id") if isinstance(bundle.get("candidate_id"), str) else None
    return ValidationResult(ok=not errors, errors=errors, warnings=warnings, candidate_id=candidate_id)


def _leaf_keys(obj: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.append(str(k))
            keys.extend(_leaf_keys(v))
    elif isinstance(obj, list):
        for v in obj:
            keys.extend(_leaf_keys(v))
    return keys


def _tokenize(key: str) -> list[str]:
    tokens: list[str] = []
    token = ""
    for ch in key.lower():
        if ch.isalnum():
            token += ch
        elif token:
            tokens.append(token)
            token = ""
    if token:
        tokens.append(token)
    return tokens
