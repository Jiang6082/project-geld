"""Step 4 gate: full discover -> bundle -> import -> re-validate -> targets round-trip.

The CI-safe path uses the committed example bundle (no Emberforge needed). A
second path GENERATES a bundle through Emberforge's real ``geld_bundle`` exporter
when the sibling repo is importable, proving the actual export coupling — it is
skipped in CI where the upstream research project is intentionally absent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from project_geld.backtest import run_backtest
from project_geld.candidates.importer import import_bundle, load_quarantined
from project_geld.candidates.integrity import verify_bundle_integrity
from project_geld.candidates.promotion import revalidate_candidate
from project_geld.config import BacktestConfig, RiskConfig
from project_geld.strategies.candidate import strategy_from_quarantine

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "schemas" / "example_candidate_bundle.json"


def _trending_bars(n_days=160, n_symbols=8, seed=11):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-01", periods=n_days, freq="B", tz="UTC")
    drift = np.linspace(-0.0006, 0.0016, n_symbols)
    rows = []
    for j in range(n_symbols):
        prices = 100 * np.exp(np.cumsum(rng.normal(drift[j], 0.004, size=n_days)))
        for ts, price in zip(idx, prices):
            rows.append({"timestamp": ts, "symbol": f"S{j:02d}", "open": float(price),
                         "high": float(price), "low": float(price), "close": float(price),
                         "volume": 1e6})
    return pd.DataFrame(rows), [f"S{j:02d}" for j in range(n_symbols)]


def _roundtrip(bundle_path: Path, tmp_path: Path) -> None:
    # 1) import + validate (schema) + quarantine
    quarantined = import_bundle(bundle_path, quarantine_dir=tmp_path / "quarantine")
    record = load_quarantined(quarantined)
    assert record["state"] == "quarantined"
    assert record["paper_enabled"] is False

    # 2) verify the checksum / signal integrity independently
    report = verify_bundle_integrity(record["bundle"])
    assert report["ok"], report["problems"]
    assert report["hash_matches"]

    # 3) build a runnable strategy from the quarantined bundle
    strat = strategy_from_quarantine(record)

    # 4) a small backtest -> valid causal targets + metrics
    bars, symbols = _trending_bars()
    result = run_backtest(bars, strat, BacktestConfig(), RiskConfig(), benchmark="SPY",
                          tradable_symbols=symbols)
    assert list(result.targets.columns) == ["timestamp", "symbol", "target_weight", "score"]
    assert (result.targets["target_weight"] >= 0).all()
    assert result.targets["target_weight"].gt(0).any()
    for key in ("sharpe", "total_return", "max_drawdown", "annual_turnover"):
        assert key in result.metrics

    # 5) independent OOS verdict is well-formed
    verdict = revalidate_candidate(strat, bars, backtest=BacktestConfig(), risk=RiskConfig(),
                                   tradable_symbols=symbols)
    assert verdict["verdict"] in {"promote_to_shadow", "reject"}
    assert {"train", "val", "test"} <= set(verdict["segments"])


def test_roundtrip_example_bundle(tmp_path):
    """CI-safe: committed example bundle through the whole pipeline."""
    _roundtrip(EXAMPLE, tmp_path)


def _emberforge_src() -> Path | None:
    import os

    env = os.environ.get("GELD_EMBERFORGE_SRC")
    for path in ([Path(env)] if env else []) + [REPO.parent / "project-emberforge" / "src"]:
        if path.exists():
            return path
    return None


def test_roundtrip_from_emberforge_exporter(tmp_path):
    """Generate a bundle via Emberforge's REAL geld_bundle exporter, then round-trip."""
    ef_src = _emberforge_src()
    if ef_src is None:
        pytest.skip("project-emberforge src not available (set GELD_EMBERFORGE_SRC)")
    sys.path.insert(0, str(ef_src))
    try:
        from emberforge.dsl import canonical as efc
        from emberforge.dsl import parser as efp
        from emberforge.export.geld_bundle import export_geld_bundle_v1, to_geld_bundle_v1
    except Exception:  # pragma: no cover - environment dependent
        pytest.skip("emberforge exporter not importable")

    expr = "ts_returns(close, 20)"
    node = efp.parse(expr)
    factor = {
        "candidate_id": "gen_mom20",
        "expression": expr,
        "canonical_expression": efc.canonical_string(node),
        "expression_hash": efc.factor_hash(node),
        "required_fields": ["close"],
        "intended_frequency": "daily",
        "max_lookback": 20,
        "expected_sign": 1,
        "complexity_score": 7,
    }
    bundle = to_geld_bundle_v1(
        factor=factor, metrics={}, statistics={},
        hypothesis="Recent winners keep winning over horizon 20.",
        data_provenance={"universe": "research-only", "dataset_fingerprint": "deadbeefcafe"},
        approval_state="auto_approved",
    )
    bundle_path = tmp_path / "gen.candidate.json"
    export_geld_bundle_v1(bundle, bundle_path)

    # the exporter's full 32-hex hash must reproduce exactly in Geld.
    report = verify_bundle_integrity(json.loads(bundle_path.read_text()))
    assert report["ok"] and report["hash_matches"], report

    _roundtrip(bundle_path, tmp_path)
