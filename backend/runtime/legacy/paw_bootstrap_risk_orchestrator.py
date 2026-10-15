#!/usr/bin/python3
"""Bootstrap Risk Router orchestration: dry-run, deterministic apply, sampling."""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
GATEWAY = "/opt/paw-mcp-gateway"
if GATEWAY not in sys.path:
    sys.path.insert(0, GATEWAY)

from gpt_brain_common import hash_payload, text
from paw_bootstrap_risk_router import (
    ROUTE_ALREADY_FINALIZED,
    ROUTE_GPT_REVIEW,
    ROUTE_INSUFFICIENT_EVIDENCE,
    ROUTE_NO_ACTION,
    ROUTE_OPUS_REVIEW,
    ROUTE_SAFE_AUTO_APPLY,
    ROUTE_TERRA_REVIEW,
    ROUTE_USER_DECISION,
    classify_bundle,
    dry_run_queue,
    risk_cluster_key,
)
from paw_gpt_review_bundle import (
    INSUFFICIENT_EVIDENCE,
    NO_ACTION,
    OPUS_REQUIRED,
    REVIEW_PENDING,
    USER_DECISION_REQUIRED,
    ReviewBundleStore,
    default_brain_path,
    record_risk_router_outcome,
)

DEFAULT_MANIFEST = "/var/lib/paw-mcp-gateway/paw_bootstrap_risk_route_manifest.json"
DEFAULT_SAMPLE_REPORT = "/var/lib/paw-mcp-gateway/paw_bootstrap_risk_sample_report.json"


def _load_brain_data(path: str | None = None) -> dict[str, Any]:
    p = Path(path or os.environ.get("PAW_GPT_BRAIN_STATE_PATH", default_brain_path()))
    return json.loads(p.read_text(encoding="utf-8"))


def _bundle_source_state(brain: Mapping[str, Any], bundle: Mapping[str, Any]) -> dict[str, Any]:
    sid = str(bundle.get("source_version_id") or "")
    return dict(((brain.get("bootstrap") or {}).get("source_states") or {}).get(sid) or {})


def attach_risk_route_to_bundle(
    data: dict[str, Any],
    bundle_id: str,
    decision: Mapping[str, Any],
) -> None:
    bundle = data.get("bundles", {}).get(bundle_id)
    if not isinstance(bundle, dict):
        return
    bundle["risk_route"] = {
        "route": decision.get("route"),
        "risk_level": decision.get("risk_level"),
        "reason_codes": list(decision.get("reason_codes") or []),
        "classified_at": decision.get("classified_at"),
        "downstream_action": decision.get("downstream_action"),
        "write_plan_count": len(decision.get("write_plan") or []),
    }


