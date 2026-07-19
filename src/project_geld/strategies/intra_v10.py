from __future__ import annotations

from dataclasses import dataclass

from project_geld.strategies.intra_v8 import IntraV8


@dataclass
class IntraV10(IntraV8):
    """V8 with each morning dislocation normalized by its prior variability."""

    relative_volatility_sessions: int = 20
    min_dislocation_sigma: float = 2.0
    name: str = "intra_v10"
