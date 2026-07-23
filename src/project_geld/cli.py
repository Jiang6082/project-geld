from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd

from project_geld.close_check import (
    bars_available_at_close,
    build_position_reconciliation,
)
from project_geld.backtest import run_backtest, save_result
from project_geld.config import AppConfig, load_config, validate_config
from project_geld.data import (
    AlpacaBarSource,
    CachedBarSource,
    CsvBarSource,
    SyntheticBarSource,
    completed_daily_bars,
    default_date_range,
    fetch_rolling_bars,
)
from project_geld.experiments import grid_search, save_experiment
from project_geld.intraday import (
    intraday_cycle_due,
    label_native_intraday_bar_ends,
    mark_intraday_cycle,
    resample_intraday_bars,
    run_intraday_backtest,
)
from project_geld.paper import (
    AlpacaPaperBroker,
    append_performance_snapshot,
    implementation_shortfall,
    mark_paper_rebalance,
    paper_rebalance_due,
    run_paper_cycle,
)
from project_geld.strategies.registry import available_strategies, create_strategy
from project_geld.shadow import AlpacaShadowMarket, run_shadow_cycle


def _date(value: str | None, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    return timestamp.to_pydatetime()


def _source(args, config: AppConfig):
    if args.source == "synthetic":
        return SyntheticBarSource(seed=getattr(args, "seed", 7))
    if args.source == "csv":
        if not args.csv:
            raise ValueError("--csv is required when --source csv is selected.")
        return CsvBarSource(Path(args.csv))
    alpaca = AlpacaBarSource(
        config.data.feed,
        config.data.adjustment,
        config.account.credential_profile,
    )
    return CachedBarSource(alpaca, config.data.cache_dir)


def _load_bars(
    args,
    config: AppConfig,
    extra_symbols: list[str] | None = None,
    timeframe: str = "1Day",
):
    default_start, default_end = default_date_range()
    start = _date(getattr(args, "start", None), default_start)
    end = _date(getattr(args, "end", None), default_end)
    symbols = list(dict.fromkeys([*config.universe.data_symbols, *(extra_symbols or [])]))
    return _source(args, config).fetch(symbols, start, end, timeframe)


def _strategy_context(strategy) -> list[str]:
    return list(dict.fromkeys(getattr(strategy, "context_symbols", [])))


def _managed_symbols(config: AppConfig, strategy) -> list[str]:
    core = getattr(strategy, "core_symbol", None)
    return list(
        dict.fromkeys(
            [*config.universe.symbols, *([str(core).upper()] if core else [])]
        )
    )


def _scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def _grid(items: list[str]) -> dict[str, list[Any]]:
    grid: dict[str, list[Any]] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"Invalid --grid '{item}'; expected name=value1,value2.")
        name, values = item.split("=", 1)
        grid[name] = [_scalar(value.strip()) for value in values.split(",")]
    return grid


def _print_metrics(metrics: dict[str, float]) -> None:
    print(json.dumps(metrics, indent=2, sort_keys=True))


def command_backtest(args) -> None:
    config = load_config(args.config)
    validate_config(config)
    strategy = create_strategy(config.strategy.name, config.strategy.parameters)
    context = _strategy_context(strategy)
    managed = _managed_symbols(config, strategy)
    bars = _load_bars(args, config, context)
    result = run_backtest(
        bars,
        strategy,
        config.backtest,
        config.risk,
        config.universe.benchmark,
        managed,
        context_symbols=context,
    )
    save_result(result, args.output)
    _print_metrics(result.metrics)
    print(f"Artifacts: {Path(args.output).resolve()}")


def command_experiment(args) -> None:
    config = load_config(args.config)
    validate_config(config)
    strategy_name = args.strategy or config.strategy.name
    configured_strategy = create_strategy(strategy_name, config.strategy.parameters)
    context = _strategy_context(configured_strategy)
    managed = _managed_symbols(config, configured_strategy)
    bars = _load_bars(args, config, context)
    results = grid_search(
        bars,
        strategy_name,
        _grid(args.grid),
        config.backtest,
        config.risk,
        config.universe.benchmark,
        args.train_fraction,
        managed,
        config.strategy.parameters,
        context_symbols=context,
    )
    save_experiment(results, args.output)
    print(results.head(args.show).to_string(index=False))
    print(f"Experiment: {Path(args.output).resolve()}")


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
    data_symbols = list(dict.fromkeys([*config.universe.data_symbols, *context]))
    bars = source.fetch(data_symbols, start, end)
    bars = completed_daily_bars(
        bars,
        timezone_name=config.backtest.session_timezone,
    )
    if bars.empty:
        raise RuntimeError("No completed daily bars are available for paper planning.")
    broker = AlpacaPaperBroker(config.account.credential_profile)
    snapshot = broker.snapshot(managed)
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
    print(
        f"Paper equity: USD {performance['equity']:,.2f}; "
        f"tracked return: {performance['cumulative_return']:.2%}"
    )
    print(
        f"Rebalance due: {due} "
        f"({elapsed}/{config.paper.rebalance_every_sessions} sessions)"
    )
    if args.submit and not due:
        print("Submission requested but cadence guard kept this cycle preview-only.")
    if submit:
        mark_paper_rebalance(config.paper, strategy.name, latest_session)
    print(result.message)
    if len(result.orders):
        print(result.orders.to_string(index=False))


