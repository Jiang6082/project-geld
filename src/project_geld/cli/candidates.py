"""Candidate validation, import, re-validation and shadow commands."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from project_geld import provenance
from project_geld.candidates.holdout_registry import (
    DEFAULT_REGISTRY,
    holdout_key,
    record_evaluations,
    trials_including,
)
from project_geld.cli.common import _date, _emit_run_manifest
from project_geld.config import load_config, validate_config
from project_geld.logs import get_logger

log = get_logger(__name__)


def command_validate_candidate(args) -> None:
    from project_geld.candidates.validator import load_bundle, validate_bundle

    result = validate_bundle(load_bundle(args.bundle))
    log.info(f"Candidate: {result.candidate_id}")
    log.info(f"Valid: {'yes' if result.ok else 'no'}")
    for error in result.errors:
        log.info(f"  [error] {error}")
    for warning in result.warnings:
        log.info(f"  [warn]  {warning}")
    log.info(
        "\nValidation does NOT import or enable anything. Use 'import-candidate' to place "
        "an approved bundle into research-only quarantine."
    )
    if not result.ok:
        raise ValueError("Candidate bundle failed validation.")


def command_import_candidate(args) -> None:
    from project_geld.candidates.importer import import_bundle

    target = import_bundle(args.bundle, quarantine_dir=args.quarantine_dir)
    log.info(f"Quarantined (research-only, paper NOT enabled): {target}")


def _holdout_registry(args) -> tuple[Path, str | None]:
    """Registry path and this run's holdout key (None when disabled).

    The default sits beside the verdict directory, so the standard layout uses
    ``artifacts/candidates/holdout_evaluations.json``.
    """
    configured = getattr(args, "holdout_registry", None)
    path = Path(configured) if configured else Path(args.output).parent / DEFAULT_REGISTRY.name
    if getattr(args, "no_holdout_registry", False):
        return path, None
    bars_path = Path(args.bars)
    if not bars_path.exists():
        return path, None
    return path, holdout_key(bars_path, getattr(args, "start", None), getattr(args, "end", None))


def _check_candidate_for_evaluation(bundle) -> None:
    from project_geld.candidates.integrity import verify_bundle_integrity
    from project_geld.candidates.validator import validate_bundle
    validation = validate_bundle(bundle)
    if not validation.ok:
        raise ValueError(f"candidate schema validation failed: {validation.errors}")
    integrity = verify_bundle_integrity(bundle)
    if not integrity["ok"]:
        raise ValueError(f"candidate integrity validation failed: {integrity['problems']}")
    if bundle.get("approval_status") != "approved":
        raise ValueError("candidate must be approved before independent re-validation or shadow evaluation")


def command_revalidate_candidate(args) -> None:
    """Independent OOS re-validation of a quarantined candidate -> verdict.

    Loads Geld's own point-in-time bars, runs the candidate strategy through the
    backtester with a cost-stress pass, applies fixed promotion gates, writes a
    machine-readable verdict, and (optionally) advances the candidate's state.
    NO parameter search — independent confirmation only.
    """
    from project_geld.candidates import state as candidate_state
    from project_geld.candidates.promotion import (
        GatePolicy,
        declared_trials,
        discovery_window,
        revalidate_candidate,
    )
    from project_geld.candidates.state import load_record
    from project_geld.candidates.universe import bind_strategy
    from project_geld.data import normalize_bars

    config = load_config(args.config)
    validate_config(config)
    record = load_record(args.bundle)
    bundle = record.get("bundle", record)
    _check_candidate_for_evaluation(bundle)

    bars = normalize_bars(pd.read_csv(args.bars))
    if args.start:
        bars = bars[bars["timestamp"] >= _date(args.start, bars["timestamp"].min())]
    if args.end:
        bars = bars[bars["timestamp"] <= _date(args.end, bars["timestamp"].max())]
    benchmark = config.universe.benchmark.upper()

    # Bind universe_assumptions to a concrete PIT universe and build a strategy
    # whose scoring is restricted to it (fails closed if it cannot be bound).
    try:
        strategy, binding = bind_strategy(bundle, bars, benchmark=benchmark, max_symbols=args.max_symbols)
    except ValueError as exc:
        raise RuntimeError(f"Universe binding failed: {exc}") from exc
    bars = bars[bars["symbol"].isin(set(binding.symbols) | {benchmark})]

    policy = GatePolicy()
    registry_path, holdout = _holdout_registry(args)
    candidate_id = str(bundle.get("candidate_id"))
    trials = max(
        getattr(args, "trials", None) or 1,
        trials_including(registry_path, holdout, [candidate_id]) if holdout else 1,
    )
    verdict = revalidate_candidate(
        strategy,
        bars,
        backtest=config.backtest,
        risk=config.risk,
        benchmark=benchmark,
        tradable_symbols=binding.symbols,
        context_symbols=[benchmark],
        policy=policy,
        candidate_id=bundle.get("candidate_id"),
        n_trials=trials,
        discovery=discovery_window(bundle),
        discovery_trials=declared_trials(bundle),
    )
    verdict["universe_binding"] = binding.to_dict()
    if holdout:
        verdict["holdout_registry"] = {
            "key": holdout,
            "distinct_candidates": record_evaluations(
                registry_path, holdout, [(candidate_id, verdict["verdict"])]
            ),
        }

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    safe_id = str(verdict["candidate_id"]).replace("/", "_").replace(":", "_")
    verdict_path = output / f"{safe_id}.verdict.json"
    verdict_path.write_text(json.dumps(verdict, indent=2, sort_keys=True, default=str), encoding="utf-8")

    log.info(f"Candidate: {verdict['candidate_id']}")
    log.info(f"Verdict:   {verdict['verdict'].upper()}")
    for gate in verdict["gates"]:
        flag = "PASS" if gate["passed"] else "FAIL"
        log.info(f"  [{flag}] {gate['name']}: value={gate['value']} threshold={gate['threshold']}")
    for warning in verdict.get("warnings", []):
        log.info(f"  [WARN] {warning}")
    log.info(f"Verdict written: {verdict_path.resolve()}")

    # Cross-boundary provenance: this run is driven by an Emberforge candidate.
    lineage = provenance.candidate_lineage(bundle)
    _emit_run_manifest(
        config, output, "candidate-revalidation", "backtest",
        summary={
            "verdict": verdict["verdict"],
            "test_sharpe": verdict["segments"]["test"].get("sharpe"),
            "test_return": verdict["segments"]["test"].get("total_return"),
            "annual_turnover": verdict["annual_turnover"],
            "verdict_path": str(verdict_path),
        },
        candidate=lineage,
    )

    if args.advance_state and "state" in record:
        evidence = {"verdict_path": str(verdict_path), "gates": verdict["reasons"]}
        try:
            target = "shadow" if verdict["verdict"] == "promote_to_shadow" else "rejected"
            if target == "shadow" and record["state"] == "quarantined":
                candidate_state.advance_file(
                    args.bundle, "validated_oos", reason="Passed Geld independent OOS gates.", evidence=evidence,
                )
            candidate_state.advance_file(
                args.bundle, target,
                reason=f"OOS gates -> {verdict['verdict']}.", evidence=evidence,
            )
            log.info(f"State advanced -> {target} (paper still requires a manual step).")
        except candidate_state.StateError as exc:
            log.info(f"[state] not advanced: {exc}")


def command_revalidate_batch(args) -> None:
    """Batch OOS re-validation with Benjamini-Hochberg FDR across candidates.

    Loads every quarantined record in a directory, re-validates each on the same
    OOS bars, and promotes only those passing per-candidate gates AND surviving
    batch-level FDR. No parameter search.
    """
    from project_geld.candidates import state as candidate_state
    from project_geld.candidates.batch import revalidate_batch
    from project_geld.candidates.state import load_record
    from project_geld.data import normalize_bars

    config = load_config(args.config)
    validate_config(config)
    records: dict[str, dict] = {}
    for path in sorted(Path(args.quarantine_dir).glob("*.json")):
        rec = load_record(path)
        bundle = rec.get("bundle", rec)
        _check_candidate_for_evaluation(bundle)
        cid = bundle.get("candidate_id") or path.stem
        if cid in records:
            raise ValueError(f"candidate_id values must be unique; duplicate {cid!r}")
        records[cid] = {"path": path, "record": rec, "bundle": bundle}
    if not records:
        raise RuntimeError(f"No quarantined candidates found in {args.quarantine_dir}")

    bars = normalize_bars(pd.read_csv(args.bars))
    if args.start:
        bars = bars[bars["timestamp"] >= _date(args.start, bars["timestamp"].min())]
    if args.end:
        bars = bars[bars["timestamp"] <= _date(args.end, bars["timestamp"].max())]
    benchmark = config.universe.benchmark.upper()

    registry_path, holdout = _holdout_registry(args)
    trials = max(
        getattr(args, "trials", None) or 1,
        trials_including(registry_path, holdout, records) if holdout else 1,
    )
    batch = revalidate_batch(
        [r["bundle"] for r in records.values()], bars,
        backtest=config.backtest, risk=config.risk, benchmark=benchmark,
        fdr_q=args.fdr_q, max_symbols=args.max_symbols, n_trials=trials,
    )
    if holdout:
        batch["holdout_registry"] = {
            "key": holdout,
            "distinct_candidates": record_evaluations(
                registry_path, holdout,
                [(row["candidate_id"], row["verdict"]) for row in batch["candidates"]],
            ),
        }

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "batch.verdict.json").write_text(
        json.dumps(batch, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    log.info(f"Candidates: {batch['n_candidates']}  trials: {batch['n_trials']}  "
          f"gate-pass: {batch['n_gate_pass']}  "
          f"promoted (survive FDR q={batch['fdr_q']}): {batch['n_promoted']}")
    for r in batch["candidates"]:
        log.info(f"  [{r['verdict'].upper():17}] {r['candidate_id']}: "
              f"sharpe={r.get('test_sharpe')} p={round(r['p_value'], 4)} "
              f"gates={'ok' if r['gates_passed'] else r.get('reasons')}")
    for r in batch["candidates"]:
        for warning in r.get("warnings", []):
            log.info(f"  [WARN] {r['candidate_id']}: {warning}")
    log.info(f"Batch verdict written: {(output / 'batch.verdict.json').resolve()}")

    if args.advance_state:
        for r in batch["candidates"]:
            entry = records.get(r["candidate_id"])
            if entry is None or "state" not in entry["record"]:
                continue
            target = "shadow" if r["verdict"] == "promote_to_shadow" else "rejected"
            try:
                if target == "shadow" and entry["record"]["state"] == "quarantined":
                    candidate_state.advance_file(entry["path"], "validated_oos",
                                                 reason="Passed batch OOS and FDR gates.")
                candidate_state.advance_file(entry["path"], target,
                                             reason=f"Batch FDR verdict -> {r['verdict']}.")
            except candidate_state.StateError as exc:
                log.info(f"[state] {r['candidate_id']} not advanced: {exc}")


def command_candidate_shadow(args) -> None:
    """Append an offline shadow snapshot for a shadow-state candidate (no orders)."""
    from project_geld.candidates.shadow import run_candidate_shadow
    from project_geld.candidates.state import load_record
    from project_geld.candidates.universe import bind_strategy
    from project_geld.data import normalize_bars

    config = load_config(args.config)
    validate_config(config)
    record = load_record(args.bundle)
    bundle = record.get("bundle", record)
    _check_candidate_for_evaluation(bundle)
    if record.get("state") != "shadow":
        raise RuntimeError(
            f"Candidate is in state {record.get('state')!r}, not 'shadow'. Run "
            "revalidate-candidate --advance-state until it reaches shadow first."
        )

    bars = normalize_bars(pd.read_csv(args.bars))
    benchmark = config.universe.benchmark.upper()
    strategy, binding = bind_strategy(bundle, bars, benchmark=benchmark, max_symbols=args.max_symbols)
    bars = bars[bars["symbol"].isin(set(binding.symbols) | {benchmark})]

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    snapshot = run_candidate_shadow(
        strategy, bars, backtest=config.backtest, risk=config.risk, benchmark=benchmark,
        tradable_symbols=binding.symbols, context_symbols=[benchmark],
        ledger_path=output / "shadow_ledger.jsonl",
        candidate_id=bundle.get("candidate_id"),
    )
    log.info(f"Shadow snapshot (research-only, NO orders): {snapshot['candidate_id']}")
    log.info(f"  as_of={snapshot['as_of_bar']} positions={snapshot['n_positions']} "
          f"gross={snapshot['gross_weight']} return={snapshot['total_return']:.2%} "
          f"sharpe={snapshot['sharpe']:.2f}")
    log.info(f"  ledger: {(output / 'shadow_ledger.jsonl').resolve()}")
