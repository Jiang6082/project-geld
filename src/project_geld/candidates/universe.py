"""Bind a bundle's declarative ``universe_assumptions`` to a real Geld universe.

Emberforge evaluates factors on a research universe that is often synthetic
(``["SYM00", ...]``) or simply ``"research-only"``. Before Geld can evaluate or
(eventually) trade a candidate, that assumption must be mapped to a concrete,
survivorship-aware set of symbols that actually exist in Geld's point-in-time
data.

The broad PIT bars under ``artifacts/research-broad`` are inherently
survivorship-aware: a symbol's bars exist only while it was a listed, liquid
member, so ranking over the non-NaN cells of that panel already excludes names
before listing and after delisting. Binding therefore resolves to the set of
real symbols present in the supplied bars (optionally intersected with an
explicit symbol list from the bundle), never the synthetic research tickers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

# Synthetic research tickers Emberforge emits; never tradeable in Geld.
_SYNTHETIC_PREFIXES = ("SYM",)


def _looks_synthetic(sym: str) -> bool:
    s = str(sym).upper()
    return any(s.startswith(p) and s[len(p):].isdigit() for p in _SYNTHETIC_PREFIXES)


@dataclass(frozen=True)
class Binding:
    ok: bool
    symbols: list[str] = field(default_factory=list)
    source: str = ""
    reason: str = ""
    requested: Any = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["n_symbols"] = len(self.symbols)
        return d


def _available_symbols(bars: pd.DataFrame, benchmark: str) -> list[str]:
    counts = bars["symbol"].value_counts()
    return [s for s in counts.index if s.upper() != benchmark.upper()]


def bind_universe(
    bundle: dict[str, Any],
    bars: pd.DataFrame,
    *,
    benchmark: str = "SPY",
    max_symbols: int | None = None,
    config_symbols: list[str] | None = None,
) -> Binding:
    """Resolve the tradeable symbol set for a candidate.

    Priority: an explicit ``config_symbols`` override > a concrete real-symbol
    list in the bundle > the broad PIT universe present in ``bars``. Synthetic
    research tickers and the benchmark are never returned as tradeables. Returns
    an unbound (``ok=False``) result when nothing resolves, so callers can flag
    or reject rather than silently trade an empty universe.
    """
    requested = bundle.get("universe_assumptions", "research-only")
    available = _available_symbols(bars, benchmark)
    available_set = {s.upper() for s in available}

    def _finalize(symbols: list[str], source: str) -> Binding:
        # preserve liquidity order from `available`, drop dups/benchmark.
        seen: set[str] = set()
        ordered = [
            s for s in available
            if s.upper() in {x.upper() for x in symbols}
            and not (s.upper() in seen or seen.add(s.upper()))
        ]
        if max_symbols is not None and max_symbols > 0:
            ordered = ordered[:max_symbols]
        if not ordered:
            return Binding(False, [], source, "no tradeable symbols after binding", requested)
        return Binding(True, ordered, source, f"bound {len(ordered)} symbols", requested)

    # 1) explicit config-specified universe wins.
    if config_symbols:
        wanted = [s for s in config_symbols if s.upper() in available_set]
        if not wanted:
            return Binding(False, [], "config", "config symbols not present in bars", requested)
        return _finalize(wanted, "config")

    # 2) a concrete real-symbol list in the bundle (not synthetic).
    if isinstance(requested, list) and requested:
        real = [s for s in requested if not _looks_synthetic(s) and str(s).upper() in available_set]
        if real:
            return _finalize(real, "bundle_symbol_list")
        # synthetic or non-matching -> fall through to the broad universe.

    # 3) default: the broad PIT universe present in the bars.
    if available:
        return _finalize(available, "research_broad_pit")

    return Binding(False, [], "none", "no bars available to bind a universe", requested)


def bind_strategy(
    bundle: dict[str, Any],
    bars: pd.DataFrame,
    *,
    benchmark: str = "SPY",
    max_symbols: int | None = None,
    config_symbols: list[str] | None = None,
    **overrides: Any,
):
    """Build a CandidateStrategy bound to a concrete PIT universe.

    Binds the bundle's ``universe_assumptions`` against ``bars`` and returns a
    ``(strategy, binding)`` pair whose scoring is restricted to the bound,
    survivorship-aware symbols. Raises ``ValueError`` if the universe cannot be
    bound, so callers never evaluate on an empty or synthetic universe.
    """
    from project_geld.strategies.candidate import CandidateStrategy

    binding = bind_universe(
        bundle, bars, benchmark=benchmark, max_symbols=max_symbols, config_symbols=config_symbols
    )
    if not binding.ok:
        raise ValueError(f"cannot bind universe: {binding.reason}")
    strategy = CandidateStrategy.from_bundle(
        bundle, universe=tuple(binding.symbols), **overrides
    )
    return strategy, binding


__all__ = ["Binding", "bind_universe", "bind_strategy"]
