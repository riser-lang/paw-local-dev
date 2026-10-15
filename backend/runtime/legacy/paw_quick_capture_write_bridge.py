#!/usr/bin/env python3
"""Governed post-approval write bridge for Quick Record and write-ready CLEAR.

Converts an already-accepted Candidate into ReviewDecision -> ApprovedChange ->
Committer -> CommitRecord. No Formal State bypass. No semantic inference.

Deterministic normalization only. Legacy Knowledge/Project shapes that need
meaning-level rewriting are queued for the ChatGPT-owned post-approval recovery
tool. Cursor/this module never invents business content.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

GATEWAY = "/opt/paw-mcp-gateway"
if GATEWAY not in sys.path:
    sys.path.insert(0, GATEWAY)

import committer as _committer
from paw_source_version_governance import (
    commit_approved_change as _governance_commit_approved_change,
    install_committer_integration,
)

install_committer_integration()

PRODUCTION_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))
RECOVERY_STORE = Path("/var/lib/paw-mcp-gateway/paw_post_approval_recovery.json")
SUCCESSOR_STORE = Path("/var/lib/paw-mcp-gateway/paw_post_approval_successors.json")
CLAIM_LEASE_SECONDS = 1800

WRITE_INTENT_TYPES = frozenset({"todo", "knowledge", "project_update"})
NO_WRITE_TYPES = frozenset({"fact", "conflict", "ambiguity", "question"})

# Unified post-approval routes (machine-readable, fail-closed).
EXECUTION_READY_DETERMINISTIC = "EXECUTION_READY_DETERMINISTIC"
GPT_NORMALIZATION_REQUIRED = "GPT_NORMALIZATION_REQUIRED"
DECISION_ONLY_ACCEPTED = "DECISION_ONLY_ACCEPTED"
NO_CANONICAL_WRITE_REQUIRED = "NO_CANONICAL_WRITE_REQUIRED"
BLOCKED_USER_DECISION = "BLOCKED_USER_DECISION"
BLOCKED_INSUFFICIENT_EVIDENCE = "BLOCKED_INSUFFICIENT_EVIDENCE"

# Legacy classification aliases kept for existing MCP consumers.
COMMITTED = "COMMITTED"
ALREADY_PRESENT = "ALREADY_PRESENT"
GPT_NORMALIZATION_PENDING = "GPT_NORMALIZATION_PENDING"
USER_DECISION_REQUIRED = "USER_DECISION_REQUIRED"
NO_WRITE = "NO_WRITE"
FAILED_CLOSED = "FAILED_CLOSED"

REVIEW_MODE_DECISION_ONLY = "DECISION_ONLY"
REVIEW_MODE_EXECUTION_READY = "EXECUTION_READY"

REVIEW_CHANNEL = "chatgpt_explicit_user"
APPROVAL_SOURCE = "candidate_review"
REVIEWER_FALLBACK = "web-review-accept"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


def payload_hash(payload: Mapping[str, Any] | None) -> str:
    body = json.dumps(payload or {}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def idempotency_key(candidate_id: str, approved: Mapping[str, Any]) -> str:
    return f"capture-write:{candidate_id}:{payload_hash(approved)[:24]}"


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


def _proposed_change(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return _json_obj(candidate.get("proposed_change"))


def _original_payload(candidate: Mapping[str, Any]) -> dict[str, Any]:
    proposed = _proposed_change(candidate)
    if isinstance(proposed.get("original_payload"), dict):
        return dict(proposed["original_payload"])
    execution = proposed.get("execution")
    if isinstance(execution, dict) and isinstance(execution.get("payload"), dict):
        return dict(execution["payload"])
    return proposed


def _review_brief(candidate: Mapping[str, Any]) -> dict[str, Any]:
    # The normalized projection is the user-facing brief when a historical
    # candidate has one. Keep review_brief_json untouched as raw lineage.
    raw = candidate.get("review_display_json") or candidate.get("review_brief_json")
    if not raw:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    return _json_obj(raw)


def _has_execution_ready_envelope(proposed: Mapping[str, Any]) -> bool:
    execution = proposed.get("execution")
    if not isinstance(execution, dict):
        return False
    target = execution.get("target_type")
    operation = execution.get("operation")
    payload = execution.get("payload")
    if not target or not operation or not isinstance(payload, dict):
        return False
    try:
        _committer.validate_change_request(str(target), str(operation), dict(payload))
        return True
    except (ValueError, TypeError):
        return False


def _explicit_review_mode(candidate: Mapping[str, Any]) -> str | None:
    brief = _review_brief(candidate)
    mode = brief.get("review_mode")
    if mode in {REVIEW_MODE_DECISION_ONLY, REVIEW_MODE_EXECUTION_READY}:
        return str(mode)
    return None


def _review_mode(candidate: Mapping[str, Any]) -> str:
    explicit = _explicit_review_mode(candidate)
    if explicit:
        return explicit
    proposed = _proposed_change(candidate)
    metadata = proposed.get("review_metadata") if isinstance(proposed.get("review_metadata"), dict) else {}
    if metadata.get("review_origin") == "gpt_patrol" and _has_execution_ready_envelope(proposed):
        return REVIEW_MODE_EXECUTION_READY
    if _has_execution_ready_envelope(proposed):
        return REVIEW_MODE_EXECUTION_READY
    original = _original_payload(candidate)
    original = _original_payload(candidate)
    ctype = str(candidate.get("candidate_type") or "")
    if ctype == "todo":
        bindings = original.get("bindings")
        if isinstance(bindings, list) and bindings:
            for binding in bindings:
                if not isinstance(binding, dict):
                    return REVIEW_MODE_DECISION_ONLY
                if not binding.get("todo_id") or not binding.get("project_id"):
                    return REVIEW_MODE_DECISION_ONLY
            return REVIEW_MODE_EXECUTION_READY
    if ctype == "knowledge":
        if original.get("title") and original.get("content") and original.get("project_id"):
            return REVIEW_MODE_EXECUTION_READY
    return REVIEW_MODE_DECISION_ONLY


def _apply_deterministic_field_aliases(payload: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    if out.get("summary") and not out.get("one_line_summary"):
        out["one_line_summary"] = out["summary"]
    if out.get("update_summary") and not out.get("latest_progress_summary"):
        out["latest_progress_summary"] = out["update_summary"]
    return out


def effective_approved_payload(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Merge reviewed over original for EXECUTION_READY; keep lineage for DECISION_ONLY."""
    proposed = _proposed_change(candidate)
    original = _original_payload(candidate)
    reviewed = proposed.get("reviewed_payload") if isinstance(proposed.get("reviewed_payload"), dict) else None
    if reviewed:
        merged = dict(original)
        merged.update(reviewed)
        return _apply_deterministic_field_aliases(merged)
    return _apply_deterministic_field_aliases(original)


