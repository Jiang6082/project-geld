"""The ``geld`` command line.

Commands live in focused modules (``research``, ``paper_commands``,
``candidates``, ``ops``); this package re-exports them and the entry point so
``project_geld.cli:main`` and ``python -m project_geld.cli`` keep working.
"""

from project_geld.cli.candidates import (
    command_candidate_shadow,
    command_import_candidate,
    command_revalidate_batch,
    command_revalidate_candidate,
    command_validate_candidate,
)
from project_geld.cli.paper_commands import (
    command_adopt_positions,
    command_daily_close_check,
    command_intraday_paper,
    command_intraday_shadow,
    command_paper,
    command_paper_status,
    command_reset_kill_switch,
)
from project_geld.cli.parser import build_parser, main
from project_geld.cli.research import (
    command_backtest,
    command_experiment,
    command_intraday_backtest,
)

__all__ = [
    "build_parser",
    "command_adopt_positions",
    "command_backtest",
    "command_candidate_shadow",
    "command_daily_close_check",
    "command_experiment",
    "command_import_candidate",
    "command_intraday_backtest",
    "command_intraday_paper",
    "command_intraday_shadow",
    "command_paper",
    "command_paper_status",
    "command_reset_kill_switch",
    "command_revalidate_batch",
    "command_revalidate_candidate",
    "command_validate_candidate",
    "main",
]
