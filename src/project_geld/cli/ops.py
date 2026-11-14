"""Unattended operation: universe refresh and the paper runners."""

from __future__ import annotations

import os
from pathlib import Path
import tomllib

from project_geld.logs import get_logger
from project_geld.runner import (
    LockBusy,
    RunnerContext,
    alpaca_session_lookup,
    run_close_check,
    run_daily,
    run_intraday,
)

log = get_logger(__name__)
RUNTIME_CONFIG = "runtime-config.toml"


def _source(args) -> tuple[Path, dict]:
    path = Path(args.config)
    return path, tomllib.loads(path.read_text(encoding="utf-8"))


def _context(args, source: Path, config: dict, refresh: bool) -> RunnerContext:
    if getattr(args, "log_json", None):
        # Child geld processes append to the same structured log.
        os.environ["GELD_LOG_JSON"] = str(Path(args.log_json).resolve())
    profile = config.get("account", {}).get("credential_profile", "")
    output = Path(args.output)

    def do_refresh() -> Path:
        from project_geld.universe_refresh import refresh as refresh_universe

        return refresh_universe(source, output)

    return RunnerContext(
        session_lookup=alpaca_session_lookup(profile),
        refresh=do_refresh if refresh else None,
    )


def command_refresh_universe(args) -> None:
    from project_geld.universe_refresh import refresh as refresh_universe

    generated = refresh_universe(Path(args.config), Path(args.output), force=args.force)
    log.info("Paper configuration: %s", generated)


def _exit(code: int) -> None:
    if code:
        raise SystemExit(code)


def command_run_daily(args) -> None:
    source, config = _source(args)
    output = Path(args.output)
    ctx = _context(args, source, config, refresh=not args.no_refresh)
    try:
        code = run_daily(
            ctx,
            output / RUNTIME_CONFIG,
            output,
            args.submit,
            latest_start=args.latest_start,
            max_wait_minutes=args.max_wait_minutes,
        )
    except LockBusy:
        log.info("Another Daily V4 runner holds the lock; exiting.")
        return
    _exit(code)


def command_run_close_check(args) -> None:
    source, config = _source(args)
    ctx = _context(args, source, config, refresh=False)
    universe = Path(args.output) / "universe.csv"
    if universe.exists():
        # Re-point the runtime config at this machine's universe path (no network).
        from project_geld.atomicio import atomic_write_text
        from project_geld.universe_refresh import runtime_config

        atomic_write_text(
            Path(args.output) / RUNTIME_CONFIG,
            runtime_config(source.read_text(encoding="utf-8"), universe),
        )
    _exit(
        run_close_check(
            ctx,
            Path(args.output) / RUNTIME_CONFIG,
            Path(args.close_output),
            delay_minutes=args.delay_minutes,
            max_late_minutes=args.max_late_minutes,
        )
    )


def command_run_intraday(args) -> None:
    source, config = _source(args)
    output = Path(args.output)
    ctx = _context(args, source, config, refresh=not args.no_refresh)
    try:
        code = run_intraday(
            ctx,
            output / RUNTIME_CONFIG,
            output,
            args.submit,
            bar_minutes=int(config.get("intraday", {}).get("bar_minutes", 15)),
            retry_seconds=args.retry_seconds,
        )
    except LockBusy:
        log.info("Another Intra V15 runner holds the lock; exiting.")
        return
    _exit(code)


def command_performance_report(args) -> None:
    from project_geld.performance_report import (
        alpaca_sources,
        build_report,
        default_accounts,
        write_report,
    )

    factory = spy = None
    if not args.offline:
        factory, spy = alpaca_sources()
    report = build_report(
        default_accounts(),
        backtest=args.backtest and not args.offline,
        source_factory=factory,
        benchmark_closes=spy,
        intraday_days=args.intraday_days,
    )
    path = write_report(report, Path(args.report_output))
    log.info("%s", path.read_text(encoding="utf-8"))
    log.info("Report written: %s (and report.json)", path)


def add_ops_commands(subparsers) -> None:
    refresh = subparsers.add_parser(
        "refresh-universe",
        help="Refresh the live universe (monthly) and write <output>/runtime-config.toml.",
    )
    refresh.add_argument("--output", required=True)
    refresh.add_argument("--force", action="store_true")
    refresh.set_defaults(func=command_refresh_universe)

    daily = subparsers.add_parser(
        "run-daily",
        help="Unattended Daily V4 runner: refresh universe, wait for the open, run one cycle.",
    )
    daily.add_argument("--output", default="artifacts/paper-daily-v4")
    daily.add_argument("--submit", action="store_true")
    daily.add_argument("--no-refresh", action="store_true")
    daily.add_argument("--latest-start", help="Skip this launch after HH:MM ET (dual-UTC schedulers).")
    daily.add_argument("--max-wait-minutes", type=float, help="Skip this launch if the open is further away.")
    daily.set_defaults(func=command_run_daily)

    close = subparsers.add_parser(
        "run-close-check",
        help="Daily V4 close reconciliation once the delayed close bar is available (read-only).",
    )
    close.add_argument("--output", default="artifacts/paper-daily-v4")
    close.add_argument("--close-output", default="artifacts/paper-daily-v4-close")
    close.add_argument("--delay-minutes", type=float, default=20.0)
    close.add_argument("--max-late-minutes", type=float,
                       help="Skip launches this long after the close bar is available (dual-UTC schedulers).")
    close.set_defaults(func=command_run_close_check)

    intraday = subparsers.add_parser(
        "run-intraday",
        help="Unattended Intra V15 session loop: one cycle per completed bar.",
    )
    intraday.add_argument("--output", default="artifacts/paper-intra-v15")
    intraday.add_argument("--submit", action="store_true")
    intraday.add_argument("--no-refresh", action="store_true")
    intraday.add_argument("--retry-seconds", type=float, default=30.0)
    intraday.set_defaults(func=command_run_intraday)

    report = subparsers.add_parser(
        "performance-report",
        help="Paper results vs SPY, runner health and (optionally) recent backtest replays.",
    )
    report.add_argument("--backtest", action="store_true",
                        help="Also replay the deployed configs over recent data (downloads bars).")
    report.add_argument("--offline", action="store_true",
                        help="Use local logs only: no SPY comparison and no replays.")
    report.add_argument("--intraday-days", type=int, default=120)
    report.add_argument("--report-output", default="artifacts/performance-report")
    report.set_defaults(func=command_performance_report)
