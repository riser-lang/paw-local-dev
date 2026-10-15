#!/usr/bin/python3
"""Run the bounded, governed PROJECT-first historical closure.

The runner is deliberately serial for the 2GB host.  It reads only the
already persisted Luna/Brain semantic artifacts and canonical rows; it does
not enumerate or read raw Source content.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if "/opt/paw-mcp-gateway" not in sys.path:
    sys.path.insert(0, "/opt/paw-mcp-gateway")

from paw_project_outcome import (
    DEFAULT_BRAIN, DEFAULT_BUILDER, DEFAULT_BUNDLES, DEFAULT_DB,
    build_relations, ensure_schema, outcome_payload, quality_gate,
    read_outcome, synthesize_project,
)
from paw_gpt_brain_policy_committer import commit_policy_write
from paw_bootstrap_risk_router import ROUTE_NO_ACTION, ROUTE_SAFE_AUTO_APPLY, classify_bundle

STATE_PATH = Path("/var/lib/paw-mcp-gateway/paw_project_first_closure.json")
INCREMENTAL_GATE_PATH = Path("/var/lib/paw-mcp-gateway/paw_project_first_incremental_gate.json")
SYSTEM_APPROVAL = "SYSTEM_EVIDENCE_GOVERNED_WRITE_V1"


def _load(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _conn(path: str) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _source_refs(outcome: dict[str, Any]) -> list[dict[str, Any]]:
    refs = [dict(x) for x in outcome.get("source_evidence_refs") or [] if isinstance(x, dict)]
    if refs:
        return refs
    return [{"formal_state_ref": f"project:{outcome.get('project_id')}:baseline"}]


def _source_ids(outcome: dict[str, Any]) -> list[str]:
    return sorted({str(x.get("source_version_id")) for x in outcome.get("source_evidence_refs") or [] if isinstance(x, dict) and x.get("source_version_id")})


def _write_outcome(db_path: str, outcome: dict[str, Any], *, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    pid = str(outcome["project_id"])
    refs = _source_refs(outcome)
    source_refs = [f"source_version:{x}" for x in _source_ids(outcome)] or [f"project:{pid}"]
    bundle = {
        "id": f"project-synthesis:{pid}:{outcome['input_watermark'][:24]}",
        "state": "CLEAR", "coverage_state": "CONSOLIDATED",
        "source_refs": source_refs, "evidence_refs": refs,
    }
    write_id = f"project-outcome:{pid}:{outcome['input_watermark'][:32]}"
    result = commit_policy_write(
        {"id": write_id, "target_type": "project", "operation": "update",
         "payload": outcome_payload(outcome), "source_refs": source_refs,
         "evidence_refs": refs, "approval_source": SYSTEM_APPROVAL,
         "preanalysis_model": "gpt-5.6-luna-reuse",
         "authoritative_model": "deterministic-project-synthesizer-v1"},
        db_path=db_path, bundle=bundle, mark_policy=False,
    )
    return result


def _write_relation(db_path: str, relation: dict[str, Any]) -> dict[str, Any]:
    refs = list(relation.get("evidence_refs") or [])
    if not refs:
        refs = [{"formal_state_ref": f"project:{relation['from_project_id']}:relation-input"}, {"formal_state_ref": f"project:{relation['to_project_id']}:relation-input"}]
    source_refs = [f"source_version:{x.get('source_version_id')}" for x in refs if x.get("source_version_id")]
    source_refs = source_refs or [f"project:{relation['from_project_id']}", f"project:{relation['to_project_id']}" ]
    bundle = {"id": f"project-relation:{relation['idempotency_key'][:24]}", "state": "CLEAR", "coverage_state": "CONSOLIDATED", "source_refs": source_refs, "evidence_refs": refs}
    return commit_policy_write(
        {"id": f"project-relation:{relation['idempotency_key'][:32]}", "target_type": "project", "operation": "update",
         "payload": {"id": relation["from_project_id"], "project_relation": relation},
         "source_refs": source_refs, "evidence_refs": refs, "approval_source": SYSTEM_APPROVAL,
         "preanalysis_model": "deterministic-project-synthesizer-v1",
         "authoritative_model": "deterministic-project-relation-v1"},
        db_path=db_path, bundle=bundle, mark_policy=False,
    )


def _read_relation(db_path: str, relation_id: str) -> bool:
    c = _conn(db_path)
    try:
        return c.execute("SELECT 1 FROM project_relation WHERE relation_id=?", (relation_id,)).fetchone() is not None
    finally:
        c.close()


def run_bootstrap(*, db_path: str = DEFAULT_DB, brain_path: str = DEFAULT_BRAIN, builder_path: str = DEFAULT_BUILDER, bundles_path: str = DEFAULT_BUNDLES, state_path: Path = STATE_PATH) -> dict[str, Any]:
    schema = ensure_schema(db_path)
    brain, builder, bundle_data = _load(brain_path), _load(builder_path), _load(bundles_path)
    bundles = bundle_data.get("bundles") or {}
    c = _conn(db_path)
    try:
        projects = [dict(x) for x in c.execute("SELECT * FROM project ORDER BY name, id").fetchall()]
        outcomes: dict[str, dict[str, Any]] = {}
        outcome_results: list[dict[str, Any]] = []
        for project in projects:
            outcome = synthesize_project(project, brain, builder, bundles, c)
            existing = read_outcome(db_path, project["id"])
            if existing and existing.get("input_watermark") == outcome.get("input_watermark") and existing.get("executive_summary") == project.get("latest_progress_summary"):
                outcome = {**outcome, "synthesized_at": existing.get("synthesized_at") or outcome["synthesized_at"], "synthesis_revision": existing.get("synthesis_revision") or 1}
                result = {"status": "ALREADY_PERSISTED", "idempotent": True, "project_id": project["id"]}
            else:
                if existing:
                    outcome["synthesis_revision"] = int(existing.get("synthesis_revision") or 1) + 1
                ok, reason = quality_gate(outcome, project["name"])
                if not ok:
                    result = {"status": "FAILED_CLOSED", "project_id": project["id"], "reason": reason}
                else:
                    result = _write_outcome(db_path, outcome, existing=existing)
            outcomes[project["id"]] = outcome
            result.update({"project_id": project["id"], "project_name": project["name"], "quality_gate": outcome.get("quality_gate"), "input_watermark": outcome.get("input_watermark")})
            if result.get("status") in {"COMMITTED", "ALREADY_PERSISTED"}:
                result["readback"] = read_outcome(db_path, project["id"])
                readback = result["readback"] or {}
                gate_ok, gate_reason = quality_gate(readback, project["name"])
                refs_ok = all(isinstance(x, dict) and x.get("source_version_id") and x.get("evidence_locator") for x in (readback.get("source_evidence_refs") or []))
                result["readback_quality_gate"] = gate_reason
                result["readback_pass"] = bool(readback and readback.get("input_watermark") == outcome.get("input_watermark") and gate_ok and (refs_ok or not readback.get("evidence_sufficient")))
            outcome_results.append(result)
    finally:
        c.close()

    # --- Phase-2 realtime closure -------------------------------------------
    # Bring the human-readable Handbook up to the Outcome that was just
    # committed.  Additive + idempotent: authored semantic sections are never
    # overwritten (see paw_project_handbook).  A failure here is recorded, not
    # swallowed - the freshness file reports CLOSURE_PENDING / BLOCKED.
    handbook_results: list[dict[str, Any]] = []
    try:
        import paw_project_handbook as _handbook_mod
        for project in projects:
            if project["id"] not in outcomes:
                continue
            handbook_results.append(_handbook_mod.close_handbook(
                db_path, project["id"],
                commit_policy_write=commit_policy_write,
                outcome=outcomes[project["id"]]))
    except Exception as exc:  # pragma: no cover - fail closed, never silent
        handbook_results.append({"status": "FAILED_CLOSED", "reason": str(exc)[:300]})

    relations = build_relations(projects, outcomes)
    relation_results = []
    for relation in relations:
        result = _write_relation(db_path, relation)
        result.update({"relation_id": relation["relation_id"], "from_project_id": relation["from_project_id"], "to_project_id": relation["to_project_id"], "relation_type": relation["relation_type"], "readback_pass": _read_relation(db_path, relation["relation_id"])})
        relation_results.append(result)

    # Attach relation references to the outcome records in a second governed,
    # idempotent pass.  This keeps Project readback self-contained.
    by_project: dict[str, list[dict[str, Any]]] = {}
    for r in relations:
        item = {"relation_id": r["relation_id"], "project_id": r["to_project_id"], "relation_type": r["relation_type"], "summary": r["summary"]}
        by_project.setdefault(r["from_project_id"], []).append(item)
        reverse = {**item, "project_id": r["from_project_id"]}
        by_project.setdefault(r["to_project_id"], []).append(reverse)
    relation_outcome_results = []
    for pid, refs in by_project.items():
        outcome = outcomes[pid]
        if outcome.get("related_projects") == refs:
            continue
        updated = {**outcome, "related_projects": refs, "synthesis_revision": int(outcome.get("synthesis_revision") or 1) + 1, "input_watermark": outcome["input_watermark"]}
        result = _write_outcome(db_path, updated)
        result.update({"project_id": pid, "readback_pass": bool(read_outcome(db_path, pid))})
        relation_outcome_results.append(result)

    # This is control-plane audit state, not canonical content.  It makes a
    # zero-relation run observable rather than silently treating it as PASS.
    relation_readback_pass = sum(1 for x in relation_results if x.get("readback_pass"))
    outcome_readback_pass = sum(1 for x in outcome_results if x.get("readback_pass"))
    report = {
        "schema": "paw-project-first-closure/1", "status": "COMPLETED" if outcome_readback_pass == len(projects) and relation_readback_pass == len(relations) and all(x.get("status") in {"COMMITTED", "ALREADY_PERSISTED"} for x in outcome_results) else "BLOCKED",
        "bootstrap_mode": "PROJECT_OUTCOME_FROM_PERSISTED_SEMANTIC_STATE",
        "bootstrap_total_projects": len(projects),
        "bootstrap_outcome_readback_pass": outcome_readback_pass,
        "cross_project_relation_readback_pass": relation_readback_pass,
        "project_results": outcome_results, "relation_analysis_executed": True,
        "relations": relation_results, "relation_outcome_results": relation_outcome_results,
        "formal_state_bypass": 0, "full_corpus_reread": False,
        "frozen_710_source_reread": False,
        "source_input": "persisted_luna_semantic_knowledge_evidence_only",
        "handbook_results": handbook_results,
        "handbook_committed": sum(1 for x in handbook_results if x.get("status") == "COMMITTED"),
        "handbook_already_fresh": sum(1 for x in handbook_results if x.get("status") == "ALREADY_FRESH"),
        "handbook_readback_pass": sum(1 for x in handbook_results if x.get("readback_pass")),
    }
    state_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # Project-first patrol may start once all machine-actionable writes have
    # drained, even while legitimate Terra/Opus/User/insufficient queues stay
    # durable.  This separate gate deliberately does not mutate the legacy
    # Brain Bootstrap status or counters.
    policy_writes = bundle_data.get("policy_writes") or {}
    ready_policy_writes = sum(1 for x in policy_writes.values() if isinstance(x, dict) and x.get("status") == "READY")
    blocked_bundles = sum(1 for x in bundles.values() if isinstance(x, dict) and x.get("state") == "BLOCKED")
    safe_pending = 0
    for bundle in bundles.values():
        if not isinstance(bundle, dict) or bundle.get("state") != "REVIEW_PENDING":
            continue
        decision = classify_bundle(bundle, brain, source_state={})
        if decision.get("route") in {ROUTE_SAFE_AUTO_APPLY, ROUTE_NO_ACTION}:
            safe_pending += 1
    readback_projects = sum(1 for x in outcome_results if x.get("readback_pass"))
    machine_backlog = ready_policy_writes + blocked_bundles + safe_pending
    gate = {
        "schema": "paw-project-first-incremental-gate/1",
        "status": "ENABLED" if machine_backlog == 0 and readback_projects == len(projects) else "BLOCKED",
        "machine_actionable_backlog_remaining": machine_backlog,
        "ready_policy_writes": ready_policy_writes,
        "blocked_bundles": blocked_bundles,
        "safe_or_no_action_review_pending": safe_pending,
        "baseline_projects": len(projects),
        "baseline_readback_pass": readback_projects,
        "full_corpus_reread": False,
        "formal_state_bypass": 0,
        "unresolved_queues_are_separate": True,
        # Informational: the PROJECT_HANDBOOK_UPDATE_GATE lives in
        # paw_project_freshness.json.  It is reported here so a stale handbook
        # can never hide behind an otherwise-green baseline gate.
        "project_handbook_committed": sum(1 for x in handbook_results if x.get("status") == "COMMITTED"),
        "project_handbook_readback_pass": sum(1 for x in handbook_results if x.get("readback_pass")),
    }
    INCREMENTAL_GATE_PATH.write_text(json.dumps(gate, ensure_ascii=False, indent=2), encoding="utf-8")
    report["incremental_gate"] = gate
    try:
        import paw_project_handbook as _handbook_mod
        report["project_freshness"] = _handbook_mod.write_freshness(db_path)
    except Exception as exc:  # pragma: no cover - fail closed, never silent
        report["project_freshness"] = {"status": "FAILED_CLOSED", "reason": str(exc)[:300]}
    state_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    report = run_bootstrap()
    print(json.dumps({
        "status": "COMPLETED" if all(x.get("readback_pass", False) for x in report["project_results"] if x.get("status") in {"COMMITTED", "ALREADY_PERSISTED"}) else "PARTIAL",
        "bootstrap_total_projects": report["bootstrap_total_projects"],
        "project_commits": sum(1 for x in report["project_results"] if x.get("status") == "COMMITTED"),
        "project_idempotent": sum(1 for x in report["project_results"] if x.get("status") == "ALREADY_PERSISTED"),
        "relations": len(report["relations"]), "relation_readback_pass": sum(1 for x in report["relations"] if x.get("readback_pass")),
        "formal_state_bypass": 0, "full_corpus_reread": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
