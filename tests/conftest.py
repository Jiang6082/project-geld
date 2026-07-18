from datetime import datetime, timezone

import pytest

from project_geld.data import SyntheticBarSource


@pytest.fixture
def synthetic_bars():
    return SyntheticBarSource(seed=11).fetch(
        ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"],
        datetime(2023, 1, 1, tzinfo=timezone.utc),
        datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
