"""Bind a bundle's declarative ``universe_assumptions`` to a real Geld universe.

Emberforge evaluates factors on a research universe that is often synthetic
(``["SYM00", ...]``) or simply ``"research-only"``. Before Geld can evaluate or
(eventually) trade a candidate, that assumption must be mapped to a concrete,
survivorship-aware set of symbols that actually exist in Geld's point-in-time
data.

Binding resolves real symbols present in supplied bars. Bar presence alone does
not prove historical liquidity or point-in-time membership; callers must supply
the appropriate eligibility mask for that claim. A fixed cap uses symbol order,
never full-history observation counts (which would select on future survival).
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
    return sorted({str(s) for s in bars["symbol"].dropna()
                   if str(s).upper() != benchmark.upper() and not _looks_synthetic(s)}, key=str.upper)


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
    if max_symbols is not None and (isinstance(max_symbols, bool) or not isinstance(max_symbols, int) or max_symbols < 1):
        raise ValueError("max_symbols must be a positive integer")
    available = _available_symbols(bars, benchmark)
    available_set = {s.upper() for s in available}

    def _finalize(symbols: list[str], source: str) -> Binding:
        # Stable symbol order, independent of future row counts.
        seen: set[str] = set()
        wanted = {x.upper() for x in symbols}
        ordered = [
            s for s in available
            if s.upper() in wanted
            and not (s.upper() in seen or seen.add(s.upper()))
        ]
        if max_symbols is not None and max_symbols > 0:
            ordered = ordered[:max_symbols]
        if not ordered:
            return Binding(False, [], source, "no tradeable symbols after binding", requested)
        return Binding(True, ordered, source, f"bound {len(ordered)} symbols", requested)

    # 1) explicit config-specified universe wins.
    if config_symbols is not None:
        wanted = [s for s in config_symbols if s.upper() in available_set]
        if not wanted:
            return Binding(False, [], "config", "config symbols not present in bars", requested)
        return _finalize(wanted, "config")

    # 2) a concrete real-symbol list in the bundle (not synthetic).
    if isinstance(requested, list) and requested:
        real = [s for s in requested if not _looks_synthetic(s) and str(s).upper() in available_set]
        if real:
            return _finalize(real, "bundle_symbol_list")
        if any(not _looks_synthetic(s) for s in requested):
            return Binding(False, [], "bundle_symbol_list", "requested real symbols not present in bars", requested)
        # Synthetic research labels can be explicitly rebound to supplied bars.

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
