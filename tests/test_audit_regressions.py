"""Research-integrity regressions from the September 2026 review."""

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from project_geld.candidates import batch
from project_geld.candidates.dsl import PreprocessConfig, compute_factor, parse
from project_geld.candidates.importer import BundleRejected, import_bundle
from project_geld.candidates.integrity import verify_bundle_integrity
from project_geld.candidates.promotion import GatePolicy, evaluate_gates
from project_geld.candidates.state import advance, save_record
from project_geld.candidates.universe import bind_universe
from project_geld.candidates.validator import load_bundle, validate_bundle
from project_geld.config import BacktestConfig, RiskConfig

EXAMPLE = Path(__file__).resolve().parents[1] / "schemas/example_candidate_bundle.json"


def test_batch_counts_failed_gates_in_testing_family(monkeypatch):
    monkeypatch.setattr(batch, "bind_strategy", lambda b, *a, **k: (b, SimpleNamespace(symbols=[])))
    monkeypatch.setattr(batch, "sharpe_pvalue", lambda *a: 0.04)
    monkeypatch.setattr(batch, "revalidate_candidate", lambda b, *a, **k: {
        "segments": {"test": {"sharpe": 1.0, "n_obs": 252}},
        "verdict": "promote_to_shadow" if b["candidate_id"] == "winner" else "reject",
        "reasons": [],
    })
    bundles = [{"candidate_id": x} for x in ["winner", *[f"fail{i}" for i in range(9)]]]
    result = batch.revalidate_batch(bundles, pd.DataFrame(), backtest=BacktestConfig(), risk=RiskConfig())
    assert result["promoted"] == []
    assert "fails_fdr" in result["candidates"][0]["reasons"]


def test_batch_rejects_duplicate_ids_before_evaluating():
    with pytest.raises(ValueError, match="unique"):
        batch.revalidate_batch([{"candidate_id": "same"}] * 2, pd.DataFrame(),
                               backtest=BacktestConfig(), risk=RiskConfig())


@pytest.mark.parametrize("q", [-0.1, 1.1, float("nan")])
def test_fdr_rejects_invalid_threshold(q):
    with pytest.raises(ValueError):
        batch.benjamini_hochberg([0.01], q=q)


def test_rejecting_paper_candidate_revokes_enable_flag():
    record = {"state": "paper", "paper_enabled": True}
    advance(record, "rejected", reason="forward evidence failed")
    assert record["paper_enabled"] is False


def test_reimport_does_not_erase_history(tmp_path):
    target = import_bundle(EXAMPLE, quarantine_dir=tmp_path / "q")
    record = json.loads(target.read_text(encoding="utf-8"))
    advance(record, "rejected", reason="failed independent check")
    save_record(target, record)
    before = target.read_bytes()
    with pytest.raises(BundleRejected, match="already"):
        import_bundle(EXAMPLE, quarantine_dir=tmp_path / "q")
    assert target.read_bytes() == before


@pytest.mark.parametrize("candidate_id", [r"..\outside", "../outside", "C:escape", "CON", "nul.txt", "trailing."])
def test_import_ids_are_portable_safe_filenames(candidate_id):
    bundle = load_bundle(EXAMPLE)
    bundle["candidate_id"] = candidate_id
    assert not validate_bundle(bundle).ok


def test_integrity_does_not_accept_single_character_hash():
    from project_geld.candidates.dsl import factor_hash
    bundle = load_bundle(EXAMPLE)
    bundle["code_hash"] = factor_hash(parse(bundle["signal_spec"]["expression"]))[:1]
    assert not verify_bundle_integrity(bundle)["ok"]


def test_integrity_malformed_signal_is_a_failure_report():
    assert not verify_bundle_integrity({"signal_spec": ["invalid"]})["ok"]


def _metrics():
    return {"total_return": 0.2, "sharpe": 1.0, "max_drawdown": -0.1,
            "annual_alpha": 0.1, "beta": 0.2}


def test_required_cost_stress_cannot_be_omitted():
    gates = evaluate_gates(test=_metrics(), val=_metrics(), test_stressed=None,
                           annual_turnover=1.0, policy=GatePolicy())
    assert any(g.name == "cost_stress" and not g.passed for g in gates)


@pytest.mark.parametrize("part,key,value", [("test", "sharpe", np.inf),
    ("test", "annual_alpha", np.inf), ("test", "sharpe", np.nan), ("val", "sharpe", np.nan)])
def test_nonfinite_metrics_never_pass(part, key, value):
    metrics = {"test": _metrics(), "val": _metrics()}
    metrics[part][key] = value
    gates = evaluate_gates(**metrics, test_stressed=_metrics(), annual_turnover=1.0, policy=GatePolicy())
    assert not all(g.passed for g in gates)


def test_negative_execution_lag_rejected():
    with pytest.raises(ValueError):
        PreprocessConfig(execution_lag=-1)


