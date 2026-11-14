from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass, replace
import json
import math
import os
from pathlib import Path
import time
from typing import Protocol

import pandas as pd
from project_geld.atomicio import atomic_write_dataframe_csv, atomic_write_text
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
    buying_power: float = 0.0
    shorting_enabled: bool = False
    unmanaged_notional: float = 0.0
    unmanaged_symbols: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ShortAvailability:
    shortable: bool
    easy_to_borrow: bool
    borrow_status: str


class PaperBrokerProtocol(Protocol):
    def get_clock(self): ...
    def snapshot(self, universe: list[str]) -> AccountSnapshot: ...
    def short_availability(
        self, symbols: list[str]
    ) -> dict[str, ShortAvailability]: ...
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
    atomic_write_dataframe_csv(history, output, index=False)
    return pd.Series(row)


def implementation_shortfall(
    planned_orders: pd.DataFrame,
    order_activity: pd.DataFrame,
) -> pd.DataFrame:
    """Per-order implementation shortfall versus the decision reference price.

    Joins planned intents (``reference_price`` set at the decision bar) to the
    realized Alpaca order activity by ``client_order_id``. ``shortfall_bps`` is
    the signed execution cost relative to the reference, positive when the fill
    is worse than the decision price for that side. Unfilled orders are flagged
    so the cost-sensitive daily sleeve can be monitored against the research's
    two-basis-point invalidation threshold.
    """
    columns = [
        "client_order_id",
        "symbol",
        "side",
        "reference_price",
        "limit_price",
        "filled_average_price",
        "planned_quantity",
        "filled_quantity",
        "fill_rate",
        "shortfall_bps",
        "missed",
    ]
    if planned_orders.empty:
        return pd.DataFrame(columns=columns)
    planned = planned_orders.copy()
    planned["client_order_id"] = planned["client_order_id"].astype(str)
    fills = (
        order_activity.copy()
        if len(order_activity)
        else pd.DataFrame(
            columns=["client_order_id", "filled_quantity", "filled_average_price"]
        )
    )
    if len(fills):
        fills["client_order_id"] = fills["client_order_id"].astype(str)
        fills = fills.drop_duplicates("client_order_id", keep="last")
    merged = planned.merge(
        fills[["client_order_id", "filled_quantity", "filled_average_price"]],
        on="client_order_id",
        how="left",
    )
    rows: list[dict] = []
    for _, order in merged.iterrows():
        reference = float(order.get("reference_price", 0.0) or 0.0)
        planned_quantity = float(order.get("quantity", 0.0) or 0.0)
        filled_quantity = float(order.get("filled_quantity", 0.0) or 0.0)
        fill_price = order.get("filled_average_price")
        fill_price = float(fill_price) if pd.notna(fill_price) else float("nan")
        side = str(order.get("side", ""))
        if reference > 0 and filled_quantity > 0 and fill_price == fill_price:
            direction = 1.0 if side == "buy" else -1.0
            shortfall_bps = direction * (fill_price - reference) / reference * 10_000.0
        else:
            shortfall_bps = float("nan")
        rows.append(
            {
                "client_order_id": str(order.get("client_order_id", "")),
                "symbol": str(order.get("symbol", "")),
                "side": side,
                "reference_price": reference,
                "limit_price": order.get("limit_price"),
                "filled_average_price": fill_price,
                "planned_quantity": planned_quantity,
                "filled_quantity": filled_quantity,
                "fill_rate": (
                    filled_quantity / planned_quantity if planned_quantity > 0 else 0.0
                ),
                "shortfall_bps": shortfall_bps,
                "missed": filled_quantity <= 0.0,
            }
        )
    return pd.DataFrame(rows, columns=columns)


SHORTFALL_RESCORED_COLUMNS = [
    "filled_average_price",
    "filled_quantity",
    "fill_rate",
    "shortfall_bps",
    "missed",
]


def refresh_shortfall_history(
    history: pd.DataFrame, order_activity: pd.DataFrame
) -> pd.DataFrame:
    """Re-score recorded orders against fresher broker activity.

    Shortfall is first measured seconds after submission, before a marketable
    limit order may have filled. Rows whose order appears in ``order_activity``
    are re-scored, so late fills replace their provisional ``missed`` status
    instead of being excluded from the kill-switch forever.
    """
    if history.empty or order_activity is None or order_activity.empty:
        return history
    updated = history.reset_index(drop=True).copy()
    planned = updated.drop(
        columns=[column for column in SHORTFALL_RESCORED_COLUMNS if column in updated.columns]
    ).rename(columns={"planned_quantity": "quantity"})
    rescored = implementation_shortfall(planned, order_activity)
    known = set(order_activity["client_order_id"].astype(str))
    mask = updated["client_order_id"].astype(str).isin(known).to_numpy()
    if not mask.any():
        return updated
    for column in SHORTFALL_RESCORED_COLUMNS:
        if column not in updated.columns:
            updated[column] = pd.NA
        updated[column] = updated[column].astype(object)
        updated.loc[mask, column] = rescored.loc[mask, column].to_numpy()
    updated["missed"] = updated["missed"].map(
        lambda value: str(value).strip().lower() in {"true", "1"}
        if not isinstance(value, bool)
        else value
    )
    for column in ["filled_average_price", "filled_quantity", "fill_rate", "shortfall_bps"]:
        updated[column] = pd.to_numeric(updated[column], errors="coerce")
    return updated


