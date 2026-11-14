"""Follow-up regressions: logging, autocorrelation, holdout registry, phased orders."""

from __future__ import annotations

import argparse
import io
import json
from types import SimpleNamespace

import numpy as np
import pytest

from project_geld.candidates.holdout_registry import (
    holdout_key,
    record_evaluations,
    trials_including,
)
from project_geld.candidates.stats import effective_sample_size, sharpe_moments
from project_geld.config import PaperConfig, RiskConfig
from project_geld.logs import configure_logging, get_logger
from project_geld.models import OrderIntent
from project_geld.paper import AccountSnapshot
from project_geld.paper.cycle import submit_in_phases, wait_for_terminal


# --------------------------------------------------------------------------
# logging
# --------------------------------------------------------------------------
def test_console_output_stays_plain_and_json_lines_carry_extras(tmp_path):
    stream = io.StringIO()
    path = tmp_path / "geld.jsonl"
    configure_logging(json_path=path, stream=stream)
    log = get_logger("project_geld.cli.paper_commands")
    assert log.name == "geld.cli.paper_commands"
    log.info("Paper equity: USD %s", "1,000.00", extra={"account": "daily-v4"})
    log.warning("Warning: something")
    assert stream.getvalue().splitlines() == ["Paper equity: USD 1,000.00", "Warning: something"]
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert records[0]["message"] == "Paper equity: USD 1,000.00"
    assert records[0]["account"] == "daily-v4"
    assert records[0]["level"] == "INFO" and records[1]["level"] == "WARNING"
    configure_logging(stream=io.StringIO())  # reconfiguring replaces, never duplicates
    assert len(get_logger("x").parent.handlers) == 1


def test_lowercase_log_level_is_accepted(monkeypatch):
    monkeypatch.setenv("GELD_LOG_LEVEL", "debug")
    root = configure_logging(stream=io.StringIO())
    assert root.level == 10
    monkeypatch.delenv("GELD_LOG_LEVEL")
    configure_logging(stream=io.StringIO())


# --------------------------------------------------------------------------
# autocorrelation-adjusted sample size
# --------------------------------------------------------------------------
def _ar1(phi, n=20_000, seed=0):
    rng = np.random.default_rng(seed)
    shocks = rng.normal(0, 0.01, n)
    values = np.empty(n)
    values[0] = shocks[0]
    for index in range(1, n):
        values[index] = phi * values[index - 1] + shocks[index]
    return values + 0.0005


def test_positive_autocorrelation_shrinks_the_effective_sample():
    n = 20_000
    persistent = effective_sample_size(_ar1(0.5, n), max_lag=60)
    # Long-run variance factor for AR(1) is (1 + phi) / (1 - phi) = 3.
    assert persistent == pytest.approx(n / 3, rel=0.15)
    assert effective_sample_size(_ar1(0.0, n)) == pytest.approx(n, rel=0.05)
    assert effective_sample_size(_ar1(-0.5, n)) == n  # never credited
    moments = sharpe_moments(_ar1(0.5, 2_000))
    assert moments["n_effective"] < moments["n"]


# --------------------------------------------------------------------------
# holdout registry
# --------------------------------------------------------------------------
def test_holdout_registry_counts_distinct_candidates_per_holdout(tmp_path):
    bars = tmp_path / "bars.csv"
    bars.write_text("timestamp,symbol\n2024-01-01,AAA\n")
    key = holdout_key(bars)
    assert key == holdout_key(bars)
    assert key != holdout_key(bars, start="2024-01-01")
    assert holdout_key(bars, start="2024-01-01") == holdout_key(bars, start="2024-01-01T00:00:00Z")
    registry = tmp_path / "registry.json"
    assert trials_including(registry, key, ["a"]) == 1
    assert record_evaluations(registry, key, [("a", "reject")]) == 1
    assert record_evaluations(registry, key, [("a", "reject")]) == 1  # same candidate again
    assert trials_including(registry, key, ["b"]) == 2
    assert record_evaluations(registry, key, [("b", "reject"), ("c", "promote_to_shadow")]) == 3
    bars.write_text("timestamp,symbol\n2024-01-01,BBB\n")
    assert trials_including(registry, holdout_key(bars), ["a"]) == 1  # different data


def test_revalidate_cli_counts_earlier_candidates_on_the_same_holdout(tmp_path):
    from project_geld.candidates.importer import import_bundle
    from project_geld.cli import command_revalidate_candidate
    from test_candidate_provenance import CONFIG, EXAMPLE, _trending_csv

    bars_csv = tmp_path / "bars.csv"
    _trending_csv(bars_csv)
    output = tmp_path / "verdicts"
    registry = tmp_path / "holdout_evaluations.json"
    key = holdout_key(bars_csv)
    record_evaluations(registry, key, [(f"earlier{i}", "reject") for i in range(7)])
    quarantined = import_bundle(EXAMPLE, quarantine_dir=tmp_path / "q")
    args = argparse.Namespace(
        config=str(CONFIG), bundle=str(quarantined), bars=str(bars_csv), start=None, end=None,
        max_symbols=None, advance_state=False, output=str(output),
    )
    command_revalidate_candidate(args)
    verdict = json.loads(next(output.glob("*.verdict.json")).read_text())
    assert verdict["n_trials"] == 8  # seven earlier candidates plus this one
    assert verdict["holdout_registry"]["distinct_candidates"] == 8
    assert any(gate["name"] == "deflated_sharpe" for gate in verdict["gates"])


