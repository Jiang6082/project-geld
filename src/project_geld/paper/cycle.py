"""One paper planning/submission cycle."""

from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
import os
import time
from typing import Callable

import pandas as pd

from project_geld.config import PaperConfig, RiskConfig
from project_geld.logs import get_logger
from project_geld.models import OrderIntent, PaperCycleResult
from project_geld.paper.account import AccountSnapshot, PaperBrokerProtocol, ShortAvailability
from project_geld.paper.planning import (
    RISK_REDUCING_REASONS,
    _floor_quantity,
    build_rebalance_orders,
)
from project_geld.strategies.base import Strategy

log = get_logger(__name__)

TERMINAL_ORDER_STATUSES = frozenset(
    {"filled", "canceled", "cancelled", "expired", "rejected", "done_for_day"}
)


def align_rebalance_to_latest_bar(strategy: Strategy) -> Strategy:
    """Return the strategy with its rebalance schedule anchored at the latest bar.

    Paper planning fetches a rolling lookback window whose first bar moves every
    day. Strategies that count rebalance bars from the first bar would otherwise
    submit a selection made up to ``rebalance_every - 1`` sessions earlier, on a
    phase that drifts with the window. Anchoring at the latest bar makes every
    paper rebalance a fresh selection at the decision bar, which is what the
    backtest models. Strategies without a ``rebalance_anchor`` field are returned
    unchanged.
    """
    if is_dataclass(strategy) and any(
        item.name == "rebalance_anchor" for item in fields(strategy)
    ):
        if getattr(strategy, "rebalance_anchor") != "end":
            return replace(strategy, rebalance_anchor="end")
    return strategy


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
    disable_symbols: set[str] | None = None,
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
    strategy = align_rebalance_to_latest_bar(strategy)
    targets = strategy.generate_targets(strategy_bars)
    latest_time = targets["timestamp"].max()
    latest_targets = targets[
        targets["timestamp"].eq(latest_time) & targets["symbol"].isin(tradables)
    ].copy()
    # Kill-switch / risk override: force these symbols flat this cycle.
    if disable_symbols:
        disabled = {symbol.upper() for symbol in disable_symbols}
        latest_targets.loc[
            latest_targets["symbol"].astype(str).str.upper().isin(disabled),
            "target_weight",
        ] = 0.0
    latest_prices = (
        bars.sort_values("timestamp")
        .groupby("symbol", as_index=False)
        .tail(1)
        .set_index("symbol")["close"]
        .astype(float)
        .to_dict()
    )
    snapshot = snapshot or broker.snapshot(universe)
    short_symbols = sorted(
        latest_targets.loc[
            latest_targets["target_weight"].lt(0), "symbol"
        ].astype(str).str.upper().unique()
    )
    availability: dict[str, ShortAvailability] = {}
    if short_symbols and paper.allow_short:
        availability = broker.short_availability(short_symbols)
        latest_targets["shortable"] = latest_targets["symbol"].map(
            lambda symbol: getattr(availability.get(str(symbol).upper()), "shortable", None)
        )
        latest_targets["easy_to_borrow"] = latest_targets["symbol"].map(
            lambda symbol: getattr(
                availability.get(str(symbol).upper()), "easy_to_borrow", None
            )
        )
        latest_targets["borrow_status"] = latest_targets["symbol"].map(
            lambda symbol: getattr(
                availability.get(str(symbol).upper()), "borrow_status", None
            )
        )
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
        paper.market_exit_orders,
        paper.allow_short,
        paper.require_easy_to_borrow,
        availability,
    )
    if submit:
        rows = submit_in_phases(
            broker,
            intents,
            universe,
            risk,
            paper,
            equity=snapshot.equity,
        )
    else:
        rows = [{**intent.__dict__, "status": "planned", "order_id": None} for intent in intents]
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


def _submit_one(broker: PaperBrokerProtocol, intent: OrderIntent, phase: int) -> dict:
    response = broker.submit(intent)
    return {
        **intent.__dict__,
        "status": str(getattr(response.status, "value", response.status)),
        "order_id": str(response.id),
        "submission_phase": phase,
    }


