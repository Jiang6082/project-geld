import pandas as pd
import pytest

from dataclasses import replace

from project_geld.config import PaperConfig, RiskConfig
from project_geld.paper import (
    AccountSnapshot,
    append_performance_snapshot,
    build_rebalance_orders,
    mark_paper_rebalance,
    paper_rebalance_due,
)


def targets():
    return pd.DataFrame(
        {
            "timestamp": pd.Timestamp("2025-01-02", tz="UTC"),
            "symbol": ["AAPL", "MSFT", "SPY"],
            "target_weight": [0.4, 0.3, 0.2],
            "score": [3.0, 2.0, 1.0],
        }
    )


def test_paper_planner_caps_orders_and_skips_open_symbols():
    snapshot = AccountSnapshot(
        equity=100_000,
        last_equity=100_000,
        positions={},
        open_order_symbols={"MSFT"},
        cash=100_000,
    )
    risk = RiskConfig(max_position_weight=0.35, max_order_notional=10_000)
    orders = build_rebalance_orders(
        targets(),
        {"AAPL": 200, "MSFT": 400, "SPY": 500},
        snapshot,
        risk,
        "geld",
        "momentum",
        pd.Timestamp("2025-01-02", tz="UTC"),
    )
    assert {order.symbol for order in orders} == {"AAPL", "SPY"}
    assert all(order.notional <= 10_000 for order in orders)
    assert all(order.client_order_id.startswith("geld-20250102") for order in orders)


def test_symbol_specific_position_and_order_limits_support_a_large_core():
    snapshot = AccountSnapshot(
        equity=100_000,
        last_equity=100_000,
        positions={},
        open_order_symbols=set(),
        cash=100_000,
    )
    risk = RiskConfig(
        max_position_weight=0.02,
        max_order_notional=1_000,
        max_order_pct_equity=0.02,
        symbol_position_weight_limits={"SPY": 0.75},
        symbol_order_notional_limits={"SPY": 100_000},
        symbol_order_pct_equity_limits={"SPY": 0.75},
    )
    orders = build_rebalance_orders(
        targets(),
        {"AAPL": 200, "MSFT": 400, "SPY": 500},
        snapshot,
        risk,
        "geld",
        "momentum_v4",
        pd.Timestamp("2025-01-02", tz="UTC"),
    )
    by_symbol = {order.symbol: order for order in orders}
    assert by_symbol["AAPL"].notional <= 1_000.01
    assert by_symbol["AAPL"].target_weight == pytest.approx(0.02)
    assert by_symbol["SPY"].notional == pytest.approx(20_000, abs=1.0)
    assert by_symbol["SPY"].target_weight == pytest.approx(0.2)


def test_cash_buffer_scales_target_gross_and_buying_budget():
    latest = targets().copy()
    latest["target_weight"] = [0.40, 0.0, 0.60]
    orders = build_rebalance_orders(
        latest,
        {"AAPL": 200, "MSFT": 400, "SPY": 500},
        AccountSnapshot(100_000, 100_000, {}, set(), cash=100_000),
        RiskConfig(max_position_weight=1.0, max_order_notional=100_000),
        "geld",
        "momentum_v4",
        pd.Timestamp("2025-01-02", tz="UTC"),
        cash_buffer_pct=0.01,
    )
    assert sum(order.notional for order in orders) <= 99_000.01
    assert sum(order.target_weight for order in orders) == pytest.approx(0.99)


def test_daily_loss_guard_blocks_paper_plan():
    snapshot = AccountSnapshot(
        equity=97_000,
        last_equity=100_000,
        positions={},
        open_order_symbols=set(),
        cash=100_000,
    )
    with pytest.raises(RuntimeError, match="Daily-loss guard"):
        build_rebalance_orders(
            targets(),
            {"AAPL": 200, "MSFT": 400, "SPY": 500},
            snapshot,
            RiskConfig(max_daily_loss_pct=0.02),
            "geld",
            "momentum",
            pd.Timestamp("2025-01-02", tz="UTC"),
        )


