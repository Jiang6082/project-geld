from __future__ import annotations

from dataclasses import dataclass

from project_geld.strategies.intra_v5 import IntraV5


@dataclass
class IntraV6(IntraV5):
    """More selective confirmed reversal requiring a 1% relative dislocation."""

    min_relative_dislocation: float = 0.01
    name: str = "intra_v6"
