"""PAW Committer V1.1 - Deterministic ApprovedChange Executor.

R1.1 Corrections:
- Payload is FROZEN from ApprovedChange.payload only.
- Review transaction is atomic (ReviewDecision + ApprovedChange + Candidate status).
- Direct User Authorization uses approval_source=direct_user, candidate_id=NULL.
- rowcount guard on all update/complete/restore/archive.
- Knowledge executor follows revision mechanism.
- Data executor for metric_definition and metric_observation.

NON-LLM. NON-REASONING.
Input: persisted ApprovedChange ID only.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parent.parent.parent / "data" / "workspace.db"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


def _get_conn() -> sqlite3.Connection:
    import os
    db = os.environ.get("PAW_WORKSPACE_DB") or str(DB_PATH)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _payload_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


# ── Payload schemas ─────────────────────────────────────────────────────────
PAYLOAD_SCHEMAS: dict[str, dict[str, list[str]]] = {
    "pm_section": {"update": ["project_id", "section_id", "text", "source_version_id"]},
    "pm_undo": {"restore": ["change_id"]},
    "todo": {
        "create": ["title"],
        "update": ["id"],
        "complete": ["id"],
        "restore": ["id"],
        "archive": ["id"],
    },
    "knowledge": {
        "create": ["title"],
        "update": ["id"],
        "archive": ["id"],
    },
    "project": {
        "create": ["name"],
        "update": ["id"],
    },
    "data": {
        "create": ["name"],
        "update": ["id"],
        "revise": ["metric_definition_id"],
    },
    "knowledge_project_link": {
        "link": ["knowledge_id", "project_id"],
        "unlink": ["knowledge_id", "project_id"],
    },
    "project_merge": {
        "merge": ["keeper_project_id", "duplicate_project_ids"],
    },
}


def _validate_payload(target_type: str, operation: str, payload: dict) -> None:
    type_schemas = PAYLOAD_SCHEMAS.get(target_type)
    if type_schemas is None:
        raise ValueError(f"Unsupported target_type: {target_type}")
    required_fields = type_schemas.get(operation)
    if required_fields is None:
        raise ValueError(f"Unsupported operation '{operation}' for target_type '{target_type}'")
    for field in required_fields:
        if field not in payload:
            raise ValueError(f"Missing required field '{field}' in payload for {target_type}.{operation}")


def _validate_idempotency(conn: sqlite3.Connection, idempotency_key: str, change_id: str) -> None:
    row = conn.execute(
        "SELECT change_id, status FROM approved_change WHERE idempotency_key = ? AND change_id != ?",
        (idempotency_key, change_id)
    ).fetchone()
    if row and row["status"] == "committed":
        raise ValueError(f"Idempotency conflict: change_id {row['change_id']} with same key already committed")


def _validate_expected_version(conn: sqlite3.Connection, target_type: str, target_id: str | None, expected_version: str | None) -> None:
    if not expected_version or not target_id:
        return
    if target_type == "todo":
        row = conn.execute("SELECT updated_at FROM todo WHERE id = ?", (target_id,)).fetchone()
    elif target_type == "knowledge":
        row = conn.execute("SELECT updated_at FROM knowledge_page WHERE id = ?", (target_id,)).fetchone()
    elif target_type == "project":
        row = conn.execute("SELECT updated_at FROM project WHERE id = ?", (target_id,)).fetchone()
    elif target_type == "data":
        row = conn.execute("SELECT updated_at FROM metric_definition WHERE id = ?", (target_id,)).fetchone()
    else:
        return
    if row is None:
        raise ValueError(f"Target {target_type} with id {target_id} not found")
    if row[0] != expected_version:
        raise ValueError(f"Version conflict: expected {expected_version}, actual {row[0]}")


# ── Payload executors with rowcount guard ────────────────────────────────────

def _execute_todo_create(conn: sqlite3.Connection, payload: dict) -> str:
    todo_id = payload.get("id") or _uuid()
    now = _now_iso()
    conn.execute(
        """INSERT INTO todo (id, domain_id, project_id, title, description, status, priority,
           due_at, start_at, source_type, ai_generated, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (todo_id, payload.get("domain_id"), payload.get("project_id"), payload["title"],
         payload.get("description"), payload.get("status", "open"), payload.get("priority"),
         payload.get("due_at"), payload.get("start_at"), payload.get("source_type", "manual"),
         payload.get("ai_generated", 0), now, now)
    )
    return todo_id


def _execute_todo_update(conn: sqlite3.Connection, payload: dict) -> str:
    todo_id = payload["id"]
    now = _now_iso()
    fields = []
    values = []
    for key in ["title", "description", "status", "priority", "due_at", "start_at", "project_id"]:
        if key in payload:
            fields.append(f"{key} = ?")
            values.append(payload[key])
    if payload.get("status") == "completed":
        fields.append("completed_at = ?")
        values.append(now)
    fields.append("updated_at = ?")
    values.append(now)
    values.append(todo_id)
    cursor = conn.execute(f"UPDATE todo SET {', '.join(fields)} WHERE id = ?", values)
    if cursor.rowcount == 0:
        raise ValueError(f"NOT_FOUND: todo {todo_id} not found for update")
    if cursor.rowcount > 1:
        raise ValueError(f"INVARIANT_ERROR: update affected {cursor.rowcount} rows, expected 1")
    return todo_id


def _execute_todo_complete(conn: sqlite3.Connection, payload: dict) -> str:
    todo_id = payload["id"]
    now = _now_iso()
    cursor = conn.execute(
        "UPDATE todo SET status = 'completed', completed_at = ?, updated_at = ? WHERE id = ?",
        (now, now, todo_id)
    )
    if cursor.rowcount == 0:
        raise ValueError(f"NOT_FOUND: todo {todo_id} not found for complete")
    if cursor.rowcount > 1:
        raise ValueError(f"INVARIANT_ERROR: complete affected {cursor.rowcount} rows")
    return todo_id


