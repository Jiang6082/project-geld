from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
from typing import Protocol

import pandas as pd
from project_geld.config import PaperConfig, RiskConfig
from project_geld.credentials import load_alpaca_credentials
from project_geld.models import OrderIntent, PaperCycleResult
from project_geld.strategies.base import Strategy


@dataclass(frozen=True)
class AccountSnapshot:
    equity: float
    last_equity: float
    positions: dict[str, float]
    open_order_symbols: set[str]
    cash: float = 0.0
    unmanaged_notional: float = 0.0


class PaperBrokerProtocol(Protocol):
    def get_clock(self): ...
    def snapshot(self, universe: list[str]) -> AccountSnapshot: ...
    def submit(self, intent: OrderIntent): ...


def append_performance_snapshot(
    snapshot: AccountSnapshot,
    path: str | Path,
    timestamp: pd.Timestamp | None = None,
) -> pd.Series:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    observed_at = pd.Timestamp.now(tz="UTC") if timestamp is None else pd.Timestamp(timestamp)
    if observed_at.tzinfo is None:
        observed_at = observed_at.tz_localize("UTC")
    session_date = observed_at.date().isoformat()
    history = pd.read_csv(output) if output.exists() else pd.DataFrame()
    if len(history):
        baseline_equity = float(history.iloc[0]["baseline_equity"])
        history = history[history["session_date"] != session_date]
    else:
        baseline_equity = snapshot.equity
    row = {
        "observed_at": observed_at.isoformat(),
        "session_date": session_date,
        "equity": snapshot.equity,
        "last_equity": snapshot.last_equity,
        "cash": snapshot.cash,
        "daily_return": (
            snapshot.equity / snapshot.last_equity - 1
            if snapshot.last_equity > 0
            else 0.0
        ),
        "baseline_equity": baseline_equity,
        "cumulative_return": (
            snapshot.equity / baseline_equity - 1 if baseline_equity > 0 else 0.0
        ),
        "managed_positions": json.dumps(snapshot.positions, sort_keys=True),
        "open_order_symbols": json.dumps(sorted(snapshot.open_order_symbols)),
        "unmanaged_notional": snapshot.unmanaged_notional,
    }
    history = pd.concat([history, pd.DataFrame([row])], ignore_index=True)
    history = history.sort_values("observed_at")
    history.to_csv(output, index=False)
    return pd.Series(row)


def paper_rebalance_due(
    bars: pd.DataFrame,
    paper: PaperConfig,
    strategy_name: str,
) -> tuple[bool, int, pd.Timestamp]:
    latest_session = pd.Timestamp(bars["timestamp"].max())
    if not paper.state_file.exists():
        return True, paper.rebalance_every_sessions, latest_session
    state = json.loads(paper.state_file.read_text(encoding="utf-8"))
    if state.get("strategy") != strategy_name:
        return True, paper.rebalance_every_sessions, latest_session
    last_session = pd.Timestamp(state["last_rebalance_session"])
    sessions = pd.Index(pd.to_datetime(bars["timestamp"], utc=True).unique())
    elapsed = int((sessions > last_session).sum())
    return elapsed >= paper.rebalance_every_sessions, elapsed, latest_session


