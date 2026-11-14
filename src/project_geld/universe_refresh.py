"""Refresh a paper strategy's live universe without rewriting research snapshots.

Intra V15 keeps its 100-stock rules. Daily V4 uses the research universe rules
(top 500 by 60-session median dollar volume, $5 minimum price, 252 sessions of
history, $20 million minimum), so its paper universe no longer depends on
re-running the broad research pipeline before the 45-day staleness guard trips.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import tomllib
from dataclasses import asdict, dataclass
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
from project_geld.logs import get_logger

log = get_logger(__name__)

RULES = BroadUniverseRules(
    top_n=100, minimum_price=5.0, minimum_history_sessions=60,
    dollar_volume_window=20, minimum_dollar_volume=10_000_000.0,
)


@dataclass(frozen=True)
class RefreshProfile:
    rules: BroadUniverseRules
    history_days: int
    # "guard": refuse a refresh that would strand positions (Intra V15).
    # "ledger": hand held names to the ownership ledger so they are sold (Daily V4).
    ownership: str


PROFILES = {
    "intra_v15": RefreshProfile(RULES, history_days=180, ownership="guard"),
    "daily_v4": RefreshProfile(BroadUniverseRules(), history_days=420, ownership="ledger"),
}


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


def snapshot_date(frame: pd.DataFrame, top_n: int = RULES.top_n) -> pd.Timestamp:
    if not {"timestamp", "symbol"}.issubset(frame.columns) or frame.empty:
        raise ValueError("Universe snapshot must contain dated symbols.")
    dates = pd.to_datetime(frame["timestamp"], utc=True, errors="raise")
    if dates.isna().any() or dates.nunique() != 1:
        raise ValueError("Live universe must contain exactly one observation date.")
    symbols = frame["symbol"]
    if len(frame) != top_n or symbols.isna().any() or symbols.duplicated().any():
        raise ValueError(f"Live universe must contain {top_n} distinct symbols.")
    if not symbols.astype(str).str.fullmatch(r"[A-Z][A-Z.]{0,5}").all():
        raise ValueError("Live universe contains malformed symbols.")
    return dates.iloc[0]


def needs_refresh(frame: pd.DataFrame, now: pd.Timestamp, top_n: int = RULES.top_n) -> bool:
    observed = snapshot_date(frame, top_n).tz_convert("America/New_York")
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


def hand_off_ownership(config: dict, symbols: list[str], previous: list[str]) -> set[str]:
    """Record every held name in the ownership ledger before the universe changes.

    Names that leave the universe are then priced and sold by the paper planner
    instead of being stranded as unmanaged positions.
    """
    from project_geld.config import PaperConfig
    from project_geld.paper import (
        AlpacaPaperBroker,
        load_managed_ledger,
        managed_ledger_path,
        update_managed_ledger,
    )

    paper_raw = config.get("paper", {})
    paper = PaperConfig(
        state_file=Path(paper_raw.get("state_file", "artifacts/paper/rebalance_state.json")),
        client_order_prefix=str(paper_raw.get("client_order_prefix", "geld")),
    )
    ledger_path = managed_ledger_path(paper)
    profile = config.get("account", {}).get("credential_profile", "")
    watched = sorted({*symbols, *previous, *load_managed_ledger(ledger_path)})
    account = AlpacaPaperBroker(profile).snapshot(watched)
    if account.unmanaged_symbols:
        log.warning(
            "Positions outside the old and new universe and the ledger stay unmanaged: %s. "
            "If they belong to this strategy, run `geld adopt-positions --symbols ...`.",
            ", ".join(sorted(account.unmanaged_symbols)),
        )
    return update_managed_ledger(ledger_path, account)


def refresh(config_path: Path, output: Path, *, now: pd.Timestamp | None = None,
            force: bool = False) -> Path:
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if now.tzinfo is None:
        raise ValueError("Refresh time must include a timezone.")
    source_text = config_path.read_text(encoding="utf-8")
    config = tomllib.loads(source_text)
    strategy_name = config["strategy"]["name"]
    if strategy_name not in PROFILES:
        raise ValueError(
            f"Universe refresh supports {', '.join(sorted(PROFILES))}, not {strategy_name}."
        )
    profile_rules = PROFILES[strategy_name]
    rules = profile_rules.rules
    output = output.resolve()
    snapshot = output / "universe.csv"
    generated = output / "runtime-config.toml"
    generated_text = runtime_config(source_text, snapshot)
    if snapshot.exists() and not force:
        current = pd.read_csv(snapshot)
        if not needs_refresh(current, now, rules.top_n):
            atomic_text(generated, generated_text)
            log.info(
                "Universe current: %s, %d symbols.",
                snapshot_date(current, rules.top_n).date(), rules.top_n,
            )
            return generated

    # Exclude today's incomplete bar, including when run after the market close.
    end = now.tz_convert("America/New_York").normalize().tz_convert("UTC")
    start = end - pd.Timedelta(days=profile_rules.history_days)
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
    if len(symbols) < 2 * rules.top_n:
        raise ValueError("Asset discovery returned too few candidates; existing universe retained.")
    cache = output / "universe-refresh" / end.strftime("%Y%m%d")
    cache.mkdir(parents=True, exist_ok=True)
    atomic_text(cache / "asset-master.csv", master.to_csv(index=False))
    log.info("Refreshing from SIP/raw daily data through %s: %d candidates.", observed.date(), len(symbols))
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
        candidate_rows.append(monthly_candidate_rows(bars, pd.DatetimeIndex([observed]), rules))
        log.info("Universe data: %d/%d candidates checked.", min(offset + 100, len(symbols)), len(symbols))
    coverage = len(covered) / len(symbols)
    if coverage < 0.90:
        raise ValueError(f"Latest-session coverage {coverage:.1%} is below 90%; existing universe retained.")
    selected = select_top_liquid(pd.concat(candidate_rows, ignore_index=True), rules)
    snapshot_date(selected, rules.top_n)
    if profile_rules.ownership == "guard":
        check_account_membership(profile, [*selected["symbol"], benchmark])
    else:
        # The outgoing universe: the published live file and the source config's file.
        previous: list[str] = []
        source_file = config.get("universe", {}).get("symbols_file")
        for candidate in [snapshot, (config_path.parent / source_file) if source_file else None]:
            if candidate is not None and Path(candidate).is_file():
                previous += pd.read_csv(candidate)["symbol"].astype(str).str.upper().tolist()
        hand_off_ownership(config, [*selected["symbol"], benchmark], previous)
    if snapshot.exists() and snapshot_date(pd.read_csv(snapshot), rules.top_n) > observed:
        raise ValueError("Refusing to replace the universe with an older observation.")
    text = selected.to_csv(index=False)
    metadata = {
        "refreshed_at": now.isoformat(), "observed_at": observed.isoformat(),
        "effective_after": end.isoformat(), "rules": asdict(rules), "strategy": strategy_name,
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
    log.info("Published %d symbols observed %s; original research snapshot preserved.", len(selected), observed.date())
    return generated
