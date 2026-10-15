#!/usr/bin/env python3
"""GPT Brain patrol is the sole semantic consumer of Quick Captures.

Production Quick Capture save persists raw Capture only. This module:
- lists unprocessed Captures for GPT patrol with exact raw fields, context, lineage
- reconciles processed_at IS NULL into the durable next-patrol batch (no LLM)
- records GPT NO_ACTION with durable lineage
- creates one human-review item for material ambiguity
- governed-writes explicit user-authored actions without a redundant human review
- requeues legacy Mistral NO_ACTION misses once, skipping already-recovered rows

No Mistral/Gemini/Claude/Agy/Cursor semantic call lives here.
Formal State writes go only through ReviewDecision -> ApprovedChange -> Committer.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

GATEWAY = "/opt/paw-mcp-gateway"
WORKSPACE = str(Path(__file__).resolve().parents[1 if "backend" in str(Path(__file__).parent.name) else 2])
if GATEWAY not in sys.path:
    sys.path.insert(0, GATEWAY)
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

import committer as _committer

from paw_capture_patrol_enqueue import (
    best_effort_enqueue_saved_capture,
    reconcile_unprocessed_captures,
    sidecar_path as capture_sidecar_path,
)
from paw_quick_capture_write_bridge import consume_accepted_candidate
from paw_review_display_normalization import normalize_review_display
from paw_source_version_governance import (
    build_version_ruling_envelope,
    parse_capture_version_directive,
    install_committer_integration,
)
install_committer_integration()
from paw_capture_lifecycle import (
    WAITING_GPT,
    WAITING_HUMAN,
    count_patrol_eligible_captures,
    enrich_capture_projection,
    is_patrol_eligible,
    list_patrol_eligible_captures,
    needs_review_candidate as _needs_review_candidate,
    patrol_state as _patrol_state,
    waiting_gpt_resolution as _waiting_gpt_resolution,
)

PRODUCTION_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))
RECOVERY_STORE = Path("/var/lib/paw-mcp-gateway/paw_capture_gpt_patrol_recovery.json")
GPT_PATROL_MODEL = "gpt_brain_patrol"
TRIGGER_TYPE = "gpt_patrol_capture"
REVIEWER = "gpt_brain_patrol"
REVIEW_CHANNEL = "standing_user_policy"
APPROVAL_SOURCE = "gpt_capture_explicit_action_v1"
SIDE_MODEL_MARKERS = (
    "mistral",
    "gemini",
    "claude",
    "opus",
    "agy",
    "mistral-small",
    "mistral-small-latest",
)
KNOWN_RECOVERED = {
    "25f70472-8d4d-4d97-a724-50a8e79fe8bd": "4eea2eb2-95b1-4b59-b098-ed138e3cd7ac",
}
POSITIVE_LINEAGE = {
    "93289bf4-fabb-43f7-aefd-1c5c0e54b98f": "todo-1870214-reconciled",
}

NO_ACTION = "NO_ACTION"
USER_DECISION_REQUIRED = "USER_DECISION_REQUIRED"
GOVERNED_WRITE = "GOVERNED_WRITE"
ALLOWED_DECISIONS = frozenset({NO_ACTION, USER_DECISION_REQUIRED, GOVERNED_WRITE})

_REQUEUE_LOCK = threading.Lock()
_REQUEUE_DONE = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _json_obj(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _json_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
        return list(parsed) if isinstance(parsed, list) else []
    return []


def _load_store(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema": "paw-capture-gpt-patrol-recovery/1", "items": {}, "updated_at": utc_now()}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": "paw-capture-gpt-patrol-recovery/1", "items": {}, "updated_at": utc_now()}
    if not isinstance(data, dict):
        return {"schema": "paw-capture-gpt-patrol-recovery/1", "items": {}, "updated_at": utc_now()}
    data.setdefault("schema", "paw-capture-gpt-patrol-recovery/1")
    data.setdefault("items", {})
    return data


def _save_store(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = utc_now()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _capture_refs(capture_id: str) -> tuple[str, ...]:
    return (capture_id, f"capture:{capture_id}")


def _source_refs_match(raw: Any, capture_id: str) -> bool:
    blob = raw if isinstance(raw, str) else json.dumps(raw or "", ensure_ascii=False)
    return any(token in blob for token in _capture_refs(capture_id))


def _parse_run_meta(raw: Any) -> dict[str, Any]:
    meta = _json_obj(raw)
    if not meta and isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, list):
            return {"refs": parsed}
    if isinstance(meta, dict):
        return meta
    return {}


def _is_side_model(model: str | None) -> bool:
    compact = (model or "").lower()
    return any(marker in compact for marker in SIDE_MODEL_MARKERS)


def _load_capture(conn: sqlite3.Connection, capture_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM capture WHERE id = ?", (capture_id,)).fetchone()
    return dict(row) if row else None


def _candidates_for_capture(conn: sqlite3.Connection, capture_id: str) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM candidate").fetchall()
    out = []
    for row in rows:
        if _source_refs_match(row["source_refs"], capture_id):
            out.append(dict(row))
    return out


def _resolutions_for_candidates(conn: sqlite3.Connection, candidate_ids: list[str]) -> list[dict[str, Any]]:
    if not candidate_ids:
        return []
    try:
        placeholders = ",".join("?" * len(candidate_ids))
        rows = conn.execute(
            f"SELECT * FROM candidate_resolution WHERE candidate_id IN ({placeholders}) ORDER BY created_at",
            tuple(candidate_ids),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(row) for row in rows]


def _canonical_todos_for_capture(conn: sqlite3.Connection, capture: Mapping[str, Any]) -> list[dict[str, Any]]:
    cid = str(capture["id"])
    raw = str(capture.get("raw_text") or "").strip()
    todos: list[dict[str, Any]] = []
    seen: set[str] = set()
    queries = [
        ("SELECT * FROM todo WHERE source_capture_id = ?", (cid,)),
        ("SELECT * FROM todo WHERE title = ?", (raw,)),
    ]
    known = KNOWN_RECOVERED.get(cid)
    if known:
        queries.append(("SELECT * FROM todo WHERE id = ?", (known,)))
    positive = POSITIVE_LINEAGE.get(cid)
    if positive:
        queries.append(("SELECT * FROM todo WHERE id = ?", (positive,)))
    for sql, args in queries:
        try:
            rows = conn.execute(sql, args).fetchall()
        except sqlite3.OperationalError:
            continue
        for row in rows:
            item = dict(row)
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            todos.append(item)
    return todos


def _project_context(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            """SELECT id, name, status, description, next_action_summary, latest_progress_summary
               FROM project ORDER BY updated_at DESC LIMIT 80"""
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(row) for row in rows]


def _knowledge_context(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            """SELECT id, title, one_line_summary, status FROM knowledge_page
               ORDER BY updated_at DESC LIMIT 40"""
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(row) for row in rows]


def _analysis_runs_for_capture(conn: sqlite3.Connection, capture_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, trigger_type, analysis_model, started_at, completed_at, raw_input_refs FROM analysis_run"
    ).fetchall()
    out = []
    for row in rows:
        if not _source_refs_match(row["raw_input_refs"], capture_id) and capture_id not in str(row["raw_input_refs"] or ""):
            continue
        meta = _parse_run_meta(row["raw_input_refs"])
        out.append({
            "id": row["id"],
            "trigger_type": row["trigger_type"],
            "analysis_model": row["analysis_model"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "status": meta.get("status"),
            "outcome": meta.get("outcome"),
            "reason": meta.get("reason"),
            "side_model": _is_side_model(row["analysis_model"]),
        })
    out.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
    return out


def _handoff_item(conn: sqlite3.Connection, capture: Mapping[str, Any]) -> dict[str, Any]:
    candidates = _candidates_for_capture(conn, str(capture["id"]))
    resolutions = _resolutions_for_candidates(conn, [c["id"] for c in candidates])
    feedback = _waiting_gpt_resolution(candidates, resolutions)
    todos = _canonical_todos_for_capture(conn, capture)
    state = _patrol_state(capture, candidates, resolutions)
    return {
        "capture_id": capture["id"],
        "raw_text": capture.get("raw_text"),
        "captured_at": capture.get("captured_at"),
        "created_at": capture.get("created_at"),
        "source": capture.get("source"),
        "daily_id": capture.get("daily_id"),
        "processed_at": capture.get("processed_at"),
        "patrol_state": state,
        "user_feedback": {
            "candidate_id": feedback.get("candidate_id"),
            "user_response": feedback.get("user_response"),
            "selected_option": feedback.get("selected_option"),
            "resolved_by": feedback.get("resolved_by") or feedback.get("reviewed_by"),
            "resolved_at": feedback.get("resolved_at") or feedback.get("reviewed_at"),
            "resolution_status": feedback.get("resolution_status"),
        } if feedback else None,
        "lineage": {
            "analysis_runs": _analysis_runs_for_capture(conn, str(capture["id"])),
            "candidates": [
                {
                    "id": c["id"],
                    "candidate_type": c.get("candidate_type"),
                    "status": c.get("status"),
                    "applied_entity_id": c.get("applied_entity_id"),
                    "applied_entity_type": c.get("applied_entity_type"),
                    "analysis_run_id": c.get("analysis_run_id"),
                }
                for c in candidates
            ],
            "resolutions": [
                {
                    "candidate_id": r.get("candidate_id"),
                    "resolution_status": r.get("resolution_status"),
                    "user_response": r.get("user_response"),
                    "selected_option": r.get("selected_option"),
                }
                for r in resolutions
            ],
            "canonical_todos": [
                {"id": t["id"], "title": t.get("title"), "status": t.get("status"), "project_id": t.get("project_id")}
                for t in todos
            ],
        },
        "current_project_context": _project_context(conn),
        "current_knowledge_context": _knowledge_context(conn),
        "semantic_authority": "gpt_brain_patrol",
        "side_model_analysis_forbidden": True,
    }


def already_recovered_canonical(conn: sqlite3.Connection, capture: Mapping[str, Any]) -> dict[str, Any] | None:
    cid = str(capture["id"])
    todos = _canonical_todos_for_capture(conn, capture)
    known_todo = KNOWN_RECOVERED.get(cid)
    if known_todo and any(t["id"] == known_todo for t in todos):
        return {"reason": "known_manual_recovery", "todo_id": known_todo, "capture_id": cid}
    positive = POSITIVE_LINEAGE.get(cid)
    if positive and any(t["id"] == positive for t in todos):
        return {"reason": "positive_lineage_preserved", "todo_id": positive, "capture_id": cid}
    candidates = _candidates_for_capture(conn, cid)
    applied = [c for c in candidates if c.get("applied_entity_id")]
    if applied:
        return {
            "reason": "applied_candidate",
            "candidate_id": applied[0]["id"],
            "applied_entity_id": applied[0]["applied_entity_id"],
            "capture_id": cid,
        }
    if todos:
        return {"reason": "canonical_todo_title_or_source", "todo_id": todos[0]["id"], "capture_id": cid}
    return None


def _legacy_mistral_no_action(conn: sqlite3.Connection, capture: Mapping[str, Any]) -> bool:
    cid = str(capture["id"])
    if not capture.get("processed_at"):
        return False
    runs = _analysis_runs_for_capture(conn, cid)
    mistral_completed = [
        run for run in runs
        if run.get("trigger_type") == "capture"
        and _is_side_model(str(run.get("analysis_model") or ""))
        and str(run.get("status") or "").upper() in {"COMPLETED", "COMPLETE", ""}
        and str(run.get("status") or "").upper() != "FAILED"
    ]
    if not mistral_completed:
        return False
    explicit = [run for run in mistral_completed if str(run.get("outcome") or "") == NO_ACTION]
    candidates = _candidates_for_capture(conn, cid)
    if explicit:
        return not candidates
    # Older rows stored no outcome; treat completed side-model runs with zero candidates as NO_ACTION.
    if candidates:
        return False
    return any(not run.get("outcome") or str(run.get("outcome")).upper() in {"COMPLETED", "COMPLETE"} for run in mistral_completed)


def requeue_legacy_no_action_captures(
    *,
    db_path: str = PRODUCTION_DB,
    store_path: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Clear processed_at once for eligible Mistral NO_ACTION misses. No semantic rewrite."""
    global _REQUEUE_DONE
    path = store_path or RECOVERY_STORE
    with _REQUEUE_LOCK:
        if _REQUEUE_DONE and not force and db_path == PRODUCTION_DB and path == RECOVERY_STORE:
            data = _load_store(path)
            return {"status": "already_ran", "count": len(data.get("items") or {}), "items": data.get("items") or {}}
        data = _load_store(path)
        conn = _connect(db_path)
        try:
            captures = [dict(row) for row in conn.execute("SELECT * FROM capture").fetchall()]
            for capture in captures:
                cid = str(capture["id"])
                existing = (data.get("items") or {}).get(cid)
                recovered = already_recovered_canonical(conn, capture)
                if recovered:
                    if not existing or existing.get("status") != "already_recovered":
                        data.setdefault("items", {})[cid] = {
                            "status": "already_recovered",
                            "reason": recovered["reason"],
                            "canonical_todo_id": recovered.get("todo_id") or recovered.get("applied_entity_id"),
                            "original_processed_at": capture.get("processed_at"),
                            "raw_text": capture.get("raw_text"),
                            "captured_at": capture.get("captured_at"),
                            "marked_at": utc_now(),
                        }
                    continue
                if existing and existing.get("status") in {"requeued", "already_recovered"}:
                    continue
                if not _legacy_mistral_no_action(conn, capture):
                    continue
                original_processed = capture.get("processed_at")
                conn.execute(
                    "UPDATE capture SET processed_at = NULL, updated_at = ? WHERE id = ?",
                    (utc_now(), cid),
                )
                data.setdefault("items", {})[cid] = {
                    "status": "requeued",
                    "reason": "legacy_mistral_no_action_without_candidate_or_entity",
                    "original_processed_at": original_processed,
                    "raw_text": capture.get("raw_text"),
                    "captured_at": capture.get("captured_at"),
                    "requeued_at": utc_now(),
                }
            conn.commit()
        finally:
            conn.close()
        _save_store(path, data)
        if db_path == PRODUCTION_DB and path == RECOVERY_STORE:
            _REQUEUE_DONE = True
        items = data.get("items") or {}
        return {
            "status": "ok",
            "requeued": [cid for cid, item in items.items() if item.get("status") == "requeued"],
            "already_recovered": [cid for cid, item in items.items() if item.get("status") == "already_recovered"],
            "items": items,
            "formal_state_bypass": 0,
        }


