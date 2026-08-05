"""Multiple-testing control for batch OOS re-validation.

Per-candidate gates aren't enough: run enough candidates and one passes by luck.
These tests pin the Sharpe-to-p-value mapping, the Benjamini-Hochberg FDR
procedure (including that multiplicity rejects a lone "significant" result), and
the batch wiring that promotes a genuine signal while rejecting noise.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from project_geld.candidates.batch import benjamini_hochberg, revalidate_batch, sharpe_pvalue
from project_geld.config import BacktestConfig, RiskConfig


def test_sharpe_pvalue_monotonic_and_bounded():
    assert sharpe_pvalue(2.5, 150) < sharpe_pvalue(0.4, 150)   # stronger -> smaller p
    assert sharpe_pvalue(1.0, 300) < sharpe_pvalue(1.0, 30)    # more data -> smaller p
    assert sharpe_pvalue(0.0, 150) == 0.5                       # no edge -> p=0.5
    assert sharpe_pvalue(1.0, 1) == 1.0                         # too little data -> no claim


def test_benjamini_hochberg_known_case():
    # classic monotone set: with q=0.25 the two smallest survive.
    p = [0.005, 0.02, 0.5, 0.9]
    assert benjamini_hochberg(p, q=0.25) == [True, True, False, False]


def test_multiplicity_rejects_lone_significant():
    # p=0.04 is "significant at 0.05" on its own...
    assert benjamini_hochberg([0.04], q=0.10) == [True]
    # ...but among 10 tests where the rest are null, FDR rejects it.
    assert benjamini_hochberg([0.04] + [0.9] * 9, q=0.10) == [False] * 10


def _batch_bars(n_days=760, n_symbols=10, seed=7, spread=0.0016):
    """Drift symmetric around zero -> random selection has ~0 edge, so only a
    real cross-sectional signal (momentum) survives."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2019-01-01", periods=n_days, freq="B", tz="UTC")
    drift = np.linspace(-spread, spread, n_symbols)
    rows = []
    for j in range(n_symbols):
        prices = 100 * np.exp(np.cumsum(rng.normal(drift[j], 0.003, size=n_days)))
        vol = rng.uniform(1e6, 5e6, size=n_days)
        for t, (p, v) in enumerate(zip(prices, vol)):
            rows.append({"timestamp": idx[t], "symbol": f"S{j:02d}", "open": p, "high": p,
                         "low": p, "close": p, "volume": v})
    return pd.DataFrame(rows), [f"S{j:02d}" for j in range(n_symbols)]


def _bundle(cid, expr, symbols):
    return {
        "candidate_id": cid,
        "signal_spec": {"kind": "expression", "expression": expr, "parameters": {"expected_sign": 1}},
        "universe_assumptions": symbols,
        "lookback": 20,
        "portfolio_construction": {"quantiles": 5, "gross_exposure": 1.0},
    }


def test_batch_promotes_signal_rejects_noise():
    bars, syms = _batch_bars()
    bundles = [
        _bundle("mom20", "ts_returns(close, 20)", syms),      # real momentum
        _bundle("vol5", "ts_returns(volume, 5)", syms),       # noise (random volume)
        _bundle("volrank", "cs_rank(volume)", syms),          # noise
        _bundle("voldelta", "ts_delta(volume, 10)", syms),    # noise
    ]
    out = revalidate_batch(bundles, bars, backtest=BacktestConfig(), risk=RiskConfig(), fdr_q=0.10)
    assert out["promoted"] == ["mom20"]
    by_id = {r["candidate_id"]: r for r in out["candidates"]}
    assert by_id["mom20"]["verdict"] == "promote_to_shadow"
    for noise in ("vol5", "volrank", "voldelta"):
        assert by_id[noise]["verdict"] == "reject"


def test_batch_of_pure_noise_promotes_none():
    bars, syms = _batch_bars()
    bundles = [
        _bundle("vol5", "ts_returns(volume, 5)", syms),
        _bundle("volrank", "cs_rank(volume)", syms),
        _bundle("voldelta", "ts_delta(volume, 10)", syms),
    ]
    out = revalidate_batch(bundles, bars, backtest=BacktestConfig(), risk=RiskConfig(), fdr_q=0.10)
    assert out["promoted"] == []
    assert out["n_promoted"] == 0
