from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class BacktestResult:
    equity: pd.DataFrame
    trades: pd.DataFrame
    targets: pd.DataFrame
    metrics: dict[str, float]


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str
    quantity: float
    reference_price: float
    notional: float
    target_weight: float
    client_order_id: str
    reason: str = "rebalance"
    limit_price: float | None = None


@dataclass(frozen=True)
class PaperCycleResult:
    orders: pd.DataFrame
    targets: pd.DataFrame
    submitted: bool
    message: str
