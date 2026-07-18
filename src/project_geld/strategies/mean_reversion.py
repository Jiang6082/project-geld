from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from project_geld.strategies.base import close_matrix, ranked_long_only_targets


@dataclass(frozen=True)
class LongOnlyMeanReversion:
    reversal_lookback: int = 5
    regime_window: int = 100
    volatility_lookback: int = 20
    top_n: int = 3
    gross_exposure: float = 0.75
    name: str = "mean_reversion"

    @property
    def warmup_bars(self) -> int:
        return max(self.regime_window, self.volatility_lookback) + self.reversal_lookback

    def generate_targets(self, bars: pd.DataFrame) -> pd.DataFrame:
        close = close_matrix(bars)
        short_return = close / close.shift(self.reversal_lookback) - 1
        volatility = close.pct_change(fill_method=None).rolling(self.volatility_lookback).std() * np.sqrt(252)
        score = -short_return / volatility.replace(0, np.nan)
        regime = close.gt(close.rolling(self.regime_window).mean())
        eligible = short_return.lt(0) & regime
        return ranked_long_only_targets(score, eligible, self.top_n, self.gross_exposure)
