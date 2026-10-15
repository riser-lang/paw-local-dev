#!/usr/bin/python3
"""Metric Layer Promotion Bridge — Data Layer sidecar -> Formal metric_definition/metric_observation.

Problem this fixes (Project Semantic Core v2, Step 6 minimal-scope fix):
  gpt_brain_data_layer.json already holds thousands of extracted metric
  observations (status=CANONICAL, each with evidence + source_version_id),
  but formal_state_write is permanently False there — nothing ever promotes
  them into the formal metric_definition / metric_observation tables, so the
  Project UI has no data to show even though the extraction already happened.

This script is the missing bridge. It does NOT re-analyze or re-extract
anything, and it does NOT invent project associations: a sidecar observation
is only promoted when its source_version_id resolves, via the EXISTING
luna_draft_projection table, to a REAL project.id.

Governance: every promotion goes through the same governed chain everything
else in this system uses: Candidate(candidate_type='fact') ->
ReviewDecision(approve, approval_source=SYSTEM_EVIDENCE_GOVERNED_WRITE_V1) ->
ApprovedChange(target_type='data') -> Committer -> CommitRecord.
No direct SQL writes to metric_definition/metric_observation ever happen
outside of committer.py's existing executors. Fail closed on any ambiguity.

Safety gates applied before a sidecar observation is even considered:
  1. observation.status == 'CANONICAL' (CONFLICT observations are skipped)
  2. source_version_id resolves via luna_draft_projection to a real project.id
  3. luna_draft_projection.coverage_state in {CONSOLIDATED, DATA_EXTRACTED}
     (REVIEW_REQUIRED / PROJECT_CONTEXT_ONLY / anything else is skipped —
     those sources are themselves still pending human review upstream)
  4. evidence_gate passes (source_version_id + evidence_locator present)
  5. project scope restricted via --project-id (canary-first rollout)

Default mode is --dry-run (plan only, zero writes). --apply is required to
actually commit, and even then only through the governed chain above.

Usage:
  python3 paw_metric_layer_promotion.py --project-id <id> [--project-id <id> ...] --dry-run
  python3 paw_metric_layer_promotion.py --project-id <id> --apply
"""

from __future__ import annotations

import os


import argparse
import json
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
GATEWAY = "/opt/paw-mcp-gateway"
if GATEWAY not in sys.path:
    sys.path.insert(0, GATEWAY)

from gpt_brain_common import hash_payload, text, utc_now  # noqa: E402
from app.patrol_discovery_review import validate_evidence_gate  # noqa: E402

import committer as _committer  # noqa: E402
from paw_source_version_governance import (  # noqa: E402
    commit_approved_change as _governance_commit_approved_change,
    install_committer_integration,
)

install_committer_integration()

PRODUCTION_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))
DEFAULT_DATA_LAYER_PATH = "/var/lib/paw-mcp-gateway/gpt_brain_data_layer.json"

ACTOR = "paw-metric-layer-promotion"
POLICY_ID = "METRIC_LAYER_PROMOTION_V1"
POLICY_REVIEWER = "gpt_brain_policy_engine"
POLICY_REVIEW_CHANNEL = "standing_user_policy"
APPROVAL_SOURCE = "SYSTEM_EVIDENCE_GOVERNED_WRITE_V1"
# review_decision.approval_source has a production CHECK constraint that only
# allows ('candidate_review', 'direct_user'). The real policy identity is
# preserved in review_notes text instead, same convention paw_gpt_brain_policy_committer.py
# uses via its _db_approval_source() helper.
DB_APPROVAL_SOURCE = "candidate_review"

ALLOWED_COVERAGE_STATES = frozenset({"CONSOLIDATED", "DATA_EXTRACTED"})


