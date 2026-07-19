import pandas as pd

from project_geld.data import BAR_COLUMNS, CachedBarSource, normalize_bars


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
