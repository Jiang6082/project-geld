from __future__ import annotations

from typing import Any

from project_geld.strategies.base import Strategy
from project_geld.strategies.core_satellite_momentum import CoreSatelliteMomentum
from project_geld.strategies.equity_momentum_v2 import EquityMomentumV2
from project_geld.strategies.equity_momentum_v3 import EquityMomentumV3
from project_geld.strategies.mean_reversion import LongOnlyMeanReversion
from project_geld.strategies.momentum import CrossSectionalMomentum
from project_geld.strategies.trend import TrendStrength
from project_geld.strategies.intraday_momentum import IntradayRelativeMomentum


STRATEGIES = {
    "momentum": CrossSectionalMomentum,
    "momentum_v2": EquityMomentumV2,
    "momentum_v3": EquityMomentumV3,
    "momentum_v4": CoreSatelliteMomentum,
    "trend": TrendStrength,
    "mean_reversion": LongOnlyMeanReversion,
    "intraday_momentum": IntradayRelativeMomentum,
}


def create_strategy(name: str, parameters: dict[str, Any] | None = None) -> Strategy:
    key = name.lower()
    if key not in STRATEGIES:
        raise ValueError(f"Unknown strategy '{name}'. Available: {', '.join(sorted(STRATEGIES))}")
    return STRATEGIES[key](**(parameters or {}))


def available_strategies() -> list[str]:
    return sorted(STRATEGIES)
