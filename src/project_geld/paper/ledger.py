"""Ownership ledger: every symbol a paper strategy holds or trades."""

from __future__ import annotations

import json
from pathlib import Path

from project_geld.atomicio import atomic_write_text
from project_geld.config import PaperConfig
from project_geld.paper.account import AccountSnapshot


def managed_ledger_path(paper: PaperConfig) -> Path:
    """Ledger of symbols this strategy has traded, kept beside its state file.

    The name includes the client-order prefix so strategies that share a state
    directory never share a ledger.
    """
    prefix = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in paper.client_order_prefix
    ) or "geld"
    return paper.state_file.with_name(f"managed_symbols-{prefix}.json")


def load_managed_ledger(path: str | Path) -> set[str]:
    ledger = Path(path)
    if not ledger.exists():
        return set()
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    return {str(symbol).upper() for symbol in payload.get("symbols", [])}


def update_managed_ledger(
    path: str | Path,
    snapshot: AccountSnapshot,
    traded_symbols: set[str] | list[str] = (),
    adopted_symbols: set[str] | list[str] = (),
) -> set[str]:
    """Record every symbol the strategy holds or trades; prune finished ones.

    Universe members are recorded too: a name held today can leave the universe
    at the next refresh, and the ledger is what lets the planner find and
    liquidate it then. A symbol is dropped once it has no position, no open order
    and was not traded this cycle. ``snapshot`` must cover the universe plus the
    current ledger so its positions describe everything the strategy owns.
    """
    held = {
        symbol.upper()
        for symbol, quantity in snapshot.positions.items()
        if abs(float(quantity)) > 1e-12
    }
    open_orders = {symbol.upper() for symbol in snapshot.open_order_symbols}
    traded = {symbol.upper() for symbol in traded_symbols}
    adopted = {symbol.upper() for symbol in adopted_symbols}
    ledger = load_managed_ledger(path)
    retained = {
        symbol
        for symbol in ledger | held | traded
        if symbol in held or symbol in open_orders or symbol in traded
    } | adopted
    # Ledger names still held but left out of this snapshot (for example an
    # unpriced orphan kept unmanaged so it still counts against gross exposure)
    # remain owned by the strategy.
    retained |= ledger & {symbol.upper() for symbol in snapshot.unmanaged_symbols}
    atomic_write_text(path, json.dumps({"symbols": sorted(retained)}, indent=2))
    return retained


def outside_universe_symbols(paper: PaperConfig, universe: list[str]) -> list[str]:
    """Ledger symbols that have left the universe and must be liquidated."""
    members = {symbol.upper() for symbol in universe}
    return sorted(load_managed_ledger(managed_ledger_path(paper)) - members)