def mark_paper_rebalance(
    paper: PaperConfig, strategy_name: str, session: pd.Timestamp
) -> None:
    paper.state_file.parent.mkdir(parents=True, exist_ok=True)
    paper.state_file.write_text(
        json.dumps(
            {
                "strategy": strategy_name,
                "last_rebalance_session": pd.Timestamp(session).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _floor_quantity(quantity: float) -> float:
    return math.floor(max(quantity, 0.0) * 1_000_000) / 1_000_000


def _safe_targets(
    latest: pd.DataFrame, risk: RiskConfig, available_gross: float
) -> pd.Series:
    weights = latest.set_index("symbol")["target_weight"].clip(lower=0.0)
    caps = pd.Series(
        {
            symbol: risk.symbol_position_weight_limits.get(
                str(symbol).upper(), risk.max_position_weight
            )
            for symbol in weights.index
        }
    )
    weights = weights.where(weights.le(caps), caps)
    gross = float(weights.sum())
    gross_limit = max(min(available_gross, risk.max_gross_exposure), 0.0)
    if gross > gross_limit and gross > 0:
        weights *= gross_limit / gross
    return weights


def build_rebalance_orders(
    latest_targets: pd.DataFrame,
    prices: dict[str, float],
    snapshot: AccountSnapshot,
    risk: RiskConfig,
    prefix: str,
    strategy_name: str,
    as_of: pd.Timestamp,
    cash_buffer_pct: float = 0.0,
    execution_style: str = "market",
    limit_offset_bps: float = 0.0,
) -> list[OrderIntent]:
    if snapshot.equity <= 0:
        raise ValueError("Paper account equity must be positive.")
    if snapshot.last_equity > 0:
        daily_return = snapshot.equity / snapshot.last_equity - 1
        if daily_return <= -risk.max_daily_loss_pct:
            raise RuntimeError(
                f"Daily-loss guard triggered at {daily_return:.2%}; no orders planned."
            )
    if any(quantity < 0 for quantity in snapshot.positions.values()):
        raise RuntimeError("A managed symbol has a short position; paper planning stopped.")
    unmanaged_weight = snapshot.unmanaged_notional / snapshot.equity
    targets = _safe_targets(
        latest_targets,
        risk,
        risk.max_gross_exposure - unmanaged_weight - cash_buffer_pct,
    )
    orders: list[OrderIntent] = []
    all_symbols = sorted(set(targets.index) | set(snapshot.positions))
    for symbol in all_symbols:
        if symbol in snapshot.open_order_symbols:
            continue
        price = float(prices.get(symbol, 0.0))
        if price <= 0:
            continue
        current_quantity = max(float(snapshot.positions.get(symbol, 0.0)), 0.0)
        target_weight = float(targets.get(symbol, 0.0))
        desired_quantity = target_weight * snapshot.equity / price
        delta = desired_quantity - current_quantity
        minimum_trade = max(
            risk.min_trade_notional,
            risk.min_trade_pct_equity * snapshot.equity,
        )
        if abs(delta * price) < minimum_trade:
            continue
        side = "buy" if delta > 0 else "sell"
        quantity = abs(delta)
        if side == "sell":
            quantity = min(quantity, current_quantity)
        limit_price = None
        if execution_style == "marketable_limit":
            offset = limit_offset_bps / 10_000
            raw_limit = price * (1 + offset if side == "buy" else 1 - offset)
            limit_price = round(raw_limit, 2 if raw_limit >= 1 else 4)
        order_price = limit_price or price
        notional_limit = risk.symbol_order_notional_limits.get(
            symbol, risk.max_order_notional
        )
        pct_limit = risk.symbol_order_pct_equity_limits.get(
            symbol, risk.max_order_pct_equity
        )
        if pct_limit is not None:
            notional_limit = min(notional_limit, pct_limit * snapshot.equity)
        quantity = min(quantity, notional_limit / order_price)
        quantity = _floor_quantity(quantity)
        if quantity <= 0:
            continue
        date_code = pd.Timestamp(as_of).strftime("%Y%m%d")
        client_order_id = (
            f"{prefix[:8]}-{date_code}-{strategy_name[:8]}-{symbol[:8]}-{side[0]}"
        )[:48]
        orders.append(
            OrderIntent(
                symbol=symbol,
                side=side,
                quantity=quantity,
                reference_price=price,
                notional=quantity * order_price,
                target_weight=target_weight,
                client_order_id=client_order_id,
                limit_price=limit_price,
            )
        )
    sell_notional = sum(order.notional for order in orders if order.side == "sell")
    remaining_buying_budget = (
        max(snapshot.cash, 0.0) * (1 - cash_buffer_pct) + sell_notional
    )
    cash_safe_orders: list[OrderIntent] = []
    for order in orders:
        if order.side == "sell":
            cash_safe_orders.append(order)
            continue
        affordable_notional = min(order.notional, remaining_buying_budget)
        affordable_quantity = _floor_quantity(
            affordable_notional / (order.limit_price or order.reference_price)
        )
        minimum_trade = max(
            risk.min_trade_notional,
            risk.min_trade_pct_equity * snapshot.equity,
        )
        if affordable_notional < minimum_trade or affordable_quantity <= 0:
            continue
        cash_safe_orders.append(
            OrderIntent(
                symbol=order.symbol,
                side=order.side,
                quantity=affordable_quantity,
                reference_price=order.reference_price,
                notional=affordable_quantity * (order.limit_price or order.reference_price),
                target_weight=order.target_weight,
                client_order_id=order.client_order_id,
                reason=order.reason,
                limit_price=order.limit_price,
            )
        )
        remaining_buying_budget -= affordable_quantity * (
            order.limit_price or order.reference_price
        )
    return cash_safe_orders


class AlpacaPaperBroker:
    """Alpaca adapter that is structurally restricted to paper=True."""

    def __init__(self, credential_profile: str = "") -> None:
        api_key, secret_key = load_alpaca_credentials(credential_profile)
        from alpaca.trading.client import TradingClient

        self.client = TradingClient(api_key, secret_key, paper=True)

    def get_clock(self):
        return self.client.get_clock()

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
        unmanaged_notional = sum(
            abs(float(position.market_value or 0))
            for position in all_positions
            if position.symbol not in wanted
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
            unmanaged_notional=unmanaged_notional,
        )

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
        return self.client.submit_order(order_data=request)


def run_paper_cycle(
    bars: pd.DataFrame,
    strategy: Strategy,
    broker: PaperBrokerProtocol,
    risk: RiskConfig,
    paper: PaperConfig,
    universe: list[str],
    submit: bool = False,
    snapshot: AccountSnapshot | None = None,
    context_symbols: list[str] | None = None,
    confirmation_env: str = "PROJECT_GELD_CONFIRM_PAPER",
) -> PaperCycleResult:
    if submit and not paper.enabled:
        raise RuntimeError("Set [paper] enabled = true before submitting paper orders.")
    if submit and os.getenv(confirmation_env) != "YES":
        raise RuntimeError(f"Set {confirmation_env}=YES before submitting paper orders.")
    clock = broker.get_clock()
    if submit and not bool(clock.is_open):
        raise RuntimeError("The US equity market is closed; no paper orders submitted.")

    tradables = {symbol.upper() for symbol in universe}
    context = {symbol.upper() for symbol in (context_symbols or [])}
    strategy_bars = bars[bars["symbol"].isin(tradables | context)]
    targets = strategy.generate_targets(strategy_bars)
    latest_time = targets["timestamp"].max()
    latest_targets = targets[
        targets["timestamp"].eq(latest_time) & targets["symbol"].isin(tradables)
    ].copy()
    latest_prices = (
        bars.sort_values("timestamp")
        .groupby("symbol", as_index=False)
        .tail(1)
        .set_index("symbol")["close"]
        .astype(float)
        .to_dict()
    )
    snapshot = snapshot or broker.snapshot(universe)
    intents = build_rebalance_orders(
        latest_targets,
        latest_prices,
        snapshot,
        risk,
        paper.client_order_prefix,
        strategy.name,
        pd.Timestamp(latest_time),
        paper.cash_buffer_pct,
        paper.execution_style,
        paper.limit_offset_bps,
    )
    rows: list[dict] = []
    for intent in intents:
        row = intent.__dict__.copy()
        if submit:
            response = broker.submit(intent)
            row["status"] = str(getattr(response.status, "value", response.status))
            row["order_id"] = str(response.id)
        else:
            row["status"] = "planned"
            row["order_id"] = None
        rows.append(row)
    message = (
        f"Submitted {len(rows)} Alpaca paper order(s)."
        if submit
        else f"Planned {len(rows)} order(s); nothing submitted."
    )
    return PaperCycleResult(
        orders=pd.DataFrame(rows),
        targets=latest_targets,
        submitted=submit,
        message=message,
    )
