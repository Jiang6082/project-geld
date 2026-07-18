from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from project_geld.strategies.base import TARGET_COLUMNS, close_matrix
from project_geld.strategies.daily_v4 import DailyV4


@dataclass(frozen=True)
class DailyV5(DailyV4):
    """Daily V4 with benchmark-aware weighting and causal exposure control."""

    core_trend_window: int = 200
    core_volatility_window: int = 20
    active_parameters: dict = field(
        default_factory=lambda: {
            "weighting_method": "benchmark_aware",
            "beta_penalty": 0.50,
            "score_tilt_strength": 0.20,
        }
    )
    core_target_volatility: float = 10.0
    minimum_exposure_multiplier: float = 1.0
    bearish_core_multiplier: float = 1.0
    bearish_active_multiplier: float = 1.0
    name: str = "daily_v5"

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.core_trend_window < 2 or self.core_volatility_window < 2:
            raise ValueError("Daily V5 trend and volatility windows must be at least 2.")
        if self.core_target_volatility <= 0:
            raise ValueError("core_target_volatility must be positive.")
        for value in [
            self.minimum_exposure_multiplier,
            self.bearish_core_multiplier,
            self.bearish_active_multiplier,
        ]:
            if not 0 <= value <= 1:
                raise ValueError("Daily V5 exposure multipliers must be in [0, 1].")

    @property
    def warmup_bars(self) -> int:
        return max(super().warmup_bars, self.core_trend_window + 1)

    def generate_targets(self, bars: pd.DataFrame) -> pd.DataFrame:
        base = super().generate_targets(bars)
        close = close_matrix(bars)
        core = self.core_symbol.upper()
        if core not in close:
            raise ValueError(f"{core} bars are required for Daily V5 exposure control.")
        benchmark = close[core]
        trend = benchmark.ge(
            benchmark.rolling(self.core_trend_window, min_periods=self.core_trend_window).mean()
        )
        realized_volatility = (
            benchmark.pct_change(fill_method=None)
            .rolling(
                self.core_volatility_window,
                min_periods=self.core_volatility_window,
            )
            .std(ddof=0)
            * np.sqrt(252)
        )
        volatility_multiplier = (
            self.core_target_volatility / realized_volatility.replace(0, np.nan)
        ).clip(lower=self.minimum_exposure_multiplier, upper=1.0).fillna(1.0)
        core_multiplier = volatility_multiplier.where(
            trend.fillna(True),
            volatility_multiplier * self.bearish_core_multiplier,
        )
        active_multiplier = pd.Series(1.0, index=benchmark.index).where(
            trend.fillna(True), self.bearish_active_multiplier
        )

        targets = base.copy()
        target_times = pd.DatetimeIndex(targets["timestamp"])
        core_scale = target_times.map(core_multiplier).astype(float)
        active_scale = target_times.map(active_multiplier).astype(float)
        is_core = targets["symbol"].eq(core).to_numpy()
        targets["target_weight"] = np.where(
            is_core,
            targets["target_weight"].to_numpy() * core_scale,
            targets["target_weight"].to_numpy() * active_scale,
        )
        return targets[TARGET_COLUMNS]
