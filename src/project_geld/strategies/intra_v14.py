from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time

import numpy as np
import pandas as pd

from project_geld.strategies.base import TARGET_COLUMNS, close_matrix


def _clock(value: str) -> time:
    return time.fromisoformat(value)


@dataclass
class IntraV14:
    """Near-daily, market-neutral cross-sectional intraday ranking strategy."""

    benchmark_symbol: str = "SPY"
    lookback_bars: int = 3
    names_per_side: int = 4
    gross_exposure: float = 0.80
    max_position_weight: float = 0.10
    min_cumulative_dollar_volume: float = 20_000_000.0
    min_absolute_score: float = 0.0
    signal_time: str = "10:30"
    flatten_at: str = "15:45"
    direction: str = "momentum"
    membership_periods: dict[str, list[list[str | None]]] = field(
        default_factory=dict
    )
    timezone: str = "America/New_York"
    name: str = "intra_v14"

    def __post_init__(self) -> None:
        self.benchmark_symbol = self.benchmark_symbol.upper()
        self.direction = self.direction.lower()
        if self.lookback_bars < 1:
            raise ValueError("lookback_bars must be positive.")
        if self.names_per_side < 1:
            raise ValueError("names_per_side must be positive.")
        if not 0 < self.gross_exposure <= 1:
            raise ValueError("gross_exposure must be in (0, 1].")
        if not 0 < self.max_position_weight <= 1:
            raise ValueError("max_position_weight must be in (0, 1].")
        if (
            2 * self.names_per_side * self.max_position_weight + 1e-12
            < self.gross_exposure
        ):
            raise ValueError(
                "Two times names_per_side times max_position_weight cannot fund "
                "gross_exposure."
            )
        if self.min_cumulative_dollar_volume < 0 or self.min_absolute_score < 0:
            raise ValueError("Liquidity and score thresholds cannot be negative.")
        if self.direction not in {"momentum", "reversal"}:
            raise ValueError("direction must be 'momentum' or 'reversal'.")
        if _clock(self.signal_time) >= _clock(self.flatten_at):
            raise ValueError("signal_time must precede flatten_at.")

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
        volume = (
            bars.pivot(index="timestamp", columns="symbol", values="volume")
            .sort_index()
            .reindex_like(close)
        )
        local_index = close.index.tz_convert(self.timezone)
        sessions = pd.Series(local_index.date, index=close.index)
        horizon_return = close.groupby(sessions).pct_change(
            self.lookback_bars, fill_method=None
        )
        relative_return = horizon_return.sub(
            horizon_return[self.benchmark_symbol], axis=0
        )
        cumulative_dollar_volume = (close * volume).groupby(sessions).cumsum()
        tradables = [
            symbol for symbol in close.columns if symbol != self.benchmark_symbol
        ]

        records: list[dict] = []
        for current_session, session_index in close.groupby(sessions).groups.items():
            timestamps = pd.DatetimeIndex(session_index)
            signal_rows = [
                timestamp
                for timestamp in timestamps
                if timestamp.tz_convert(self.timezone).time().replace(tzinfo=None)
                == _clock(self.signal_time)
            ]
            if not signal_rows:
                continue
            timestamp = signal_rows[-1]
            scores = relative_return.loc[timestamp, tradables].replace(
                [np.inf, -np.inf], np.nan
            )
            members = self.membership_mask(current_session, tradables)
            liquid = cumulative_dollar_volume.loc[timestamp, tradables].ge(
                self.min_cumulative_dollar_volume
            )
            ranked = scores[members & liquid].dropna()
            if self.min_absolute_score:
                ranked = ranked[ranked.abs().ge(self.min_absolute_score)]
            if len(ranked) < 2:
                continue

            side_count = min(self.names_per_side, len(ranked) // 2)
            low = ranked.nsmallest(side_count).index.tolist()
            high = ranked.nlargest(side_count).index.tolist()
            selected_long, selected_short = (
                (high, low) if self.direction == "momentum" else (low, high)
            )
            active_count = len(selected_long) + len(selected_short)
            weight = min(
                self.max_position_weight,
                self.gross_exposure / active_count,
            )
            active_rows = [
                item
                for item in timestamps
                if _clock(self.signal_time)
                <= item.tz_convert(self.timezone).time().replace(tzinfo=None)
                < _clock(self.flatten_at)
            ]
            for item in active_rows:
                for symbol in selected_long:
                    records.append(
                        {
                            "timestamp": item,
                            "symbol": symbol,
                            "target_weight": weight,
                            "score": float(relative_return.at[item, symbol]),
                        }
                    )
                for symbol in selected_short:
                    records.append(
                        {
                            "timestamp": item,
                            "symbol": symbol,
                            "target_weight": -weight,
                            "score": float(relative_return.at[item, symbol]),
                        }
                    )
            flatten_rows = [
                item
                for item in timestamps
                if item.tz_convert(self.timezone).time().replace(tzinfo=None)
                == _clock(self.flatten_at)
            ]
            for item in flatten_rows:
                for symbol in [*selected_long, *selected_short]:
                    score = relative_return.at[item, symbol]
                    records.append(
                        {
                            "timestamp": item,
                            "symbol": symbol,
                            "target_weight": 0.0,
                            "score": float(score) if pd.notna(score) else float("nan"),
                        }
                    )
        return pd.DataFrame.from_records(records, columns=TARGET_COLUMNS)

    def membership_mask(
        self, session_date: object, symbols: list[str]
    ) -> pd.Series:
        if not self.membership_periods:
            return pd.Series(True, index=symbols)
        date = pd.Timestamp(session_date).date()
        result = {}
        for symbol in symbols:
            periods = self.membership_periods.get(symbol.upper(), [])
            result[symbol] = any(
                pd.Timestamp(start).date() <= date
                and (end is None or date <= pd.Timestamp(end).date())
                for start, end in periods
            )
        return pd.Series(result, dtype=bool)
