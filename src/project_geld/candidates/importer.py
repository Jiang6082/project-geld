"""Manual, explicit import of an Emberforge candidate bundle into quarantine.

Guarantees:
  * Import is manual and explicit (a CLI command / function call). There is no
    automatic ingestion and no live link to Emberforge.
  * A bundle must pass validation before it is accepted.
  * An accepted bundle is written to a QUARANTINE directory with state
    "quarantined" and paper_enabled=False. It is never wired into any config,
    never enables trading, and never affects the deployed Daily V4 / Intra V15
    strategies.
  * This module imports no execution/broker code, so importing cannot trade.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from project_geld.atomicio import atomic_write_text
from project_geld.candidates.validator import load_bundle, validate_bundle

DEFAULT_QUARANTINE = Path("artifacts/candidates/quarantine")


class BundleRejected(Exception):
    pass


def import_bundle(path: str | Path, *, quarantine_dir: str | Path = DEFAULT_QUARANTINE) -> Path:
    """Validate and quarantine a bundle. Returns the quarantined file path.

    Raises BundleRejected if validation fails. NEVER enables trading.
    """
    bundle = load_bundle(path)
    result = validate_bundle(bundle)
    if not result.ok:
        raise BundleRejected(f"Bundle failed validation and was NOT imported: {result.errors}")

    record: dict[str, Any] = {
        "state": "quarantined",       # research-only; not promotable here
        "paper_enabled": False,       # importing never enables trading
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "source_path": str(path),
        "validation": result.to_dict(),
        "bundle": bundle,
    }
    target_dir = Path(quarantine_dir)
    safe_id = (result.candidate_id or "unknown").replace("/", "_").replace(":", "_")
    target = target_dir / f"{safe_id}.json"
    atomic_write_text(target, json.dumps(record, indent=2, sort_keys=True))
    return target


def load_quarantined(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
