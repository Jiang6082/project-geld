from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import tomllib


@dataclass(frozen=True)
class UniverseConfig:
    symbols: list[str]
    benchmark: str = "SPY"
    symbols_file: Path | None = None
    symbols_as_of: str | None = None

    @property
    def data_symbols(self) -> list[str]:
        return list(dict.fromkeys([*self.symbols, self.benchmark]))


@dataclass(frozen=True)
class DataConfig:
    feed: str = "iex"
    adjustment: str = "all"
    cache_dir: Path = Path("data/cache")


@dataclass(frozen=True)
class AccountConfig:
    name: str = "default"
    credential_profile: str = ""
    confirmation_env: str = "PROJECT_GELD_CONFIRM_PAPER"


@dataclass(frozen=True)
class StrategyConfig:
    name: str = "momentum"
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float = 100_000.0
    slippage_bps: float = 5.0
    commission_per_share: float = 0.0
    allow_fractional: bool = True
    rebalance_every: int = 5
    missing_price_exit_sessions: int = 5
    missing_price_haircut_pct: float = 0.25
    force_flat_at_session_end: bool = False
    session_timezone: str = "America/New_York"


@dataclass(frozen=True)
class RiskConfig:
    max_gross_exposure: float = 1.0
    max_position_weight: float = 0.35
    max_order_notional: float = 20_000.0
    max_order_pct_equity: float | None = None
    symbol_position_weight_limits: dict[str, float] = field(default_factory=dict)
    symbol_order_notional_limits: dict[str, float] = field(default_factory=dict)
    symbol_order_pct_equity_limits: dict[str, float] = field(default_factory=dict)
    min_trade_notional: float = 50.0
    min_trade_pct_equity: float = 0.0
    max_daily_loss_pct: float = 0.02


@dataclass(frozen=True)
class PaperConfig:
    enabled: bool = False
    lookback_days: int = 400
    client_order_prefix: str = "geld"
    rebalance_every_sessions: int = 10
    state_file: Path = Path("artifacts/paper/rebalance_state.json")
    cash_buffer_pct: float = 0.01
    max_universe_age_days: int = 45
    execution_style: str = "market"
    limit_offset_bps: float = 0.0


@dataclass(frozen=True)
class IntradayConfig:
    bar_minutes: int = 15
    lookback_days: int = 10
    state_file: Path = Path("artifacts/intraday/cycle_state.json")


@dataclass(frozen=True)
class AppConfig:
    universe: UniverseConfig
    account: AccountConfig = AccountConfig()
    data: DataConfig = DataConfig()
    strategy: StrategyConfig = StrategyConfig()
    backtest: BacktestConfig = BacktestConfig()
    risk: RiskConfig = RiskConfig()
    paper: PaperConfig = PaperConfig()
    intraday: IntradayConfig = IntradayConfig()


def load_config(path: str | Path = "config.example.toml") -> AppConfig:
    config_path = Path(path)
    raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    universe_raw = raw["universe"]
    symbols_file_raw = universe_raw.get("symbols_file")
    symbols_file = Path(symbols_file_raw) if symbols_file_raw else None
    inline_symbols = [str(s).upper() for s in universe_raw.get("symbols", [])]
    file_symbols: list[str] = []
    symbols_as_of: str | None = None
    if symbols_file is not None:
        candidate_path = symbols_file
        if not candidate_path.exists():
            candidate_path = config_path.parent / symbols_file
        if not candidate_path.exists():
            raise FileNotFoundError(f"Universe symbols file not found: {symbols_file}")
        if candidate_path.suffix.lower() == ".json":
            import json

            loaded = json.loads(candidate_path.read_text(encoding="utf-8"))
            values = loaded.get("symbols", []) if isinstance(loaded, dict) else loaded
            file_symbols = [str(symbol).upper() for symbol in values]
        else:
            import pandas as pd

            frame = pd.read_csv(candidate_path)
            if "symbol" not in frame.columns:
                raise ValueError("Universe CSV must contain a 'symbol' column.")
            if "timestamp" in frame.columns and len(frame):
                timestamps = pd.to_datetime(frame["timestamp"], utc=True)
                frame = frame[timestamps.eq(timestamps.max())]
                symbols_as_of = timestamps.max().isoformat()
            file_symbols = [str(symbol).upper() for symbol in frame["symbol"]]
    symbols = list(dict.fromkeys([*inline_symbols, *file_symbols]))
    benchmark = str(universe_raw.get("benchmark", "SPY")).upper()
    data_raw = raw.get("data", {})
    account_raw = raw.get("account", {})
    strategy_raw = raw.get("strategy", {})
    return AppConfig(
        universe=UniverseConfig(
            symbols=symbols,
            benchmark=benchmark,
            symbols_file=symbols_file,
            symbols_as_of=symbols_as_of,
        ),
        account=AccountConfig(
            name=str(account_raw.get("name", "default")),
            credential_profile=str(account_raw.get("credential_profile", "")),
            confirmation_env=str(
                account_raw.get(
                    "confirmation_env", "PROJECT_GELD_CONFIRM_PAPER"
                )
            ),
        ),
        data=DataConfig(
            feed=str(data_raw.get("feed", "iex")).lower(),
            adjustment=str(data_raw.get("adjustment", "all")).lower(),
            cache_dir=Path(data_raw.get("cache_dir", "data/cache")),
        ),
        strategy=StrategyConfig(
            name=str(strategy_raw.get("name", "momentum")).lower(),
            parameters=dict(strategy_raw.get("parameters", {})),
        ),
        backtest=BacktestConfig(**raw.get("backtest", {})),
        risk=RiskConfig(**raw.get("risk", {})),
        paper=PaperConfig(
            enabled=bool(raw.get("paper", {}).get("enabled", False)),
            lookback_days=int(raw.get("paper", {}).get("lookback_days", 400)),
            client_order_prefix=str(
                raw.get("paper", {}).get("client_order_prefix", "geld")
            ),
            rebalance_every_sessions=int(
                raw.get("paper", {}).get("rebalance_every_sessions", 10)
            ),
            state_file=Path(
                raw.get("paper", {}).get(
                    "state_file", "artifacts/paper/rebalance_state.json"
                )
            ),
            cash_buffer_pct=float(
                raw.get("paper", {}).get("cash_buffer_pct", 0.01)
            ),
            max_universe_age_days=int(
                raw.get("paper", {}).get("max_universe_age_days", 45)
            ),
            execution_style=str(
                raw.get("paper", {}).get("execution_style", "market")
            ).lower(),
            limit_offset_bps=float(
                raw.get("paper", {}).get("limit_offset_bps", 0.0)
            ),
        ),
        intraday=IntradayConfig(
            bar_minutes=int(raw.get("intraday", {}).get("bar_minutes", 15)),
            lookback_days=int(raw.get("intraday", {}).get("lookback_days", 10)),
            state_file=Path(
                raw.get("intraday", {}).get(
                    "state_file", "artifacts/intraday/cycle_state.json"
                )
            ),
        ),
    )


