from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from project_geld.strategies.base import TARGET_COLUMNS, close_matrix
from project_geld.strategies.equity_momentum_v3 import EquityMomentumV3


# The active sleeve (EquityMomentumV3) always applies a forecast-volatility
# ceiling. When DailyV4 leaves ``active_target_volatility`` unset the ceiling is
# disabled by passing a value far above any realizable forecast, since the
# wrapper already bounds the sleeve's gross exposure at ``active_weight``.
_DISABLED_VOLATILITY_CEILING = 1_000.0


@dataclass(frozen=True)
class DailyV4:
    """A stable index core plus a diversified residual-momentum stock sleeve.

    Version history (major = strategy generation, patch = applied improvement):
    - 4.0.1: explicit, overridable ``regime_enabled`` / ``active_target_volatility``;
      removed the duplicated update and the magic volatility ceiling
      (behavior-preserving).
    - 4.0.2: regime-aware exposure control enabled on the active sleeve.
    - 4.0.3: allocation set to 75/25, the PnL-optimal, lowest-turnover point.
    - 4.0.4: benchmark-aware active weighting (total return 304.3% -> 305.9% at
      equal drawdown and turnover).
    """

    core_symbol: str = "SPY"
    core_weight: float = 0.75
    active_weight: float = 0.25
    active_name_cap: float = 0.02
    no_trade_band: float = 0.0025
    rebalance_every: int = 21
    regime_enabled: bool = False
    active_target_volatility: float | None = None
    # Risk-managed momentum: when set, scale the active sleeve down (never up) so
    # its trailing realized volatility targets this annualized fraction. Freed
    # capital moves to cash, cutting momentum-crash drawdowns. None disables it.
    sleeve_volatility_target: float | None = None
    sleeve_volatility_lookback: int = 126
    active_parameters: dict = field(default_factory=dict)
    name: str = "daily_v4"
    version: str = "Daily V4.0.4"

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
        if (
            self.active_target_volatility is not None
            and self.active_target_volatility <= 0
        ):
            raise ValueError("active_target_volatility must be positive when set.")
        if (
            self.sleeve_volatility_target is not None
            and self.sleeve_volatility_target <= 0
        ):
            raise ValueError("sleeve_volatility_target must be positive when set.")
        if self.sleeve_volatility_lookback < 2:
            raise ValueError("sleeve_volatility_lookback must be at least 2.")

    def _active_strategy(self) -> EquityMomentumV3:
        ceiling = (
            self.active_target_volatility
            if self.active_target_volatility is not None
            else _DISABLED_VOLATILITY_CEILING
        )
        parameters = {
            "max_symbols": 40,
            "exit_rank": 80,
            **self.active_parameters,
            # DailyV4 owns these knobs; they stay authoritative over any values
            # supplied through active_parameters.
            "benchmark_symbol": self.core_symbol.upper(),
            "max_position_weight": self.active_name_cap,
            "rebalance_every": self.rebalance_every,
            "regime_enabled": self.regime_enabled,
            "bullish_exposure": self.active_weight,
            "target_portfolio_volatility": ceiling,
        }
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

        returns = close_matrix(bars).pct_change(fill_method=None).reindex(weights.index)
        sleeve_returns: list[float] = []

        for index, timestamp in enumerate(weights.index):
            # Realized sleeve return from the weights held coming into this bar.
            if current:
                day = returns.loc[timestamp]
                sleeve_returns.append(
                    sum(
                        weight * float(day.get(symbol, 0.0))
                        for symbol, weight in current.items()
                        if pd.notna(day.get(symbol))
                    )
                )
            else:
                sleeve_returns.append(0.0)
            if len(sleeve_returns) > self.sleeve_volatility_lookback:
                sleeve_returns.pop(0)

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
                if (
                    self.sleeve_volatility_target is not None
                    and len(sleeve_returns) >= self.sleeve_volatility_lookback
                ):
                    realized = float(np.std(sleeve_returns, ddof=0)) * np.sqrt(252.0)
                    if realized > 0:
                        vol_scale = min(1.0, self.sleeve_volatility_target / realized)
                        proposed = {
                            symbol: weight * vol_scale
                            for symbol, weight in proposed.items()
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


# Backward-compatible import for older research scripts and notebooks.
CoreSatelliteMomentum = DailyV4
