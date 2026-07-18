from __future__ import annotations

from dataclasses import dataclass
from datetime import time

import numpy as np
import pandas as pd

from project_geld.strategies.base import TARGET_COLUMNS, close_matrix


def _clock(value: str) -> time:
    return time.fromisoformat(value)


@dataclass
class IntraV1:
    benchmark_symbol: str = "SPY"
    lookback_bars: int = 2
    top_n: int = 6
    exit_rank: int = 12
    rebalance_every_bars: int = 4
    gross_exposure: float = 0.70
    max_position_weight: float = 0.12
    min_bar_dollar_volume: float = 1_000_000.0
    signal_start: str = "10:00"
    last_entry: str = "15:30"
    flatten_at: str = "15:45"
    require_benchmark_above_vwap: bool = True
    signal_mode: str = "momentum"
    require_stock_vwap_confirmation: bool = True
    timezone: str = "America/New_York"
    name: str = "intra_v1"

    def __post_init__(self) -> None:
        self.benchmark_symbol = self.benchmark_symbol.upper()
        if self.lookback_bars < 1 or self.top_n < 1 or self.rebalance_every_bars < 1:
            raise ValueError("lookback_bars, top_n, and rebalance_every_bars must be positive.")
        if self.exit_rank < self.top_n:
            raise ValueError("exit_rank must be at least top_n.")
        if not 0 < self.gross_exposure <= 1:
            raise ValueError("gross_exposure must be in (0, 1].")
        if not 0 < self.max_position_weight <= 1:
            raise ValueError("max_position_weight must be in (0, 1].")
        if self.top_n * self.max_position_weight + 1e-12 < self.gross_exposure:
            raise ValueError("top_n times max_position_weight cannot fund gross_exposure.")
        if self.signal_mode not in {"momentum", "mean_reversion"}:
            raise ValueError("signal_mode must be 'momentum' or 'mean_reversion'.")
        if not (_clock(self.signal_start) < _clock(self.last_entry) < _clock(self.flatten_at)):
            raise ValueError("Intraday signal times must be ordered.")

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
        volume = bars.pivot(
            index="timestamp", columns="symbol", values="volume"
        ).sort_index().reindex_like(close)
        if self.benchmark_symbol not in close.columns:
            raise ValueError(f"{self.benchmark_symbol} bars are required as context.")
        local_index = close.index.tz_convert(self.timezone)
        sessions = pd.Series(local_index.date, index=close.index)
        returns = close.groupby(sessions).pct_change(self.lookback_bars)
        benchmark_return = returns[self.benchmark_symbol]
        relative = returns.sub(benchmark_return, axis=0)

        typical = bars.assign(
            typical=(bars["high"] + bars["low"] + bars["close"]) / 3.0
        ).pivot(index="timestamp", columns="symbol", values="typical").reindex_like(close)
        cumulative_value = (typical * volume).groupby(sessions).cumsum()
        cumulative_volume = volume.groupby(sessions).cumsum().replace(0, np.nan)
        benchmark_vwap = cumulative_value[self.benchmark_symbol] / cumulative_volume[self.benchmark_symbol]
        stock_vwap = cumulative_value / cumulative_volume
        market_ok = close[self.benchmark_symbol].ge(benchmark_vwap)
        dollar_volume = close * volume
        tradables = [symbol for symbol in close.columns if symbol != self.benchmark_symbol]
        records: list[dict] = []
        current_session = None
        selected: list[str] = []
        bars_since_rebalance = self.rebalance_every_bars

        for timestamp in close.index:
            local_timestamp = timestamp.tz_convert(self.timezone)
            local_time = local_timestamp.time().replace(tzinfo=None)
            if local_timestamp.date() != current_session:
                current_session = local_timestamp.date()
                selected = []
                bars_since_rebalance = self.rebalance_every_bars
            can_enter = _clock(self.signal_start) <= local_time <= _clock(self.last_entry)
            must_flatten = local_time >= _clock(self.flatten_at)
            raw_relative = relative.loc[timestamp, tradables].replace([np.inf, -np.inf], np.nan)
            scores = raw_relative if self.signal_mode == "momentum" else -raw_relative
            liquid = dollar_volume.loc[timestamp, tradables].ge(self.min_bar_dollar_volume)
            if self.require_stock_vwap_confirmation:
                if self.signal_mode == "momentum":
                    confirmed = close.loc[timestamp, tradables].ge(
                        stock_vwap.loc[timestamp, tradables]
                    )
                else:
                    confirmed = close.loc[timestamp, tradables].le(
                        stock_vwap.loc[timestamp, tradables]
                    )
            else:
                confirmed = pd.Series(True, index=tradables)
            regime_ok = bool(market_ok.loc[timestamp]) or not self.require_benchmark_above_vwap
            if must_flatten or local_time < _clock(self.signal_start):
                selected = []
                bars_since_rebalance = self.rebalance_every_bars
            elif can_enter and (selected or regime_ok):
                ranking = scores[
                    liquid & confirmed & scores.gt(0)
                ].dropna().sort_values(ascending=False)
                if selected:
                    bars_since_rebalance += 1
                if not selected or bars_since_rebalance >= self.rebalance_every_bars:
                    exit_set = set(ranking.head(self.exit_rank).index)
                    retained = [symbol for symbol in selected if symbol in exit_set]
                    entrants = [
                        symbol for symbol in ranking.index if symbol not in retained
                    ]
                    selected = (retained + entrants)[: self.top_n]
                    bars_since_rebalance = 0
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


# Backward-compatible import for older research scripts and notebooks.
IntradayRelativeMomentum = IntraV1
