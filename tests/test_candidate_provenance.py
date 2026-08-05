"""Step 3 gate: cross-boundary provenance lineage for candidate-driven runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from project_geld import provenance
from project_geld.candidates.importer import import_bundle
from project_geld.candidates.validator import load_bundle
from project_geld.cli import command_revalidate_candidate

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "schemas" / "example_candidate_bundle.json"
CONFIG = REPO / "config.example.toml"

_LINEAGE_FIELDS = {"candidate_id", "source_project_version", "code_hash", "data_fingerprint"}


def test_candidate_lineage_extracts_bundle_fields():
    bundle = load_bundle(EXAMPLE)
    lineage = provenance.candidate_lineage(bundle)
    assert _LINEAGE_FIELDS <= set(lineage)
    assert lineage["candidate_id"] == "momentum_20"
    assert lineage["source_project_version"].startswith("emberforge")
    assert lineage["code_hash"] == bundle["code_hash"]
    assert lineage["data_fingerprint"] == bundle["data_fingerprint"]
    assert lineage["expression"] == "ts_returns(close,20)"


def test_manifest_round_trips_candidate_lineage(tmp_path):
    bundle = load_bundle(EXAMPLE)
    manifest = provenance.new_manifest("run-1", "candidate-revalidation")
    manifest.candidate = provenance.candidate_lineage(bundle)
    provenance.finalize(manifest, "success", outputs={"verdict": "promote_to_shadow"})
    path = tmp_path / "manifests" / "run-1.json"
    provenance.write_manifest(path, manifest)

    loaded = provenance.load_manifest(path)
    assert _LINEAGE_FIELDS <= set(loaded["candidate"])
    assert loaded["candidate"]["candidate_id"] == "momentum_20"
    assert loaded["outputs"]["verdict"] == "promote_to_shadow"


def _trending_csv(path: Path, n_days=160, n_symbols=8, seed=7) -> None:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-01", periods=n_days, freq="B", tz="UTC")
    drift = np.linspace(-0.0006, 0.0016, n_symbols)
    rows = []
    for j in range(n_symbols):
        prices = 100 * np.exp(np.cumsum(rng.normal(drift[j], 0.004, size=n_days)))
        for ts, price in zip(idx, prices):
            rows.append({"timestamp": ts, "symbol": f"S{j:02d}", "open": price, "high": price,
                         "low": price, "close": price, "volume": 1e6})
    pd.DataFrame(rows).to_csv(path, index=False)


def test_gate_candidate_run_manifest_has_lineage(tmp_path):
    # import the example bundle into a temp quarantine, then re-validate it and
    # assert the emitted run manifest carries the cross-boundary lineage.
    quarantine = tmp_path / "quarantine"
    quarantined = import_bundle(EXAMPLE, quarantine_dir=quarantine)
    bars_csv = tmp_path / "bars.csv"
    _trending_csv(bars_csv)
    output = tmp_path / "verdicts"

    args = argparse.Namespace(
        config=str(CONFIG), bundle=str(quarantined), bars=str(bars_csv),
        start=None, end=None, max_symbols=None, advance_state=True, output=str(output),
    )
    command_revalidate_candidate(args)

    manifests = list((output / "manifests").glob("*.json"))
    assert manifests, "no run manifest emitted"
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["run_kind"] == "candidate-revalidation"
    assert _LINEAGE_FIELDS <= set(manifest["candidate"])
    assert manifest["candidate"]["candidate_id"] == "momentum_20"

    # the JSONL run log also carries the lineage.
    log_lines = (output / "run_log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    record = json.loads(log_lines[-1])
    assert record["candidate"]["candidate_id"] == "momentum_20"
    assert record["verdict"] in {"promote_to_shadow", "reject"}
