from __future__ import annotations

from dataclasses import dataclass
from datetime import time

import numpy as np
import pandas as pd

from project_geld.strategies.base import TARGET_COLUMNS, close_matrix


def _clock(value: str) -> time:
    return time.fromisoformat(value)


@dataclass
class IntraV7:
    """Short laggards only after a later bar confirms downside continuation."""

    benchmark_symbol: str = "SPY"
    lookback_bars: int = 2
    top_n: int = 4
    gross_exposure: float = 0.40
    max_position_weight: float = 0.10
    min_bar_dollar_volume: float = 1_000_000.0
    min_relative_dislocation: float = 0.01
    signal_time: str = "10:30"
    confirmation_bars: int = 1
    entry_delay_bars: int = 0
    flatten_at: str = "15:45"
    require_benchmark_above_vwap: bool = True
    daily_trend_sessions: int = 0
    require_below_prior_close: bool = False
    relative_volume_sessions: int = 0
    min_relative_volume: float = 0.0
    max_relative_volume: float = 0.0
    min_confirmation_break: float = 0.0
    relative_volatility_sessions: int = 0
    min_dislocation_sigma: float = 0.0
    timezone: str = "America/New_York"
    name: str = "intra_v7"

    def __post_init__(self) -> None:
        self.benchmark_symbol = self.benchmark_symbol.upper()
        if self.lookback_bars < 1 or self.top_n < 1 or self.confirmation_bars < 1:
            raise ValueError("lookback_bars, top_n, and confirmation_bars must be positive.")
        if self.entry_delay_bars < 0:
            raise ValueError("entry_delay_bars cannot be negative.")
        if not 0 < self.gross_exposure <= 1:
            raise ValueError("gross_exposure must be in (0, 1].")
        if not 0 < self.max_position_weight <= 1:
            raise ValueError("max_position_weight must be in (0, 1].")
        if self.top_n * self.max_position_weight + 1e-12 < self.gross_exposure:
            raise ValueError("top_n times max_position_weight cannot fund gross_exposure.")
        if self.min_relative_dislocation < 0:
            raise ValueError("min_relative_dislocation cannot be negative.")
        if self.daily_trend_sessions < 0:
            raise ValueError("daily_trend_sessions cannot be negative.")
        if (
            self.relative_volume_sessions < 0
            or self.min_relative_volume < 0
            or self.max_relative_volume < 0
        ):
            raise ValueError("Relative-volume settings cannot be negative.")
        if self.max_relative_volume and self.max_relative_volume < self.min_relative_volume:
            raise ValueError("max_relative_volume cannot be below min_relative_volume.")
        if not 0 <= self.min_confirmation_break < 1:
            raise ValueError("min_confirmation_break must be in [0, 1).")
        if self.relative_volatility_sessions < 0 or self.min_dislocation_sigma < 0:
            raise ValueError("Relative-volatility settings cannot be negative.")
        if _clock(self.signal_time) >= _clock(self.flatten_at):
            raise ValueError("signal_time must precede flatten_at.")

    @property
    def warmup_bars(self) -> int:
        return self.lookback_bars + self.confirmation_bars + 1

    @property
    def context_symbols(self) -> list[str]:
        return [self.benchmark_symbol]

    def generate_targets(self, bars: pd.DataFrame) -> pd.DataFrame:
        if bars.empty:
            return pd.DataFrame(columns=TARGET_COLUMNS)
        close = close_matrix(bars)
        if self.benchmark_symbol not in close:
            raise ValueError(f"{self.benchmark_symbol} bars are required as context.")
        low = bars.pivot(
            index="timestamp", columns="symbol", values="low"
        ).sort_index().reindex_like(close)
        volume = bars.pivot(
            index="timestamp", columns="symbol", values="volume"
        ).sort_index().reindex_like(close)
        typical = bars.assign(
            typical=(bars["high"] + bars["low"] + bars["close"]) / 3.0
        ).pivot(index="timestamp", columns="symbol", values="typical").reindex_like(close)
        local_index = close.index.tz_convert(self.timezone)
        sessions = pd.Series(local_index.date, index=close.index)
        session_close = close.groupby(sessions).last()
        prior_session_close = session_close.shift(1)
        prior_trend_average = (
            prior_session_close.rolling(
                self.daily_trend_sessions,
                min_periods=self.daily_trend_sessions,
            ).mean()
            if self.daily_trend_sessions
            else prior_session_close
        )
        horizon_return = close.groupby(sessions).pct_change(
            self.lookback_bars, fill_method=None
        )
        relative = horizon_return.sub(horizon_return[self.benchmark_symbol], axis=0)
        cumulative_value = (typical * volume).groupby(sessions).cumsum()
        cumulative_volume = volume.groupby(sessions).cumsum().replace(0, np.nan)
        vwap = cumulative_value / cumulative_volume
        dollar_volume = close * volume
        tradables = [symbol for symbol in close.columns if symbol != self.benchmark_symbol]
        signal_rows = [
            timestamp
            for timestamp in close.index
            if timestamp.tz_convert(self.timezone).time().replace(tzinfo=None)
            == _clock(self.signal_time)
        ]
        signal_volume = volume.loc[signal_rows].copy()
        signal_volume.index = [
            timestamp.tz_convert(self.timezone).date() for timestamp in signal_rows
        ]
        prior_signal_volume = (
            signal_volume.shift(1).rolling(
                self.relative_volume_sessions,
                min_periods=self.relative_volume_sessions,
            ).median()
            if self.relative_volume_sessions
            else signal_volume
        )
        signal_dislocation = -relative.loc[signal_rows].copy()
        signal_dislocation.index = signal_volume.index
        prior_dislocation_volatility = (
            signal_dislocation.shift(1).rolling(
                self.relative_volatility_sessions,
                min_periods=self.relative_volatility_sessions,
            ).std(ddof=0)
            if self.relative_volatility_sessions
            else signal_dislocation
        )
        bar_minutes = self._infer_bar_minutes(close.index)
        confirmation_time = (
            pd.Timestamp.combine(pd.Timestamp.today(), _clock(self.signal_time))
            + pd.Timedelta(bar_minutes * self.confirmation_bars, unit="m")
        ).time()
        entry_time = (
            pd.Timestamp.combine(pd.Timestamp.today(), confirmation_time)
            + pd.Timedelta(bar_minutes * self.entry_delay_bars, unit="m")
        ).time()
        if entry_time >= _clock(self.flatten_at):
            raise ValueError("Delayed entry must precede flatten_at.")

        current_session = None
        candidates: dict[str, tuple[float, float]] = {}
        selected: list[str] = []
        records: list[dict] = []
        for timestamp in close.index:
            local_timestamp = timestamp.tz_convert(self.timezone)
            local_time = local_timestamp.time().replace(tzinfo=None)
            if local_timestamp.date() != current_session:
                current_session = local_timestamp.date()
                candidates = {}
                selected = []
            scores = -relative.loc[timestamp, tradables].replace(
                [np.inf, -np.inf], np.nan
            )
            if local_time == _clock(self.signal_time):
                liquid = dollar_volume.loc[timestamp, tradables].ge(
                    self.min_bar_dollar_volume
                )
                dislocated = scores.ge(self.min_relative_dislocation)
                below_vwap = close.loc[timestamp, tradables].le(
                    vwap.loc[timestamp, tradables]
                )
                if self.daily_trend_sessions:
                    downtrend = prior_session_close.loc[
                        current_session, tradables
                    ].lt(prior_trend_average.loc[current_session, tradables])
                else:
                    downtrend = pd.Series(True, index=tradables)
                if self.require_below_prior_close:
                    below_prior_close = close.loc[timestamp, tradables].lt(
                        prior_session_close.loc[current_session, tradables]
                    )
                else:
                    below_prior_close = pd.Series(True, index=tradables)
                if self.relative_volume_sessions:
                    relative_volume = signal_volume.loc[
                        current_session, tradables
                    ].div(prior_signal_volume.loc[current_session, tradables])
                    volume_surge = relative_volume.ge(self.min_relative_volume)
                    if self.max_relative_volume:
                        volume_surge &= relative_volume.le(self.max_relative_volume)
                else:
                    volume_surge = pd.Series(True, index=tradables)
                if self.relative_volatility_sessions:
                    dislocation_sigma = scores.div(
                        prior_dislocation_volatility.loc[current_session, tradables]
                    ).replace([np.inf, -np.inf], np.nan)
                    unusual_dislocation = dislocation_sigma.ge(
                        self.min_dislocation_sigma
                    )
                    ranking_scores = dislocation_sigma
                else:
                    unusual_dislocation = pd.Series(True, index=tradables)
                    ranking_scores = scores
                qualified = ranking_scores[
                    liquid
                    & dislocated
                    & below_vwap
                    & downtrend
                    & below_prior_close
                    & volume_surge
                    & unusual_dislocation
                ].dropna()
                candidates = {
                    symbol: (float(score), float(low.at[timestamp, symbol]))
                    for symbol, score in qualified.items()
                    if pd.notna(low.at[timestamp, symbol])
                }
            if local_time == confirmation_time:
                market_ok = (
                    close.at[timestamp, self.benchmark_symbol]
                    >= vwap.at[timestamp, self.benchmark_symbol]
                    if self.require_benchmark_above_vwap
                    else True
                )
                confirmed = [
                    (symbol, score)
                    for symbol, (score, signal_low) in candidates.items()
                    if close.at[timestamp, symbol]
                    < signal_low * (1.0 - self.min_confirmation_break)
                ]
                confirmed.sort(key=lambda item: item[1], reverse=True)
                selected = [symbol for symbol, _ in confirmed[: self.top_n]] if market_ok else []
            if local_time >= _clock(self.flatten_at):
                selected = []
            active = selected if local_time >= entry_time else []
            weight = min(
                self.max_position_weight,
                self.gross_exposure / len(active) if active else 0.0,
            )
            for symbol in tradables:
                score = scores.get(symbol, np.nan)
                records.append(
                    {
                        "timestamp": timestamp,
                        "symbol": symbol,
                        "target_weight": -weight if symbol in active else 0.0,
                        "score": float(score) if pd.notna(score) else float("nan"),
                    }
                )
        return pd.DataFrame.from_records(records, columns=TARGET_COLUMNS)

    def _infer_bar_minutes(self, index: pd.DatetimeIndex) -> int:
        differences = index.to_series().diff().dropna()
        minutes = differences.dt.total_seconds().div(60)
        same_session = minutes[minutes.between(1, 60)]
        if same_session.empty:
            raise ValueError("At least two same-session bars are required.")
        return int(same_session.mode().iloc[0])
