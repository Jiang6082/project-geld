"""End-to-end command checks with fake Alpaca: intraday ledger, flatten, refresh hand-off, report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from project_geld.paper import AccountSnapshot, load_managed_ledger, update_managed_ledger

REPO = Path(__file__).resolve().parents[1]


def _intraday_config(tmp_path: Path, symbols: list[str]) -> Path:
    stamp = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=1)).normalize().isoformat()
    universe = tmp_path / "universe.csv"
    pd.DataFrame({"timestamp": stamp, "symbol": symbols}).to_csv(universe, index=False)
    text = (REPO / "configs/paper-intra-v15.toml").read_text(encoding="utf-8")
    text = text.replace('symbols_file = "../universes/pit-liquid-100-monthly-2019-2026.csv.gz"',
                        f"symbols_file = {json.dumps(universe.as_posix())}")
    text = text.replace('cache_dir = "data/cache/intraday-v13-paper-iex"',
                        f"cache_dir = {json.dumps((tmp_path / 'cache').as_posix())}")
    text = text.replace('state_file = "artifacts/paper-intra-v15/cycle_state.json"',
                        f"state_file = {json.dumps((tmp_path / 'cycle_state.json').as_posix())}")
    text = text.replace('state_file = "artifacts/paper-intra-v15/unused-daily-state.json"',
                        f"state_file = {json.dumps((tmp_path / 'state.json').as_posix())}")
    config = tmp_path / "v15.toml"
    config.write_text(text, encoding="utf-8")
    return config


def _minute_bars(symbols, day="2026-11-13", until="11:00"):
    minutes = pd.date_range(f"{day} 09:30", f"{day} {until}", freq="1min", tz="America/New_York",
                            inclusive="left").tz_convert("UTC")
    rng = np.random.default_rng(1)
    rows = []
    for symbol in symbols:
        price = 100.0 + 10 * rng.random()
        for stamp in minutes:
            nxt = price * (1 + rng.normal(0.0001, 0.0005))
            rows.append({"timestamp": stamp, "symbol": symbol, "open": price, "high": max(price, nxt),
                         "low": min(price, nxt), "close": nxt, "volume": 50_000.0})
            price = nxt
    return pd.DataFrame(rows)


class FakeBroker:
    instances: list["FakeBroker"] = []

    fill_orders = True

    def __init__(self, profile, positions):
        self.positions = positions
        self.submitted, self.cancel_calls = [], []
        FakeBroker.instances.append(self)

    def get_clock(self):
        return SimpleNamespace(is_open=True)

    def snapshot(self, universe):
        wanted = {symbol.upper() for symbol in universe}
        return AccountSnapshot(
            equity=100_000, last_equity=100_000,
            positions={s: q for s, q in self.positions.items() if s in wanted},
            open_order_symbols=set(), cash=50_000, buying_power=50_000,
            unmanaged_symbols=frozenset(s for s in self.positions if s not in wanted),
        )

    def short_availability(self, symbols):
        from project_geld.paper import ShortAvailability
        return {s: ShortAvailability(True, True, "easy") for s in symbols}

    def cancel_stale_orders(self, symbols, max_age_seconds, **_):
        self.cancel_calls.append((sorted(symbols), max_age_seconds))
        return pd.DataFrame()

    def submit(self, intent):
        self.submitted.append(intent)
        if self.fill_orders:
            signed = intent.quantity if intent.side == "buy" else -intent.quantity
            self.positions[intent.symbol] = self.positions.get(intent.symbol, 0.0) + signed
        return SimpleNamespace(status="accepted", id=f"o{len(self.submitted)}")

    def order_status(self, order_id):
        return "filled"

    def order_activity(self, after):
        return pd.DataFrame()


@pytest.fixture
def intraday_env(tmp_path, monkeypatch):
    from project_geld.cli import paper_commands

    universe = ["AAA", "BBB", "CCC", "DDD"]
    config = _intraday_config(tmp_path, universe)
    requested: list[list[str]] = []

    def fake_rolling(source, symbols, start, end, timeframe, cache):
        requested.append(list(symbols))
        return _minute_bars(symbols)

    positions = {"SPY": 10.0, "OLD": 5.0}
    FakeBroker.instances = []
    monkeypatch.setattr(paper_commands, "AlpacaBarSource", lambda *a, **k: object())
    monkeypatch.setattr(paper_commands, "fetch_rolling_bars", fake_rolling)
    monkeypatch.setattr(paper_commands, "AlpacaPaperBroker", lambda profile: FakeBroker(profile, positions))
    monkeypatch.setenv("PROJECT_GELD_INTRADAY_CONFIRM_PAPER", "YES")
    ledger = tmp_path / "managed_symbols-geldiv15.json"
    update_managed_ledger(ledger, AccountSnapshot(1, 1, {"OLD": 5.0, "SPY": 10.0}, set()))
    return SimpleNamespace(config=config, requested=requested, ledger=ledger, tmp=tmp_path)


def _args(env, **extra):
    return argparse.Namespace(config=str(env.config), output=str(env.tmp / "out"), submit=True,
                              flatten_all=False, **extra)


def test_intraday_cycle_prices_and_sells_names_that_left_the_universe(intraday_env):
    from project_geld.cli.paper_commands import command_intraday_paper

    command_intraday_paper(_args(intraday_env))
    assert "OLD" in intraday_env.requested[0]
    broker = FakeBroker.instances[-1]
    orders = {order.symbol: order for order in broker.submitted}
    assert orders["OLD"].side == "sell" and orders["OLD"].quantity == pytest.approx(5.0)
    targets = pd.read_csv(intraday_env.tmp / "out" / "latest_targets.csv")
    assert "OLD" not in set(targets["symbol"])  # never offered to the strategy
    assert "SPY" in load_managed_ledger(intraday_env.ledger)


def test_flatten_all_exits_every_position_with_distinct_order_ids(intraday_env):
    from project_geld.cli.paper_commands import command_intraday_paper

    FakeBroker.fill_orders = False
    command_intraday_paper(_args(intraday_env))  # marks this bar as processed
    FakeBroker.fill_orders = True
    command_intraday_paper(argparse.Namespace(**{**vars(_args(intraday_env)), "flatten_all": True}))
    broker = FakeBroker.instances[-1]
    assert {order.symbol for order in broker.submitted} >= {"SPY", "OLD"}
    assert all(order.side == "sell" for order in broker.submitted)
    assert all(order.client_order_id.startswith("fx") for order in broker.submitted)
    assert broker.cancel_calls and broker.cancel_calls[-1][1] == 1
    assert not any(broker.positions.values())  # verified flat before returning


def test_flatten_that_leaves_a_position_fails_so_the_runner_retries(intraday_env):
    from project_geld.cli.paper_commands import command_intraday_paper

    FakeBroker.fill_orders = False
    try:
        with pytest.raises(RuntimeError, match="Flatten incomplete"):
            command_intraday_paper(argparse.Namespace(
                **{**vars(_args(intraday_env)), "flatten_all": True, "flatten_wait_seconds": 0}
            ))
    finally:
        FakeBroker.fill_orders = True


def test_daily_refresh_hands_held_names_to_the_ledger(tmp_path, monkeypatch):
    from project_geld import paper
    from project_geld import universe_refresh as refresh
    from project_geld.broad_universe import BroadUniverseRules

    symbols = ["N" + chr(65 + i // 26) + chr(65 + i % 26) for i in range(40)]
    rules = BroadUniverseRules(top_n=10, minimum_price=5.0, minimum_history_sessions=20,
                               dollar_volume_window=10, minimum_dollar_volume=1_000_000.0)
    monkeypatch.setitem(refresh.PROFILES, "daily_v4",
                        refresh.RefreshProfile(rules, history_days=60, ownership="ledger"))
    dates = pd.bdate_range(end="2026-11-13", periods=40, tz="America/New_York").tz_convert("UTC")

    class Source:
        def __init__(self, *args):
            pass

        def fetch(self, batch, start, end):
            return pd.concat([pd.DataFrame({"timestamp": dates, "symbol": s, "open": 50., "close": 50.,
                                            "high": 51., "low": 49., "volume": 1e6 * (1 + i)})
                              for i, s in enumerate(batch)], ignore_index=True)

    monkeypatch.setattr(refresh, "AlpacaBarSource", Source)
    monkeypatch.setattr(refresh, "active_candidates", lambda _: pd.DataFrame({"symbol": symbols}))
    held = {"NAA": 3.0, "SPY": 20.0}
    monkeypatch.setattr(paper, "AlpacaPaperBroker", lambda _: FakeBroker("", held))
    source = tmp_path / "daily.toml"
    source.write_text(f'''[universe]
symbols = []
symbols_file = "old.csv"
benchmark = "SPY"
[strategy]
name = "daily_v4"
[account]
credential_profile = "SWING"
[paper]
client_order_prefix = "geldswg"
state_file = {json.dumps((tmp_path / "rebalance_state.json").as_posix())}
''')
    pd.DataFrame({"timestamp": "2026-07-15T04:00:00Z", "symbol": ["NAA", "NAB"]}).to_csv(tmp_path / "old.csv", index=False)
    generated = refresh.refresh(source, tmp_path / "out", now=pd.Timestamp("2026-11-16T14:00:00Z"))
    selected = pd.read_csv(tmp_path / "out" / "universe.csv")["symbol"].tolist()
    assert len(selected) == 10 and "NAA" not in selected  # lowest volume drops out
    ledger = load_managed_ledger(tmp_path / "managed_symbols-geldswg.json")
    assert {"NAA", "SPY"} <= ledger  # held name kept so the planner sells it
    assert generated.exists()


def test_performance_report_summarizes_logs_and_flags_a_stale_universe(tmp_path):
    from project_geld.performance_report import (
        AccountSpec,
        benchmark_return,
        build_report,
        render_markdown,
    )

    output = tmp_path / "paper-daily-v4"
    output.mkdir()
    sessions = pd.bdate_range("2026-08-03", periods=30)
    equity = 100_000 * np.cumprod(1 + np.r_[0.0, np.full(29, 0.001)])
    equity[15] *= 0.97
    pd.DataFrame({
        "observed_at": [f"{d.date()}T13:35:00+00:00" for d in sessions],
        "session_date": [str(d.date()) for d in sessions],
        "equity": equity, "last_equity": equity, "baseline_equity": 100_000.0,
    }).to_csv(output / "performance.csv", index=False)
    (output / "runner-20260915.log").write_text("x Universe snapshot is 52 days old; refresh it before paper planning.\n")
    universe = tmp_path / "universe.csv"
    pd.DataFrame({"timestamp": "2026-07-15T04:00:00Z", "symbol": ["AAA"]}).to_csv(universe, index=False)
    config = tmp_path / "daily.toml"
    config.write_text(f'[universe]\nsymbols_file = {json.dumps(universe.as_posix())}\n[paper]\n'
                      f'state_file = {json.dumps((output / "rebalance_state.json").as_posix())}\n')
    closes = pd.Series([400.0, 404.0, 410.0], index=["2026-07-31", "2026-08-03", "2026-09-11"])
    spec = AccountSpec("daily_v4", "Daily V4", config, output, intraday=False)
    report = build_report([spec], benchmark_closes=lambda first, last: closes,
                          now=pd.Timestamp("2026-11-14T12:00:00Z"))
    entry = report["accounts"]["daily_v4"]
    assert entry["paper"]["sessions_logged"] == 30
    assert entry["paper"]["max_drawdown"] < -0.02
    assert entry["paper"]["benchmark_return"] == pytest.approx(410 / 400 - 1)
    assert entry["health"]["planning_blocked_by_stale_universe"]
    assert entry["health"]["logs"]["counts"]["universe_stale"] == 1
    assert entry["paper"]["days_since_last_observation"] > 60
    markdown = render_markdown(report)
    assert "BLOCKED (stale)" in markdown and "Daily V4" in markdown
    assert benchmark_return(pd.Series(dtype=float), "2026-01-01", "2026-02-01") != benchmark_return(closes, "2026-08-03", "2026-09-11")


def test_daily_backtest_replay_runs_on_injected_data(tmp_path):
    from project_geld.data import SyntheticBarSource
    from project_geld.performance_report import AccountSpec, backtest_recent

    symbols = [f"S{i:02d}" for i in range(12)]
    universe = tmp_path / "universe.csv"
    pd.DataFrame({"timestamp": "2026-11-13T05:00:00Z", "symbol": symbols}).to_csv(universe, index=False)
    text = (REPO / "configs/paper-daily-v4.toml").read_text(encoding="utf-8")
    text = text.replace('symbols_file = "../artifacts/paper-v4-shadow/universe.csv"',
                        f"symbols_file = {json.dumps(universe.as_posix())}")
    config = tmp_path / "daily.toml"
    config.write_text(text, encoding="utf-8")
    spec = AccountSpec("daily_v4", "Daily V4", config, tmp_path / "out", intraday=False)

    class Synthetic:
        def fetch(self, symbols, start, end, timeframe="1Day"):
            return SyntheticBarSource(seed=4).fetch(symbols, start, end)

    replay = backtest_recent(spec, lambda cfg: Synthetic(), now=pd.Timestamp("2026-11-13T21:00:00Z"))
    assert set(replay["windows"]) >= {"last_3_months", "year_to_date", "last_12_months"}
    row = replay["windows"]["last_12_months"]
    assert np.isfinite(row["total_return"]) and np.isfinite(row["benchmark_return"])
    assert "survivorship" in replay["caveats"]
