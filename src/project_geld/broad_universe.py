from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

import pandas as pd


ALLOWED_EXCHANGES = {"NYSE", "NASDAQ", "AMEX"}
EXCLUDED_NAME = re.compile(
    r"\b(ETF|ETN|EXCHANGE[- ]TRADED|FUND|PORTFOLIO|WARRANT|RIGHTS?|UNITS?|"
    r"PREFERRED|PFD|DEPOSITARY|ADS|ADR|NOTES?|BONDS?|DEBENTURES?|"
    r"BENEFICIAL INTEREST|INCOME SHARES|CONTRA)\b",
    re.IGNORECASE,
)
FUND_BRANDS = re.compile(
    r"\b(SPDR|ISHARES|PROSHARES|DIREXION|VANGUARD|GLOBAL X|"
    r"WISDOMTREE|INNOVATOR ETF|INVESCO ETF|FIRST TRUST ETF)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BroadUniverseRules:
    top_n: int = 500
    minimum_price: float = 5.0
    minimum_history_sessions: int = 252
    dollar_volume_window: int = 60
    minimum_dollar_volume: float = 20_000_000.0


def _enum_value(value: object) -> str:
    return str(value).split(".")[-1].upper()


def classify_asset(asset: object) -> tuple[bool, str]:
    symbol = str(getattr(asset, "symbol", "")).upper().strip()
    name = str(getattr(asset, "name", "") or "").strip()
    exchange = _enum_value(getattr(asset, "exchange", ""))
    if exchange not in ALLOWED_EXCHANGES:
        return False, "exchange"
    if not symbol or len(symbol) > 6:
        return False, "symbol_length"
    if any(character.isdigit() for character in symbol):
        return False, "numeric_symbol"
    if any(character in symbol for character in ["/", "^", "-", " "]):
        return False, "security_suffix"
    if re.search(r"\.(U|W|WS|R)$", symbol):
        return False, "security_suffix"
    if EXCLUDED_NAME.search(name) or FUND_BRANDS.search(name):
        return False, "security_type"
    if " TRUST" in name.upper() and not re.search(
        r"COMMON|ORDINARY|CLASS [ABC]", name, re.IGNORECASE
    ):
        return False, "trust_without_common_shares"
    return True, "candidate"


def asset_master_frame(assets: Iterable[object]) -> pd.DataFrame:
    rows = []
    for asset in assets:
        included, reason = classify_asset(asset)
        rows.append(
            {
                "symbol": str(getattr(asset, "symbol", "")).upper(),
                "name": str(getattr(asset, "name", "") or ""),
                "exchange": _enum_value(getattr(asset, "exchange", "")),
                "status": _enum_value(getattr(asset, "status", "")),
                "tradable_now": bool(getattr(asset, "tradable", False)),
                "included": included,
                "classification": reason,
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["active_sort"] = frame["status"].eq("ACTIVE").astype(int)
    frame = frame.sort_values(
        ["symbol", "included", "active_sort"], ascending=[True, False, False]
    ).drop_duplicates("symbol", keep="first")
    return frame.drop(columns="active_sort").reset_index(drop=True)


def monthly_candidate_rows(
    bars: pd.DataFrame,
    month_end_sessions: pd.DatetimeIndex,
    rules: BroadUniverseRules,
) -> pd.DataFrame:
    if bars.empty:
        return pd.DataFrame(
            columns=[
                "timestamp",
                "symbol",
                "close",
                "history_sessions",
                "median_dollar_volume",
            ]
        )
    frame = bars.sort_values(["symbol", "timestamp"]).copy()
    frame["dollar_volume"] = frame["close"] * frame["volume"]
    frame["history_sessions"] = frame.groupby("symbol").cumcount() + 1
    frame["median_dollar_volume"] = frame.groupby("symbol")[
        "dollar_volume"
    ].transform(
        lambda values: values.rolling(
            rules.dollar_volume_window,
            min_periods=rules.dollar_volume_window,
        ).median()
    )
    candidates = frame[frame["timestamp"].isin(month_end_sessions)].copy()
    candidates = candidates[
        candidates["close"].ge(rules.minimum_price)
        & candidates["history_sessions"].ge(rules.minimum_history_sessions)
        & candidates["median_dollar_volume"].ge(rules.minimum_dollar_volume)
    ]
    return candidates[
        [
            "timestamp",
            "symbol",
            "close",
            "history_sessions",
            "median_dollar_volume",
        ]
    ].reset_index(drop=True)


def select_top_liquid(
    candidates: pd.DataFrame, rules: BroadUniverseRules
) -> pd.DataFrame:
    if candidates.empty:
        return candidates.assign(liquidity_rank=pd.Series(dtype=int))
    selected = candidates.sort_values(
        ["timestamp", "median_dollar_volume", "symbol"],
        ascending=[True, False, True],
    ).copy()
    selected["liquidity_rank"] = selected.groupby("timestamp").cumcount() + 1
    return selected[selected["liquidity_rank"].le(rules.top_n)].reset_index(
        drop=True
    )


def membership_periods_from_selections(
    selected: pd.DataFrame,
    month_end_sessions: pd.DatetimeIndex,
    market_sessions: pd.DatetimeIndex,
) -> dict[str, list[list[str | None]]]:
    month_ends = pd.DatetimeIndex(sorted(pd.to_datetime(month_end_sessions, utc=True)))
    sessions = pd.DatetimeIndex(sorted(pd.to_datetime(market_sessions, utc=True)))
    month_position = {timestamp: index for index, timestamp in enumerate(month_ends)}
    periods: dict[str, list[list[str | None]]] = {}
    for symbol, group in selected.groupby("symbol"):
        positions = sorted(
            month_position[timestamp]
            for timestamp in pd.to_datetime(group["timestamp"], utc=True)
            if timestamp in month_position
        )
        if not positions:
            continue
        runs: list[tuple[int, int]] = []
        run_start = positions[0]
        run_end = positions[0]
        for position in positions[1:]:
            if position == run_end + 1:
                run_end = position
            else:
                runs.append((run_start, run_end))
                run_start = run_end = position
        runs.append((run_start, run_end))
        symbol_periods: list[list[str | None]] = []
        for start_position, end_position in runs:
            start = month_ends[start_position]
            if end_position == len(month_ends) - 1:
                end: pd.Timestamp | None = None
            else:
                next_month_end = month_ends[end_position + 1]
                prior_sessions = sessions[sessions < next_month_end]
                end = prior_sessions[-1] if len(prior_sessions) else start
            symbol_periods.append(
                [
                    start.strftime("%Y-%m-%d"),
                    end.strftime("%Y-%m-%d") if end is not None else None,
                ]
            )
        periods[str(symbol)] = symbol_periods
    return periods
