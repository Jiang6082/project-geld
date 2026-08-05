"""Run provenance: fingerprints and manifests for paper/research runs.

A manifest answers "were two runs actually equivalent?" without relying on memory.
It records software versions, git state, a config fingerprint, the strategy, run
mode, timing, and a run id. It NEVER contains secrets — only public configuration
and environment metadata (Alpaca credentials live in env/keyring, not here).
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from project_geld.atomicio import atomic_write_text

_SORT = dict(sort_keys=True, separators=(",", ":"))


def make_run_id(prefix: str = "", now: datetime | None = None) -> str:
    moment = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%S")
    suffix = os.urandom(3).hex()
    core = f"{moment}-{suffix}"
    return f"{prefix}-{core}" if prefix else core


def _stable_hash(obj: Any) -> str:
    payload = json.dumps(obj, default=str, **_SORT).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def config_fingerprint(config: Any) -> str:
    """Deterministic fingerprint of the full AppConfig (nested dataclasses).

    Paths and other non-JSON values are stringified. There are no secrets in the
    config object (credentials are referenced by profile name only)."""
    return _stable_hash(asdict(config))


def git_info(repo_dir: str | Path | None = None) -> dict[str, Any]:
    cwd = str(repo_dir) if repo_dir else None
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, timeout=5
        )
        if commit.returncode != 0:
            return {"commit": None, "dirty": None}
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True, timeout=5
        )
        return {"commit": commit.stdout.strip() or None, "dirty": bool(status.stdout.strip())}
    except Exception:
        return {"commit": None, "dirty": None}


def software_versions() -> dict[str, Any]:
    """Dependency versions via package metadata (no heavy imports)."""
    from importlib.metadata import PackageNotFoundError, version

    versions: dict[str, Any] = {"python": platform.python_version()}
    for dist in ("project-geld", "numpy", "pandas", "python-dotenv", "alpaca-py"):
        try:
            versions[dist] = version(dist)
        except PackageNotFoundError:
            versions[dist] = "not-installed"
        except Exception:
            versions[dist] = "unknown"
    return versions


_SENSITIVE = ("key", "secret", "token", "password", "webhook")


def _sanitize_args(args: list[str]) -> list[str]:
    clean: list[str] = []
    for arg in args:
        low = arg.lower()
        if "=" in arg and any(h in low.split("=", 1)[0] for h in _SENSITIVE):
            clean.append(arg.split("=", 1)[0] + "=***")
        else:
            clean.append(arg)
    return clean


@dataclass
class RunManifest:
    run_id: str
    run_kind: str
    started_at: str
    completed_at: str | None = None
    status: str = "started"
    command: str = ""
    sanitized_args: list[str] = field(default_factory=list)
    config_fingerprint: str | None = None
    strategy: str | None = None
    account: str | None = None
    run_mode: str | None = None
    software_versions: dict[str, Any] = field(default_factory=dict)
    git: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    # Cross-project lineage for Emberforge candidate-driven runs. Empty for
    # ordinary Geld runs; populated from the imported bundle so a candidate run
    # can be traced back to the exact discovered factor it came from.
    candidate: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def candidate_lineage(bundle: dict[str, Any]) -> dict[str, Any]:
    """Extract cross-boundary lineage fields from an Emberforge candidate bundle.

    Records who the candidate is (``candidate_id``), which upstream project +
    version produced it (``source_project_version``), the signal fingerprint
    (``code_hash``), and the dataset it was discovered on
    (``data_fingerprint``) — plus the declarative expression and Emberforge's own
    approval state for a complete audit trail. All values are declarative data;
    nothing here is executed.
    """
    signal = bundle.get("signal_spec") or {}
    summary = bundle.get("evaluation_summary") or {}
    return {
        "candidate_id": bundle.get("candidate_id"),
        "source_project_version": bundle.get("source_project_version"),
        "code_hash": bundle.get("code_hash", ""),
        "data_fingerprint": bundle.get("data_fingerprint", ""),
        "bundle_schema_version": bundle.get("bundle_schema_version"),
        "expression": signal.get("expression") if isinstance(signal, dict) else None,
        "emberforge_approval_state": summary.get("emberforge_approval_state")
        or bundle.get("approval_status"),
    }


def new_manifest(run_id: str, run_kind: str, config: Any | None = None, *, repo_dir: str | Path | None = None) -> RunManifest:
    manifest = RunManifest(
        run_id=run_id,
        run_kind=run_kind,
        started_at=datetime.now(timezone.utc).isoformat(),
        command=" ".join(_sanitize_args(sys.argv)),
        sanitized_args=_sanitize_args(sys.argv),
        software_versions=software_versions(),
        git=git_info(repo_dir),
    )
    if config is not None:
        manifest.config_fingerprint = config_fingerprint(config)
        manifest.strategy = getattr(getattr(config, "strategy", None), "name", None)
        manifest.account = getattr(getattr(config, "account", None), "name", None)
    return manifest


def finalize(manifest: RunManifest, status: str, outputs: dict[str, Any] | None = None) -> RunManifest:
    manifest.completed_at = datetime.now(timezone.utc).isoformat()
    manifest.status = status
    if outputs:
        manifest.outputs.update(outputs)
    return manifest


def write_manifest(path: str | Path, manifest: RunManifest) -> None:
    atomic_write_text(path, json.dumps(manifest.to_dict(), indent=2, sort_keys=True, default=str) + "\n")


def append_jsonl(path: str | Path, record: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_manifest(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


_VOLATILE = {"run_id", "started_at", "completed_at", "command", "sanitized_args", "notes", "outputs"}


def compare_manifests(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """True ``equivalent`` iff everything determining the run's result matches
    (config fingerprint, strategy, account, run mode, versions, git)."""
    diffs: dict[str, dict[str, Any]] = {}
    for key in sorted(set(a) | set(b)):
        if key in _VOLATILE:
            continue
        if a.get(key) != b.get(key):
            diffs[key] = {"a": a.get(key), "b": b.get(key)}
    return {"equivalent": not diffs, "differences": diffs}
