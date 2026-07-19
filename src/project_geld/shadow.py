from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import pandas as pd

from project_geld.credentials import load_alpaca_credentials


EVENT_COLUMNS = [
    "observed_at", "signal_timestamp", "symbol", "action", "reason",
    "target_weight", "reference_price", "bid", "ask", "spread_bps",
    "limit_price", "fill_price", "quantity", "notional", "shortable",
    "easy_to_borrow", "borrow_status", "pnl", "return",
]


@dataclass(frozen=True)
class ShadowQuote:
    bid: float
    ask: float
    timestamp: pd.Timestamp


@dataclass(frozen=True)
class ShadowAvailability:
    shortable: bool
    easy_to_borrow: bool
    borrow_status: str


class AlpacaShadowMarket:
    """Read-only quote and asset metadata adapter; it has no order method."""

    def __init__(self, credential_profile: str, feed: str = "iex") -> None:
        api_key, secret_key = load_alpaca_credentials(credential_profile)
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.trading.client import TradingClient

        self.data_client = StockHistoricalDataClient(api_key, secret_key)
        self.trading_client = TradingClient(api_key, secret_key, paper=True)
        self.feed = feed

    def quotes(self, symbols: list[str]) -> dict[str, ShadowQuote]:
        if not symbols:
            return {}
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockLatestQuoteRequest

        request = StockLatestQuoteRequest(
            symbol_or_symbols=symbols,
            feed={"iex": DataFeed.IEX, "sip": DataFeed.SIP}[self.feed],
        )
        raw = self.data_client.get_stock_latest_quote(request)
        return {
            symbol: ShadowQuote(
                bid=float(quote.bid_price or 0),
                ask=float(quote.ask_price or 0),
                timestamp=pd.Timestamp(quote.timestamp),
            )
            for symbol, quote in raw.items()
        }

    def availability(self, symbols: list[str]) -> dict[str, ShadowAvailability]:
        result = {}
        for symbol in symbols:
            asset = self.trading_client.get_asset(symbol)
            shortable = bool(asset.shortable)
            easy = bool(asset.easy_to_borrow)
            status = "easy_to_borrow" if easy else ("hard_to_borrow" if shortable else "not_shortable")
            result[symbol] = ShadowAvailability(shortable, easy, status)
        return result


def run_shadow_cycle(
    targets: pd.DataFrame,
    reference_prices: dict[str, float],
    quotes: dict[str, ShadowQuote],
    availability: dict[str, ShadowAvailability],
    state_file: str | Path,
    events_file: str | Path,
    capital: float = 100_000.0,
    limit_offset_bps: float = 2.0,
    observed_at: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Execute the prior bar's targets against current quotes, without orders."""
    state_path = Path(state_file)
    event_path = Path(events_file)
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    latest_time = pd.Timestamp(targets["timestamp"].max())
    if state.get("last_bar") == latest_time.isoformat():
        return pd.DataFrame(columns=EVENT_COLUMNS)
    now = pd.Timestamp.now(tz="UTC") if observed_at is None else pd.Timestamp(observed_at)
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    positions = dict(state.get("positions", {}))
    pending = dict(state.get("pending_targets", {}))
    pending_signal = state.get("pending_signal")
    events: list[dict] = []
    offset = limit_offset_bps / 10_000

    symbols = sorted(set(positions) | set(pending))
    for symbol in symbols:
        target = float(pending.get(symbol, 0.0))
        quote = quotes.get(symbol)
        availability_row = availability.get(
            symbol, ShadowAvailability(False, False, "unknown")
        )
        reference = float(reference_prices.get(symbol, 0.0))
        spread_bps = (
            (quote.ask - quote.bid) / ((quote.ask + quote.bid) / 2) * 10_000
            if quote and quote.bid > 0 and quote.ask > quote.bid
            else float("nan")
        )
        base = {
            "observed_at": now.isoformat(), "signal_timestamp": pending_signal,
            "symbol": symbol, "target_weight": target,
            "reference_price": reference,
            "bid": quote.bid if quote else 0.0, "ask": quote.ask if quote else 0.0,
            "spread_bps": spread_bps, "shortable": availability_row.shortable,
            "easy_to_borrow": availability_row.easy_to_borrow,
            "borrow_status": availability_row.borrow_status,
            "pnl": 0.0, "return": 0.0,
        }
        if symbol in positions and target >= 0:
            position = positions[symbol]
            limit_price = reference * (1 + offset)
            if quote and quote.ask > 0 and limit_price >= quote.ask:
                fill = quote.ask
                pnl = (float(position["fill_price"]) - fill) * float(position["quantity"])
                events.append({**base, "action": "exit", "reason": "marketable",
                    "limit_price": limit_price, "fill_price": fill,
                    "quantity": position["quantity"], "notional": fill * position["quantity"],
                    "pnl": pnl, "return": float(position["fill_price"]) / fill - 1})
                positions.pop(symbol)
            else:
                events.append({**base, "action": "missed_exit", "reason": "limit_not_marketable",
                    "limit_price": limit_price, "fill_price": 0.0,
                    "quantity": position["quantity"], "notional": 0.0})
        elif symbol not in positions and target < 0:
            limit_price = reference * (1 - offset)
            allowed = availability_row.shortable and availability_row.easy_to_borrow
            marketable = quote and quote.bid > 0 and limit_price <= quote.bid
            if allowed and marketable:
                fill = quote.bid
                quantity = abs(target) * capital / fill
                positions[symbol] = {"fill_price": fill, "quantity": quantity,
                    "entered_at": now.isoformat(), "signal_timestamp": pending_signal}
                events.append({**base, "action": "entry", "reason": "marketable_etb",
                    "limit_price": limit_price, "fill_price": fill,
                    "quantity": quantity, "notional": fill * quantity})
            else:
                reason = "not_easy_to_borrow" if not allowed else "limit_not_marketable"
                events.append({**base, "action": "blocked_entry", "reason": reason,
                    "limit_price": limit_price, "fill_price": 0.0,
                    "quantity": 0.0, "notional": 0.0})

    latest = targets[targets["timestamp"].eq(latest_time)]
    next_pending = {
        str(row.symbol): float(row.target_weight)
        for row in latest.itertuples()
        if float(row.target_weight) < 0
    }
    state = {"last_bar": latest_time.isoformat(), "pending_signal": latest_time.isoformat(),
        "pending_targets": next_pending, "positions": positions}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    frame = pd.DataFrame(events, columns=EVENT_COLUMNS)
    if len(frame):
        event_path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(event_path, mode="a", header=not event_path.exists(), index=False)
    return frame
