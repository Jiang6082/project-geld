from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import time

import numpy as np
import pandas as pd

from project_geld.strategies.base import TARGET_COLUMNS, close_matrix
from project_geld.strategies.intra_v8 import IntraV8


def _clock(value: str) -> time:
    return time.fromisoformat(value)


FEATURE_COLUMNS = [
    "relative_dislocation",
    "session_relative_return",
    "vwap_distance",
    "signal_relative_volume",
    "prior_trend_gap",
    "confirmation_return",
    "benchmark_session_return",
]


@dataclass
class IntraV11(IntraV8):
    """V8 setup filtered by a causal rolling ridge return forecast.

    The estimator is refit once per session using only fully completed earlier
    sessions. It predicts the raw stock return from the next bar's open to the
    session close; only candidates with a sufficiently negative forecast are
    shorted.
    """

    training_window_sessions: int = 504
    min_training_sessions: int = 252
    min_training_samples: int = 5_000
    model_relative_volume_sessions: int = 20
    ridge_alpha: float = 25.0
    prediction_threshold: float = -0.0016
    min_calibration_samples: int = 12
    calibration_clip: float = 0.05
    label_winsor: float = 0.10
    feature_clip: float = 10.0
    name: str = "intra_v11"

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.training_window_sessions < 1 or self.min_training_sessions < 1:
            raise ValueError("Training-window settings must be positive.")
        if self.min_training_sessions > self.training_window_sessions:
            raise ValueError("min_training_sessions cannot exceed the training window.")
        if self.min_training_samples < 1 or self.min_calibration_samples < 1:
            raise ValueError("Training and calibration sample counts must be positive.")
        if self.model_relative_volume_sessions < 1:
            raise ValueError("model_relative_volume_sessions must be positive.")
        if (
            self.ridge_alpha < 0
            or self.label_winsor <= 0
            or self.feature_clip <= 0
            or self.calibration_clip <= 0
        ):
            raise ValueError("Model regularization and clipping settings are invalid.")
        if self.prediction_threshold > 0:
            raise ValueError("prediction_threshold must be non-positive for short entries.")

    @staticmethod
    def fit_predict(
        training: pd.DataFrame,
        current: pd.DataFrame,
        ridge_alpha: float,
        label_winsor: float,
        feature_clip: float,
    ) -> pd.Series:
        """Fit standardized ridge regression and predict current rows."""
        required = [*FEATURE_COLUMNS, "label"]
        clean = training[required].replace([np.inf, -np.inf], np.nan).dropna()
        current_x = current[FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan)
        predictions = pd.Series(np.nan, index=current.index, dtype=float)
        valid_current = current_x.notna().all(axis=1)
        if clean.empty or not valid_current.any():
            return predictions

        x = clean[FEATURE_COLUMNS].to_numpy(dtype=float)
        y = clean["label"].clip(-label_winsor, label_winsor).to_numpy(dtype=float)
        means = x.mean(axis=0)
        scales = x.std(axis=0)
        scales[scales < 1e-12] = 1.0
        z = np.clip((x - means) / scales, -feature_clip, feature_clip)
        y_mean = float(y.mean())
        penalty = ridge_alpha * np.eye(z.shape[1])
        beta = np.linalg.solve(z.T @ z + penalty, z.T @ (y - y_mean))
        current_z = np.clip(
            (current_x.loc[valid_current].to_numpy(dtype=float) - means) / scales,
            -feature_clip,
            feature_clip,
        )
        predictions.loc[valid_current] = y_mean + current_z @ beta
        return predictions

    def generate_targets(self, bars: pd.DataFrame) -> pd.DataFrame:
        if bars.empty:
            return pd.DataFrame(columns=TARGET_COLUMNS)
        close = close_matrix(bars)
        if self.benchmark_symbol not in close:
            raise ValueError(f"{self.benchmark_symbol} bars are required as context.")
        matrices = {
            column: bars.pivot(index="timestamp", columns="symbol", values=column)
            .sort_index()
            .reindex_like(close)
            for column in ["open", "high", "low", "volume"]
        }
        open_price = matrices["open"]
        low = matrices["low"]
        volume = matrices["volume"]
        typical = bars.assign(
            typical=(bars["high"] + bars["low"] + bars["close"]) / 3.0
        ).pivot(index="timestamp", columns="symbol", values="typical").reindex_like(close)

        local_index = close.index.tz_convert(self.timezone)
        sessions = pd.Series(local_index.date, index=close.index)
        session_dates = list(dict.fromkeys(sessions.tolist()))
        session_close = close.groupby(sessions).last()
        prior_close = session_close.shift(1)
        prior_average = prior_close.rolling(
            self.daily_trend_sessions,
            min_periods=self.daily_trend_sessions,
        ).mean()
        first_open = open_price.groupby(sessions).first()
        horizon_return = close.groupby(sessions).pct_change(
            self.lookback_bars, fill_method=None
        )
        relative = horizon_return.sub(horizon_return[self.benchmark_symbol], axis=0)
        cumulative_value = (typical * volume).groupby(sessions).cumsum()
        cumulative_volume = volume.groupby(sessions).cumsum().replace(0, np.nan)
        vwap = cumulative_value / cumulative_volume
        dollar_volume = close * volume
        tradables = [symbol for symbol in close.columns if symbol != self.benchmark_symbol]

        signal_rows = {
            timestamp.tz_convert(self.timezone).date(): timestamp
            for timestamp in close.index
            if timestamp.tz_convert(self.timezone).time().replace(tzinfo=None)
            == _clock(self.signal_time)
        }
        bar_minutes = self._infer_bar_minutes(close.index)
        confirmation_time = (
            pd.Timestamp.combine(pd.Timestamp.today(), _clock(self.signal_time))
            + pd.Timedelta(bar_minutes * self.confirmation_bars, unit="m")
        ).time()
        confirmation_rows = {
            timestamp.tz_convert(self.timezone).date(): timestamp
            for timestamp in close.index
            if timestamp.tz_convert(self.timezone).time().replace(tzinfo=None)
            == confirmation_time
        }
        next_rows: dict[object, pd.Timestamp] = {}
        for session_date, confirmation_timestamp in confirmation_rows.items():
            later = close.index[(sessions.eq(session_date)) & (close.index > confirmation_timestamp)]
            if len(later):
                next_rows[session_date] = later[0]

        signal_volume = volume.loc[list(signal_rows.values())].copy()
        signal_volume.index = list(signal_rows.keys())
        prior_signal_volume = signal_volume.shift(1).rolling(
            self.model_relative_volume_sessions,
            min_periods=self.model_relative_volume_sessions,
        ).median()

        feature_frames: dict[object, pd.DataFrame] = {}
        candidate_symbols: dict[object, list[str]] = {}
        market_ok: dict[object, bool] = {}
        for session_date in session_dates:
            if (
                session_date not in signal_rows
                or session_date not in confirmation_rows
                or session_date not in next_rows
                or session_date not in prior_close.index
            ):
                continue
            signal_timestamp = signal_rows[session_date]
            confirmation_timestamp = confirmation_rows[session_date]
            next_timestamp = next_rows[session_date]
            dislocation = -relative.loc[signal_timestamp, tradables]
            stock_session_return = close.loc[confirmation_timestamp, tradables].div(
                first_open.loc[session_date, tradables]
            ) - 1.0
            benchmark_session_return = (
                close.at[confirmation_timestamp, self.benchmark_symbol]
                / first_open.at[session_date, self.benchmark_symbol]
                - 1.0
            )
            frame = pd.DataFrame(index=tradables)
            frame["relative_dislocation"] = dislocation
            frame["session_relative_return"] = (
                stock_session_return - benchmark_session_return
            )
            frame["vwap_distance"] = (
                close.loc[confirmation_timestamp, tradables]
                / vwap.loc[confirmation_timestamp, tradables]
                - 1.0
            )
            frame["signal_relative_volume"] = signal_volume.loc[
                session_date, tradables
            ].div(prior_signal_volume.loc[session_date, tradables])
            frame["prior_trend_gap"] = (
                prior_close.loc[session_date, tradables]
                / prior_average.loc[session_date, tradables]
                - 1.0
            )
            frame["confirmation_return"] = (
                close.loc[confirmation_timestamp, tradables]
                / close.loc[signal_timestamp, tradables]
                - 1.0
            )
            frame["benchmark_session_return"] = benchmark_session_return
            frame["label"] = (
                session_close.loc[session_date, tradables]
                / open_price.loc[next_timestamp, tradables]
                - 1.0
            )
            feature_frames[session_date] = frame

            liquid = dollar_volume.loc[signal_timestamp, tradables].ge(
                self.min_bar_dollar_volume
            )
            setup = (
                dislocation.ge(self.min_relative_dislocation)
                & close.loc[signal_timestamp, tradables].le(
                    vwap.loc[signal_timestamp, tradables]
                )
                & prior_close.loc[session_date, tradables].lt(
                    prior_average.loc[session_date, tradables]
                )
                & close.loc[signal_timestamp, tradables].lt(
                    prior_close.loc[session_date, tradables]
                )
                & close.loc[confirmation_timestamp, tradables].lt(
                    low.loc[signal_timestamp, tradables]
                )
            )
            candidate_symbols[session_date] = list(dislocation[liquid & setup].dropna().index)
            market_ok[session_date] = bool(
                close.at[confirmation_timestamp, self.benchmark_symbol]
                >= vwap.at[confirmation_timestamp, self.benchmark_symbol]
                if self.require_benchmark_above_vwap
                else True
            )

        selected_by_session: dict[object, list[str]] = {}
        predictions_by_session: dict[object, pd.Series] = {}
        raw_predictions_by_session: dict[object, pd.Series] = {}
        history: deque[pd.DataFrame] = deque(maxlen=self.training_window_sessions)
        history_sessions: deque[object] = deque(maxlen=self.training_window_sessions)
        calibration_history: deque[pd.Series] = deque(
            maxlen=self.training_window_sessions
        )
        previous_date = None
        for session_date in session_dates:
            if previous_date in feature_frames:
                history.append(feature_frames[previous_date])
                history_sessions.append(previous_date)
                previous_predictions = raw_predictions_by_session.get(previous_date)
                if previous_predictions is not None:
                    previous_candidates = candidate_symbols.get(previous_date, [])
                    residuals = (
                        feature_frames[previous_date]["label"].reindex(
                            previous_candidates
                        )
                        - previous_predictions.reindex(previous_candidates)
                    ).replace([np.inf, -np.inf], np.nan).dropna()
                    if len(residuals):
                        calibration_history.append(residuals)
            previous_date = session_date
            if session_date not in feature_frames:
                continue
            current = feature_frames[session_date]
            raw_predictions = pd.Series(np.nan, index=current.index, dtype=float)
            if len(history_sessions) >= self.min_training_sessions:
                training = pd.concat(list(history), axis=0)
                clean_count = len(
                    training[[*FEATURE_COLUMNS, "label"]]
                    .replace([np.inf, -np.inf], np.nan)
                    .dropna()
                )
                if clean_count >= self.min_training_samples:
                    raw_predictions = self.fit_predict(
                        training,
                        current,
                        self.ridge_alpha,
                        self.label_winsor,
                        self.feature_clip,
                    )
            raw_predictions_by_session[session_date] = raw_predictions
            predictions = pd.Series(np.nan, index=current.index, dtype=float)
            if calibration_history:
                calibration = pd.concat(list(calibration_history)).dropna()
                if len(calibration) >= self.min_calibration_samples:
                    adjustment = float(
                        calibration.clip(
                            -self.calibration_clip, self.calibration_clip
                        ).median()
                    )
                    predictions = raw_predictions + adjustment
            predictions_by_session[session_date] = predictions
            candidates = candidate_symbols.get(session_date, [])
            qualified = predictions.reindex(candidates).dropna()
            qualified = qualified[qualified.le(self.prediction_threshold)].sort_values()
            selected_by_session[session_date] = (
                list(qualified.index[: self.top_n])
                if market_ok.get(session_date, False)
                else []
            )

        records: list[dict] = []
        for timestamp in close.index:
            session_date = timestamp.tz_convert(self.timezone).date()
            local_time = timestamp.tz_convert(self.timezone).time().replace(tzinfo=None)
            selected = selected_by_session.get(session_date, [])
            active = selected if confirmation_time <= local_time < _clock(self.flatten_at) else []
            weight = min(
                self.max_position_weight,
                self.gross_exposure / len(active) if active else 0.0,
            )
            predictions = predictions_by_session.get(
                session_date, pd.Series(dtype=float)
            )
            for symbol in tradables:
                score = predictions.get(symbol, np.nan)
                records.append(
                    {
                        "timestamp": timestamp,
                        "symbol": symbol,
                        "target_weight": -weight if symbol in active else 0.0,
                        "score": float(score) if pd.notna(score) else float("nan"),
                    }
                )
        return pd.DataFrame.from_records(records, columns=TARGET_COLUMNS)
