"""Alpaca paper execution: planning, submission, ownership and monitoring.

The package is split by concern; every public name is re-exported here so
``from project_geld.paper import ...`` keeps working:

- ``account``: account snapshots, the broker protocol, the performance log
- ``planning``: rebalance cadence state and order planning
- ``ledger``: ownership ledger for symbols a strategy holds or trades
- ``shortfall``: implementation shortfall and the latching kill-switch
- ``broker``: the Alpaca adapter (always ``paper=True``)
- ``cycle``: one planning/submission cycle
"""

from project_geld.paper.account import (
    AccountSnapshot,
    PaperBrokerProtocol,
    ShortAvailability,
    append_performance_snapshot,
)
from project_geld.paper.broker import AlpacaPaperBroker
from project_geld.paper.cycle import align_rebalance_to_latest_bar, run_paper_cycle
from project_geld.paper.ledger import (
    load_managed_ledger,
    managed_ledger_path,
    outside_universe_symbols,
    update_managed_ledger,
)
from project_geld.paper.planning import (
    RISK_REDUCING_REASONS,
    build_rebalance_orders,
    mark_paper_rebalance,
    paper_rebalance_due,
    sequence_orders,
    strategy_order_token,
)
from project_geld.paper.shortfall import (
    KILL_SWITCH_LATCH_FILE,
    KILL_SWITCH_RESET_FILE,
    SHORTFALL_RESCORED_COLUMNS,
    evaluate_kill_switch,
    implementation_shortfall,
    kill_switch_latched,
    load_shortfall_history,
    read_kill_switch_reset,
    record_kill_switch_latch,
    record_kill_switch_reset,
    refresh_shortfall_history,
    shortfall_kill_switch_active,
    trailing_shortfall_bps,
    unresolved_shortfall_since,
)

__all__ = [
    "AccountSnapshot",
    "AlpacaPaperBroker",
    "KILL_SWITCH_LATCH_FILE",
    "KILL_SWITCH_RESET_FILE",
    "PaperBrokerProtocol",
    "RISK_REDUCING_REASONS",
    "SHORTFALL_RESCORED_COLUMNS",
    "ShortAvailability",
    "align_rebalance_to_latest_bar",
    "append_performance_snapshot",
    "build_rebalance_orders",
    "evaluate_kill_switch",
    "implementation_shortfall",
    "kill_switch_latched",
    "load_managed_ledger",
    "load_shortfall_history",
    "managed_ledger_path",
    "mark_paper_rebalance",
    "outside_universe_symbols",
    "paper_rebalance_due",
    "read_kill_switch_reset",
    "record_kill_switch_latch",
    "record_kill_switch_reset",
    "refresh_shortfall_history",
    "run_paper_cycle",
    "sequence_orders",
    "shortfall_kill_switch_active",
    "strategy_order_token",
    "trailing_shortfall_bps",
    "unresolved_shortfall_since",
    "update_managed_ledger",
]
