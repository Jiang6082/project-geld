"""Ingest a CRSP daily stock-file export into Project Geld's bar/universe format.

CRSP (via WRDS) is the survivorship-bias-free, point-in-time reference dataset
for US equities back to 1925, which is exactly the independent long history the
Alpaca-based research lacks. This module converts a CRSP daily CSV export into
the engine's normalized bar schema and builds a monthly point-in-time
top-liquidity universe, so Daily V4 can be backtested out of sample with the
existing `run_backtest` / broad-universe machinery.

Expected CRSP columns (standard DSF names; override via ``columns`` if your
export differs):
    PERMNO, date, TICKER, PRC, OPENPRC, ASKHI, BIDLO, VOL, CFACPR, CFACSHR

Notes on CRSP conventions handled here:
    - PRC is negative when it is a bid/ask average; the magnitude is used.
    - Prices are split/dividend-adjusted by dividing by CFACPR; volume is scaled
      by CFACSHR so it is comparable across splits.
    - Missing OPENPRC/ASKHI/BIDLO fall back to the (adjusted) close.
    - The bar timestamp is midnight America/New_York converted to UTC, so the
      backtest recovers the correct session date.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from project_geld.data import BAR_COLUMNS, normalize_bars


DEFAULT_COLUMNS = {
    "permno": "PERMNO",
    "date": "date",
    "ticker": "TICKER",
    "price": "PRC",
    "open": "OPENPRC",
    "high": "ASKHI",
    "low": "BIDLO",
    "volume": "VOL",
    "price_factor": "CFACPR",
    "share_factor": "CFACSHR",
}


@dataclass(frozen=True)
class CrspIngestConfig:
    columns: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_COLUMNS))
    symbol_from: str = "ticker"  # "ticker" or "permno"
    timezone: str = "America/New_York"


def load_crsp_daily(
    frame: pd.DataFrame, config: CrspIngestConfig | None = None
) -> pd.DataFrame:
    """Normalize a raw CRSP daily frame into engine bars (adjusted OHLCV)."""
    config = config or CrspIngestConfig()
    cols = config.columns
    required = [cols["permno"], cols["date"], cols["price"]]
    missing = [name for name in required if name not in frame.columns]
    if missing:
        raise ValueError(f"CRSP export is missing required columns: {missing}")

    data = frame.copy()
    if config.symbol_from == "ticker" and cols["ticker"] in data.columns:
        symbol = data[cols["ticker"]].astype("string")
        symbol = symbol.fillna(data[cols["permno"]].astype("string"))
    else:
        symbol = data[cols["permno"]].astype("string")
    data["symbol"] = symbol.str.upper().str.strip()

    date = pd.to_datetime(data[cols["date"]].astype(str), format="mixed", utc=False)
    data["timestamp"] = (
        date.dt.tz_localize(config.timezone, ambiguous="NaT", nonexistent="shift_forward")
        .dt.tz_convert("UTC")
    )

    price = data[cols["price"]].abs()
    factor = (
        data[cols["price_factor"]].replace(0, np.nan)
        if cols["price_factor"] in data.columns
        else pd.Series(1.0, index=data.index)
    ).fillna(1.0)
    share_factor = (
        data[cols["share_factor"]].replace(0, np.nan)
        if cols["share_factor"] in data.columns
        else pd.Series(1.0, index=data.index)
    ).fillna(1.0)

    close = price / factor

    def adjusted(name: str) -> pd.Series:
        source = cols[name]
        if source in data.columns:
            raw = data[source].abs()
            return (raw / factor).where(raw.notna() & raw.gt(0), close)
        return close

    data["close"] = close
    data["open"] = adjusted("open")
    data["high"] = adjusted("high")
    data["low"] = adjusted("low")
    volume = (
        data[cols["volume"]] if cols["volume"] in data.columns else pd.Series(0.0, index=data.index)
    )
    data["volume"] = (pd.to_numeric(volume, errors="coerce").fillna(0.0) * share_factor).clip(lower=0.0)

    bars = data.loc[
        data["timestamp"].notna() & data["close"].gt(0), BAR_COLUMNS
    ]
    bars = bars.dropna(subset=["open", "high", "low", "close"])
    return normalize_bars(bars)


def monthly_pit_universe(
    bars: pd.DataFrame,
    top_n: int = 500,
    minimum_price: float = 5.0,
    minimum_dollar_volume: float = 20_000_000.0,
    liquidity_window: int = 63,
    benchmark: str = "SPY",
) -> dict[str, list[list[str | None]]]:
    """Build monthly point-in-time top-liquidity membership from CRSP bars.

    Each calendar month, a symbol is a member if its trailing median dollar
    volume ranks in the top ``top_n`` and it clears the price/volume floors. The
    returned mapping is the ``membership_periods`` format the momentum strategies
    accept. The benchmark is never a member (it is context, not a holding).
    """
    close = bars.pivot(index="timestamp", columns="symbol", values="close").sort_index()
    volume = (
        bars.pivot(index="timestamp", columns="symbol", values="volume")
        .reindex_like(close)
        .fillna(0.0)
    )
    dollar_volume = (close * volume).rolling(liquidity_window, min_periods=liquidity_window // 2).median()
    months = close.index.tz_convert("UTC").tz_localize(None).to_period("M")
    membership: dict[str, list[list[str | None]]] = {}
    for period in pd.PeriodIndex(months.unique()).sort_values():
        month_mask = months == period
        as_of = close.index[month_mask][-1]
        eligible_price = close.loc[as_of] >= minimum_price
        liquidity = dollar_volume.loc[as_of]
        eligible = eligible_price & liquidity.ge(minimum_dollar_volume)
        if benchmark.upper() in eligible.index:
            eligible[benchmark.upper()] = False
        ranked = liquidity.where(eligible).dropna().sort_values(ascending=False)
        start = period.start_time.strftime("%Y-%m-%d")
        end = period.end_time.strftime("%Y-%m-%d")
        for symbol in ranked.head(top_n).index:
            membership.setdefault(str(symbol), []).append([start, end])
    return _merge_adjacent(membership)


def _merge_adjacent(
    membership: dict[str, list[list[str | None]]]
) -> dict[str, list[list[str | None]]]:
    merged: dict[str, list[list[str | None]]] = {}
    for symbol, periods in membership.items():
        periods = sorted(periods, key=lambda item: item[0])
        collapsed: list[list[str | None]] = []
        for start, end in periods:
            adjacent = collapsed and (
                pd.Timestamp(start) - pd.Timestamp(collapsed[-1][1])
            ).days <= 4
            if adjacent:
                collapsed[-1][1] = end
            else:
                collapsed.append([start, end])
        merged[symbol] = collapsed
    return merged