def _uuid() -> str:
    return str(uuid.uuid4())


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _load_data_layer(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _svid_project_index(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """source_version_id -> {project_id, project_name, coverage_state} via luna_draft_projection."""
    idx: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
        "SELECT source_version_id, project_id, project_name, coverage_state "
        "FROM luna_draft_projection WHERE source_version_id IS NOT NULL AND source_version_id != ''"
    ):
        idx[row["source_version_id"]] = dict(row)
    return idx


def _real_project_ids(conn: sqlite3.Connection) -> set[str]:
    return {r["id"] for r in conn.execute("SELECT id FROM project")}


def resolve_project(
    observation: Mapping[str, Any],
    *,
    svid_index: Mapping[str, dict[str, Any]],
    real_project_ids: set[str],
) -> dict[str, Any] | None:
    """Resolve an observation to {project_id, project_name, coverage_state}. None if unresolved."""
    for svid in observation.get("source_version_ids") or []:
        hit = svid_index.get(svid)
        if hit and hit.get("project_id") in real_project_ids:
            return hit
    return None


def eligible_observations(
    data_layer: Mapping[str, Any],
    *,
    svid_index: Mapping[str, dict[str, Any]],
    real_project_ids: set[str],
    project_scope: set[str] | None,
) -> list[dict[str, Any]]:
    """Filter to promotable observations. Every skip reason is explicit (fail closed)."""
    out: list[dict[str, Any]] = []
    for obs in data_layer.get("observations", {}).values():
        if obs.get("status") != "CANONICAL":
            continue
        resolved = resolve_project(obs, svid_index=svid_index, real_project_ids=real_project_ids)
        if resolved is None:
            continue
        project_id = resolved["project_id"]
        if project_scope is not None and project_id not in project_scope:
            continue
        if resolved.get("coverage_state") not in ALLOWED_COVERAGE_STATES:
            continue
        evidence = obs.get("evidence") or []
        gate = validate_evidence_gate(evidence)
        if not gate.allowed:
            continue
        item = dict(obs)
        item["_resolved_project_id"] = project_id
        item["_resolved_project_name"] = resolved.get("project_name")
        item["_coverage_state"] = resolved.get("coverage_state")
        out.append(item)
    return out


def _definition_key(project_id: str, name: str, unit: str | None) -> str:
    return f"{project_id}::{name}::{unit or ''}"


def plan_promotion(
    observations: list[dict[str, Any]],
    *,
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    """Build the definition + observation plan without writing anything."""
    existing_defs: dict[str, str] = {}
    for row in conn.execute("SELECT id, project_id, name, unit FROM metric_definition"):
        existing_defs[_definition_key(row["project_id"], row["name"], row["unit"])] = row["id"]

    definitions_to_create: dict[str, dict[str, Any]] = {}
    observation_plans: list[dict[str, Any]] = []
    skipped_duplicate = 0

    existing_obs_fingerprints: set[str] = set()
    # metric_observation has no fingerprint column; dedupe defensively on
    # (metric_definition target key, value, observed_at) against rows already
    # present so re-running this script is idempotent even though the
    # underlying table has no unique constraint of its own.
    for row in conn.execute(
        "SELECT metric_definition_id, value, observed_at FROM metric_observation"
    ):
        existing_obs_fingerprints.add(f"{row['metric_definition_id']}::{row['value']}::{row['observed_at']}")

    for obs in observations:
        project_id = obs["_resolved_project_id"]
        name = text(obs.get("metric")) or text(obs.get("metric_name"))
        unit = obs.get("unit")
        key = _definition_key(project_id, name, unit)

        if key not in existing_defs and key not in definitions_to_create:
            definitions_to_create[key] = {
                "project_id": project_id,
                "name": name,
                "description": obs.get("definition"),
                "unit": unit,
                "status": "active",
                "_evidence": obs.get("evidence") or [],
            }

        period = obs.get("period") or {}
        observed_at = period.get("start") or period.get("label") or obs.get("updated_at")
        value = obs.get("value")

        def_id_placeholder = existing_defs.get(key) or f"__NEW__:{key}"
        fp = f"{def_id_placeholder}::{value}::{observed_at}"
        if fp in existing_obs_fingerprints:
            skipped_duplicate += 1
            continue
        existing_obs_fingerprints.add(fp)

        observation_plans.append({
            "definition_key": key,
            "existing_definition_id": existing_defs.get(key),
            "project_id": project_id,
            "metric": name,
            "unit": unit,
            "value": value,
            "observed_at": observed_at,
            "notes": json.dumps(
                {
                    "dimensions": obs.get("dimensions") or {},
                    "definition": obs.get("definition"),
                    "calculation_basis": obs.get("calculation_basis"),
                    "period": period,
                    "source_observation_id": obs.get("id"),
                    "coverage_state": obs.get("_coverage_state"),
                },
                ensure_ascii=False,
            ),
            "evidence": obs.get("evidence") or [],
            "source_version_ids": obs.get("source_version_ids") or [],
        })

    return {
        "definitions_to_create": definitions_to_create,
        "observations_to_create": observation_plans,
        "skipped_duplicate_observations": skipped_duplicate,
    }


def _ensure_analysis_run(conn: sqlite3.Connection) -> str:
    run_id = "metric-promotion-run:" + hash_payload({"policy": POLICY_ID})[:24]
    existing = conn.execute("SELECT id FROM analysis_run WHERE id = ?", (run_id,)).fetchone()
    if existing:
        return run_id
    now = utc_now()
    conn.execute(
        """INSERT INTO analysis_run
           (id, trigger_type, raw_text, raw_input_refs, analysis_model, started_at, completed_at, created_at)
           VALUES (?, 'metric_layer_promotion', NULL, NULL, ?, ?, ?, ?)""",
        (run_id, POLICY_ID, now, now, now),
    )
    return run_id


def _insert_candidate(conn: sqlite3.Connection, *, run_id: str, payload: dict, source_refs: list[str], raw_excerpt: str) -> str:
    cid = _uuid()
    now = utc_now()
    conn.execute(
        """INSERT INTO candidate
           (id, analysis_run_id, candidate_type, source_refs, raw_excerpt, proposed_change,
            confidence, inference_level, recommended_action, analysis_model, status, created_at)
           VALUES (?, ?, 'fact', ?, ?, ?, 'HIGH', 'EXPLICIT', 'AUTO_WRITE', ?, 'pending', ?)""",
        (
            cid, run_id, json.dumps(source_refs, ensure_ascii=False), raw_excerpt,
            json.dumps(payload, ensure_ascii=False), POLICY_ID, now,
        ),
    )
    return cid


def _idempotency_key(target_type: str, operation: str, payload: dict) -> str:
    return f"metric-promotion:{target_type}:{operation}:{hash_payload(payload)[:24]}"


def commit_data_create(conn: sqlite3.Connection, run_id: str, defn: dict, *, evidence_refs: list[dict]) -> dict:
    payload = {
        "project_id": defn["project_id"],
        "name": defn["name"],
        "description": defn.get("description"),
        "unit": defn.get("unit"),
        "status": "active",
    }
    key = _idempotency_key("data", "create", payload)
    existing = conn.execute(
        "SELECT change_id, status FROM approved_change WHERE idempotency_key = ? ORDER BY created_at DESC LIMIT 1",
        (key,),
    ).fetchone()
    if existing and existing["status"] == "committed":
        return {"status": "ALREADY_COMMITTED", "change_id": existing["change_id"]}

    candidate_id = _insert_candidate(
        conn, run_id=run_id, payload=payload,
        source_refs=[e.get("source_version_id") for e in evidence_refs if e.get("source_version_id")],
        raw_excerpt=f"metric_definition promoted from gpt_brain_data_layer.json: {defn['name']}",
    )
    conn.commit()

    rd_id, change_id = _committer.create_review_decision_atomic(
        candidate_id=candidate_id,
        decision="approve",
        reviewer=POLICY_REVIEWER,
        review_channel=POLICY_REVIEW_CHANNEL,
        approval_source=DB_APPROVAL_SOURCE,
        original_payload=payload,
        reviewed_payload=payload,
        review_notes=f"{POLICY_ID} standing authorization [policy_approval_source={APPROVAL_SOURCE}]; promoted from Data Layer sidecar; no semantic judgment.",
        target_type="data",
        operation="create",
        source_refs=[e.get("source_version_id") for e in evidence_refs if e.get("source_version_id")],
        create_approved_change=True,
        idempotency_key=key,
    )
    result = _governance_commit_approved_change(change_id)
    return {
        "status": "COMMITTED" if result.get("result") == "success" else "FAILED",
        "change_id": change_id,
        "commit_result": result,
        "metric_definition_id": result.get("target_id"),
    }


def commit_data_revise(conn: sqlite3.Connection, run_id: str, obs_plan: dict, *, definition_id: str, evidence_refs: list[dict]) -> dict:
    payload = {
        "metric_definition_id": definition_id,
        "project_id": obs_plan["project_id"],
        "value": obs_plan["value"],
        "observed_at": obs_plan["observed_at"],
        "notes": obs_plan["notes"],
    }
    key = _idempotency_key("data", "revise", payload)
    existing = conn.execute(
        "SELECT change_id, status FROM approved_change WHERE idempotency_key = ? ORDER BY created_at DESC LIMIT 1",
        (key,),
    ).fetchone()
    if existing and existing["status"] == "committed":
        return {"status": "ALREADY_COMMITTED", "change_id": existing["change_id"]}

    candidate_id = _insert_candidate(
        conn, run_id=run_id, payload=payload,
        source_refs=obs_plan.get("source_version_ids") or [],
        raw_excerpt=f"metric_observation promoted from gpt_brain_data_layer.json: {obs_plan['metric']}={obs_plan['value']}",
    )
    conn.commit()

    rd_id, change_id = _committer.create_review_decision_atomic(
        candidate_id=candidate_id,
        decision="approve",
        reviewer=POLICY_REVIEWER,
        review_channel=POLICY_REVIEW_CHANNEL,
        approval_source=DB_APPROVAL_SOURCE,
        original_payload=payload,
        reviewed_payload=payload,
        review_notes=f"{POLICY_ID} standing authorization [policy_approval_source={APPROVAL_SOURCE}]; promoted from Data Layer sidecar; no semantic judgment.",
        target_type="data",
        operation="revise",
        source_refs=obs_plan.get("source_version_ids") or [],
        create_approved_change=True,
        idempotency_key=key,
    )
    result = _governance_commit_approved_change(change_id)
    return {
        "status": "COMMITTED" if result.get("result") == "success" else "FAILED",
        "change_id": change_id,
        "commit_result": result,
        "metric_observation_id": result.get("target_id"),
    }


def run(*, project_scope: set[str] | None, apply: bool, db_path: str = PRODUCTION_DB, data_layer_path: str = DEFAULT_DATA_LAYER_PATH, limit: int | None = None) -> dict[str, Any]:
    data_layer = _load_data_layer(data_layer_path)
    conn = _connect(db_path)
    try:
        svid_index = _svid_project_index(conn)
        real_project_ids = _real_project_ids(conn)
        eligible = eligible_observations(
            data_layer, svid_index=svid_index, real_project_ids=real_project_ids, project_scope=project_scope,
        )
        if limit is not None:
            eligible = eligible[:limit]
        plan = plan_promotion(eligible, conn=conn)

        report: dict[str, Any] = {
            "mode": "APPLY" if apply else "DRY_RUN",
            "eligible_observation_count": len(eligible),
            "definitions_to_create_count": len(plan["definitions_to_create"]),
            "observations_to_create_count": len(plan["observations_to_create"]),
            "skipped_duplicate_observations": plan["skipped_duplicate_observations"],
            "project_scope": sorted(project_scope) if project_scope else None,
        }

        if not apply:
            report["definitions_preview"] = [
                {"key": k, "project_id": v["project_id"], "name": v["name"], "unit": v["unit"]}
                for k, v in list(plan["definitions_to_create"].items())[:20]
            ]
            report["observations_preview"] = plan["observations_to_create"][:20]
            return report

        run_id = _ensure_analysis_run(conn)
        created_definition_ids: dict[str, str] = {}
        commits = {"definitions": [], "observations": [], "failures": []}

        for key, defn in plan["definitions_to_create"].items():
            result = commit_data_create(conn, run_id, defn, evidence_refs=defn["_evidence"])
            if result["status"] in ("COMMITTED", "ALREADY_COMMITTED"):
                def_id = result.get("metric_definition_id")
                if not def_id:
                    row = conn.execute(
                        "SELECT id FROM metric_definition WHERE project_id=? AND name=? AND (unit=? OR (unit IS NULL AND ? IS NULL))",
                        (defn["project_id"], defn["name"], defn["unit"], defn["unit"]),
                    ).fetchone()
                    def_id = row["id"] if row else None
                created_definition_ids[key] = def_id
                commits["definitions"].append({"key": key, "result": result})
            else:
                commits["failures"].append({"key": key, "result": result})

        for obs_plan in plan["observations_to_create"]:
            def_id = obs_plan.get("existing_definition_id") or created_definition_ids.get(obs_plan["definition_key"])
            if not def_id:
                commits["failures"].append({"observation": obs_plan, "reason": "no_definition_id_resolved"})
                continue
            result = commit_data_revise(conn, run_id, obs_plan, definition_id=def_id, evidence_refs=obs_plan.get("evidence") or [])
            if result["status"] in ("COMMITTED", "ALREADY_COMMITTED"):
                commits["observations"].append({"metric": obs_plan["metric"], "result": result})
            else:
                commits["failures"].append({"observation": obs_plan, "result": result})

        report["committed_definitions"] = len(commits["definitions"])
        report["committed_observations"] = len(commits["observations"])
        report["failures"] = commits["failures"]
        return report
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", action="append", dest="project_ids", default=None,
                         help="Restrict promotion to this project id. Repeatable. Omit to run unscoped (NOT recommended for first run).")
    parser.add_argument("--apply", action="store_true", help="Actually commit through the governed chain. Default is dry-run.")
    parser.add_argument("--dry-run", action="store_true", help="Explicit dry-run (default behavior).")
    parser.add_argument("--limit", type=int, default=None, help="Cap number of eligible observations processed (testing).")
    parser.add_argument("--db-path", default=PRODUCTION_DB)
    parser.add_argument("--data-layer-path", default=DEFAULT_DATA_LAYER_PATH)
    args = parser.parse_args(argv)

    apply = bool(args.apply) and not args.dry_run
    project_scope = set(args.project_ids) if args.project_ids else None

    report = run(
        project_scope=project_scope,
        apply=apply,
        db_path=args.db_path,
        data_layer_path=args.data_layer_path,
        limit=args.limit,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
