from types import SimpleNamespace

import pandas as pd
import pytest

from project_geld.close_check import (
    bars_available_at_close,
    build_position_reconciliation,
)
from project_geld.paper import AccountSnapshot, AlpacaPaperBroker


def daily_bars() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2026-07-20 04:00:00+00:00", "2026-07-21 04:00:00+00:00"]
            ),
            "symbol": ["SPY", "SPY"],
            "open": [740.0, 745.0],
            "high": [750.0, 752.0],
            "low": [738.0, 743.0],
            "close": [745.0, 750.0],
            "volume": [1_000_000, 1_100_000],
        }
    )


def test_close_bars_exclude_partial_session_and_include_final_session():
    observed = pd.Timestamp("2026-07-21 20:15:00+00:00")
    preview = bars_available_at_close(daily_bars(), observed, market_is_open=True)
    final = bars_available_at_close(daily_bars(), observed, market_is_open=False)
    assert preview["timestamp"].max() == pd.Timestamp("2026-07-20 04:00:00+00:00")
    assert final["timestamp"].max() == pd.Timestamp("2026-07-21 04:00:00+00:00")


def test_position_reconciliation_identifies_drift_and_unexpected_positions():
    targets = pd.DataFrame(
        {
            "timestamp": pd.Timestamp("2026-07-21", tz="UTC"),
            "symbol": ["SPY", "AAPL"],
            "target_weight": [0.40, 0.10],
            "score": [0.0, 1.0],
        }
    )
    result = build_position_reconciliation(
        targets,
        {"SPY": 500.0, "AAPL": 200.0, "OLD": 50.0},
        AccountSnapshot(
            equity=100_000,
            last_equity=99_000,
            positions={"SPY": 75.0, "OLD": 10.0},
            open_order_symbols=set(),
            cash=62_000,
        ),
    ).set_index("symbol")
    assert result.at["SPY", "current_weight"] == 0.375
    assert result.at["SPY", "weight_drift"] == pytest.approx(0.025)
    assert bool(result.at["OLD", "unexpected_position"])
    assert result.at["AAPL", "drift_notional"] == 10_000


@pytest.mark.filterwarnings("ignore:websockets.legacy is deprecated:DeprecationWarning")
def test_alpaca_order_activity_normalizes_order_fields():
    order = SimpleNamespace(
        submitted_at=pd.Timestamp("2026-07-21 14:31", tz="UTC"),
        filled_at=pd.Timestamp("2026-07-21 14:31", tz="UTC"),
        symbol="SPY",
        side=SimpleNamespace(value="buy"),
        qty="2.5",
        filled_qty="2.5",
        status=SimpleNamespace(value="filled"),
        filled_avg_price="750.25",
        limit_price="750.30",
        client_order_id="geld-close-test",
        id="order-id",
    )

    class FakeClient:
        request = None

        def get_orders(self, filter):
            self.request = filter
            return [order]

    broker = object.__new__(AlpacaPaperBroker)
    broker.client = FakeClient()
    activity = broker.order_activity(pd.Timestamp("2026-07-21", tz="UTC"))
    assert activity.iloc[0]["status"] == "filled"
    assert activity.iloc[0]["filled_quantity"] == 2.5
    assert activity.iloc[0]["client_order_id"] == "geld-close-test"
