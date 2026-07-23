from __future__ import annotations

import json
from pathlib import Path
import runpy

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/research-intra-v15-improvement"
TIMEZONE = "America/New_York"
TRAIN_END = pd.Timestamp("2022-12-31").date()
VALIDATION_END = pd.Timestamp("2024-12-31").date()
TEST_END = pd.Timestamp("2026-07-17").date()


def summarize(returns: pd.Series) -> dict[str, float]:
    returns = returns.fillna(0.0).astype(float)
    equity = (1.0 + returns).cumprod()
    drawdown = equity.div(equity.cummax()).sub(1.0)
    deviation = float(returns.std(ddof=0))
    return {
        "total_return": float(equity.iloc[-1] - 1.0) if len(equity) else 0.0,
        "sharpe": (
            float(returns.mean() / deviation * np.sqrt(252.0))
            if deviation > 0
            else 0.0
        ),
        "max_drawdown": float(drawdown.min()) if len(drawdown) else 0.0,
    }


def period_summary(returns: pd.Series) -> dict[str, float]:
    periods = {
        "train": (None, TRAIN_END),
        "validation": (
            pd.Timestamp("2023-01-01").date(),
            VALIDATION_END,
        ),
        "test": (pd.Timestamp("2025-01-01").date(), TEST_END),
    }
    row: dict[str, float] = {}
    for label, (start, end) in periods.items():
        sample = returns.loc[start:end]
        metrics = summarize(sample)
        for key, value in metrics.items():
            row[f"{label}_{key}"] = value
    return row


def load_spy_and_control() -> tuple[pd.DataFrame, pd.Series]:
    helpers = runpy.run_path(str(ROOT / "scripts/intraday_v13_research.py"))
    bars, _, _ = helpers["load_research_inputs"]("iex")
    spy = bars[bars["symbol"].eq("SPY")].copy()
    spy["timestamp"] = pd.to_datetime(spy["timestamp"], utc=True)
    spy = spy.sort_values("timestamp").set_index("timestamp")
    local = spy.index.tz_convert(TIMEZONE)
    spy["session"] = local.date
    spy["clock"] = local.time
    spy = spy[spy["session"].le(TEST_END)].copy()

    equity = pd.read_csv(
        ROOT / "artifacts/research-intra-v13-pit-sip/v13_final_iex/equity.csv"
    )
    equity["timestamp"] = pd.to_datetime(equity["timestamp"], utc=True)
    equity["session"] = equity["timestamp"].dt.tz_convert(TIMEZONE).dt.date
    daily = equity.sort_values("timestamp").groupby("session").tail(1)
    control = daily.set_index("session")["equity"].pct_change(fill_method=None)
    control = control.fillna(0.0).astype(float)
    return spy, control


def signal_frame(spy: pd.DataFrame) -> pd.DataFrame:
    frame = spy.copy()
    grouped = frame.groupby("session", sort=True)
    frame["first_open"] = grouped["open"].transform("first")
    typical = (frame["high"] + frame["low"] + frame["close"]) / 3.0
    frame["vwap"] = (typical * frame["volume"]).groupby(frame["session"]).cumsum().div(
        frame["volume"].groupby(frame["session"]).cumsum()
    )
    frame["recent_30m"] = grouped["close"].pct_change(2, fill_method=None)
    frame["open_trend"] = frame["close"].div(frame["first_open"]).sub(1.0)
    frame["vwap_trend"] = frame["close"].div(frame["vwap"]).sub(1.0)

    daily = grouped.agg(first_open=("open", "first"), last_close=("close", "last"))
    daily["gap"] = daily["first_open"].div(daily["last_close"].shift()).sub(1.0)
    daily["prior_day"] = daily["last_close"].pct_change(fill_method=None).shift()
    frame["gap"] = frame["session"].map(daily["gap"])
    frame["prior_day"] = frame["session"].map(daily["prior_day"])
    frame["next_open"] = grouped["open"].shift(-1)
    return frame


