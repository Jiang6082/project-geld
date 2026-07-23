from dataclasses import replace

import pytest

from project_geld.config import load_config, validate_config


def test_example_config_loads():
    config = load_config("config.example.toml")
    validate_config(config)
    assert config.universe.benchmark == "SPY"
    assert isinstance(config.paper.enabled, bool)


def test_intra_v15_enables_stale_order_recovery_and_market_exits():
    config = load_config("configs/paper-intra-v15.toml")
    validate_config(config)
    assert config.paper.stale_order_seconds == 300
    assert config.paper.market_exit_orders is True


def test_invalid_gross_exposure_is_rejected():
    config = load_config("config.example.toml")
    with pytest.raises(ValueError, match="max_gross_exposure"):
        validate_config(config.__class__(
            universe=config.universe,
            data=config.data,
            strategy=config.strategy,
            backtest=config.backtest,
            risk=replace(config.risk, max_gross_exposure=1.5),
            paper=config.paper,
        ))


def test_benchmark_is_data_only_when_not_in_tradable_symbols():
    config = load_config("configs/equity-momentum-v2.toml")
    assert "SPY" not in config.universe.symbols
    assert "SPY" in config.universe.data_symbols


def test_latest_timestamp_is_loaded_from_universe_csv(tmp_path):
    symbols = tmp_path / "universe.csv"
    symbols.write_text(
        "timestamp,symbol\n2026-06-30,OLD\n2026-07-31,AAPL\n2026-07-31,MSFT\n",
        encoding="utf-8",
    )
    path = tmp_path / "config.toml"
    path.write_text(
        f'[universe]\nsymbols = []\nsymbols_file = "{symbols.as_posix()}"\n',
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.universe.symbols == ["AAPL", "MSFT"]
    assert config.universe.symbols_as_of.startswith("2026-07-31")
