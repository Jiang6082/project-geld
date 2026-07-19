from __future__ import annotations

from pathlib import Path
import json

import numpy as np
import pandas as pd

from project_geld.config import BacktestConfig, RiskConfig
from project_geld.data import normalize_bars
from project_geld.metrics import calculate_metrics
from project_geld.models import BacktestResult
from project_geld.strategies.base import Strategy


TRADE_COLUMNS = [
    "signal_timestamp",
    "timestamp",
    "symbol",
    "side",
    "quantity",
    "fill_price",
    "notional",
    "fees",
    "target_weight",
    "exit_reason",
]


def _constrain_weights(
    targets: pd.Series, risk: RiskConfig, allow_short: bool = False
) -> pd.Series:
    caps = pd.Series(
        {
            symbol: risk.symbol_position_weight_limits.get(
                str(symbol).upper(), risk.max_position_weight
            )
            for symbol in targets.index
        }
    )
    weights = targets.fillna(0.0)
    weights = (
        weights.clip(lower=-caps, upper=caps)
        if allow_short
        else weights.clip(lower=0.0, upper=caps)
    )
    gross = float(weights.abs().sum())
    if gross > risk.max_gross_exposure:
        weights *= risk.max_gross_exposure / gross
    return weights


def _quantity(value: float, fractional: bool) -> float:
    if fractional:
        return round(max(value, 0.0), 6)
    return float(np.floor(max(value, 0.0)))


def _signed_quantity(value: float, fractional: bool) -> float:
    direction = -1.0 if value < 0 else 1.0
    return direction * _quantity(abs(value), fractional)


