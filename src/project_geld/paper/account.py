"""Account snapshots, broker protocol and the paper performance log."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Protocol

import pandas as pd

from project_geld.atomicio import atomic_write_dataframe_csv
from project_geld.models import OrderIntent


@dataclass(frozen=True)
class AccountSnapshot:
    equity: float
    last_equity: float
    positions: dict[str, float]
    open_order_symbols: set[str]
    cash: float = 0.0
    buying_power: float = 0.0
    shorting_enabled: bool = False
    unmanaged_notional: float = 0.0
    unmanaged_symbols: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ShortAvailability:
    shortable: bool
    easy_to_borrow: bool
    borrow_status: str


class PaperBrokerProtocol(Protocol):
    def get_clock(self): ...
    def snapshot(self, universe: list[str]) -> AccountSnapshot: ...
    def short_availability(
        self, symbols: list[str]
    ) -> dict[str, ShortAvailability]: ...
    def submit(self, intent: OrderIntent): ...


def append_performance_snapshot(
    snapshot: AccountSnapshot,
    path: str | Path,
    timestamp: pd.Timestamp | None = None,
) -> pd.Series:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    observed_at = pd.Timestamp.now(tz="UTC") if timestamp is None else pd.Timestamp(timestamp)
    if observed_at.tzinfo is None:
        observed_at = observed_at.tz_localize("UTC")
    session_date = observed_at.date().isoformat()
    history = pd.read_csv(output) if output.exists() else pd.DataFrame()
    if len(history):
        baseline_equity = float(history.iloc[0]["baseline_equity"])
        history = history[history["session_date"] != session_date]
    else:
        baseline_equity = snapshot.equity
    row = {
        "observed_at": observed_at.isoformat(),
        "session_date": session_date,
        "equity": snapshot.equity,
        "last_equity": snapshot.last_equity,
        "cash": snapshot.cash,
        "daily_return": (
            snapshot.equity / snapshot.last_equity - 1
            if snapshot.last_equity > 0
            else 0.0
        ),
        "baseline_equity": baseline_equity,
        "cumulative_return": (
            snapshot.equity / baseline_equity - 1 if baseline_equity > 0 else 0.0
        ),
        "managed_positions": json.dumps(snapshot.positions, sort_keys=True),
        "open_order_symbols": json.dumps(sorted(snapshot.open_order_symbols)),
        "unmanaged_notional": snapshot.unmanaged_notional,
    }
    history = pd.concat([history, pd.DataFrame([row])], ignore_index=True)
    history = history.sort_values("observed_at")
    atomic_write_dataframe_csv(history, output, index=False)
    return pd.Series(row)
