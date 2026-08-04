# Hardening additions

This change set adds operational safety and reproducibility infrastructure. It is
**additive and behavior-preserving**: it does not change the economic logic, target
weights, order behavior, or signal timing of any strategy (Daily V4, Intra V15, or
otherwise). The existing test suite plus new tests pass.

## 1. Atomic state writes (`src/project_geld/atomicio.py`)
Paper-critical state was written with plain `write_text` / `to_csv`, so a crash
mid-write could corrupt live state (the performance CSV is read-modify-written in
full, so a partial write there loses the whole history). `atomic_write_text` and
`atomic_write_dataframe_csv` write to a temp file in the same directory, `fsync`,
then `os.replace` — an atomic rename. Applied to:

- `paper.mark_paper_rebalance` (rebalance state)
- `paper.append_performance_snapshot` (performance history CSV)
- `intraday.mark_intraday_cycle` (intraday cycle state)
- `shadow` state file

Same content as before — only the write is made crash-safe.

## 2. Run provenance & observability (`src/project_geld/provenance.py`)
Answers "were two runs equivalent?" and gives each paper run a traceable record.

- `config_fingerprint(AppConfig)` — stable hash of the full (nested) config.
- `git_info`, `software_versions` (via package metadata, no heavy imports).
- `RunManifest` + `new_manifest` / `finalize` / `write_manifest` (atomic) / `compare_manifests`.
- `make_run_id`, `append_jsonl`. Args are sanitized; **no secrets** are recorded.

Wired additively into `paper-once` and `intraday-paper-once`: each run writes
`<output>/manifests/<run_id>.json` and appends a line to `<output>/run_log.jsonl`
(run id, config fingerprint, git commit, run mode, equity, order count, submit
flag). The emission is fully guarded — a provenance failure can never affect the
trading command.

## 3. Emberforge candidate-bundle boundary (`src/project_geld/candidates/`)
Emberforge emits approved candidates as offline JSON bundles
(`emberforge/runtime/pipeline/geld_bundles/*.candidate.json`). This adds the ingest
contract Geld previously lacked:

- `schemas/candidate_bundle_v1.schema.json` + `schemas/example_candidate_bundle.json`.
- `validator.py` — offline, dependency-free. Rejects unknown fields and any key
  implying executable content; nothing in a bundle is ever `exec`/`eval`'d. Accepts
  the shapes Emberforge actually emits (`preprocessing` object, `universe_assumptions`
  list) and type-checks them so malformed shapes are rejected.
- `importer.py` — `import-candidate` validates then writes the bundle to
  `artifacts/candidates/quarantine/` with `state: quarantined`, `paper_enabled: false`.
  Import is manual/explicit, never edits a config, never enables trading, and never
  touches deployed strategies. There is no live link to Emberforge.
- CLI: `geld validate-candidate --bundle <path>` and `geld import-candidate --bundle <path>`.

**Note:** a quarantined bundle cannot yet *run* in Geld — there is no factor-expression
evaluator, and the current Emberforge candidates are long/short, daily, and evaluated
on a synthetic universe, so promotion to a live strategy remains deliberate, out-of-scope
future work.

## Tests
New: `tests/test_atomicio.py`, `tests/test_provenance.py`, `tests/test_candidates.py`
(the last validates the real Emberforge bundles when the sibling repo is present).
No test submits orders or requires credentials.