def list_unprocessed_captures(*, db_path: str = PRODUCTION_DB, limit: int = 20, store_path: Path | None = None) -> dict[str, Any]:
    requeue = requeue_legacy_no_action_captures(db_path=db_path, store_path=store_path)
    conn = _connect(db_path)
    try:
        eligible = list_patrol_eligible_captures(
            conn,
            limit=max(1, min(int(limit), 100)),
            order="captured_at ASC",
        )
        items = [_handoff_item(conn, dict(row)) for row in eligible]
        unprocessed = count_patrol_eligible_captures(conn)
    finally:
        conn.close()
    patrol_store = Path(os.environ.get("PAW_PATROL_BATCH_STORE") or (
        str(Path(store_path).parent / "patrol_batch.json") if store_path else ""
    ))
    if not str(patrol_store):
        try:
            from paw_patrol_orchestrator import default_store_path
            patrol_store = default_store_path()
        except Exception:
            patrol_store = Path("/var/lib/paw-mcp-gateway/paw_patrol_input_batch.json")
    sidecar = Path(store_path).parent / "capture_patrol_enqueue.json" if store_path else capture_sidecar_path()
    try:
        reconcile_unprocessed_captures(
            db_path=db_path,
            store_path=patrol_store,
            sidecar=sidecar,
        )
    except Exception:
        for item in items:
            best_effort_enqueue_saved_capture(
                item["capture_id"],
                db_path=db_path,
                store_path=patrol_store,
                sidecar=sidecar,
            )
    try:
        from paw_gpt_review_bundle import compute_patrol_resume_gate
        patrol_gate = compute_patrol_resume_gate()
    except Exception:
        patrol_gate = {
            "daily_capture_patrol_enabled": False,
            "daily_capture_patrol_blocked_reason": "patrol_gate_unavailable",
            "incremental_mirror_patrol_enabled": False,
            "incremental_mirror_patrol_blocked_reason": "patrol_gate_unavailable",
            "patrol": {"enabled": False, "blocked_reason": "patrol_gate_unavailable"},
        }
    return {
        "count": len(items),
        "capture_unprocessed": unprocessed,
        "items": items,
        "semantic_authority": "gpt_brain_patrol",
        "immediate_side_model_analysis": False,
        "semantic_processing_on_ingest": False,
        "legacy_requeue": {
            "requeued": requeue.get("requeued") or [],
            "already_recovered": requeue.get("already_recovered") or [],
        },
        "daily_capture_patrol_enabled": patrol_gate.get("daily_capture_patrol_enabled"),
        "daily_capture_patrol_blocked_reason": patrol_gate.get("daily_capture_patrol_blocked_reason"),
        "incremental_mirror_patrol_enabled": patrol_gate.get("incremental_mirror_patrol_enabled"),
        "incremental_mirror_patrol_blocked_reason": patrol_gate.get("incremental_mirror_patrol_blocked_reason"),
        "patrol_enabled": (patrol_gate.get("patrol") or {}).get("enabled"),
        "patrol_blocked_reason": (patrol_gate.get("patrol") or {}).get("blocked_reason"),
        "formal_state_bypass": 0,
    }


