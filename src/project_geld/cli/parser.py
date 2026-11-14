"""Argument parser and entry point for the ``geld`` command."""

from __future__ import annotations

import argparse

from project_geld.cli.candidates import (
    command_candidate_shadow,
    command_import_candidate,
    command_revalidate_batch,
    command_revalidate_candidate,
    command_validate_candidate,
)
from project_geld.cli.ops import add_ops_commands
from project_geld.cli.paper_commands import (
    command_adopt_positions,
    command_daily_close_check,
    command_intraday_paper,
    command_intraday_shadow,
    command_paper,
    command_paper_status,
    command_reset_kill_switch,
)
from project_geld.cli.research import (
    command_backtest,
    command_experiment,
    command_intraday_backtest,
)
from project_geld.logs import configure_logging, get_logger
from project_geld.strategies.registry import available_strategies

log = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="geld", description="Project Geld research and Alpaca paper engine")
    parser.add_argument("--config", default="config.example.toml")
    parser.add_argument(
        "--log-json",
        help="Also append structured JSON-lines logs to this file (or set GELD_LOG_JSON).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list-strategies")
    list_parser.set_defaults(func=lambda args: print("\n".join(available_strategies())))

    backtest_parser = subparsers.add_parser("backtest")
    backtest_parser.add_argument("--source", choices=["alpaca", "csv", "synthetic"], default="synthetic")
    backtest_parser.add_argument("--csv")
    backtest_parser.add_argument("--start")
    backtest_parser.add_argument("--end")
    backtest_parser.add_argument("--seed", type=int, default=7)
    backtest_parser.add_argument("--output", default="artifacts/backtest")
    backtest_parser.set_defaults(func=command_backtest)

    experiment_parser = subparsers.add_parser("experiment")
    experiment_parser.add_argument("--source", choices=["alpaca", "csv", "synthetic"], default="synthetic")
    experiment_parser.add_argument("--csv")
    experiment_parser.add_argument("--start")
    experiment_parser.add_argument("--end")
    experiment_parser.add_argument("--seed", type=int, default=7)
    experiment_parser.add_argument("--strategy", choices=available_strategies())
    experiment_parser.add_argument("--grid", action="append", required=True)
    experiment_parser.add_argument("--train-fraction", type=float, default=0.70)
    experiment_parser.add_argument("--show", type=int, default=10)
    experiment_parser.add_argument("--output", default="artifacts/experiments/results.csv")
    experiment_parser.set_defaults(func=command_experiment)

    paper_parser = subparsers.add_parser("paper-once")
    paper_parser.add_argument("--submit", action="store_true", help="Submit to Alpaca paper; default only plans orders")
    paper_parser.add_argument("--output", default="artifacts/paper")
    paper_parser.set_defaults(func=command_paper)

    status_parser = subparsers.add_parser("paper-status")
    status_parser.add_argument("--output", default="artifacts/paper")
    status_parser.set_defaults(func=command_paper_status)

    close_parser = subparsers.add_parser("daily-close-check")
    close_parser.add_argument("--output", default="artifacts/paper-daily-v4-close")
    close_parser.set_defaults(func=command_daily_close_check)

    adopt_parser = subparsers.add_parser(
        "adopt-positions",
        help="Add stranded outside-universe positions to the strategy ledger (no orders).",
    )
    adopt_selection = adopt_parser.add_mutually_exclusive_group()
    adopt_selection.add_argument(
        "--symbols", help="Comma-separated unmanaged positions to adopt."
    )
    adopt_selection.add_argument(
        "--all",
        action="store_true",
        help="Adopt every unmanaged position (only on an account dedicated to this strategy).",
    )
    adopt_parser.set_defaults(func=command_adopt_positions)

    reset_parser = subparsers.add_parser(
        "reset-kill-switch",
        help="Re-enable a latched Intra V15 shortfall kill-switch from now on.",
    )
    reset_parser.add_argument("--output", default="artifacts/paper-intra-v15")
    reset_parser.set_defaults(func=command_reset_kill_switch)

    intraday_backtest = subparsers.add_parser("intraday-backtest")
    intraday_backtest.add_argument(
        "--source", choices=["alpaca", "csv"], default="alpaca"
    )
    intraday_backtest.add_argument("--csv")
    intraday_backtest.add_argument("--start")
    intraday_backtest.add_argument("--end")
    intraday_backtest.add_argument("--output", default="artifacts/intraday-backtest")
    intraday_backtest.add_argument(
        "--native-bars",
        action="store_true",
        help="Fetch native Alpaca intraday bars instead of aggregating one-minute bars",
    )
    intraday_backtest.set_defaults(func=command_intraday_backtest)

    intraday_paper = subparsers.add_parser("intraday-paper-once")
    intraday_paper.add_argument(
        "--submit", action="store_true", help="Submit to the configured Alpaca paper account"
    )
    intraday_paper.add_argument("--output", default="artifacts/intraday-paper")
    intraday_paper.add_argument(
        "--flatten-all",
        action="store_true",
        help="Target zero for every managed position (used before an early close).",
    )
    intraday_paper.set_defaults(func=command_intraday_paper)
    intraday_shadow = subparsers.add_parser("intraday-shadow-once")
    intraday_shadow.add_argument("--output", default="artifacts/intraday-shadow")
    intraday_shadow.set_defaults(func=command_intraday_shadow)

    validate_candidate = subparsers.add_parser(
        "validate-candidate", help="Offline validation of an Emberforge candidate bundle (no import)."
    )
    validate_candidate.add_argument("--bundle", required=True, help="Path to a candidate_bundle_v1 JSON file.")
    validate_candidate.set_defaults(func=command_validate_candidate)

    import_candidate = subparsers.add_parser(
        "import-candidate", help="Validate + quarantine a candidate bundle (research-only; never enables trading)."
    )
    import_candidate.add_argument("--bundle", required=True)
    import_candidate.add_argument("--quarantine-dir", default="artifacts/candidates/quarantine")
    import_candidate.set_defaults(func=command_import_candidate)

    revalidate = subparsers.add_parser(
        "revalidate-candidate",
        help="Independent OOS re-validation of a quarantined candidate -> promote/reject verdict.",
    )
    revalidate.add_argument("--bundle", required=True, help="Quarantined record JSON (or a raw bundle).")
    revalidate.add_argument("--bars", default="artifacts/research-broad/selected-bars.csv.gz",
                            help="Point-in-time OOS bars (CSV/gz) for independent re-validation.")
    revalidate.add_argument("--start")
    revalidate.add_argument("--end")
    revalidate.add_argument("--max-symbols", type=int, default=None,
                            help="Cap the bound universe in stable symbol order for a faster diagnostic run.")
    revalidate.add_argument("--trials", type=int, default=None,
                            help="Minimum evaluations on this holdout, including this one (deflated Sharpe). The holdout "
                                 "registry count is used when larger; the bundle's research_trials also counts "
                                 "unless the holdout follows its discovery_window.")
    revalidate.add_argument("--holdout-registry", default=None,
                            help="Holdout evaluation registry (default: holdout_evaluations.json beside the verdict directory).")
    revalidate.add_argument("--no-holdout-registry", action="store_true",
                            help="Do not count or record this evaluation against the holdout.")
    revalidate.add_argument("--advance-state", action="store_true",
                            help="Persist the state transition (validated_oos -> shadow/rejected).")
    revalidate.add_argument("--output", default="artifacts/candidates/verdicts")
    revalidate.set_defaults(func=command_revalidate_candidate)

    revalidate_batch = subparsers.add_parser(
        "revalidate-batch",
        help="Batch OOS re-validation with FDR across candidates (guards against lucky passes).",
    )
    revalidate_batch.add_argument("--quarantine-dir", default="artifacts/candidates/quarantine")
    revalidate_batch.add_argument("--bars", default="artifacts/research-broad/selected-bars.csv.gz")
    revalidate_batch.add_argument("--start")
    revalidate_batch.add_argument("--end")
    revalidate_batch.add_argument("--fdr-q", type=float, default=0.10, help="Benjamini-Hochberg FDR level.")
    revalidate_batch.add_argument("--max-symbols", type=int, default=None)
    revalidate_batch.add_argument("--trials", type=int, default=None,
                                  help="Evaluations on this holdout including this batch; at least the batch size is used.")
    revalidate_batch.add_argument("--holdout-registry", default=None)
    revalidate_batch.add_argument("--no-holdout-registry", action="store_true")
    revalidate_batch.add_argument("--advance-state", action="store_true")
    revalidate_batch.add_argument("--output", default="artifacts/candidates/verdicts")
    revalidate_batch.set_defaults(func=command_revalidate_batch)

    candidate_shadow = subparsers.add_parser(
        "candidate-shadow-once",
        help="Append an offline shadow snapshot for a shadow-state candidate (no orders).",
    )
    candidate_shadow.add_argument("--bundle", required=True, help="Quarantined record JSON (state must be 'shadow').")
    candidate_shadow.add_argument("--bars", default="artifacts/research-broad/selected-bars.csv.gz")
    candidate_shadow.add_argument("--max-symbols", type=int, default=None)
    candidate_shadow.add_argument("--output", default="artifacts/candidates/shadow")
    candidate_shadow.set_defaults(func=command_candidate_shadow)

    add_ops_commands(subparsers)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(json_path=args.log_json)
    try:
        args.func(args)
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        log.error("%s failed: %s", args.command, exc, extra={"command": args.command})
        parser.error(str(exc))
