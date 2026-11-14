"""Unattended paper runners that work under systemd, cron or GitHub Actions.

These replace the Windows-only PowerShell loops with the same behaviour:

- a non-blocking process lock so overlapping launches exit quietly;
- a heartbeat file (``runner-heartbeat.json``) with the current phase;
- the market calendar decides whether today is a session (holidays and early
  closes included), so runners exit cleanly on closed days;
- the live universe is refreshed (monthly) before planning;
- failed cycles are retried; order IDs are deterministic, so retries never
  duplicate orders.

Each geld command runs in a child process, so one failed cycle cannot take the
session loop down with it.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable, Iterator

import pandas as pd

from project_geld.atomicio import atomic_write_text
from project_geld.logs import get_logger

log = get_logger(__name__)

EASTERN = "America/New_York"
Session = tuple[pd.Timestamp, pd.Timestamp]


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz=EASTERN)


def run_geld(argv: list[str]) -> int:
    """Run one geld command in a child process; returns its exit code."""
    return subprocess.call([sys.executable, "-m", "project_geld.cli", *argv])


def alpaca_session_lookup(profile: str) -> Callable[[pd.Timestamp], Session | None]:
    def lookup(day: pd.Timestamp) -> Session | None:
        from project_geld.paper import AlpacaPaperBroker

        return AlpacaPaperBroker(profile).session_for(day.date())

    return lookup


@dataclass
class RunnerContext:
    """Injectable clock, sleep, calendar and command execution (for tests)."""

    session_lookup: Callable[[pd.Timestamp], Session | None]
    now: Callable[[], pd.Timestamp] = _now
    sleep: Callable[[float], None] = time.sleep
    run: Callable[[list[str]], int] = run_geld
    refresh: Callable[[], Path] | None = None
    events: list[str] = field(default_factory=list)


class LockBusy(RuntimeError):
    pass


@contextmanager
def process_lock(path: Path) -> Iterator[None]:
    """Exclusive, non-blocking lock held for the runner's lifetime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    try:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise LockBusy(str(path)) from error
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        yield
    finally:
        handle.close()


class Heartbeat:
    def __init__(self, path: Path, runner: str, dry_run: bool, now: Callable[[], pd.Timestamp]):
        self.path, self.runner, self.dry_run, self.now = path, runner, dry_run, now

    def __call__(self, phase: str, next_run: pd.Timestamp | None = None, exit_code: int | None = None) -> None:
        record = {
            "runner": self.runner,
            "process_id": os.getpid(),
            "updated_at": self.now().tz_convert("UTC").isoformat(),
            "phase": phase,
            "dry_run": self.dry_run,
            "next_run": None if next_run is None else pd.Timestamp(next_run).tz_convert("UTC").isoformat(),
            "last_exit_code": exit_code,
        }
        atomic_write_text(self.path, json.dumps(record, indent=2) + "\n")


def _wait_until(ctx: RunnerContext, target: pd.Timestamp, beat: Heartbeat, phase: str) -> None:
    while True:
        remaining = (target - ctx.now()).total_seconds()
        if remaining <= 0:
            return
        beat(phase, next_run=target)
        ctx.sleep(min(remaining, 60.0))


def _today_session(ctx: RunnerContext, attempts: int = 3, wait_seconds: float = 30.0) -> Session | None:
    """Today's session from the market calendar, retrying transient API errors."""
    for attempt in range(1, attempts + 1):
        try:
            return ctx.session_lookup(ctx.now())
        except Exception as error:
            if attempt == attempts:
                raise
            log.warning("Market calendar lookup failed (%s); retrying.", error)
            ctx.sleep(wait_seconds)
    return None


def _refresh(ctx: RunnerContext, runtime_config: Path) -> bool:
    """Refresh the universe; True when a usable runtime config exists.

    A failed refresh never publishes a partial universe. If an earlier runtime
    config exists, planning continues on that universe (the paper command's
    45-day staleness guard still applies); otherwise planning pauses.
    """
    if ctx.refresh is None:
        return runtime_config.exists()
    try:
        ctx.refresh()
        return True
    except Exception as error:
        log.warning("Universe refresh failed (%s); previous snapshot retained.", type(error).__name__)
        if isinstance(error, (ValueError, FileNotFoundError)):
            log.warning("%s", error)
        if runtime_config.exists():
            log.warning("Continuing with the previously published universe.")
            return True
        return False


