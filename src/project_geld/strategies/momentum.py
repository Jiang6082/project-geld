from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from project_geld.strategies.base import close_matrix, ranked_long_only_targets


@dataclass(frozen=True)
class CrossSectionalMomentum:
    lookback: int = 126
    volatility_lookback: int = 20
    top_n: int = 3
    gross_exposure: float = 0.90
    name: str = "momentum"

    @property
    def warmup_bars(self) -> int:
        return max(self.lookback, self.volatility_lookback) + 1

    def generate_targets(self, bars: pd.DataFrame) -> pd.DataFrame:
        close = close_matrix(bars)
        returns = close.pct_change(fill_method=None)
        momentum = close / close.shift(self.lookback) - 1
        volatility = returns.rolling(self.volatility_lookback).std() * np.sqrt(252)
        score = momentum / volatility.replace(0, np.nan)
        return ranked_long_only_targets(
            score=score,
            eligible=momentum.gt(0),
            top_n=self.top_n,
            gross_exposure=self.gross_exposure,
        )
