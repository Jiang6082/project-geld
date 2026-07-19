from __future__ import annotations

from dataclasses import dataclass

from project_geld.strategies.intra_v8 import IntraV8


@dataclass
class IntraV12(IntraV8):
    """V8 with quiet-volume and decisive-break confirmation filters."""

    relative_volume_sessions: int = 20
    min_relative_volume: float = 0.0
    max_relative_volume: float = 1.5
    min_confirmation_break: float = 0.0025
    name: str = "intra_v12"
