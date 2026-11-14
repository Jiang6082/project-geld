"""Implementation-shortfall measurement and the latching kill-switch."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from project_geld.atomicio import atomic_write_text
from project_geld.logs import get_logger

log = get_logger(__name__)


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
        log.warning(
            f"Warning: unreadable shortfall log ({error}); moved to {quarantined.name}."
        )
        return pd.DataFrame()
