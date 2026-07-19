from __future__ import annotations

from dataclasses import dataclass

from project_geld.strategies.intra_v7 import IntraV7


@dataclass
class IntraV8(IntraV7):
    """Confirmed short continuation aligned with the prior daily trend."""

    daily_trend_sessions: int = 20
    require_below_prior_close: bool = True
    name: str = "intra_v8"