def sleeve_returns(
    frame: pd.DataFrame,
    feature: str,
    signal_time: str,
    exit_time: str,
    direction: str,
    threshold_bps: float,
    long_weight: float,
    short_weight: float,
    weak_weight: float,
    cost_bps: float,
) -> tuple[pd.Series, pd.Series]:
    signal_clock = pd.Timestamp(signal_time).time()
    exit_clock = pd.Timestamp(exit_time).time()
    slip = cost_bps / 10_000.0
    threshold = threshold_bps / 10_000.0
    signal = (
        frame.loc[frame["clock"].eq(signal_clock), ["session", feature, "next_open"]]
        .drop_duplicates("session", keep="last")
        .set_index("session")
    )
    exit_prices = (
        frame.loc[frame["clock"].eq(exit_clock), ["session", "next_open"]]
        .drop_duplicates("session", keep="last")
        .set_index("session")["next_open"]
        .rename("exit_open")
    )
    joined = signal.join(exit_prices, how="left")
    score = joined[feature].astype(float)
    strong = score.abs().ge(threshold)
    side = np.sign(score)
    if direction == "reversal":
        side *= -1.0
    entry = joined["next_open"].astype(float)
    exit_ = joined["exit_open"].astype(float)
    valid = score.notna() & entry.gt(0.0) & exit_.gt(0.0)
    side = side.where(valid & (strong | (weak_weight > 0.0)), 0.0)
    long_size = pd.Series(
        np.where(strong, long_weight, weak_weight), index=joined.index
    )
    short_size = pd.Series(
        np.where(strong, short_weight, weak_weight), index=joined.index
    )
    long_return = exit_.mul(1.0 - slip).div(entry.mul(1.0 + slip)).sub(1.0)
    short_return = entry.mul(1.0 - slip).sub(exit_.mul(1.0 + slip)).div(entry)
    sleeve = pd.Series(
        np.select(
            [side.gt(0.0), side.lt(0.0)],
            [long_size * long_return, short_size * short_return],
            default=0.0,
        ),
        index=joined.index,
        dtype=float,
    )
    return sleeve, side.astype(float)


