import json
import tomllib
from types import SimpleNamespace

import pandas as pd
import pytest

from project_geld import universe_refresh as refresh

NOW = pd.Timestamp("2026-09-15T14:00:00Z")
SYMBOLS = ["A" + chr(65 + i // 26) + chr(65 + i % 26) for i in range(200)]
SOURCE = '''[universe]
symbols = []
symbols_file = "../universes/research.csv.gz"
benchmark = "SPY"
[strategy]
name = "intra_v15"
[strategy.parameters]
gross_exposure = 0.40
[account]
credential_profile = "INTRADAY"
[paper]
max_universe_age_days = 45
enabled = true
'''


def snapshot(date="2026-09-14T04:00:00Z"):
    return pd.DataFrame({"timestamp": [date] * 100, "symbol": SYMBOLS[:100]})


@pytest.fixture
def fake_source(tmp_path, monkeypatch):
    config = tmp_path / "original.toml"
    config.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "paper"
    dates = pd.bdate_range(end="2026-09-15", periods=75, tz="America/New_York").tz_convert("UTC")

    class Source:
        missing = set()
        fail = False
        old_benchmark = False

        def __init__(self, feed, adjustment, profile):
            assert (feed, adjustment, profile) == ("sip", "raw", "INTRADAY")

        def fetch(self, symbols, start, end):
            if self.fail and symbols != ["SPY"]:
                raise RuntimeError("download failed")
            used_dates = dates[:-20] if self.old_benchmark and symbols == ["SPY"] else dates
            return pd.concat([
                pd.DataFrame({"timestamp": used_dates, "symbol": symbol, "open": 50.,
                              "close": 50., "high": 51., "low": 49., "volume": 2_000_000.})
                for symbol in symbols if symbol not in self.missing
            ], ignore_index=True)

    monkeypatch.setattr(refresh, "AlpacaBarSource", Source)
    monkeypatch.setattr(refresh, "active_candidates", lambda _: pd.DataFrame({"symbol": SYMBOLS}))
    monkeypatch.setattr(refresh, "check_account_membership", lambda *args: None)
    return config, output, Source


def test_refresh_builds_current_universe_without_changing_research_config(fake_source):
    config, output, _ = fake_source
    generated = refresh.refresh(config, output, now=NOW)
    current = pd.read_csv(output / "universe.csv")
    assert current["symbol"].tolist() == SYMBOLS[:100]
    assert refresh.snapshot_date(current) == pd.Timestamp("2026-09-14T04:00:00Z")
    assert config.read_text(encoding="utf-8") == SOURCE
    original = tomllib.loads(SOURCE)
    original["universe"]["symbols_file"] = (output / "universe.csv").as_posix()
    assert tomllib.loads(generated.read_text()) == original
    manifest = json.loads(next(output.rglob("manifest.json")).read_text())
    assert manifest["latest_session_coverage"] == 1.0
    assert manifest["rules"]["minimum_history_sessions"] == 60


def test_current_snapshot_avoids_network_and_updates_runtime_config(fake_source):
    config, output, Source = fake_source
    output.mkdir()
    snapshot().to_csv(output / "universe.csv", index=False)
    Source.fail = True
    refresh.refresh(config, output, now=NOW)
    assert (output / "runtime-config.toml").exists()
    assert not (output / "universe-refresh").exists()


@pytest.mark.parametrize("failure", ["fail", "missing", "old_benchmark"])
def test_failed_refresh_preserves_snapshot_and_runtime_config(fake_source, failure):
    config, output, Source = fake_source
    output.mkdir()
    snapshot("2026-07-17T04:00:00Z").to_csv(output / "universe.csv", index=False)
    (output / "runtime-config.toml").write_text("previous config")
    original = (output / "universe.csv").read_bytes()
    setattr(Source, failure, set(SYMBOLS[:30]) if failure == "missing" else True)
    with pytest.raises((RuntimeError, ValueError)):
        refresh.refresh(config, output, now=NOW)
    assert (output / "universe.csv").read_bytes() == original
    assert (output / "runtime-config.toml").read_text() == "previous config"


def test_month_change_triggers_refresh():
    assert refresh.needs_refresh(snapshot("2026-08-31T04:00:00Z"), NOW)
    assert not refresh.needs_refresh(snapshot(), NOW)


@pytest.mark.parametrize("date", ["2026-09-15T04:00:00Z", "2026-09-16T04:00:00Z"])
def test_current_day_and_future_snapshots_are_rejected(date):
    with pytest.raises(ValueError, match="completed earlier session"):
        refresh.needs_refresh(snapshot(date), NOW)


@pytest.mark.parametrize("invalid", ["duplicate", "missing", "mixed_dates", "empty_symbol"])
def test_invalid_snapshot_is_rejected(invalid):
    frame = snapshot()
    if invalid == "duplicate":
        frame.loc[1, "symbol"] = frame.loc[0, "symbol"]
    elif invalid == "missing":
        frame = frame.iloc[:99]
    elif invalid == "mixed_dates":
        frame.loc[1, "timestamp"] = "2026-08-31T04:00:00Z"
    else:
        frame.loc[1, "symbol"] = ""
    with pytest.raises(ValueError):
        refresh.snapshot_date(frame)


def test_runtime_config_preserves_all_other_settings_and_handles_spaces(tmp_path):
    target = tmp_path / "folder with spaces" / "universe.csv"
    result = tomllib.loads(refresh.runtime_config(SOURCE, target))
    expected = tomllib.loads(SOURCE)
    expected["universe"]["symbols_file"] = target.as_posix()
    assert result == expected


def test_inline_symbols_cannot_silently_expand_refreshed_universe(tmp_path):
    with pytest.raises(ValueError, match="file-only"):
        refresh.runtime_config(SOURCE.replace("symbols = []", 'symbols = ["XYZ"]'), tmp_path / "x.csv")


@pytest.mark.parametrize("unmanaged,orders", [(1.0, set()), (0.0, {"REMOVED"})])
def test_refresh_rejects_stranded_positions_or_orders(monkeypatch, unmanaged, orders):
    from project_geld import paper
    account = SimpleNamespace(unmanaged_notional=unmanaged, open_order_symbols=orders)
    monkeypatch.setattr(paper, "AlpacaPaperBroker", lambda _: SimpleNamespace(snapshot=lambda _: account))
    with pytest.raises(ValueError, match="existing paper position or open order"):
        refresh.check_account_membership("INTRADAY", ["SPY", "NEW"])
