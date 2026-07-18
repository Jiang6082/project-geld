from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd

from project_geld.backtest import run_backtest, save_result
from project_geld.config import AppConfig, load_config, validate_config
from project_geld.data import (
    AlpacaBarSource,
    CachedBarSource,
    CsvBarSource,
    SyntheticBarSource,
    default_date_range,
)
from project_geld.experiments import grid_search, save_experiment
from project_geld.paper import (
    AlpacaPaperBroker,
    append_performance_snapshot,
    mark_paper_rebalance,
    paper_rebalance_due,
    run_paper_cycle,
)
from project_geld.strategies.registry import available_strategies, create_strategy


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
    alpaca = AlpacaBarSource(config.data.feed, config.data.adjustment)
    return CachedBarSource(alpaca, config.data.cache_dir)


def _load_bars(args, config: AppConfig, extra_symbols: list[str] | None = None):
    default_start, default_end = default_date_range()
    start = _date(getattr(args, "start", None), default_start)
    end = _date(getattr(args, "end", None), default_end)
    symbols = list(dict.fromkeys([*config.universe.data_symbols, *(extra_symbols or [])]))
    return _source(args, config).fetch(symbols, start, end)


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
    source = CachedBarSource(
        AlpacaBarSource(config.data.feed, config.data.adjustment),
        config.data.cache_dir,
    )
    data_symbols = list(dict.fromkeys([*config.universe.data_symbols, *context]))
    bars = source.fetch(data_symbols, start, end)
    broker = AlpacaPaperBroker()
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
    snapshot = AlpacaPaperBroker().snapshot(_managed_symbols(config, strategy))
    output = Path(args.output)
    row = append_performance_snapshot(snapshot, output / "performance.csv")
    print(
        f"Paper equity: USD {row['equity']:,.2f}\n"
        f"Previous equity: USD {row['last_equity']:,.2f}\n"
        f"Daily return: {row['daily_return']:.2%}\n"
        f"Tracked return: {row['cumulative_return']:.2%}\n"
        f"Managed positions: {row['managed_positions']}"
    )


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