def approved_payload(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Return the user-authorized payload for execution identity checks."""
    return effective_approved_payload(candidate)


def _legacy_classification_for_route(route: str, *, equivalent_id: str | None = None) -> str:
    if route == EXECUTION_READY_DETERMINISTIC:
        return ALREADY_PRESENT if equivalent_id else COMMITTED
    if route == GPT_NORMALIZATION_REQUIRED:
        return GPT_NORMALIZATION_PENDING
    if route == NO_CANONICAL_WRITE_REQUIRED:
        return NO_WRITE
    if route == BLOCKED_USER_DECISION:
        return USER_DECISION_REQUIRED
    if route == BLOCKED_INSUFFICIENT_EVIDENCE:
        return FAILED_CLOSED
    if route == DECISION_ONLY_ACCEPTED:
        return USER_DECISION_REQUIRED
    return FAILED_CLOSED


def _next_action_for_route(route: str) -> str:
    return {
        EXECUTION_READY_DETERMINISTIC: "consume_or_commit",
        GPT_NORMALIZATION_REQUIRED: "gpt_patrol_claim_post_approval_recovery",
        DECISION_ONLY_ACCEPTED: "gpt_patrol_prepare_successor",
        NO_CANONICAL_WRITE_REQUIRED: "none",
        BLOCKED_USER_DECISION: "user_decision_required",
        BLOCKED_INSUFFICIENT_EVIDENCE: "blocked_insufficient_evidence",
    }.get(route, "blocked")


def _has_committed_change(conn: sqlite3.Connection, candidate_id: str) -> bool:
    row = conn.execute(
        """SELECT 1 FROM approved_change ac
           JOIN review_decision rd ON rd.id = ac.approval_ref
           JOIN commit_record cr ON cr.change_id = ac.change_id
           WHERE rd.candidate_id = ? AND ac.status = 'committed' AND cr.result = 'success'
           LIMIT 1""",
        (candidate_id,),
    ).fetchone()
    return row is not None


def route_post_approval(
    conn: sqlite3.Connection,
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Unified fail-closed post-approval router."""
    ctype = str(candidate.get("candidate_type") or "")
    review_mode = _review_mode(candidate)
    approved = effective_approved_payload(candidate)
    base = {
        "candidate_id": candidate.get("id"),
        "candidate_type": ctype,
        "review_mode": review_mode,
        "approved_payload_hash": payload_hash(approved),
        "execution": None,
        "equivalent_id": None,
        "reason": "",
        "route": "",
        "next_action": "",
        "classification": "",
        "canonical_write_allowed": False,
    }

    if ctype in NO_WRITE_TYPES:
        route = NO_CANONICAL_WRITE_REQUIRED
        return {
            **base,
            "route": route,
            "classification": _legacy_classification_for_route(route),
            "next_action": _next_action_for_route(route),
            "reason": "candidate_type has no canonical write",
        }

    if ctype not in WRITE_INTENT_TYPES:
        route = BLOCKED_USER_DECISION
        return {
            **base,
            "route": route,
            "classification": _legacy_classification_for_route(route),
            "next_action": _next_action_for_route(route),
            "reason": f"unsupported candidate_type {ctype}",
        }

    if candidate.get("status") == "applied" or candidate.get("applied_entity_id") or _has_committed_change(conn, str(candidate.get("id"))):
        route = EXECUTION_READY_DETERMINISTIC
        return {
            **base,
            "route": route,
            "classification": COMMITTED,
            "next_action": _next_action_for_route(route),
            "reason": "candidate already applied or committed",
            "canonical_write_allowed": False,
        }

    if review_mode == REVIEW_MODE_DECISION_ONLY and _explicit_review_mode(candidate) == REVIEW_MODE_DECISION_ONLY:
        route = DECISION_ONLY_ACCEPTED
        return {
            **base,
            "route": route,
            "classification": _legacy_classification_for_route(route),
            "next_action": _next_action_for_route(route),
            "reason": "review_mode=DECISION_ONLY confirms direction only; successor EXECUTION_READY review required",
        }

    classified = classify_and_normalize(conn, candidate)
    legacy = classified["classification"]
    if legacy in {COMMITTED, ALREADY_PRESENT}:
        route = EXECUTION_READY_DETERMINISTIC
    elif legacy == GPT_NORMALIZATION_PENDING:
        route = GPT_NORMALIZATION_REQUIRED
    elif legacy == NO_WRITE:
        route = NO_CANONICAL_WRITE_REQUIRED
    elif legacy == USER_DECISION_REQUIRED:
        route = BLOCKED_USER_DECISION
    else:
        route = BLOCKED_INSUFFICIENT_EVIDENCE
    return {
        **base,
        "route": route,
        "classification": classified["classification"],
        "next_action": _next_action_for_route(route),
        "reason": classified.get("reason") or "",
        "execution": classified.get("execution"),
        "equivalent_id": classified.get("equivalent_id"),
        "canonical_write_allowed": route == EXECUTION_READY_DETERMINISTIC,
    }


def _source_ref_list(candidate: Mapping[str, Any]) -> list[str]:
    refs = candidate.get("source_refs")
    parsed = refs
    if isinstance(refs, str):
        try:
            parsed = json.loads(refs)
        except (json.JSONDecodeError, TypeError):
            parsed = []
    if isinstance(parsed, dict):
        raw = parsed.get("refs") or []
        return [str(x) for x in raw if x]
    if isinstance(parsed, list):
        return [str(x) for x in parsed if x]
    return []


def _project_exists(conn: sqlite3.Connection, project_id: str | None) -> bool:
    if not project_id:
        return False
    row = conn.execute("SELECT 1 FROM project WHERE id = ?", (project_id,)).fetchone()
    return row is not None


def _deterministic_slug(title: str) -> str:
    ascii_part = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
    digest = hashlib.sha256(title.encode("utf-8")).hexdigest()[:10]
    return f"{ascii_part}-{digest}" if ascii_part else f"k-{digest}"


def _todo_equivalent(conn: sqlite3.Connection, payload: Mapping[str, Any]) -> str | None:
    title = str(payload.get("title") or "").strip()
    if not title:
        return None
    project_id = payload.get("project_id")
    if project_id:
        row = conn.execute(
            "SELECT id FROM todo WHERE title = ? AND IFNULL(project_id,'') = ? ORDER BY created_at LIMIT 1",
            (title, project_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id FROM todo WHERE title = ? ORDER BY created_at LIMIT 1",
            (title,),
        ).fetchone()
    return row["id"] if row else None


def _knowledge_equivalent(conn: sqlite3.Connection, payload: Mapping[str, Any]) -> str | None:
    title = str(payload.get("title") or "").strip()
    if not title:
        return None
    row = conn.execute(
        "SELECT id FROM knowledge_page WHERE title = ? ORDER BY created_at LIMIT 1",
        (title,),
    ).fetchone()
    return row["id"] if row else None


def _project_progress_equivalent(conn: sqlite3.Connection, payload: Mapping[str, Any]) -> str | None:
    project_id = str(payload.get("id") or "").strip()
    summary = str(payload.get("latest_progress_summary") or "").strip()
    if not project_id or not summary:
        return None
    row = conn.execute(
        "SELECT id, latest_progress_summary FROM project WHERE id = ?",
        (project_id,),
    ).fetchone()
    if not row:
        return None
    current = str(row["latest_progress_summary"] or "").strip()
    if current == summary:
        return row["id"]
    return None


def classify_and_normalize(
    conn: sqlite3.Connection,
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Deterministic classification. Never invents titles, ids, or metric values."""
    ctype = str(candidate.get("candidate_type") or "")
    approved = effective_approved_payload(candidate)
    proposed = _proposed_change(candidate)
    base = {
        "candidate_id": candidate.get("id"),
        "candidate_type": ctype,
        "approved_payload_hash": payload_hash(approved),
        "execution": None,
        "equivalent_id": None,
        "reason": "",
    }

    if ctype in NO_WRITE_TYPES:
        return {**base, "classification": NO_WRITE, "reason": "candidate_type has no canonical write"}

    if ctype not in WRITE_INTENT_TYPES:
        return {**base, "classification": USER_DECISION_REQUIRED, "reason": f"unsupported candidate_type {ctype}"}

    if proposed.get("create_new_project") is True or approved.get("create_new_project") is True:
        return {**base, "classification": USER_DECISION_REQUIRED, "reason": "project creation remains human-defined"}

    execution = None
    raw_exec = approved.get("execution") if isinstance(approved.get("execution"), dict) else None
    if raw_exec is None and isinstance(proposed.get("execution"), dict):
        raw_exec = proposed["execution"]
    if isinstance(raw_exec, dict) and raw_exec.get("target_type") and raw_exec.get("operation"):
        payload = raw_exec.get("payload") if isinstance(raw_exec.get("payload"), dict) else {}
        try:
            _committer.validate_change_request(str(raw_exec["target_type"]), str(raw_exec["operation"]), payload)
            execution = {
                "target_type": str(raw_exec["target_type"]),
                "operation": str(raw_exec["operation"]),
                "payload": dict(payload),
            }
        except ValueError as exc:
            return {**base, "classification": FAILED_CLOSED, "reason": f"stale execution envelope: {exc}"}

    if execution is None and ctype == "todo":
        title = str(approved.get("title") or "").strip()
        bindings = approved.get("bindings")
        if not title and isinstance(bindings, list) and bindings:
            return {
                **base,
                "classification": GPT_NORMALIZATION_PENDING,
                "reason": "todo bindings require semantic normalization",
            }
        if not title:
            return {**base, "classification": GPT_NORMALIZATION_PENDING, "reason": "todo missing title"}
        payload: dict[str, Any] = {"title": title}
        if approved.get("description") not in (None, ""):
            payload["description"] = approved["description"]
        if approved.get("due_at") not in (None, ""):
            payload["due_at"] = approved["due_at"]
        if approved.get("priority") not in (None, ""):
            payload["priority"] = approved["priority"]
        project_id = approved.get("project_id")
        if project_id and _project_exists(conn, str(project_id)):
            payload["project_id"] = project_id
        payload["source_type"] = "ai_candidate"
        payload["ai_generated"] = 1
        execution = {"target_type": "todo", "operation": "create", "payload": payload}

    if execution is None and ctype == "knowledge":
        title = str(approved.get("title") or approved.get("target_title") or "").strip()
        existing_id = (
            approved.get("knowledge_id")
            or approved.get("canonical_knowledge_id")
            or approved.get("target_id")
            or approved.get("duplicate_knowledge_id")
        )
        if existing_id:
            return {
                **base,
                "classification": GPT_NORMALIZATION_PENDING,
                "reason": "legacy knowledge revise/merge requires semantic normalization",
            }
        if not title:
            return {**base, "classification": GPT_NORMALIZATION_PENDING, "reason": "knowledge missing title"}
        has_body = approved.get("content") not in (None, "") or approved.get("one_line_summary") not in (None, "")
        if not has_body:
            return {
                **base,
                "classification": GPT_NORMALIZATION_PENDING,
                "reason": "knowledge missing content/one_line_summary; Cursor will not invent body text",
            }
        payload = {
            "title": title,
            "slug": str(approved.get("slug") or "").strip() or _deterministic_slug(title),
        }
        if "content" in approved:
            payload["content"] = approved["content"]
        if approved.get("one_line_summary") not in (None, ""):
            payload["one_line_summary"] = approved["one_line_summary"]
        if approved.get("knowledge_type") not in (None, ""):
            payload["knowledge_type"] = approved["knowledge_type"]
        execution = {"target_type": "knowledge", "operation": "create", "payload": payload}

    if execution is None and ctype == "project_update":
        project_id = str(
            approved.get("project_id")
            or approved.get("target_project_id")
            or approved.get("id")
            or ""
        ).strip()
        summary = str(
            approved.get("update_summary")
            or approved.get("latest_progress_summary")
            or ""
        ).strip()
        if not project_id or not _project_exists(conn, project_id):
            return {
                **base,
                "classification": GPT_NORMALIZATION_PENDING,
                "reason": "project_update missing resolvable project_id",
            }
        if not summary:
            return {
                **base,
                "classification": GPT_NORMALIZATION_PENDING,
                "reason": "project_update missing update_summary",
            }
        payload = {"id": project_id, "latest_progress_summary": summary}
        execution = {"target_type": "project", "operation": "update", "payload": payload}

    if execution is None:
        return {**base, "classification": GPT_NORMALIZATION_PENDING, "reason": "no deterministic execution envelope"}

    try:
        _committer.validate_change_request(execution["target_type"], execution["operation"], execution["payload"])
    except ValueError as exc:
        return {**base, "classification": FAILED_CLOSED, "reason": f"schema validation failed: {exc}", "execution": execution}

    equivalent = _equivalent_for_execution(conn, execution)
    classification = ALREADY_PRESENT if equivalent else COMMITTED
    return {
        **base,
        "classification": classification,
        "execution": execution,
        "equivalent_id": equivalent,
        "reason": "canonical equivalent already present" if equivalent else "deterministic execution ready",
    }


def _load_candidate(conn: sqlite3.Connection, candidate_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM candidate WHERE id = ?", (candidate_id,)).fetchone()
    return dict(row) if row else None


def _find_existing_change(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM approved_change WHERE idempotency_key = ? ORDER BY created_at DESC LIMIT 1",
        (key,),
    ).fetchone()


def _equivalent_for_execution(conn: sqlite3.Connection, execution: Mapping[str, Any]) -> str | None:
    payload = execution.get("payload") if isinstance(execution.get("payload"), dict) else {}
    target = execution.get("target_type")
    operation = execution.get("operation")
    if target == "todo" and operation == "create":
        return _todo_equivalent(conn, payload)
    if target == "knowledge" and operation == "create":
        return _knowledge_equivalent(conn, payload)
    if target == "project" and operation == "update":
        return _project_progress_equivalent(conn, payload)
    return None


def _reconcile_candidate(
    conn: sqlite3.Connection,
    candidate_id: str,
    *,
    entity_type: str,
    entity_id: str,
) -> None:
    conn.execute(
        """UPDATE candidate
           SET status = 'applied',
               applied_entity_type = ?,
               applied_entity_id = ?
           WHERE id = ?""",
        (entity_type, entity_id, candidate_id),
    )


def _mark_already_present(
    conn: sqlite3.Connection,
    *,
    change_id: str,
    target_type: str,
    target_id: str,
) -> str:
    now = utc_now()
    conn.execute("UPDATE approved_change SET status = 'committed' WHERE change_id = ?", (change_id,))
    commit_id = _uuid()
    conn.execute(
        """INSERT INTO commit_record (id, change_id, transaction_id, result, committed_at, error)
           VALUES (?, ?, ?, 'success', ?, NULL)""",
        (commit_id, change_id, f"already-present:{target_id}", now),
    )
    conn.execute(
        """INSERT INTO audit_event
           (id, event_type, actor, target_type, target_id, change_id, commit_id, details, created_at)
           VALUES (?, 'commit', 'committer', ?, ?, ?, ?, ?, ?)""",
        (_uuid(), target_type, target_id, change_id, commit_id,
         json.dumps({"operation": "already_present", "no_rewrite": True}, ensure_ascii=False), now),
    )
    return commit_id


def _recovery_store(path: Path | None) -> Path:
    return path or RECOVERY_STORE


def _load_recovery(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": "paw-post-approval-recovery/1", "items": {}, "updated_at": utc_now()}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"schema": "paw-post-approval-recovery/1", "items": {}, "updated_at": utc_now()}


def _save_recovery(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = utc_now()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _successor_store(path: Path | None) -> Path:
    return path or SUCCESSOR_STORE


def _load_successor(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": "paw-post-approval-successors/1", "items": {}, "updated_at": utc_now()}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"schema": "paw-post-approval-successors/1", "items": {}, "updated_at": utc_now()}


def _save_successor(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = utc_now()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _lease_expired(item: Mapping[str, Any]) -> bool:
    expires = item.get("lease_expires_at")
    if not expires:
        return True
    try:
        return datetime.fromisoformat(str(expires)) <= datetime.now(timezone.utc)
    except ValueError:
        return True


def _refresh_recovery_leases(data: dict[str, Any]) -> None:
    for cid, item in list((data.get("items") or {}).items()):
        if item.get("status") == "claimed" and _lease_expired(item):
            item["status"] = "pending"
            item["claimed_by"] = None
            item["claimed_at"] = None
            item["lease_expires_at"] = None
            data["items"][cid] = item


def _review_feedback(candidate: Mapping[str, Any]) -> dict[str, Any] | None:
    """Read durable Web/API review metadata without treating it as a payload."""
    value: Any = candidate.get("proposed_change")
    for _ in range(4):
        value = _json_obj(value)
        metadata = value.get("review_metadata") if isinstance(value, dict) else None
        if isinstance(metadata, dict):
            return {
                "reviewed_by": metadata.get("reviewed_by"),
                "reviewed_at": metadata.get("reviewed_at"),
                "review_notes": metadata.get("review_notes"),
                "review_origin": metadata.get("review_origin", "api"),
                "action": metadata.get("action"),
            }
        if not isinstance(value, dict):
            break
        value = value.get("reviewed_payload") or value.get("original_payload")
    return None


def _patrol_evidence(conn: sqlite3.Connection, candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for ref in _source_ref_list(candidate):
        capture_id = ref.split(":", 1)[1] if ref.startswith("capture:") else ref
        try:
            row = conn.execute("SELECT id, raw_text, captured_at FROM capture WHERE id = ?", (capture_id,)).fetchone()
        except sqlite3.OperationalError:
            return evidence
        if row:
            evidence.append({"ref": ref, "capture_id": row["id"], "captured_at": row["captured_at"], "raw_text": str(row["raw_text"] or "")[:8000]})
    return evidence


def _patrol_context(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            "SELECT id, name, status, description, next_action_summary, latest_progress_summary FROM project ORDER BY updated_at DESC LIMIT 100"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(row) for row in rows]


def _related_resolution_feedback(conn: sqlite3.Connection, candidate_id: str) -> list[dict[str, Any]]:
    """Include clarifications attached to this candidate or its lineage."""
    try:
        rows = conn.execute(
            """SELECT r.candidate_id, r.user_response, r.selected_option, r.resolved_by,
                      r.resolved_at, r.resolution_status
                 FROM candidate_resolution r
                 LEFT JOIN candidate c ON c.id = r.candidate_id
                WHERE r.candidate_id = ? OR c.source_refs LIKE ?
                ORDER BY r.created_at ASC""",
            (candidate_id, f"%{candidate_id}%"),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        {
            "candidate_id": row["candidate_id"],
            "user_response": row["user_response"],
            "selected_option": row["selected_option"],
            "reviewed_by": row["resolved_by"],
            "reviewed_at": row["resolved_at"],
            "resolution_status": row["resolution_status"],
        }
        for row in rows
        if row["user_response"] or row["selected_option"]
    ]


def enqueue_gpt_recovery(
    candidate: Mapping[str, Any],
    classified: Mapping[str, Any],
    *,
    store_path: Path | None = None,
    db_path: str = PRODUCTION_DB,
) -> dict[str, Any]:
    path = _recovery_store(store_path)
    data = _load_recovery(path)
    _refresh_recovery_leases(data)
    cid = str(candidate["id"])
    existing = (data.get("items") or {}).get(cid) or {}
    route = classified.get("route") or classified.get("classification")
    if (
        existing.get("status") in {"pending", "claimed"}
        and existing.get("approved_payload_hash") == classified.get("approved_payload_hash")
        and existing.get("route") == route
        and existing.get("review_feedback") is not None
        and "original_evidence" in existing
        and "current_project_context" in existing
    ):
        return existing
    conn = _connect(db_path)
    try:
        evidence = _patrol_evidence(conn, candidate)
        projects = _patrol_context(conn)
        related_feedback = _related_resolution_feedback(conn, str(candidate["id"]))
    finally:
        conn.close()
    original = _proposed_change(candidate)
    original_payload = original.get("original_payload") if isinstance(original.get("original_payload"), dict) else original
    item = {
        "candidate_id": cid,
        "candidate_type": candidate.get("candidate_type"),
        "status": "pending" if existing.get("status") != "consumed" else "consumed",
        "route": route,
        "classification": classified.get("classification"),
        "next_action": classified.get("next_action") or _next_action_for_route(str(route)),
        "reason": classified.get("reason"),
        "review_mode": classified.get("review_mode") or _review_mode(candidate),
        "approved_payload_hash": classified.get("approved_payload_hash"),
        "approved_payload": effective_approved_payload(candidate),
        "candidate_payload": original,
        "original_payload": original_payload,
        "edited_payload": effective_approved_payload(candidate),
        "review_feedback": _review_feedback(candidate),
        "related_review_feedback": related_feedback,
        "original_evidence": evidence,
        "current_project_context": projects,
        "lineage": {
            "analysis_run_id": candidate.get("analysis_run_id"),
            "source_refs": _source_ref_list(candidate),
            "candidate_id": cid,
            "review_mode": classified.get("review_mode") or _review_mode(candidate),
        },
        "allowed_target_types": ["todo", "knowledge", "project"],
        "claimed_by": None,
        "claimed_at": None,
        "lease_expires_at": None,
        "created_at": existing.get("created_at") or utc_now(),
    }
    if existing.get("status") == "consumed":
        item["consumed_at"] = existing.get("consumed_at")
        item["result"] = existing.get("result")
    data.setdefault("items", {})[cid] = item
    _save_recovery(path, data)
    return item


def enqueue_decision_only_successor(
    candidate: Mapping[str, Any],
    routed: Mapping[str, Any],
    *,
    store_path: Path | None = None,
    db_path: str = PRODUCTION_DB,
) -> dict[str, Any]:
    """Idempotently queue DECISION_ONLY accepted items for GPT successor preparation."""
    path = _successor_store(store_path)
    data = _load_successor(path)
    cid = str(candidate["id"])
    existing = (data.get("items") or {}).get(cid) or {}
    if existing.get("status") in {"prepared", "successor_created"} and existing.get("approved_payload_hash") == routed.get("approved_payload_hash"):
        return existing
    conn = _connect(db_path)
    try:
        evidence = _patrol_evidence(conn, candidate)
        projects = _patrol_context(conn)
        related_feedback = _related_resolution_feedback(conn, cid)
    finally:
        conn.close()
    brief = _review_brief(candidate)
    item = {
        "candidate_id": cid,
        "predecessor_id": cid,
        "candidate_type": candidate.get("candidate_type"),
        "status": "pending_preparation",
        "route": DECISION_ONLY_ACCEPTED,
        "review_mode": REVIEW_MODE_DECISION_ONLY,
        "next_action": "gpt_patrol_prepare_successor",
        "reason": routed.get("reason"),
        "approved_payload_hash": routed.get("approved_payload_hash"),
        "approved_payload": effective_approved_payload(candidate),
        "original_payload": _original_payload(candidate),
        "review_brief": brief,
        "review_feedback": _review_feedback(candidate),
        "related_review_feedback": related_feedback,
        "original_evidence": evidence,
        "current_project_context": projects,
        "lineage": {
            "analysis_run_id": candidate.get("analysis_run_id"),
            "source_refs": _source_ref_list(candidate),
            "candidate_id": cid,
            "predecessor_id": cid,
            "review_mode": REVIEW_MODE_DECISION_ONLY,
        },
        "successor_candidate_id": existing.get("successor_candidate_id"),
        "created_at": existing.get("created_at") or utc_now(),
        "updated_at": utc_now(),
    }
    data.setdefault("items", {})[cid] = item
    _save_successor(path, data)
    return item


def sync_post_approval_projection(
    candidate: Mapping[str, Any],
    routed: Mapping[str, Any],
    *,
    db_path: str = PRODUCTION_DB,
    recovery_store: Path | None = None,
    successor_store: Path | None = None,
) -> dict[str, Any]:
    """Ensure durable queue visibility matches the unified route."""
    route = routed.get("route")
    cid = str(candidate["id"])
    recovery_path = _recovery_store(recovery_store)
    recovery_data = _load_recovery(recovery_path)
    _refresh_recovery_leases(recovery_data)
    recovery_item = (recovery_data.get("items") or {}).get(cid)
    if recovery_item and recovery_item.get("status") != "consumed" and route != GPT_NORMALIZATION_REQUIRED:
        recovery_item["status"] = "superseded"
        recovery_item["superseded_by"] = route
        recovery_item["superseded_at"] = utc_now()
        recovery_data["items"][cid] = recovery_item
        _save_recovery(recovery_path, recovery_data)
    if route == GPT_NORMALIZATION_REQUIRED:
        successor_data = _load_successor(_successor_store(successor_store))
        successor_item = (successor_data.get("items") or {}).get(cid)
        if successor_item and successor_item.get("status") == "pending_preparation":
            successor_item["status"] = "superseded"
            successor_item["superseded_by"] = route
            successor_item["superseded_at"] = utc_now()
            successor_data["items"][cid] = successor_item
            _save_successor(_successor_store(successor_store), successor_data)
        return enqueue_gpt_recovery(candidate, routed, store_path=recovery_store, db_path=db_path)
    if route == DECISION_ONLY_ACCEPTED:
        return enqueue_decision_only_successor(candidate, routed, store_path=successor_store, db_path=db_path)
    return {"route": route, "synced": False}


def consume_accepted_candidate(
    candidate_id: str,
    *,
    db_path: str = PRODUCTION_DB,
    recovery_store: Path | None = None,
    successor_store: Path | None = None,
    execution_override: Mapping[str, Any] | None = None,
    idempotency_key_override: str | None = None,
) -> dict[str, Any]:
    """Consume one accepted write-intent candidate through the governed chain."""
    previous_db = _committer.DB_PATH
    _committer.DB_PATH = db_path
    conn: sqlite3.Connection | None = _connect(db_path)
    try:
        candidate = _load_candidate(conn, candidate_id)
        if not candidate:
            return {"error": "candidate_not_found", "candidate_id": candidate_id}
        if candidate.get("status") == "applied" and candidate.get("applied_entity_id"):
            return {
                "candidate_id": candidate_id,
                "classification": COMMITTED,
                "status": "applied",
                "idempotent": True,
                "applied_entity_type": candidate.get("applied_entity_type"),
                "applied_entity_id": candidate.get("applied_entity_id"),
                "message": "already applied; no duplicate write",
            }
        if candidate.get("status") != "accepted" and candidate.get("status") != "applied":
            return {
                "error": "invalid_status",
                "candidate_id": candidate_id,
                "status": candidate.get("status"),
                "message": "only accepted candidates may be consumed without a second approval",
            }

        routed = route_post_approval(conn, candidate)
        classified = dict(routed)
        if execution_override:
            classified = dict(classified)
            classified["execution"] = {
                "target_type": str(execution_override["target_type"]),
                "operation": str(execution_override["operation"]),
                "payload": dict(execution_override["payload"]),
            }
            try:
                _committer.validate_change_request(
                    classified["execution"]["target_type"],
                    classified["execution"]["operation"],
                    classified["execution"]["payload"],
                )
            except (KeyError, TypeError, ValueError) as exc:
                return {"error": "FAILED_CLOSED", "candidate_id": candidate_id, "reason": str(exc)}
            equivalent = _equivalent_for_execution(conn, classified["execution"])
            classified["equivalent_id"] = equivalent
            classified["classification"] = ALREADY_PRESENT if equivalent else COMMITTED
            classified["reason"] = (
                "canonical equivalent already present"
                if equivalent
                else "GPT-normalized exact execution envelope"
            )

            classified["classification"] = ALREADY_PRESENT if equivalent else COMMITTED
            classified["route"] = EXECUTION_READY_DETERMINISTIC
            classified["reason"] = (
                "canonical equivalent already present"
                if equivalent
                else "GPT-normalized exact execution envelope"
            )

        route = classified.get("route") or routed.get("route")
        if route == DECISION_ONLY_ACCEPTED:
            sync_post_approval_projection(
                candidate, routed, db_path=db_path,
                recovery_store=recovery_store, successor_store=successor_store,
            )
            return {
                "candidate_id": candidate_id,
                "route": route,
                "classification": classified.get("classification"),
                "reason": classified.get("reason"),
                "next_action": classified.get("next_action"),
                "status": candidate.get("status"),
                "canonical_write_allowed": False,
            }

        if route in {GPT_NORMALIZATION_REQUIRED, BLOCKED_USER_DECISION, NO_CANONICAL_WRITE_REQUIRED, BLOCKED_INSUFFICIENT_EVIDENCE}:
            if route == GPT_NORMALIZATION_REQUIRED:
                sync_post_approval_projection(
                    candidate, routed, db_path=db_path,
                    recovery_store=recovery_store, successor_store=successor_store,
                )
            return {
                "candidate_id": candidate_id,
                "route": route,
                "classification": classified.get("classification"),
                "reason": classified.get("reason"),
                "next_action": classified.get("next_action"),
                "status": candidate.get("status"),
                "canonical_write_allowed": False,
            }

        if classified["classification"] in {GPT_NORMALIZATION_PENDING, USER_DECISION_REQUIRED, NO_WRITE, FAILED_CLOSED}:
            if classified["classification"] == GPT_NORMALIZATION_PENDING:
                sync_post_approval_projection(
                    candidate, routed, db_path=db_path,
                    recovery_store=recovery_store, successor_store=successor_store,
                )
            return {
                "candidate_id": candidate_id,
                "route": route,
                "classification": classified["classification"],
                "reason": classified["reason"],
                "next_action": classified.get("next_action"),
                "status": candidate.get("status"),
                "canonical_write_allowed": False,
            }

        approved = approved_payload(candidate)
        key = idempotency_key_override or idempotency_key(candidate_id, approved)
        existing = _find_existing_change(conn, key)
        execution = classified["execution"]
        assert execution is not None

        if existing and existing["status"] == "committed":
            rec = conn.execute(
                "SELECT * FROM commit_record WHERE change_id = ? AND result = 'success' ORDER BY committed_at DESC LIMIT 1",
                (existing["change_id"],),
            ).fetchone()
            target_id = classified.get("equivalent_id") or candidate.get("applied_entity_id")
            if rec and not candidate.get("applied_entity_id"):
                payload = _json_obj(existing["payload"])
                target_id = payload.get("id") or classified.get("equivalent_id")
                if not target_id and rec["transaction_id"] and str(rec["transaction_id"]).startswith("already-present:"):
                    target_id = str(rec["transaction_id"]).split(":", 1)[-1]
                if target_id:
                    _reconcile_candidate(conn, candidate_id, entity_type=existing["target_type"], entity_id=str(target_id))
                    conn.commit()
            return {
                "candidate_id": candidate_id,
                "classification": ALREADY_PRESENT if classified["classification"] == ALREADY_PRESENT else COMMITTED,
                "status": "applied",
                "idempotent": True,
                "change_id": existing["change_id"],
                "commit_id": rec["id"] if rec else None,
                "applied_entity_id": target_id,
                "message": "idempotent replay of committed ApprovedChange",
            }

        if existing and existing["status"] == "pending":
            change_id = existing["change_id"]
            if classified["classification"] == ALREADY_PRESENT:
                target_id = str(classified["equivalent_id"])
                commit_id = _mark_already_present(
                    conn,
                    change_id=change_id,
                    target_type=execution["target_type"],
                    target_id=target_id,
                )
                _reconcile_candidate(conn, candidate_id, entity_type=execution["target_type"], entity_id=target_id)
                conn.commit()
                return {
                    "candidate_id": candidate_id,
                    "classification": ALREADY_PRESENT,
                    "change_id": change_id,
                    "commit_id": commit_id,
                    "applied_entity_id": target_id,
                    "status": "applied",
                    "message": "already-equivalent canonical entity; no rewrite",
                }
            conn.close()
            conn = None
            result = _governance_commit_approved_change(change_id)
            conn = _connect(db_path)
            if result.get("result") == "success" and result.get("target_id"):
                _reconcile_candidate(conn, candidate_id, entity_type=execution["target_type"], entity_id=str(result["target_id"]))
                conn.commit()
            return {
                "candidate_id": candidate_id,
                "classification": COMMITTED if result.get("result") == "success" else FAILED_CLOSED,
                "change_id": change_id,
                "commit_id": result.get("commit_id"),
                "applied_entity_id": result.get("target_id"),
                "commit_result": result,
            }

        reviewer = str(candidate.get("reviewed_by") or REVIEWER_FALLBACK)
        notes = (
            f"Consume durable accept reviewed_at={candidate.get('reviewed_at')}; "
            "no second user approval."
        )
        if classified["classification"] == ALREADY_PRESENT:
            notes += " Canonical equivalent present; no rewrite."
        already = classified["classification"] == ALREADY_PRESENT
        equivalent_id = classified.get("equivalent_id")
        conn.close()
        conn = None

        rd_id, change_id = _committer.create_review_decision_atomic(
            candidate_id=candidate_id,
            decision="approve",
            reviewer=reviewer,
            review_channel=REVIEW_CHANNEL,
            approval_source=APPROVAL_SOURCE,
            original_payload=execution["payload"],
            reviewed_payload=execution["payload"],
            review_notes=notes,
            target_type=execution["target_type"],
            operation=execution["operation"],
            source_refs=_source_ref_list(candidate),
            create_approved_change=True,
            idempotency_key=key,
        )

        if already:
            conn = _connect(db_path)
            target_id = str(equivalent_id)
            commit_id = _mark_already_present(
                conn,
                change_id=change_id,
                target_type=execution["target_type"],
                target_id=target_id,
            )
            _reconcile_candidate(conn, candidate_id, entity_type=execution["target_type"], entity_id=target_id)
            conn.commit()
            return {
                "candidate_id": candidate_id,
                "classification": ALREADY_PRESENT,
                "review_decision_id": rd_id,
                "change_id": change_id,
                "commit_id": commit_id,
                "applied_entity_type": execution["target_type"],
                "applied_entity_id": target_id,
                "status": "applied",
                "message": "already-equivalent canonical entity; no rewrite",
            }

        result = _governance_commit_approved_change(change_id)
        conn = _connect(db_path)
        if result.get("result") != "success" or not result.get("target_id"):
            return {
                "candidate_id": candidate_id,
                "classification": FAILED_CLOSED,
                "review_decision_id": rd_id,
                "change_id": change_id,
                "commit_result": result,
                "reason": result.get("error") or "commit failed",
            }
        _reconcile_candidate(
            conn, candidate_id,
            entity_type=execution["target_type"],
            entity_id=str(result["target_id"]),
        )
        conn.commit()
        return {
            "candidate_id": candidate_id,
            "classification": COMMITTED,
            "review_decision_id": rd_id,
            "change_id": change_id,
            "commit_id": result.get("commit_id"),
            "applied_entity_type": execution["target_type"],
            "applied_entity_id": result.get("target_id"),
            "status": "applied",
            "transaction_id": result.get("transaction_id"),
        }
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        _committer.DB_PATH = previous_db


def list_accepted_write_intents(*, db_path: str = PRODUCTION_DB, limit: int = 50, recovery_store: Path | None = None, successor_store: Path | None = None) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """SELECT * FROM candidate
               WHERE status = 'accepted'
                 AND candidate_type IN ('todo','knowledge','project_update')
               ORDER BY reviewed_at ASC, created_at ASC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        out = []
        for row in rows:
            cand = dict(row)
            routed = route_post_approval(conn, cand)
            sync_post_approval_projection(
                cand,
                routed,
                db_path=db_path,
                recovery_store=recovery_store,
                successor_store=successor_store,
            )
            out.append({
                "candidate_id": cand["id"],
                "candidate_type": cand["candidate_type"],
                "status": cand["status"],
                "review_mode": routed.get("review_mode"),
                "route": routed.get("route"),
                "classification": routed.get("classification"),
                "reason": routed.get("reason"),
                "next_action": routed.get("next_action"),
                "approved_payload_hash": routed.get("approved_payload_hash"),
                "canonical_write_allowed": routed.get("canonical_write_allowed", False),
            })
        return out
    finally:
        conn.close()


def consume_accepted_writes(
    *,
    db_path: str = PRODUCTION_DB,
    recovery_store: Path | None = None,
    successor_store: Path | None = None,
    limit: int = 20,
    candidate_ids: list[str] | None = None,
    patrol_gate: bool = False,
) -> dict[str, Any]:
    if candidate_ids:
        ids = candidate_ids[:limit]
    else:
        ids = [item["candidate_id"] for item in list_accepted_write_intents(db_path=db_path, limit=limit)]
    results = []
    for cid in ids:
        if patrol_gate:
            conn = _connect(db_path)
            try:
                candidate = _load_candidate(conn, cid)
                if not candidate:
                    results.append({"candidate_id": cid, "error": "candidate_not_found"})
                    continue
                routed = route_post_approval(conn, candidate)
                sync_post_approval_projection(
                    candidate,
                    routed,
                    db_path=db_path,
                    recovery_store=recovery_store,
                    successor_store=successor_store,
                )
                results.append({
                    "candidate_id": cid,
                    "route": routed.get("route"),
                    "classification": routed.get("classification"),
                    "next_action": routed.get("next_action"),
                    "status": candidate.get("status"),
                    "canonical_applied": False,
                    "canonical_write_allowed": False,
                })
            finally:
                conn.close()
        else:
            results.append(consume_accepted_candidate(
                cid, db_path=db_path, recovery_store=recovery_store, successor_store=successor_store,
            ))
    return {"processed": len(results), "results": results}


def list_post_approval_recovery(*, store_path: Path | None = None, status: str = "pending", limit: int = 20) -> dict[str, Any]:
    path = _recovery_store(store_path)
    data = _load_recovery(path)
    _refresh_recovery_leases(data)
    _save_recovery(path, data)
    items = [x for x in (data.get("items") or {}).values() if x.get("route") == GPT_NORMALIZATION_REQUIRED or x.get("classification") == GPT_NORMALIZATION_PENDING]
    if status != "all":
        items = [x for x in items if x.get("status") == status]
    items.sort(key=lambda x: x.get("created_at") or "")
    return {"count": len(items[:limit]), "items": items[:limit]}


def list_decision_only_successors(*, store_path: Path | None = None, status: str = "pending_preparation", limit: int = 20) -> dict[str, Any]:
    data = _load_successor(_successor_store(store_path))
    items = list((data.get("items") or {}).values())
    if status != "all":
        items = [x for x in items if x.get("status") == status]
    items.sort(key=lambda x: x.get("created_at") or "")
    return {"count": len(items[:limit]), "items": items[:limit]}


def _successor_execution_hash(execution: Mapping[str, Any]) -> str:
    return payload_hash({
        "target_type": execution.get("target_type"),
        "operation": execution.get("operation"),
        "payload": execution.get("payload"),
    })


def _deterministic_binding_executions(
    conn: sqlite3.Connection,
    parent: Mapping[str, Any],
) -> list[dict[str, Any]] | None:
    """Build todo.update executions when DECISION_ONLY bindings are fully frozen."""
    approved = effective_approved_payload(parent)
    bindings = approved.get("bindings")
    if not isinstance(bindings, list) or not bindings:
        original = _original_payload(parent)
        bindings = original.get("bindings") if isinstance(original.get("bindings"), list) else None
    if not isinstance(bindings, list) or not bindings:
        return None
    executions: list[dict[str, Any]] = []
    for binding in bindings:
        if not isinstance(binding, dict):
            return None
        todo_id = str(binding.get("todo_id") or "").strip()
        project_id = str(binding.get("project_id") or "").strip()
        if not todo_id or not project_id:
            return None
        if conn.execute("SELECT 1 FROM todo WHERE id = ?", (todo_id,)).fetchone() is None:
            return None
        if not _project_exists(conn, project_id):
            return None
        executions.append({
            "target_type": "todo",
            "operation": "update",
            "payload": {"id": todo_id, "project_id": project_id},
        })
    return executions or None


def _validate_successor_execution(
    conn: sqlite3.Connection,
    parent: Mapping[str, Any],
    execution: Mapping[str, Any],
) -> str | None:
    """Fail closed when successor execution drifts from the approved DECISION_ONLY direction."""
    try:
        _committer.validate_change_request(
            str(execution["target_type"]),
            str(execution["operation"]),
            dict(execution["payload"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        return str(exc)

    approved = effective_approved_payload(parent)
    original = _original_payload(parent)
    target = str(execution.get("target_type") or "")
    operation = str(execution.get("operation") or "")
    payload = execution.get("payload") if isinstance(execution.get("payload"), dict) else {}

    if approved.get("create_new_project") is True or original.get("create_new_project") is True:
        return "project creation remains human-defined"
    if target == "project" and operation == "create":
        return "project creation remains human-defined"

    bindings = approved.get("bindings") if isinstance(approved.get("bindings"), list) else original.get("bindings")
    if isinstance(bindings, list) and bindings:
        if target != "todo" or operation != "update":
            return "approved direction only authorizes todo project binding updates"
        todo_id = str(payload.get("id") or "").strip()
        project_id = str(payload.get("project_id") or "").strip()
        allowed = {
            (str(b.get("todo_id") or "").strip(), str(b.get("project_id") or "").strip())
            for b in bindings
            if isinstance(b, dict)
        }
        if (todo_id, project_id) not in allowed:
            return "todo binding does not match approved DECISION_ONLY mapping"

    for key in ("knowledge_id", "canonical_knowledge_id", "duplicate_knowledge_id"):
        parent_id = str(approved.get(key) or original.get(key) or "").strip()
        if not parent_id:
            continue
        if target == "knowledge":
            got = str(payload.get("id") or payload.get("knowledge_id") or "").strip()
            if got and got != parent_id:
                return f"successor knowledge id must preserve approved {key}"
    parent_project = str(approved.get("project_id") or original.get("project_id") or "").strip()
    if parent_project and target == "knowledge_project_link":
        if str(payload.get("project_id") or "").strip() != parent_project:
            return "successor project link must preserve approved project_id"
    return None


def _build_successor_review_brief(
    parent: Mapping[str, Any],
    execution: Mapping[str, Any],
    brief_override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    parent_brief = dict(_review_brief(parent))
    override = dict(brief_override or {})
    payload = execution.get("payload") if isinstance(execution.get("payload"), dict) else {}
    target = str(execution.get("target_type") or "")
    operation = str(execution.get("operation") or "")
    label = f"{target}.{operation}"
    current_state = "direction approved; no canonical write yet"
    proposed_state = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    brief = {
        "schema_version": str(parent_brief.get("schema_version") or "1"),
        "title": str(override.get("title") or parent_brief.get("title") or label),
        "situation": str(override.get("situation") or parent_brief.get("situation") or ""),
        "decision_question": str(
            override.get("decision_question")
            or f"Approve the exact EXECUTION_READY write for {label}?"
        ),
        "recommendation": str(
            override.get("recommendation")
            or parent_brief.get("recommendation")
            or "Review the frozen execution envelope before second approval."
        ),
        "approval_effect": str(
            override.get("approval_effect")
            or "Second approval authorizes exactly this frozen execution envelope through ReviewDecision -> ApprovedChange -> Committer. No write occurs from successor preparation alone."
        ),
        "review_mode": REVIEW_MODE_EXECUTION_READY,
        "affected_items": override.get("affected_items") or [{
            "label": label,
            "current_state": current_state,
            "proposed_state": proposed_state,
        }],
        "evidence_summary": override.get("evidence_summary") or list(parent_brief.get("evidence_summary") or []),
    }
    if parent_brief.get("attention_points"):
        brief["attention_points"] = list(parent_brief.get("attention_points") or [])
    if parent_brief.get("decision_options"):
        brief["decision_options"] = list(parent_brief.get("decision_options") or [])
    brief.update({k: v for k, v in override.items() if k not in brief or k in {"title", "situation", "decision_question", "recommendation", "approval_effect", "affected_items", "evidence_summary", "attention_points", "decision_options"}})
    brief["review_mode"] = REVIEW_MODE_EXECUTION_READY
    return brief


def _insert_successor_candidate(
    conn: sqlite3.Connection,
    parent: Mapping[str, Any],
    execution: Mapping[str, Any],
    review_brief: Mapping[str, Any],
) -> str:
    new_id = _uuid()
    parent_id = str(parent["id"])
    original = _original_payload(parent)
    proposed = {
        "original_payload": original,
        "execution": {
            "target_type": str(execution["target_type"]),
            "operation": str(execution["operation"]),
            "payload": dict(execution["payload"]),
        },
        "review_metadata": {
            "review_origin": "gpt_patrol_successor",
            "predecessor_id": parent_id,
            "predecessor_review_mode": REVIEW_MODE_DECISION_ONLY,
        },
    }
    refs = _source_ref_list(parent)
    lineage_ref = f"predecessor:{parent_id}"
    if lineage_ref not in refs:
        refs.append(lineage_ref)
    now = utc_now()
    conn.execute(
        """INSERT INTO candidate
           (id, analysis_run_id, candidate_type, source_refs, raw_excerpt, proposed_change,
            confidence, inference_level, recommended_action, analysis_model, status,
            created_at, review_brief_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ASK_USER', ?, 'pending', ?, ?)""",
        (
            new_id,
            parent.get("analysis_run_id"),
            parent.get("candidate_type"),
            json.dumps(refs, ensure_ascii=False),
            parent.get("raw_excerpt"),
            json.dumps(proposed, ensure_ascii=False),
            parent.get("confidence") or "HIGH",
            parent.get("inference_level") or "EXPLICIT",
            parent.get("analysis_model") or "gpt-patrol-successor",
            now,
            json.dumps(dict(review_brief), ensure_ascii=False),
        ),
    )
    return new_id


def prepare_decision_only_successor(
    candidate_id: str,
    execution: Mapping[str, Any] | None = None,
    review_brief: Mapping[str, Any] | None = None,
    *,
    db_path: str = PRODUCTION_DB,
    successor_store: Path | None = None,
) -> dict[str, Any]:
    """Create pending EXECUTION_READY successor Candidate(s) for a DECISION_ONLY parent.

    Does not create ApprovedChange, does not call Committer, and does not write Formal State.
    """
    path = _successor_store(successor_store)
    store = _load_successor(path)
    previous_db = _committer.DB_PATH
    _committer.DB_PATH = db_path
    conn = _connect(db_path)
    try:
        parent = _load_candidate(conn, candidate_id)
        if not parent:
            return {"error": "candidate_not_found", "candidate_id": candidate_id, "formal_state_bypass": 0}
        if parent.get("status") != "accepted":
            return {
                "error": "invalid_parent_status",
                "candidate_id": candidate_id,
                "status": parent.get("status"),
                "formal_state_bypass": 0,
            }
        routed = route_post_approval(conn, parent)
        if routed.get("route") != DECISION_ONLY_ACCEPTED:
            return {
                "error": "not_decision_only_accepted",
                "candidate_id": candidate_id,
                "route": routed.get("route"),
                "formal_state_bypass": 0,
            }
        if _explicit_review_mode(parent) != REVIEW_MODE_DECISION_ONLY:
            return {
                "error": "parent_not_decision_only",
                "candidate_id": candidate_id,
                "formal_state_bypass": 0,
            }

        store_item = (store.get("items") or {}).get(candidate_id) or {}
        if not store_item:
            store_item = enqueue_decision_only_successor(parent, routed, store_path=successor_store, db_path=db_path)
            store = _load_successor(path)

        executions: list[dict[str, Any]]
        if execution is not None:
            if not isinstance(execution, dict):
                return {"error": "execution_must_be_object", "candidate_id": candidate_id, "formal_state_bypass": 0}
            executions = [dict(execution)]
        else:
            derived = _deterministic_binding_executions(conn, parent)
            if not derived:
                return {
                    "error": "execution_required",
                    "candidate_id": candidate_id,
                    "reason": "DECISION_ONLY successor needs an explicit execution proposal or fully frozen todo bindings",
                    "formal_state_bypass": 0,
                }
            executions = derived

        created_ids: list[str] = []
        newly_created: list[str] = []
        existing_ids = list(store_item.get("successor_candidate_ids") or [])
        if store_item.get("successor_candidate_id") and store_item.get("successor_candidate_id") not in existing_ids:
            existing_ids.append(str(store_item["successor_candidate_id"]))

        for item_execution in executions:
            err = _validate_successor_execution(conn, parent, item_execution)
            if err:
                return {
                    "error": "FAILED_CLOSED",
                    "candidate_id": candidate_id,
                    "reason": err,
                    "formal_state_bypass": 0,
                }
            exec_hash = _successor_execution_hash(item_execution)
            matched_existing = None
            for sid in existing_ids:
                row = _load_candidate(conn, sid)
                if not row:
                    continue
                proposed = _proposed_change(row)
                existing_exec = proposed.get("execution") if isinstance(proposed.get("execution"), dict) else None
                if existing_exec and _successor_execution_hash(existing_exec) == exec_hash:
                    matched_existing = sid
                    break
            if matched_existing:
                created_ids.append(matched_existing)
                continue

            brief = _build_successor_review_brief(parent, item_execution, review_brief)
            sys_path = "/opt/personal-workspace/brain/review"
            if sys_path not in sys.path:
                sys.path.insert(0, sys_path)
            from review_brief_contract import validate_review_brief as _validate_review_brief

            valid, brief_err = _validate_review_brief(brief)
            if not valid:
                return {
                    "error": "REVIEW_BRIEF_INVALID",
                    "candidate_id": candidate_id,
                    "reason": brief_err,
                    "formal_state_bypass": 0,
                }
            new_id = _insert_successor_candidate(conn, parent, item_execution, brief)
            created_ids.append(new_id)
            newly_created.append(new_id)
            existing_ids.append(new_id)

        conn.commit()
        store = _load_successor(path)
        store_item = (store.get("items") or {}).get(candidate_id) or {}
        store_item.update({
            "status": "successor_created",
            "successor_candidate_id": created_ids[0] if len(created_ids) == 1 else None,
            "successor_candidate_ids": created_ids,
            "execution_hash": _successor_execution_hash(executions[0]),
            "prepared_at": utc_now(),
            "next_action": "user_review_successor",
            "formal_state_bypass": 0,
        })
        store.setdefault("items", {})[candidate_id] = store_item
        _save_successor(path, store)
        return {
            "candidate_id": candidate_id,
            "predecessor_id": candidate_id,
            "successor_candidate_ids": created_ids,
            "successor_candidate_id": created_ids[0] if len(created_ids) == 1 else None,
            "successor_status": "pending",
            "review_mode": REVIEW_MODE_EXECUTION_READY,
            "canonical_write_allowed": False,
            "idempotent": len(newly_created) == 0,
            "new_successor_count": len(newly_created),
            "formal_state_bypass": 0,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
        _committer.DB_PATH = previous_db


def claim_post_approval_recovery(
    candidate_id: str,
    *,
    worker_id: str = "chatgpt-patrol",
    store_path: Path | None = None,
) -> dict[str, Any]:
    path = _recovery_store(store_path)
    data = _load_recovery(path)
    _refresh_recovery_leases(data)
    item = (data.get("items") or {}).get(candidate_id)
    if not item:
        return {"error": "not_found", "candidate_id": candidate_id}
    if item.get("status") == "consumed":
        return {"error": "already_consumed", "candidate_id": candidate_id, "item": item}
    if item.get("status") == "claimed" and not _lease_expired(item) and item.get("claimed_by") not in {None, worker_id}:
        return {
            "error": "claim_held_by_other_worker",
            "candidate_id": candidate_id,
            "claimed_by": item.get("claimed_by"),
            "lease_expires_at": item.get("lease_expires_at"),
        }
    now = datetime.now(timezone.utc)
    item["status"] = "claimed"
    item["claimed_by"] = worker_id
    item["claimed_at"] = now.isoformat()
    item["lease_expires_at"] = datetime.fromtimestamp(now.timestamp() + CLAIM_LEASE_SECONDS, tz=timezone.utc).isoformat()
    data["items"][candidate_id] = item
    _save_recovery(path, data)
    return {"claimed": True, "item": item}


def _material_change(approved: Mapping[str, Any], execution: Mapping[str, Any]) -> str | None:
    """Reject GPT envelopes that change already-approved identity fields."""
    payload = execution.get("payload") if isinstance(execution.get("payload"), dict) else {}
    target = str(execution.get("target_type") or "")
    # Candidate proposals commonly wrap the actual execution in an outer
    # proposal object (title/evidence/recommended_action/execution).  Compare
    # identity fields with that inner payload; comparing against the proposal
    # title incorrectly rejects legitimate project-link updates.
    approved_execution = approved.get("execution") if isinstance(approved.get("execution"), dict) else {}
    approved_inner = approved_execution.get("payload") if isinstance(approved_execution.get("payload"), dict) else {}
    if target == "todo":
        approved_title = str(approved_inner.get("title") or approved.get("title") or "").strip()
        got = str(payload.get("title") or "").strip()
        if approved_title and got and got != approved_title:
            return "todo title differs from approved payload"
    if target == "knowledge":
        approved_title = str(approved_inner.get("title") or approved.get("title") or approved.get("target_title") or "").strip()
        got = str(payload.get("title") or "").strip()
        if approved_title and got and got != approved_title:
            return "knowledge title differs from approved payload"
    if target == "project":
        approved_id = str(approved_inner.get("project_id") or approved_inner.get("id") or approved.get("project_id") or approved.get("target_project_id") or approved.get("id") or "").strip()
        got = str(payload.get("id") or "").strip()
        if approved_id and got and got != approved_id:
            return "project id differs from approved payload"
    return None


def submit_post_approval_execution(
    candidate_id: str,
    execution: Mapping[str, Any],
    *,
    db_path: str = PRODUCTION_DB,
    recovery_store: Path | None = None,
    require_claim: bool = False,
) -> dict[str, Any]:
    if require_claim:
        item = (_load_recovery(_recovery_store(recovery_store)).get("items") or {}).get(candidate_id)
        if not item or item.get("status") != "claimed":
            return {
                "error": "GPT_PATROL_CLAIM_REQUIRED",
                "candidate_id": candidate_id,
                "reason": "claim the reviewed work item before submitting its normalized execution envelope",
            }
    conn = _connect(db_path)
    try:
        candidate = _load_candidate(conn, candidate_id)
        if not candidate:
            return {"error": "candidate_not_found", "candidate_id": candidate_id}
        approved = approved_payload(candidate)
        material = _material_change(approved, execution)
        if material:
            return {
                "error": "MATERIAL_CHANGE_REQUIRES_USER_DECISION",
                "candidate_id": candidate_id,
                "reason": material,
            }
        try:
            _committer.validate_change_request(
                str(execution["target_type"]),
                str(execution["operation"]),
                dict(execution["payload"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            return {"error": "FAILED_CLOSED", "candidate_id": candidate_id, "reason": str(exc)}
    finally:
        conn.close()
    # A prior deterministic attempt may have created a failed ApprovedChange
    # (for example, a stale target id in a legacy proposal).  Keep that audit
    # history intact and retry the newly normalized envelope under a stable
    # retry key instead of colliding with the failed row.
    approved = approved_payload(candidate)
    base_key = idempotency_key(candidate_id, approved)
    retry_key = None
    conn = _connect(db_path)
    try:
        prior = _find_existing_change(conn, base_key)
        if prior and prior["status"] not in {"committed", "pending"}:
            retry_key = f"{base_key}:retry:{payload_hash(dict(execution))[:24]}"
    finally:
        conn.close()
    result = consume_accepted_candidate(
        candidate_id,
        db_path=db_path,
        recovery_store=recovery_store,
        execution_override=execution,
        idempotency_key_override=retry_key,
    )
    path = _recovery_store(recovery_store)
    data = _load_recovery(path)
    if candidate_id in (data.get("items") or {}) and result.get("classification") in {COMMITTED, ALREADY_PRESENT}:
        data["items"][candidate_id]["status"] = "consumed"
        data["items"][candidate_id]["consumed_at"] = utc_now()
        data["items"][candidate_id]["result"] = {
            "change_id": result.get("change_id"),
            "commit_id": result.get("commit_id"),
        }
        _save_recovery(path, data)
    return result


def mcp_list_accepted_writes(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    items = list_accepted_write_intents(limit=int(params.get("limit") or 20))
    return {"count": len(items), "items": items, "formal_state_bypass": 0}


def mcp_list_decision_only_successors(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    out = list_decision_only_successors(
        status=str(params.get("status") or "pending_preparation"),
        limit=int(params.get("limit") or 20),
    )
    out["formal_state_bypass"] = 0
    return out


def mcp_prepare_successor(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    cid = str(params.get("candidate_id") or "")
    if not cid:
        return {"error": "candidate_id required", "formal_state_bypass": 0}
    execution = params.get("execution")
    if isinstance(execution, str):
        execution = _json_obj(execution)
    brief = params.get("review_brief")
    if isinstance(brief, str):
        brief = _json_obj(brief)
    out = prepare_decision_only_successor(
        cid,
        execution if isinstance(execution, dict) else None,
        brief if isinstance(brief, dict) else None,
    )
    if isinstance(out, dict) and "formal_state_bypass" not in out:
        out["formal_state_bypass"] = 0
    return out


def mcp_consume_accepted_writes(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    ids = params.get("candidate_ids")
    if isinstance(ids, str) and ids:
        ids = [ids]
    if ids is not None and not isinstance(ids, list):
        return {"error": "candidate_ids must be a list"}
    result = consume_accepted_writes(
        limit=int(params.get("limit") or 10),
        candidate_ids=list(ids) if ids else None,
        patrol_gate=True,
    )
    result["formal_state_bypass"] = 0
    return result


def mcp_list_post_approval_recovery(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    out = list_post_approval_recovery(
        status=str(params.get("status") or "pending"),
        limit=int(params.get("limit") or 20),
    )
    out["formal_state_bypass"] = 0
    return out


def mcp_claim_post_approval_recovery(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    cid = str(params.get("candidate_id") or "")
    if not cid:
        return {"error": "candidate_id required"}
    out = claim_post_approval_recovery(cid, worker_id=str(params.get("worker_id") or "chatgpt-patrol"))
    out["formal_state_bypass"] = 0
    return out


def mcp_submit_post_approval_execution(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    cid = str(params.get("candidate_id") or "")
    execution = params.get("execution")
    if isinstance(execution, str):
        execution = _json_obj(execution)
    if not cid or not isinstance(execution, dict):
        return {"error": "candidate_id and execution object required"}
    out = submit_post_approval_execution(cid, execution, require_claim=True)
    if isinstance(out, dict):
        out["formal_state_bypass"] = 0
    return out
