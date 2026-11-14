"""Deflated Sharpe and discovery-window checks for candidate re-validation."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from project_geld.candidates.batch import revalidate_batch
from project_geld.candidates.promotion import (
    declared_trials,
    discovery_window,
    revalidate_candidate,
)
from project_geld.candidates.stats import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    probabilistic_sharpe_ratio,
    sharpe_moments,
    trial_sharpe_variance,
)
from project_geld.candidates.validator import validate_bundle
from project_geld.config import BacktestConfig, RiskConfig
from project_geld.strategies.candidate import CandidateStrategy

EXAMPLE = Path(__file__).resolve().parents[1] / "schemas" / "example_candidate_bundle.json"


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------
def test_deflated_sharpe_matches_published_example():
    # Bailey & Lopez de Prado (2014), numerical example: best annualized SR 2.5
    # of N=100 trials, annualized SR variance 0.5, T=1250, skew -3, kurtosis 10.
    result = deflated_sharpe_ratio(
        2.5 / math.sqrt(250), 1250, -3.0, 10.0, n_trials=100, sharpe_variance=0.5 / 250
    )
    assert result["expected_max_sharpe"] == pytest.approx(0.1132, abs=1e-4)
    assert result["deflated_sharpe"] == pytest.approx(0.9004, abs=1e-3)
    assert result["variance_source"] == "trials"


def test_more_trials_raise_the_luck_threshold():
    thresholds = [expected_max_sharpe(n, 0.01) for n in (1, 2, 10, 100, 1000)]
    assert thresholds[0] == 0.0
    assert thresholds == sorted(thresholds)
    dsr = [deflated_sharpe_ratio(0.1, 500, 0.0, 3.0, n)["deflated_sharpe"] for n in (1, 10, 1000)]
    assert dsr[0] > dsr[1] > dsr[2]


def test_probabilistic_sharpe_and_moments():
    rng = np.random.default_rng(0)
    returns = rng.normal(0.001, 0.01, 20_000)
    moments = sharpe_moments(returns)
    assert moments["n"] == 20_000
    assert moments["kurtosis"] == pytest.approx(3.0, abs=0.15)
    assert moments["skew"] == pytest.approx(0.0, abs=0.1)
    assert probabilistic_sharpe_ratio(0.0, 500, 0.0, 3.0) == pytest.approx(0.5)
    assert math.isnan(sharpe_moments([0.01, 0.01, 0.01])["sharpe"])
    assert math.isnan(probabilistic_sharpe_ratio(float("nan"), 500, 0.0, 3.0))
    assert trial_sharpe_variance([0.1]) is None
    assert trial_sharpe_variance([0.1, 0.3, float("nan")]) == pytest.approx(0.02)


# --------------------------------------------------------------------------
# bundle contract
# --------------------------------------------------------------------------
def _example():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_bundle_without_research_provenance_is_valid_with_warnings():
    result = validate_bundle(_example())
    assert result.ok, result.errors
    assert any("discovery_window" in warning for warning in result.warnings)
    assert any("research_trials" in warning for warning in result.warnings)


def test_bundle_with_research_provenance_validates():
    bundle = _example()
    bundle["discovery_window"] = {"start": "2016-01-04", "end": "2022-12-30T00:00:00Z"}
    bundle["research_trials"] = 250
    result = validate_bundle(bundle)
    assert result.ok, result.errors
    assert not any("discovery_window" in warning for warning in result.warnings)
    assert discovery_window(bundle) == (
        pd.Timestamp("2016-01-04", tz="UTC"), pd.Timestamp("2022-12-30", tz="UTC")
    )
    assert declared_trials(bundle) == 250


@pytest.mark.parametrize(
    "field, value",
    [
        ("discovery_window", {"start": "2020-01-01"}),
        ("discovery_window", {"start": "2021-01-01", "end": "2020-01-01"}),
        ("discovery_window", {"start": "not a date", "end": "2020-01-01"}),
        ("discovery_window", {"start": "2020-01-01", "end": "2021-01-01", "extra": 1}),
        ("research_trials", 0),
        ("research_trials", True),
        ("research_trials", "100"),
    ],
)
def test_malformed_research_provenance_is_rejected(field, value):
    bundle = _example()
    bundle[field] = value
    assert not validate_bundle(bundle).ok


# --------------------------------------------------------------------------
# re-validation gates
# --------------------------------------------------------------------------
def _trending_bars(n_days=400, n_symbols=8, seed=7):
    rng = np.random.default_rng(seed)
    index = pd.date_range("2021-01-01", periods=n_days, freq="B", tz="UTC")
    symbols = [f"S{i:02d}" for i in range(n_symbols)]
    drift = np.linspace(-0.0006, 0.0016, n_symbols)
    rows = []
    for j, symbol in enumerate(symbols):
        prices = 100 * np.exp(np.cumsum(rng.normal(drift[j], 0.004, size=n_days)))
        volume = rng.uniform(1e6, 5e6, size=n_days)
        rows += [{"timestamp": ts, "symbol": symbol, "open": p, "high": p, "low": p,
                  "close": p, "volume": v} for ts, p, v in zip(index, prices, volume)]
    benchmark = 100 * np.exp(np.cumsum(rng.normal(0.0, 0.004, size=n_days)))
    rows += [{"timestamp": ts, "symbol": "SPY", "open": p, "high": p, "low": p,
              "close": p, "volume": 1e7} for ts, p in zip(index, benchmark)]
    return pd.DataFrame(rows), symbols


@pytest.fixture(scope="module")
def trending():
    return _trending_bars()


def _strategy():
    return CandidateStrategy(
        expression="ts_returns(close, 20)", lookback=20, expected_sign=1, quantiles=4,
        candidate_id="mom20",
    )


def _revalidate(bars, symbols, **kwargs):
    return revalidate_candidate(
        _strategy(), bars, backtest=BacktestConfig(), risk=RiskConfig(),
        tradable_symbols=symbols, context_symbols=["SPY"], candidate_id="mom20", **kwargs,
    )


def _gate(verdict, name):
    return next((g for g in verdict["gates"] if g["name"] == name), None)


def test_single_trial_reports_psr_but_does_not_gate(trending):
    verdict = _revalidate(*trending)
    assert _gate(verdict, "deflated_sharpe") is None
    test = verdict["segments"]["test"]
    assert 0.0 <= test["probabilistic_sharpe"] <= 1.0
    assert test["n_returns"] >= 30
    assert any("discovery_window" in warning for warning in verdict["warnings"])
    assert any("deflated Sharpe not gated" in warning for warning in verdict["warnings"])


def test_declared_trials_add_a_deflated_sharpe_gate(trending):
    modest = _revalidate(*trending, n_trials=5)
    huge = _revalidate(*trending, n_trials=10**9)
    assert _gate(modest, "deflated_sharpe") is not None
    assert modest["deflated_sharpe"]["n_trials"] == 5
    assert huge["deflated_sharpe"]["deflated_sharpe"] < modest["deflated_sharpe"]["deflated_sharpe"]
    assert not _gate(huge, "deflated_sharpe")["passed"]
    assert huge["verdict"] == "reject"
    assert "deflated_sharpe" in huge["reasons"]


def test_holdout_must_start_after_the_discovery_window(trending):
    bars, symbols = trending
    dates = sorted(bars["timestamp"].unique())
    before = (pd.Timestamp(dates[0]) - pd.Timedelta(days=400), pd.Timestamp(dates[0]) - pd.Timedelta(days=1))
    overlapping = (pd.Timestamp(dates[0]), pd.Timestamp(dates[-1]))

    clean = _revalidate(bars, symbols, discovery=before)
    assert _gate(clean, "holdout_after_discovery")["passed"]
    assert not any("discovery_window" in warning for warning in clean["warnings"])

    leaked = _revalidate(bars, symbols, discovery=overlapping)
    assert not _gate(leaked, "holdout_after_discovery")["passed"]
    assert leaked["verdict"] == "reject"
    assert "holdout_after_discovery" in leaked["reasons"]


def _bundle(cid, expression, symbols, **extra):
    return {
        "candidate_id": cid,
        "signal_spec": {"kind": "expression", "expression": expression, "parameters": {"expected_sign": 1}},
        "universe_assumptions": symbols,
        "lookback": 20,
        "portfolio_construction": {"quantiles": 4, "gross_exposure": 1.0},
        **extra,
    }


def test_batch_counts_discovery_trials_only_when_holdout_may_overlap(trending):
    bars, symbols = trending
    first = pd.Timestamp(sorted(bars["timestamp"].unique())[0])
    before = {"start": "2015-01-01", "end": str((first - pd.Timedelta(days=1)).date())}
    bundles = [
        _bundle("mom20", "ts_returns(close, 20)", symbols, research_trials=40),
        _bundle("mom20_clean", "ts_returns(close, 20)", symbols, research_trials=40,
                discovery_window=before),
        _bundle("vol5", "ts_returns(volume, 5)", symbols),
    ]
    out = revalidate_batch(copy.deepcopy(bundles), bars, backtest=BacktestConfig(), risk=RiskConfig())
    assert out["n_trials"] == 3  # every candidate evaluated on this holdout
    assert out["trial_sharpe_variance"] is not None  # several trials produced a Sharpe
    by_id = {row["candidate_id"]: row for row in out["candidates"]}
    # No discovery window: the 40-variant search may have seen the holdout.
    assert by_id["mom20"]["deflated_sharpe"]["n_trials"] == 40
    # Holdout provably after discovery: only evaluations on the holdout count.
    assert by_id["mom20_clean"]["deflated_sharpe"]["n_trials"] == 3
    assert "_test" not in by_id["mom20"] and "_trials" not in by_id["mom20"]

    forced = revalidate_batch(copy.deepcopy(bundles), bars, backtest=BacktestConfig(),
                              risk=RiskConfig(), n_trials=10**9)
    assert forced["n_trials"] == 10**9
    assert forced["promoted"] == []
    assert all("deflated_sharpe" in row["reasons"] for row in forced["candidates"])


def test_near_duplicate_trials_cannot_collapse_the_luck_threshold():
    from project_geld.candidates.promotion import GatePolicy, deflated_sharpe_gate

    test = {"sharpe_per_period": 0.12, "n_returns": 250, "skew": 0.0, "kurtosis": 3.0}
    alone, alone_result = deflated_sharpe_gate(test, 1000, GatePolicy())
    twin, twin_result = deflated_sharpe_gate(test, 1000, GatePolicy(), sharpe_variance=1e-6)
    assert not alone.passed
    assert not twin.passed
    assert twin_result["deflated_sharpe"] == pytest.approx(alone_result["deflated_sharpe"])


def test_single_candidate_counts_discovery_trials_unless_holdout_is_independent(trending):
    bars, symbols = trending
    dates = sorted(bars["timestamp"].unique())
    before = (pd.Timestamp(dates[0]) - pd.Timedelta(days=400), pd.Timestamp(dates[0]) - pd.Timedelta(days=1))
    unknown = _revalidate(bars, symbols, discovery_trials=50)
    independent = _revalidate(bars, symbols, discovery=before, discovery_trials=50)
    assert unknown["n_trials"] == 50 and _gate(unknown, "deflated_sharpe") is not None
    assert independent["holdout_independent_of_discovery"]
    assert independent["n_trials"] == 1 and _gate(independent, "deflated_sharpe") is None


def test_date_only_discovery_end_covers_the_whole_day():
    window = discovery_window({"discovery_window": {"start": "2020-01-01", "end": "2022-12-30"}})
    daily_bar = pd.Timestamp("2022-12-30T05:00:00Z")  # Alpaca daily bar stamp
    assert window[1] >= daily_bar
    assert window[1] < pd.Timestamp("2022-12-31T00:00:00Z")
    timed = discovery_window({"discovery_window": {"start": "2020-01-01", "end": "2022-12-30T00:00:00Z"}})
    assert timed[1] == pd.Timestamp("2022-12-30T00:00:00Z")


def test_batch_rejects_invalid_trial_counts(trending):
    bars, symbols = trending
    with pytest.raises(ValueError, match="n_trials"):
        revalidate_batch([_bundle("a", "ts_returns(close, 20)", symbols)], bars,
                         backtest=BacktestConfig(), risk=RiskConfig(), n_trials=0)
