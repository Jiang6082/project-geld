"""Step 1 gate: safe causal evaluator + candidate strategy.

Covers evaluator correctness, a numeric cross-check against Emberforge's REAL
parser and operators (skipped when the sibling repo is absent), structural and
data-driven causality, target validity, and position-sizing caps. The final test
is the mission GATE: import the example bundle -> build strategy -> targets.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from project_geld.candidates.dsl import (
    CausalityError,
    ValidationError,
    compute_factor,
    evaluate,
    panels_from_bars,
    parse,
    validate,
)
from project_geld.candidates.dsl.parser import ParseError
from project_geld.strategies.candidate import CandidateStrategy, strategy_from_quarantine
from project_geld.strategies.registry import create_strategy

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "schemas" / "example_candidate_bundle.json"


def _emberforge_src() -> Path | None:
    """Locate project-emberforge's ``src`` for the numeric cross-check.

    Honours ``GELD_EMBERFORGE_SRC`` first, then the sibling-repo convention.
    Returns ``None`` (test skips) when it cannot be found -- e.g. in CI, where
    the upstream research repo is intentionally absent.
    """
    env = os.environ.get("GELD_EMBERFORGE_SRC")
    candidates = [Path(env)] if env else []
    candidates.append(REPO.parent / "project-emberforge" / "src")
    for path in candidates:
        if path.exists():
            return path
    return None


# --------------------------------------------------------------------------
# synthetic panels / bars
# --------------------------------------------------------------------------
def _panels(n_days: int = 40, symbols=("A", "B", "C", "D", "E"), seed: int = 3):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-01", periods=n_days, freq="B", tz="UTC")
    steps = rng.normal(0.001, 0.02, size=(n_days, len(symbols)))
    close = pd.DataFrame(100 * np.exp(np.cumsum(steps, axis=0)), index=idx, columns=list(symbols))
    volume = pd.DataFrame(rng.uniform(1e6, 5e6, size=(n_days, len(symbols))), index=idx, columns=list(symbols))
    return {"close": close, "volume": volume}


def _bars_from_panels(panels) -> pd.DataFrame:
    close = panels["close"]
    volume = panels["volume"]
    rows = []
    for ts in close.index:
        for sym in close.columns:
            price = float(close.at[ts, sym])
            rows.append(
                {
                    "timestamp": ts,
                    "symbol": sym,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": float(volume.at[ts, sym]),
                }
            )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# parser + validation
# --------------------------------------------------------------------------
def test_parse_desugars_binops():
    node = parse("close / ts_mean(close, 5) - 1")
    # subtract(divide(close, ts_mean(close,5)), 1)
    assert node.op == "subtract"
    assert node.args[0].op == "divide"


def test_unknown_operator_rejected():
    with pytest.raises(ValidationError):
        validate(parse("wibble(close, 5)"))


def test_forbidden_future_operator_rejected():
    with pytest.raises((ValidationError, CausalityError)):
        validate(parse("future_return(close, 5)"))


def test_negative_window_rejected_as_lookahead():
    node = parse("ts_returns(close, 5)")
    # hand-build a negative lag which the parser cannot produce directly
    from project_geld.candidates.dsl.nodes import Call, Const, Field

    bad = Call("ts_delay", (Field("close"), Const(-3.0)))
    with pytest.raises(CausalityError):
        validate(bad)
    validate(node)  # the good one is fine


def test_bare_non_field_identifier_rejected():
    with pytest.raises(ParseError):
        parse("foo")


# --------------------------------------------------------------------------
# evaluator correctness (hand-computed)
# --------------------------------------------------------------------------
def test_ts_returns_matches_pandas():
    panels = _panels()
    out = evaluate(parse("ts_returns(close, 3)"), panels)
    expected = panels["close"] / panels["close"].shift(3) - 1.0
    pd.testing.assert_frame_equal(out, expected)


def test_divide_by_zero_is_nan():
    panels = _panels()
    panels = {**panels, "volume": panels["volume"] * 0.0}
    out = evaluate(parse("divide(close, volume)"), panels)
    assert out.isna().all().all()


def test_cs_rank_within_row():
    panels = _panels()
    out = evaluate(parse("cs_rank(close)"), panels)
    expected = panels["close"].rank(axis=1)
    pd.testing.assert_frame_equal(out, expected)


def test_compute_factor_normalizes_cross_section():
    panels = _panels()
    scores = compute_factor(parse("ts_returns(close, 5)"), panels)
    # after z-score, each fully-covered row is ~mean 0
    row = scores.dropna(how="all").iloc[-1]
    assert abs(float(row.mean())) < 1e-9


# --------------------------------------------------------------------------
# cross-check against Emberforge's REAL parser + operators
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "expr",
    [
        "ts_returns(close, 5)",
        "neg(ts_std(ts_returns(close, 1), 10))",
        "cs_rank(divide(volume, ts_mean(volume, 20)))",
        "close / ts_mean(close, 5) - 1",
        "cs_zscore(ts_delta(close, 3))",
        "signed_power(ts_returns(close, 10), 2)",
    ],
)
def test_matches_emberforge(expr):
    ef_src = _emberforge_src()
    if ef_src is None:
        pytest.skip("project-emberforge src not available (set GELD_EMBERFORGE_SRC)")
    sys.path.insert(0, str(ef_src))
    try:
        from emberforge.dsl import nodes as efn
        from emberforge.dsl import operators as efo
        from emberforge.dsl import parser as efp
    except Exception:  # pragma: no cover - environment dependent
        pytest.skip("emberforge.dsl not importable")
    finally:
        pass

    def ef_eval(node, panels):
        if isinstance(node, efn.Field):
            return panels[node.name]
        if isinstance(node, efn.Const):
            return node.value
        return efo.get(node.op).fn(*[ef_eval(a, panels) for a in node.args])

    panels = _panels()
    ours = evaluate(parse(expr), panels)
    theirs = ef_eval(efp.parse(expr), panels)
    theirs = theirs.reindex(index=ours.index, columns=ours.columns)
    pd.testing.assert_frame_equal(ours, theirs, check_dtype=False)


# --------------------------------------------------------------------------
# causality: perturbing the future must not change the past
# --------------------------------------------------------------------------
def test_scores_are_causal_under_future_perturbation():
    panels = _panels()
    bars = _bars_from_panels(panels)
    strat = CandidateStrategy(expression="ts_returns(close, 5)", lookback=5)
    targets = strat.generate_targets(bars)

    cut = int(len(panels["close"]) * 0.6)
    cut_ts = panels["close"].index[cut]
    perturbed = panels["close"].copy()
    perturbed.iloc[cut:] *= 2.0
    pert_panels = {**panels, "close": perturbed}
    pert_bars = _bars_from_panels(pert_panels)
    pert_targets = strat.generate_targets(pert_bars)

    before = targets[targets["timestamp"] < cut_ts].reset_index(drop=True)
    pert_before = pert_targets[pert_targets["timestamp"] < cut_ts].reset_index(drop=True)
    pd.testing.assert_frame_equal(before, pert_before)


# --------------------------------------------------------------------------
# target validity + sizing caps
# --------------------------------------------------------------------------
def test_targets_schema_and_long_only():
    panels = _panels()
    bars = _bars_from_panels(panels)
    strat = CandidateStrategy(expression="ts_returns(close, 5)", lookback=5, gross_exposure=0.9)
    targets = strat.generate_targets(bars)
    assert list(targets.columns) == ["timestamp", "symbol", "target_weight", "score"]
    assert (targets["target_weight"] >= 0).all()  # long-only default
    gross = targets.groupby("timestamp")["target_weight"].sum()
    assert gross.max() <= 0.9 + 1e-9


def test_position_weight_cap_respected():
    panels = _panels(symbols=("A", "B", "C", "D", "E", "F", "G", "H", "I", "J"))
    bars = _bars_from_panels(panels)
    strat = CandidateStrategy(
        expression="ts_returns(close, 5)",
        lookback=5,
        quantiles=2,  # top half -> several names
        gross_exposure=1.0,
        max_position_weight=0.10,
    )
    targets = strat.generate_targets(bars)
    assert targets["target_weight"].max() <= 0.10 + 1e-9


def test_expected_sign_inverts_selection():
    panels = _panels()
    bars = _bars_from_panels(panels)
    up = CandidateStrategy(expression="ts_returns(close, 5)", lookback=5, expected_sign=1)
    down = CandidateStrategy(expression="ts_returns(close, 5)", lookback=5, expected_sign=-1)
    su = up.scores(bars).dropna(how="all").iloc[-1]
    sd = down.scores(bars).dropna(how="all").iloc[-1]
    pd.testing.assert_series_equal(su, -sd)


# --------------------------------------------------------------------------
# GATE: example bundle -> strategy -> targets, no error
# --------------------------------------------------------------------------
def test_gate_example_bundle_to_targets():
    bundle = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    strat = CandidateStrategy.from_bundle(bundle)
    assert strat.candidate_id == "momentum_20"
    assert strat.lookback == 20

    # bundle universe is SYM00..SYM04; synthesise bars for them.
    panels = _panels(n_days=60, symbols=tuple(bundle["universe_assumptions"]))
    bars = _bars_from_panels(panels)
    targets = strat.generate_targets(bars)
    assert not targets.empty
    assert list(targets.columns) == ["timestamp", "symbol", "target_weight", "score"]
    # after warmup, at least one row selects a name
    assert targets["target_weight"].gt(0).any()


def test_strategy_from_quarantine_record():
    bundle = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    record = {"state": "quarantined", "bundle": bundle}
    strat = strategy_from_quarantine(record)
    assert isinstance(strat, CandidateStrategy)
    assert strat.candidate_id == "momentum_20"


def test_registry_exposes_candidate():
    strat = create_strategy("candidate", {"expression": "ts_returns(close, 5)", "lookback": 5})
    assert isinstance(strat, CandidateStrategy)
