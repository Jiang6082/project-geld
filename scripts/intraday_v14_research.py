from __future__ import annotations

from dataclasses import replace
import gc
import json
from pathlib import Path
import runpy

import pandas as pd

from project_geld.config import load_config
from project_geld.intraday import run_intraday_backtest
from project_geld.research import period_metrics
from project_geld.strategies.registry import create_strategy


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/research-intra-v14-pit"
V12_OUTPUT = ROOT / "artifacts/research-intra-v12-pit"
COMMON_START = pd.Timestamp("2020-07-27", tz="UTC")
COMMON_END = pd.Timestamp("2026-07-17 20:00:00", tz="UTC")


def load_inputs(feed: str, matched_period: bool = True):
    helpers = runpy.run_path(
        str(ROOT / "scripts/intraday_v12_pit_validation.py")
    )
    membership = helpers["load_membership"]()
    v12_config = load_config(ROOT / "configs/research-intra-v12-broad.toml")
    config = load_config(ROOT / "configs/research-intra-v14-pit.toml")
    symbols = json.loads(
        (V12_OUTPUT / "intraday-symbols.json").read_text(encoding="utf-8")
    )
    bars = helpers["assemble_feed"](
        feed,
        symbols,
        membership,
        set(v12_config.universe.symbols),
    )
    if matched_period:
        bars = bars[
            bars["timestamp"].between(COMMON_START, COMMON_END, inclusive="both")
        ].copy()
    bars = bars[
        bars["symbol"].isin(set(membership) | {config.universe.benchmark})
    ].copy()
    return bars, membership, config


