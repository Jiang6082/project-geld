import pandas as pd

from project_geld.data import BAR_COLUMNS, normalize_bars


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
