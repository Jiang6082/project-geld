from __future__ import annotations

from itertools import product
import json
from pathlib import Path
from typing import Any

import pandas as pd

from project_geld.backtest import run_backtest
from project_geld.config import BacktestConfig, RiskConfig
from project_geld.metrics import calculate_metrics
from project_geld.strategies.registry import create_strategy


def _period_metrics(result, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, float]:
    equity = result.equity[
        result.equity["timestamp"].between(start, end, inclusive="both")
    ].copy()
    trades = result.trades[
        result.trades["timestamp"].between(start, end, inclusive="both")
    ].copy()
    if len(equity) < 2:
        return {"total_return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0}
    return calculate_metrics(equity, trades)


def grid_search(
    bars: pd.DataFrame,
    strategy_name: str,
    parameter_grid: dict[str, list[Any]],
    backtest: BacktestConfig,
    risk: RiskConfig,
    benchmark: str = "SPY",
    train_fraction: float = 0.70,
    tradable_symbols: list[str] | None = None,
    base_parameters: dict[str, Any] | None = None,
    context_symbols: list[str] | None = None,
) -> pd.DataFrame:
    if not 0.5 <= train_fraction < 1:
        raise ValueError("train_fraction must be in [0.5, 1).")
    keys = sorted(parameter_grid)
    combinations = product(*(parameter_grid[key] for key in keys))
    dates = pd.Index(sorted(pd.to_datetime(bars["timestamp"], utc=True).unique()))
    split_index = min(max(int(len(dates) * train_fraction), 1), len(dates) - 1)
    train_start, train_end = dates[0], dates[split_index - 1]
    test_start, test_end = dates[split_index], dates[-1]
    rows: list[dict] = []

    for values in combinations:
        parameters = {
            **(base_parameters or {}),
            **dict(zip(keys, values)),
        }
        strategy = create_strategy(strategy_name, parameters)
        result = run_backtest(
            bars,
            strategy,
            backtest,
            risk,
            benchmark,
            tradable_symbols,
            context_symbols,
        )
        train = _period_metrics(result, train_start, train_end)
        test = _period_metrics(result, test_start, test_end)
        rows.append(
            {
                "strategy": strategy_name,
                "parameters": json.dumps(parameters, sort_keys=True),
                "train_start": train_start,
                "train_end": train_end,
                "test_start": test_start,
                "test_end": test_end,
                "train_return": train["total_return"],
                "train_sharpe": train["sharpe"],
                "train_max_drawdown": train["max_drawdown"],
                "test_return": test["total_return"],
                "test_sharpe": test["sharpe"],
                "test_max_drawdown": test["max_drawdown"],
                "robust_score": min(train["sharpe"], test["sharpe"]),
                "orders": result.metrics["orders"],
                "annual_turnover": result.metrics["annual_turnover"],
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["robust_score", "test_sharpe"], ascending=False
    ).reset_index(drop=True)


def save_experiment(results: pd.DataFrame, output: str | Path) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(path, index=False)
    return path
