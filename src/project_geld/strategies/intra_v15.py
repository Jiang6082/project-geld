from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from project_geld.strategies.base import TARGET_COLUMNS, close_matrix
from project_geld.strategies.intra_v13 import IntraV13
from project_geld.strategies.intra_v7 import _clock


@dataclass
class IntraV15(IntraV13):
    """Daily liquid-index trend sleeve combined with the selective V13 overlay.

    Version history (major = strategy generation, patch = applied improvement):
    - 15.0.1: minimum-trade floor lowered below the weak-signal base leg so it
      executes as designed (active-session rate 94% -> 99.7%, PnL-neutral).
    - 15.0.2: per-cycle implementation-shortfall tracking for the cost-sensitive
      SPY sleeve.
    - 15.0.3: universe-staleness guard added to the intraday paper path.
    - 15.0.4: continuous confidence sizing for the base sleeve (total return
      3.26% -> 3.45%, Sharpe 0.82 -> 0.88, lower turnover and drawdown).
    - 15.0.5: automatic shortfall kill-switch — the paper path flattens the base
      sleeve when its trailing implementation shortfall breaches ~2 bps.
    """

    core_symbol: str = "SPY"
    base_signal_time: str = "10:30"
    base_flatten_at: str = "15:30"
    base_long_weight: float = 0.05
    base_short_weight: float = 0.025
    base_min_signal_bps: float = 5.0
    base_weak_weight: float = 0.005
    # When set, the base sleeve sizes continuously with signal strength: it ramps
    # from the weak floor at base_min_signal_bps up to the full long/short weight
    # at base_saturation_bps, instead of stepping straight to full weight. None
    # keeps the original two-tier step behavior.
    base_saturation_bps: float | None = None
    base_weight: float | None = None
    name: str = "intra_v15"
    version: str = "Intra V15.0.5"

    def __post_init__(self) -> None:
        super().__post_init__()
        self.core_symbol = self.core_symbol.upper()
        if self.core_symbol != self.benchmark_symbol:
            raise ValueError("core_symbol must match benchmark_symbol.")
        if self.base_weight is not None:
            if not 0 < self.base_weight <= 1:
                raise ValueError("base_weight must be in (0, 1].")
            self.base_long_weight = self.base_weight
            self.base_short_weight = self.base_weight
        for field, value in [
            ("base_long_weight", self.base_long_weight),
            ("base_short_weight", self.base_short_weight),
            ("base_weak_weight", self.base_weak_weight),
        ]:
            if not 0 <= value <= 1:
                raise ValueError(f"{field} must be in [0, 1].")
        if self.base_long_weight == 0 or self.base_short_weight == 0:
            raise ValueError("base long and short weights must be positive.")
        if self.base_min_signal_bps < 0:
            raise ValueError("base_min_signal_bps cannot be negative.")
        if (
            self.base_saturation_bps is not None
            and self.base_saturation_bps <= self.base_min_signal_bps
        ):
            raise ValueError("base_saturation_bps must exceed base_min_signal_bps.")
        if _clock(self.base_signal_time) >= _clock(self.base_flatten_at):
            raise ValueError("base_signal_time must precede base_flatten_at.")
        if _clock(self.base_flatten_at) > _clock(self.flatten_at):
            raise ValueError("base_flatten_at cannot follow overlay flatten_at.")

    def generate_targets(self, bars: pd.DataFrame) -> pd.DataFrame:
        overlay = super().generate_targets(bars)
        if bars.empty:
            return overlay
        close = close_matrix(bars)
        if self.core_symbol not in close:
            raise ValueError(f"{self.core_symbol} bars are required for the base sleeve.")
        open_ = (
            bars.pivot(index="timestamp", columns="symbol", values="open")
            .sort_index()
            .reindex_like(close)
        )
        local_index = close.index.tz_convert(self.timezone)
        sessions = pd.Series(local_index.date, index=close.index)
        first_open = open_[self.core_symbol].groupby(sessions).transform("first")
        opening_return = close[self.core_symbol].div(first_open).sub(1.0)

        records: list[dict] = []
        for _, session_index in close.groupby(sessions).groups.items():
            direction = 0.0
            for timestamp in pd.DatetimeIndex(session_index):
                local_time = timestamp.tz_convert(self.timezone).time().replace(
                    tzinfo=None
                )
                score = opening_return.at[timestamp]
                if local_time == _clock(self.base_signal_time) and pd.notna(score):
                    sign = float(np.sign(score))
                    magnitude_bps = abs(float(score)) * 10_000
                    full = (
                        self.base_long_weight if sign > 0 else self.base_short_weight
                    )
                    if sign == 0.0:
                        direction = 0.0
                    elif self.base_saturation_bps is None:
                        direction = (
                            sign * full
                            if magnitude_bps >= self.base_min_signal_bps
                            else sign * self.base_weak_weight
                        )
                    elif magnitude_bps <= self.base_min_signal_bps:
                        direction = sign * self.base_weak_weight
                    else:
                        span = self.base_saturation_bps - self.base_min_signal_bps
                        ramp = min(
                            (magnitude_bps - self.base_min_signal_bps) / span, 1.0
                        )
                        magnitude = self.base_weak_weight + ramp * (
                            full - self.base_weak_weight
                        )
                        direction = sign * magnitude
                if local_time >= _clock(self.base_flatten_at):
                    direction = 0.0
                records.append(
                    {
                        "timestamp": timestamp,
                        "symbol": self.core_symbol,
                        "target_weight": direction,
                        "score": float(score) if pd.notna(score) else float("nan"),
                    }
                )
        base = pd.DataFrame.from_records(records, columns=TARGET_COLUMNS)
        return pd.concat([overlay, base], ignore_index=True)