def evaluate(
    frame: pd.DataFrame,
    control: pd.Series,
    **parameters,
) -> tuple[dict, pd.Series]:
    sleeve, directions = sleeve_returns(frame, **parameters)
    common = control.index.intersection(sleeve.index)
    combined = control.reindex(common).fillna(0.0) + sleeve.reindex(common).fillna(0.0)
    active = directions.reindex(common).fillna(0.0).ne(0.0)
    row = {
        **parameters,
        **summarize(combined),
        **period_summary(combined),
        "active_rate": float(active.mean()),
        "long_rate": float(directions.reindex(common).gt(0).mean()),
        "short_rate": float(directions.reindex(common).lt(0).mean()),
    }
    row["selection_score"] = min(row["train_sharpe"], row["validation_sharpe"])
    return row, combined


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    spy, control = load_spy_and_control()
    frame = signal_frame(spy)
    rows = []
    for feature in ["open_trend", "recent_30m", "vwap_trend", "gap", "prior_day"]:
        for signal_time in ["09:45", "10:00", "10:15", "10:30", "11:00"]:
            for exit_time in ["15:15", "15:30", "15:45"]:
                for direction in ["trend", "reversal"]:
                    for threshold_bps in [0.0, 5.0, 10.0, 20.0]:
                        row, _ = evaluate(
                            frame,
                            control,
                            feature=feature,
                            signal_time=signal_time,
                            exit_time=exit_time,
                            direction=direction,
                            threshold_bps=threshold_bps,
                            long_weight=0.05,
                            short_weight=0.05,
                            weak_weight=0.0,
                            cost_bps=2.0,
                        )
                        rows.append(row)
    screen = pd.DataFrame(rows).sort_values(
        ["selection_score", "validation_sharpe", "train_sharpe"],
        ascending=False,
    )
    screen.to_csv(OUTPUT / "screen.csv", index=False)

    eligible = screen[
        screen["active_rate"].ge(0.80)
        & screen["train_total_return"].gt(0.0)
        & screen["validation_total_return"].gt(0.0)
    ]
    selected = eligible.iloc[0].to_dict() if len(eligible) else screen.iloc[0].to_dict()
    fixed = {
        key: selected[key]
        for key in [
            "feature",
            "signal_time",
            "exit_time",
            "direction",
            "threshold_bps",
        ]
    }
    stress_rows = []
    stress_returns = None
    for long_weight, short_weight in [
        (0.025, 0.025),
        (0.05, 0.025),
        (0.05, 0.05),
        (0.075, 0.025),
    ]:
        for cost_bps in [0.5, 1.0, 2.0, 4.0, 8.0]:
            row, returns = evaluate(
                frame,
                control,
                **fixed,
                long_weight=long_weight,
                short_weight=short_weight,
                weak_weight=0.0,
                cost_bps=cost_bps,
            )
            stress_rows.append(row)
            if long_weight == 0.05 and short_weight == 0.025 and cost_bps == 2.0:
                stress_returns = returns
    stress = pd.DataFrame(stress_rows)
    stress.to_csv(OUTPUT / "selected-cost-and-sizing-stress.csv", index=False)

    fallback_rows = []
    for weak_weight in [0.0, 0.005, 0.01, 0.015, 0.025]:
        row, _ = evaluate(
            frame,
            control,
            **fixed,
            long_weight=0.05,
            short_weight=0.025,
            weak_weight=weak_weight,
            cost_bps=2.0,
        )
        fallback_rows.append(row)
    fallback = pd.DataFrame(fallback_rows).sort_values(
        "selection_score", ascending=False
    )
    fallback.to_csv(OUTPUT / "weak-signal-fallback-screen.csv", index=False)
    daily_fallback = fallback[fallback["active_rate"].ge(0.98)].iloc[0]
    daily_cost_rows = []
    for cost_bps in [0.5, 1.0, 2.0, 4.0, 8.0]:
        row, returns = evaluate(
            frame,
            control,
            **fixed,
            long_weight=0.05,
            short_weight=0.025,
            weak_weight=float(daily_fallback["weak_weight"]),
            cost_bps=cost_bps,
        )
        daily_cost_rows.append(row)
        if cost_bps == 2.0:
            stress_returns = returns
    pd.DataFrame(daily_cost_rows).to_csv(
        OUTPUT / "daily-fallback-cost-stress.csv", index=False
    )
    if stress_returns is not None:
        pd.DataFrame(
            {
                "session": stress_returns.index,
                "combined_return": stress_returns.values,
                "equity": (1.0 + stress_returns).cumprod().values,
            }
        ).to_csv(OUTPUT / "selected-equity.csv", index=False)
    selected_parameters = {
        **fixed,
        "long_weight": 0.05,
        "short_weight": 0.025,
        "weak_weight": float(daily_fallback["weak_weight"]),
        "decision_cost_bps": 2.0,
    }
    (OUTPUT / "selection.json").write_text(
        json.dumps(selected_parameters, indent=2, sort_keys=True), encoding="utf-8"
    )
    print("Selected pre-2025 candidate:")
    print(json.dumps(fixed, indent=2, sort_keys=True))
    print(
        stress.sort_values(["cost_bps", "selection_score"], ascending=[True, False])[
            [
                "long_weight",
                "short_weight",
                "cost_bps",
                "total_return",
                "sharpe",
                "max_drawdown",
                "train_total_return",
                "validation_total_return",
                "test_total_return",
                "active_rate",
            ]
        ].to_string(index=False)
    )
    print("Daily weak-signal fallback screen at two basis points:")
    print(
        fallback[
            [
                "weak_weight",
                "total_return",
                "sharpe",
                "max_drawdown",
                "train_sharpe",
                "validation_sharpe",
                "test_sharpe",
                "active_rate",
            ]
        ].to_string(index=False)
    )
    print("Selected daily fallback cost stress:")
    print(
        pd.DataFrame(daily_cost_rows)[
            [
                "weak_weight",
                "cost_bps",
                "total_return",
                "sharpe",
                "max_drawdown",
                "train_total_return",
                "validation_total_return",
                "test_total_return",
                "active_rate",
            ]
        ].to_string(index=False)
    )


if __name__ == "__main__":
    main()