def _insert_gpt_run(
    conn: sqlite3.Connection,
    *,
    capture_id: str,
    raw_text: str | None,
    outcome: str,
    reason: str,
) -> str:
    run_id = _uuid()
    now = utc_now()
    meta = {
        "refs": [capture_id],
        "fingerprint": None,
        "status": "COMPLETED",
        "outcome": outcome,
        "reason": reason,
        "semantic_authority": "gpt_brain_patrol",
        "side_model": None,
    }
    conn.execute(
        """INSERT INTO analysis_run
           (id, trigger_type, raw_text, raw_input_refs, analysis_model, started_at, completed_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, TRIGGER_TYPE, raw_text, json.dumps(meta, ensure_ascii=False), GPT_PATROL_MODEL, now, now, now),
    )
    return run_id


def _mark_processed(conn: sqlite3.Connection, capture_id: str) -> None:
    now = utc_now()
    conn.execute(
        "UPDATE capture SET processed_at = ?, updated_at = ? WHERE id = ? AND processed_at IS NULL",
        (now, now, capture_id),
    )


def _insert_pending_review(
    conn: sqlite3.Connection,
    *,
    capture: Mapping[str, Any],
    run_id: str,
    statement: str,
    reason: str,
) -> str:
    candidate_id = _uuid()
    now = utc_now()
    proposed = {
        "statement": statement,
        "question": statement,
        "reason": reason,
        "capture_id": capture["id"],
        "review_required": True,
    }
    review_display = normalize_review_display(
        {
            "title": statement,
            "situation": reason,
            "decision_question": statement,
            "recommendation": "建议暂不执行正式写入，等待用户确认。",
            "approval_effect": "用户确认后才可继续正式审批流程。",
            "conflict_explanation": reason,
            "next_action": "请核对来源证据并提交用户决定。",
            "evidence_summary": [{"evidence_locator": capture.get("id")}],
            "affected_items": [capture.get("id")],
            "state": USER_DECISION_REQUIRED,
        },
        machine_state=USER_DECISION_REQUIRED,
        stage="capture_user",
        raw_output={"statement": statement, "reason": reason, "capture": dict(capture)},
    )
    review_display.pop("internal_evidence", None)
    conn.execute(
        """INSERT INTO candidate
           (id, analysis_run_id, candidate_type, source_refs, raw_excerpt, proposed_change,
            confidence, inference_level, recommended_action, analysis_model, status, created_at, review_brief_json)
           VALUES (?, ?, 'ambiguity', ?, ?, ?, 'LOW', 'WEAK_INFERENCE', 'ASK_USER', ?, 'pending', ?, ?)""",
        (
            candidate_id,
            run_id,
            json.dumps({"refs": [capture["id"], f"capture:{capture['id']}"]}, ensure_ascii=False),
            str(capture.get("raw_text") or "")[:500],
            json.dumps(proposed, ensure_ascii=False),
            GPT_PATROL_MODEL,
            now,
            json.dumps(review_display, ensure_ascii=False),
        ),
    )
    return candidate_id


def inspect_capture_version_directive(
    capture: Mapping[str, Any],
    *,
    linked_source_version_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Parse explicit capture version directives without mutating state."""
    refs = list(linked_source_version_ids or [])
    meta = _json_obj(capture.get("metadata"))
    for key in ("source_version_ids", "linked_source_versions"):
        for value in meta.get(key) or []:
            if value:
                refs.append(str(value))
    return parse_capture_version_directive(str(capture.get("raw_text") or ""), linked_source_version_ids=refs)


def _insert_write_candidate(
    conn: sqlite3.Connection,
    *,
    capture: Mapping[str, Any],
    run_id: str,
    execution: Mapping[str, Any],
    reason: str,
    feedback: Mapping[str, Any] | None,
) -> str:
    candidate_id = _uuid()
    now = utc_now()
    target = str(execution.get("target_type") or "todo")
    candidate_type = "project_update" if target == "project" else target
    if candidate_type not in {"todo", "knowledge", "project_update"}:
        candidate_type = "todo"
    payload = dict(execution.get("payload") or {})
    if payload.get("version_ruling"):
        candidate_type = "conflict"
    proposed = {
        "original_payload": payload,
        "execution": {
            "target_type": target,
            "operation": execution.get("operation"),
            "payload": payload,
        },
        "reason": reason,
        "capture_id": capture["id"],
        "user_feedback": dict(feedback) if feedback else None,
        "review_metadata": {
            "reviewed_by": REVIEWER,
            "reviewed_at": now,
            "review_notes": "explicit user-authored capture; standing policy; no redundant human review",
            "review_origin": "gpt_patrol",
            "action": "accepted",
        },
    }
    conn.execute(
        """INSERT INTO candidate
           (id, analysis_run_id, candidate_type, source_refs, raw_excerpt, proposed_change,
            confidence, inference_level, recommended_action, analysis_model, status,
            created_at, reviewed_at, reviewed_by)
           VALUES (?, ?, ?, ?, ?, ?, 'HIGH', 'EXPLICIT', 'AUTO_WRITE', ?, 'accepted', ?, ?, ?)""",
        (
            candidate_id,
            run_id,
            candidate_type,
            json.dumps({"refs": [capture["id"], f"capture:{capture['id']}"]}, ensure_ascii=False),
            str(payload.get("title") or capture.get("raw_text") or "")[:500],
            json.dumps(proposed, ensure_ascii=False),
            GPT_PATROL_MODEL,
            now,
            now,
            REVIEWER,
        ),
    )
    return candidate_id


def submit_capture_decision(
    capture_id: str,
    decision: str,
    *,
    reason: str = "",
    review_statement: str | None = None,
    execution: Mapping[str, Any] | None = None,
    db_path: str = PRODUCTION_DB,
    apply_now: bool = False,
    patrol_store_path: Path | None = None,
) -> dict[str, Any]:
    decision = str(decision or "").strip().upper()
    if decision not in ALLOWED_DECISIONS:
        return {"error": "invalid_decision", "allowed": sorted(ALLOWED_DECISIONS)}
    reason = (reason or "").strip()
    if not reason:
        return {"error": "reason_required"}
    requested_apply_now = bool(apply_now)
    conn = _connect(db_path)
    try:
        capture = _load_capture(conn, capture_id)
        if not capture:
            return {"error": "capture_not_found", "capture_id": capture_id}
        candidates = _candidates_for_capture(conn, capture_id)
        resolutions = _resolutions_for_candidates(conn, [c["id"] for c in candidates])
        state = _patrol_state(capture, candidates, resolutions)
        waiting_human = _needs_review_candidate(candidates, resolutions)
        feedback = _waiting_gpt_resolution(candidates, resolutions)

        if capture.get("processed_at") and state == "gpt_resolved":
            return {
                "status": "already_resolved",
                "capture_id": capture_id,
                "processed_at": capture.get("processed_at"),
                "idempotent": True,
                "formal_state_bypass": 0,
            }

        if decision == USER_DECISION_REQUIRED and waiting_human:
            return {
                "status": "already_waiting_human",
                "capture_id": capture_id,
                "candidate_id": waiting_human["id"],
                "patrol_state": "waiting_human",
                "idempotent": True,
                "formal_state_bypass": 0,
            }

        if decision in {NO_ACTION, GOVERNED_WRITE} and waiting_human and not feedback:
            return {
                "error": "waiting_human",
                "capture_id": capture_id,
                "candidate_id": waiting_human["id"],
                "message": "user review is still pending; do not re-ask or write until the exact answer exists",
            }

        if decision == NO_ACTION:
            run_id = _insert_gpt_run(conn, capture_id=capture_id, raw_text=capture.get("raw_text"), outcome=NO_ACTION, reason=reason)
            _mark_processed(conn, capture_id)
            conn.commit()
            return {
                "status": "recorded",
                "decision": NO_ACTION,
                "capture_id": capture_id,
                "analysis_run_id": run_id,
                "analysis_model": GPT_PATROL_MODEL,
                "reason": reason,
                "processed": True,
                "formal_state_bypass": 0,
            }

        if decision == USER_DECISION_REQUIRED:
            statement = (review_statement or "").strip() or reason
            run_id = _insert_gpt_run(
                conn, capture_id=capture_id, raw_text=capture.get("raw_text"),
                outcome=USER_DECISION_REQUIRED, reason=reason,
            )
            candidate_id = _insert_pending_review(
                conn, capture=capture, run_id=run_id, statement=statement, reason=reason,
            )
            conn.commit()
            return {
                "status": "review_created",
                "decision": USER_DECISION_REQUIRED,
                "capture_id": capture_id,
                "candidate_id": candidate_id,
                "analysis_run_id": run_id,
                "patrol_state": "waiting_human",
                "processed_at": None,
                "formal_state_bypass": 0,
            }

        if not isinstance(execution, Mapping) or not execution.get("payload"):
            return {"error": "execution_required", "message": "GOVERNED_WRITE needs an exact execution envelope"}
        payload = dict(execution.get("payload") or {})
        if payload.get("version_ruling"):
            if not payload.get("id"):
                return {"error": "FAILED_CLOSED", "reason": "version_ruling requires project id in payload", "formal_state_bypass": 0}
            if not isinstance(payload.get("version_ruling"), Mapping):
                return {"error": "FAILED_CLOSED", "reason": "version_ruling must be an object", "formal_state_bypass": 0}
            if not _text(payload["version_ruling"].get("current_source_version_id")):
                return {"error": "FAILED_CLOSED", "reason": "version_ruling requires current_source_version_id", "formal_state_bypass": 0}
            payload["governance_only"] = True
            execution = {**dict(execution), "payload": payload}
        else:
            directive = inspect_capture_version_directive(capture)
            if directive.get("status") == "READY" and not payload.get("version_ruling"):
                return {
                    "error": "execution_required",
                    "message": "explicit version directive requires governed version_ruling execution envelope",
                    "parsed_directive": directive,
                    "formal_state_bypass": 0,
                }
        try:
            _committer.validate_change_request(
                str(execution["target_type"]),
                str(execution["operation"]),
                dict(execution["payload"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            return {"error": "FAILED_CLOSED", "reason": str(exc), "formal_state_bypass": 0}

        # Semantic writes are always deferred to the next GPT Patrol.  Keep
        # apply_now as a compatibility input, but never let it bypass patrol.
        from paw_patrol_orchestrator import PatrolOrchestrator, default_store_path
        store = patrol_store_path or default_store_path()
        orch = PatrolOrchestrator(store, db_path=db_path)
        queued = orch.enqueue_capture(capture_id, extra={"proposed_execution": dict(execution), "reason": reason})
        hold_status = "USER_DECISION_RECEIVED_WAITING_NEXT_PATROL" if (waiting_human or feedback) else queued.get("status")
        if waiting_human or feedback:
            orch.record_user_decision_receipt(
                receipt_id="capture-receipt:" + capture_id,
                subject_kind="capture",
                subject_id=capture_id,
                decision="APPROVE",
                original_input_ref=capture_id,
                note=reason,
            )
        return {
            "status": hold_status,
            "decision": GOVERNED_WRITE,
            "capture_id": capture_id,
            "queued_for_patrol": True,
            "immediate_write": False,
            "apply_now_requested": requested_apply_now,
            "todo_written": False,
            "knowledge_written": False,
            "project_written": False,
            "batch_id": queued.get("batch_id"),
            "reason": reason,
            "formal_state_bypass": 0,
        }
    finally:
        conn.close()

    result = consume_accepted_candidate(candidate_id, db_path=db_path)
    conn = _connect(db_path)
    try:
        if result.get("classification") in {"COMMITTED", "ALREADY_PRESENT"} or result.get("status") == "applied":
            _mark_processed(conn, capture_id)
            conn.commit()
        capture = _load_capture(conn, capture_id) or {}
    finally:
        conn.close()
    result.update({
        "decision": GOVERNED_WRITE,
        "capture_id": capture_id,
        "analysis_run_id": run_id,
        "analysis_model": GPT_PATROL_MODEL,
        "review_channel": REVIEW_CHANNEL,
        "approval_source": APPROVAL_SOURCE,
        "redundant_human_review": False,
        "processed_at": capture.get("processed_at"),
        "formal_state_bypass": 0,
    })
    return result


def capture_counts(*, db_path: str = PRODUCTION_DB) -> dict[str, Any]:
    conn = _connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM capture").fetchone()[0]
        unprocessed = count_patrol_eligible_captures(conn)
        processed = conn.execute("SELECT COUNT(*) FROM capture WHERE processed_at IS NOT NULL").fetchone()[0]
    finally:
        conn.close()
    return {
        "capture_total": total,
        "capture_unprocessed": unprocessed,
        "capture_processed": processed,
        "meaning": "capture_unprocessed counts patrol-eligible Captures only (waiting_patrol/waiting_gpt); waiting_human is excluded",
    }


def known_duplicate_counts(*, db_path: str = PRODUCTION_DB) -> dict[str, Any]:
    conn = _connect(db_path)
    try:
        cap = conn.execute(
            "SELECT COUNT(*) FROM capture WHERE id = ?",
            ("25f70472-8d4d-4d97-a724-50a8e79fe8bd",),
        ).fetchone()[0]
        todo = conn.execute(
            "SELECT COUNT(*) FROM todo WHERE id = ?",
            ("4eea2eb2-95b1-4b59-b098-ed138e3cd7ac",),
        ).fetchone()[0]
        title = conn.execute(
            "SELECT COUNT(*) FROM todo WHERE title = ?",
            ("下周需要做一下季度产能季刊了",),
        ).fetchone()[0]
        positive = conn.execute(
            "SELECT COUNT(*) FROM todo WHERE id = ?",
            ("todo-1870214-reconciled",),
        ).fetchone()[0]
    finally:
        conn.close()
    return {
        "KNOWN_CAPTURE_25F7_DUPLICATE_COUNT": cap,
        "TODO_4EEA_DUPLICATE_COUNT": todo,
        "TODO_QUARTERLY_TITLE_COUNT": title,
        "POSITIVE_TODO_1870214_COUNT": positive,
    }


def mcp_list_unprocessed_captures(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    return list_unprocessed_captures(limit=int(params.get("limit") or 20))


def mcp_submit_capture_decision(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    capture_id = str(params.get("capture_id") or "").strip()
    if not capture_id:
        return {"error": "capture_id required"}
    execution = params.get("execution")
    if isinstance(execution, str):
        execution = _json_obj(execution)
    return submit_capture_decision(
        capture_id,
        str(params.get("decision") or ""),
        reason=str(params.get("reason") or ""),
        review_statement=params.get("review_statement") or params.get("statement"),
        execution=execution if isinstance(execution, dict) else None,
        apply_now=bool(params.get("apply_now") or params.get("patrol_apply")),
    )


if os.environ.get("PAW_CAPTURE_GPT_PATROL_AUTO_REQUEUE") == "1":
    try:
        requeue_legacy_no_action_captures()
    except Exception:
        pass
