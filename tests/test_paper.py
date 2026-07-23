import pandas as pd
import pytest

from dataclasses import replace
from types import SimpleNamespace

from project_geld.config import PaperConfig, RiskConfig
from project_geld.paper import (
    AlpacaPaperBroker,
    AccountSnapshot,
    ShortAvailability,
    append_performance_snapshot,
    build_rebalance_orders,
    implementation_shortfall,
    mark_paper_rebalance,
    paper_rebalance_due,
)
from project_geld.models import OrderIntent


def test_implementation_shortfall_scores_fills_and_flags_missed_orders():
    planned = pd.DataFrame(
        [
            {
                "symbol": "SPY",
                "side": "buy",
                "quantity": 100.0,
                "reference_price": 500.0,
                "limit_price": 500.1,
                "client_order_id": "a",
            },
            {
                "symbol": "AAPL",
                "side": "sell",
                "quantity": 50.0,
                "reference_price": 200.0,
                "limit_price": 199.9,
                "client_order_id": "b",
            },
            {
                "symbol": "MSFT",
                "side": "buy",
                "quantity": 10.0,
                "reference_price": 400.0,
                "limit_price": 400.1,
                "client_order_id": "c",
            },
        ]
    )
    activity = pd.DataFrame(
        [
            {"client_order_id": "a", "filled_quantity": 100.0, "filled_average_price": 500.5},
            {"client_order_id": "b", "filled_quantity": 50.0, "filled_average_price": 199.8},
            {"client_order_id": "c", "filled_quantity": 0.0, "filled_average_price": None},
        ]
    )
    result = implementation_shortfall(planned, activity).set_index("client_order_id")
    # Buy filled above reference is a positive (worse) shortfall.
    assert result.loc["a", "shortfall_bps"] == pytest.approx(10.0)
    # Sell filled below reference is also a positive (worse) shortfall.
    assert result.loc["b", "shortfall_bps"] == pytest.approx(10.0)
    assert bool(result.loc["c", "missed"]) is True
    assert result.loc["c", "shortfall_bps"] != result.loc["c", "shortfall_bps"]  # NaN
    assert result.loc["a", "fill_rate"] == pytest.approx(1.0)


def test_implementation_shortfall_handles_no_orders():
    empty = implementation_shortfall(pd.DataFrame(), pd.DataFrame())
    assert list(empty.columns)
    assert empty.empty


def test_shortfall_kill_switch_triggers_only_when_base_sleeve_breaches():
    from project_geld.paper import shortfall_kill_switch_active, trailing_shortfall_bps

    history = pd.DataFrame(
        [
            {"symbol": "SPY", "shortfall_bps": 3.0, "missed": False},
            {"symbol": "SPY", "shortfall_bps": 4.0, "missed": False},
            {"symbol": "SPY", "shortfall_bps": 50.0, "missed": True},   # unfilled ignored
            {"symbol": "AAPL", "shortfall_bps": -1.0, "missed": False},  # other symbol
        ]
    )
    assert trailing_shortfall_bps(history, "SPY") == pytest.approx(3.5)
    assert shortfall_kill_switch_active(history, "SPY", threshold_bps=2.0) is True
    # Below the gate and empty history both stay inactive.
    calm = pd.DataFrame(
        [{"symbol": "SPY", "shortfall_bps": 1.0, "missed": False}]
    )
    assert shortfall_kill_switch_active(calm, "SPY", threshold_bps=2.0) is False
    assert shortfall_kill_switch_active(pd.DataFrame(), "SPY") is False


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


