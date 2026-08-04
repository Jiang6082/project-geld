from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_geld.candidates.importer import BundleRejected, import_bundle, load_quarantined
from project_geld.candidates.validator import load_bundle, validate_bundle

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "schemas" / "example_candidate_bundle.json"
# The real Emberforge output, when this repo sits next to project-emberforge.
EMBERFORGE = REPO.parent / "project-emberforge" / "runtime" / "pipeline" / "geld_bundles"


def test_example_bundle_is_valid() -> None:
    result = validate_bundle(load_bundle(EXAMPLE))
    assert result.ok, result.errors
    assert result.candidate_id == "momentum_20"


def test_emberforge_shapes_accepted() -> None:
    # preprocessing as settings OBJECT, universe_assumptions as symbol LIST.
    bundle = load_bundle(EXAMPLE)
    assert isinstance(bundle["preprocessing"], dict)
    assert isinstance(bundle["universe_assumptions"], list)
    assert validate_bundle(bundle).ok


def test_wrong_schema_version_rejected() -> None:
    bundle = load_bundle(EXAMPLE)
    bundle["bundle_schema_version"] = "v2"
    assert not validate_bundle(bundle).ok


def test_embedded_code_rejected() -> None:
    bundle = load_bundle(EXAMPLE)
    bundle["signal_spec"]["python_code"] = "import os"
    result = validate_bundle(bundle)
    assert not result.ok
    assert any("executable content" in e or "unknown signal_spec" in e for e in result.errors)


def test_bad_field_types_rejected() -> None:
    bundle = load_bundle(EXAMPLE)
    bundle["universe_assumptions"] = 42
    assert not validate_bundle(bundle).ok
    bundle = load_bundle(EXAMPLE)
    bundle["preprocessing"] = "winsorize"
    assert not validate_bundle(bundle).ok


def test_import_quarantines_and_never_enables(tmp_path: Path) -> None:
    target = import_bundle(EXAMPLE, quarantine_dir=tmp_path / "q")
    record = load_quarantined(target)
    assert record["state"] == "quarantined"
    assert record["paper_enabled"] is False
    assert record["bundle"]["candidate_id"] == "momentum_20"


def test_import_rejects_invalid(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"bundle_schema_version": "candidate_bundle_v1"}), encoding="utf-8")
    with pytest.raises(BundleRejected):
        import_bundle(bad, quarantine_dir=tmp_path / "q")


@pytest.mark.skipif(not EMBERFORGE.exists(), reason="Emberforge bundles not present on this machine")
def test_real_emberforge_bundles_validate() -> None:
    bundles = sorted(EMBERFORGE.glob("*.candidate.json"))
    assert bundles, "expected at least one Emberforge bundle"
    for path in bundles:
        result = validate_bundle(load_bundle(path))
        assert result.ok, f"{path.name}: {result.errors}"