def run_backtest(
    bars: pd.DataFrame,
    strategy: Strategy,
    config: BacktestConfig,
    risk: RiskConfig,
    benchmark: str = "SPY",
    tradable_symbols: list[str] | None = None,
    context_symbols: list[str] | None = None,
) -> BacktestResult:
    bars = normalize_bars(bars)
    if bars.empty:
        raise ValueError("No bars supplied.")
    tradables = (
        None
        if tradable_symbols is None
        else {symbol.upper() for symbol in tradable_symbols}
    )
    context = {symbol.upper() for symbol in (context_symbols or [])}
    strategy_bars = (
        bars if tradables is None else bars[bars["symbol"].isin(tradables | context)]
    )
    targets = strategy.generate_targets(strategy_bars)
    if tradables is not None:
        targets = targets.copy()
        targets.loc[~targets["symbol"].isin(tradables), "target_weight"] = 0.0
    opens = bars.pivot(index="timestamp", columns="symbol", values="open").sort_index()
    closes = bars.pivot(index="timestamp", columns="symbol", values="close").sort_index()
    dates = closes.index.intersection(opens.index).sort_values()
    if len(dates) < 2:
        raise ValueError("At least two sessions are required.")
    session_dates = pd.Series(
        dates.tz_convert(config.session_timezone).date, index=dates
    )
    session_ends = set(session_dates.groupby(session_dates).tail(1).index)

    target_table = targets.pivot(index="timestamp", columns="symbol", values="target_weight").reindex(dates)
    target_table = target_table.reindex(columns=closes.columns, fill_value=0.0).fillna(0.0)
    cash = float(config.initial_cash)
    shares: dict[str, float] = {}
    last_prices: dict[str, float] = {}
    last_seen_session: dict[str, int] = {}
    pending: tuple[pd.Timestamp, pd.Series] | None = None
    equity_rows: list[dict] = []
    trade_rows: list[dict] = []

    for session_index, timestamp in enumerate(dates):
        open_prices = opens.loc[timestamp].dropna()
        close_prices = closes.loc[timestamp].dropna()
        valuation_prices = {**last_prices, **{s: float(v) for s, v in open_prices.items()}}

        if pending is not None:
            signal_timestamp, raw_weights = pending
            weights = _constrain_weights(raw_weights, risk, config.allow_short)
            equity_at_open = cash + sum(
                quantity * valuation_prices.get(symbol, last_prices.get(symbol, 0.0))
                for symbol, quantity in shares.items()
            )
            desired: dict[str, float] = {}
            for symbol in closes.columns:
                price = float(open_prices.get(symbol, np.nan))
                if not np.isfinite(price) or price <= 0:
                    continue
                desired[symbol] = _signed_quantity(
                    float(weights.get(symbol, 0.0)) * equity_at_open / price,
                    config.allow_fractional,
                )

            deltas = {symbol: desired.get(symbol, 0.0) - shares.get(symbol, 0.0) for symbol in set(shares) | set(desired)}
            ordered = sorted(deltas, key=lambda symbol: deltas[symbol])
            for symbol in ordered:
                delta = deltas[symbol]
                market_open = float(open_prices.get(symbol, np.nan))
                if not np.isfinite(market_open) or market_open <= 0 or abs(delta) < 1e-9:
                    continue
                side = "sell" if delta < 0 else "buy"
                slip = config.slippage_bps / 10_000
                fill_price = market_open * (1 - slip if side == "sell" else 1 + slip)
                quantity = _quantity(abs(delta), config.allow_fractional)
                minimum_trade = max(
                    risk.min_trade_notional,
                    risk.min_trade_pct_equity * equity_at_open,
                )
                if quantity <= 0 or quantity * fill_price < minimum_trade:
                    continue
                fees = quantity * config.commission_per_share
                if side == "buy":
                    if not config.allow_short:
                        affordable = _quantity(
                            cash / (fill_price + config.commission_per_share),
                            config.allow_fractional,
                        )
                        quantity = min(quantity, affordable)
                    fees = quantity * config.commission_per_share
                    if quantity <= 0:
                        continue
                    cash -= quantity * fill_price + fees
                    shares[symbol] = shares.get(symbol, 0.0) + quantity
                else:
                    cash += quantity * fill_price - fees
                    remaining = shares.get(symbol, 0.0) - quantity
                    if abs(remaining) <= 1e-9:
                        shares.pop(symbol, None)
                    else:
                        shares[symbol] = remaining
                trade_rows.append(
                    {
                        "signal_timestamp": signal_timestamp,
                        "timestamp": timestamp,
                        "symbol": symbol,
                        "side": side,
                        "quantity": quantity,
                        "fill_price": fill_price,
                        "notional": quantity * fill_price,
                        "fees": fees,
                        "target_weight": float(weights.get(symbol, 0.0)),
                        "exit_reason": "rebalance",
                    }
                )
            pending = None

        for symbol in list(shares):
            last_seen = last_seen_session.get(symbol)
            if (
                symbol not in close_prices
                and last_seen is not None
                and session_index - last_seen >= config.missing_price_exit_sessions
            ):
                signed_quantity = shares.pop(symbol)
                quantity = abs(signed_quantity)
                reference_price = last_prices.get(symbol, 0.0)
                side = "sell" if signed_quantity > 0 else "buy"
                fill_price = reference_price * (
                    1 - config.missing_price_haircut_pct
                    if side == "sell"
                    else 1 + config.missing_price_haircut_pct
                )
                fees = quantity * config.commission_per_share
                cash += (
                    quantity * fill_price - fees
                    if side == "sell"
                    else -quantity * fill_price - fees
                )
                trade_rows.append(
                    {
                        "signal_timestamp": timestamp,
                        "timestamp": timestamp,
                        "symbol": symbol,
                        "side": side,
                        "quantity": quantity,
                        "fill_price": fill_price,
                        "notional": quantity * fill_price,
                        "fees": fees,
                        "target_weight": 0.0,
                        "exit_reason": "missing_price_forced_exit",
                    }
                )

        last_prices.update({symbol: float(price) for symbol, price in close_prices.items()})
        last_seen_session.update({symbol: session_index for symbol in close_prices.index})
        if config.force_flat_at_session_end and timestamp in session_ends:
            for symbol, signed_quantity in list(shares.items()):
                market_close = float(close_prices.get(symbol, np.nan))
                if not np.isfinite(market_close) or market_close <= 0:
                    continue
                quantity = abs(signed_quantity)
                side = "sell" if signed_quantity > 0 else "buy"
                fill_price = market_close * (
                    1 - config.slippage_bps / 10_000
                    if side == "sell"
                    else 1 + config.slippage_bps / 10_000
                )
                fees = quantity * config.commission_per_share
                cash += (
                    quantity * fill_price - fees
                    if side == "sell"
                    else -quantity * fill_price - fees
                )
                shares.pop(symbol, None)
                trade_rows.append(
                    {
                        "signal_timestamp": timestamp,
                        "timestamp": timestamp,
                        "symbol": symbol,
                        "side": side,
                        "quantity": quantity,
                        "fill_price": fill_price,
                        "notional": quantity * fill_price,
                        "fees": fees,
                        "target_weight": 0.0,
                        "exit_reason": "intraday_session_end",
                    }
                )
        position_value = sum(quantity * last_prices.get(symbol, 0.0) for symbol, quantity in shares.items())
        gross_position_value = sum(
            abs(quantity * last_prices.get(symbol, 0.0))
            for symbol, quantity in shares.items()
        )
        equity_value = cash + position_value
        equity_rows.append(
            {
                "timestamp": timestamp,
                "equity": equity_value,
                "cash": cash,
                "gross_exposure": gross_position_value / equity_value if equity_value > 0 else 0.0,
            }
        )

        if session_index % config.rebalance_every == 0:
            pending = (timestamp, target_table.loc[timestamp].copy())

    equity = pd.DataFrame(equity_rows)
    equity["daily_return"] = equity["equity"].pct_change(fill_method=None).fillna(0.0)
    if benchmark.upper() in closes.columns:
        benchmark_return = closes[benchmark.upper()].reindex(equity["timestamp"]).pct_change(fill_method=None).fillna(0.0)
        equity["benchmark_return"] = benchmark_return.to_numpy()
    else:
        equity["benchmark_return"] = 0.0
    trades = pd.DataFrame(trade_rows, columns=TRADE_COLUMNS)
    metrics = calculate_metrics(equity, trades)
    return BacktestResult(equity=equity, trades=trades, targets=targets, metrics=metrics)


def save_result(result: BacktestResult, output_dir: str | Path) -> Path:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    result.equity.to_csv(path / "equity.csv", index=False)
    result.trades.to_csv(path / "trades.csv", index=False)
    result.targets.to_csv(path / "targets.csv", index=False)
    (path / "metrics.json").write_text(json.dumps(result.metrics, indent=2, sort_keys=True), encoding="utf-8")
    return path