def test_unmanaged_positions_reduce_available_gross_exposure():
    snapshot = AccountSnapshot(
        equity=100_000,
        last_equity=100_000,
        positions={},
        open_order_symbols=set(),
        cash=50_000,
        unmanaged_notional=50_000,
    )
    orders = build_rebalance_orders(
        targets(),
        {"AAPL": 200, "MSFT": 400, "SPY": 500},
        snapshot,
        RiskConfig(max_gross_exposure=0.8, max_order_notional=100_000),
        "geld",
        "momentum",
        pd.Timestamp("2025-01-02", tz="UTC"),
    )
    assert sum(order.notional for order in orders if order.side == "buy") <= 30_000.01


def test_equity_relative_no_trade_band_ignores_small_drift():
    latest = targets().copy()
    latest["target_weight"] = [0.101, 0.0, 0.0]
    orders = build_rebalance_orders(
        latest,
        {"AAPL": 100, "MSFT": 100, "SPY": 100},
        AccountSnapshot(
            equity=100_000,
            last_equity=100_000,
            positions={"AAPL": 100.0},
            open_order_symbols=set(),
            cash=90_000,
        ),
        RiskConfig(
            max_position_weight=1.0,
            max_order_notional=100_000,
            min_trade_notional=10,
            min_trade_pct_equity=0.005,
        ),
        "geld",
        "intraday_momentum",
        pd.Timestamp("2025-01-02", tz="UTC"),
    )
    assert not orders


def test_marketable_limit_plan_exposes_price_ceiling():
    orders = build_rebalance_orders(
        targets(),
        {"AAPL": 200, "MSFT": 400, "SPY": 500},
        AccountSnapshot(100_000, 100_000, {}, set(), cash=100_000),
        RiskConfig(max_position_weight=1.0, max_order_notional=100_000),
        "geld",
        "intraday_momentum",
        pd.Timestamp("2025-01-02", tz="UTC"),
        execution_style="marketable_limit",
        limit_offset_bps=2.0,
    )
    by_symbol = {order.symbol: order for order in orders}
    assert by_symbol["AAPL"].limit_price == 200.04
    assert by_symbol["AAPL"].notional == pytest.approx(
        by_symbol["AAPL"].quantity * 200.04
    )


def test_performance_log_replaces_same_day_and_tracks_baseline(tmp_path):
    path = tmp_path / "performance.csv"
    first = AccountSnapshot(100_000, 99_000, {}, set(), cash=100_000)
    second = AccountSnapshot(101_000, 100_000, {}, set(), cash=101_000)
    append_performance_snapshot(
        first, path, pd.Timestamp("2026-01-02 15:00", tz="UTC")
    )
    append_performance_snapshot(
        second, path, pd.Timestamp("2026-01-02 20:00", tz="UTC")
    )
    history = pd.read_csv(path)
    assert len(history) == 1
    assert history.iloc[0]["cumulative_return"] == pytest.approx(0.01)


def test_paper_rebalance_cadence_uses_persistent_state(tmp_path):
    dates = pd.date_range("2026-01-02", periods=12, freq="B", tz="UTC")
    bars = pd.DataFrame({"timestamp": dates, "symbol": "AAPL"})
    paper = replace(
        PaperConfig(),
        rebalance_every_sessions=10,
        state_file=tmp_path / "state.json",
    )
    due, _, _ = paper_rebalance_due(bars, paper, "momentum_v2")
    assert due
    mark_paper_rebalance(paper, "momentum_v2", dates[1])
    due, elapsed, latest = paper_rebalance_due(bars, paper, "momentum_v2")
    assert due
    assert elapsed == 10
    mark_paper_rebalance(paper, "momentum_v2", latest)
    due, elapsed, _ = paper_rebalance_due(bars, paper, "momentum_v2")
    assert not due
    assert elapsed == 0