def unresolved_shortfall_since(
    history: pd.DataFrame, now: pd.Timestamp, max_age_days: int = 5
) -> pd.Timestamp | None:
    """Earliest recent decision bar with an unfilled or partially filled order."""
    if history.empty or "decision_bar" not in history.columns:
        return None
    decision = pd.to_datetime(history["decision_bar"], utc=True, errors="coerce")
    missed = (
        history["missed"].astype(str).str.lower().eq("true")
        if "missed" in history.columns
        else pd.Series(False, index=history.index)
    )
    fill_rate = (
        pd.to_numeric(history["fill_rate"], errors="coerce").fillna(0.0)
        if "fill_rate" in history.columns
        else pd.Series(0.0, index=history.index)
    )
    open_rows = (missed | fill_rate.lt(1.0)) & decision.ge(
        pd.Timestamp(now) - pd.Timedelta(days=max_age_days)
    )
    return decision[open_rows].min() if open_rows.any() else None


def _rows_since(history: pd.DataFrame, since: pd.Timestamp | None) -> pd.DataFrame:
    if since is None or "decision_bar" not in history.columns:
        return history
    decision = pd.to_datetime(history["decision_bar"], utc=True, errors="coerce")
    return history[decision.gt(pd.Timestamp(since))]


def trailing_shortfall_bps(
    history: pd.DataFrame,
    symbol: str,
    lookback: int = 20,
    since: pd.Timestamp | None = None,
) -> float:
    """Mean implementation shortfall (bps) over recent filled orders for a symbol.

    Returns NaN when there is no filled history yet, so the caller treats an
    empty record as "not breached" rather than disabling a sleeve prematurely.
    Rows decided at or before ``since`` (a manual kill-switch reset) are ignored.
    """
    if history.empty or "shortfall_bps" not in history.columns:
        return float("nan")
    history = _rows_since(history, since)
    rows = history[history["symbol"].astype(str).str.upper() == symbol.upper()]
    if "missed" in rows.columns:
        rows = rows[~rows["missed"].astype(str).str.lower().eq("true")]
    rows = rows.assign(
        shortfall_bps=pd.to_numeric(rows["shortfall_bps"], errors="coerce")
    ).dropna(subset=["shortfall_bps"]).tail(lookback)
    return float(rows["shortfall_bps"].mean()) if len(rows) else float("nan")


def shortfall_kill_switch_active(
    history: pd.DataFrame,
    symbol: str,
    threshold_bps: float = 2.0,
    lookback: int = 20,
    since: pd.Timestamp | None = None,
) -> bool:
    """True when a symbol's trailing implementation shortfall breaches the gate.

    The Intra V15 research invalidates the daily base sleeve above ~2 bps of
    average shortfall per side. This is the stateless test; the paper loop uses
    ``evaluate_kill_switch``, which latches a breach until a manual reset.
    """
    average = trailing_shortfall_bps(history, symbol, lookback, since)
    return bool(pd.notna(average) and average > threshold_bps)


KILL_SWITCH_RESET_FILE = "kill_switch_reset.json"
KILL_SWITCH_LATCH_FILE = "kill_switch_latch.json"


def record_kill_switch_latch(
    output_dir: str | Path, trailing_bps: float, at: pd.Timestamp | None = None
) -> pd.Timestamp:
    """Persist a tripped kill-switch so it survives log changes and later fills."""
    stamp = pd.Timestamp.now(tz="UTC") if at is None else pd.Timestamp(at)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    atomic_write_text(
        Path(output_dir) / KILL_SWITCH_LATCH_FILE,
        json.dumps({"latched_at": stamp.isoformat(), "trailing_bps": float(trailing_bps)}, indent=2),
    )
    return stamp


def kill_switch_latched(output_dir: str | Path) -> bool:
    """True while a recorded trip has not been followed by a manual reset."""
    path = Path(output_dir) / KILL_SWITCH_LATCH_FILE
    if not path.exists():
        return False
    latched_at = pd.Timestamp(json.loads(path.read_text(encoding="utf-8"))["latched_at"])
    reset_at = read_kill_switch_reset(output_dir)
    return reset_at is None or reset_at <= latched_at


