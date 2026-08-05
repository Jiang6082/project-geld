"""Offline shadow tracking for a shadow-state candidate (no orders, no capital)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from project_geld.candidates import state as st
from project_geld.candidates.importer import import_bundle
from project_geld.candidates.shadow import run_candidate_shadow
from project_geld.cli import command_candidate_shadow
from project_geld.config import BacktestConfig, RiskConfig
from project_geld.strategies.candidate import CandidateStrategy

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "schemas" / "example_candidate_bundle.json"
CONFIG = REPO / "config.example.toml"


def _trending(path: Path | None = None, n_days=120, n_symbols=6, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-01", periods=n_days, freq="B", tz="UTC")
    drift = np.linspace(-0.0004, 0.0016, n_symbols)
    rows = []
    for j in range(n_symbols):
        prices = 100 * np.exp(np.cumsum(rng.normal(drift[j], 0.004, size=n_days)))
        for ts, price in zip(idx, prices):
            rows.append({"timestamp": ts, "symbol": f"S{j:02d}", "open": float(price), "high": float(price),
                         "low": float(price), "close": float(price), "volume": 1e6})
    df = pd.DataFrame(rows)
    if path is not None:
        df.to_csv(path, index=False)
    return df, [f"S{j:02d}" for j in range(n_symbols)]


def test_shadow_snapshot_appends_ledger(tmp_path):
    bars, syms = _trending()
    strat = CandidateStrategy(expression="ts_returns(close, 20)", lookback=20,
                              expected_sign=1, quantiles=3, candidate_id="mom20")
    ledger = tmp_path / "shadow_ledger.jsonl"
    snap1 = run_candidate_shadow(strat, bars, backtest=BacktestConfig(), risk=RiskConfig(),
                                 tradable_symbols=syms, ledger_path=ledger, candidate_id="mom20")
    assert snap1["mode"] == "shadow"
    assert snap1["n_positions"] >= 1
    assert 0 < snap1["gross_weight"] <= 1.0 + 1e-9
    assert snap1["candidate_id"] == "mom20"

    run_candidate_shadow(strat, bars, backtest=BacktestConfig(), risk=RiskConfig(),
                         tradable_symbols=syms, ledger_path=ledger, candidate_id="mom20")
    lines = ledger.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2  # appends, never overwrites
    assert json.loads(lines[0])["candidate_id"] == "mom20"


def test_shadow_cli_refuses_non_shadow_state(tmp_path):
    quarantined = import_bundle(EXAMPLE, quarantine_dir=tmp_path / "q")  # state=quarantined
    bars_csv = tmp_path / "bars.csv"
    _trending(bars_csv)
    args = argparse.Namespace(config=str(CONFIG), bundle=str(quarantined), bars=str(bars_csv),
                              max_symbols=None, output=str(tmp_path / "out"))
    with pytest.raises(RuntimeError, match="not 'shadow'"):
        command_candidate_shadow(args)


def test_shadow_cli_runs_for_shadow_state(tmp_path):
    quarantined = import_bundle(EXAMPLE, quarantine_dir=tmp_path / "q")
    # advance quarantined -> validated_oos -> shadow
    st.advance_file(quarantined, "validated_oos", reason="oos ok")
    st.advance_file(quarantined, "shadow", reason="gates pass")

    bars_csv = tmp_path / "bars.csv"
    _trending(bars_csv)
    output = tmp_path / "out"
    args = argparse.Namespace(config=str(CONFIG), bundle=str(quarantined), bars=str(bars_csv),
                              max_symbols=None, output=str(output))
    command_candidate_shadow(args)

    ledger = output / "shadow_ledger.jsonl"
    assert ledger.exists()
    snap = json.loads(ledger.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert snap["mode"] == "shadow"
    assert snap["candidate_id"] == "momentum_20"
