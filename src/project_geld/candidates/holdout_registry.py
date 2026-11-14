"""Count how many candidates have been evaluated against each holdout.

Every candidate judged on the same holdout is a trial for the deflated Sharpe,
whether it ran alone or in a batch, today or last month. The registry keys a
holdout by the content hash of the bars file plus the date filter and split, and
records which candidate IDs were evaluated on it. Re-running the same candidate
is not a new trial; a new candidate is.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Iterable

import pandas as pd

from project_geld.atomicio import atomic_write_text

DEFAULT_REGISTRY = Path("artifacts/candidates/holdout_evaluations.json")
SPLIT_LABEL = "train=0.6,val=0.2"


def file_digest(path: str | Path, chunk_size: int = 1 << 20) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def holdout_key(
    bars_path: str | Path,
    start: str | None = None,
    end: str | None = None,
    split: str = SPLIT_LABEL,
) -> str:
    """Stable identifier for one holdout: data content, date filter and split.

    Dates are normalized, so ``2024-01-01`` and ``2024-01-01T00:00:00Z`` give the
    same key. Different data (even one appended day) is a different holdout.
    """
    def normalize(value: str | None) -> str:
        if not value:
            return ""
        stamp = pd.Timestamp(value)
        stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
        return stamp.isoformat()

    return f"{file_digest(bars_path)[:20]}|start={normalize(start)}|end={normalize(end)}|{split}"


def _load(path: Path) -> dict:
    if not path.exists():
        return {"holdouts": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.setdefault("holdouts", {})
    return payload


def evaluated_candidates(path: str | Path, key: str) -> set[str]:
    entry = _load(Path(path))["holdouts"].get(key, {})
    return {str(item["candidate_id"]) for item in entry.get("evaluations", [])}


def trials_including(path: str | Path, key: str, candidate_ids: Iterable[str]) -> int:
    """Distinct candidates on this holdout once ``candidate_ids`` are included."""
    return len(evaluated_candidates(path, key) | {str(cid) for cid in candidate_ids})


def record_evaluations(
    path: str | Path,
    key: str,
    results: Iterable[tuple[str, str]],
    at: pd.Timestamp | None = None,
) -> int:
    """Append ``(candidate_id, verdict)`` results; returns the distinct count."""
    registry_path = Path(path)
    payload = _load(registry_path)
    entry = payload["holdouts"].setdefault(key, {"evaluations": []})
    stamp = (pd.Timestamp.now(tz="UTC") if at is None else pd.Timestamp(at)).isoformat()
    for candidate_id, verdict in results:
        entry["evaluations"].append(
            {"candidate_id": str(candidate_id), "verdict": str(verdict), "evaluated_at": stamp}
        )
    entry["distinct_candidates"] = len({item["candidate_id"] for item in entry["evaluations"]})
    atomic_write_text(registry_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return entry["distinct_candidates"]


__all__ = [
    "DEFAULT_REGISTRY",
    "evaluated_candidates",
    "file_digest",
    "holdout_key",
    "record_evaluations",
    "trials_including",
]
