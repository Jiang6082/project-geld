# Emberforge → Geld candidate pipeline (runbook)

How a factor discovered in **Emberforge** becomes an independently re-validated,
paper-tradeable strategy in **Geld**. The two projects are coupled by exactly one
thing: a one-way, offline, checksummed `candidate_bundle_v1` JSON file. Geld
never imports Emberforge at runtime; it replicates the factor DSL and re-derives
all evidence itself.

```
 Emberforge (discovery, FDR control)          Geld (independent confirmation, paper)
 ───────────────────────────────────          ─────────────────────────────────────
 factor DSL + backtest + deflated Sharpe
 BH/Holm FDR, PBO/CSCV, leakage tests
        │  export_candidate(approved=True)
        ▼
   candidate_bundle_v1.json  ───────────────▶  validate-candidate      (schema, no code)
   (expression, code_hash,                      import-candidate        (quarantine, paper OFF)
    universe_assumptions,                        verify integrity        (recompute canonical + hash)
    portfolio_construction, ...)                 revalidate-candidate    (OOS gates -> verdict)
                                                 (manual) enable paper
```

## State machine

```
quarantined ──(OOS run completes)──▶ validated_oos ──(all gates pass)──▶ shadow ──(MANUAL)──▶ paper
     │                                     │                               │
     └──────────────▶ rejected ◀───────────┴───────────────────────────────┘
```

`paper` is **manual-only** — the automated gates never reach it. State + an
append-only audit `history` live in the quarantine record
(`artifacts/candidates/quarantine/<id>.json`). Gate policy:
[docs/CANDIDATE_PROMOTION.md](CANDIDATE_PROMOTION.md).

## 1. Export (in Emberforge)

Emberforge produces the bundle from an approved factor:

```python
from emberforge.export.geld_bundle import to_geld_bundle_v1, export_geld_bundle_v1
bundle = to_geld_bundle_v1(factor=factor_json, metrics=metrics, statistics=stats,
                           hypothesis=hypothesis, data_provenance=provenance,
                           approval_state="human_approved")
export_geld_bundle_v1(bundle, "momentum_20.candidate.json")
```

The bundle is declarative data only — no executable Python. `code_hash` is the
canonical-expression hash; `universe_assumptions` defaults to the label
`"research-only"`.

## 2. Validate + import (in Geld)

```bash
# schema + safety validation only; imports/enables nothing
python -m project_geld.cli validate-candidate --bundle momentum_20.candidate.json

# validate + quarantine (research-only; paper stays OFF)
python -m project_geld.cli import-candidate --bundle momentum_20.candidate.json
```

Import writes `artifacts/candidates/quarantine/<candidate_id>.json` with
`state="quarantined"`, `paper_enabled=false`. Geld additionally re-derives the
canonical form and hash from the expression and checks them against `code_hash`
(`project_geld.candidates.integrity.verify_bundle_integrity`) — a tampered spec
is caught even if a file checksum were regenerated.

## 3. Independent OOS re-validation (in Geld)

```bash
python -m project_geld.cli revalidate-candidate \
  --bundle artifacts/candidates/quarantine/momentum_20.json \
  --bars   artifacts/research-broad/selected-bars.csv.gz \
  --advance-state
```

This binds `universe_assumptions` to Geld's concrete, survivorship-aware
point-in-time universe (see below), runs the candidate strategy through Geld's
own backtester on train/val/**test** splits with a cost-stress pass, applies the
fixed promotion gates, and writes a machine-readable verdict to
`artifacts/candidates/verdicts/<id>.verdict.json`. **No parameter search** — this
is independent confirmation, not re-optimization. With `--advance-state`:
`quarantined → validated_oos → shadow` on a pass, or `→ rejected` on a fail. A
provenance run manifest recording the cross-boundary lineage (`candidate_id`,
`source_project_version`, `code_hash`, `data_fingerprint`) is emitted under
`<output>/manifests/`.

Use `--max-symbols N` / `--start` / `--end` for a faster bounded run.

## 4. Universe binding

`universe_assumptions` is mapped to a real tradeable set
(`project_geld.candidates.universe.bind_universe`):

- a **label** (`"research-only"`) or Emberforge's **synthetic** tickers
  (`SYM00…`) → the broad PIT universe present in the supplied bars (inherently
  survivorship-aware: a symbol's bars exist only while it was a listed member);
- a **concrete real-symbol list** → intersected with the available symbols;
- an explicit **config-specified** set → used verbatim.

Binding **fails closed**: if nothing resolves, the run is flagged and rejected
rather than trading an empty or synthetic universe.

## 5. Promote to paper (manual)

Only after reviewing the verdict and shadow behaviour, a human advances
`shadow → paper`. This is deliberately outside the automated path.

## Reproduce the whole round-trip

The integration test `tests/test_candidate_roundtrip.py` proves
discover → bundle → import → verify checksum → build strategy → backtest →
verdict. It runs CI-safe on the committed example bundle, and additionally
exercises Emberforge's real exporter when `GELD_EMBERFORGE_SRC` points at the
sibling repo's `src`:

```bash
python -m pytest tests/test_candidate_roundtrip.py -q
GELD_EMBERFORGE_SRC=../project-emberforge/src python -m pytest tests/test_candidate_roundtrip.py -q
```
