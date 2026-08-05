"""Step 5 gate: bind universe_assumptions to a concrete PIT universe.

Binding yields the expected survivorship-aware symbol set, fails closed when it
cannot resolve, and — crucially — the candidate's cross-sectional scoring is
computed only over the bound universe, so names outside it are never ranked or
selected.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from project_geld.candidates.universe import bind_strategy, bind_universe
from project_geld.strategies.candidate import CandidateStrategy


def _bars(symbols, drifts, n=80, seed=5, noise=0.002):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-01", periods=n, freq="B", tz="UTC")
    rows = []
    for sym, mu in zip(symbols, drifts):
        prices = 100 * np.exp(np.cumsum(rng.normal(mu, noise, size=n)))
        for ts, price in zip(idx, prices):
            rows.append({"timestamp": ts, "symbol": sym, "open": float(price), "high": float(price),
                         "low": float(price), "close": float(price), "volume": 1e6})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# binding resolves the expected symbol set
# --------------------------------------------------------------------------
def test_label_binds_to_broad_pit():
    bars = _bars(["AAA", "BBB", "CCC", "SPY"], [0.0, 0.0005, 0.001, 0.0])
    binding = bind_universe({"universe_assumptions": "research-only"}, bars, benchmark="SPY")
    assert binding.ok and binding.source == "research_broad_pit"
    assert set(binding.symbols) == {"AAA", "BBB", "CCC"}


def test_config_override_wins():
    bars = _bars(["AAA", "BBB", "CCC", "SPY"], [0.0, 0.0005, 0.001, 0.0])
    binding = bind_universe(
        {"universe_assumptions": "research-only"}, bars, benchmark="SPY",
        config_symbols=["AAA", "BBB"],
    )
    assert binding.ok and binding.source == "config"
    assert set(binding.symbols) == {"AAA", "BBB"}


def test_max_symbols_caps_by_liquidity_order():
    # BBB has the most bars -> most liquid -> kept first.
    bars = pd.concat([
        _bars(["AAA"], [0.0], n=40),
        _bars(["BBB"], [0.0], n=80),
        _bars(["CCC"], [0.0], n=60),
    ], ignore_index=True)
    binding = bind_universe({"universe_assumptions": "research-only"}, bars, benchmark="SPY", max_symbols=2)
    assert binding.symbols[0] == "BBB"
    assert len(binding.symbols) == 2


def test_unbindable_config_flags_not_ok():
    bars = _bars(["AAA", "BBB"], [0.0, 0.0])
    binding = bind_universe({"universe_assumptions": "research-only"}, bars,
                            benchmark="SPY", config_symbols=["ZZZ"])
    assert not binding.ok


def test_bind_strategy_raises_when_unbindable():
    empty = pd.DataFrame(columns=["timestamp", "symbol", "open", "high", "low", "close", "volume"])
    with pytest.raises(ValueError):
        bind_strategy({"signal_spec": {"kind": "expression", "expression": "ts_returns(close,5)"},
                       "universe_assumptions": "research-only", "lookback": 5}, empty)


# --------------------------------------------------------------------------
# evaluation is restricted to the bound universe
# --------------------------------------------------------------------------
def test_scoring_ignores_symbols_outside_bound_universe():
    # D has the strongest momentum but is OUTSIDE the bound universe {A,B,C}.
    bars = _bars(["A", "B", "C", "D"], [-0.0004, 0.0002, 0.0008, 0.0030])

    bound = CandidateStrategy(expression="ts_returns(close, 20)", lookback=20,
                              expected_sign=1, quantiles=4, universe=("A", "B", "C"))
    targets = bound.generate_targets(bars)
    d_rows = targets[targets["symbol"] == "D"]
    assert (d_rows["target_weight"] == 0).all()          # never traded
    assert d_rows["score"].isna().all()                  # never scored

    # among the bound names the top momentum (C) is selected on the last bar.
    last_ts = targets["timestamp"].max()
    last = targets[targets["timestamp"] == last_ts].set_index("symbol")["target_weight"]
    assert last["C"] > 0 and last["A"] == 0

    # an UNBOUND strategy would instead pick the out-of-universe winner D.
    unbound = CandidateStrategy(expression="ts_returns(close, 20)", lookback=20,
                                expected_sign=1, quantiles=4)
    u_last = unbound.generate_targets(bars)
    u_last = u_last[u_last["timestamp"] == last_ts].set_index("symbol")["target_weight"]
    assert u_last["D"] > 0


def test_bound_strategy_from_bundle_binds_and_evaluates():
    bars = _bars(["A", "B", "C", "SPY"], [0.0, 0.0005, 0.0012, 0.0])
    bundle = {
        "signal_spec": {"kind": "expression", "expression": "ts_returns(close, 20)",
                        "parameters": {"expected_sign": 1}},
        "universe_assumptions": ["SYM00", "SYM01"],  # synthetic -> broad PIT
        "lookback": 20,
        "candidate_id": "mom20",
        "portfolio_construction": {"quantiles": 3, "gross_exposure": 1.0},
    }
    strat, binding = bind_strategy(bundle, bars, benchmark="SPY")
    assert set(binding.symbols) == {"A", "B", "C"}
    assert strat.universe == ("A", "B", "C")
    targets = strat.generate_targets(bars)
    assert targets[targets["symbol"] == "SPY"]["target_weight"].eq(0).all()
    assert targets["target_weight"].gt(0).any()
