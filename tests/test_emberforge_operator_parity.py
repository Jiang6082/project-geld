"""Optional cross-repository contract check; no production import of Emberforge."""

import numpy as np
import pandas as pd
import pytest

from project_geld.candidates.dsl import compute_factor, parse
from project_geld.candidates.dsl import operators


@pytest.mark.parametrize("expression", ["ts_sum(close,5)", "ts_skew(close,5)", "ts_zscore(close,5)",
    "ts_argmax(close,5)", "ts_argmin(close,5)", "ts_mean(cs_rank(close),3)"])
def test_evaluator_matches_emberforge(expression):
    emberforge = pytest.importorskip("emberforge.compute")
    from emberforge.data.schema import DatasetMetadata, MarketData
    from emberforge.dsl import make_factor
    from emberforge.dsl.operators import REGISTRY
    assert set(operators.REGISTRY) == set(REGISTRY)
    rng = np.random.default_rng(10)
    close = pd.DataFrame(rng.normal(100, 4, (25, 6)),
                         index=pd.date_range("2020-01-01", periods=25, tz="UTC"), columns=list("ABCDEF"))
    eligibility = pd.DataFrame(True, index=close.index, columns=close.columns)
    eligibility["F"] = False
    data = MarketData({"close": close}, DatasetMetadata(source="parity"))
    expected = emberforge.compute_factor(make_factor("parity", expression), data, eligibility=eligibility)
    actual = compute_factor(parse(expression), {"close": close}, eligibility=eligibility)
    pd.testing.assert_frame_equal(actual, expected)