def run_daily(
    ctx: RunnerContext,
    runtime_config: Path,
    output: Path,
    submit: bool,
    *,
    latest_start: str | None = None,
    max_wait_minutes: float | None = None,
    attempts: int = 3,
    retry_seconds: float = 60.0,
) -> int:
    """One Daily V4 cycle after the open (cadence state decides whether it trades).

    ``latest_start`` (HH:MM ET) and ``max_wait_minutes`` let a scheduler that
    fires at two UTC times (daylight saving) skip the run that falls outside
    the intended window.
    """
    beat = Heartbeat(output / "runner-heartbeat.json", "daily_v4", not submit, ctx.now)
    with process_lock(output / "runner.lock"):
        beat("starting")
        session = _today_session(ctx)
        if session is None:
            log.info("No regular session today; Daily V4 runner exiting.")
            beat("stopped", exit_code=0)
            return 0
        open_, close = session
        start_at = open_ + pd.Timedelta(minutes=1)
        now = ctx.now()
        if now >= close:
            log.info("Regular session is over; Daily V4 runner exiting.")
            beat("stopped", exit_code=0)
            return 0
        if latest_start is not None:
            hour, minute = (int(part) for part in latest_start.split(":"))
            if now > now.normalize() + pd.Timedelta(hours=hour, minutes=minute):
                log.info("Outside the start window (after %s ET); skipping this launch.", latest_start)
                beat("stopped", exit_code=0)
                return 0
        if max_wait_minutes is not None and (start_at - now).total_seconds() > max_wait_minutes * 60:
            log.info("Open is more than %.0f minutes away; skipping this launch.", max_wait_minutes)
            beat("stopped", exit_code=0)
            return 0
        beat("checking_universe")
        if not _refresh(ctx, runtime_config):
            log.error("No usable universe; Daily V4 cycle not started.")
            beat("universe_refresh_error", exit_code=2)
            return 2
        _wait_until(ctx, start_at, beat, "waiting_for_open")
        command = ["--config", str(runtime_config), "paper-once", "--output", str(output)]
        if submit:
            command.append("--submit")
        code = 1
        for attempt in range(1, attempts + 1):
            beat("running_cycle")
            log.info("Starting Daily V4 cycle (attempt %d/%d).", attempt, attempts)
            code = ctx.run(command)
            if code == 0:
                log.info("Daily V4 cycle completed successfully.")
                beat("stopped", exit_code=0)
                return 0
            log.warning("Daily V4 cycle failed with exit code %d.", code)
            beat("cycle_error", exit_code=code)
            if attempt < attempts and ctx.now() < close:
                ctx.sleep(retry_seconds)
        beat("stopped", exit_code=code)
        return code


def run_close_check(
    ctx: RunnerContext,
    runtime_config: Path,
    output: Path,
    *,
    delay_minutes: float = 20.0,
    max_late_minutes: float | None = None,
) -> int:
    """Read-only Daily V4 close reconciliation once the delayed close bar exists.

    ``max_late_minutes`` skips launches long after the bar became available,
    so a dual-UTC scheduler records one reconciliation per day.
    """
    session = _today_session(ctx)
    if session is None:
        log.info("No regular session today; close check skipped.")
        return 0
    ready = session[1] + pd.Timedelta(minutes=delay_minutes)
    if ctx.now() < ready:
        log.info("Close bar not available until %s; close check skipped.", ready.strftime("%H:%M %Z"))
        return 0
    if max_late_minutes is not None and ctx.now() > ready + pd.Timedelta(minutes=max_late_minutes):
        log.info("Close check window passed; skipping this launch.")
        return 0
    if not runtime_config.exists():
        log.error("Runtime config %s not found; run a Daily V4 cycle first.", runtime_config)
        return 2
    return ctx.run(["--config", str(runtime_config), "daily-close-check", "--output", str(output)])


