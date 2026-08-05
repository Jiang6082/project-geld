# Candidate promotion policy (independent OOS re-validation)

Emberforge discovers factors and controls false discovery upstream (deflated
Sharpe, BH/Holm FDR, PBO/CSCV, leakage detection). **Geld does not trust those
numbers.** Before a candidate can shadow or paper-trade, Geld re-derives the
evidence itself, on its own point-in-time data, and applies fixed gates. This
document is the written policy those gates implement
(`src/project_geld/candidates/promotion.py`).

## Principles

- **Independent confirmation, not re-optimization.** The candidate runs with the
  parameters shipped in the bundle. There is **no grid search, no tuning to the
  holdout** — doing so would reintroduce the exact selection bias Emberforge
  exists to defeat.
- **Judge after costs.** All gate metrics are net of slippage and fees.
- **Judge out-of-sample.** The timeline is split `train / val / test`; gates are
  applied to the **test (holdout)** segment, with `val` used only for a
  sign-consistency (stability) check.
- **Survive cost stress.** A second pass at elevated slippage must remain
  net-positive on the holdout.
- **Reject by default.** Any failed gate ⇒ `reject`. Only an all-pass result
  yields `promote_to_shadow`.

## Timeline split

Sessions are ordered and split by fraction (defaults `train=0.6`, `val=0.2`,
`test=0.2`). `test` is the untouched holdout that decides promotion.

## Gates

| Gate | Condition (default threshold) | Rationale |
|------|-------------------------------|-----------|
| `net_return_positive` | test net-of-cost total return > `0.0` | must make money after costs OOS |
| `oos_sharpe` | test Sharpe ≥ `0.30` | risk-adjusted, not just positive |
| `stability` | `min(val, test)` Sharpe ≥ `0.0` | sign consistency across splits (not one lucky window) |
| `max_drawdown` | test max drawdown ≥ `-0.35` | tolerable holdout drawdown |
| `turnover_capacity` | full-period annual turnover ≤ `50.0` | capacity / cost-sensitivity proxy |
| `oos_alpha` | test annualized alpha > `0.0` | edge is real, not just market beta |
| `beta_sanity` | \|test beta\| ≤ `1.5` | not closet-index / leveraged-beta exposure |
| `cost_stress` | test total return > `0.0` at `15 bps` slippage | edge survives higher costs |

Thresholds live in `GatePolicy` and are intentionally **modest** — the gates
exist to filter out candidates that don't survive independent confirmation, not
to re-rank the survivors.

## Multiple-testing control (batch re-validation)

Per-candidate gates are necessary but not sufficient: run enough candidates and
one clears the holdout-Sharpe gate by chance. When several candidates are
re-validated together (`revalidate-batch`, `project_geld.candidates.batch`), Geld
applies a **family-wise correction**: each candidate's holdout Sharpe becomes a
one-sided p-value (`t = SR/√ppy · √n`), and a **Benjamini-Hochberg FDR**
procedure at level `q` (default `0.10`) decides which survive. A candidate is
promoted only if it **passes its own gates AND survives batch FDR**. This is
deliberately stricter than judging candidates in isolation — a p-value that looks
significant alone is rejected once it competes in a large family. No parameter
search is performed at any stage.

## Verdict

A machine-readable JSON verdict is written to
`artifacts/candidates/verdicts/<candidate_id>.verdict.json`:

```json
{
  "candidate_id": "...",
  "verdict": "promote_to_shadow" | "reject",
  "reasons": ["<failed gate names>" | "all gates passed"],
  "gates": [{"name": "...", "passed": true, "value": ..., "threshold": ...}],
  "segments": {"train": {...}, "val": {...}, "test": {...}},
  "test_stressed": {...},
  "policy": {...}
}
```

## State machine

```
quarantined ──(OOS run completes)──> validated_oos ──(all gates pass)──> shadow ──(MANUAL)──> paper
     │                                     │                               │
     └────────────> rejected <─────────────┴───────────────────────────────┘
```

- `quarantined` — imported + schema-valid (importer default), research-only.
- `validated_oos` — completed Geld's independent OOS re-validation.
- `shadow` — cleared to run without capital after all gates passed.
- `paper` — **manual only**; the automated gates never perform this transition.
- `rejected` — failed a gate; terminal.

State is persisted in the quarantine record with an append-only `history` audit
trail (`src/project_geld/candidates/state.py`). Run:

```bash
python -m project_geld.cli revalidate-candidate \
  --bundle artifacts/candidates/quarantine/<id>.json \
  --bars artifacts/research-broad/selected-bars.csv.gz \
  --advance-state
```
