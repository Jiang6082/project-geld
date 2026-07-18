from project_geld.config import BacktestConfig, RiskConfig
from project_geld.experiments import grid_search


def test_grid_search_reports_out_of_sample_metrics(synthetic_bars):
    results = grid_search(
        synthetic_bars,
        "momentum",
        {
            "lookback": [20, 40],
            "volatility_lookback": [10],
            "top_n": [2],
            "gross_exposure": [0.8],
        },
        BacktestConfig(rebalance_every=5),
        RiskConfig(),
        train_fraction=0.7,
    )
    assert len(results) == 2
    assert {
        "train_sharpe",
        "test_sharpe",
        "robust_score",
        "annual_turnover",
    }.issubset(results)
    assert results["robust_score"].is_monotonic_decreasing
