from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from project_geld.atomicio import atomic_write_dataframe_csv, atomic_write_text


def test_atomic_write_text_creates_and_replaces(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "state.json"
    atomic_write_text(target, json.dumps({"a": 1}))
    atomic_write_text(target, json.dumps({"a": 2}))
    assert json.loads(target.read_text()) == {"a": 2}
    # No leftover temp files from the atomic swap.
    assert list((tmp_path / "sub").glob(".state.json*")) == []


def test_atomic_write_failure_preserves_original(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    atomic_write_text(target, json.dumps({"ok": True}))
    with pytest.raises(TypeError):
        atomic_write_text(target, None)  # type: ignore[arg-type]
    assert json.loads(target.read_text()) == {"ok": True}
    assert list(tmp_path.glob(".state.json*")) == []


def test_atomic_write_dataframe_csv_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "perf.csv"
    frame = pd.DataFrame([{"session_date": "2026-08-01", "equity": 100.0}])
    atomic_write_dataframe_csv(frame, target)
    back = pd.read_csv(target)
    assert list(back.columns) == ["session_date", "equity"]
    assert back.iloc[0]["equity"] == 100.0
    assert list(tmp_path.glob(".perf.csv*")) == []
