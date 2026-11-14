"""Backtest and experiment commands."""

from __future__ import annotations

from pathlib import Path

from project_geld.backtest import run_backtest, save_result
from project_geld.cli.common import (
    _grid,
    _load_bars,
    _managed_symbols,
    _print_metrics,
    _strategy_context,
)
from project_geld.config import load_config, validate_config
from project_geld.experiments import grid_search, save_experiment
from project_geld.intraday import (
    label_native_intraday_bar_ends,
    resample_intraday_bars,
    run_intraday_backtest,
)
from project_geld.logs import get_logger
from project_geld.strategies.registry import create_strategy

log = get_logger(__name__)


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
    log.info(f"Artifacts: {Path(args.output).resolve()}")


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
    log.info(results.head(args.show).to_string(index=False))
    log.info(f"Experiment: {Path(args.output).resolve()}")


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
    log.info(f"Artifacts: {Path(args.output).resolve()}")
