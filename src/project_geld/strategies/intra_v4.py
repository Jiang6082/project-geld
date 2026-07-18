from __future__ import annotations

from dataclasses import dataclass
from datetime import time

import numpy as np
import pandas as pd

from project_geld.strategies.base import TARGET_COLUMNS, close_matrix


def _clock(value: str) -> time:
    return time.fromisoformat(value)


@dataclass
class IntraV4:
    """Once-daily relative continuation with diversified, capped exposure."""

    benchmark_symbol: str = "SPY"
    lookback_bars: int = 2
    top_n: int = 8
    gross_exposure: float = 0.80
    max_position_weight: float = 0.10
    min_bar_dollar_volume: float = 1_000_000.0
    min_relative_outperformance: float = 0.006
    entry_time: str = "10:30"
    flatten_at: str = "15:45"
    require_benchmark_above_vwap: bool = True
    timezone: str = "America/New_York"
    name: str = "intra_v4"

    def __post_init__(self) -> None:
        self.benchmark_symbol = self.benchmark_symbol.upper()
        if self.lookback_bars < 1 or self.top_n < 1:
            raise ValueError("lookback_bars and top_n must be positive.")
        if not 0 < self.gross_exposure <= 1:
            raise ValueError("gross_exposure must be in (0, 1].")
        if not 0 < self.max_position_weight <= 1:
            raise ValueError("max_position_weight must be in (0, 1].")
        if self.top_n * self.max_position_weight + 1e-12 < self.gross_exposure:
            raise ValueError("top_n times max_position_weight cannot fund gross_exposure.")
        if self.min_relative_outperformance < 0:
            raise ValueError("min_relative_outperformance cannot be negative.")
        if _clock(self.entry_time) >= _clock(self.flatten_at):
            raise ValueError("entry_time must precede flatten_at.")

    @property
    def warmup_bars(self) -> int:
        return self.lookback_bars + 1

    @property
    def context_symbols(self) -> list[str]:
        return [self.benchmark_symbol]

    def generate_targets(self, bars: pd.DataFrame) -> pd.DataFrame:
        if bars.empty:
            return pd.DataFrame(columns=TARGET_COLUMNS)
        close = close_matrix(bars)
        if self.benchmark_symbol not in close:
            raise ValueError(f"{self.benchmark_symbol} bars are required as context.")
        volume = bars.pivot(
            index="timestamp", columns="symbol", values="volume"
        ).sort_index().reindex_like(close)
        typical = bars.assign(
            typical=(bars["high"] + bars["low"] + bars["close"]) / 3.0
        ).pivot(index="timestamp", columns="symbol", values="typical").reindex_like(close)
        local_index = close.index.tz_convert(self.timezone)
        sessions = pd.Series(local_index.date, index=close.index)
        horizon_return = close.groupby(sessions).pct_change(
            self.lookback_bars, fill_method=None
        )
        relative = horizon_return.sub(horizon_return[self.benchmark_symbol], axis=0)
        cumulative_value = (typical * volume).groupby(sessions).cumsum()
        cumulative_volume = volume.groupby(sessions).cumsum().replace(0, np.nan)
        vwap = cumulative_value / cumulative_volume
        dollar_volume = close * volume
        tradables = [symbol for symbol in close.columns if symbol != self.benchmark_symbol]

        current_session = None
        selected: list[str] = []
        records: list[dict] = []
        for timestamp in close.index:
            local_timestamp = timestamp.tz_convert(self.timezone)
            local_time = local_timestamp.time().replace(tzinfo=None)
            if local_timestamp.date() != current_session:
                current_session = local_timestamp.date()
                selected = []
            scores = relative.loc[timestamp, tradables].replace(
                [np.inf, -np.inf], np.nan
            )
            if local_time == _clock(self.entry_time):
                market_ok = (
                    close.at[timestamp, self.benchmark_symbol]
                    >= vwap.at[timestamp, self.benchmark_symbol]
                    if self.require_benchmark_above_vwap
                    else True
                )
                liquid = dollar_volume.loc[timestamp, tradables].ge(
                    self.min_bar_dollar_volume
                )
                outperforming = scores.ge(self.min_relative_outperformance)
                above_vwap = close.loc[timestamp, tradables].ge(
                    vwap.loc[timestamp, tradables]
                )
                candidates = scores[liquid & outperforming & above_vwap].dropna()
                candidates = candidates.sort_values(ascending=False)
                selected = list(candidates.head(self.top_n).index) if market_ok else []
            if local_time >= _clock(self.flatten_at):
                selected = []
            weight = min(
                self.max_position_weight,
                self.gross_exposure / len(selected) if selected else 0.0,
            )
            for symbol in tradables:
                score = scores.get(symbol, np.nan)
                records.append(
                    {
                        "timestamp": timestamp,
                        "symbol": symbol,
                        "target_weight": weight if symbol in selected else 0.0,
                        "score": float(score) if pd.notna(score) else float("nan"),
                    }
                )
        return pd.DataFrame.from_records(records, columns=TARGET_COLUMNS)
