from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd
from dotenv import load_dotenv


BAR_COLUMNS = ["timestamp", "symbol", "open", "high", "low", "close", "volume"]


class BarSource(Protocol):
    def fetch(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        timeframe: str = "1Day",
    ) -> pd.DataFrame: ...


def normalize_bars(frame: pd.DataFrame) -> pd.DataFrame:
    missing = set(BAR_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"Bar data is missing columns: {sorted(missing)}")
    bars = frame[BAR_COLUMNS].copy()
    bars["timestamp"] = pd.to_datetime(bars["timestamp"], utc=True)
    bars["symbol"] = bars["symbol"].astype(str).str.upper()
    for column in ["open", "high", "low", "close", "volume"]:
        bars[column] = pd.to_numeric(bars[column], errors="coerce")
    bars = bars.dropna(subset=["timestamp", "symbol", "open", "close"])
    bars = bars[bars["open"].gt(0) & bars["close"].gt(0)]
    bars = bars.drop_duplicates(["timestamp", "symbol"], keep="last")
    return bars.sort_values(["timestamp", "symbol"]).reset_index(drop=True)


@dataclass
class CsvBarSource:
    path: Path

    def fetch(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        timeframe: str = "1Day",
    ) -> pd.DataFrame:
        bars = normalize_bars(pd.read_csv(self.path))
        wanted = {symbol.upper() for symbol in symbols}
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        if start_ts.tzinfo is None:
            start_ts = start_ts.tz_localize("UTC")
        else:
            start_ts = start_ts.tz_convert("UTC")
        if end_ts.tzinfo is None:
            end_ts = end_ts.tz_localize("UTC")
        else:
            end_ts = end_ts.tz_convert("UTC")
        return bars[
            bars["symbol"].isin(wanted)
            & bars["timestamp"].between(start_ts, end_ts, inclusive="both")
        ].reset_index(drop=True)


@dataclass
class SyntheticBarSource:
    seed: int = 7

    def fetch(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        timeframe: str = "1Day",
    ) -> pd.DataFrame:
        if timeframe.lower() not in {"1day", "day"}:
            raise ValueError("SyntheticBarSource currently supports daily bars only.")
        dates = pd.date_range(start=pd.Timestamp(start).date(), end=pd.Timestamp(end).date(), freq="B", tz="UTC")
        rng = np.random.default_rng(self.seed)
        frames: list[pd.DataFrame] = []
        common = rng.normal(0.00025, 0.009, len(dates))
        for index, symbol in enumerate(symbols):
            idiosyncratic = rng.normal(0.00005 * index, 0.006 + index * 0.0003, len(dates))
            returns = 0.65 * common + idiosyncratic
            close = (80.0 + index * 15.0) * np.exp(np.cumsum(returns))
            overnight = rng.normal(0, 0.0025, len(dates))
            open_ = np.r_[close[0], close[:-1]] * (1 + overnight)
            high = np.maximum(open_, close) * (1 + rng.uniform(0.0005, 0.01, len(dates)))
            low = np.minimum(open_, close) * (1 - rng.uniform(0.0005, 0.01, len(dates)))
            frames.append(
                pd.DataFrame(
                    {
                        "timestamp": dates,
                        "symbol": symbol.upper(),
                        "open": open_,
                        "high": high,
                        "low": low,
                        "close": close,
                        "volume": rng.integers(750_000, 12_000_000, len(dates)),
                    }
                )
            )
        return normalize_bars(pd.concat(frames, ignore_index=True))


class AlpacaBarSource:
    def __init__(self, feed: str = "iex", adjustment: str = "all") -> None:
        load_dotenv()
        api_key = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
        secret_key = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
        if not api_key or not secret_key:
            raise RuntimeError("Set ALPACA_API_KEY and ALPACA_SECRET_KEY in .env.")

        from alpaca.data.historical import StockHistoricalDataClient

        self.client = StockHistoricalDataClient(api_key, secret_key)
        self.feed = feed.lower()
        self.adjustment = adjustment.lower()

    def fetch(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        timeframe: str = "1Day",
    ) -> pd.DataFrame:
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        timeframe_map = {
            "1day": TimeFrame.Day,
            "day": TimeFrame.Day,
            "1min": TimeFrame.Minute,
            "minute": TimeFrame.Minute,
        }
        adjustment_map = {
            "all": Adjustment.ALL,
            "raw": Adjustment.RAW,
            "split": Adjustment.SPLIT,
            "dividend": Adjustment.DIVIDEND,
        }
        feed_map = {"iex": DataFeed.IEX, "sip": DataFeed.SIP}
        key = timeframe.lower()
        if key not in timeframe_map:
            raise ValueError(f"Unsupported Alpaca timeframe: {timeframe}")
        request = StockBarsRequest(
            symbol_or_symbols=[symbol.upper() for symbol in symbols],
            timeframe=timeframe_map[key],
            start=start,
            end=end,
            adjustment=adjustment_map[self.adjustment],
            feed=feed_map[self.feed],
        )
        response = self.client.get_stock_bars(request)
        if response.df.empty:
            return pd.DataFrame(columns=BAR_COLUMNS)
        return normalize_bars(response.df.reset_index())


@dataclass
class CachedBarSource:
    source: BarSource
    cache_dir: Path

    def fetch(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        timeframe: str = "1Day",
    ) -> pd.DataFrame:
        signature = "|".join(
            [
                ",".join(sorted(symbol.upper() for symbol in symbols)),
                pd.Timestamp(start).isoformat(),
                pd.Timestamp(end).isoformat(),
                timeframe,
                type(self.source).__name__,
            ]
        )
        digest = sha256(signature.encode("utf-8")).hexdigest()[:16]
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"bars_{digest}.csv"
        if path.exists():
            return normalize_bars(pd.read_csv(path))
        bars = self.source.fetch(symbols, start, end, timeframe)
        if not bars.empty:
            bars.to_csv(path, index=False)
        return bars


def default_date_range(lookback_days: int = 730) -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc)
    return end - timedelta(days=lookback_days), end
