from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd
from project_geld.credentials import load_alpaca_credentials


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
    bars["timestamp"] = pd.to_datetime(bars["timestamp"], utc=True).astype(
        "datetime64[ns, UTC]"
    )
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
    def __init__(
        self, feed: str = "iex", adjustment: str = "all", credential_profile: str = ""
    ) -> None:
        api_key, secret_key = load_alpaca_credentials(credential_profile)
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
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        timeframe_map = {
            "1day": TimeFrame.Day,
            "day": TimeFrame.Day,
            "1min": TimeFrame.Minute,
            "minute": TimeFrame.Minute,
            "5min": TimeFrame(5, TimeFrameUnit.Minute),
            "10min": TimeFrame(10, TimeFrameUnit.Minute),
            "15min": TimeFrame(15, TimeFrameUnit.Minute),
            "30min": TimeFrame(30, TimeFrameUnit.Minute),
            "60min": TimeFrame.Hour,
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
    batch_size: int = 25

    def _path(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        timeframe: str,
    ) -> Path:
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
        return self.cache_dir / f"bars_{digest}.csv"

    def fetch(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        timeframe: str = "1Day",
    ) -> pd.DataFrame:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._path(symbols, start, end, timeframe)
        if path.exists():
            return normalize_bars(pd.read_csv(path))
        if self.batch_size > 0 and len(symbols) > self.batch_size:
            frames: list[pd.DataFrame] = []
            for offset in range(0, len(symbols), self.batch_size):
                batch = symbols[offset : offset + self.batch_size]
                batch_path = self._path(batch, start, end, timeframe)
                if batch_path.exists():
                    frame = normalize_bars(pd.read_csv(batch_path))
                else:
                    frame = self.source.fetch(batch, start, end, timeframe)
                    if not frame.empty:
                        frame.to_csv(batch_path, index=False)
                if not frame.empty:
                    frames.append(frame)
            bars = (
                normalize_bars(pd.concat(frames, ignore_index=True))
                if frames
                else pd.DataFrame(columns=BAR_COLUMNS)
            )
        else:
            bars = self.source.fetch(symbols, start, end, timeframe)
        if not bars.empty:
            bars.to_csv(path, index=False)
        return bars


def default_date_range(lookback_days: int = 730) -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc)
    return end - timedelta(days=lookback_days), end
