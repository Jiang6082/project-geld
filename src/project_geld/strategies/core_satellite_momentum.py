from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from project_geld.strategies.base import TARGET_COLUMNS
from project_geld.strategies.equity_momentum_v3 import EquityMomentumV3


@dataclass(frozen=True)
class CoreSatelliteMomentum:
    """A stable index core plus a diversified residual-momentum stock sleeve."""

    core_symbol: str = "SPY"
    core_weight: float = 0.75
    active_weight: float = 0.25
    active_name_cap: float = 0.02
    no_trade_band: float = 0.0025
    rebalance_every: int = 21
    active_parameters: dict = field(default_factory=dict)
    name: str = "momentum_v4"

    def __post_init__(self) -> None:
        if not 0 <= self.core_weight <= 1:
            raise ValueError("core_weight must be in [0, 1].")
        if not 0 <= self.active_weight <= 1:
            raise ValueError("active_weight must be in [0, 1].")
        if self.core_weight + self.active_weight > 1 + 1e-12:
            raise ValueError("core_weight plus active_weight cannot exceed 1.")
        if not 0 < self.active_name_cap <= 1:
            raise ValueError("active_name_cap must be in (0, 1].")
        if self.no_trade_band < 0:
            raise ValueError("no_trade_band cannot be negative.")
        if self.rebalance_every < 1:
            raise ValueError("rebalance_every must be positive.")

    def _active_strategy(self) -> EquityMomentumV3:
        parameters = {
            "benchmark_symbol": self.core_symbol.upper(),
            "max_symbols": 40,
            "exit_rank": 80,
            "max_position_weight": self.active_name_cap,
            "rebalance_every": self.rebalance_every,
            "regime_enabled": False,
            "bullish_exposure": self.active_weight,
            "target_portfolio_volatility": 10.0,
            **self.active_parameters,
        }
        parameters.update(
            {
                "benchmark_symbol": self.core_symbol.upper(),
                "max_position_weight": self.active_name_cap,
                "rebalance_every": self.rebalance_every,
                "regime_enabled": False,
                "bullish_exposure": self.active_weight,
                "target_portfolio_volatility": 10.0,
            }
        )
        return EquityMomentumV3(**parameters)

    @property
    def warmup_bars(self) -> int:
        return self._active_strategy().warmup_bars

    @property
    def context_symbols(self) -> list[str]:
        return self._active_strategy().context_symbols

    def generate_targets(self, bars: pd.DataFrame) -> pd.DataFrame:
        active = self._active_strategy().generate_targets(bars)
        weights = active.pivot(
            index="timestamp", columns="symbol", values="target_weight"
        ).sort_index()
        scores = active.pivot(
            index="timestamp", columns="symbol", values="score"
        ).reindex(index=weights.index, columns=weights.columns)
        core = self.core_symbol.upper()
        context = set(self.context_symbols)
        active_symbols = [symbol for symbol in weights.columns if symbol not in context]
        current: dict[str, float] = {}
        rows: list[dict] = []

        for index, timestamp in enumerate(weights.index):
            if index % self.rebalance_every == 0:
                desired = weights.loc[timestamp, active_symbols].fillna(0.0)
                proposed: dict[str, float] = {}
                for symbol in active_symbols:
                    old = current.get(symbol, 0.0)
                    new = float(desired.get(symbol, 0.0))
                    proposed[symbol] = (
                        old if abs(new - old) < self.no_trade_band else new
                    )
                gross = sum(proposed.values())
                if gross > self.active_weight and gross > 0:
                    scale = self.active_weight / gross
                    proposed = {
                        symbol: weight * scale for symbol, weight in proposed.items()
                    }
                current = {
                    symbol: weight
                    for symbol, weight in proposed.items()
                    if weight > 1e-12
                }

            for symbol in weights.columns:
                score = scores.at[timestamp, symbol]
                rows.append(
                    {
                        "timestamp": timestamp,
                        "symbol": symbol,
                        "target_weight": (
                            self.core_weight
                            if symbol == core
                            else current.get(symbol, 0.0)
                        ),
                        "score": float(score) if pd.notna(score) else float("nan"),
                    }
                )
        return pd.DataFrame(rows, columns=TARGET_COLUMNS)
