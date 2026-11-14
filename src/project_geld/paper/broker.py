"""Alpaca adapter that is structurally restricted to paper=True."""

from __future__ import annotations

import time

import pandas as pd

from project_geld.credentials import load_alpaca_credentials
from project_geld.models import OrderIntent
from project_geld.paper.account import AccountSnapshot, ShortAvailability


class AlpacaPaperBroker:
    """Alpaca adapter that is structurally restricted to paper=True."""

    def __init__(self, credential_profile: str = "") -> None:
        api_key, secret_key = load_alpaca_credentials(credential_profile)
        from alpaca.trading.client import TradingClient

        self.client = TradingClient(api_key, secret_key, paper=True)

    def get_clock(self):
        return self.client.get_clock()

    def session_for(self, day) -> tuple[pd.Timestamp, pd.Timestamp] | None:
        """Regular-session open and close (US/Eastern) for ``day``; None if closed.

        Uses Alpaca's market calendar, which includes holidays and early closes.
        """
        from alpaca.trading.requests import GetCalendarRequest

        days = self.client.get_calendar(GetCalendarRequest(start=day, end=day))
        for entry in days or []:
            if pd.Timestamp(entry.date).date() == pd.Timestamp(day).date():
                return (
                    pd.Timestamp(entry.open).tz_localize("America/New_York"),
                    pd.Timestamp(entry.close).tz_localize("America/New_York"),
                )
        return None

    def snapshot(self, universe: list[str]) -> AccountSnapshot:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        account = self.client.get_account()
        wanted = {symbol.upper() for symbol in universe}
        all_positions = self.client.get_all_positions()
        positions = {
            position.symbol: float(position.qty)
            for position in all_positions
            if position.symbol in wanted
        }
        unmanaged = [
            position for position in all_positions if position.symbol not in wanted
        ]
        unmanaged_notional = sum(
            abs(float(position.market_value or 0)) for position in unmanaged
        )
        open_orders = self.client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN)
        )
        return AccountSnapshot(
            equity=float(account.equity),
            last_equity=float(account.last_equity),
            positions=positions,
            open_order_symbols={order.symbol for order in open_orders},
            cash=float(account.cash),
            buying_power=float(account.buying_power),
            shorting_enabled=bool(account.shorting_enabled),
            unmanaged_notional=unmanaged_notional,
            unmanaged_symbols=frozenset(
                str(position.symbol).upper() for position in unmanaged
            ),
        )

    def short_availability(
        self, symbols: list[str]
    ) -> dict[str, ShortAvailability]:
        results: dict[str, ShortAvailability] = {}
        for symbol in sorted({item.upper() for item in symbols}):
            asset = self.client.get_asset(symbol)
            raw_status = getattr(asset, "borrow_status", None)
            borrow_status = str(getattr(raw_status, "value", raw_status) or "unknown")
            normalized = borrow_status.lower().replace("-", "_").replace(" ", "_")
            legacy_easy = bool(getattr(asset, "easy_to_borrow", False))
            easy_to_borrow = legacy_easy or normalized in {
                "easy",
                "easy_to_borrow",
                "etb",
            }
            results[symbol] = ShortAvailability(
                shortable=bool(getattr(asset, "shortable", False)),
                easy_to_borrow=easy_to_borrow,
                borrow_status=borrow_status,
            )
        return results

    def order_activity(self, after: pd.Timestamp) -> pd.DataFrame:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        observed = pd.Timestamp(after)
        if observed.tzinfo is None:
            observed = observed.tz_localize("UTC")
        orders = self.client.get_orders(
            filter=GetOrdersRequest(
                status=QueryOrderStatus.ALL,
                after=observed.to_pydatetime(),
                limit=500,
            )
        )

        def value(item):
            return getattr(item, "value", item)

        rows = []
        for order in orders:
            rows.append(
                {
                    "submitted_at": getattr(order, "submitted_at", None),
                    "filled_at": getattr(order, "filled_at", None),
                    "symbol": str(order.symbol),
                    "side": str(value(order.side)),
                    "quantity": float(order.qty or 0),
                    "filled_quantity": float(order.filled_qty or 0),
                    "status": str(value(order.status)),
                    "filled_average_price": (
                        float(order.filled_avg_price)
                        if order.filled_avg_price is not None
                        else None
                    ),
                    "limit_price": (
                        float(order.limit_price)
                        if order.limit_price is not None
                        else None
                    ),
                    "client_order_id": str(order.client_order_id),
                    "order_id": str(order.id),
                }
            )
        return pd.DataFrame(rows)

    def cancel_stale_orders(
        self,
        symbols: list[str],
        max_age_seconds: int,
        observed_at: pd.Timestamp | None = None,
        wait_timeout_seconds: float = 3.0,
    ) -> pd.DataFrame:
        """Cancel managed open orders old enough to block a fresh decision cycle."""
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        if max_age_seconds <= 0:
            return pd.DataFrame()
        now = (
            pd.Timestamp.now(tz="UTC")
            if observed_at is None
            else pd.Timestamp(observed_at)
        )
        if now.tzinfo is None:
            now = now.tz_localize("UTC")
        wanted = {symbol.upper() for symbol in symbols}
        open_orders = self.client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN)
        )
        rows: list[dict] = []
        cancelled_ids: set[str] = set()
        for order in open_orders:
            submitted_value = getattr(order, "submitted_at", None)
            if submitted_value is None:
                continue
            submitted_at = pd.Timestamp(submitted_value)
            if submitted_at.tzinfo is None:
                submitted_at = submitted_at.tz_localize("UTC")
            age_seconds = max((now - submitted_at).total_seconds(), 0.0)
            if str(order.symbol).upper() not in wanted or age_seconds < max_age_seconds:
                continue
            try:
                self.client.cancel_order_by_id(order.id)
            except Exception as error:
                current = self.client.get_order_by_id(order.id)
                status = str(
                    getattr(
                        getattr(current, "status", "unknown"),
                        "value",
                        getattr(current, "status", "unknown"),
                    )
                ).lower()
                if status in {
                    "canceled",
                    "expired",
                    "filled",
                    "rejected",
                    "done_for_day",
                }:
                    continue
                raise error
            cancelled_ids.add(str(order.id))
            rows.append(
                {
                    "cancel_requested_at": now.isoformat(),
                    "submitted_at": submitted_at.isoformat(),
                    "symbol": str(order.symbol),
                    "side": str(getattr(order.side, "value", order.side)),
                    "quantity": float(order.qty or 0),
                    "filled_quantity": float(order.filled_qty or 0),
                    "age_seconds": age_seconds,
                    "client_order_id": str(order.client_order_id),
                    "order_id": str(order.id),
                }
            )

        deadline = time.monotonic() + max(wait_timeout_seconds, 0.0)
        while cancelled_ids and time.monotonic() < deadline:
            remaining = self.client.get_orders(
                filter=GetOrdersRequest(status=QueryOrderStatus.OPEN)
            )
            open_ids = {str(order.id) for order in remaining}
            if cancelled_ids.isdisjoint(open_ids):
                break
            time.sleep(0.25)
        return pd.DataFrame(rows)

    def open_order_ids(self) -> set[str]:
        """IDs of every open order in the account (one request)."""
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        orders = self.client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
        return {str(order.id) for order in orders}

    def order_status(self, order_id: str) -> str:
        """Lower-case Alpaca status of one order (``filled``, ``new``, ...)."""
        order = self.client.get_order_by_id(order_id)
        status = getattr(order, "status", "unknown")
        return str(getattr(status, "value", status)).lower()

    def submit(self, intent: OrderIntent):
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

        common = {
            "symbol": intent.symbol,
            "qty": intent.quantity,
            "side": OrderSide.BUY if intent.side == "buy" else OrderSide.SELL,
            "time_in_force": TimeInForce.DAY,
            "client_order_id": intent.client_order_id,
        }
        request = (
            LimitOrderRequest(**common, limit_price=intent.limit_price)
            if intent.limit_price is not None
            else MarketOrderRequest(**common)
        )
        try:
            return self.client.submit_order(order_data=request)
        except Exception as error:
            if "client_order_id must be unique" not in str(error).lower():
                raise
            try:
                return self.client.get_order_by_client_id(intent.client_order_id)
            except Exception:
                raise error
