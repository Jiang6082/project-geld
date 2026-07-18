from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export the latest point-in-time selected universe snapshot."
    )
    parser.add_argument(
        "--selected",
        default="artifacts/research-broad/monthly-selected-universe.csv.gz",
    )
    parser.add_argument(
        "--output", default="artifacts/paper-v4-shadow/universe.csv"
    )
    args = parser.parse_args()

    selected = pd.read_csv(args.selected)
    if not {"timestamp", "symbol"}.issubset(selected.columns):
        raise ValueError("Selected-universe data must contain timestamp and symbol.")
    timestamps = pd.to_datetime(selected["timestamp"], utc=True)
    latest = timestamps.max()
    snapshot = selected[timestamps.eq(latest)].copy()
    snapshot["timestamp"] = latest.isoformat()
    snapshot = snapshot.sort_values(
        "liquidity_rank" if "liquidity_rank" in snapshot.columns else "symbol"
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    snapshot.to_csv(output, index=False)
    print(
        f"exported {len(snapshot)} symbols as of {latest.date()} to {output.resolve()}"
    )


if __name__ == "__main__":
    main()