def _execute_todo_restore(conn: sqlite3.Connection, payload: dict) -> str:
    todo_id = payload["id"]
    now = _now_iso()
    cursor = conn.execute(
        "UPDATE todo SET status = 'open', completed_at = NULL, updated_at = ? WHERE id = ?",
        (now, todo_id)
    )
    if cursor.rowcount == 0:
        raise ValueError(f"NOT_FOUND: todo {todo_id} not found for restore")
    if cursor.rowcount > 1:
        raise ValueError(f"INVARIANT_ERROR: restore affected {cursor.rowcount} rows")
    return todo_id


def _execute_todo_archive(conn: sqlite3.Connection, payload: dict) -> str:
    todo_id = payload["id"]
    now = _now_iso()
    cursor = conn.execute(
        "UPDATE todo SET status = 'archived', updated_at = ? WHERE id = ?",
        (now, todo_id)
    )
    if cursor.rowcount == 0:
        raise ValueError(f"NOT_FOUND: todo {todo_id} not found for archive")
    if cursor.rowcount > 1:
        raise ValueError(f"INVARIANT_ERROR: archive affected {cursor.rowcount} rows")
    return todo_id


def _normalize_knowledge_status(raw_status: Any) -> str:
    s = str(raw_status or "").strip().lower()
    if s in ("current", "active", "published", "正式", "发布", "已发布", "完成"):
        return "current"
    if s in ("needs_review", "pending", "待审", "待审阅", "待审核"):
        return "needs_review"
    if s in ("deprecated", "archived", "废弃", "归档"):
        return "deprecated"
    return "draft"


def _normalize_knowledge_type(raw_type: Any) -> str:
    t = str(raw_type or "").strip().lower()
    if t in ("domain", "业务域", "领域"):
        return "domain"
    if t in ("rule", "policy", "制度", "规范", "规则", "办法"):
        return "rule"
    if t in ("method", "sop", "方法", "流程", "手册"):
        return "method"
    if t in ("analysis", "report", "分析", "报告"):
        return "analysis"
    return "project"


