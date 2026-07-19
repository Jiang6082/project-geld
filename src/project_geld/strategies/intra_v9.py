from __future__ import annotations

from dataclasses import dataclass

from project_geld.strategies.intra_v8 import IntraV8


@dataclass
class IntraV9(IntraV8):
    """Trend-aligned short continuation confirmed by unusual signal-bar volume."""

    relative_volume_sessions: int = 20
    min_relative_volume: float = 1.5
    name: str = "intra_v9"
