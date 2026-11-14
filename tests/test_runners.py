"""Unattended runners: calendar, windows, retries, early close, locking."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from project_geld.runner import (
    LockBusy,
    RunnerContext,
    process_lock,
    run_close_check,
    run_daily,
    run_intraday,
)

ET = "America/New_York"


def at(clock: str, day: str = "2026-11-16") -> pd.Timestamp:
    return pd.Timestamp(f"{day} {clock}", tz=ET)


def regular(day: str = "2026-11-16"):
    return at("09:30", day), at("16:00", day)


class Fake:
    """Clock that advances only when the runner sleeps or runs a command."""

    def __init__(self, start: pd.Timestamp, session=regular(), codes=None, command_minutes=1.0,
                 refresh_ok=True):
        self.time = start
        self.session = session
        self.codes = list(codes or [])
        self.commands: list[list[str]] = []
        self.command_minutes = command_minutes
        self.refresh_ok = refresh_ok
        self.refreshes = 0

    def now(self):
        return self.time

    def sleep(self, seconds):
        self.time += pd.Timedelta(seconds=seconds)

    def run(self, argv):
        self.commands.append(argv)
        self.time += pd.Timedelta(minutes=self.command_minutes)
        return self.codes.pop(0) if self.codes else 0

    def refresh(self):
        self.refreshes += 1
        if not self.refresh_ok:
            raise ValueError("coverage below 90%")

    def context(self, refresh=True):
        return RunnerContext(session_lookup=lambda _: self.session, now=self.now, sleep=self.sleep,
                             run=self.run, refresh=self.refresh if refresh else None)


def test_daily_waits_for_the_open_then_runs_one_cycle(tmp_path):
    fake = Fake(at("09:25"))
    assert run_daily(fake.context(), tmp_path / "rt.toml", tmp_path, submit=True) == 0
    assert fake.refreshes == 1
    assert len(fake.commands) == 1 and fake.commands[0][-1] == "--submit"
    assert "paper-once" in fake.commands[0]
    assert fake.time >= at("09:31")
    beat = json.loads((tmp_path / "runner-heartbeat.json").read_text())
    assert beat["phase"] == "stopped" and beat["last_exit_code"] == 0


def test_daily_skips_holidays_and_launches_outside_the_window(tmp_path):
    holiday = Fake(at("09:25"), session=None)
    assert run_daily(holiday.context(), tmp_path / "rt.toml", tmp_path, submit=True) == 0
    assert holiday.commands == []
    late = Fake(at("10:35"))  # the summer duplicate of a dual-UTC schedule
    assert run_daily(late.context(), tmp_path / "rt.toml", tmp_path, True, latest_start="10:00") == 0
    assert late.commands == []
    early = Fake(at("08:35"))  # the winter duplicate
    assert run_daily(early.context(), tmp_path / "rt.toml", tmp_path, True, max_wait_minutes=10) == 0
    assert early.commands == []


def test_daily_retries_failed_cycles(tmp_path):
    fake = Fake(at("09:40"), codes=[1, 1, 0])
    assert run_daily(fake.context(), tmp_path / "rt.toml", tmp_path, submit=False) == 0
    assert len(fake.commands) == 3 and "--submit" not in fake.commands[0]
    failing = Fake(at("09:40"), codes=[2, 2, 2])
    assert run_daily(failing.context(), tmp_path / "rt.toml", tmp_path, submit=False) == 2


def test_daily_uses_the_previous_universe_when_refresh_fails(tmp_path):
    runtime = tmp_path / "rt.toml"
    without = Fake(at("09:40"), refresh_ok=False)
    assert run_daily(without.context(), runtime, tmp_path, submit=False) == 2
    assert without.commands == []
    runtime.write_text("[universe]\n")
    fallback = Fake(at("09:40"), refresh_ok=False)
    assert run_daily(fallback.context(), runtime, tmp_path, submit=False) == 0
    assert fallback.refreshes == 1 and len(fallback.commands) == 1


def test_intraday_runs_one_cycle_per_bar_until_the_stop(tmp_path):
    fake = Fake(at("09:20"), command_minutes=0.5)
    assert run_intraday(fake.context(), tmp_path / "rt.toml", tmp_path, submit=True) == 0
    cycles = [c for c in fake.commands if "intraday-paper-once" in c]
    # 09:31, 09:46, ..., 15:46 -> 26 completed-bar cycles; no flatten on a full day.
    assert len(cycles) == 26
    assert not any("--flatten-all" in c for c in fake.commands)
    assert fake.time <= at("16:00")


def test_intraday_flattens_before_an_early_close(tmp_path):
    fake = Fake(at("09:20"), session=(at("09:30", "2026-11-27"), at("13:00", "2026-11-27")),
                command_minutes=0.5)
    fake.time = at("09:20", "2026-11-27")
    assert run_intraday(fake.context(), tmp_path / "rt.toml", tmp_path, submit=True) == 0
    flattens = [c for c in fake.commands if "--flatten-all" in c]
    assert len(flattens) == 1 and "--submit" in flattens[0]
    assert fake.commands[-1] is flattens[0]
    assert fake.time < at("13:00", "2026-11-27")


def test_intraday_pauses_planning_while_the_universe_cannot_refresh(tmp_path):
    fake = Fake(at("15:20"), refresh_ok=False)
    assert run_intraday(fake.context(), tmp_path / "rt.toml", tmp_path, submit=True) == 0
    assert fake.commands == []
    assert fake.refreshes >= 1


def test_close_check_waits_for_the_delayed_close_bar(tmp_path):
    runtime = tmp_path / "rt.toml"
    runtime.write_text("[universe]\n")
    early = Fake(at("16:05"))
    assert run_close_check(early.context(), runtime, tmp_path / "close") == 0
    assert early.commands == []
    ready = Fake(at("16:25"))
    assert run_close_check(ready.context(), runtime, tmp_path / "close") == 0
    assert "daily-close-check" in ready.commands[0]


def test_process_lock_is_exclusive(tmp_path):
    lock = tmp_path / "runner.lock"
    with process_lock(lock):
        with pytest.raises(LockBusy):
            with process_lock(lock):
                pass
    with process_lock(lock):
        pass


def test_intraday_never_acts_after_an_early_close(tmp_path):
    day = "2026-11-27"
    fake = Fake(at("13:20", day), session=(at("09:30", day), at("13:00", day)))
    assert run_intraday(fake.context(), tmp_path / "rt.toml", tmp_path, submit=True) == 0
    assert fake.commands == []  # a late start or restart after the close does nothing


def test_failing_flatten_is_capped_and_stops_at_the_close(tmp_path):
    day = "2026-11-27"
    fake = Fake(at("12:44", day), session=(at("09:30", day), at("13:00", day)),
                codes=[0] + [2] * 50)
    code = run_intraday(fake.context(), tmp_path / "rt.toml", tmp_path, submit=True)
    flattens = [c for c in fake.commands if "--flatten-all" in c]
    assert 1 <= len(flattens) <= 5
    assert code != 0
    assert fake.time < at("13:30", day)


def test_close_check_skips_the_late_duplicate_launch(tmp_path):
    runtime = tmp_path / "rt.toml"
    runtime.write_text("[universe]\n")
    late = Fake(at("17:30"))  # 21:30 UTC in summer
    assert run_close_check(late.context(), runtime, tmp_path / "close", max_late_minutes=45) == 0
    assert late.commands == []


def test_calendar_lookup_errors_are_retried(tmp_path):
    fake = Fake(at("09:40"))
    calls = {"n": 0}

    def flaky(_):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("calendar timeout")
        return regular()

    ctx = fake.context()
    ctx.session_lookup = flaky
    assert run_daily(ctx, tmp_path / "rt.toml", tmp_path, submit=False) == 0
    assert calls["n"] == 3 and len(fake.commands) == 1