def position_frame(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(
            columns=["session", "symbol", "net_pnl", "orders", "gross_notional"]
        )
    frame = trades[trades["quantity"].gt(0)].copy()
    frame["session"] = (
        pd.to_datetime(frame["timestamp"], utc=True)
        .dt.tz_convert("America/New_York")
        .dt.date
    )
    frame["cash_flow"] = frame["notional"].where(
        frame["side"].eq("sell"), -frame["notional"]
    ) - frame["fees"]
    return (
        frame.groupby(["session", "symbol"], as_index=False)
        .agg(
            net_pnl=("cash_flow", "sum"),
            orders=("side", "size"),
            gross_notional=("notional", "sum"),
        )
    )


def diagnostics(result, positions: pd.DataFrame) -> dict[str, float]:
    equity = result.equity.copy()
    equity["timestamp"] = pd.to_datetime(equity["timestamp"], utc=True)
    equity["session"] = (
        equity["timestamp"].dt.tz_convert("America/New_York").dt.date
    )
    daily = equity.sort_values("timestamp").groupby("session").tail(1)
    daily_returns = daily["equity"].pct_change(fill_method=None).dropna()
    active = daily_returns[daily_returns.abs().gt(1e-12)]
    position_returns = positions["net_pnl"].div(
        positions["gross_notional"].div(2.0).replace(0, pd.NA)
    ).dropna()
    return {
        "sessions": len(daily_returns),
        "active_sessions": len(active),
        "active_session_rate": len(active) / len(daily_returns)
        if len(daily_returns)
        else 0.0,
        "active_daily_skew": active.skew() if len(active) > 2 else float("nan"),
        "position_return_skew": position_returns.skew()
        if len(position_returns) > 2
        else float("nan"),
        "positions": len(positions),
        "win_rate": positions["net_pnl"].gt(0).mean() if len(positions) else 0.0,
    }


def run_variant(
    label: str,
    bars: pd.DataFrame,
    membership: dict,
    config,
    overrides: dict,
    slippage_bps: float = 8.0,
    save: bool = False,
) -> dict:
    parameters = {
        **config.strategy.parameters,
        **overrides,
        "membership_periods": membership,
    }
    strategy = create_strategy("intra_v14", parameters)
    backtest = replace(config.backtest, slippage_bps=slippage_bps)
    result = run_intraday_backtest(
        bars,
        strategy,
        backtest,
        config.risk,
        config.universe.benchmark,
        sorted(membership),
        strategy.context_symbols,
    )
    positions = position_frame(result.trades)
    row = {
        "label": label,
        "slippage_bps": slippage_bps,
        **result.metrics,
        **diagnostics(result, positions),
    }
    for period, start, end in [
        ("train", "2020-07-27", "2022-12-31"),
        ("validation", "2023-01-01", "2024-12-31"),
        ("test", "2025-01-01", "2026-07-17"),
    ]:
        metrics = period_metrics(result, start, end)
        row[f"{period}_return"] = metrics["total_return"]
        row[f"{period}_sharpe"] = metrics["sharpe"]
        row[f"{period}_max_drawdown"] = metrics["max_drawdown"]
    if save:
        directory = OUTPUT / label
        directory.mkdir(parents=True, exist_ok=True)
        result.equity.to_csv(directory / "equity.csv", index=False)
        result.trades.to_csv(directory / "trades.csv", index=False)
        positions.to_csv(directory / "positions.csv", index=False)
        (directory / "metrics.json").write_text(
            json.dumps(row, indent=2, sort_keys=True), encoding="utf-8"
        )
    print(
        f"{label}: return={row['total_return']:.2%}, "
        f"sharpe={row['sharpe']:.3f}, dd={row['max_drawdown']:.2%}, "
        f"active={row['active_session_rate']:.1%}",
        flush=True,
    )
    del result, positions, strategy
    gc.collect()
    return row


def _summary(returns: pd.Series) -> dict[str, float]:
    returns = returns.fillna(0.0).astype(float)
    equity = (1.0 + returns).cumprod()
    drawdown = equity.div(equity.cummax()).sub(1.0)
    years = len(returns) / 252.0
    total_return = float(equity.iloc[-1] - 1.0) if len(equity) else 0.0
    cagr = (float(equity.iloc[-1]) ** (1.0 / years) - 1.0) if years > 0 else 0.0
    volatility = float(returns.std(ddof=0) * (252.0**0.5))
    sharpe = (
        float(returns.mean() / returns.std(ddof=0) * (252.0**0.5))
        if returns.std(ddof=0) > 0
        else 0.0
    )
    return {
        "total_return": total_return,
        "cagr": cagr,
        "annual_volatility": volatility,
        "sharpe": sharpe,
        "max_drawdown": float(drawdown.min()) if len(drawdown) else 0.0,
    }


def prepare_fast_data(bars: pd.DataFrame, config) -> dict:
    close = bars.pivot(index="timestamp", columns="symbol", values="close").sort_index()
    open_ = bars.pivot(index="timestamp", columns="symbol", values="open").reindex_like(close)
    volume = bars.pivot(index="timestamp", columns="symbol", values="volume").reindex_like(close)
    local = close.index.tz_convert(config.backtest.session_timezone)
    sessions = pd.Series(local.date, index=close.index)
    return {
        "close": close,
        "open": open_,
        "sessions": sessions,
        "times": pd.Series(
            [item.time().replace(tzinfo=None) for item in local], index=close.index
        ),
        "cumulative_dollar_volume": (close * volume).groupby(sessions).cumsum(),
        "tradables": [
            item for item in close.columns if item != config.universe.benchmark
        ],
    }


def fast_screen_variant(
    label: str,
    prepared: dict,
    relative: pd.DataFrame,
    membership: dict,
    config,
    direction: str,
    lookback_bars: int,
    slippage_bps: float = 8.0,
) -> dict:
    close = prepared["close"]
    open_ = prepared["open"]
    sessions = prepared["sessions"]
    times = prepared["times"]
    cumulative_dollar_volume = prepared["cumulative_dollar_volume"]
    tradables = prepared["tradables"]
    signal_clock = pd.Timestamp(config.strategy.parameters["signal_time"]).time()
    flatten_clock = pd.Timestamp(config.strategy.parameters["flatten_at"]).time()
    slip = slippage_bps / 10_000.0
    daily_returns: dict[object, float] = {}
    position_returns: list[float] = []
    positions = 0

    for session_date, session_index in close.groupby(sessions).groups.items():
        timestamps = pd.DatetimeIndex(session_index)
        signal_rows = [item for item in timestamps if times.at[item] == signal_clock]
        flatten_rows = [item for item in timestamps if times.at[item] == flatten_clock]
        daily_returns[session_date] = 0.0
        if not signal_rows or not flatten_rows:
            continue
        signal = signal_rows[-1]
        signal_location = close.index.get_loc(signal)
        flatten_location = close.index.get_loc(flatten_rows[-1])
        if signal_location + 1 >= len(close.index) or flatten_location + 1 >= len(close.index):
            continue
        entry = close.index[signal_location + 1]
        exit_ = close.index[flatten_location + 1]
        if sessions.at[entry] != session_date or sessions.at[exit_] != session_date:
            continue
        date = pd.Timestamp(session_date).date()
        members = pd.Series(
            {
                symbol: any(
                    pd.Timestamp(start).date() <= date
                    and (end is None or date <= pd.Timestamp(end).date())
                    for start, end in membership.get(symbol.upper(), [])
                )
                for symbol in tradables
            },
            dtype=bool,
        )
        liquid = cumulative_dollar_volume.loc[signal, tradables].ge(
            config.strategy.parameters["min_cumulative_dollar_volume"]
        )
        ranked = relative.loc[signal, tradables][members & liquid].dropna()
        if len(ranked) < 2:
            continue
        side_count = min(
            config.strategy.parameters["names_per_side"], len(ranked) // 2
        )
        low = ranked.nsmallest(side_count).index.tolist()
        high = ranked.nlargest(side_count).index.tolist()
        longs, shorts = (high, low) if direction == "momentum" else (low, high)
        selected = [*longs, *shorts]
        entry_prices = open_.loc[entry, selected]
        exit_prices = open_.loc[exit_, selected]
        valid = entry_prices.gt(0) & exit_prices.gt(0)
        longs = [item for item in longs if bool(valid.get(item, False))]
        shorts = [item for item in shorts if bool(valid.get(item, False))]
        active_count = len(longs) + len(shorts)
        if not active_count:
            continue
        weight = min(
            config.strategy.parameters["max_position_weight"],
            config.strategy.parameters["gross_exposure"] / active_count,
        )
        contribution = 0.0
        for symbol in longs:
            entry_price = float(open_.at[entry, symbol])
            position_return = (
                float(open_.at[exit_, symbol]) * (1.0 - slip)
                - entry_price * (1.0 + slip)
            ) / entry_price
            contribution += weight * position_return
            position_returns.append(position_return)
        for symbol in shorts:
            entry_price = float(open_.at[entry, symbol])
            position_return = (
                entry_price * (1.0 - slip)
                - float(open_.at[exit_, symbol]) * (1.0 + slip)
            ) / entry_price
            contribution += weight * position_return
            position_returns.append(position_return)
        daily_returns[session_date] = contribution
        positions += active_count

    returns = pd.Series(daily_returns, dtype=float).sort_index()
    active = returns[returns.abs().gt(1e-12)]
    row = {
        "label": label,
        "direction": direction,
        "lookback_bars": lookback_bars,
        "slippage_bps": slippage_bps,
        **_summary(returns),
        "sessions": len(returns),
        "active_sessions": len(active),
        "active_session_rate": len(active) / len(returns) if len(returns) else 0.0,
        "active_daily_skew": active.skew() if len(active) > 2 else float("nan"),
        "position_return_skew": pd.Series(position_returns).skew(),
        "positions": positions,
        "win_rate": pd.Series(position_returns).gt(0).mean(),
        "annual_turnover": 2.0
        * config.strategy.parameters["gross_exposure"]
        * len(active)
        / len(returns)
        * 252.0,
    }
    for period, start, end in [
        ("train", "2020-07-27", "2022-12-31"),
        ("validation", "2023-01-01", "2024-12-31"),
        ("test", "2025-01-01", "2026-07-17"),
    ]:
        subset = returns.loc[pd.Timestamp(start).date() : pd.Timestamp(end).date()]
        metrics = _summary(subset)
        row[f"{period}_return"] = metrics["total_return"]
        row[f"{period}_sharpe"] = metrics["sharpe"]
        row[f"{period}_max_drawdown"] = metrics["max_drawdown"]
    print(
        f"{label}: return={row['total_return']:.2%}, "
        f"sharpe={row['sharpe']:.3f}, dd={row['max_drawdown']:.2%}, "
        f"active={row['active_session_rate']:.1%}",
        flush=True,
    )
    return row


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    iex_bars, membership, config = load_inputs("iex")
    prepared = prepare_fast_data(iex_bars, config)
    del iex_bars
    gc.collect()
    screen = []
    close = prepared["close"]
    sessions = prepared["sessions"]
    for lookback in [1, 2, 3]:
        horizon = close.groupby(sessions).pct_change(lookback, fill_method=None)
        relative = horizon.sub(horizon[config.universe.benchmark], axis=0)
        for slippage_bps in [0.0, 2.0, 4.0, 8.0]:
            for direction in ["momentum", "reversal"]:
                label = (
                    f"iex_{direction}_lookback_{lookback}_"
                    f"cost_{int(slippage_bps)}bps"
                )
                screen.append(
                    fast_screen_variant(
                        label,
                        prepared,
                        relative,
                        membership,
                        config,
                        direction,
                        lookback,
                        slippage_bps=slippage_bps,
                    )
                )
    pd.DataFrame(screen).to_csv(OUTPUT / "screen-results.csv", index=False)


if __name__ == "__main__":
    main()
