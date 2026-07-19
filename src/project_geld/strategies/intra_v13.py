from __future__ import annotations

from dataclasses import dataclass

from project_geld.strategies.intra_v12 import IntraV12


@dataclass
class IntraV13(IntraV12):
    """PIT/SIP V12 with causal capacity, breadth, and crowding controls."""

    daily_volatility_sessions: int = 20
    max_annualized_daily_volatility: float = 1.50
    min_market_breadth: float = 0.45
    correlation_lookback_sessions: int = 60
    max_pairwise_correlation: float = 0.85
    name: str = "intra_v13"
