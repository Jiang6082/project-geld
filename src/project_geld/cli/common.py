"""Helpers shared by the command modules."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

import pandas as pd

from project_geld import provenance
from project_geld.config import AppConfig
from project_geld.data import (
    AlpacaBarSource,
    CachedBarSource,
    CsvBarSource,
    SyntheticBarSource,
    default_date_range,
)
from project_geld.logs import get_logger

log = get_logger(__name__)


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


def _report_ownership(snapshot, orphans: list[str], latest_prices: dict) -> None:
    """Print positions the planner cannot or will not manage."""
    unpriced = [symbol for symbol in orphans if symbol not in latest_prices]
    held_orphans = [
        symbol for symbol in orphans if abs(float(snapshot.positions.get(symbol, 0.0))) > 1e-12
    ]
    if held_orphans:
        log.info(
            "Outside-universe positions scheduled for liquidation at the next "
            f"rebalance: {', '.join(held_orphans)}"
        )
    if unpriced:
        log.warning(
            "Warning: no recent bars for ledger symbol(s) "
            f"{', '.join(unpriced)}; they cannot be priced or liquidated automatically "
            "and still count against gross exposure."
        )
    unmanaged = sorted(set(snapshot.unmanaged_symbols) - set(unpriced))
    if unmanaged:
        log.warning(
            f"Warning: {len(unmanaged)} position(s) are outside the "
            f"strategy universe and its ledger ({', '.join(unmanaged)}). "
            "They are never traded and reduce available gross exposure. If they belong "
            "to this strategy, run `geld adopt-positions --symbols ...` to have them liquidated."
        )


def _priced(symbols: list[str], latest_prices: dict) -> list[str]:
    """Symbols the planner can price. Unpriced orphans stay unmanaged, so their
    market value keeps counting against available gross exposure."""
    return [symbol for symbol in symbols if symbol in latest_prices]


def _latest_closes(bars: pd.DataFrame) -> dict[str, float]:
    return (
        bars.sort_values("timestamp")
        .groupby("symbol", as_index=False)
        .tail(1)
        .set_index("symbol")["close"]
        .astype(float)
        .to_dict()
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
    log.info(json.dumps(metrics, indent=2, sort_keys=True))


def _emit_run_manifest(
    config, output: Path, run_kind: str, run_mode: str, summary: dict,
    candidate: dict | None = None,
) -> None:
    """Additive provenance: write a run manifest + a JSONL record for a run.

    Guarded so a provenance failure can never affect the trading command. Never
    writes secrets — only public config metadata. When ``candidate`` lineage is
    supplied (an Emberforge candidate-driven run), it is recorded on the manifest
    and in the JSONL record so the run traces back across the project boundary.
    """
    try:
        run_id = provenance.make_run_id(run_kind)
        manifest = provenance.new_manifest(run_id, run_kind, config, repo_dir=Path(__file__).resolve().parents[3])
        manifest.run_mode = run_mode
        if candidate:
            manifest.candidate = candidate
        provenance.finalize(manifest, "success", outputs=summary)
        provenance.write_manifest(output / "manifests" / f"{run_id}.json", manifest)
        record = {
            "run_id": run_id,
            "run_kind": run_kind,
            "run_mode": run_mode,
            "config_fingerprint": manifest.config_fingerprint,
            "git_commit": manifest.git.get("commit"),
            "completed_at": manifest.completed_at,
            **({"candidate": candidate} if candidate else {}),
            **summary,
        }
        provenance.append_jsonl(output / "run_log.jsonl", record)
    except Exception as exc:  # pragma: no cover - provenance must never break a run
        log.warning(f"[provenance] warning: could not write run manifest: {exc}")
