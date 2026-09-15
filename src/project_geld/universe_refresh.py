"""Refresh the live Intra V15 universe without rewriting research snapshots."""
from __future__ import annotations

import json
import os
import re
import tempfile
import tomllib
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

import pandas as pd

from project_geld.broad_universe import (
    BroadUniverseRules,
    asset_master_frame,
    monthly_candidate_rows,
    select_top_liquid,
)
from project_geld.data import AlpacaBarSource, normalize_bars

RULES = BroadUniverseRules(
    top_n=100, minimum_price=5.0, minimum_history_sessions=60,
    dollar_volume_window=20, minimum_dollar_volume=10_000_000.0,
)


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def snapshot_date(frame: pd.DataFrame) -> pd.Timestamp:
    if not {"timestamp", "symbol"}.issubset(frame.columns) or frame.empty:
        raise ValueError("Universe snapshot must contain dated symbols.")
    dates = pd.to_datetime(frame["timestamp"], utc=True, errors="raise")
    if dates.isna().any() or dates.nunique() != 1:
        raise ValueError("Live universe must contain exactly one observation date.")
    symbols = frame["symbol"]
    if len(frame) != RULES.top_n or symbols.isna().any() or symbols.duplicated().any():
        raise ValueError("Live universe must contain 100 distinct symbols.")
    if not symbols.astype(str).str.fullmatch(r"[A-Z][A-Z.]{0,5}").all():
        raise ValueError("Live universe contains malformed symbols.")
    return dates.iloc[0]


def needs_refresh(frame: pd.DataFrame, now: pd.Timestamp) -> bool:
    observed = snapshot_date(frame).tz_convert("America/New_York")
    local = now.tz_convert("America/New_York")
    if observed.date() >= local.date():
        raise ValueError("Universe must use a completed earlier session.")
    return (observed.year, observed.month) < (local.year, local.month)


def runtime_config(source: str, snapshot: Path) -> str:
    """Change only the universe file; retain the original strategy and risk settings."""
    original = tomllib.loads(source)
    lines = source.splitlines(keepends=True)
    in_universe = False
    replaced = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("["):
            in_universe = stripped.split("#", 1)[0].strip() == "[universe]"
        if in_universe and re.match(r"^\s*symbols_file\s*=", line):
            lines[index] = f"symbols_file = {json.dumps(snapshot.resolve().as_posix())}\n"
            replaced += 1
    if replaced != 1:
        raise ValueError("Expected one symbols_file entry in [universe].")
    result = "".join(lines)
    expected = tomllib.loads(source)
    expected["universe"]["symbols_file"] = snapshot.resolve().as_posix()
    if tomllib.loads(result) != expected or original["universe"].get("symbols"):
        raise ValueError("Runtime config must preserve settings and use file-only symbols.")
    return result


def active_candidates(profile: str) -> pd.DataFrame:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import AssetClass, AssetStatus
    from alpaca.trading.requests import GetAssetsRequest

    from project_geld.credentials import load_alpaca_credentials

    key, secret = load_alpaca_credentials(profile)
    # Read-only asset discovery; this refresh never submits or cancels orders.
    client = TradingClient(key, secret, paper=True)
    assets = client.get_all_assets(GetAssetsRequest(
        asset_class=AssetClass.US_EQUITY, status=AssetStatus.ACTIVE,
    ))
    frame = asset_master_frame(assets)
    return frame.loc[frame["included"] & frame["tradable_now"]].copy()


def check_account_membership(profile: str, symbols: list[str]) -> None:
    from project_geld.paper import AlpacaPaperBroker

    account = AlpacaPaperBroker(profile).snapshot(symbols)
    if account.unmanaged_notional > 0 or account.open_order_symbols - set(symbols):
        raise ValueError(
            "Refresh would exclude an existing paper position or open order; "
            "reconcile it before changing the universe. Existing universe retained."
        )


