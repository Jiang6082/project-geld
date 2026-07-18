from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from project_geld.strategies.base import close_matrix, ranked_long_only_targets


@dataclass(frozen=True)
class TrendStrength:
    fast_window: int = 50
    slow_window: int = 200
    momentum_lookback: int = 126
    volatility_lookback: int = 20
    top_n: int = 3
    gross_exposure: float = 0.90
    name: str = "trend"

    @property
    def warmup_bars(self) -> int:
        return max(self.slow_window, self.momentum_lookback, self.volatility_lookback) + 1

    def generate_targets(self, bars: pd.DataFrame) -> pd.DataFrame:
        close = close_matrix(bars)
        fast = close.rolling(self.fast_window).mean()
        slow = close.rolling(self.slow_window).mean()
        momentum = close / close.shift(self.momentum_lookback) - 1
        volatility = close.pct_change(fill_method=None).rolling(self.volatility_lookback).std() * np.sqrt(252)
        score = (fast / slow - 1 + momentum) / volatility.replace(0, np.nan)
        eligible = fast.gt(slow) & close.gt(slow) & momentum.gt(0)
        return ranked_long_only_targets(score, eligible, self.top_n, self.gross_exposure)
