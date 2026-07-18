from __future__ import annotations

from dataclasses import dataclass

from project_geld.strategies.intra_v2 import IntraV2


@dataclass
class IntraV3(IntraV2):
    """Broader Intra V2 allocation: eight names, 10% each, 80% gross."""

    top_n: int = 8
    gross_exposure: float = 0.80
    max_position_weight: float = 0.10
    name: str = "intra_v3"