# --------------------------------------------------------------------------
# phased submission
# --------------------------------------------------------------------------
def _intent(symbol, side, reason, quantity, price=100.0):
    return OrderIntent(symbol, side, quantity, price, quantity * price, 0.0, f"id-{symbol}", reason)


class PhasedBroker:
    def __init__(self, fills_after=0, cash_after=10_000.0):
        self.submitted, self.polls = [], 0
        self.fills_after, self.cash_after = fills_after, cash_after

    def submit(self, intent):
        self.submitted.append(intent)
        return SimpleNamespace(status="accepted", id=f"o-{intent.symbol}")

    def order_status(self, order_id):
        self.polls += 1
        return "filled" if self.polls > self.fills_after else "new"

    def snapshot(self, universe):
        return AccountSnapshot(equity=20_000, last_equity=20_000, positions={},
                               open_order_symbols=set(), cash=self.cash_after)


class Clock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def test_buys_wait_for_sells_and_fit_the_refreshed_cash():
    broker, clock = PhasedBroker(fills_after=2, cash_after=6_000.0), Clock()
    intents = [_intent("ZZZ", "sell", "close_long", 40), _intent("AAA", "buy", "open_long", 80)]
    rows = submit_in_phases(broker, intents, ["AAA", "ZZZ"], RiskConfig(), PaperConfig(cash_buffer_pct=0.0),
                            equity=20_000, monotonic=clock.monotonic, sleep=clock.sleep)
    assert [intent.symbol for intent in broker.submitted] == ["ZZZ", "AAA"]
    assert rows[0]["status"] == "filled" and rows[0]["submission_phase"] == 1
    assert rows[1]["submission_phase"] == 2
    assert broker.submitted[1].quantity == pytest.approx(60)  # trimmed from 80 to the $6,000 cash
    assert clock.t > 0  # it waited for the fill


def test_timeout_still_submits_buys_within_available_cash():
    broker, clock = PhasedBroker(fills_after=10**9, cash_after=2_000.0), Clock()
    intents = [_intent("ZZZ", "sell", "close_long", 40), _intent("AAA", "buy", "open_long", 50)]
    rows = submit_in_phases(broker, intents, [], RiskConfig(), PaperConfig(sell_fill_timeout_seconds=5,
                            cash_buffer_pct=0.0), equity=20_000, monotonic=clock.monotonic, sleep=clock.sleep)
    assert rows[0]["status"] == "new"
    assert broker.submitted[1].quantity == pytest.approx(20)
    assert clock.t >= 5


def test_brokers_without_status_or_zero_timeout_submit_in_one_pass():
    class Plain:
        def __init__(self):
            self.submitted = []

        def submit(self, intent):
            self.submitted.append(intent)
            return SimpleNamespace(status="accepted", id=intent.symbol)

    broker = Plain()
    intents = [_intent("ZZZ", "sell", "close_long", 40), _intent("AAA", "buy", "open_long", 80)]
    rows = submit_in_phases(broker, intents, [], RiskConfig(), PaperConfig(), equity=20_000)
    assert [row["symbol"] for row in rows] == ["ZZZ", "AAA"]
    assert broker.submitted[1].quantity == 80  # not trimmed
    zero = PhasedBroker()
    submit_in_phases(zero, intents, [], RiskConfig(), PaperConfig(sell_fill_timeout_seconds=0), equity=20_000)
    assert zero.polls == 0


def test_status_read_errors_do_not_abort_waiting():
    class Flaky(PhasedBroker):
        def order_status(self, order_id):
            raise RuntimeError("timeout")

    clock = Clock()
    statuses = wait_for_terminal(Flaky(), ["a"], 3, monotonic=clock.monotonic, sleep=clock.sleep)
    assert statuses == {"a": "unknown"}


def test_waiting_uses_one_open_orders_request_per_poll():
    class Batched(PhasedBroker):
        def __init__(self):
            super().__init__()
            self.open_calls, self.status_calls = 0, 0

        def open_order_ids(self):
            self.open_calls += 1
            return {"a", "b"} if self.open_calls < 3 else set()

        def order_status(self, order_id):
            self.status_calls += 1
            return "filled"

    broker, clock = Batched(), Clock()
    statuses = wait_for_terminal(broker, ["a", "b"], 30, monotonic=clock.monotonic, sleep=clock.sleep)
    assert statuses == {"a": "filled", "b": "filled"}
    assert broker.open_calls == 3 and broker.status_calls == 2