def run_intraday(
    ctx: RunnerContext,
    runtime_config: Path,
    output: Path,
    submit: bool,
    *,
    bar_minutes: int = 15,
    retry_seconds: float = 30.0,
    refresh_retry_seconds: float = 300.0,
    stop_before_close_minutes: float = 10.0,
    early_close_flatten_minutes: float = 15.0,
    max_flatten_attempts: int = 5,
) -> int:
    """Intra V15 session loop: one cycle per completed bar until the stop time.

    On an early-close day (close before 16:00 ET) the runner sends a flatten-all
    cycle ``early_close_flatten_minutes`` before the close, because the
    strategy's own 15:30/15:45 exits would come after the market has closed.
    """
    beat = Heartbeat(output / "runner-heartbeat.json", "intra_v15", not submit, ctx.now)
    with process_lock(output / "runner.lock"):
        beat("starting")
        session = _today_session(ctx)
        if session is None:
            log.info("No regular session today; Intra V15 runner exiting.")
            beat("stopped", exit_code=0)
            return 0
        open_, close = session
        first = open_ + pd.Timedelta(minutes=1)
        regular_close = close.normalize() + pd.Timedelta(hours=16)
        early_close = close < regular_close
        flatten_at = close - pd.Timedelta(minutes=early_close_flatten_minutes)
        stop = close - pd.Timedelta(minutes=stop_before_close_minutes)
        flattened = False
        flatten_attempts = 0
        last_code = 0
        while True:
            now = ctx.now()
            if now >= close:
                # Never act after the close (late start, restart or a slow flatten);
                # exiting also frees the lock for the next session.
                log.info("Market is closed; Intra V15 runner exiting.")
                break
            if now < first:
                _wait_until(ctx, first, beat, "waiting_for_open")
                continue
            if early_close and not flattened and now >= flatten_at:
                if flatten_attempts >= max_flatten_attempts:
                    log.error("Flatten-all failed %d times; positions may remain open.", flatten_attempts)
                    last_code = last_code or 3
                    break
                command = ["--config", str(runtime_config), "intraday-paper-once",
                           "--output", str(output), "--flatten-all"]
                if submit:
                    command.append("--submit")
                log.info("Early close at %s; flattening all managed positions.", close.strftime("%H:%M"))
                beat("flattening")
                flatten_attempts += 1
                last_code = ctx.run(command)
                if last_code == 0:
                    flattened = True
                    continue
                ctx.sleep(max(5.0, min(retry_seconds, 30.0)))
                continue
            if now > stop or (early_close and flattened):
                log.info("Intra V15 paper window complete; runner exiting.")
                break
            beat("checking_universe")
            if not _refresh(ctx, runtime_config):
                log.warning("Paper planning paused; retrying the universe in %.0f seconds.", refresh_retry_seconds)
                beat("universe_refresh_error", exit_code=2)
                ctx.sleep(max(refresh_retry_seconds, retry_seconds))
                continue
            command = ["--config", str(runtime_config), "intraday-paper-once", "--output", str(output)]
            if submit:
                command.append("--submit")
            beat("running_cycle")
            last_code = ctx.run(command)
            if last_code != 0:
                log.warning("Cycle failed with exit code %d; retrying in %.0f seconds.", last_code, retry_seconds)
                beat("cycle_error", exit_code=last_code)
                ctx.sleep(max(5.0, retry_seconds))
                continue
            elapsed = max((ctx.now() - first).total_seconds() / 60.0, 0.0)
            next_slot = first + pd.Timedelta(minutes=(int(elapsed // bar_minutes) + 1) * bar_minutes)
            if early_close and next_slot > flatten_at:
                next_slot = flatten_at
            if next_slot > stop and not (early_close and not flattened):
                log.info("Intra V15 paper window complete; runner exiting.")
                break
            beat("waiting_for_cycle", next_run=next_slot, exit_code=0)
            _wait_until(ctx, next_slot, beat, "waiting_for_cycle")
        beat("stopped", exit_code=last_code)
        return last_code
