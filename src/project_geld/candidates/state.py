"""Candidate lifecycle state machine (persisted, auditable).

A candidate imported from Emberforge moves through explicit states:

    quarantined -> validated_oos -> shadow -> paper
                \\-> rejected      \\-> rejected  \\-> rejected

* ``quarantined``   — imported + schema-valid, research-only (importer default).
* ``validated_oos`` — passed Geld's INDEPENDENT out-of-sample re-validation.
* ``shadow``        — cleared to run in shadow (no capital) after OOS gates.
* ``paper``         — cleared for the paper account. This transition is MANUAL
                      only; the automated gates never reach it.
* ``rejected``      — failed a gate at some stage; terminal.

State lives inside the quarantine record JSON (written by ``importer``); every
transition appends to an audit ``history`` list. Writes are atomic.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from project_geld.atomicio import atomic_write_text

STATES = ("quarantined", "validated_oos", "shadow", "paper", "rejected")

# Allowed forward transitions. ``paper`` is reachable only with manual=True.
ALLOWED: dict[str, set[str]] = {
    "quarantined": {"validated_oos", "rejected"},
    "validated_oos": {"shadow", "rejected"},
    "shadow": {"paper", "rejected"},
    "paper": {"rejected"},
    "rejected": set(),
}

# Transitions that must be performed by a human, never by the automated gates.
_MANUAL_ONLY = {("shadow", "paper")}


class StateError(Exception):
    pass


def can_transition(src: str, dst: str) -> bool:
    return dst in ALLOWED.get(src, set())


def advance(
    record: dict[str, Any],
    to_state: str,
    *,
    reason: str,
    evidence: dict[str, Any] | None = None,
    manual: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return ``record`` mutated to ``to_state`` with an appended history entry.

    Raises :class:`StateError` on an illegal transition, an unknown state, or a
    manual-only transition attempted without ``manual=True``.
    """
    if to_state not in STATES:
        raise StateError(f"unknown target state {to_state!r}; valid: {STATES}")
    src = record.get("state", "quarantined")
    if src == to_state:
        raise StateError(f"already in state {to_state!r}")
    if not can_transition(src, to_state):
        raise StateError(f"illegal transition {src!r} -> {to_state!r}")
    if (src, to_state) in _MANUAL_ONLY and not manual:
        raise StateError(
            f"transition {src!r} -> {to_state!r} is manual-only; the automated "
            "promotion gates cannot perform it"
        )
    moment = (now or datetime.now(timezone.utc)).isoformat()
    record["state"] = to_state
    # paper is only ever enabled by an explicit manual step to paper state.
    if to_state == "paper" and manual:
        record["paper_enabled"] = True
    record.setdefault("history", []).append(
        {
            "from": src,
            "to": to_state,
            "at": moment,
            "reason": reason,
            "manual": bool(manual),
            "evidence": evidence or {},
        }
    )
    return record


def load_record(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_record(path: str | Path, record: dict[str, Any]) -> None:
    atomic_write_text(Path(path), json.dumps(record, indent=2, sort_keys=True))


def advance_file(
    path: str | Path,
    to_state: str,
    *,
    reason: str,
    evidence: dict[str, Any] | None = None,
    manual: bool = False,
) -> dict[str, Any]:
    """Load a quarantine record, advance its state, persist atomically."""
    record = load_record(path)
    advance(record, to_state, reason=reason, evidence=evidence, manual=manual)
    save_record(path, record)
    return record


__all__ = [
    "STATES", "ALLOWED", "StateError", "can_transition",
    "advance", "advance_file", "load_record", "save_record",
]