def test_intraday_client_order_ids_are_unique_per_decision_bar():
    snapshot = AccountSnapshot(100_000, 100_000, {}, set(), cash=100_000)
    risk = RiskConfig(max_position_weight=1.0, max_order_notional=100_000)
    first = build_rebalance_orders(
        targets(),
        {"AAPL": 200, "MSFT": 400, "SPY": 500},
        snapshot,
        risk,
        "geld",
        "intra_v15",
        pd.Timestamp("2025-01-02 14:30", tz="UTC"),
    )
    second = build_rebalance_orders(
        targets(),
        {"AAPL": 200, "MSFT": 400, "SPY": 500},
        snapshot,
        risk,
        "geld",
        "intra_v15",
        pd.Timestamp("2025-01-02 14:45", tz="UTC"),
    )
    assert {order.client_order_id for order in first}.isdisjoint(
        {order.client_order_id for order in second}
    )
    assert all(len(order.client_order_id) <= 48 for order in [*first, *second])


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
        "daily_v4",
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
        "daily_v4",
        pd.Timestamp("2025-01-02", tz="UTC"),
        cash_buffer_pct=0.01,
    )
    assert sum(order.notional for order in orders) <= 99_000.01
    assert sum(order.target_weight for order in orders) == pytest.approx(0.99)


def test_daily_loss_guard_flattens_positions_and_opens_nothing():
    snapshot = AccountSnapshot(
        equity=97_000,
        last_equity=100_000,
        positions={"AAPL": 10.0},
        open_order_symbols=set(),
        cash=100_000,
    )
    orders = build_rebalance_orders(
        targets(),
        {"AAPL": 200, "MSFT": 400, "SPY": 500},
        snapshot,
        RiskConfig(max_daily_loss_pct=0.02),
        "geld",
        "momentum",
        pd.Timestamp("2025-01-02", tz="UTC"),
    )
    assert len(orders) == 1
    assert orders[0].side == "sell"
    assert orders[0].quantity == 10
    assert orders[0].reason == "daily_loss_exit"


def test_short_targets_are_rejected_by_paper_planner():
    latest = targets().copy()
    latest.loc[latest["symbol"].eq("AAPL"), "target_weight"] = -0.1
    with pytest.raises(RuntimeError, match="allow_short"):
        build_rebalance_orders(
            latest,
            {"AAPL": 200, "MSFT": 400, "SPY": 500},
            AccountSnapshot(100_000, 100_000, {}, set(), cash=100_000),
            RiskConfig(),
            "geld",
            "intra_v7",
            pd.Timestamp("2025-01-02", tz="UTC"),
        )


def test_short_target_uses_whole_shares_when_account_and_asset_allow_it():
    latest = targets().iloc[[0]].copy()
    latest["target_weight"] = -0.101
    orders = build_rebalance_orders(
        latest,
        {"AAPL": 200},
        AccountSnapshot(
            100_000,
            100_000,
            {},
            set(),
            cash=100_000,
            buying_power=200_000,
            shorting_enabled=True,
        ),
        RiskConfig(max_position_weight=0.2, max_order_notional=100_000),
        "geld",
        "intra_v13",
        pd.Timestamp("2025-01-02", tz="UTC"),
        allow_short=True,
        short_availability={
            "AAPL": ShortAvailability(True, True, "easy_to_borrow")
        },
    )
    assert len(orders) == 1
    assert orders[0].side == "sell"
    assert orders[0].quantity == 50
    assert orders[0].target_weight == pytest.approx(-0.101)
    assert orders[0].reason == "open_short"


def test_unavailable_short_is_not_opened():
    latest = targets().iloc[[0]].copy()
    latest["target_weight"] = -0.1
    orders = build_rebalance_orders(
        latest,
        {"AAPL": 200},
        AccountSnapshot(
            100_000,
            100_000,
            {},
            set(),
            cash=100_000,
            buying_power=200_000,
            shorting_enabled=True,
        ),
        RiskConfig(max_position_weight=0.2, max_order_notional=100_000),
        "geld",
        "intra_v13",
        pd.Timestamp("2025-01-02", tz="UTC"),
        allow_short=True,
        short_availability={
            "AAPL": ShortAvailability(True, False, "hard_to_borrow")
        },
    )
    assert orders == []


def test_account_without_shorting_permission_opens_no_short():
    latest = targets().iloc[[0]].copy()
    latest["target_weight"] = -0.1
    orders = build_rebalance_orders(
        latest,
        {"AAPL": 200},
        AccountSnapshot(
            100_000,
            100_000,
            {},
            set(),
            cash=100_000,
            buying_power=100_000,
            shorting_enabled=False,
        ),
        RiskConfig(max_position_weight=0.2, max_order_notional=100_000),
        "geld",
        "intra_v13",
        pd.Timestamp("2025-01-02", tz="UTC"),
        allow_short=True,
        short_availability={
            "AAPL": ShortAvailability(True, True, "easy_to_borrow")
        },
    )
    assert orders == []


