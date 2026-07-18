from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from project_geld.strategies.base import TARGET_COLUMNS, close_matrix


@dataclass(frozen=True)
class EquityMomentumV2:
    """Buffered 12-1 momentum with trend filters and inverse-volatility weights."""

    formation_lookback: int = 252
    skip_recent: int = 21
    volatility_lookback: int = 60
    fast_window: int = 50
    slow_window: int = 200
    max_symbols: int = 5
    exit_rank: int = 10
    gross_exposure: float = 0.75
    rebalance_every: int = 10
    max_per_sector: int = 2
    sector_map: dict[str, str] = field(default_factory=dict)
    membership_periods: dict[str, list[list[str | None]]] = field(default_factory=dict)
    name: str = "momentum_v2"

    def __post_init__(self) -> None:
        if self.formation_lookback <= self.skip_recent:
            raise ValueError("formation_lookback must exceed skip_recent.")
        if self.max_symbols < 1:
            raise ValueError("max_symbols must be positive.")
        if self.exit_rank < self.max_symbols:
            raise ValueError("exit_rank must be at least max_symbols.")
        if self.rebalance_every < 1:
            raise ValueError("rebalance_every must be positive.")
        if self.max_per_sector < 1:
            raise ValueError("max_per_sector must be positive.")
        if not 0 < self.gross_exposure <= 1:
            raise ValueError("gross_exposure must be in (0, 1].")

    @property
    def warmup_bars(self) -> int:
        return max(
            self.formation_lookback,
            self.volatility_lookback,
            self.slow_window,
        ) + 1

    def generate_targets(self, bars: pd.DataFrame) -> pd.DataFrame:
        close = close_matrix(bars)
        returns = close.pct_change(fill_method=None)
        momentum_12_1 = (
            close.shift(self.skip_recent) / close.shift(self.formation_lookback) - 1
        )
        volatility = (
            returns.rolling(self.volatility_lookback).std() * np.sqrt(252)
        )
        fast = close.rolling(self.fast_window).mean()
        slow = close.rolling(self.slow_window).mean()
        score = momentum_12_1 / volatility.replace(0, np.nan)
        eligible = (
            momentum_12_1.gt(0)
            & close.gt(slow)
            & fast.gt(slow)
            & score.notna()
        )
        if self.membership_periods:
            membership = pd.DataFrame(False, index=close.index, columns=close.columns)
            for raw_symbol, periods in self.membership_periods.items():
                symbol = raw_symbol.upper()
                if symbol not in membership.columns:
                    continue
                for start, end in periods:
                    start_ts = pd.Timestamp(start, tz="UTC")
                    end_ts = (
                        pd.Timestamp(end, tz="UTC")
                        if end is not None
                        else close.index.max()
                    )
                    active = membership.index.to_series().between(
                        start_ts, end_ts, inclusive="both"
                    )
                    membership.loc[active, symbol] = True
            eligible &= membership

        selected: list[str] = []
        current_weights: dict[str, float] = {}
        rows: list[dict] = []
        for index, timestamp in enumerate(close.index):
            if index % self.rebalance_every == 0:
                day_score = (
                    score.loc[timestamp]
                    .where(eligible.loc[timestamp])
                    .replace([np.inf, -np.inf], np.nan)
                    .dropna()
                    .sort_values(ascending=False)
                )
                ranks = {
                    symbol: rank
                    for rank, symbol in enumerate(day_score.index, start=1)
                }
                kept_candidates = [
                    symbol
                    for symbol in selected
                    if symbol in ranks and ranks[symbol] <= self.exit_rank
                ]
                selected = []
                sector_counts: dict[str, int] = {}
                for symbol in kept_candidates:
                    sector = self.sector_map.get(symbol, symbol)
                    if sector_counts.get(sector, 0) >= self.max_per_sector:
                        continue
                    selected.append(symbol)
                    sector_counts[sector] = sector_counts.get(sector, 0) + 1
                    if len(selected) >= self.max_symbols:
                        break
                for symbol in day_score.index:
                    if symbol not in selected:
                        sector = self.sector_map.get(symbol, symbol)
                        if sector_counts.get(sector, 0) >= self.max_per_sector:
                            continue
                        selected.append(symbol)
                        sector_counts[sector] = sector_counts.get(sector, 0) + 1
                    if len(selected) >= self.max_symbols:
                        break

                day_volatility = volatility.loc[timestamp].reindex(selected)
                inverse_volatility = 1 / day_volatility.replace(0, np.nan)
                inverse_volatility = inverse_volatility.replace(
                    [np.inf, -np.inf], np.nan
                ).dropna()
                if len(inverse_volatility):
                    weights = (
                        inverse_volatility
                        / inverse_volatility.sum()
                        * self.gross_exposure
                    )
                    current_weights = {
                        symbol: float(weight)
                        for symbol, weight in weights.items()
                    }
                    selected = list(current_weights)
                else:
                    selected = []
                    current_weights = {}

            for symbol in close.columns:
                value = score.at[timestamp, symbol]
                rows.append(
                    {
                        "timestamp": timestamp,
                        "symbol": symbol,
                        "target_weight": current_weights.get(symbol, 0.0),
                        "score": float(value) if pd.notna(value) else float("nan"),
                    }
                )
        return pd.DataFrame(rows, columns=TARGET_COLUMNS)
