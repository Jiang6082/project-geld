"""Rebalance cadence state and order planning from target weights."""

from __future__ import annotations

import json
import math

import pandas as pd

from project_geld.atomicio import atomic_write_text
from project_geld.config import PaperConfig, RiskConfig
from project_geld.models import OrderIntent
from project_geld.paper.account import AccountSnapshot, ShortAvailability


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
    atomic_write_text(
        paper.state_file,
        json.dumps(
            {
                "strategy": strategy_name,
                "last_rebalance_session": pd.Timestamp(session).isoformat(),
            },
            indent=2,
        ),
    )


def strategy_order_token(strategy_name: str) -> str:
    """Eight-character strategy token for client order IDs.

    Plain truncation mapped ``intra_v13`` and ``intra_v15`` (and every other
    ``intra_v1x``) to the same ``intra_v1``. Underscores are dropped first, and
    longer names keep their first and last four characters so version suffixes
    survive: ``intra_v15`` -> ``intrav15``, ``momentum_v3`` -> ``momeumv3``.
    """
    compact = "".join(character for character in strategy_name if character.isalnum())
    return compact if len(compact) <= 8 else compact[:4] + compact[-4:]


def _floor_quantity(quantity: float) -> float:
    return math.floor(max(quantity, 0.0) * 1_000_000) / 1_000_000


def _safe_targets(
    latest: pd.DataFrame,
    risk: RiskConfig,
    available_gross: float,
    allow_short: bool = False,
) -> pd.Series:
    raw_weights = latest.set_index("symbol")["target_weight"]
    if raw_weights.lt(0).any() and not allow_short:
        raise RuntimeError(
            "Paper short targets require [paper] allow_short = true; no orders planned."
        )
    weights = raw_weights.copy() if allow_short else raw_weights.clip(lower=0.0)
    caps = pd.Series(
        {
            symbol: risk.symbol_position_weight_limits.get(
                str(symbol).upper(), risk.max_position_weight
            )
            for symbol in weights.index
        }
    )
    weights = weights.clip(lower=-caps, upper=caps)
    gross = float(weights.abs().sum())
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
    market_exit_orders: bool = False,
    allow_short: bool = False,
    require_easy_to_borrow: bool = True,
    short_availability: dict[str, ShortAvailability] | None = None,
) -> list[OrderIntent]:
    if snapshot.equity <= 0:
        raise ValueError("Paper account equity must be positive.")
    daily_loss_guard = False
    if snapshot.last_equity > 0:
        daily_return = snapshot.equity / snapshot.last_equity - 1
        if daily_return <= -risk.max_daily_loss_pct:
            daily_loss_guard = True
    unmanaged_weight = snapshot.unmanaged_notional / snapshot.equity
    targets = _safe_targets(
        latest_targets,
        risk,
        risk.max_gross_exposure - unmanaged_weight - cash_buffer_pct,
        allow_short,
    )
    if daily_loss_guard:
        targets[:] = 0.0

    availability = {
        symbol.upper(): status
        for symbol, status in (short_availability or {}).items()
    }
    orders: list[OrderIntent] = []
    all_symbols = sorted(set(targets.index) | set(snapshot.positions))
    for symbol in all_symbols:
        if symbol in snapshot.open_order_symbols:
            continue
        price = float(prices.get(symbol, 0.0))
        if price <= 0:
            continue
        current_quantity = float(snapshot.positions.get(symbol, 0.0))
        target_weight = float(targets.get(symbol, 0.0))
        desired_quantity = target_weight * snapshot.equity / price
        if desired_quantity < 0:
            desired_quantity = -float(math.floor(abs(desired_quantity)))

        reason = "daily_loss_exit" if daily_loss_guard else "rebalance"
        if current_quantity * desired_quantity < 0:
            desired_quantity = 0.0
            reason = "flatten_before_reverse"

        increasing_short = desired_quantity < min(current_quantity, 0.0)
        if increasing_short:
            status = availability.get(symbol.upper())
            can_short = (
                snapshot.shorting_enabled
                and snapshot.equity >= 2_000
                and bool(status and status.shortable)
            )
            if require_easy_to_borrow:
                can_short = can_short and bool(status and status.easy_to_borrow)
            if not can_short:
                desired_quantity = current_quantity

        delta = desired_quantity - current_quantity
        if abs(delta) < 1e-12:
            continue
        reducing_risk = (
            abs(desired_quantity) < abs(current_quantity)
            and current_quantity * desired_quantity >= 0
        )
        minimum_trade = max(
            risk.min_trade_notional,
            risk.min_trade_pct_equity * snapshot.equity,
        )
        mandatory_exit = reducing_risk and abs(desired_quantity) < 1e-12
        if abs(delta * price) < minimum_trade and not mandatory_exit:
            continue
        side = "buy" if delta > 0 else "sell"
        quantity = abs(delta)
        if current_quantity < 0 or desired_quantity < 0:
            quantity = float(math.floor(quantity))
        if reason == "rebalance":
            if current_quantity < 0 and delta > 0:
                reason = "cover_short"
            elif desired_quantity < current_quantity and desired_quantity < 0:
                reason = "open_short"
            elif current_quantity > 0 and delta < 0:
                reason = "close_long"
            elif delta > 0:
                reason = "open_long"
        limit_price = None
        if execution_style == "marketable_limit" and not (
            market_exit_orders and mandatory_exit
        ):
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
        if not reducing_risk:
            quantity = min(quantity, notional_limit / order_price)
        quantity = (
            float(math.floor(quantity))
            if current_quantity < 0 or desired_quantity < 0
            else _floor_quantity(quantity)
        )
        if quantity <= 0:
            continue
        decision_code = pd.Timestamp(as_of).strftime("%Y%m%d%H%M")
        client_order_id = (
            f"{prefix[:8]}-{decision_code}-{strategy_order_token(strategy_name)}"
            f"-{symbol[:8]}-{side[0]}"
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
                reason=reason,
                limit_price=limit_price,
            )
        )
    closing_sale_notional = sum(
        order.notional
        for order in orders
        if order.side == "sell" and order.reason != "open_short"
    )
    base_buying_power = (
        snapshot.buying_power
        if allow_short and snapshot.buying_power > 0
        else snapshot.cash
    )
    remaining_buying_budget = (
        max(base_buying_power, 0.0) * (1 - cash_buffer_pct)
        + closing_sale_notional
    )
    cash_safe_orders: list[OrderIntent] = []
    # Budget in submission order, so risk-reducing buys (covers) are funded
    # before new longs rather than after them.
    for order in sequence_orders(orders):
        if order.side == "sell":
            cash_safe_orders.append(order)
            continue
        if order.reason in {"cover_short", "daily_loss_exit", "flatten_before_reverse"}:
            cash_safe_orders.append(order)
            remaining_buying_budget = max(remaining_buying_budget - order.notional, 0.0)
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
    return sequence_orders(cash_safe_orders)


RISK_REDUCING_REASONS = frozenset(
    {"close_long", "cover_short", "flatten_before_reverse", "daily_loss_exit"}
)


def sequence_orders(orders: list[OrderIntent]) -> list[OrderIntent]:
    """Submission order: risk-reducing orders first, and sells before buys.

    The buying budget already counts proceeds from closing sales, so those sales
    must reach the broker before the buys that rely on them. The sort is stable,
    so symbols stay alphabetical within each group.
    """
    return sorted(
        orders,
        key=lambda order: (
            0 if order.reason in RISK_REDUCING_REASONS else 1,
            0 if order.side == "sell" else 1,
        ),
    )
