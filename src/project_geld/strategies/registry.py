from __future__ import annotations

from typing import Any

from project_geld.strategies.base import Strategy
from project_geld.strategies.daily_v4 import DailyV4
from project_geld.strategies.equity_momentum_v2 import EquityMomentumV2
from project_geld.strategies.equity_momentum_v3 import EquityMomentumV3
from project_geld.strategies.mean_reversion import LongOnlyMeanReversion
from project_geld.strategies.momentum import CrossSectionalMomentum
from project_geld.strategies.trend import TrendStrength
from project_geld.strategies.intra_v1 import IntraV1
from project_geld.strategies.daily_v5 import DailyV5
from project_geld.strategies.intra_v2 import IntraV2
from project_geld.strategies.intra_v3 import IntraV3
from project_geld.strategies.intra_v4 import IntraV4
from project_geld.strategies.intra_v5 import IntraV5
from project_geld.strategies.intra_v6 import IntraV6
from project_geld.strategies.intra_v7 import IntraV7
from project_geld.strategies.intra_v8 import IntraV8
from project_geld.strategies.intra_v9 import IntraV9
from project_geld.strategies.intra_v10 import IntraV10
from project_geld.strategies.intra_v11 import IntraV11
from project_geld.strategies.intra_v12 import IntraV12
from project_geld.strategies.intra_v13 import IntraV13


STRATEGIES = {
    "momentum": CrossSectionalMomentum,
    "momentum_v2": EquityMomentumV2,
    "momentum_v3": EquityMomentumV3,
    "daily_v4": DailyV4,
    "daily_v5": DailyV5,
    "momentum_v4": DailyV4,
    "trend": TrendStrength,
    "mean_reversion": LongOnlyMeanReversion,
    "intra_v1": IntraV1,
    "intra_v2": IntraV2,
    "intra_v3": IntraV3,
    "intra_v4": IntraV4,
    "intra_v5": IntraV5,
    "intra_v6": IntraV6,
    "intra_v7": IntraV7,
    "intra_v8": IntraV8,
    "intra_v9": IntraV9,
    "intra_v10": IntraV10,
    "intra_v11": IntraV11,
    "intra_v12": IntraV12,
    "intra_v13": IntraV13,
    "intraday_momentum": IntraV1,
}


def create_strategy(name: str, parameters: dict[str, Any] | None = None) -> Strategy:
    key = name.lower()
    if key not in STRATEGIES:
        raise ValueError(f"Unknown strategy '{name}'. Available: {', '.join(sorted(STRATEGIES))}")
    return STRATEGIES[key](**(parameters or {}))


def available_strategies() -> list[str]:
    return sorted(STRATEGIES)