def test_nested_cross_section_ignores_ineligible_names():
    index = pd.date_range("2020-01-01", periods=5, tz="UTC")
    close = pd.DataFrame([[1., 3., 2., 100.]] * 5, index=index, columns=list("ABCD"))
    eligible = pd.DataFrame([[True, True, False, False]] * 5, index=index, columns=close.columns)
    config = PreprocessConfig(normalize=False, winsorize_p=None)
    node = parse("ts_mean(cs_rank(close), 2)")
    score = compute_factor(node, {"close": close}, config, eligible)
    assert score.loc[index[-1], "B"] == 2.0
    # Coverage is relative to eligible names, not the complete historical panel.
    close["E"] = 200.
    score = compute_factor(node, {"close": close}, config, eligible)
    assert score.loc[index[-1], "B"] == 2.0


def test_explicit_unavailable_universe_does_not_expand_silently():
    bars = pd.DataFrame({"symbol": ["AAA", "BBB"]})
    binding = bind_universe({"universe_assumptions": ["ZZZ"]}, bars)
    assert not binding.ok


def test_synthetic_symbols_in_data_never_bind():
    bars = pd.DataFrame({"symbol": ["SYM00", "SYM01", "AAA"]})
    assert bind_universe({}, bars).symbols == ["AAA"]


def test_future_bar_counts_do_not_change_capped_universe():
    bars = pd.DataFrame({"symbol": ["AAA", "BBB"]})
    original = bind_universe({}, bars, max_symbols=1).symbols
    extended = pd.concat([bars, pd.DataFrame({"symbol": ["BBB"] * 100})])
    assert bind_universe({}, extended, max_symbols=1).symbols == original


def test_missing_benchmark_blocks_promotion():
    from test_candidate_promotion import _trending_bars
    from project_geld.candidates.promotion import revalidate_candidate
    from project_geld.strategies.candidate import CandidateStrategy
    bars, symbols = _trending_bars()
    bars = bars[bars["symbol"] != "SPY"]
    verdict = revalidate_candidate(CandidateStrategy("ts_returns(close,20)", lookback=20), bars,
        backtest=BacktestConfig(), risk=RiskConfig(), tradable_symbols=symbols)
    assert verdict["verdict"] == "reject"
    assert "benchmark_data" in verdict["reasons"]


def test_segment_includes_first_holdout_loss():
    from project_geld.experiments import _period_metrics
    dates = pd.date_range("2020-01-01", periods=3, tz="UTC")
    result = SimpleNamespace(
        equity=pd.DataFrame({"timestamp": dates, "equity": [100., 50., 60.], "benchmark_return": [0., 0., 0.]}),
        trades=pd.DataFrame(columns=["timestamp", "notional", "fees"]))
    metrics = _period_metrics(result, dates[1], dates[2])
    assert metrics["total_return"] == pytest.approx(-.4)
    assert metrics["max_drawdown"] == pytest.approx(-.5)


def test_normalize_preserves_vwap_and_removes_invalid_symbols():
    from project_geld.data import normalize_bars
    frame = pd.DataFrame({"timestamp": ["2020-01-01"] * 3, "symbol": ["A", None, ""],
                          "open": 1., "high": 2., "low": 1., "close": 2., "volume": 10., "vwap": 1.5})
    actual = normalize_bars(frame)
    assert actual["symbol"].tolist() == ["A"]
    assert actual["vwap"].tolist() == [1.5]


def test_grid_ranking_does_not_select_on_holdout(monkeypatch):
    import project_geld.experiments as experiments
    monkeypatch.setattr(experiments, "create_strategy", lambda name, params: params["lookback"])
    monkeypatch.setattr(experiments, "run_backtest", lambda bars, strategy, *a: SimpleNamespace(
        strategy=strategy, metrics={"orders": 0, "annual_turnover": 0}))
    dates = pd.date_range("2020-01-01", periods=10, tz="UTC")
    def metrics(result, start, end):
        train = start == dates[0]
        sharpe = (2. if train else -5.) if result.strategy == 5 else (1. if train else 20.)
        return {"total_return": 0., "sharpe": sharpe, "max_drawdown": 0.}
    monkeypatch.setattr(experiments, "_period_metrics", metrics)
    result = experiments.grid_search(pd.DataFrame({"timestamp": dates}), "momentum", {"lookback": [5, 10]},
                                      BacktestConfig(), RiskConfig())
    assert json.loads(result.iloc[0]["parameters"])["lookback"] == 5


def test_full_preprocessing_settings_are_preserved():
    from project_geld.strategies.candidate import CandidateStrategy
    settings = {"min_coverage": .8, "winsorize_p": .1, "normalize": False,
                "neutralize": True, "execution_lag": 2}
    strategy = CandidateStrategy("close", preprocessing=settings)
    assert strategy._preprocess == PreprocessConfig(**settings)


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nonfinite_risk_configuration_rejected(value):
    from project_geld.config import AppConfig, UniverseConfig, validate_config
    with pytest.raises(ValueError, match="finite"):
        validate_config(AppConfig(universe=UniverseConfig(["SPY"]), risk=RiskConfig(max_order_notional=value)))