def _execute_knowledge_create(conn: sqlite3.Connection, payload: dict) -> str:
    page_id = payload.get("id") or _uuid()
    now = _now_iso()
    base_slug = payload.get("slug") or payload["title"].lower().replace(" ", "-")[:50]
    slug = base_slug
    while conn.execute("SELECT 1 FROM knowledge_page WHERE slug = ?", (slug,)).fetchone():
        slug = f"{base_slug[:40]}-{_uuid()[:8]}"
    k_status = _normalize_knowledge_status(payload.get("status"))
    k_type = _normalize_knowledge_type(payload.get("knowledge_type"))
    conn.execute(
        """INSERT INTO knowledge_page
           (id, domain_id, title, slug, knowledge_type, one_line_summary,
            applicability, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (page_id, payload.get("domain_id"), payload["title"], slug,
         k_type, payload.get("one_line_summary", ""),
         payload.get("applicability"), k_status, now, now)
    )
    # Create initial revision
    rev_id = _uuid()
    conn.execute(
        """INSERT INTO knowledge_revision
           (id, knowledge_id, revision_no, content_json, change_summary, compiled_at, created_at)
           VALUES (?, ?, 1, ?, ?, ?, ?)""",
        (rev_id, page_id, json.dumps(payload.get("content", {})),
         payload.get("change_summary", "Initial creation"), now, now)
    )
    return page_id


def _execute_knowledge_update(conn: sqlite3.Connection, payload: dict) -> str:
    page_id = payload["id"]
    now = _now_iso()
    fields = []
    values = []
    for key in ["title", "knowledge_type", "one_line_summary", "applicability", "status"]:
        if key in payload:
            val = payload[key]
            if key == "status":
                val = _normalize_knowledge_status(val)
            elif key == "knowledge_type":
                val = _normalize_knowledge_type(val)
            fields.append(f"{key} = ?")
            values.append(val)
    fields.append("updated_at = ?")
    values.append(now)
    values.append(page_id)
    cursor = conn.execute(f"UPDATE knowledge_page SET {', '.join(fields)} WHERE id = ?", values)
    if cursor.rowcount == 0:
        raise ValueError(f"NOT_FOUND: knowledge_page {page_id} not found for update")
    if cursor.rowcount > 1:
        raise ValueError(f"INVARIANT_ERROR: update affected {cursor.rowcount} rows")
    # Create new revision if content provided
    if "content" in payload:
        max_rev = conn.execute(
            "SELECT MAX(revision_no) FROM knowledge_revision WHERE knowledge_id = ?",
            (page_id,)
        ).fetchone()[0] or 0
        rev_id = _uuid()
        conn.execute(
            """INSERT INTO knowledge_revision
               (id, knowledge_id, revision_no, content_json, change_summary, compiled_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (rev_id, page_id, max_rev + 1, json.dumps(payload["content"]),
             payload.get("change_summary", "Update"), now, now)
        )
    return page_id


def _execute_knowledge_archive(conn: sqlite3.Connection, payload: dict) -> str:
    page_id = payload["id"]
    now = _now_iso()
    cursor = conn.execute(
        "UPDATE knowledge_page SET status = 'deprecated', updated_at = ? WHERE id = ?",
        (now, page_id)
    )
    if cursor.rowcount == 0:
        raise ValueError(f"NOT_FOUND: knowledge_page {page_id} not found for archive")
    if cursor.rowcount > 1:
        raise ValueError(f"INVARIANT_ERROR: archive affected {cursor.rowcount} rows")
    return page_id




def _execute_project_merge(conn: sqlite3.Connection, payload: dict) -> str:
    """Deterministic project merge: fold duplicate projects into the keeper.

    Migrates knowledge links, todos, relations and outcomes from the duplicate
    project(s) to the keeper project, then archives the duplicates. All within
    the caller's transaction; bypass remains 0 (only reachable via approved change).
    """
    keeper_id = payload.get("keeper_project_id") or ""
    duplicate_ids = payload.get("duplicate_project_ids") or []
    if not keeper_id or not duplicate_ids:
        raise ValueError("project_merge payload requires keeper_project_id and duplicate_project_ids")

    keeper = conn.execute("SELECT id, name, status FROM project WHERE id = ?", (keeper_id,)).fetchone()
    if not keeper:
        raise ValueError(f"Keeper project {keeper_id} not found")
    if keeper["status"] == "merged":
        raise ValueError(f"Keeper project {keeper_id} was itself merged into another project")

    now = _now_iso()
    for dup_id in duplicate_ids:
        if dup_id == keeper_id:
            continue
        dup = conn.execute("SELECT id, name, status FROM project WHERE id = ?", (dup_id,)).fetchone()
        if not dup or dup["status"] == "merged":
            continue

        # 1. Migrate knowledge links: avoid UNIQUE constraint on (knowledge_id, project_id)
        conn.execute(
            "UPDATE OR IGNORE knowledge_project_link SET project_id = ? WHERE project_id = ?",
            (keeper_id, dup_id),
        )
        conn.execute(
            "DELETE FROM knowledge_project_link WHERE project_id = ?",
            (dup_id,),
        )
        # 2. Migrate todos
        conn.execute(
            "UPDATE todo SET project_id = ? WHERE project_id = ?",
            (keeper_id, dup_id),
        )
        # 3. Migrate decisions, pending_items, ideas, materials
        conn.execute(
            "UPDATE decision SET project_id = ? WHERE project_id = ?",
            (keeper_id, dup_id),
        )
        conn.execute(
            "UPDATE pending_item SET project_id = ? WHERE project_id = ?",
            (keeper_id, dup_id),
        )
        conn.execute(
            "UPDATE idea SET project_id = ? WHERE project_id = ?",
            (keeper_id, dup_id),
        )
        conn.execute(
            "UPDATE material SET project_id = ? WHERE project_id = ?",
            (keeper_id, dup_id),
        )
        # 4. Migrate relations (from_project_id / to_project_id)
        conn.execute(
            "UPDATE project_relation SET from_project_id = ? WHERE from_project_id = ?",
            (keeper_id, dup_id),
        )
        conn.execute(
            "UPDATE project_relation SET to_project_id = ? WHERE to_project_id = ?",
            (keeper_id, dup_id),
        )
        conn.execute(
            "DELETE FROM project_relation WHERE from_project_id = ? AND to_project_id = ?",
            (keeper_id, keeper_id),
        )
        # 5. Migrate project outcomes safely (PK: project_id)
        keeper_outcome = conn.execute("SELECT 1 FROM project_outcome WHERE project_id = ?", (keeper_id,)).fetchone()
        if keeper_outcome:
            conn.execute("DELETE FROM project_outcome WHERE project_id = ?", (dup_id,))
        else:
            conn.execute("UPDATE project_outcome SET project_id = ? WHERE project_id = ?", (keeper_id, dup_id))

        # 6. Migrate project explainers and handbooks safely (PK: project_id)
        keeper_explainer = conn.execute("SELECT 1 FROM project_explainer WHERE project_id = ?", (keeper_id,)).fetchone()
        if keeper_explainer:
            conn.execute("DELETE FROM project_explainer WHERE project_id = ?", (dup_id,))
        else:
            conn.execute("UPDATE project_explainer SET project_id = ? WHERE project_id = ?", (keeper_id, dup_id))

        keeper_handbook = conn.execute("SELECT 1 FROM project_handbook WHERE project_id = ?", (keeper_id,)).fetchone()
        if keeper_handbook:
            conn.execute("DELETE FROM project_handbook WHERE project_id = ?", (dup_id,))
        else:
            conn.execute("UPDATE project_handbook SET project_id = ? WHERE project_id = ?", (keeper_id, dup_id))

        # 7. Archive the duplicate project (soft delete, keeps audit trail)
        conn.execute(
            "UPDATE project SET status = 'merged', updated_at = ?, description = COALESCE(description, '') || ? WHERE id = ?",
            (now, f" [已合并至 {keeper['name']} @ {now}]", dup_id),
        )

    return keeper_id

def _execute_project_create(conn: sqlite3.Connection, payload: dict) -> str:
    # Approving a "create" for a project that already exists under the same
    # name (e.g. a proposal filed before convergence existed) updates that
    # project instead of inserting a duplicate.
    from backend.runtime_core.project_convergence import INACTIVE_STATUSES, normalize_project_name
    key = normalize_project_name(payload.get("name", ""))
    if key and not payload.get("id"):
        for row in conn.execute("SELECT id, name, status FROM project ORDER BY created_at, id"):
            if (row[2] or "active") not in INACTIVE_STATUSES and normalize_project_name(row[1]) == key:
                return _execute_project_update(conn, {**payload, "id": row[0]})

    project_id = payload.get("id") or _uuid()
    now = _now_iso()
    domain_id = payload.get("domain_id") or "domain-default"
    # Ensure domain exists
    if conn.execute("SELECT 1 FROM domain WHERE id = ?", (domain_id,)).fetchone() is None:
        conn.execute(
            """INSERT OR IGNORE INTO domain (id, slug, name, description, is_enabled, sort_order, created_at, updated_at)
               VALUES (?, ?, ?, ?, 1, 0, ?, ?)""",
            (domain_id, "default", "默认业务域", "系统自动初始化业务域", now, now)
        )
    conn.execute(
        """INSERT INTO project
           (id, domain_id, name, description, status, stage, priority, due_date,
            next_action_summary, latest_progress_summary, privacy_level, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            project_id,
            domain_id,
            payload["name"],
            payload.get("description", ""),
            payload.get("status", "active"),
            payload.get("stage", "executing"),
            payload.get("priority", "medium"),
            payload.get("due_date"),
            payload.get("next_action_summary"),
            payload.get("latest_progress_summary"),
            payload.get("privacy_level", "normal"),
            now,
            now,
        ),
    )
    return project_id


def _execute_project_update(conn: sqlite3.Connection, payload: dict) -> str:
    # A Project Handbook is an additive, machine-owned read projection.  It
    # travels through the existing project.update governance envelope, but it
    # must not mutate the business Project row itself.
    if payload.get("project_handbook") is not None:
        return _execute_project_handbook_update(conn, payload)

    project_id = payload.get("id")
    if not project_id and payload.get("name"):
        row = conn.execute("SELECT id FROM project WHERE name = ?", (payload["name"],)).fetchone()
        if not row:
            row = conn.execute("SELECT id FROM project WHERE name LIKE ?", (f"%{payload['name'][:8]}%",)).fetchone()
        if row:
            project_id = row[0]
            payload["id"] = project_id
    if not project_id:
        row = conn.execute("SELECT id FROM project LIMIT 1").fetchone()
        if row:
            project_id = row[0]
            payload["id"] = project_id
        else:
            raise ValueError("project.update requires valid project id or name")

    now = _now_iso()
    existing = conn.execute("SELECT name FROM project WHERE id = ?", (project_id,)).fetchone()
    if existing is None:
        raise ValueError(f"NOT_FOUND: project {project_id} not found for update")
    project_name = str(existing[0] or "").strip()
    if "latest_progress_summary" in payload:
        progress = str(payload.get("latest_progress_summary") or "").strip()
        structured = any(payload.get(key) is not None for key in ("project_outcome", "project_relation", "knowledge_project_link"))
        other_context = any(str(payload.get(key) or "").strip() for key in ("description", "next_action_summary", "stage", "status"))
        if not progress:
            payload["latest_progress_summary"] = str(payload.get("note") or payload.get("description") or f"{project_name} 治理更新")
        elif progress == project_name and not structured and not other_context:
            payload["latest_progress_summary"] = f"{project_name} 状态与文档已同步更新"
    fields = []
    values = []
    for key in ["name", "description", "status", "stage", "priority", "due_date",
                "next_action_summary", "latest_progress_summary"]:
        if key in payload:
            fields.append(f"{key} = ?")
            values.append(payload[key])
    fields.append("updated_at = ?")
    values.append(now)
    values.append(project_id)
    cursor = conn.execute(f"UPDATE project SET {', '.join(fields)} WHERE id = ?", values)
    if cursor.rowcount == 0:
        raise ValueError(f"NOT_FOUND: project {project_id} not found for update")
    if cursor.rowcount > 1:
        raise ValueError(f"INVARIANT_ERROR: update affected {cursor.rowcount} rows")
    # PROJECT-first outcome/relation records are still canonical Project
    # writes: they are carried by the existing ApprovedChange -> Committer
    # transaction, never written by a side channel.
    outcome = payload.get("project_outcome")
    if outcome is not None:
        if not isinstance(outcome, dict) or outcome.get("project_id") != project_id:
            raise ValueError("project_outcome identity mismatch")
        now = _now_iso()
        encoded = {
            "project_id": project_id,
            "schema_version": outcome.get("schema_version", "paw-project-outcome/1"),
            "current_status": outcome.get("current_status"),
            "phase": outcome.get("phase"),
            "executive_summary": outcome.get("executive_summary", ""),
            "objectives_json": json.dumps(outcome.get("objectives", []), ensure_ascii=False),
            "scope_json": json.dumps(outcome.get("scope", {}), ensure_ascii=False),
            "latest_progress_json": json.dumps(outcome.get("latest_progress", []), ensure_ascii=False),
            "completed_milestones_json": json.dumps(outcome.get("completed_milestones", []), ensure_ascii=False),
            "open_items_json": json.dumps(outcome.get("open_items", []), ensure_ascii=False),
            "key_decisions_json": json.dumps(outcome.get("key_decisions", []), ensure_ascii=False),
            "current_effective_rules_json": json.dumps(outcome.get("current_effective_rules", []), ensure_ascii=False),
            "risks_json": json.dumps(outcome.get("risks", []), ensure_ascii=False),
            "blockers_json": json.dumps(outcome.get("blockers", []), ensure_ascii=False),
            "ambiguities_json": json.dumps(outcome.get("ambiguities", []), ensure_ascii=False),
            "key_metrics_json": json.dumps(outcome.get("key_metrics", []), ensure_ascii=False),
            "canonical_knowledge_refs_json": json.dumps(outcome.get("canonical_knowledge_refs", []), ensure_ascii=False),
            "source_evidence_refs_json": json.dumps(outcome.get("source_evidence_refs", []), ensure_ascii=False),
            "related_projects_json": json.dumps(outcome.get("related_projects", []), ensure_ascii=False),
            "unresolved_items_json": json.dumps(outcome.get("unresolved_items", {}), ensure_ascii=False),
            "synthesized_at": outcome.get("synthesized_at") or now,
            "input_watermark": outcome.get("input_watermark", ""),
            "synthesis_revision": int(outcome.get("synthesis_revision") or 1),
            "evidence_fingerprint": outcome.get("evidence_fingerprint", ""),
            "evidence_sufficient": 1 if outcome.get("evidence_sufficient") else 0,
            "quality_gate": outcome.get("quality_gate", "PASS"),
            "created_at": now,
            "updated_at": now,
        }
        conn.execute(
            """INSERT INTO project_outcome (
                project_id,schema_version,current_status,phase,executive_summary,
                objectives_json,scope_json,latest_progress_json,completed_milestones_json,
                open_items_json,key_decisions_json,current_effective_rules_json,risks_json,
                blockers_json,ambiguities_json,key_metrics_json,canonical_knowledge_refs_json,
                source_evidence_refs_json,related_projects_json,unresolved_items_json,
                synthesized_at,input_watermark,synthesis_revision,evidence_fingerprint,
                evidence_sufficient,quality_gate,created_at,updated_at)
               VALUES (:project_id,:schema_version,:current_status,:phase,:executive_summary,
                :objectives_json,:scope_json,:latest_progress_json,:completed_milestones_json,
                :open_items_json,:key_decisions_json,:current_effective_rules_json,:risks_json,
                :blockers_json,:ambiguities_json,:key_metrics_json,:canonical_knowledge_refs_json,
                :source_evidence_refs_json,:related_projects_json,:unresolved_items_json,
                :synthesized_at,:input_watermark,:synthesis_revision,:evidence_fingerprint,
                :evidence_sufficient,:quality_gate,:created_at,:updated_at)
               ON CONFLICT(project_id) DO UPDATE SET
                schema_version=excluded.schema_version,current_status=excluded.current_status,
                phase=excluded.phase,executive_summary=excluded.executive_summary,
                objectives_json=excluded.objectives_json,scope_json=excluded.scope_json,
                latest_progress_json=excluded.latest_progress_json,
                completed_milestones_json=excluded.completed_milestones_json,
                open_items_json=excluded.open_items_json,key_decisions_json=excluded.key_decisions_json,
                current_effective_rules_json=excluded.current_effective_rules_json,
                risks_json=excluded.risks_json,blockers_json=excluded.blockers_json,
                ambiguities_json=excluded.ambiguities_json,key_metrics_json=excluded.key_metrics_json,
                canonical_knowledge_refs_json=excluded.canonical_knowledge_refs_json,
                source_evidence_refs_json=excluded.source_evidence_refs_json,
                related_projects_json=excluded.related_projects_json,
                unresolved_items_json=excluded.unresolved_items_json,synthesized_at=excluded.synthesized_at,
                input_watermark=excluded.input_watermark,synthesis_revision=excluded.synthesis_revision,
                evidence_fingerprint=excluded.evidence_fingerprint,evidence_sufficient=excluded.evidence_sufficient,
                quality_gate=excluded.quality_gate,updated_at=excluded.updated_at""", encoded)
    relation = payload.get("project_relation")
    if relation is not None:
        if not isinstance(relation, dict) or relation.get("from_project_id") != project_id:
            raise ValueError("project_relation identity mismatch")
        conn.execute(
            """INSERT INTO project_relation (
                relation_id,schema_version,from_project_id,to_project_id,relation_type,summary,
                evidence_refs_json,confidence,evidence_sufficiency,temporal_state,revision,
                input_watermark,idempotency_key,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(idempotency_key) DO UPDATE SET
                relation_id=excluded.relation_id,summary=excluded.summary,
                evidence_refs_json=excluded.evidence_refs_json,confidence=excluded.confidence,
                evidence_sufficiency=excluded.evidence_sufficiency,temporal_state=excluded.temporal_state,
                revision=excluded.revision,input_watermark=excluded.input_watermark,
                updated_at=excluded.updated_at""",
            (relation.get("relation_id"), relation.get("schema_version", "paw-project-relation/1"),
             relation.get("from_project_id"), relation.get("to_project_id"), relation.get("relation_type"),
             relation.get("summary", ""), json.dumps(relation.get("evidence_refs", []), ensure_ascii=False),
             relation.get("confidence", "MEDIUM"), relation.get("evidence_sufficiency", "SUFFICIENT"),
             relation.get("temporal_state", "current"), int(relation.get("revision") or 1),
             relation.get("input_watermark", ""), relation.get("idempotency_key"),
             relation.get("created_at") or now, relation.get("updated_at") or now),
        )
    project_link = payload.get("knowledge_project_link")
    if project_link is not None:
        if not isinstance(project_link, dict) or project_link.get("project_id") != project_id:
            raise ValueError("knowledge_project_link identity mismatch")
        _execute_knowledge_project_link(conn, project_link)
    return project_id


def _execute_project_handbook_update(conn: sqlite3.Connection, payload: dict) -> str:
    """Persist the already validated Handbook projection.

    This is deliberately a narrow executor behind the normal Committer.  The
    Handbook builder owns semantic validation; the Committer owns the durable
    write and commit record.  No Project/Outcome/Knowledge/Source row is
    created or rewritten here.
    """
    project_id = payload["id"]
    handbook = payload["project_handbook"]
    if not isinstance(handbook, dict) or handbook.get("project_id") != project_id:
        raise ValueError("project_handbook identity mismatch")
    required = (
        "schema_version", "sections", "section_metadata", "generated_at",
        "revision", "input_watermark", "evidence_fingerprint",
        "quality_gate", "evidence_sufficient", "baseline_input_hash",
    )
    missing = [key for key in required if key not in handbook]
    if missing:
        raise ValueError("project_handbook missing required fields: " + ",".join(missing))
    if conn.execute("SELECT 1 FROM project WHERE id = ?", (project_id,)).fetchone() is None:
        raise ValueError(f"NOT_FOUND: project {project_id} not found for handbook update")
    now = _now_iso()
    existing = conn.execute(
        "SELECT created_at FROM project_handbook WHERE project_id = ?", (project_id,)
    ).fetchone()
    conn.execute(
        """INSERT INTO project_handbook
           (project_id,schema_version,sections_json,section_metadata_json,
            generated_at,revision,input_watermark,evidence_fingerprint,
            quality_gate,evidence_sufficient,baseline_input_hash,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(project_id) DO UPDATE SET
            schema_version=excluded.schema_version,
            sections_json=excluded.sections_json,
            section_metadata_json=excluded.section_metadata_json,
            generated_at=excluded.generated_at,
            revision=excluded.revision,
            input_watermark=excluded.input_watermark,
            evidence_fingerprint=excluded.evidence_fingerprint,
            quality_gate=excluded.quality_gate,
            evidence_sufficient=excluded.evidence_sufficient,
            baseline_input_hash=excluded.baseline_input_hash,
            updated_at=excluded.updated_at""",
        (
            project_id, handbook["schema_version"],
            json.dumps(handbook["sections"], ensure_ascii=False, sort_keys=True),
            json.dumps(handbook["section_metadata"], ensure_ascii=False, sort_keys=True),
            handbook["generated_at"], int(handbook["revision"]),
            handbook["input_watermark"], handbook["evidence_fingerprint"],
            handbook["quality_gate"], int(bool(handbook["evidence_sufficient"])),
            handbook["baseline_input_hash"], existing[0] if existing else now, now,
        ),
    )
    return project_id


def _execute_data_create(conn: sqlite3.Connection, payload: dict) -> str:
    """Create metric_definition."""
    def_id = payload.get("id") or _uuid()
    now = _now_iso()
    conn.execute(
        """INSERT INTO metric_definition
           (id, project_id, name, description, unit, direction, target_value, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (def_id, payload.get("project_id"), payload["name"], payload.get("description"),
         payload.get("unit"), payload.get("direction"), payload.get("target_value"),
         payload.get("status", "active"), now, now)
    )
    return def_id


def _execute_data_update(conn: sqlite3.Connection, payload: dict) -> str:
    """Update metric_definition."""
    def_id = payload["id"]
    now = _now_iso()
    fields = []
    values = []
    for key in ["name", "description", "unit", "direction", "target_value", "status"]:
        if key in payload:
            fields.append(f"{key} = ?")
            values.append(payload[key])
    fields.append("updated_at = ?")
    values.append(now)
    values.append(def_id)
    cursor = conn.execute(f"UPDATE metric_definition SET {', '.join(fields)} WHERE id = ?", values)
    if cursor.rowcount == 0:
        raise ValueError(f"NOT_FOUND: metric_definition {def_id} not found")
    if cursor.rowcount > 1:
        raise ValueError(f"INVARIANT_ERROR: update affected {cursor.rowcount} rows")
    return def_id


def _execute_data_revise(conn: sqlite3.Connection, payload: dict) -> str:
    """Create metric_observation for a metric_definition."""
    obs_id = _uuid()
    now = _now_iso()
    conn.execute(
        """INSERT INTO metric_observation
           (id, metric_definition_id, project_id, value, observed_at, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (obs_id, payload["metric_definition_id"], payload.get("project_id"),
         payload["value"], payload.get("observed_at", now), payload.get("notes"), now)
    )
    return obs_id


def _execute_knowledge_project_link(conn: sqlite3.Connection, payload: dict) -> str:
    """Link a knowledge page to a project (deterministic UPSERT)."""
    knowledge_id = payload["knowledge_id"]
    project_id = payload["project_id"]
    relation_type = payload.get("relation_type", "related")
    if relation_type not in ("primary", "related"):
        raise ValueError(f"Invalid relation_type: {relation_type}")
    # Verify knowledge page exists
    kp = conn.execute("SELECT id FROM knowledge_page WHERE id = ?", (knowledge_id,)).fetchone()
    if kp is None:
        raise ValueError(f"NOT_FOUND: knowledge_page {knowledge_id} not found")
    # Verify project exists
    proj = conn.execute("SELECT id FROM project WHERE id = ?", (project_id,)).fetchone()
    if proj is None:
        raise ValueError(f"NOT_FOUND: project {project_id} not found")
    now = _now_iso()
    link_id = _uuid()
    columns = {row[1] for row in conn.execute("PRAGMA table_info(knowledge_project_link)").fetchall()}
    # Conflict-safe UPSERT: try insert, on conflict update relation_type
    try:
        if "id" in columns:
            conn.execute(
                """INSERT INTO knowledge_project_link (id, knowledge_id, project_id, relation_type, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (link_id, knowledge_id, project_id, relation_type, now, now)
            )
        else:
            conn.execute(
                """INSERT INTO knowledge_project_link (knowledge_id, project_id, relation_type, created_at)
                   VALUES (?, ?, ?, ?)""",
                (knowledge_id, project_id, relation_type, now)
            )
    except sqlite3.IntegrityError:
        # Link already exists — update relation_type (idempotent)
        conn.execute(
            f"UPDATE knowledge_project_link SET relation_type = ?{', updated_at = ?' if 'updated_at' in columns else ''} WHERE knowledge_id = ? AND project_id = ?",
            (relation_type, now, knowledge_id, project_id) if "updated_at" in columns else (relation_type, knowledge_id, project_id)
        )
        if "id" in columns:
            row = conn.execute(
                "SELECT id FROM knowledge_project_link WHERE knowledge_id = ? AND project_id = ?",
                (knowledge_id, project_id)
            ).fetchone()
            link_id = row["id"] if row else link_id
        else:
            link_id = f"knowledge-project-link:{knowledge_id}:{project_id}"
    return link_id


def _execute_knowledge_project_unlink(conn: sqlite3.Connection, payload: dict) -> str:
    """Unlink a knowledge page from a project (deterministic with rowcount guard)."""
    knowledge_id = payload["knowledge_id"]
    project_id = payload["project_id"]
    cursor = conn.execute(
        "DELETE FROM knowledge_project_link WHERE knowledge_id = ? AND project_id = ?",
        (knowledge_id, project_id)
    )
    if cursor.rowcount == 0:
        raise ValueError(f"NOT_FOUND: link between knowledge {knowledge_id} and project {project_id} not found")
    if cursor.rowcount > 1:
        raise ValueError(f"INVARIANT_ERROR: unlink affected {cursor.rowcount} rows, expected 1")
    return f"{knowledge_id}:{project_id}"


EXECUTORS = {
    ("todo", "create"): _execute_todo_create,
    ("todo", "update"): _execute_todo_update,
    ("todo", "complete"): _execute_todo_complete,
    ("todo", "restore"): _execute_todo_restore,
    ("todo", "archive"): _execute_todo_archive,
    ("knowledge", "create"): _execute_knowledge_create,
    ("knowledge", "update"): _execute_knowledge_update,
    ("knowledge", "archive"): _execute_knowledge_archive,
    ("project", "create"): _execute_project_create,
    ("project", "update"): _execute_project_update,
    ("project_merge", "merge"): _execute_project_merge,
    ("data", "create"): _execute_data_create,
    ("data", "update"): _execute_data_update,
    ("data", "revise"): _execute_data_revise,
    ("knowledge_project_link", "link"): _execute_knowledge_project_link,
    ("knowledge_project_link", "unlink"): _execute_knowledge_project_unlink,
}


def commit_approved_change(change_id: str) -> dict:
    """Execute an ApprovedChange deterministically.

    R1.1: Payload is FROZEN from ApprovedChange.payload only.
    Does NOT re-read ReviewDecision.reviewed_payload or Candidate payload.
    """
    conn = _get_conn()
    ac = None
    savepoint_started = False
    try:
        # Reserve the writer BEFORE reading approval/status/version. Otherwise
        # two consumers can both read pending and create two different objects.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("SAVEPOINT commit_attempt")
        savepoint_started = True
        ac = conn.execute("SELECT * FROM approved_change WHERE change_id = ?", (change_id,)).fetchone()
        if ac is None:
            return {"commit_id": None, "result": "failure", "target_id": None,
                    "error": f"ApprovedChange not found: {change_id}"}

        if ac["status"] == "committed":
            existing = conn.execute(
                "SELECT * FROM commit_record WHERE change_id = ? AND result = 'success' "
                "ORDER BY committed_at DESC, id DESC LIMIT 1", (change_id,)).fetchone()
            if existing:
                return {"commit_id": existing["id"], "result": existing["result"],
                        "target_id": None, "message": "Already committed"}
            # A corrupt receipt is not permission to execute the business write again.
            return {"commit_id": None, "result": "failure", "target_id": None,
                    "error": "INVARIANT_ERROR: committed change has no success receipt"}

        # ── Validate approval ──
        rd = conn.execute("SELECT * FROM review_decision WHERE id = ?", (ac["approval_ref"],)).fetchone()
        if rd is None:
            raise ValueError(f"ReviewDecision not found: {ac['approval_ref']}")
        if rd["decision"] not in ("approve", "edit_and_approve"):
            raise ValueError(f"ReviewDecision is not an approval: {rd['decision']}")

        # ── R1.1: Use FROZEN payload from ApprovedChange only ──
        payload = json.loads(ac["payload"])
        if not isinstance(payload, dict):
            raise ValueError("ApprovedChange payload must be a dict")
        auth_hash = rd["authorization_payload_hash"]
        if not auth_hash or auth_hash != _payload_hash(payload):
            raise ValueError("ApprovedChange authorization payload hash missing or mismatched")

        # ── Validate schema ──
        _validate_payload(ac["target_type"], ac["operation"], payload)

        # ── Validate expected version ──
        target_id = payload.get("id")
        _validate_expected_version(conn, ac["target_type"], target_id, ac["expected_target_version"])

        # ── Validate idempotency ──
        _validate_idempotency(conn, ac["idempotency_key"], change_id)

        # ── Transaction: execute payload write ──
        executor = EXECUTORS.get((ac["target_type"], ac["operation"]))
        if executor is None and ac['target_type'] not in ('pm_section', 'pm_undo'):
            raise ValueError(f"No executor for {ac['target_type']}.{ac['operation']}")

        transaction_id = _uuid()
        from backend.pm import governance as pm_governance
        if ac['target_type'] in ('pm_section', 'pm_undo'):
            committed_target_id = pm_governance.execute(conn, ac, payload)
        else:
            before = pm_governance.project_row(conn, payload.get('id')) if ac['target_type'] == 'project' else None
            committed_target_id = executor(conn, payload)
            pm_governance.record_project_change(conn, ac, payload, committed_target_id, before)

        conn.execute("UPDATE approved_change SET status = 'committed' WHERE change_id = ?", (change_id,))

        commit_id = _uuid()
        now = _now_iso()
        conn.execute(
            "INSERT INTO commit_record (id, change_id, transaction_id, result, committed_at, error) VALUES (?, ?, ?, 'success', ?, NULL)",
            (commit_id, change_id, transaction_id, now)
        )

        audit_id = _uuid()
        conn.execute(
            """INSERT INTO audit_event (id, event_type, actor, target_type, target_id, change_id, commit_id, details, created_at)
               VALUES (?, 'commit', 'committer', ?, ?, ?, ?, ?, ?)""",
            (audit_id, ac["target_type"], committed_target_id, change_id, commit_id,
             json.dumps({"operation": ac["operation"], "transaction_id": transaction_id}), now)
        )

        conn.commit()
        return {"commit_id": commit_id, "result": "success",
                "target_id": committed_target_id, "transaction_id": transaction_id}

    except Exception as e:
        error_msg = str(e) if isinstance(e, ValueError) else f"Unexpected error: {e}"
        if ac is None or not savepoint_started:
            conn.rollback()
            return {"commit_id": None, "result": "failure", "target_id": None,
                    "error": error_msg}
        # Undo partial entity changes without releasing the writer reservation.
        # A full rollback here permits a concurrent success before our failure
        # status update, which could then incorrectly overwrite that success.
        conn.execute("ROLLBACK TO SAVEPOINT commit_attempt")
        conn.execute("RELEASE SAVEPOINT commit_attempt")
        is_conflict = isinstance(e, ValueError) and (
            "conflict" in error_msg.lower() or "version" in error_msg.lower())
        status = "conflict" if is_conflict else "failed"
        conn.execute("UPDATE approved_change SET status = ? WHERE change_id = ?", (status, change_id))
        commit_id = _uuid()
        now = _now_iso()
        conn.execute(
            "INSERT INTO commit_record (id, change_id, transaction_id, result, committed_at, error) VALUES (?, ?, NULL, ?, ?, ?)",
            (commit_id, change_id, "conflict" if is_conflict else "failure", now, error_msg)
        )
        audit_id = _uuid()
        conn.execute(
            """INSERT INTO audit_event (id, event_type, actor, target_type, target_id, change_id, commit_id, details, created_at)
               VALUES (?, 'commit_failed', 'committer', NULL, NULL, ?, ?, ?, ?)""",
            (audit_id, change_id, commit_id, json.dumps({"error": error_msg}), now)
        )
        conn.commit()
        return {"commit_id": commit_id, "result": "conflict" if is_conflict else "failure",
                "target_id": None, "error": error_msg}

    finally:
        conn.close()


def create_review_decision_atomic(
    candidate_id: str | None,
    decision: str,
    reviewer: str = "chatgpt",
    review_channel: str = "chatgpt_explicit_user",
    approval_source: str = "candidate_review",
    original_payload: dict | None = None,
    reviewed_payload: dict | None = None,
    review_notes: str | None = None,
    target_type: str | None = None,
    operation: str | None = None,
    source_refs: list[str] | None = None,
    expected_target_version: str | None = None,
    create_approved_change: bool = True,
    idempotency_key: str | None = None,
) -> tuple[str, str | None]:
    """Atomically create ReviewDecision + ApprovedChange (if approved) + update Candidate status.

    R1.1: Review transaction is atomic. All or nothing.
    R1.1: For edit_and_approve, reviewed_payload is frozen into ApprovedChange.payload.
    R1.2: When create_approved_change=False and decision is approve/edit_and_approve,
          only ReviewDecision is created — no ApprovedChange, no target_type/operation.
          This supports DECISION_ONLY review mode.

    Returns: (review_decision_id, approved_change_id or None)
    """
    # Direction-only mode: reject target_type/operation if provided
    if not create_approved_change and decision in ("approve", "edit_and_approve"):
        if target_type is not None or operation is not None:
            raise ValueError("DIRECTION_ONLY_MODE_REJECTS_TARGET_TYPE_OPERATION")
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")

        rd_id = _uuid()
        now = _now_iso()

        # Determine final payload
        if decision == "edit_and_approve" and reviewed_payload is not None:
            final_payload = reviewed_payload
        elif original_payload is not None:
            final_payload = original_payload
        else:
            final_payload = {}

        auth_hash = _payload_hash(final_payload) if final_payload else None

        conn.execute(
            """INSERT INTO review_decision
               (id, candidate_id, decision, reviewer, review_channel, approval_source,
                original_payload, reviewed_payload, authorization_payload_hash, review_notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rd_id, candidate_id, decision, reviewer, review_channel, approval_source,
             json.dumps(original_payload) if original_payload else None,
             json.dumps(reviewed_payload) if reviewed_payload else None,
             auth_hash, review_notes, now)
        )

        change_id = None
        if decision in ("approve", "edit_and_approve") and create_approved_change:
            if not target_type or not operation:
                raise ValueError("target_type and operation required for approval decisions")

            # Pre-persistence validation — ensure all ApprovedChange are schema-valid
            validate_change_request(target_type, operation, final_payload, source_refs)

            change_id = _uuid()
            idempotency_key = idempotency_key or f"{target_type}:{operation}:{rd_id}"

            conn.execute(
                """INSERT INTO approved_change
                   (change_id, target_type, operation, payload, approval_ref,
                    source_refs, expected_target_version, idempotency_key, created_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (change_id, target_type, operation, json.dumps(final_payload), rd_id,
                 json.dumps(source_refs) if source_refs else None,
                 expected_target_version, idempotency_key, now)
            )

        # Update candidate status if real candidate
        if candidate_id and candidate_id != "direct-user-authorization":
            new_status = "accepted" if decision in ("approve", "edit_and_approve") else \
                         "rejected" if decision == "reject" else "pending"
            conn.execute(
                "UPDATE candidate SET status = ?, reviewed_at = ?, reviewed_by = ? WHERE id = ?",
                (new_status, now, reviewer, candidate_id)
            )

        conn.commit()
        return rd_id, change_id

    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


def validate_change_request(
    target_type: str,
    operation: str,
    payload: dict,
    source_refs: list[str] | None = None,
) -> None:
    """Public deterministic validator for change requests.

    Validates BEFORE any DB write:
    - target_type must exist in PAYLOAD_SCHEMAS
    - operation must belong to target_type
    - payload must be dict
    - source_refs must be list if provided
    - payload fields validated via _validate_payload()
    - knowledge_project_link relation_type enum if present

    Raises ValueError on validation failure.
    No LLM, no inference.
    """
    if not isinstance(payload, dict):
        raise ValueError(f"payload must be a dict, got {type(payload).__name__}")
    if source_refs is not None and not isinstance(source_refs, list):
        raise ValueError(f"source_refs must be a list if provided, got {type(source_refs).__name__}")
    # Validate target_type and operation via existing schema
    _validate_payload(target_type, operation, payload)
    # Additional validation for knowledge_project_link relation_type
    if target_type == "knowledge_project_link" and operation == "link":
        relation_type = payload.get("relation_type", "related")
        if relation_type not in ("primary", "related"):
            raise ValueError(f"Invalid relation_type: {relation_type}")


def create_direct_user_approved_change(
    target_type: str,
    operation: str,
    payload: dict,
    source_refs: list[str] | None = None,
) -> tuple[str, str]:
    """Create ApprovedChange via Direct User Authorization.

    R1.1: candidate_id=NULL, approval_source=direct_user.
    Server-side fixed: actor=chatgpt, channel=chatgpt_explicit_user.
    R1.3: Pre-persistence validation via validate_change_request().
    """
    # Pre-persistence validation — zero DB write on failure
    validate_change_request(target_type, operation, payload, source_refs)

    rd_id, change_id = create_review_decision_atomic(
        candidate_id=None,
        decision="approve",
        reviewer="chatgpt",
        review_channel="chatgpt_explicit_user",
        approval_source="direct_user",
        original_payload=payload,
        reviewed_payload=payload,
        review_notes="Direct user authorization in trusted ChatGPT session",
        target_type=target_type,
        operation=operation,
        source_refs=source_refs,
    )
    return rd_id, change_id


def get_commit_record(change_id: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM commit_record WHERE change_id = ? ORDER BY committed_at DESC LIMIT 1",
            (change_id,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)
    finally:
        conn.close()
