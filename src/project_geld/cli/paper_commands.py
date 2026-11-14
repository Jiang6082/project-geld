"""Paper planning, submission, reconciliation and kill-switch commands."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import time

import pandas as pd

from project_geld.atomicio import atomic_write_dataframe_csv
from project_geld.cli.common import (
    _emit_run_manifest,
    _latest_closes,
    _managed_symbols,
    _priced,
    _report_ownership,
    _strategy_context,
)
from project_geld.close_check import (
    bars_available_at_close,
    build_position_reconciliation,
)
from project_geld.config import load_config, validate_config
from project_geld.data import (
    AlpacaBarSource,
    CachedBarSource,
    completed_daily_bars,
    default_date_range,
    fetch_rolling_bars,
)
from project_geld.intraday import (
    intraday_cycle_due,
    mark_intraday_cycle,
    resample_intraday_bars,
)
from project_geld.logs import get_logger
from project_geld.paper import (
    AlpacaPaperBroker,
    append_performance_snapshot,
    evaluate_kill_switch,
    implementation_shortfall,
    load_shortfall_history,
    managed_ledger_path,
    mark_paper_rebalance,
    outside_universe_symbols,
    paper_rebalance_due,
    record_kill_switch_reset,
    refresh_shortfall_history,
    run_paper_cycle,
    unresolved_shortfall_since,
    update_managed_ledger,
)
from project_geld.shadow import AlpacaShadowMarket, run_shadow_cycle
from project_geld.strategies.registry import create_strategy

log = get_logger(__name__)


def command_paper(args) -> None:
    config = load_config(args.config)
    validate_config(config)
    strategy = create_strategy(config.strategy.name, config.strategy.parameters)
    if config.universe.symbols_as_of is not None:
        universe_age = (
            pd.Timestamp.now(tz="UTC")
            - pd.Timestamp(config.universe.symbols_as_of)
        ).days
        if universe_age > config.paper.max_universe_age_days:
            raise RuntimeError(
                f"Universe snapshot is {universe_age} days old; refresh it before paper planning."
            )
    context = _strategy_context(strategy)
    managed = _managed_symbols(config, strategy)
    # Symbols this strategy still owns but that have left the universe. They are
    # priced and liquidated, but never passed to the strategy as candidates.
    orphans = outside_universe_symbols(config.paper, managed)
    start, end = default_date_range(config.paper.lookback_days)
    end -= timedelta(minutes=config.paper.market_data_delay_minutes)
    source = CachedBarSource(
        AlpacaBarSource(
            config.data.feed,
            config.data.adjustment,
            config.account.credential_profile,
        ),
        config.data.cache_dir,
    )
    data_symbols = list(
        dict.fromkeys([*config.universe.data_symbols, *context, *orphans])
    )
    bars = source.fetch(data_symbols, start, end)
    bars = completed_daily_bars(
        bars,
        timezone_name=config.backtest.session_timezone,
    )
    if bars.empty:
        raise RuntimeError("No completed daily bars are available for paper planning.")
    broker = AlpacaPaperBroker(config.account.credential_profile)
    latest_prices = _latest_closes(bars)
    snapshot = broker.snapshot([*managed, *_priced(orphans, latest_prices)])
    _report_ownership(snapshot, orphans, latest_prices)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    performance = append_performance_snapshot(
        snapshot, output / "performance.csv"
    )
    due, elapsed, latest_session = paper_rebalance_due(
        bars, config.paper, strategy.name
    )
    submit = args.submit and due
    result = run_paper_cycle(
        bars,
        strategy,
        broker,
        config.risk,
        config.paper,
        managed,
        submit=submit,
        snapshot=snapshot,
        context_symbols=context,
        confirmation_env=config.account.confirmation_env,
    )
    result.targets.to_csv(output / "latest_targets.csv", index=False)
    result.orders.to_csv(output / "paper_orders.csv", index=False)
    log.info(
        f"Paper equity: USD {performance['equity']:,.2f}; "
        f"tracked return: {performance['cumulative_return']:.2%}"
    )
    log.info(
        f"Rebalance due: {due} "
        f"({elapsed}/{config.paper.rebalance_every_sessions} sessions)"
    )
    if args.submit and not due:
        log.info("Submission requested but cadence guard kept this cycle preview-only.")
    if submit:
        mark_paper_rebalance(config.paper, strategy.name, latest_session)
        update_managed_ledger(
            managed_ledger_path(config.paper),
            snapshot,
            traded_symbols=(
                set(result.orders["symbol"].astype(str)) if len(result.orders) else set()
            ),
        )
    log.info(result.message)
    if len(result.orders):
        log.info(result.orders.to_string(index=False))
    _emit_run_manifest(
        config, output, "paper_cycle",
        "submission" if submit else "planning-only",
        {
            "equity": float(performance["equity"]),
            "cumulative_return": float(performance["cumulative_return"]),
            "rebalance_due": bool(due),
            "sessions_elapsed": int(elapsed),
            "planned_orders": int(len(result.orders)),
            "submitted": bool(submit),
        },
    )


def command_paper_status(args) -> None:
    config = load_config(args.config)
    validate_config(config)
    strategy = create_strategy(config.strategy.name, config.strategy.parameters)
    snapshot = AlpacaPaperBroker(config.account.credential_profile).snapshot(
        _managed_symbols(config, strategy)
    )
    output = Path(args.output)
    row = append_performance_snapshot(snapshot, output / "performance.csv")
    log.info(
        f"Paper equity: USD {row['equity']:,.2f}\n"
        f"Previous equity: USD {row['last_equity']:,.2f}\n"
        f"Daily return: {row['daily_return']:.2%}\n"
        f"Tracked return: {row['cumulative_return']:.2%}\n"
        f"Managed positions: {row['managed_positions']}"
    )


def command_daily_close_check(args) -> None:
    config = load_config(args.config)
    validate_config(config)
    strategy = create_strategy(config.strategy.name, config.strategy.parameters)
    if strategy.name != "daily_v4":
        raise ValueError("daily-close-check requires the Daily V4 configuration.")

    observed_at = pd.Timestamp.now(tz="UTC")
    broker = AlpacaPaperBroker(config.account.credential_profile)
    clock = broker.get_clock()
    market_is_open = bool(clock.is_open)
    context = _strategy_context(strategy)
    managed = _managed_symbols(config, strategy)
    orphans = outside_universe_symbols(config.paper, managed)
    start, end = default_date_range(config.paper.lookback_days)
    end -= timedelta(minutes=config.paper.market_data_delay_minutes)
    source = CachedBarSource(
        AlpacaBarSource(
            config.data.feed,
            config.data.adjustment,
            config.account.credential_profile,
        ),
        config.data.cache_dir,
    )
    data_symbols = list(
        dict.fromkeys([*config.universe.data_symbols, *context, *orphans])
    )
    bars = bars_available_at_close(
        source.fetch(data_symbols, start, end),
        observed_at,
        market_is_open,
        config.backtest.session_timezone,
    )
    if bars.empty:
        raise RuntimeError("No completed daily bars are available for the close check.")

    latest_prices = _latest_closes(bars)
    snapshot = broker.snapshot([*managed, *_priced(orphans, latest_prices)])
    _report_ownership(snapshot, orphans, latest_prices)
    result = run_paper_cycle(
        bars,
        strategy,
        broker,
        config.risk,
        config.paper,
        managed,
        submit=False,
        snapshot=snapshot,
        context_symbols=context,
        confirmation_env=config.account.confirmation_env,
    )
    reconciliation = build_position_reconciliation(
        result.targets, latest_prices, snapshot
    )
    due, elapsed, latest_session = paper_rebalance_due(
        bars, config.paper, strategy.name
    )
    local_observed = observed_at.tz_convert(config.backtest.session_timezone)
    local_midnight = local_observed.normalize().tz_convert("UTC")
    activity = broker.order_activity(local_midnight)
    status_counts = (
        activity["status"].value_counts().to_dict() if len(activity) else {}
    )
    latest_local_date = latest_session.tz_convert(
        config.backtest.session_timezone
    ).date()
    current_local_date = local_observed.date()
    universe_age_days = (
        (observed_at - pd.Timestamp(config.universe.symbols_as_of)).days
        if config.universe.symbols_as_of is not None
        else None
    )
    drift_threshold = max(
        float(getattr(strategy, "no_trade_band", 0.0)),
        config.risk.min_trade_pct_equity,
    )
    summary = {
        "observed_at": observed_at.isoformat(),
        "market_is_open": market_is_open,
        "mode": "prior_close_preview" if market_is_open else "final_close",
        "latest_signal_session": latest_session.isoformat(),
        "signal_includes_current_session": latest_local_date == current_local_date,
        "equity": snapshot.equity,
        "last_equity": snapshot.last_equity,
        "daily_return": (
            snapshot.equity / snapshot.last_equity - 1
            if snapshot.last_equity > 0
            else 0.0
        ),
        "cash": snapshot.cash,
        "current_gross_exposure": float(
            reconciliation["current_weight"].abs().sum()
        ),
        "target_gross_exposure": float(
            reconciliation["target_weight"].abs().sum()
        ),
        "significant_drift_positions": int(
            reconciliation["weight_drift"].abs().ge(drift_threshold).sum()
        ),
        "unexpected_positions": int(reconciliation["unexpected_position"].sum()),
        "missing_price_positions": int(reconciliation["missing_price"].sum()),
        "open_order_symbols": sorted(snapshot.open_order_symbols),
        "orders_observed_today": int(len(activity)),
        "order_status_counts": status_counts,
        "rejected_orders": int(status_counts.get("rejected", 0)),
        "partially_filled_orders": int(status_counts.get("partially_filled", 0)),
        "staged_order_count": int(len(result.orders)),
        "rebalance_due_next_open": due,
        "sessions_since_rebalance": elapsed,
        "rebalance_interval_sessions": config.paper.rebalance_every_sessions,
        "universe_age_days": universe_age_days,
        "universe_is_stale": bool(
            universe_age_days is not None
            and universe_age_days > config.paper.max_universe_age_days
        ),
    }

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    result.targets.to_csv(output / "staged_targets.csv", index=False)
    result.orders.to_csv(output / "staged_orders.csv", index=False)
    reconciliation.to_csv(output / "position_reconciliation.csv", index=False)
    activity.to_csv(output / "order_activity.csv", index=False)
    append_performance_snapshot(snapshot, output / "close_performance.csv", observed_at)
    (output / "close_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    history_path = output / "close_summary_history.csv"
    history = pd.read_csv(history_path) if history_path.exists() else pd.DataFrame()
    history = pd.concat(
        [history, pd.DataFrame([{**summary, "order_status_counts": json.dumps(status_counts)}])],
        ignore_index=True,
    )
    atomic_write_dataframe_csv(history, history_path, index=False)

    log.info(
        f"Daily V4 close check: {summary['mode']}; equity USD "
        f"{snapshot.equity:,.2f}; daily return {summary['daily_return']:.2%}"
    )
    log.info(
        f"Signal session {latest_local_date}; rebalance due next open: {due} "
        f"({elapsed}/{config.paper.rebalance_every_sessions} sessions)"
    )
    log.info(
        f"Staged {len(result.orders)} order(s); submitted 0. "
        f"Open order symbols: {len(snapshot.open_order_symbols)}; "
        f"rejected today: {summary['rejected_orders']}; "
        f"partial today: {summary['partially_filled_orders']}"
    )
    if market_is_open:
        log.info("Market is open; the current partial daily bar was excluded.")
    elif not summary["signal_includes_current_session"]:
        log.warning("Warning: the current session's final daily bar was not available.")


def command_adopt_positions(args) -> None:
    """Add existing outside-universe positions to the strategy's ledger.

    Use this once for positions that were stranded before the ledger existed.
    Adopted symbols are liquidated at the next submitting rebalance. No orders
    are placed by this command.
    """
    config = load_config(args.config)
    validate_config(config)
    strategy = create_strategy(config.strategy.name, config.strategy.parameters)
    managed = _managed_symbols(config, strategy)
    orphans = outside_universe_symbols(config.paper, managed)
    snapshot = AlpacaPaperBroker(config.account.credential_profile).snapshot(
        [*managed, *orphans]
    )
    available = set(snapshot.unmanaged_symbols)
    if not args.symbols and not args.all:
        log.info(
            "Unmanaged positions: "
            + (", ".join(sorted(available)) if available else "none")
            + ". Pass --symbols A,B (or --all on an account dedicated to this "
            "strategy) to adopt them; nothing was changed."
        )
        return
    requested = (
        {item.strip().upper() for item in args.symbols.split(",") if item.strip()}
        if args.symbols
        else available
    )
    missing = sorted(requested - available)
    if missing:
        raise ValueError(
            f"Not unmanaged positions in this account: {', '.join(missing)}"
        )
    if not requested:
        log.info("No unmanaged positions to adopt.")
        return
    ledger = update_managed_ledger(
        managed_ledger_path(config.paper), snapshot, adopted_symbols=requested
    )
    log.info(
        f"Adopted {len(requested)} position(s): {', '.join(sorted(requested))}. "
        f"Ledger now tracks {len(ledger)} symbol(s); adopted names outside the "
        "universe are liquidated at the next submitting rebalance."
    )


def command_reset_kill_switch(args) -> None:
    """Re-enable a latched shortfall kill-switch from now on.

    Shortfall rows decided before the reset are ignored by the switch but kept in
    the log. Run this only after investigating why execution cost breached the
    gate.
    """
    output = Path(args.output)
    stamp = record_kill_switch_reset(output)
    log.info(
        f"Kill-switch reset at {stamp.isoformat()}; earlier shortfall rows in "
        f"{output / 'implementation_shortfall.csv'} no longer count toward it."
    )


def command_intraday_paper(args) -> None:
    config = load_config(args.config)
    validate_config(config)
    strategy = create_strategy(config.strategy.name, config.strategy.parameters)
    if not strategy.name.startswith("intra_v"):
        raise ValueError("intraday-paper-once requires an intraday strategy config.")
    if config.universe.symbols_as_of is not None:
        universe_age = (
            pd.Timestamp.now(tz="UTC") - pd.Timestamp(config.universe.symbols_as_of)
        ).days
        if universe_age > config.paper.max_universe_age_days:
            raise RuntimeError(
                f"Universe snapshot is {universe_age} days old; refresh it before paper planning."
            )
    context = _strategy_context(strategy)
    managed = _managed_symbols(config, strategy)
    # Ledger symbols that left the universe are priced and flattened, never selected.
    orphans = outside_universe_symbols(config.paper, managed)
    flatten = bool(getattr(args, "flatten_all", False))
    start, end = default_date_range(config.intraday.lookback_days)
    source = AlpacaBarSource(
        config.data.feed,
        config.data.adjustment,
        config.account.credential_profile,
    )
    symbols = list(
        dict.fromkeys([*config.universe.data_symbols, *context, *orphans])
    )
    one_minute = fetch_rolling_bars(
        source,
        symbols,
        start,
        end,
        "1Min",
        config.data.cache_dir / "paper-rolling-1min.pkl",
    )
    bars = resample_intraday_bars(
        one_minute,
        config.intraday.bar_minutes,
        config.backtest.session_timezone,
    )
    if bars.empty:
        raise RuntimeError("No completed intraday bars are available.")
    latest_bar = pd.Timestamp(bars["timestamp"].max())
    due = intraday_cycle_due(config.intraday.state_file, latest_bar)
    broker = AlpacaPaperBroker(config.account.credential_profile)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    latest_prices = _latest_closes(bars)
    owned = [*managed, *_priced(orphans, latest_prices)]
    cancellations = pd.DataFrame()
    if args.submit and (config.paper.stale_order_seconds > 0 or flatten):
        # A flatten cancels every resting managed order so exits are not blocked.
        max_age = 1 if flatten else config.paper.stale_order_seconds
        cancellations = broker.cancel_stale_orders(owned, max_age)
        if len(cancellations):
            history_path = output / "order_cancellations.csv"
            history = (
                pd.read_csv(history_path)
                if history_path.exists()
                else pd.DataFrame()
            )
            history = pd.concat([history, cancellations], ignore_index=True)
            atomic_write_dataframe_csv(
                history.drop_duplicates(subset=["order_id"], keep="last"),
                history_path,
                index=False,
            )
    snapshot = broker.snapshot(owned)
    _report_ownership(snapshot, orphans, latest_prices)
    performance = append_performance_snapshot(snapshot, output / "performance.csv")
    # A flatten (early close) runs even when this bar was already processed.
    submit = args.submit and (due or flatten)
    # Shortfall kill-switch: if the cost-sensitive base sleeve's trailing
    # implementation shortfall has breached the research's ~2 bps gate, force its
    # core symbol flat and trade only the overlay. A breach is latched in
    # kill_switch_latch.json until `geld reset-kill-switch` is run. Monitoring
    # never aborts the cycle: if the switch cannot be evaluated, only the base
    # sleeve sits out this cycle.
    disable_symbols: set[str] = set()
    core_symbol = getattr(strategy, "core_symbol", None)
    shortfall_path = output / "implementation_shortfall.csv"
    shortfall_history = pd.DataFrame()
    if core_symbol:
        try:
            shortfall_history = load_shortfall_history(shortfall_path)
            if evaluate_kill_switch(output, shortfall_history, str(core_symbol)):
                disable_symbols.add(str(core_symbol).upper())
                log.info(
                    f"Kill-switch latched: {core_symbol} base sleeve trailing shortfall "
                    "exceeded 2 bps; it stays flat until you investigate and run "
                    f"`geld --config {args.config} reset-kill-switch --output {output}`."
                )
        except Exception as error:  # monitoring must never break the trading cycle
            disable_symbols.add(str(core_symbol).upper())
            log.warning(
                f"Warning: kill-switch evaluation failed ({error}); the "
                f"{core_symbol} base sleeve sits out this cycle."
            )
    paper_config = config.paper
    if flatten:
        # Force every managed symbol flat (early close). A per-attempt order
        # prefix (fxHHMMSS) keeps a retried flatten from reusing an earlier,
        # possibly cancelled, client order ID.
        disable_symbols |= {symbol.upper() for symbol in owned}
        attempt = pd.Timestamp.now(tz="UTC").strftime("%H%M%S")
        paper_config = replace(config.paper, client_order_prefix=f"fx{attempt}")
        log.info("Flatten-all requested: every managed position targets zero.")
    result = run_paper_cycle(
        bars,
        strategy,
        broker,
        config.risk,
        paper_config,
        managed,
        submit=submit,
        snapshot=snapshot,
        context_symbols=context,
        confirmation_env=config.account.confirmation_env,
        disable_symbols=disable_symbols,
    )
    result.targets.to_csv(output / "latest_targets.csv", index=False)
    result.orders.to_csv(output / "paper_orders.csv", index=False)
    if submit:
        # Shortfall logging is monitoring only; it must never disrupt the trading
        # cycle or the state marking that follows, so failures are contained.
        try:
            session_open = latest_bar.tz_convert(
                config.backtest.session_timezone
            ).normalize().tz_convert("UTC")
            history = shortfall_history
            unresolved = unresolved_shortfall_since(history, latest_bar)
            activity_start = (
                min(session_open, unresolved) if unresolved is not None else session_open
            )
            activity = broker.order_activity(activity_start)
            current = pd.DataFrame()
            if len(result.orders):
                current = implementation_shortfall(result.orders, activity)
                current.insert(0, "decision_bar", latest_bar.isoformat())
            combined = pd.concat([history, current], ignore_index=True)
            if len(combined):
                combined = combined.drop_duplicates(
                    subset=["decision_bar", "client_order_id"], keep="last"
                )
                # Earlier orders measured before they filled are re-scored here.
                # A re-scoring failure must not lose this cycle's new rows.
                try:
                    combined = refresh_shortfall_history(combined, activity)
                except Exception as error:
                    log.warning(f"Warning: re-scoring earlier shortfall rows failed: {error}")
                atomic_write_dataframe_csv(combined, shortfall_path, index=False)
            if len(current):
                filled = current[~current["missed"]]
                average_shortfall = (
                    float(filled["shortfall_bps"].mean()) if len(filled) else float("nan")
                )
                log.info(
                    f"Implementation shortfall: {len(filled)}/{len(current)} filled at "
                    f"measurement; average {average_shortfall:.2f} bps "
                    "(research invalidation > 2 bps). Late fills are re-scored on "
                    "later cycles."
                )
        except Exception as error:  # monitoring must never break the trading cycle
            log.warning(f"Warning: implementation-shortfall logging failed: {error}")
    log.info(
        f"Intraday paper account '{config.account.name}': "
        f"USD {performance['equity']:,.2f}; latest completed bar {latest_bar}"
    )
    if len(cancellations):
        log.info(
            f"Cancelled {len(cancellations)} stale managed order(s) before replanning."
        )
    log.info(f"Cycle due: {due}")
    if args.submit and not due:
        log.info("Submission requested but this completed bar was already processed.")
    if submit and flatten:
        _verify_flat(broker, owned, getattr(args, "flatten_wait_seconds", 30.0))
    if submit:
        mark_intraday_cycle(config.intraday.state_file, latest_bar)
        update_managed_ledger(
            managed_ledger_path(config.paper),
            snapshot,
            traded_symbols=(
                set(result.orders["symbol"].astype(str)) if len(result.orders) else set()
            ),
        )
    log.info(result.message)
    if len(result.orders):
        log.info(result.orders.to_string(index=False))
    _emit_run_manifest(
        config, output, "intraday_paper_cycle",
        "submission" if submit else "planning-only",
        {
            "equity": float(performance["equity"]),
            "latest_bar": str(latest_bar),
            "cycle_due": bool(due),
            "planned_orders": int(len(result.orders)),
            "submitted": bool(submit),
        },
    )


def _verify_flat(broker, symbols: list[str], wait_seconds: float, poll_seconds: float = 2.0) -> None:
    """Fail (non-zero exit) unless every managed position is closed.

    A position can survive a flatten if its exit order was skipped (an order
    still pending cancel) or did not fill; the runner then retries.
    """
    deadline = time.monotonic() + max(wait_seconds, 0.0)
    while True:
        remaining = {
            symbol: quantity
            for symbol, quantity in broker.snapshot(symbols).positions.items()
            if abs(float(quantity)) > 1e-12
        }
        if not remaining:
            log.info("Flatten confirmed: no managed positions remain.")
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "Flatten incomplete; still holding "
                + ", ".join(f"{symbol} {quantity:g}" for symbol, quantity in sorted(remaining.items()))
            )
        time.sleep(poll_seconds)


def command_intraday_shadow(args) -> None:
    config = load_config(args.config)
    validate_config(config)
    strategy = create_strategy(config.strategy.name, config.strategy.parameters)
    if not strategy.name.startswith("intra_v"):
        raise ValueError("intraday-shadow-once requires an intraday strategy config.")
    context = _strategy_context(strategy)
    start, end = default_date_range(config.intraday.lookback_days)
    source = AlpacaBarSource(config.data.feed, config.data.adjustment, config.account.credential_profile)
    symbols = list(dict.fromkeys([*config.universe.data_symbols, *context]))
    bars = resample_intraday_bars(
        source.fetch(symbols, start, end, "1Min"),
        config.intraday.bar_minutes,
        config.backtest.session_timezone,
    )
    if bars.empty:
        raise RuntimeError("No completed intraday bars are available.")
    targets = strategy.generate_targets(bars)
    latest_time = targets["timestamp"].max()
    latest_targets = targets[targets["timestamp"].eq(latest_time)].copy()
    state_path = Path(args.output) / "state.json"
    pending_symbols = set()
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        pending_symbols |= set(state.get("pending_targets", {})) | set(state.get("positions", {}))
    pending_symbols |= set(latest_targets.loc[latest_targets["target_weight"].lt(0), "symbol"])
    market = AlpacaShadowMarket(config.account.credential_profile, config.data.feed)
    quotes = market.quotes(sorted(pending_symbols))
    availability = market.availability(sorted(pending_symbols))
    prices = bars.sort_values("timestamp").groupby("symbol").tail(1).set_index("symbol")["close"].to_dict()
    events = run_shadow_cycle(
        targets, prices, quotes, availability, state_path, Path(args.output) / "events.csv",
        capital=config.backtest.initial_cash, limit_offset_bps=config.paper.limit_offset_bps,
    )
    log.info(f"Shadow cycle {latest_time}: {len(events)} event(s); zero orders submitted.")
    if len(events):
        log.info(events.to_string(index=False))