def test_cover_is_never_blocked_by_borrow_or_order_notional_limit():
    latest = targets().iloc[[0]].copy()
    latest["target_weight"] = 0.0
    orders = build_rebalance_orders(
        latest,
        {"AAPL": 200},
        AccountSnapshot(
            100_000,
            100_000,
            {"AAPL": -75.0},
            set(),
            cash=0,
            buying_power=0,
            shorting_enabled=True,
        ),
        RiskConfig(max_order_notional=1_000, min_trade_notional=100_000),
        "geld",
        "intra_v13",
        pd.Timestamp("2025-01-02", tz="UTC"),
        allow_short=True,
        short_availability={
            "AAPL": ShortAvailability(False, False, "unavailable")
        },
    )
    assert len(orders) == 1
    assert orders[0].side == "buy"
    assert orders[0].quantity == 75
    assert orders[0].reason == "cover_short"


def test_sign_reversal_flattens_before_opening_opposite_side():
    latest = targets().iloc[[0]].copy()
    latest["target_weight"] = -0.1
    orders = build_rebalance_orders(
        latest,
        {"AAPL": 200},
        AccountSnapshot(
            100_000,
            100_000,
            {"AAPL": 20.0},
            set(),
            cash=96_000,
            buying_power=190_000,
            shorting_enabled=True,
        ),
        RiskConfig(max_position_weight=0.2, max_order_notional=100_000),
        "geld",
        "intra_v13",
        pd.Timestamp("2025-01-02", tz="UTC"),
        allow_short=True,
        short_availability={
            "AAPL": ShortAvailability(True, True, "easy_to_borrow")
        },
    )
    assert len(orders) == 1
    assert orders[0].side == "sell"
    assert orders[0].quantity == 20
    assert orders[0].reason == "flatten_before_reverse"


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
        "intra_v1",
        pd.Timestamp("2025-01-02", tz="UTC"),
    )
    assert not orders


def test_small_risk_reduction_is_skipped_but_full_exit_is_not():
    snapshot = AccountSnapshot(
        equity=100_000,
        last_equity=100_000,
        positions={"AAPL": 100.0},
        open_order_symbols=set(),
        cash=90_000,
    )
    risk = RiskConfig(
        max_position_weight=1.0,
        max_order_notional=100_000,
        min_trade_notional=10,
        min_trade_pct_equity=0.005,
    )
    drift = targets().copy()
    drift["target_weight"] = [0.099, 0.0, 0.0]
    assert not build_rebalance_orders(
        drift,
        {"AAPL": 100, "MSFT": 100, "SPY": 100},
        snapshot,
        risk,
        "geld",
        "intra_v15",
        pd.Timestamp("2025-01-02 14:30", tz="UTC"),
    )
    flatten = drift.copy()
    flatten["target_weight"] = 0.0
    orders = build_rebalance_orders(
        flatten,
        {"AAPL": 100, "MSFT": 100, "SPY": 100},
        snapshot,
        risk,
        "geld",
        "intra_v15",
        pd.Timestamp("2025-01-02 19:30", tz="UTC"),
    )
    assert len(orders) == 1
    assert orders[0].quantity == 100


def test_marketable_limit_plan_exposes_price_ceiling():
    orders = build_rebalance_orders(
        targets(),
        {"AAPL": 200, "MSFT": 400, "SPY": 500},
        AccountSnapshot(100_000, 100_000, {}, set(), cash=100_000),
        RiskConfig(max_position_weight=1.0, max_order_notional=100_000),
        "geld",
        "intra_v1",
        pd.Timestamp("2025-01-02", tz="UTC"),
        execution_style="marketable_limit",
        limit_offset_bps=2.0,
    )
    by_symbol = {order.symbol: order for order in orders}
    assert by_symbol["AAPL"].limit_price == 200.04
    assert by_symbol["AAPL"].notional == pytest.approx(
        by_symbol["AAPL"].quantity * 200.04
    )


