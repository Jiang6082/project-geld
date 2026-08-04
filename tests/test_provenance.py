from __future__ import annotations

from pathlib import Path

from project_geld.config import load_config
from project_geld.provenance import (
    _sanitize_args,
    compare_manifests,
    config_fingerprint,
    finalize,
    load_manifest,
    make_run_id,
    new_manifest,
    write_manifest,
)

CONFIG = "config.example.toml"


def test_config_fingerprint_stable_and_sensitive() -> None:
    c1 = load_config(CONFIG)
    c2 = load_config(CONFIG)
    assert config_fingerprint(c1) == config_fingerprint(c2)


def test_fingerprint_changes_with_config() -> None:
    from dataclasses import replace

    base = load_config(CONFIG)
    changed = replace(base, backtest=replace(base.backtest, initial_cash=base.backtest.initial_cash + 1))
    assert config_fingerprint(base) != config_fingerprint(changed)


def test_run_id_unique_and_prefixed() -> None:
    a = make_run_id("paper_cycle")
    b = make_run_id("paper_cycle")
    assert a.startswith("paper_cycle-")
    assert a != b


def test_sanitize_args_redacts_values() -> None:
    clean = " ".join(_sanitize_args(["geld", "--api-key=pk_live_x", "--config=c.toml"]))
    assert "pk_live_x" not in clean
    assert "--config=c.toml" in clean


def test_manifest_roundtrip_no_secrets(tmp_path: Path) -> None:
    config = load_config(CONFIG)
    manifest = finalize(new_manifest("run-1", "paper_cycle", config), "success", {"orders": 0})
    path = tmp_path / "m.json"
    write_manifest(path, manifest)
    loaded = load_manifest(path)
    assert loaded["status"] == "success"
    assert loaded["config_fingerprint"] == config_fingerprint(config)
    # credential profile name may appear, but never a secret value.
    assert "secret" not in path.read_text().lower() or "credential_profile" not in loaded


def test_compare_manifests_equivalent_and_diff(tmp_path: Path) -> None:
    config = load_config(CONFIG)
    m1 = finalize(new_manifest("a", "paper_cycle", config), "success").to_dict()
    m2 = finalize(new_manifest("b", "paper_cycle", config), "success").to_dict()
    assert compare_manifests(m1, m2)["equivalent"]

    m3 = finalize(new_manifest("c", "research", config), "success").to_dict()
    result = compare_manifests(m1, m3)
    assert not result["equivalent"]
    assert "run_kind" in result["differences"]
