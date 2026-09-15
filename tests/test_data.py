import pandas as pd

from project_geld.data import (
    BAR_COLUMNS,
    CachedBarSource,
    completed_daily_bars,
    fetch_rolling_bars,
    normalize_bars,
)


def test_normalize_bars_sorts_and_deduplicates():
    frame = pd.DataFrame(
        [
            ["2024-01-03", "spy", 101, 103, 99, 102, 10],
            ["2024-01-02", "spy", 100, 102, 99, 101, 9],
            ["2024-01-02", "spy", 100, 102, 99, 101.5, 11],
        ],
        columns=BAR_COLUMNS,
    )
    bars = normalize_bars(frame)
    assert len(bars) == 2
    assert bars.iloc[0]["close"] == 101.5
    assert bars.iloc[0]["symbol"] == "SPY"
    assert str(bars["timestamp"].dt.tz) == "UTC"


def test_normalize_bars_rejects_incomplete_schema():
    try:
        normalize_bars(pd.DataFrame({"timestamp": []}))
    except ValueError as exc:
        assert "missing columns" in str(exc).lower()
    else:
        raise AssertionError("Expected a schema error.")


def test_cached_source_batches_large_symbol_requests_and_reuses_cache(tmp_path):
    class RecordingSource:
        def __init__(self):
            self.calls = []

        def fetch(self, symbols, start, end, timeframe="1Day"):
            self.calls.append(list(symbols))
            return pd.DataFrame(
                {
                    "timestamp": pd.Timestamp("2026-01-02", tz="UTC"),
                    "symbol": symbols,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 1_000_000,
                }
            )

    source = RecordingSource()
    cached = CachedBarSource(source, tmp_path, batch_size=2)
    symbols = ["A", "B", "C", "D", "E"]
    first = cached.fetch(symbols, pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-03"))
    second = cached.fetch(symbols, pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-03"))
    assert source.calls == [["A", "B"], ["C", "D"], ["E"]]
    assert set(first["symbol"]) == set(symbols)
    pd.testing.assert_frame_equal(first, second)


def test_rolling_cache_fetches_only_overlap_after_initial_history(tmp_path):
    class RecordingSource:
        def __init__(self):
            self.starts = []

        def fetch(self, symbols, start, end, timeframe="1Min"):
            self.starts.append(pd.Timestamp(start))
            timestamp = pd.Timestamp("2026-01-02 15:00", tz="UTC")
            if len(self.starts) == 2:
                timestamp += pd.Timedelta(1, unit="min")
            return pd.DataFrame(
                {
                    "timestamp": timestamp,
                    "symbol": symbols,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 1_000_000,
                }
            )

    source = RecordingSource()
    cache_path = tmp_path / "rolling.pkl"
    start = pd.Timestamp("2026-01-01", tz="UTC").to_pydatetime()
    end = pd.Timestamp("2026-01-03", tz="UTC").to_pydatetime()
    first = fetch_rolling_bars(source, ["A", "B"], start, end, "1Min", cache_path)
    second = fetch_rolling_bars(source, ["A", "B"], start, end, "1Min", cache_path)
    assert source.starts == [
        pd.Timestamp(start),
        pd.Timestamp("2026-01-02 14:55", tz="UTC"),
    ]
    assert len(first) == 2
    assert len(second) == 4
    assert cache_path.exists()


def test_completed_daily_bars_excludes_current_session():
    bars = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2026-07-17 04:00:00+00:00", "2026-07-20 04:00:00+00:00"]
            ),
            "symbol": ["SPY", "SPY"],
            "open": [100.0, 101.0],
            "high": [101.0, 102.0],
            "low": [99.0, 100.0],
            "close": [100.5, 101.5],
            "volume": [1_000_000, 100],
        }
    )
    completed = completed_daily_bars(
        bars,
        pd.Timestamp("2026-07-20 09:31", tz="America/New_York"),
    )
    assert len(completed) == 1
    assert completed.iloc[0]["close"] == 100.5


def test_rolling_cache_backfills_new_universe_members(tmp_path):
    start = pd.Timestamp("2026-08-01", tz="UTC")
    end = pd.Timestamp("2026-09-15", tz="UTC")
    latest = pd.Timestamp("2026-09-14 19:00", tz="UTC")
    cache = tmp_path / "rolling.pkl"
    pd.DataFrame([[latest, "OLD", 10, 11, 9, 10, 100]], columns=BAR_COLUMNS).to_pickle(cache)

    class Source:
        calls = []

        def fetch(self, symbols, beginning, ending, timeframe):
            self.calls.append((symbols, pd.Timestamp(beginning)))
            return pd.DataFrame([[beginning, symbol, 10, 11, 9, 10, 100]
                                 for symbol in symbols], columns=BAR_COLUMNS)

    source = Source()
    result = fetch_rolling_bars(source, ["OLD", "NEW"], start, end, "1Min", cache)
    assert source.calls == [(["OLD"], latest - pd.Timedelta(minutes=5)), (["NEW"], start)]
    assert result.loc[result.symbol.eq("NEW"), "timestamp"].min() == start