def test_configured_market_exit_does_not_leave_a_flattening_limit_order():
    latest = targets().iloc[[0]].copy()
    latest["target_weight"] = 0.0
    orders = build_rebalance_orders(
        latest,
        {"AAPL": 200},
        AccountSnapshot(
            100_000,
            100_000,
            {"AAPL": 10.0},
            set(),
            cash=98_000,
        ),
        RiskConfig(max_position_weight=1.0, max_order_notional=100_000),
        "geld",
        "intra_v15",
        pd.Timestamp("2025-01-02 20:30", tz="UTC"),
        execution_style="marketable_limit",
        limit_offset_bps=2.0,
        market_exit_orders=True,
    )
    assert len(orders) == 1
    assert orders[0].reason == "close_long"
    assert orders[0].limit_price is None


@pytest.mark.filterwarnings("ignore:websockets.legacy is deprecated:DeprecationWarning")
def test_alpaca_adapter_cancels_only_stale_managed_orders():
    now = pd.Timestamp("2026-07-22 18:00", tz="UTC")
    orders = [
        SimpleNamespace(
            id="stale-managed",
            symbol="SPY",
            side="buy",
            qty="5",
            filled_qty="0",
            submitted_at=pd.Timestamp("2026-07-22 17:45", tz="UTC"),
            client_order_id="geld-stale",
        ),
        SimpleNamespace(
            id="fresh-managed",
            symbol="AAPL",
            side="buy",
            qty="2",
            filled_qty="0",
            submitted_at=pd.Timestamp("2026-07-22 17:59:30", tz="UTC"),
            client_order_id="geld-fresh",
        ),
        SimpleNamespace(
            id="stale-unmanaged",
            symbol="QQQ",
            side="buy",
            qty="1",
            filled_qty="0",
            submitted_at=pd.Timestamp("2026-07-22 17:45", tz="UTC"),
            client_order_id="other-stale",
        ),
    ]

    class FakeClient:
        def get_orders(self, filter):
            return list(orders)

        def cancel_order_by_id(self, order_id):
            orders[:] = [order for order in orders if order.id != str(order_id)]

    broker = object.__new__(AlpacaPaperBroker)
    broker.client = FakeClient()
    cancelled = broker.cancel_stale_orders(
        ["SPY", "AAPL"], 300, observed_at=now, wait_timeout_seconds=0
    )
    assert cancelled["order_id"].tolist() == ["stale-managed"]
    assert {order.id for order in orders} == {"fresh-managed", "stale-unmanaged"}


@pytest.mark.filterwarnings("ignore:websockets.legacy is deprecated:DeprecationWarning")
def test_alpaca_adapter_constructs_limit_order_without_real_submission():
    class FakeClient:
        request = None

        def submit_order(self, order_data):
            self.request = order_data
            return order_data

    broker = object.__new__(AlpacaPaperBroker)
    broker.client = FakeClient()
    broker.submit(
        OrderIntent(
            symbol="AAPL",
            side="buy",
            quantity=1.5,
            reference_price=100.0,
            notional=150.03,
            target_weight=0.1,
            client_order_id="geld-test-limit",
            limit_price=100.02,
        )
    )
    assert float(broker.client.request.limit_price) == pytest.approx(100.02)
    assert float(broker.client.request.qty) == pytest.approx(1.5)


@pytest.mark.filterwarnings("ignore:websockets.legacy is deprecated:DeprecationWarning")
def test_alpaca_adapter_treats_duplicate_client_id_as_idempotent_success():
    expected = object()

    class FakeClient:
        def submit_order(self, order_data):
            raise RuntimeError("client_order_id must be unique")

        def get_order_by_client_id(self, client_id):
            assert client_id == "geld-existing"
            return expected

    broker = object.__new__(AlpacaPaperBroker)
    broker.client = FakeClient()
    response = broker.submit(
        OrderIntent(
            symbol="SPY",
            side="buy",
            quantity=1,
            reference_price=100.0,
            notional=100.0,
            target_weight=0.01,
            client_order_id="geld-existing",
        )
    )
    assert response is expected


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