def evaluate_kill_switch(
    output_dir: str | Path,
    history: pd.DataFrame,
    symbol: str,
    threshold_bps: float = 2.0,
    lookback: int = 20,
    now: pd.Timestamp | None = None,
) -> bool:
    """Latching kill-switch decision for one cycle.

    A breach is recorded in ``kill_switch_latch.json`` and stays in force until
    ``record_kill_switch_reset``. Later fills (including the sleeve's own
    flattening order) therefore cannot quietly re-enable it, and neither can a
    quarantined or rewritten shortfall log.
    """
    if kill_switch_latched(output_dir):
        return True
    reset_at = read_kill_switch_reset(output_dir)
    average = trailing_shortfall_bps(history, symbol, lookback, reset_at)
    if pd.notna(average) and average > threshold_bps:
        record_kill_switch_latch(output_dir, average, now)
        return True
    return False


def record_kill_switch_reset(
    output_dir: str | Path, at: pd.Timestamp | None = None
) -> pd.Timestamp:
    stamp = pd.Timestamp.now(tz="UTC") if at is None else pd.Timestamp(at)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    atomic_write_text(
        Path(output_dir) / KILL_SWITCH_RESET_FILE,
        json.dumps({"reset_at": stamp.isoformat()}, indent=2),
    )
    return stamp


def read_kill_switch_reset(output_dir: str | Path) -> pd.Timestamp | None:
    path = Path(output_dir) / KILL_SWITCH_RESET_FILE
    if not path.exists():
        return None
    return pd.Timestamp(json.loads(path.read_text(encoding="utf-8"))["reset_at"])


def load_shortfall_history(path: str | Path) -> pd.DataFrame:
    """Read the shortfall log; an unreadable file is set aside, not fatal.

    The corrupt file is renamed with a ``.corrupt-<timestamp>`` suffix so its
    evidence is kept for inspection and the next write starts a clean log.
    """
    history_path = Path(path)
    if not history_path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(history_path)
    except Exception as error:
        stamp = pd.Timestamp.now(tz="UTC").strftime("%Y%m%dT%H%M%SZ")
        quarantined = history_path.with_name(f"{history_path.name}.corrupt-{stamp}")
        history_path.replace(quarantined)
        print(
            f"Warning: unreadable shortfall log ({error}); moved to {quarantined.name}."
        )
        return pd.DataFrame()


def managed_ledger_path(paper: PaperConfig) -> Path:
    """Ledger of symbols this strategy has traded, kept beside its state file.

    The name includes the client-order prefix so strategies that share a state
    directory never share a ledger.
    """
    prefix = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in paper.client_order_prefix
    ) or "geld"
    return paper.state_file.with_name(f"managed_symbols-{prefix}.json")


def load_managed_ledger(path: str | Path) -> set[str]:
    ledger = Path(path)
    if not ledger.exists():
        return set()
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    return {str(symbol).upper() for symbol in payload.get("symbols", [])}


def update_managed_ledger(
    path: str | Path,
    snapshot: AccountSnapshot,
    traded_symbols: set[str] | list[str] = (),
    adopted_symbols: set[str] | list[str] = (),
) -> set[str]:
    """Record every symbol the strategy holds or trades; prune finished ones.

    Universe members are recorded too: a name held today can leave the universe
    at the next refresh, and the ledger is what lets the planner find and
    liquidate it then. A symbol is dropped once it has no position, no open order
    and was not traded this cycle. ``snapshot`` must cover the universe plus the
    current ledger so its positions describe everything the strategy owns.
    """
    held = {
        symbol.upper()
        for symbol, quantity in snapshot.positions.items()
        if abs(float(quantity)) > 1e-12
    }
    open_orders = {symbol.upper() for symbol in snapshot.open_order_symbols}
    traded = {symbol.upper() for symbol in traded_symbols}
    adopted = {symbol.upper() for symbol in adopted_symbols}
    ledger = load_managed_ledger(path)
    retained = {
        symbol
        for symbol in ledger | held | traded
        if symbol in held or symbol in open_orders or symbol in traded
    } | adopted
    # Ledger names still held but left out of this snapshot (for example an
    # unpriced orphan kept unmanaged so it still counts against gross exposure)
    # remain owned by the strategy.
    retained |= ledger & {symbol.upper() for symbol in snapshot.unmanaged_symbols}
    atomic_write_text(path, json.dumps({"symbols": sorted(retained)}, indent=2))
    return retained


def outside_universe_symbols(paper: PaperConfig, universe: list[str]) -> list[str]:
    """Ledger symbols that have left the universe and must be liquidated."""
    members = {symbol.upper() for symbol in universe}
    return sorted(load_managed_ledger(managed_ledger_path(paper)) - members)


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