def wait_for_terminal(
    broker: PaperBrokerProtocol,
    order_ids: list[str],
    timeout_seconds: float,
    poll_seconds: float = 1.0,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, str]:
    """Poll order statuses until all are terminal or the timeout passes.

    Brokers with ``open_order_ids`` are polled with one request per interval
    (rate-limit friendly); final statuses are then read once per order.
    """
    statuses = {order_id: "unknown" for order_id in order_ids}
    deadline = monotonic() + timeout_seconds
    if hasattr(broker, "open_order_ids"):
        while True:
            try:
                still_open = set(order_ids) & broker.open_order_ids()
            except Exception as error:
                log.warning("Open-order read failed: %s", error)
                still_open = set(order_ids)
            if not still_open or monotonic() >= deadline:
                break
            sleep(max(poll_seconds, 2.0))
        for order_id in order_ids:
            try:
                statuses[order_id] = broker.order_status(order_id)
            except Exception as error:
                log.warning("Order status read failed for %s: %s", order_id, error)
        return statuses
    while True:
        for order_id in order_ids:
            if statuses[order_id] not in TERMINAL_ORDER_STATUSES:
                try:
                    statuses[order_id] = broker.order_status(order_id)
                except Exception as error:  # a status read must not abort submission
                    log.warning("Order status read failed for %s: %s", order_id, error)
        if all(status in TERMINAL_ORDER_STATUSES for status in statuses.values()):
            return statuses
        if monotonic() >= deadline:
            return statuses
        sleep(poll_seconds)


def submit_in_phases(
    broker: PaperBrokerProtocol,
    intents: list[OrderIntent],
    universe: list[str],
    risk: RiskConfig,
    paper: PaperConfig,
    equity: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict]:
    """Send sells and risk-reducing orders, wait for them, then send buys.

    The planned buys assume the proceeds of closing sales. After the first phase
    settles (or ``paper.sell_fill_timeout_seconds`` passes), the account is read
    again and new buys are trimmed to the cash actually available, so a sale that
    has not filled can no longer push buys past the account's buying power.
    Brokers without ``order_status`` (or a timeout of 0) submit in one pass.
    """
    first = [i for i in intents if i.side == "sell" or i.reason in RISK_REDUCING_REASONS]
    second = [i for i in intents if not (i.side == "sell" or i.reason in RISK_REDUCING_REASONS)]
    rows = [_submit_one(broker, intent, 1) for intent in first]
    can_wait = paper.sell_fill_timeout_seconds > 0 and hasattr(broker, "order_status")
    if not (first and second and can_wait):
        return rows + [_submit_one(broker, intent, 1 if not first else 2) for intent in second]

    statuses = wait_for_terminal(
        broker,
        [row["order_id"] for row in rows],
        paper.sell_fill_timeout_seconds,
        monotonic=monotonic,
        sleep=sleep,
    )
    for row in rows:
        observed = statuses.get(row["order_id"])
        if observed and observed != "unknown":
            row["status"] = observed
    pending = [row["symbol"] for row in rows if row["status"] not in TERMINAL_ORDER_STATUSES]
    if pending:
        log.warning(
            "Sells/covers still open after %.0fs (%s); buys are trimmed to available cash.",
            paper.sell_fill_timeout_seconds,
            ", ".join(pending),
        )
    try:
        refreshed = broker.snapshot(universe)
        base = (
            refreshed.buying_power
            if paper.allow_short and refreshed.buying_power > 0
            else refreshed.cash
        )
        budget = max(base, 0.0) * (1 - paper.cash_buffer_pct)
    except Exception as error:  # fall back to the plan rather than skip buying
        log.warning("Account refresh before buys failed (%s); using planned quantities.", error)
        budget = None
    minimum_trade = max(risk.min_trade_notional, risk.min_trade_pct_equity * equity)
    for intent in second:
        if budget is not None:
            price = intent.limit_price or intent.reference_price
            quantity = _floor_quantity(min(intent.notional, budget) / price)
            if quantity <= 0 or quantity * price < minimum_trade:
                log.info("Skipped %s buy: %.2f available after sells.", intent.symbol, budget)
                continue
            if quantity < intent.quantity:
                log.info(
                    "Trimmed %s buy from %s to %s shares to fit available cash.",
                    intent.symbol, intent.quantity, quantity,
                )
                intent = replace(intent, quantity=quantity, notional=quantity * price)
            budget -= intent.notional
        rows.append(_submit_one(broker, intent, 2))
    return rows