def refresh(config_path: Path, output: Path, *, now: pd.Timestamp | None = None,
            force: bool = False) -> Path:
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if now.tzinfo is None:
        raise ValueError("Refresh time must include a timezone.")
    source_text = config_path.read_text(encoding="utf-8")
    config = tomllib.loads(source_text)
    if config["strategy"]["name"] != "intra_v15":
        raise ValueError("This refresh implements the Intra V15 universe rules only.")
    output = output.resolve()
    snapshot = output / "universe.csv"
    generated = output / "runtime-config.toml"
    generated_text = runtime_config(source_text, snapshot)
    if snapshot.exists() and not force:
        current = pd.read_csv(snapshot)
        if not needs_refresh(current, now):
            atomic_text(generated, generated_text)
            print(f"Universe current: {snapshot_date(current).date()}, 100 symbols.", flush=True)
            return generated

    # Exclude today's incomplete bar, including when run after the market close.
    end = now.tz_convert("America/New_York").normalize().tz_convert("UTC")
    start = end - pd.Timedelta(days=180)
    profile = config.get("account", {}).get("credential_profile", "")
    bars_source = AlpacaBarSource("sip", "raw", profile)
    benchmark = config["universe"].get("benchmark", "SPY")
    benchmark_bars = bars_source.fetch([benchmark], start.to_pydatetime(), end.to_pydatetime())
    benchmark_bars = benchmark_bars.loc[benchmark_bars["timestamp"].lt(end)]
    if benchmark_bars.empty:
        raise ValueError("No completed benchmark sessions; existing universe retained.")
    observed = pd.Timestamp(benchmark_bars["timestamp"].max())
    if (end - observed).days > 7:
        raise ValueError("Benchmark data is stale; existing universe retained.")
    master = active_candidates(profile)
    symbols = sorted(set(master["symbol"]) - {benchmark})
    if len(symbols) < 2 * RULES.top_n:
        raise ValueError("Asset discovery returned too few candidates; existing universe retained.")
    cache = output / "universe-refresh" / end.strftime("%Y%m%d")
    cache.mkdir(parents=True, exist_ok=True)
    atomic_text(cache / "asset-master.csv", master.to_csv(index=False))
    print(f"Refreshing from SIP/raw daily data through {observed.date()}: {len(symbols)} candidates.", flush=True)
    candidate_rows = []
    covered: set[str] = set()
    for offset in range(0, len(symbols), 100):
        batch = symbols[offset:offset + 100]
        signature = sha256(("|".join(batch) + str(start) + str(end) + "sip/raw").encode()).hexdigest()[:16]
        path = cache / f"bars-{signature}.csv.gz"
        if path.exists():
            bars = normalize_bars(pd.read_csv(path))
        else:
            bars = bars_source.fetch(batch, start.to_pydatetime(), end.to_pydatetime())
            # Failed downloads never produce a reusable partial cache file.
            temporary = path.with_suffix(".tmp")
            try:
                bars.to_csv(temporary, index=False, compression="gzip")
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        bars = bars.loc[bars["timestamp"].between(start, observed)]
        covered.update(bars.loc[bars["timestamp"].eq(observed), "symbol"])
        candidate_rows.append(monthly_candidate_rows(bars, pd.DatetimeIndex([observed]), RULES))
        print(f"Universe data: {min(offset + 100, len(symbols))}/{len(symbols)} candidates checked.", flush=True)
    coverage = len(covered) / len(symbols)
    if coverage < 0.90:
        raise ValueError(f"Latest-session coverage {coverage:.1%} is below 90%; existing universe retained.")
    selected = select_top_liquid(pd.concat(candidate_rows, ignore_index=True), RULES)
    snapshot_date(selected)
    check_account_membership(profile, [*selected["symbol"], benchmark])
    if snapshot.exists() and snapshot_date(pd.read_csv(snapshot)) > observed:
        raise ValueError("Refusing to replace the universe with an older observation.")
    text = selected.to_csv(index=False)
    metadata = {
        "refreshed_at": now.isoformat(), "observed_at": observed.isoformat(),
        "effective_after": end.isoformat(), "rules": asdict(RULES),
        "feed": "sip", "adjustment": "raw", "candidate_symbols": len(symbols),
        "latest_session_coverage": coverage, "selected_symbols": len(selected),
        "snapshot_sha256": sha256(text.encode()).hexdigest(),
        "scope": "Current active tradable assets for forward paper planning; not historical PIT evidence.",
    }
    # Dated refresh evidence is written before publishing the live file.
    atomic_text(cache / "selected.csv", text)
    atomic_text(cache / "manifest.json", json.dumps(metadata, indent=2) + "\n")
    atomic_text(snapshot, text)
    atomic_text(generated, generated_text)
    print(f"Published {len(selected)} symbols observed {observed.date()}; original research snapshot preserved.", flush=True)
    return generated