def command_paper_status(args) -> None:
    config = load_config(args.config)
    validate_config(config)
    strategy = create_strategy(config.strategy.name, config.strategy.parameters)
    snapshot = AlpacaPaperBroker(config.account.credential_profile).snapshot(
        _managed_symbols(config, strategy)
    )
    output = Path(args.output)
    row = append_performance_snapshot(snapshot, output / "performance.csv")
    print(
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
    data_symbols = list(dict.fromkeys([*config.universe.data_symbols, *context]))
    bars = bars_available_at_close(
        source.fetch(data_symbols, start, end),
        observed_at,
        market_is_open,
        config.backtest.session_timezone,
    )
    if bars.empty:
        raise RuntimeError("No completed daily bars are available for the close check.")

    snapshot = broker.snapshot(managed)
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
    latest_prices = (
        bars.sort_values("timestamp")
        .groupby("symbol", as_index=False)
        .tail(1)
        .set_index("symbol")["close"]
        .astype(float)
        .to_dict()
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
    history.to_csv(history_path, index=False)

    print(
        f"Daily V4 close check: {summary['mode']}; equity USD "
        f"{snapshot.equity:,.2f}; daily return {summary['daily_return']:.2%}"
    )
    print(
        f"Signal session {latest_local_date}; rebalance due next open: {due} "
        f"({elapsed}/{config.paper.rebalance_every_sessions} sessions)"
    )
    print(
        f"Staged {len(result.orders)} order(s); submitted 0. "
        f"Open order symbols: {len(snapshot.open_order_symbols)}; "
        f"rejected today: {summary['rejected_orders']}; "
        f"partial today: {summary['partially_filled_orders']}"
    )
    if market_is_open:
        print("Market is open; the current partial daily bar was excluded.")
    elif not summary["signal_includes_current_session"]:
        print("Warning: the current session's final daily bar was not available.")


def command_intraday_backtest(args) -> None:
    config = load_config(args.config)
    validate_config(config)
    strategy = create_strategy(config.strategy.name, config.strategy.parameters)
    if not strategy.name.startswith("intra_v"):
        raise ValueError("intraday-backtest requires an intraday strategy config.")
    context = _strategy_context(strategy)
    if args.native_bars:
        native = _load_bars(
            args,
            config,
            context,
            timeframe=f"{config.intraday.bar_minutes}Min",
        )
        bars = label_native_intraday_bar_ends(
            native,
            config.intraday.bar_minutes,
            config.backtest.session_timezone,
        )
    else:
        one_minute = _load_bars(args, config, context, timeframe="1Min")
        bars = resample_intraday_bars(
            one_minute,
            config.intraday.bar_minutes,
            config.backtest.session_timezone,
        )
    result = run_intraday_backtest(
        bars,
        strategy,
        config.backtest,
        config.risk,
        config.universe.benchmark,
        _managed_symbols(config, strategy),
        context_symbols=context,
    )
    save_result(result, args.output)
    _print_metrics(result.metrics)
    print(f"Artifacts: {Path(args.output).resolve()}")


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
    start, end = default_date_range(config.intraday.lookback_days)
    source = AlpacaBarSource(
        config.data.feed,
        config.data.adjustment,
        config.account.credential_profile,
    )
    symbols = list(
        dict.fromkeys([*config.universe.data_symbols, *context])
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
    cancellations = pd.DataFrame()
    if args.submit and config.paper.stale_order_seconds > 0:
        cancellations = broker.cancel_stale_orders(
            managed,
            config.paper.stale_order_seconds,
        )
        if len(cancellations):
            history_path = output / "order_cancellations.csv"
            history = (
                pd.read_csv(history_path)
                if history_path.exists()
                else pd.DataFrame()
            )
            history = pd.concat([history, cancellations], ignore_index=True)
            history.drop_duplicates(subset=["order_id"], keep="last").to_csv(
                history_path, index=False
            )
    snapshot = broker.snapshot(managed)
    performance = append_performance_snapshot(snapshot, output / "performance.csv")
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
    if submit and len(result.orders):
        # Shortfall logging is monitoring only; it must never disrupt the trading
        # cycle or the state marking that follows, so failures are contained.
        try:
            session_open = latest_bar.tz_convert(
                config.backtest.session_timezone
            ).normalize().tz_convert("UTC")
            shortfall = implementation_shortfall(
                result.orders, broker.order_activity(session_open)
            )
            shortfall.insert(0, "decision_bar", latest_bar.isoformat())
            shortfall_path = output / "implementation_shortfall.csv"
            history = (
                pd.read_csv(shortfall_path)
                if shortfall_path.exists()
                else pd.DataFrame()
            )
            pd.concat([history, shortfall], ignore_index=True).drop_duplicates(
                subset=["decision_bar", "client_order_id"], keep="last"
            ).to_csv(shortfall_path, index=False)
            filled = shortfall[~shortfall["missed"]]
            average_shortfall = (
                float(filled["shortfall_bps"].mean()) if len(filled) else float("nan")
            )
            print(
                f"Implementation shortfall: {len(filled)}/{len(shortfall)} filled; "
                f"average {average_shortfall:.2f} bps (research invalidation > 2 bps)."
            )
        except Exception as error:  # monitoring must never break the trading cycle
            print(f"Warning: implementation-shortfall logging failed: {error}")
    print(
        f"Intraday paper account '{config.account.name}': "
        f"USD {performance['equity']:,.2f}; latest completed bar {latest_bar}"
    )
    if len(cancellations):
        print(
            f"Cancelled {len(cancellations)} stale managed order(s) before replanning."
        )
    print(f"Cycle due: {due}")
    if args.submit and not due:
        print("Submission requested but this completed bar was already processed.")
    if submit:
        mark_intraday_cycle(config.intraday.state_file, latest_bar)
    print(result.message)
    if len(result.orders):
        print(result.orders.to_string(index=False))


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
    print(f"Shadow cycle {latest_time}: {len(events)} event(s); zero orders submitted.")
    if len(events):
        print(events.to_string(index=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="geld", description="Project Geld research and Alpaca paper engine")
    parser.add_argument("--config", default="config.example.toml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list-strategies")
    list_parser.set_defaults(func=lambda args: print("\n".join(available_strategies())))

    backtest_parser = subparsers.add_parser("backtest")
    backtest_parser.add_argument("--source", choices=["alpaca", "csv", "synthetic"], default="synthetic")
    backtest_parser.add_argument("--csv")
    backtest_parser.add_argument("--start")
    backtest_parser.add_argument("--end")
    backtest_parser.add_argument("--seed", type=int, default=7)
    backtest_parser.add_argument("--output", default="artifacts/backtest")
    backtest_parser.set_defaults(func=command_backtest)

    experiment_parser = subparsers.add_parser("experiment")
    experiment_parser.add_argument("--source", choices=["alpaca", "csv", "synthetic"], default="synthetic")
    experiment_parser.add_argument("--csv")
    experiment_parser.add_argument("--start")
    experiment_parser.add_argument("--end")
    experiment_parser.add_argument("--seed", type=int, default=7)
    experiment_parser.add_argument("--strategy", choices=available_strategies())
    experiment_parser.add_argument("--grid", action="append", required=True)
    experiment_parser.add_argument("--train-fraction", type=float, default=0.70)
    experiment_parser.add_argument("--show", type=int, default=10)
    experiment_parser.add_argument("--output", default="artifacts/experiments/results.csv")
    experiment_parser.set_defaults(func=command_experiment)

    paper_parser = subparsers.add_parser("paper-once")
    paper_parser.add_argument("--submit", action="store_true", help="Submit to Alpaca paper; default only plans orders")
    paper_parser.add_argument("--output", default="artifacts/paper")
    paper_parser.set_defaults(func=command_paper)

    status_parser = subparsers.add_parser("paper-status")
    status_parser.add_argument("--output", default="artifacts/paper")
    status_parser.set_defaults(func=command_paper_status)

    close_parser = subparsers.add_parser("daily-close-check")
    close_parser.add_argument("--output", default="artifacts/paper-daily-v4-close")
    close_parser.set_defaults(func=command_daily_close_check)

    intraday_backtest = subparsers.add_parser("intraday-backtest")
    intraday_backtest.add_argument(
        "--source", choices=["alpaca", "csv"], default="alpaca"
    )
    intraday_backtest.add_argument("--csv")
    intraday_backtest.add_argument("--start")
    intraday_backtest.add_argument("--end")
    intraday_backtest.add_argument("--output", default="artifacts/intraday-backtest")
    intraday_backtest.add_argument(
        "--native-bars",
        action="store_true",
        help="Fetch native Alpaca intraday bars instead of aggregating one-minute bars",
    )
    intraday_backtest.set_defaults(func=command_intraday_backtest)

    intraday_paper = subparsers.add_parser("intraday-paper-once")
    intraday_paper.add_argument(
        "--submit", action="store_true", help="Submit to the configured Alpaca paper account"
    )
    intraday_paper.add_argument("--output", default="artifacts/intraday-paper")
    intraday_paper.set_defaults(func=command_intraday_paper)
    intraday_shadow = subparsers.add_parser("intraday-shadow-once")
    intraday_shadow.add_argument("--output", default="artifacts/intraday-shadow")
    intraday_shadow.set_defaults(func=command_intraday_shadow)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
