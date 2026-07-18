from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from project_geld.metrics import calculate_metrics
from project_geld.strategies.base import TARGET_COLUMNS, close_matrix


@dataclass(frozen=True)
class StaticAllocation:
    """Constant target allocation used for investable benchmark comparisons."""

    gross_exposure: float = 0.75
    symbol_weights: dict[str, float] = field(default_factory=dict)
    name: str = "static_allocation"

    @property
    def warmup_bars(self) -> int:
        return 1

    def generate_targets(self, bars: pd.DataFrame) -> pd.DataFrame:
        close = close_matrix(bars)
        if self.symbol_weights:
            raw = pd.Series(
                {symbol.upper(): weight for symbol, weight in self.symbol_weights.items()},
                dtype=float,
            ).reindex(close.columns, fill_value=0.0)
            raw = raw / raw.sum() * self.gross_exposure if raw.sum() else raw
        else:
            raw = pd.Series(
                self.gross_exposure / len(close.columns), index=close.columns
            )
        rows = [
            {
                "timestamp": timestamp,
                "symbol": symbol,
                "target_weight": float(raw[symbol]),
                "score": 0.0,
            }
            for timestamp in close.index
            for symbol in close.columns
        ]
        return pd.DataFrame(rows, columns=TARGET_COLUMNS)


@dataclass(frozen=True)
class MembershipAllocation:
    """Equal-weight allocation whose eligible symbols vary through time."""

    membership_periods: dict[str, list[list[str | None]]]
    gross_exposure: float = 0.75
    name: str = "membership_allocation"

    @property
    def warmup_bars(self) -> int:
        return 1

    def generate_targets(self, bars: pd.DataFrame) -> pd.DataFrame:
        close = close_matrix(bars)
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
                    else membership.index.max()
                )
                active = membership.index.to_series().between(
                    start_ts, end_ts, inclusive="both"
                )
                membership.loc[active, symbol] = True
        counts = membership.sum(axis=1).astype(float)
        counts = counts.where(counts.ne(0), float("nan"))
        weights = membership.div(counts, axis=0).fillna(0.0) * self.gross_exposure
        rows = [
            {
                "timestamp": timestamp,
                "symbol": symbol,
                "target_weight": float(weights.at[timestamp, symbol]),
                "score": 0.0,
            }
            for timestamp in close.index
            for symbol in close.columns
        ]
        return pd.DataFrame(rows, columns=TARGET_COLUMNS)


def period_metrics(
    result: Any, start: pd.Timestamp | str, end: pd.Timestamp | str
) -> dict[str, float]:
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if start_ts.tzinfo is None:
        start_ts = start_ts.tz_localize("UTC")
    if end_ts.tzinfo is None:
        end_ts = end_ts.tz_localize("UTC")
    equity = result.equity[
        result.equity["timestamp"].between(start_ts, end_ts, inclusive="both")
    ].copy()
    trades = result.trades[
        result.trades["timestamp"].between(start_ts, end_ts, inclusive="both")
    ].copy()
    if len(equity) < 2:
        return {
            "total_return": 0.0,
            "cagr": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "annual_turnover": 0.0,
        }
    return calculate_metrics(equity, trades)


def one_at_a_time_variants(base: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """A compact stability grid that avoids presenting a giant overfit search."""

    variations: dict[str, list[Any]] = {
        "formation_lookback": [126, 189, 252, 315],
        "skip_recent": [0, 21, 42],
        "volatility_lookback": [40, 60, 90],
        "max_symbols": [3, 5, 8],
        "rebalance_every": [5, 10, 21],
    }
    variants: list[tuple[str, dict[str, Any]]] = [("base", dict(base))]
    seen = {tuple(sorted((key, repr(value)) for key, value in base.items()))}
    for key, values in variations.items():
        for value in values:
            parameters = {**base, key: value}
            if key == "max_symbols":
                parameters["exit_rank"] = max(int(parameters["exit_rank"]), int(value))
            signature = tuple(
                sorted((name, repr(item)) for name, item in parameters.items())
            )
            if signature in seen:
                continue
            seen.add(signature)
            variants.append((f"{key}={value}", parameters))
    return variants
