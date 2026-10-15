#!/usr/bin/python3
"""Policy committer for GPT_CLEAR_AUTO_WRITE_V1 exact write-ready changes.

Consumes only policy-write-ready envelopes. Creates a truthful governed chain:
Candidate -> ReviewDecision(reviewer=gpt_brain_policy_engine,
review_channel=standing_user_policy, approval_source=gpt_clear_auto_write_v1
or opus_clear_auto_write_v1) -> ApprovedChange -> Committer -> CommitRecord.

No semantic model invocation. Fail closed on schema/version/identity conflict.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
GATEWAY = "/opt/paw-mcp-gateway"
if GATEWAY not in sys.path:
    sys.path.insert(0, GATEWAY)

from gpt_brain_common import FileLeaseLock, hash_payload, text, utc_now
from paw_gpt_clear_auto_write_policy import (
    APPROVAL_GPT_CLEAR,
    APPROVAL_OPUS_CLEAR,
    APPROVAL_RISK_ROUTER,
    SYSTEM_EVIDENCE_GOVERNED_WRITE_V1,
    APPROVAL_USER_RECEIPT,
    POLICY_ID,
    POLICY_REVIEW_CHANNEL,
    POLICY_REVIEWER,
    evaluate_write_change,
)
from paw_gpt_review_bundle import (
    ReviewBundleStore,
    attach_user_cluster_candidate,
    default_brain_path,
    default_store_path,
    list_policy_write_ready,
    mark_policy_failed,
    mark_policy_written,
)
from paw_review_display_normalization import normalize_user_decision_card

import committer as _committer
from paw_source_version_governance import (
    commit_approved_change as _governance_commit_approved_change,
    install_committer_integration,
)

install_committer_integration()

PRODUCTION_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))
ACTOR = "paw-gpt-brain-policy-committer"
DEFAULT_POLL_SECONDS = 15
STANDING_POLICY_SOURCES = frozenset({
    APPROVAL_GPT_CLEAR,
    APPROVAL_OPUS_CLEAR,
    APPROVAL_USER_RECEIPT,
    APPROVAL_RISK_ROUTER,
    "candidate_review",
    SYSTEM_EVIDENCE_GOVERNED_WRITE_V1,
})
CANDIDATE_TYPE = {
    "knowledge": "knowledge",
    "project": "project_update",
    "data": "fact",
    "knowledge_project_link": "knowledge",
}


def _uuid() -> str:
    return str(uuid.uuid4())


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def idempotency_key(write_id: str, payload: Mapping[str, Any]) -> str:
    return f"policy-write:{write_id}:{hash_payload(payload)[:24]}"


def _find_existing_change(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM approved_change WHERE idempotency_key = ? ORDER BY created_at DESC LIMIT 1",
        (key,),
    ).fetchone()


def _ensure_analysis_run(conn: sqlite3.Connection, *, trigger: str, model: str) -> str:
    run_id = "policy-run:" + hash_payload({"trigger": trigger, "model": model})[:24]
    existing = conn.execute("SELECT id FROM analysis_run WHERE id = ?", (run_id,)).fetchone()
    if existing:
        return run_id
    now = utc_now()
    conn.execute(
        """INSERT INTO analysis_run
           (id, trigger_type, raw_text, raw_input_refs, analysis_model, started_at, completed_at, created_at)
           VALUES (?, ?, NULL, NULL, ?, ?, ?, ?)""",
        (run_id, trigger, model, now, now, now),
    )
    return run_id


def _insert_candidate(
    conn: sqlite3.Connection,
    *,
    write_item: Mapping[str, Any],
    run_id: str,
) -> str:
    candidate_id = "policy-cand:" + hash_payload({"write_id": write_item.get("id")})[:24]
    existing = conn.execute("SELECT id FROM candidate WHERE id = ?", (candidate_id,)).fetchone()
    if existing:
        return candidate_id
    now = utc_now()
    pre_model = write_item.get("preanalysis_model") or "gpt-5.6-luna"
    auth_model = write_item.get("authoritative_model") or POLICY_REVIEWER
    proposed = {
        "execution": {
            "target_type": write_item.get("target_type"),
            "operation": write_item.get("operation"),
            "payload": write_item.get("payload"),
        },
        "brain_bundle_id": write_item.get("bundle_id"),
        "policy_write_id": write_item.get("id"),
        "policy_id": POLICY_ID,
        "preanalysis_model": pre_model,
        "authoritative_model": auth_model,
    }
    ctype = CANDIDATE_TYPE.get(text(write_item.get("target_type")), "knowledge")
    title = (
        (write_item.get("payload") or {}).get("title")
        or (write_item.get("payload") or {}).get("name")
        or (write_item.get("payload") or {}).get("latest_progress_summary")
        or write_item.get("id")
    )
    conn.execute(
        """INSERT INTO candidate
           (id, analysis_run_id, candidate_type, source_refs, raw_excerpt, proposed_change,
            confidence, inference_level, recommended_action, analysis_model, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'HIGH', 'EXPLICIT', 'AUTO_WRITE', ?, 'pending', ?)""",
        (
            candidate_id,
            run_id,
            ctype,
            json.dumps(write_item.get("source_refs") or [], ensure_ascii=False),
            str(title)[:500],
            json.dumps(proposed, ensure_ascii=False),
            auth_model,
            now,
        ),
    )
    return candidate_id


def _db_approval_source(approval_source: str) -> str:
    """Production review_decision CHECK only allows candidate_review/direct_user."""
    source = text(approval_source) or APPROVAL_GPT_CLEAR
    if source in {APPROVAL_GPT_CLEAR, APPROVAL_OPUS_CLEAR, APPROVAL_USER_RECEIPT, APPROVAL_RISK_ROUTER, SYSTEM_EVIDENCE_GOVERNED_WRITE_V1}:
        return "candidate_review"
    return source


def _standing_policy_source_from_row(rd: Any) -> str:
    try:
        notes = text(rd["review_notes"])
        reviewer = text(rd["reviewer"])
        approval = text(rd["approval_source"])
    except Exception:
        notes = text(getattr(rd, "get", lambda _k, _d=None: "")("review_notes"))
        reviewer = text(getattr(rd, "get", lambda _k, _d=None: "")("reviewer"))
        approval = text(getattr(rd, "get", lambda _k, _d=None: "")("approval_source"))
    for token in (SYSTEM_EVIDENCE_GOVERNED_WRITE_V1, APPROVAL_RISK_ROUTER, APPROVAL_OPUS_CLEAR, APPROVAL_USER_RECEIPT, APPROVAL_GPT_CLEAR):
        if token in notes:
            return token
    if reviewer == POLICY_REVIEWER and approval == "candidate_review":
        return APPROVAL_GPT_CLEAR
    return approval


def _verify_commit_record(conn: sqlite3.Connection, change_id: str) -> dict[str, Any]:
    ac = conn.execute("SELECT * FROM approved_change WHERE change_id = ?", (change_id,)).fetchone()
    if ac is None or ac["status"] != "committed":
        raise ValueError("ApprovedChange not committed")
    cr = conn.execute(
        "SELECT * FROM commit_record WHERE change_id = ? AND result = 'success' ORDER BY committed_at DESC LIMIT 1",
        (change_id,),
    ).fetchone()
    if cr is None:
        raise ValueError("No successful CommitRecord")
    rd = conn.execute("SELECT * FROM review_decision WHERE id = ?", (ac["approval_ref"],)).fetchone()
    if rd is None:
        raise ValueError("ApprovedChange has no ReviewDecision")
    if text(rd["reviewer"]) != POLICY_REVIEWER and text(rd["approval_source"]) not in STANDING_POLICY_SOURCES:
        raise ValueError("ReviewDecision is not a policy/user receipt lineage")
    return {
        "change_id": change_id,
        "commit_record_id": cr["id"],
        "transaction_id": cr["transaction_id"],
        "review_decision_id": rd["id"],
        "approval_source": _standing_policy_source_from_row(rd),
        "reviewer": rd["reviewer"],
        "review_channel": rd["review_channel"],
    }


def _metric_payload_with_evidence(
    payload: Mapping[str, Any], evidence: list[Any], source_refs: list[Any],
) -> dict[str, Any]:
    """Preserve exact NEW-write refs BEFORE Candidate and frozen approval.

    No value/period/dimension/authority inference, no history backfill. This
    is never applied to an already frozen pending or committed change.
    Only explicit SourceVersion-locator pairs survive; parallel lists and
    Formal-only refs cannot supply a raw metric locator.
    """
    sources = {s.removeprefix("source_version:") for s in source_refs if isinstance(s, str)}
    refs: list[dict[str, Any]] = []
    for ref in evidence:
        if (not isinstance(ref, Mapping) or not isinstance(ref.get("source_version_id"), str)
                or ref["source_version_id"] not in sources
                or not isinstance(ref.get("evidence_locator"), str) or not ref["evidence_locator"].strip()):
            raise ValueError("metric_evidence_requires_exact_source_locator_pair")
        if dict(ref) not in refs:
            refs.append(dict(ref))
    if not refs:
        raise ValueError("metric_evidence_requires_exact_source_locator_pair")
    notes = payload.get("notes")
    if notes in (None, ""):
        notes = {}
    elif isinstance(notes, str):
        try:
            notes = json.loads(notes)
        except (ValueError, TypeError) as exc:
            raise ValueError("metric_notes_must_be_json_object") from exc
    if not isinstance(notes, Mapping):
        raise ValueError("metric_notes_must_be_json_object")
    notes = dict(notes)
    # Do not overwrite, extend or borrow evidence already supplied inside
    # notes. Validate it against this write's exact evidence envelope.
    if "evidence_refs" in notes:
        nested = notes["evidence_refs"]
        if not isinstance(nested, list) or not nested:
            raise ValueError("metric_notes_evidence_refs_malformed")
        for ref in nested:
            if not isinstance(ref, Mapping):
                raise ValueError("metric_notes_evidence_refs_malformed")
            matching = [r for r in refs if r.get("source_version_id") == ref.get("source_version_id")
                        and r.get("evidence_locator") == ref.get("evidence_locator")]
            if not matching or any(k in ref and ref[k] != matching[0].get(k)
                                   for k in ("raw_artifact_sha256", "content_fingerprint",
                                             "reader_result_ref", "artifact_sha256")):
                raise ValueError("metric_notes_evidence_outside_write_envelope")
        refs = [dict(r) for r in nested]
    notes["evidence_refs"] = refs
    return {**payload, "notes": json.dumps(notes, ensure_ascii=False, sort_keys=True)}


def commit_policy_write(
    write_item: Mapping[str, Any],
    *,
    db_path: str = PRODUCTION_DB,
    store: ReviewBundleStore | None = None,
    bundle: Mapping[str, Any] | None = None,
    source_state: Mapping[str, Any] | None = None,
    mark_policy: bool = True,
) -> dict[str, Any]:
    """Commit one READY policy write through the governed chain. Idempotent."""
    store = store or ReviewBundleStore()
    write_id = text(write_item.get("id"))
    evaluated = evaluate_write_change(
        {
            "target_type": write_item.get("target_type"),
            "operation": write_item.get("operation"),
            "payload": write_item.get("payload"),
            "source_refs": write_item.get("source_refs"),
            "evidence_refs": write_item.get("evidence_refs"),
            "proposal_id": write_item.get("proposal_id"),
            "expected_target_version": write_item.get("expected_target_version"),
        },
        bundle=bundle or {"source_refs": write_item.get("source_refs"), "evidence_refs": write_item.get("evidence_refs"), "state": "CLEAR"},
        source_state=source_state,
    )
    if not evaluated.get("allowed"):
        if mark_policy:
            mark_policy_failed(write_id, str(evaluated.get("reason")), store=store)
        return {"write_id": write_id, "status": "FAILED_CLOSED", "reason": evaluated.get("reason")}

    payload = dict(evaluated["normalized"]["payload"])
    target_type = evaluated["normalized"]["target_type"]
    operation = evaluated["normalized"]["operation"]
    source_refs = list(evaluated["normalized"]["source_refs"] or [])
    key = idempotency_key(write_id, payload)
    approval_source = text(write_item.get("approval_source")) or APPROVAL_GPT_CLEAR
    db_approval_source = _db_approval_source(approval_source)

    pre_model = write_item.get("preanalysis_model") or "gpt-5.6-luna"
    auth_model = write_item.get("authoritative_model") or POLICY_REVIEWER
    previous_db = _committer.DB_PATH
    _committer.DB_PATH = db_path
    conn = _connect(db_path)
    try:
        existing = _find_existing_change(conn, key)
        if existing and existing["status"] == "committed":
            verified = _verify_commit_record(conn, existing["change_id"])
            marked = mark_policy_written(write_id, commit_ref=verified, store=store) if mark_policy else {}
            return {
                "write_id": write_id,
                "status": "COMMITTED",
                "idempotent": True,
                "change_id": verified["change_id"],
                "commit_record_id": verified["commit_record_id"],
                "bundle_state": marked.get("status"),
                "preanalysis_model": pre_model,
                "authoritative_model": auth_model,
            }
        if existing and existing["status"] != "pending":
            raise ValueError("policy_write_existing_change_not_retryable")
        if not existing and (target_type, operation) == ("data", "revise"):
            # key was computed from the original plan exactly as before.
            # Sidecar/queued retries therefore retain their original identity.
            # The enriched payload is reviewed and hashed by the existing
            # approval API, never appended after approval/commit.
            payload = _metric_payload_with_evidence(
                payload, evaluated["normalized"]["evidence_refs"], source_refs)
        run_id = _ensure_analysis_run(conn, trigger="gpt_brain_policy", model=POLICY_ID)
        conn.commit()
        candidate_id = _insert_candidate(conn, write_item={**write_item, "payload": payload}, run_id=run_id)
        conn.commit()
        conn.close()
        conn = None

        if existing and existing["status"] == "pending":
            result = _governance_commit_approved_change(existing["change_id"])
            change_id = existing["change_id"]
            rd_id = None
        else:
            rd_id, change_id = _committer.create_review_decision_atomic(
                candidate_id=candidate_id,
                decision="approve",
                reviewer=POLICY_REVIEWER,
                review_channel=POLICY_REVIEW_CHANNEL,
                approval_source=db_approval_source,
                original_payload=payload,
                reviewed_payload=payload,
                review_notes=(
                    f"{POLICY_ID} standing authorization "
                    f"[policy_approval_source={approval_source}; preanalysis={pre_model}, authoritative={auth_model}]; "
                    "no semantic judgment."
                ),
                target_type=target_type,
                operation=operation,
                source_refs=source_refs,
                expected_target_version=write_item.get("expected_target_version"),
                create_approved_change=True,
                idempotency_key=key,
            )
            result = _governance_commit_approved_change(change_id)

        if result.get("result") != "success":
            reason = result.get("error") or "commit failed"
            if mark_policy:
                mark_policy_failed(write_id, reason, store=store)
            return {"write_id": write_id, "status": "FAILED_CLOSED", "reason": reason, "change_id": change_id}

        conn = _connect(db_path)
        verified = _verify_commit_record(conn, change_id)
        if rd_id:
            verified["review_decision_id"] = rd_id
        verified["target_id"] = result.get("target_id")
        marked = mark_policy_written(write_id, commit_ref=verified, store=store) if mark_policy else {}
        return {
            "write_id": write_id,
            "status": "COMMITTED",
            "idempotent": False,
            "candidate_id": candidate_id,
            "review_decision_id": verified.get("review_decision_id"),
            "change_id": verified["change_id"],
            "commit_record_id": verified["commit_record_id"],
            "target_id": result.get("target_id"),
            "approval_source": approval_source,
            "reviewer": POLICY_REVIEWER,
            "review_channel": POLICY_REVIEW_CHANNEL,
            "preanalysis_model": pre_model,
            "authoritative_model": auth_model,
            "formal_state_bypass": 0,
        }
    except Exception as exc:
        try:
            if mark_policy:
                mark_policy_failed(write_id, str(exc), store=store)
        except Exception:
            pass
        return {"write_id": write_id, "status": "FAILED_CLOSED", "reason": str(exc)[:1000]}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        _committer.DB_PATH = previous_db


def surface_user_decision_cluster(
    cluster: Mapping[str, Any],
    *,
    db_path: str = PRODUCTION_DB,
    store: ReviewBundleStore | None = None,
) -> dict[str, Any]:
    """Create one pending Candidate for a user decision cluster. No Formal write."""
    store = store or ReviewBundleStore()
    if cluster.get("candidate_id"):
        return {"cluster_id": cluster.get("id"), "candidate_id": cluster.get("candidate_id"), "reused": True}
    conn = _connect(db_path)
    try:
        run_id = _ensure_analysis_run(conn, trigger="gpt_brain_user_decision", model="chatgpt-post-opus")
        now = utc_now()
        candidate_id = "decision-cand:" + hash_payload({"cluster_id": cluster.get("id")})[:24]
        existing = conn.execute("SELECT id FROM candidate WHERE id = ?", (candidate_id,)).fetchone()
        if existing:
            attach_user_cluster_candidate(str(cluster.get("id")), candidate_id, store=store)
            return {"cluster_id": cluster.get("id"), "candidate_id": candidate_id, "reused": True}
        question = text(cluster.get("decision_question")) or "Need a user decision"
        proposed = {
            "decision_cluster_id": cluster.get("id"),
            "bundle_ids": list(cluster.get("bundle_ids") or []),
            "alternatives": list(cluster.get("alternatives") or []),
            "why_unresolved": cluster.get("why_unresolved"),
            "affected_entities": list(cluster.get("affected_entities") or []),
        }
        raw_brief = {
            "schema_version": "1",
            "title": question[:80],
            "situation": text(cluster.get("why_unresolved")) or question,
            "decision_question": question,
            "recommendation": "GPT reviewed this cluster and, where required, Opus reviewed it once. A user receipt is required because the remaining issue is unresolved, subjective, or high-impact.",
            "approval_effect": "One receipt finalizes this decision cluster. Deterministic commit follows without a second approval.",
            "review_mode": "DECISION_ONLY",
            "affected_items": [
                {
                    "label": text(entity) or "affected entity",
                    "current_state": "unresolved after GPT+Opus",
                    "proposed_state": "user-decided canonical direction",
                }
                for entity in (cluster.get("affected_entities") or ["business decision"])[:8]
            ] or [{
                "label": "business decision",
                "current_state": "unresolved after GPT+Opus",
                "proposed_state": "user-decided canonical direction",
            }],
            "evidence_summary": [
                text((ref or {}).get("evidence_locator") if isinstance(ref, Mapping) else ref) or "bundle evidence"
                for ref in (cluster.get("evidence_refs") or [{"evidence_locator": question}])[:8]
            ],
            "attention_points": [
                "User review is exception-only and clustered by decision topic.",
                text(cluster.get("impact")) or "Canonical write is blocked until this receipt.",
            ],
        }
        brief = normalize_user_decision_card({
            **cluster,
            **raw_brief,
            "decision_question": question,
            "status": cluster.get("status") or "OPEN",
        })
        # review_brief_json is the user projection. The original wording is
        # still retained in candidate.raw_excerpt/proposed_change and in the
        # ReviewBundle cluster; approval and lineage are untouched.
        brief.pop("internal_evidence", None)
        brief["schema_version"] = "1"
        brief["review_mode"] = "DECISION_ONLY"
        if not brief["evidence_summary"] or not brief["evidence_summary"][0]:
            brief["evidence_summary"] = ["已关联来源证据；具体原始内容保留在内部证据中。"]
        conn.execute(
            """INSERT INTO candidate
               (id, analysis_run_id, candidate_type, source_refs, raw_excerpt, proposed_change,
                confidence, inference_level, recommended_action, analysis_model, status, created_at, review_brief_json)
               VALUES (?, ?, 'conflict', ?, ?, ?, 'MEDIUM', 'STRONG_INFERENCE', 'ASK_USER', ?, 'pending', ?, ?)""",
            (
                candidate_id,
                run_id,
                json.dumps([f"decision-cluster:{cluster.get('id')}"] + [f"bundle:{b}" for b in (cluster.get("bundle_ids") or [])], ensure_ascii=False),
                question,
                json.dumps(proposed, ensure_ascii=False),
                "chatgpt-post-opus",
                now,
                json.dumps(brief, ensure_ascii=False),
            ),
        )
        conn.commit()
        attach_user_cluster_candidate(str(cluster.get("id")), candidate_id, store=store)
        return {"cluster_id": cluster.get("id"), "candidate_id": candidate_id, "reused": False, "formal_state_bypass": 0}
    finally:
        conn.close()


def dispatch_once(
    *,
    store: ReviewBundleStore | None = None,
    db_path: str = PRODUCTION_DB,
    limit: int = 5,
) -> dict[str, Any]:
    store = store or ReviewBundleStore()
    ready = list_policy_write_ready(store=store, limit=limit)
    results = []
    data = store.load()
    for item in ready.get("items") or []:
        bundle = (data.get("bundles") or {}).get(item.get("bundle_id")) or {}
        source_state = {"state": bundle.get("coverage_state")}
        results.append(commit_policy_write(item, db_path=db_path, store=store, bundle=bundle, source_state=source_state))
    clusters = [
        c for c in (data.get("user_decision_clusters") or {}).values()
        if c.get("status") == "OPEN" and not c.get("candidate_id")
    ]
    surfaced = []
    for cluster in clusters[:limit]:
        try:
            surfaced.append(surface_user_decision_cluster(cluster, db_path=db_path, store=store))
        except Exception as exc:
            surfaced.append({"cluster_id": cluster.get("id"), "error": str(exc)[:300]})
    return {
        "processed": len(results),
        "results": results,
        "user_clusters_surfaced": surfaced,
        "formal_state_bypass": 0,
    }


def run_forever(*, poll_seconds: float = DEFAULT_POLL_SECONDS) -> None:
    paths_root = Path(os.environ.get("PAW_GPT_BRAIN_OPUS_STATE_DIR", "/var/lib/paw-mcp-gateway"))
    lock = FileLeaseLock(paths_root / "paw_gpt_brain_policy_committer.lock", stale_seconds=900)
    lock.acquire(run_id=str(uuid.uuid4()), actor=ACTOR)
    store = ReviewBundleStore()
    try:
        while True:
            try:
                dispatch_once(store=store, db_path=os.environ.get("PAW_WORKSPACE_DB", PRODUCTION_DB))
            except Exception:
                pass
            time.sleep(max(1.0, poll_seconds))
    finally:
        lock.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    args = parser.parse_args(argv)
    store = ReviewBundleStore()
    if args.status:
        print(json.dumps({
            "actor": ACTOR,
            "policy_id": POLICY_ID,
            "store": str(default_store_path()),
            "brain": str(default_brain_path()),
            "ready": list_policy_write_ready(store=store),
        }, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.once:
        print(json.dumps(dispatch_once(store=store), ensure_ascii=False, indent=2, default=str))
        return 0
    run_forever(poll_seconds=args.poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
