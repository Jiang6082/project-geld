from __future__ import annotations

from typing import Protocol

import numpy as np
import pandas as pd


TARGET_COLUMNS = ["timestamp", "symbol", "target_weight", "score"]


class Strategy(Protocol):
    name: str

    @property
    def warmup_bars(self) -> int: ...

    def generate_targets(self, bars: pd.DataFrame) -> pd.DataFrame: ...


def close_matrix(bars: pd.DataFrame) -> pd.DataFrame:
    return bars.pivot(index="timestamp", columns="symbol", values="close").sort_index()


def ranked_long_only_targets(
    score: pd.DataFrame,
    eligible: pd.DataFrame,
    top_n: int,
    gross_exposure: float,
) -> pd.DataFrame:
    if top_n < 1:
        raise ValueError("top_n must be at least 1.")
    if not 0 < gross_exposure <= 1:
        raise ValueError("gross_exposure must be in (0, 1].")

    records: list[dict] = []
    for timestamp in score.index:
        day_score = score.loc[timestamp].replace([float("inf"), float("-inf")], np.nan).dropna()
        day_eligible = eligible.loc[timestamp].reindex(day_score.index).fillna(False).astype(bool)
        candidates = day_score[day_eligible & day_score.gt(0)].nlargest(top_n)
        weight = gross_exposure / len(candidates) if len(candidates) else 0.0
        selected = set(candidates.index)
        for symbol in score.columns:
            value = score.at[timestamp, symbol]
            records.append(
                {
                    "timestamp": timestamp,
                    "symbol": symbol,
                    "target_weight": weight if symbol in selected else 0.0,
                    "score": float(value) if pd.notna(value) else float("nan"),
                }
            )
    return pd.DataFrame.from_records(records, columns=TARGET_COLUMNS)
