"""Paper-planning regressions: rebalance anchoring, ownership ledger, sequencing."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from project_geld.config import PaperConfig, RiskConfig
from project_geld.data import SyntheticBarSource
from project_geld.models import OrderIntent
from project_geld.paper import (
    AccountSnapshot,
    align_rebalance_to_latest_bar,
    build_rebalance_orders,
    load_managed_ledger,
    managed_ledger_path,
    outside_universe_symbols,
    run_paper_cycle,
    sequence_orders,
    update_managed_ledger,
)
from project_geld.strategies.base import is_rebalance_bar
from project_geld.strategies.daily_v4 import DailyV4
from project_geld.strategies.equity_momentum_v2 import EquityMomentumV2
from project_geld.strategies.equity_momentum_v3 import EquityMomentumV3
from project_geld.strategies.intra_v15 import IntraV15


SLEEVE = {"max_symbols": 4, "exit_rank": 8}


@pytest.fixture(scope="module")
def daily_bars():
    symbols = ["SPY"] + [f"S{index:02d}" for index in range(24)]
    return SyntheticBarSource(seed=3).fetch(
        symbols,
        datetime(2021, 1, 1, tzinfo=timezone.utc),
        datetime(2023, 12, 29, tzinfo=timezone.utc),
    )


def _final_sleeve(strategy, bars):
    targets = strategy.generate_targets(bars)
    latest = targets[targets["timestamp"].eq(targets["timestamp"].max())]
    weights = latest.set_index("symbol")["target_weight"]
    return tuple(sorted(weights[(weights > 0) & (weights.index != "SPY")].round(8).items()))


def test_rebalance_bar_schedules():
    assert [i for i in range(10) if is_rebalance_bar(i, 10, 4, "start")] == [0, 4, 8]
    assert [i for i in range(10) if is_rebalance_bar(i, 10, 4, "end")] == [1, 5, 9]


@pytest.mark.parametrize("cls", [DailyV4, EquityMomentumV2, EquityMomentumV3])
def test_rebalance_anchor_is_validated(cls):
    with pytest.raises(ValueError, match="rebalance_anchor"):
        cls(rebalance_anchor="middle")


def test_end_anchor_makes_paper_targets_independent_of_window_start(daily_bars):
    dates = sorted(daily_bars["timestamp"].unique())
    windows = [daily_bars[daily_bars["timestamp"] >= dates[shift]] for shift in (0, 4, 9, 13)]

    anchored = DailyV4(active_parameters=SLEEVE, rebalance_anchor="end")
    assert len({_final_sleeve(anchored, window) for window in windows}) == 1

    # The start-anchored schedule (the backtest default) is phase dependent,
    # which is exactly why paper planning must not use it on a rolling window.
    legacy = DailyV4(active_parameters=SLEEVE)
    assert len({_final_sleeve(legacy, window) for window in windows}) > 1


def test_backtest_default_schedule_is_unchanged():
    assert DailyV4().rebalance_anchor == "start"
    assert EquityMomentumV3().rebalance_anchor == "start"
    assert EquityMomentumV2().rebalance_anchor == "start"


def test_align_rebalance_to_latest_bar_only_touches_anchored_strategies():
    aligned = align_rebalance_to_latest_bar(DailyV4(active_parameters=SLEEVE))
    assert aligned.rebalance_anchor == "end"
    assert aligned.active_parameters == SLEEVE
    intraday = IntraV15()
    assert align_rebalance_to_latest_bar(intraday) is intraday
    plain = SimpleNamespace(name="plain")
    assert align_rebalance_to_latest_bar(plain) is plain


class FakeBroker:
    def __init__(self, snapshot):
        self._snapshot = snapshot
        self.submitted = []

    def get_clock(self):
        return SimpleNamespace(is_open=True)

    def snapshot(self, universe):
        return self._snapshot

    def short_availability(self, symbols):
        return {}

    def submit(self, intent):
        self.submitted.append(intent)
        return SimpleNamespace(status="accepted", id=f"id-{len(self.submitted)}")


def test_paper_cycle_plans_with_latest_bar_anchor(daily_bars, tmp_path):
    universe = sorted(daily_bars["symbol"].unique())
    strategy = DailyV4(active_parameters=SLEEVE)
    snapshot = AccountSnapshot(
        equity=100_000, last_equity=100_000, positions={}, open_order_symbols=set(),
        cash=100_000, buying_power=100_000,
    )
    result = run_paper_cycle(
        daily_bars, strategy, FakeBroker(snapshot), RiskConfig(max_position_weight=0.8),
        PaperConfig(state_file=tmp_path / "state.json"), universe, snapshot=snapshot,
    )
    expected = DailyV4(active_parameters=SLEEVE, rebalance_anchor="end").generate_targets(daily_bars)
    expected = expected[expected["timestamp"].eq(expected["timestamp"].max())]
    planned = result.targets.set_index("symbol")["target_weight"].sort_index()
    pd.testing.assert_series_equal(
        planned, expected.set_index("symbol")["target_weight"].sort_index(), check_names=False
    )


def test_paper_cycle_liquidates_outside_universe_holding_without_selecting_it(daily_bars, tmp_path):
    universe = [symbol for symbol in sorted(daily_bars["symbol"].unique()) if symbol != "S05"]
    orphan_price = float(daily_bars[daily_bars["symbol"].eq("S05")]["close"].iloc[-1])
    snapshot = AccountSnapshot(
        equity=100_000, last_equity=100_000, positions={"S05": 12.0},
        open_order_symbols=set(), cash=90_000, buying_power=90_000,
    )
    result = run_paper_cycle(
        daily_bars, DailyV4(active_parameters=SLEEVE), FakeBroker(snapshot),
        RiskConfig(max_position_weight=0.8), PaperConfig(state_file=tmp_path / "s.json"),
        universe, snapshot=snapshot,
    )
    orders = result.orders.set_index("symbol")
    assert orders.loc["S05", "side"] == "sell"
    assert orders.loc["S05", "quantity"] == pytest.approx(12.0)
    assert orders.loc["S05", "reference_price"] == pytest.approx(orphan_price)
    assert "S05" not in set(result.targets["symbol"])


def test_managed_ledger_tracks_held_names_and_prunes_finished_ones(tmp_path):
    paper = PaperConfig(state_file=tmp_path / "rebalance_state.json", client_order_prefix="geldswg")
    path = managed_ledger_path(paper)
    assert path.name == "managed_symbols-geldswg.json"

    first = AccountSnapshot(
        equity=1, last_equity=1, positions={"AAA": 5.0, "SPY": 10.0}, open_order_symbols=set()
    )
    assert update_managed_ledger(path, first, traded_symbols={"BBB"}) == {"AAA", "BBB", "SPY"}

    # AAA left the universe but is still held: it must be reported for liquidation.
    assert outside_universe_symbols(paper, ["SPY", "BBB"]) == ["AAA"]

    # After AAA is sold and BBB has an open order, AAA is pruned and BBB kept.
    second = AccountSnapshot(
        equity=1, last_equity=1, positions={"SPY": 10.0}, open_order_symbols={"BBB"}
    )
    assert update_managed_ledger(path, second) == {"BBB", "SPY"}
    assert load_managed_ledger(path) == {"BBB", "SPY"}


def test_adopted_symbols_are_retained_until_liquidated(tmp_path):
    path = tmp_path / "ledger.json"
    snapshot = AccountSnapshot(equity=1, last_equity=1, positions={}, open_order_symbols=set())
    assert update_managed_ledger(path, snapshot, adopted_symbols={"old"}) == {"OLD"}


def _intent(symbol, side, reason):
    return OrderIntent(symbol, side, 1.0, 10.0, 10.0, 0.0, f"id-{symbol}", reason)


def test_sequence_orders_submits_risk_reducing_sells_first():
    orders = [
        _intent("AAA", "buy", "open_long"),
        _intent("BBB", "sell", "open_short"),
        _intent("CCC", "buy", "cover_short"),
        _intent("DDD", "sell", "close_long"),
        _intent("EEE", "sell", "close_long"),
    ]
    assert [order.symbol for order in sequence_orders(orders)] == ["DDD", "EEE", "CCC", "BBB", "AAA"]


def test_build_rebalance_orders_returns_sells_before_buys():
    targets = pd.DataFrame(
        {"symbol": ["AAA", "ZZZ"], "target_weight": [0.4, 0.0], "score": [1.0, 0.0]}
    )
    snapshot = AccountSnapshot(
        equity=10_000, last_equity=10_000, positions={"ZZZ": 40.0},
        open_order_symbols=set(), cash=6_000,
    )
    orders = build_rebalance_orders(
        targets, {"AAA": 100.0, "ZZZ": 100.0}, snapshot,
        RiskConfig(max_position_weight=0.5), "geld", "daily_v4", pd.Timestamp("2026-01-02", tz="UTC"),
    )
    assert [(order.symbol, order.side) for order in orders] == [("ZZZ", "sell"), ("AAA", "buy")]


def test_strategy_order_tokens_are_unique_across_the_registry():
    from project_geld.paper import strategy_order_token
    from project_geld.strategies.registry import STRATEGIES

    # Aliases share a class (and therefore a strategy.name); compare real names.
    names = sorted({cls().name for cls in STRATEGIES.values() if cls.__name__ != "CandidateStrategy"})
    tokens = {name: strategy_order_token(name) for name in names}
    assert len(set(tokens.values())) == len(tokens), tokens
    assert all(len(token) <= 8 for token in tokens.values())
    assert strategy_order_token("intra_v13") != strategy_order_token("intra_v15")


def test_client_order_ids_distinguish_intraday_versions():
    targets = pd.DataFrame({"symbol": ["SPY"], "target_weight": [0.05], "score": [1.0]})
    snapshot = AccountSnapshot(
        equity=100_000, last_equity=100_000, positions={}, open_order_symbols=set(), cash=100_000
    )
    as_of = pd.Timestamp("2026-11-13T15:30:00Z")
    ids = {
        name: build_rebalance_orders(
            targets, {"SPY": 500.0}, snapshot, RiskConfig(), "geld", name, as_of
        )[0].client_order_id
        for name in ["intra_v13", "intra_v15"]
    }
    assert ids["intra_v13"] != ids["intra_v15"]
    assert ids["intra_v15"] == "geld-202611131530-intrav15-SPY-b"


def test_buying_budget_funds_covers_before_new_longs():
    targets = pd.DataFrame(
        {"symbol": ["AAPL", "ZZZ"], "target_weight": [0.45, 0.0], "score": [1.0, 0.0]}
    )
    snapshot = AccountSnapshot(
        equity=20_000, last_equity=20_000, positions={"ZZZ": -50.0}, open_order_symbols=set(),
        cash=10_000, buying_power=10_000, shorting_enabled=True,
    )
    orders = build_rebalance_orders(
        targets, {"AAPL": 100.0, "ZZZ": 100.0}, snapshot,
        RiskConfig(max_position_weight=0.5, max_order_notional=50_000), "geld", "intra_v15",
        pd.Timestamp("2026-11-13T15:30:00Z"), allow_short=True,
    )
    by_symbol = {order.symbol: order for order in orders}
    assert [order.symbol for order in orders] == ["ZZZ", "AAPL"]
    assert by_symbol["ZZZ"].reason == "cover_short" and by_symbol["ZZZ"].quantity == 50
    buys = sum(order.notional for order in orders if order.side == "buy")
    assert buys <= 10_000 + 1e-6  # the new long is trimmed, not the cover


def test_ledger_keeps_unpriced_orphans_left_unmanaged(tmp_path):
    path = tmp_path / "ledger.json"
    update_managed_ledger(
        path, AccountSnapshot(equity=1, last_equity=1, positions={"OLD": 3.0}, open_order_symbols=set())
    )
    # Next cycle OLD has no bars, so it is excluded from the snapshot request and
    # shows up as unmanaged; it must stay in the ledger.
    unpriced = AccountSnapshot(
        equity=1, last_equity=1, positions={}, open_order_symbols=set(),
        unmanaged_symbols=frozenset({"OLD", "MANUAL"}),
    )
    assert update_managed_ledger(path, unpriced) == {"OLD"}
