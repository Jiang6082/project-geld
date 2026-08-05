"""Step 2 gate: independent OOS re-validation, gate policy, state machine.

Covers the pure gate logic, the persisted lifecycle state machine, universe
binding, and the mission GATE: run a candidate through Geld's own backtester on
out-of-sample data and get a machine-readable promote/reject verdict.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from project_geld.candidates import state as st
from project_geld.candidates.promotion import GatePolicy, evaluate_gates, revalidate_candidate
from project_geld.candidates.universe import bind_universe
from project_geld.config import BacktestConfig, RiskConfig
from project_geld.strategies.candidate import CandidateStrategy


# --------------------------------------------------------------------------
# gate logic (pure)
# --------------------------------------------------------------------------
def _m(total_return=0.2, sharpe=1.0, max_drawdown=-0.1):
    return {"total_return": total_return, "sharpe": sharpe, "max_drawdown": max_drawdown}


def test_all_gates_pass():
    gates = evaluate_gates(
        test=_m(), val=_m(sharpe=0.6), test_stressed=_m(total_return=0.1),
        annual_turnover=10.0, policy=GatePolicy(),
    )
    assert all(g.passed for g in gates)
    assert {g.name for g in gates} >= {
        "net_return_positive", "oos_sharpe", "stability", "max_drawdown",
        "turnover_capacity", "cost_stress",
    }


def test_negative_holdout_return_fails():
    gates = evaluate_gates(
        test=_m(total_return=-0.05, sharpe=-0.2), val=_m(), test_stressed=_m(),
        annual_turnover=10.0, policy=GatePolicy(),
    )
    failed = {g.name for g in gates if not g.passed}
    assert "net_return_positive" in failed


def test_cost_stress_gate_fails_when_stressed_negative():
    gates = evaluate_gates(
        test=_m(), val=_m(), test_stressed=_m(total_return=-0.02),
        annual_turnover=10.0, policy=GatePolicy(),
    )
    assert {g.name for g in gates if not g.passed} == {"cost_stress"}


def test_turnover_capacity_gate():
    gates = evaluate_gates(
        test=_m(), val=_m(), test_stressed=_m(),
        annual_turnover=999.0, policy=GatePolicy(),
    )
    assert not next(g for g in gates if g.name == "turnover_capacity").passed


# --------------------------------------------------------------------------
# state machine
# --------------------------------------------------------------------------
def test_forward_transitions_and_history():
    rec = {"state": "quarantined", "paper_enabled": False}
    st.advance(rec, "validated_oos", reason="oos ok")
    st.advance(rec, "shadow", reason="gates pass")
    assert rec["state"] == "shadow"
    assert [h["to"] for h in rec["history"]] == ["validated_oos", "shadow"]
    assert rec["paper_enabled"] is False


def test_illegal_transition_rejected():
    rec = {"state": "quarantined"}
    with pytest.raises(st.StateError):
        st.advance(rec, "paper", reason="skip ahead")


def test_paper_requires_manual():
    rec = {"state": "shadow", "paper_enabled": False}
    with pytest.raises(st.StateError):
        st.advance(rec, "paper", reason="auto")  # manual=False -> blocked
    st.advance(rec, "paper", reason="human sign-off", manual=True)
    assert rec["state"] == "paper"
    assert rec["paper_enabled"] is True


def test_state_persistence_roundtrip(tmp_path):
    path = tmp_path / "cand.json"
    st.save_record(path, {"state": "quarantined", "bundle": {"candidate_id": "x"}})
    st.advance_file(path, "validated_oos", reason="oos ok")
    st.advance_file(path, "rejected", reason="gate fail")
    reloaded = st.load_record(path)
    assert reloaded["state"] == "rejected"
    assert len(reloaded["history"]) == 2


# --------------------------------------------------------------------------
# universe binding
# --------------------------------------------------------------------------
def _real_bars(symbols, n=40, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-01", periods=n, freq="B", tz="UTC")
    rows = []
    for j, sym in enumerate(symbols):
        price = 100.0
        for ts in idx:
            price *= float(np.exp(rng.normal(0.0005 * j, 0.01)))
            rows.append({"timestamp": ts, "symbol": sym, "open": price, "high": price,
                         "low": price, "close": price, "volume": 1e6})
    return pd.DataFrame(rows)


def test_binding_synthetic_falls_back_to_broad_pit():
    bars = _real_bars(["AAA", "BBB", "CCC", "SPY"])
    binding = bind_universe({"universe_assumptions": ["SYM00", "SYM01"]}, bars, benchmark="SPY")
    assert binding.ok
    assert binding.source == "research_broad_pit"
    assert set(binding.symbols) == {"AAA", "BBB", "CCC"}  # benchmark excluded


def test_binding_real_symbol_list_intersects():
    bars = _real_bars(["AAA", "BBB", "CCC", "SPY"])
    binding = bind_universe({"universe_assumptions": ["AAA", "CCC", "ZZZ"]}, bars, benchmark="SPY")
    assert binding.ok and set(binding.symbols) == {"AAA", "CCC"}
    assert binding.source == "bundle_symbol_list"


def test_binding_empty_bars_unbound():
    empty = pd.DataFrame(columns=["timestamp", "symbol", "open", "high", "low", "close", "volume"])
    binding = bind_universe({"universe_assumptions": "research-only"}, empty, benchmark="SPY")
    assert not binding.ok


# --------------------------------------------------------------------------
# end-to-end OOS re-validation (the GATE)
# --------------------------------------------------------------------------
def _trending_bars(n_days=160, n_symbols=8, seed=7):
    """Symbols with monotonically increasing drift -> a genuine, persistent
    cross-sectional momentum signal that should survive OOS confirmation."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-01", periods=n_days, freq="B", tz="UTC")
    symbols = [f"S{i:02d}" for i in range(n_symbols)]
    drift = np.linspace(-0.0006, 0.0016, n_symbols)
    rows = []
    for j, sym in enumerate(symbols):
        steps = rng.normal(drift[j], 0.004, size=n_days)
        prices = 100 * np.exp(np.cumsum(steps))
        for ts, price in zip(idx, prices):
            rows.append({"timestamp": ts, "symbol": sym, "open": float(price), "high": float(price),
                         "low": float(price), "close": float(price), "volume": 1e6})
    return pd.DataFrame(rows), symbols


def test_revalidation_promotes_a_real_signal():
    bars, symbols = _trending_bars()
    strat = CandidateStrategy(
        expression="ts_returns(close, 20)", lookback=20, expected_sign=1, quantiles=4,
        candidate_id="mom20",
    )
    verdict = revalidate_candidate(
        strat, bars, backtest=BacktestConfig(), risk=RiskConfig(),
        tradable_symbols=symbols, candidate_id="mom20",
    )
    assert verdict["verdict"] == "promote_to_shadow", verdict["reasons"]
    assert verdict["candidate_id"] == "mom20"
    assert {"train", "val", "test"} <= set(verdict["segments"])
    assert len(verdict["gates"]) >= 6


def test_revalidation_rejects_inverted_sign():
    bars, symbols = _trending_bars()
    # Longing the LOW-drift names (wrong sign) should not survive OOS gates.
    strat = CandidateStrategy(
        expression="ts_returns(close, 20)", lookback=20, expected_sign=-1, quantiles=4,
        candidate_id="mom20_inv",
    )
    verdict = revalidate_candidate(
        strat, bars, backtest=BacktestConfig(), risk=RiskConfig(),
        tradable_symbols=symbols, candidate_id="mom20_inv",
    )
    assert verdict["verdict"] == "reject"
