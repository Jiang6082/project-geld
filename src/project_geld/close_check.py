from __future__ import annotations

import pandas as pd

from project_geld.data import normalize_bars
from project_geld.paper import AccountSnapshot


RECONCILIATION_COLUMNS = [
    "symbol",
    "price",
    "quantity",
    "market_value",
    "current_weight",
    "target_weight",
    "weight_drift",
    "drift_notional",
    "unexpected_position",
    "missing_price",
]


def bars_available_at_close(
    bars: pd.DataFrame,
    observed_at: pd.Timestamp,
    market_is_open: bool,
    timezone: str = "America/New_York",
) -> pd.DataFrame:
    """Return only daily sessions safe to use in a read-only close check."""
    frame = normalize_bars(bars)
    if frame.empty:
        return frame
    observed = pd.Timestamp(observed_at)
    if observed.tzinfo is None:
        observed = observed.tz_localize("UTC")
    local_date = observed.tz_convert(timezone).date()
    session_dates = frame["timestamp"].dt.tz_convert(timezone).dt.date
    allowed = session_dates.lt(local_date) if market_is_open else session_dates.le(local_date)
    return normalize_bars(frame[allowed])


def build_position_reconciliation(
    latest_targets: pd.DataFrame,
    prices: dict[str, float],
    snapshot: AccountSnapshot,
) -> pd.DataFrame:
    targets = (
        latest_targets.drop_duplicates("symbol", keep="last")
        .set_index("symbol")["target_weight"]
        .astype(float)
    )
    symbols = sorted(set(targets.index) | set(snapshot.positions))
    rows: list[dict] = []
    for symbol in symbols:
        price = float(prices.get(symbol, 0.0))
        quantity = float(snapshot.positions.get(symbol, 0.0))
        target_weight = float(targets.get(symbol, 0.0))
        market_value = quantity * price if price > 0 else 0.0
        current_weight = (
            market_value / snapshot.equity if snapshot.equity > 0 else 0.0
        )
        drift = target_weight - current_weight
        rows.append(
            {
                "symbol": symbol,
                "price": price,
                "quantity": quantity,
                "market_value": market_value,
                "current_weight": current_weight,
                "target_weight": target_weight,
                "weight_drift": drift,
                "drift_notional": drift * snapshot.equity,
                "unexpected_position": symbol not in targets.index
                and abs(quantity) > 1e-12,
                "missing_price": price <= 0 and abs(quantity) > 1e-12,
            }
        )
    return pd.DataFrame(rows, columns=RECONCILIATION_COLUMNS)