def validate_config(config: AppConfig) -> None:
    if not config.universe.symbols:
        raise ValueError("The universe must contain at least one symbol.")
    if config.backtest.initial_cash <= 0:
        raise ValueError("initial_cash must be positive.")
    if config.backtest.rebalance_every < 1:
        raise ValueError("rebalance_every must be at least 1.")
    if config.backtest.missing_price_exit_sessions < 1:
        raise ValueError("missing_price_exit_sessions must be at least 1.")
    if not 0 <= config.backtest.missing_price_haircut_pct < 1:
        raise ValueError("missing_price_haircut_pct must be in [0, 1).")
    if not 0 < config.risk.max_gross_exposure <= 1:
        raise ValueError("max_gross_exposure must be in (0, 1].")
    if not 0 < config.risk.max_position_weight <= 1:
        raise ValueError("max_position_weight must be in (0, 1].")
    if not 0 < config.risk.max_daily_loss_pct < 1:
        raise ValueError("max_daily_loss_pct must be in (0, 1).")
    if not 0 <= config.risk.min_trade_pct_equity < 1:
        raise ValueError("min_trade_pct_equity must be in [0, 1).")
    if config.risk.max_order_pct_equity is not None and not (
        0 < config.risk.max_order_pct_equity <= 1
    ):
        raise ValueError("max_order_pct_equity must be in (0, 1].")
    if any(
        not 0 < float(limit) <= 1
        for limit in config.risk.symbol_position_weight_limits.values()
    ):
        raise ValueError("Symbol position-weight limits must be in (0, 1].")
    if any(
        float(limit) <= 0
        for limit in config.risk.symbol_order_notional_limits.values()
    ):
        raise ValueError("Symbol order-notional limits must be positive.")
    if any(
        not 0 < float(limit) <= 1
        for limit in config.risk.symbol_order_pct_equity_limits.values()
    ):
        raise ValueError("Symbol order equity-percentage limits must be in (0, 1].")
    if config.paper.rebalance_every_sessions < 1:
        raise ValueError("rebalance_every_sessions must be at least 1.")
    if not 0 <= config.paper.cash_buffer_pct < 1:
        raise ValueError("cash_buffer_pct must be in [0, 1).")
    if config.paper.max_universe_age_days < 1:
        raise ValueError("max_universe_age_days must be positive.")
    if config.paper.execution_style not in {"market", "marketable_limit"}:
        raise ValueError("paper.execution_style must be market or marketable_limit.")
    if not 0 <= config.paper.limit_offset_bps <= 100:
        raise ValueError("paper.limit_offset_bps must be in [0, 100].")
    if config.intraday.bar_minutes not in {1, 5, 10, 15, 30, 60}:
        raise ValueError("intraday.bar_minutes must be one of 1, 5, 10, 15, 30, 60.")
    if config.intraday.lookback_days < 1:
        raise ValueError("intraday.lookback_days must be positive.")
    if not config.account.confirmation_env:
        raise ValueError("account.confirmation_env cannot be empty.")
