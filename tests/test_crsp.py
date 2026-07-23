import numpy as np
import pandas as pd

from project_geld.crsp import load_crsp_daily, monthly_pit_universe


def _sample_crsp() -> pd.DataFrame:
    rows = []
    dates = pd.bdate_range("2001-01-02", periods=80)
    for i, date in enumerate(dates):
        # AAA: a 2-for-1 split convention (CFACPR=2 before split), negative PRC once.
        price = 100.0 + i
        rows.append(
            {
                "PERMNO": 10001,
                "date": date.strftime("%Y-%m-%d"),
                "TICKER": "AAA",
                "PRC": -price if i == 0 else price,  # first day is a bid/ask average
                "OPENPRC": price - 0.5,
                "ASKHI": price + 1.0,
                "BIDLO": price - 1.0,
                "VOL": 5_000_000,
                "CFACPR": 2.0,
                "CFACSHR": 2.0,
            }
        )
        rows.append(
            {
                "PERMNO": 10002,
                "date": date.strftime("%Y-%m-%d"),
                "TICKER": "BBB",
                "PRC": 50.0 + i * 0.5,
                "OPENPRC": np.nan,  # missing -> falls back to close
                "ASKHI": np.nan,
                "BIDLO": np.nan,
                "VOL": 1_000_000,
                "CFACPR": 1.0,
                "CFACSHR": 1.0,
            }
        )
    return pd.DataFrame(rows)


def test_load_crsp_daily_normalizes_prices_and_adjustment():
    bars = load_crsp_daily(_sample_crsp())
    assert set(bars.columns) == {"timestamp", "symbol", "open", "high", "low", "close", "volume"}
    assert bars["timestamp"].dt.tz is not None
    aaa = bars[bars["symbol"] == "AAA"].sort_values("timestamp")
    # Negative PRC becomes positive, and CFACPR=2 halves the adjusted price.
    first = aaa.iloc[0]
    assert first["close"] > 0
    assert first["close"] == 100.0 / 2.0
    # Missing OHLC falls back to the adjusted close (BBB).
    bbb = bars[bars["symbol"] == "BBB"].iloc[0]
    assert bbb["open"] == bbb["close"] == bbb["high"] == bbb["low"]
    # Session date is preserved through the tz conversion.
    assert aaa.iloc[0]["timestamp"].tz_convert("America/New_York").date().isoformat() == "2001-01-02"


def test_monthly_pit_universe_builds_membership():
    bars = load_crsp_daily(_sample_crsp())
    membership = monthly_pit_universe(
        bars, top_n=1, minimum_price=1.0, minimum_dollar_volume=1.0,
        liquidity_window=20, benchmark="SPY",
    )
    # AAA has the higher dollar volume, so it is the top-1 member; BBB is not.
    assert "AAA" in membership
    assert "BBB" not in membership
    # Membership periods are well-formed [start, end] ranges.
    for periods in membership.values():
        for start, end in periods:
            assert pd.Timestamp(start) <= pd.Timestamp(end)