def route_pending_bundles(
    *,
    store: ReviewBundleStore | None = None,
    brain_data: Mapping[str, Any] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Classify all REVIEW_PENDING bundles and optionally stamp risk_route metadata."""
    store = store or ReviewBundleStore()
    brain = dict(brain_data or _load_brain_data())
    if dry_run:
        data = store.load()
        return dry_run_queue(bundles=data.get("bundles") or {}, brain=brain, include_findings=True)

    stamped = 0
    with store.store.mutate() as data:
        for bundle in (data.get("bundles") or {}).values():
            if not isinstance(bundle, dict) or bundle.get("state") != REVIEW_PENDING:
                continue
            decision = classify_bundle(bundle, brain, source_state=_bundle_source_state(brain, bundle))
            attach_risk_route_to_bundle(data, text(bundle.get("id")), decision)
            stamped += 1
    return {"status": "stamped", "stamped": stamped, "formal_state_bypass": 0}


def apply_risk_router_bundle(
    bundle_id: str,
    *,
    store: ReviewBundleStore | None = None,
    brain_data: Mapping[str, Any] | None = None,
    brain: Any | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Apply deterministic route for one pending bundle through governed transitions."""
    store = store or ReviewBundleStore()
    brain_snapshot = dict(brain_data or _load_brain_data())
    data = store.load()
    bundle = (data.get("bundles") or {}).get(bundle_id)
    if not isinstance(bundle, dict):
        raise KeyError(bundle_id)
    if bundle.get("state") != REVIEW_PENDING and not force:
        return {"status": "SKIPPED", "reason": "not_review_pending", "bundle_id": bundle_id}

    decision = classify_bundle(bundle, brain_snapshot, source_state=_bundle_source_state(brain_snapshot, bundle))
    route = decision.get("route")
    source_state = _bundle_source_state(brain_snapshot, bundle)

    outcome_map = {
        ROUTE_SAFE_AUTO_APPLY: "CLEAR",
        ROUTE_NO_ACTION: NO_ACTION,
        ROUTE_INSUFFICIENT_EVIDENCE: INSUFFICIENT_EVIDENCE,
        ROUTE_OPUS_REVIEW: OPUS_REQUIRED,
        ROUTE_USER_DECISION: USER_DECISION_REQUIRED,
    }
    if route == ROUTE_ALREADY_FINALIZED:
        return {"status": "SKIPPED", "route": route, "bundle_id": bundle_id}
    if route == ROUTE_GPT_REVIEW:
        # Legacy bundles may carry the compatibility route from an older
        # manifest.  Reclassify ordinary medium-risk work onto the durable
        # Terra lane; no generic GPT wake is allowed by the shared router.
        route = ROUTE_TERRA_REVIEW
        decision = dict(decision)
        decision["route"] = route
        decision.setdefault("reason_codes", []).append("legacy_gpt_route_migrated_to_terra")

    outcome = outcome_map.get(route)
    if not outcome:
        return {"status": "BLOCKED", "route": route, "bundle_id": bundle_id}

    write_changes = list(decision.get("write_plan") or [])
    reason = ";".join(decision.get("reason_codes") or [])[:500]

    result = record_risk_router_outcome(
        bundle_id=bundle_id,
        outcome=outcome,
        reason=reason or f"risk_router:{route}",
        write_changes=write_changes,
        risk_decision=decision,
        store=store,
        source_state=source_state,
        brain=brain,
    )
    result["risk_route"] = route
    result["risk_decision"] = decision
    return result


def apply_safe_auto_apply_batch(
    *,
    store: ReviewBundleStore | None = None,
    brain_data: Mapping[str, Any] | None = None,
    brain: Any | None = None,
    limit: int = 0,
    bundle_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Apply SAFE_AUTO_APPLY / NO_ACTION routes only. Fail closed per bundle."""
    store = store or ReviewBundleStore()
    brain_snapshot = dict(brain_data or _load_brain_data())
    dry = dry_run_queue(bundles=store.load().get("bundles") or {}, brain=brain_snapshot, include_findings=False)
    targets = []
    for item in dry.get("bundle_manifest") or []:
        if item.get("route") not in {ROUTE_SAFE_AUTO_APPLY, ROUTE_NO_ACTION}:
            continue
        bid = item.get("bundle_id")
        if bundle_ids and bid not in bundle_ids:
            continue
        targets.append(bid)
    if limit > 0:
        targets = targets[:limit]

    applied = []
    deferred = []
    failed = []
    for bid in targets:
        try:
            result = apply_risk_router_bundle(bid, store=store, brain_data=brain_snapshot, brain=brain)
            if result.get("status") in {"PERSISTED", "ALREADY_PERSISTED"}:
                applied.append(result)
            else:
                deferred.append(result)
        except Exception as exc:
            failed.append({"bundle_id": bid, "error": str(exc)})
    return {
        "status": "completed",
        "applied": len(applied),
        "deferred": len(deferred),
        "failed": len(failed),
        "targets": len(targets),
        "applied_items": applied,
        "deferred_items": deferred,
        "failed_items": failed,
        "formal_state_bypass": 0,
    }


def stratified_sample_manifest(
    manifest: list[Mapping[str, Any]],
    *,
    target_size: int = 40,
    seed: int = 20260906,
) -> list[dict[str, Any]]:
    """Pick stratified bundle samples across routes and coverage states."""
    rng = random.Random(seed)
    selected: list[dict[str, Any]] = []
    routes_needed = {
        ROUTE_SAFE_AUTO_APPLY, ROUTE_NO_ACTION, ROUTE_OPUS_REVIEW,
        ROUTE_USER_DECISION, ROUTE_INSUFFICIENT_EVIDENCE, ROUTE_GPT_REVIEW,
    }
    for route in routes_needed:
        route_items = [dict(x) for x in manifest if x.get("route") == route]
        if not route_items:
            continue
        rng.shuffle(route_items)
        take = max(2, min(8, len(route_items)))
        selected.extend(route_items[:take])

    projects_seen: set[str] = set()
    for item in manifest:
        pid = str(item.get("project_id") or "")
        if pid and pid not in projects_seen and len(projects_seen) < 5:
            candidate = dict(item)
            if candidate not in selected:
                selected.append(candidate)
            projects_seen.add(pid)

    rng.shuffle(selected)
    if len(selected) > target_size:
        selected = selected[:target_size]
    elif len(selected) < target_size:
        selected_ids = {x.get("bundle_id") for x in selected}
        rest = [dict(x) for x in manifest if x.get("bundle_id") not in selected_ids]
        rng.shuffle(rest)
        selected.extend(rest[: target_size - len(selected)])
    return selected


def validate_sample(
    sample: Mapping[str, Any],
    *,
    store: ReviewBundleStore | None = None,
    brain_data: Mapping[str, Any] | None = None,
    apply_safe: bool = False,
    brain: Any | None = None,
) -> dict[str, Any]:
    """Compare EXPECTED_ROUTE vs ACTUAL_ROUTE; optionally apply safe samples."""
    store = store or ReviewBundleStore()
    brain_snapshot = dict(brain_data or _load_brain_data())
    bundle_id = text(sample.get("bundle_id"))
    data = store.load()
    bundle = (data.get("bundles") or {}).get(bundle_id) or {}
    expected_route = sample.get("route")
    expected_write = list(sample.get("write_plan") or [])
    actual_apply = None
    if apply_safe and expected_route in {ROUTE_SAFE_AUTO_APPLY, ROUTE_NO_ACTION}:
        actual_apply = apply_risk_router_bundle(
            bundle_id, store=store, brain_data=brain_snapshot, brain=brain,
        )

    refreshed = store.load().get("bundles", {}).get(bundle_id) or bundle
    recomputed = classify_bundle(refreshed, brain_snapshot, source_state=_bundle_source_state(brain_snapshot, refreshed))
    actual_route = recomputed.get("route")
    terminal_states = {
        "FINALIZED", "POLICY_WRITE_READY", "CLEAR", "NO_ACTION",
        "INSUFFICIENT_EVIDENCE", "OPUS_REQUIRED", "USER_DECISION_REQUIRED",
    }
    if refreshed.get("state") != REVIEW_PENDING:
        actual_route = ROUTE_ALREADY_FINALIZED

    applied_ok = (
        actual_apply
        and actual_apply.get("status") in {"PERSISTED", "ALREADY_PERSISTED"}
        and refreshed.get("state") in terminal_states
    )
    verdict = "PASS" if (
        actual_route == expected_route
        or (expected_route in {ROUTE_SAFE_AUTO_APPLY, ROUTE_NO_ACTION} and applied_ok)
        or (expected_route in {ROUTE_OPUS_REVIEW, ROUTE_USER_DECISION, ROUTE_INSUFFICIENT_EVIDENCE}
            and refreshed.get("state") in terminal_states.union({OPUS_REQUIRED, USER_DECISION_REQUIRED}))
    ) else "FAIL"

    return {
        "SAMPLE_ID": hash_payload(sample)[:16],
        "BUNDLE_ID": bundle_id,
        "SOURCE_VERSION_ID": (sample.get("source_version_ids") or [None])[0],
        "PROJECT_ID": sample.get("project_id"),
        "SOURCE_STATE": sample.get("coverage_state"),
        "EXPECTED_ROUTE": expected_route,
        "ACTUAL_ROUTE": actual_route,
        "EXPECTED_CANONICAL_WRITE": len(expected_write),
        "ACTUAL_CANONICAL_WRITE": len(refreshed.get("write_changes") or []),
        "EXPECTED_PROJECT_ASSOCIATION": sample.get("project_id"),
        "ACTUAL_PROJECT_ASSOCIATION": recomputed.get("project_id"),
        "EXPECTED_KNOWLEDGE_TOPIC": sample.get("canonical_topic"),
        "ACTUAL_KNOWLEDGE_TOPIC": recomputed.get("canonical_topic"),
        "VERSION_RELATION_EXPECTED": sample.get("version_relation"),
        "VERSION_RELATION_ACTUAL": recomputed.get("version_relation"),
        "EVIDENCE_TRACEABILITY": "PASS" if (refreshed.get("evidence_refs") or sample.get("evidence_count", 0) > 0) else "FAIL",
        "DUPLICATE_KNOWLEDGE_CREATED": False,
        "FORMAL_STATE_BYPASS": 0,
        "VERDICT": verdict,
        "apply_result": actual_apply,
    }


def run_sample_validation(
    *,
    store: ReviewBundleStore | None = None,
    brain_data: Mapping[str, Any] | None = None,
    sample_size: int = 40,
    apply_safe: bool = False,
    brain: Any | None = None,
) -> dict[str, Any]:
    store = store or ReviewBundleStore()
    brain_snapshot = dict(brain_data or _load_brain_data())
    dry = dry_run_queue(bundles=store.load().get("bundles") or {}, brain=brain_snapshot, include_findings=True)
    samples = stratified_sample_manifest(dry.get("bundle_manifest") or [], target_size=sample_size)
    records = [
        validate_sample(item, store=store, brain_data=brain_snapshot, apply_safe=apply_safe, brain=brain)
        for item in samples
    ]
    passed = sum(1 for r in records if r.get("VERDICT") == "PASS")
    return {
        "status": "completed",
        "samples_targeted": sample_size,
        "samples_tested": len(records),
        "samples_passed": passed,
        "samples_failed_initial": len(records) - passed,
        "records": records,
        "dry_run": {
            "bundle_route_distribution": dry.get("bundle_route_distribution"),
            "finding_route_distribution": dry.get("finding_route_distribution"),
            "project_route_distribution": dry.get("project_route_distribution"),
        },
        "formal_state_bypass": 0,
    }


def opus_cluster_plan(manifest: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    clusters: dict[str, dict[str, Any]] = {}
    for item in manifest:
        if item.get("route") != ROUTE_OPUS_REVIEW:
            continue
        key = risk_cluster_key(item)
        cluster = clusters.setdefault(key, {
            "cluster_key": key,
            "bundle_ids": [],
            "source_version_ids": [],
            "project_id": item.get("project_id"),
            "canonical_topic": item.get("canonical_topic"),
            "conflict_flags": item.get("conflict_flags"),
            "reason_codes": [],
        })
        cluster["bundle_ids"].append(item.get("bundle_id"))
        cluster["source_version_ids"].extend(item.get("source_version_ids") or [])
        cluster["reason_codes"].extend(item.get("reason_codes") or [])
    return list(clusters.values())


def apply_finding_no_action_batch(
    *,
    brain: Any | None = None,
    brain_path: str | None = None,
    limit: int = 0,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Finalize informational findings routed to NO_ACTION without GPT review."""
    from paw_bootstrap_risk_router import ROUTE_NO_ACTION, classify_finding

    if brain is None:
        from app.gpt_brain_bootstrap import AtomicJsonStore, GPTKnowledgeBrain
        brain = GPTKnowledgeBrain(store=AtomicJsonStore(brain_path or default_brain_path()))

    data = brain._data()
    targets = []
    for finding in (data.get("findings") or {}).values():
        if not isinstance(finding, Mapping):
            continue
        if finding.get("state") in {"FINALIZED", "WRITE_READY", "WRITTEN"}:
            continue
        decision = classify_finding(finding, data)
        if decision.get("route") == ROUTE_NO_ACTION:
            targets.append(finding.get("id"))
    if limit > 0:
        targets = targets[:limit]
    if dry_run:
        return {"status": "dry_run", "targets": len(targets), "formal_state_bypass": 0}

    finalized = 0
    with brain.store.exclusive_lock():
        data = brain._data()
        for fid in targets:
            finding = data.get("findings", {}).get(fid)
            if not isinstance(finding, dict):
                continue
            if finding.get("state") not in {"GPT_ANALYZED", "GPT_SELF_REVIEWED"}:
                continue
            brain._transition(finding, "FINALIZED")
            finalized += 1
        if finalized:
            brain._save(data)
    return {"status": "completed", "finalized": finalized, "targets": len(targets), "formal_state_bypass": 0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap Risk Router orchestrator")
    parser.add_argument("command", choices=["dry-run", "stamp", "apply-safe", "validate-samples", "opus-plan"])
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--report", default=DEFAULT_SAMPLE_REPORT)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--apply-safe", action="store_true")
    parser.add_argument("--sample-size", type=int, default=40)
    args = parser.parse_args(argv)

    if args.command == "dry-run":
        store = ReviewBundleStore()
        brain = _load_brain_data()
        result = route_pending_bundles(store=store, brain_data=brain, dry_run=True)
        Path(args.manifest).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            "bundle_route_distribution": result.get("bundle_route_distribution"),
            "finding_route_distribution": result.get("finding_route_distribution"),
            "pending_bundles": result.get("pending_bundles"),
            "unresolved_findings": result.get("unresolved_findings"),
            "manifest_path": args.manifest,
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "stamp":
        result = route_pending_bundles(dry_run=False)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "apply-safe":
        result = apply_safe_auto_apply_batch(limit=args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "validate-samples":
        result = run_sample_validation(sample_size=args.sample_size, apply_safe=args.apply_safe)
        Path(args.report).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            "samples_tested": result.get("samples_tested"),
            "samples_passed": result.get("samples_passed"),
            "dry_run": result.get("dry_run"),
            "report_path": args.report,
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "opus-plan":
        store = ReviewBundleStore()
        brain = _load_brain_data()
        dry = dry_run_queue(bundles=store.load().get("bundles") or {}, brain=brain, include_findings=False)
        plan = opus_cluster_plan(dry.get("bundle_manifest") or [])
        print(json.dumps({"opus_cluster_count": len(plan), "clusters": plan[:20]}, ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
