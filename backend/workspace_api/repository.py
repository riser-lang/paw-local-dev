from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
from pathlib import Path
from datetime import datetime, timezone, date
from typing import Any

from .db import DB_PATH, connect
from .time_utils import business_date, business_today, date_window_end, parse_date, parse_month
from .current_context_policy import (
    is_current_business_project,
    is_current_confirmed_knowledge,
    knowledge_authority_state,
)
from .project_handbook import read_project_handbook, read_project_explainer
# TD-003: bounded deterministic content projection for the Knowledge list.
from .knowledge_summary_projection import project_summary_content

# Runtime module root comes from the explicit PAW Runtime Path Contract
# (/etc/paw-runtime-paths.env).  PAW_KSHEET_WORKER_ROOT is still honoured
# FIRST for backward compatibility: paw-actions sets it to the divergent
# fork /opt/personal-workspace/brain_runtime and must keep resolving to it.
# There is deliberately NO fallback to a development tree.
_KSHEET_WORKER = (
    os.environ.get("PAW_KSHEET_WORKER_ROOT")
    or os.environ.get("PAW_RUNTIME_MODULE_ROOT")
    or str(Path(__file__).resolve().parent.parent / "runtime")
)
if _KSHEET_WORKER not in sys.path:
    sys.path.insert(0, _KSHEET_WORKER)


class KnowledgeContentError(ValueError):
    """A safe, machine-readable validation failure for Knowledge content."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def uid() -> str:
    return str(uuid.uuid4())

def row_dict(row):
    return dict(row) if row is not None else None


def _project_capture_rows(conn: sqlite3.Connection, daily_id: str) -> list[dict[str, Any]]:
    from paw_capture_lifecycle import enrich_capture_projection

    rows = conn.execute(
        "SELECT * FROM capture WHERE daily_id=? ORDER BY captured_at ASC, id ASC",
        (daily_id,),
    ).fetchall()
    return [enrich_capture_projection(conn, dict(row)) for row in rows]

def get_or_create_daily(day: str | None = None) -> dict[str, Any]:
    day = (parse_date(day) if day else business_today()).isoformat()
    with connect() as conn:
        row = conn.execute("SELECT * FROM daily WHERE date=?", (day,)).fetchone()
        if row:
            return dict(row)
        ts = now_iso()
        did = uid()
        conn.execute(
            "INSERT INTO daily(id,date,title,sync_status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (did, day, day, "local", ts, ts),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM daily WHERE id=?", (did,)).fetchone())

def create_capture(raw_text: str, captured_at: str | None, source: str) -> dict[str, Any]:
    """Persist exact raw Capture, then register it for the next GPT Patrol.

    processed_at stays NULL. This path must not invoke an LLM, Candidate,
    Todo/Knowledge/Project write, or review flow. Queue write failure must
    not roll back the saved Capture; processed_at IS NULL remains recoverable.
    """
    ts = now_iso()
    cid = uid()
    captured_at = captured_at or ts
    daily = get_or_create_daily(business_date(captured_at).isoformat())
    with connect() as conn:
        conn.execute(
            "INSERT INTO capture("
            "id,daily_id,raw_text,captured_at,source,user_edited,sync_status,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            (cid, daily["id"], raw_text, captured_at, source, 0, "local", ts, ts),
        )
        conn.commit()
        row = dict(conn.execute("SELECT * FROM capture WHERE id=?", (cid,)).fetchone())
    try:
        from .capture_patrol_enqueue import best_effort_enqueue_saved_capture
        best_effort_enqueue_saved_capture(row["id"], db_path=str(DB_PATH))
    except Exception:
        pass
    return row

def unprocessed_captures(limit: int = 100) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM capture WHERE processed_at IS NULL "
            "ORDER BY captured_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

def mark_capture_processed(capture_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM capture WHERE id=?", (capture_id,)).fetchone()
        if row is None:
            return None
        already_processed = row["processed_at"] is not None
        if not already_processed:
            ts = now_iso()
            conn.execute(
                "UPDATE capture SET processed_at=?, updated_at=? "
                "WHERE id=? AND processed_at IS NULL",
                (ts, ts, capture_id),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM capture WHERE id=?", (capture_id,)).fetchone()
        return {"capture": dict(row), "already_processed": already_processed}

def capture_for_daily_sync(capture_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT c.*, d.date, d.wps_document_id FROM capture c "
            "JOIN daily d ON d.id=c.daily_id WHERE c.id=?",
            (capture_id,),
        ).fetchone()
        return dict(row) if row is not None else None

def mark_capture_synced(capture_id: str, document_id: str) -> dict[str, Any] | None:
    ts = now_iso()
    with connect() as conn:
        row = conn.execute(
            "SELECT id,daily_id FROM capture WHERE id=?", (capture_id,)
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE daily SET wps_document_id=?, sync_status='synced', "
            "last_synced_at=?, updated_at=? WHERE id=?",
            (document_id, ts, ts, row["daily_id"]),
        )
        conn.execute(
            "UPDATE capture SET sync_status='synced', wps_anchor=?, updated_at=? WHERE id=?",
            (f"daily:{row['daily_id']}:capture:{capture_id}", ts, capture_id),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM capture WHERE id=?", (capture_id,)).fetchone())


def mark_capture_wps_unavailable(capture_id: str) -> dict[str, Any] | None:
    """Record that the canonical Daily sync succeeded but the optional WPS replica
    could not be written. The main Capture->Daily flow must NOT fail because of
    WPS; this only marks the WPS replica state as unavailable for later retry.
    """
    ts = now_iso()
    with connect() as conn:
        row = conn.execute(
            "SELECT id,daily_id FROM capture WHERE id=?", (capture_id,)
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE daily SET sync_status='wps_unavailable', updated_at=? WHERE id=?",
            (ts, row["daily_id"]),
        )
        conn.execute(
            "UPDATE capture SET sync_status='wps_unavailable', updated_at=? WHERE id=?",
            (ts, capture_id),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM capture WHERE id=?", (capture_id,)).fetchone())

def create_todo_in_transaction(con, data: dict[str, Any]) -> dict[str, Any]:
    """Canonical, connection-owned Todo INSERT.

    Accepts an EXISTING sqlite connection; does NOT connect / commit / rollback.
    Executes the single canonical Todo INSERT semantics and returns the row dict.

    Safety: source_capture_id is only written when it truly exists in the
    capture table; otherwise coerced to NULL (verified safe logic).
    project_id is written as-is (canonical writer formal policy).
    """
    ts = now_iso()
    tid = uid()
    source_capture_id = data.get("source_capture_id")
    if source_capture_id:
        ref_check = con.execute(
            "SELECT 1 FROM capture WHERE id = ?", (source_capture_id,)
        ).fetchone()
        if not ref_check:
            source_capture_id = None
    con.execute(
        "INSERT INTO todo("
        "id,project_id,title,description,status,priority,due_at,source_type,"
        "source_capture_id,ai_generated,sync_status,created_at,updated_at"
        ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            tid, data.get("project_id"), data["title"], data.get("description"),
            data.get("status") or "open", data.get("priority"), data.get("due_at"),
            data.get("source_type") or "manual", source_capture_id,
            data.get("ai_generated", 0), data.get("sync_status") or "local", ts, ts
        ),
    )
    actor = "ai_committer" if data.get("ai_generated") else "user"
    event_type = "committer_create_todo" if data.get("ai_generated") else "user_create_todo"
    con.execute(
        """INSERT INTO audit_event (id, event_type, actor, target_type, target_id, change_id, commit_id, details, created_at)
           VALUES (?, ?, ?, 'todo', ?, NULL, NULL, ?, ?)""",
        (
            uid(),
            event_type,
            actor,
            tid,
            json.dumps({"title": data.get("title"), "priority": data.get("priority"), "source_type": data.get("source_type") or "manual"}, ensure_ascii=False),
            ts,
        ),
    )
    row = con.execute("SELECT * FROM todo WHERE id=?", (tid,)).fetchone()
    return dict(row) if row is not None else {"id": tid}


def create_todo(data: dict[str, Any]) -> dict[str, Any]:
    """Create a Todo in the Canonical Workspace (own transaction)."""
    with connect() as conn:
        return create_todo_in_transaction(conn, data)

def list_todos(
    status: str | None = None,
    limit: int = 200,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    project_id: str | None = None,
    undated: bool = False,
    overdue: bool = False,
) -> list[dict[str, Any]]:
    """List Todo projections without changing their stored business state."""
    clauses = []
    args: list[Any] = []
    if status == "active":
        clauses.append("todo.status NOT IN ('done','cancelled')")
    elif status and status != "all":
        clauses.append("todo.status=?")
        args.append(status)
    if from_date:
        start = parse_date(from_date, field="from")
        # Todo.due_at may be a date-only ISO value (YYYY-MM-DD) or an
        # ISO-8601 timestamp. Comparing the raw strings against a timestamp
        # boundary drops date-only values, so bucket by the canonical first
        # ten characters for both representations.
        clauses.append("substr(todo.due_at, 1, 10) >= ?")
        args.append(start.isoformat())
    if to_date:
        end = date_window_end(parse_date(to_date, field="to"))
        clauses.append("substr(todo.due_at, 1, 10) < ?")
        args.append(end.isoformat())
    if undated:
        clauses.append("todo.due_at IS NULL")
    if overdue:
        clauses.append("substr(todo.due_at, 1, 10) < ?")
        args.append(business_today().isoformat())
        if status is None:
            clauses.append("todo.status NOT IN ('done','cancelled')")
    if project_id:
        clauses.append("todo.project_id=?")
        args.append(project_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    args.append(limit)
    with connect() as conn:
        rows = conn.execute(
            "SELECT todo.*, project.name AS project_name "
            "FROM todo LEFT JOIN project ON project.id=todo.project_id "
            f"{where} "
            "ORDER BY CASE WHEN todo.due_at IS NULL THEN 1 ELSE 0 END, "
            "todo.due_at ASC, todo.created_at DESC LIMIT ?",
            args,
        ).fetchall()
        return [dict(r) for r in rows]

def update_todo(todo_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {"title", "description", "status", "priority", "due_at"}
    if not changes or set(changes) - allowed:
        raise ValueError("Todo update contains no supported fields")
    # Completion is an explicit user action.  Keep its audit timestamp in
    # lock-step with the status transition, while making repeated same-state
    # requests true no-ops for the completion timestamp (and all provenance
    # fields, which are never in ``allowed``).
    ts = now_iso()
    with connect() as conn:
        current = conn.execute("SELECT status, completed_at FROM todo WHERE id=?", (todo_id,)).fetchone()
        if current is None:
            return None
        normalized = dict(changes)
        requested_status = normalized.get("status")
        if requested_status is not None and requested_status != current["status"]:
            if requested_status == "done":
                normalized["completed_at"] = ts
            elif requested_status == "open":
                normalized["completed_at"] = None
        assignments = [f"{field}=?" for field in normalized]
        conn.execute(
            f"UPDATE todo SET {', '.join(assignments)}, updated_at=? WHERE id=?",
            (*normalized.values(), ts, todo_id),
        )
        conn.execute(
            """INSERT INTO audit_event (id, event_type, actor, target_type, target_id, change_id, commit_id, details, created_at)
               VALUES (?, 'user_update_todo', 'user', 'todo', ?, NULL, NULL, ?, ?)""",
            (uid(), todo_id, json.dumps({"changes": normalized}, ensure_ascii=False), ts),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM todo WHERE id=?", (todo_id,)).fetchone())

def delete_todo(todo_id: str) -> dict[str, Any] | None:
    ts = now_iso()
    with connect() as conn:
        row = conn.execute("SELECT * FROM todo WHERE id=?", (todo_id,)).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM todo WHERE id=?", (todo_id,))
        conn.execute(
            """INSERT INTO audit_event (id, event_type, actor, target_type, target_id, change_id, commit_id, details, created_at)
               VALUES (?, 'user_delete_todo', 'user', 'todo', ?, NULL, NULL, ?, ?)""",
            (uid(), todo_id, json.dumps({"deleted_todo": dict(row)}, ensure_ascii=False), ts),
        )
        conn.commit()
        return dict(row)

def create_project(data: dict[str, Any]) -> dict[str, Any]:
    ts = now_iso()
    pid = uid()
    with connect() as conn:
        conn.execute(
            "INSERT INTO project("
            "id,name,description,status,priority,due_date,privacy_level,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            (
                pid, data["name"], data.get("description"), data.get("status") or "planning",
                data.get("priority"), data.get("due_date"), "normal", ts, ts
            ),
        )
        conn.execute(
            """INSERT INTO audit_event (id, event_type, actor, target_type, target_id, change_id, commit_id, details, created_at)
               VALUES (?, 'user_create_project', 'user', 'project', ?, NULL, NULL, ?, ?)""",
            (uid(), pid, json.dumps({"name": data["name"], "status": data.get("status") or "planning"}, ensure_ascii=False), ts),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM project WHERE id=?", (pid,)).fetchone())

def _lightweight_explainer(one: Any, current: Any, issue: Any, nxt: Any) -> dict[str, Any] | None:
    """Project the four list-card fields. Never invents text from other columns."""
    fields = {
        "one_sentence": one if isinstance(one, str) and one.strip() else None,
        "current_state": current if isinstance(current, str) and current.strip() else None,
        "key_open_issue": issue if isinstance(issue, str) and issue.strip() else None,
        "next_step": nxt if isinstance(nxt, str) and nxt.strip() else None,
    }
    if not any(fields.values()):
        return None
    return fields


def list_projects(limit: int = 200) -> list[dict[str, Any]]:
    """List Projects with a one-shot lightweight explainer projection.

    The four explainer fields are read from authored
    ``project_handbook.sections_json.project_explainer`` in the same SELECT
    (LEFT JOIN, not N+1).  A sidecar ``project_explainer`` row is only a
    fallback.  Other Project columns are never concatenated into copy.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT project.*, ("
            "SELECT COUNT(*) FROM todo WHERE todo.project_id=project.id "
            "AND todo.status NOT IN ('done','cancelled')"
            ") AS pending_todo_count, "
            "json_extract(ph.sections_json, '$.project_explainer.one_sentence') AS _ex_one_sentence, "
            "json_extract(ph.sections_json, '$.project_explainer.current_state') AS _ex_current_state, "
            "json_extract(ph.sections_json, '$.project_explainer.key_open_issue') AS _ex_key_open_issue, "
            "json_extract(ph.sections_json, '$.project_explainer.next_step') AS _ex_next_step "
            "FROM project LEFT JOIN project_handbook ph ON ph.project_id = project.id "
            "ORDER BY project.updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            explainer = _lightweight_explainer(
                item.pop("_ex_one_sentence", None),
                item.pop("_ex_current_state", None),
                item.pop("_ex_key_open_issue", None),
                item.pop("_ex_next_step", None),
            )
            item["explainer"] = explainer
            items.append(item)
        missing = [item["id"] for item in items if item.get("explainer") is None]
        if missing:
            try:
                placeholders = ",".join("?" for _ in missing)
                sidecar = conn.execute(
                    f"SELECT project_id, explainer_json FROM project_explainer WHERE project_id IN ({placeholders})",
                    missing,
                ).fetchall()
            except sqlite3.OperationalError as exc:
                if "no such table: project_explainer" in str(exc).lower():
                    sidecar = []
                else:
                    raise
            by_id = {}
            for raw_row in sidecar:
                raw = json.loads(raw_row["explainer_json"] or "{}")
                projected = _lightweight_explainer(
                    raw.get("one_sentence"), raw.get("current_state"),
                    raw.get("key_open_issue"), raw.get("next_step"),
                )
                if projected:
                    by_id[raw_row["project_id"]] = projected
            for item in items:
                if item.get("explainer") is None and item["id"] in by_id:
                    item["explainer"] = by_id[item["id"]]
                elif item.get("explainer") is None:
                    desc = (item.get("description") or "").strip()
                    stage = (item.get("stage") or "").strip()
                    progress = (item.get("latest_progress_summary") or "").strip()
                    nxt = (item.get("next_action_summary") or "").strip()
                    first_sent = re.split(r"[。\n\r;；!！?？]", desc)[0].strip() if desc else ""
                    one_s = first_sent[:60] if first_sent else f"推进{item.get('name', '工作项目')}核心目标"
                    curr_s = progress or (f"阶段推进中 · {stage}" if stage else "进行中")
                    open_iss = "按计划推进中，暂无阻碍项"
                    next_s = nxt or "推进当前阶段核心交付物"
                    item["explainer"] = {
                        "one_sentence": one_s,
                        "current_state": curr_s,
                        "key_open_issue": open_iss,
                        "next_step": next_s,
                    }
        return items

def project_context(project_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        project = conn.execute("SELECT * FROM project WHERE id=?", (project_id,)).fetchone()
        if not project:
            return None
        todos = conn.execute(
            "SELECT * FROM todo WHERE project_id=? ORDER BY created_at DESC LIMIT 100",
            (project_id,),
        ).fetchall()
        decisions = conn.execute(
            "SELECT * FROM decision WHERE project_id=? ORDER BY created_at DESC LIMIT 100",
            (project_id,),
        ).fetchall()
        pending = conn.execute(
            "SELECT * FROM pending_item WHERE project_id=? ORDER BY created_at DESC LIMIT 100",
            (project_id,),
        ).fetchall()
        knowledge = conn.execute(
            "SELECT kp.*, kpl.relation_type, "
            "(SELECT MAX(revision_no) FROM knowledge_revision kr "
            " WHERE kr.knowledge_id=kp.id) AS latest_revision_no "
            "FROM knowledge_page kp "
            "JOIN knowledge_project_link kpl ON kpl.knowledge_id=kp.id "
            "WHERE kpl.project_id=? ORDER BY kp.updated_at DESC",
            (project_id,),
        ).fetchall()
        sources = conn.execute(
            "SELECT w.id, w.title, w.category, w.wps_document_id, "
            "COALESCE((SELECT ksl2.source_role FROM knowledge_source_link ksl2 "
            "JOIN knowledge_project_link kpl2 ON kpl2.knowledge_id=ksl2.knowledge_id "
            "WHERE kpl2.project_id=? AND ksl2.source_page_id=w.id "
            "ORDER BY CASE ksl2.source_role WHEN 'primary' THEN 0 WHEN 'data' THEN 1 "
            "WHEN 'supporting' THEN 2 WHEN 'historical' THEN 3 ELSE 4 END LIMIT 1), 'supporting') AS source_role, "
            "w.source_origin, w.canonical_state, w.source_kind, w.source_path "
            "FROM knowledge_project_link kpl "
            "JOIN knowledge_source_link ksl ON ksl.knowledge_id=kpl.knowledge_id "
            "JOIN wiki_page w ON w.id=ksl.source_page_id "
            "WHERE kpl.project_id=? "
            "GROUP BY w.id "
            "ORDER BY CASE w.source_origin WHEN 'formal_business' THEN 0 "
            "WHEN 'office_sync' THEN 1 WHEN 'legacy_ai_index' THEN 2 ELSE 3 END, w.title",
            (project_id, project_id),
        ).fetchall()
        # P1 Source Gate: a Source reaches the default context only through
        # CURRENT_CONFIRMED Knowledge.  Knowledge that the admission gate
        # rejected must not smuggle its Sources back in.  Nothing is deleted -
        # the complete list stays available as sources_all.
        admitted_knowledge_ids = [str(x["id"]) for x in knowledge if is_current_confirmed_knowledge(x)]
        if admitted_knowledge_ids:
            placeholders = ",".join("?" for _ in admitted_knowledge_ids)
            admitted_sources = conn.execute(
                "SELECT w.id, w.title, w.category, w.wps_document_id, "
                "COALESCE((SELECT ksl2.source_role FROM knowledge_source_link ksl2 "
                "JOIN knowledge_project_link kpl2 ON kpl2.knowledge_id=ksl2.knowledge_id "
                "WHERE kpl2.project_id=? AND ksl2.source_page_id=w.id "
                "ORDER BY CASE ksl2.source_role WHEN 'primary' THEN 0 WHEN 'data' THEN 1 "
                "WHEN 'supporting' THEN 2 WHEN 'historical' THEN 3 ELSE 4 END LIMIT 1), 'supporting') AS source_role, "
                "w.source_origin, w.canonical_state, w.source_kind, w.source_path "
                "FROM knowledge_project_link kpl "
                "JOIN knowledge_source_link ksl ON ksl.knowledge_id=kpl.knowledge_id "
                "JOIN wiki_page w ON w.id=ksl.source_page_id "
                f"WHERE kpl.project_id=? AND kpl.knowledge_id IN ({placeholders}) "
                "GROUP BY w.id "
                "ORDER BY CASE w.source_origin WHEN 'formal_business' THEN 0 "
                "WHEN 'office_sync' THEN 1 WHEN 'legacy_ai_index' THEN 2 ELSE 3 END, w.title",
                (project_id, project_id, *admitted_knowledge_ids),
            ).fetchall()
        else:
            # Fail closed: without an admitted Knowledge row no Source may enter
            # the default context (this is also what a schema without the
            # Knowledge Confirmation Gate produces).
            admitted_sources = []
        draft_summary = list_luna_drafts(project_id=project_id, limit=20)
        project_row = dict(project)
        brain_projection = _project_brain_projection(project_id, project_name=project_row.get("name"))
        handbook_projection = read_project_handbook(project_id)
        explainer_projection = read_project_explainer(project_id)
        try:
            from paw_project_soul_bootstrap import project_soul_read_model
            project_soul_status = project_soul_read_model(conn, project_id)
        except Exception:
            # Older/partially migrated databases remain fully readable; the
            # readiness block is additive and never gates the existing context.
            project_soul_status = {
                "soul_schema_version": "paw-project-soul/1",
                "soul_quality": "PASS_PARTIAL",
                "missing_or_partial_concepts": [],
                "bootstrap_state": "UNKNOWN",
                "formal_state_bypass": 0,
            }
        return {
            "project": project_row,
            # P0-5: additive marker so callers can gate archived / non-business
            # Projects without changing the existing management read path.
            "is_current_business_project": is_current_business_project(project_row),
            "todos": [dict(x) for x in todos],
            "decisions": [dict(x) for x in decisions],
            "pending": [dict(x) for x in pending],
            # P0-2 Current-State Admission Policy: the default context carries
            # only current + confirmed-latest Knowledge.  The complete list
            # stays available as knowledge_all so management and historical
            # readers keep working, and nothing is deleted.
            "knowledge": [dict(x) for x in knowledge if is_current_confirmed_knowledge(x)],
            "knowledge_all": [dict(x) for x in knowledge],
            "knowledge_provisional_count": sum(
                1 for x in knowledge if not is_current_confirmed_knowledge(x)
            ),
            # P1 Source Gate: `sources` carries only Sources reachable through
            # CURRENT_CONFIRMED Knowledge; `sources_all` preserves the previous
            # complete aggregation so management / historical readers keep
            # working and nothing is deleted.
            "sources": [dict(x) for x in admitted_sources],
            "sources_all": [dict(x) for x in sources],
            "source_admitted_count": len(admitted_sources),
            "source_provisional_count": len(sources) - len(admitted_sources),
            "source_excluded_count": len(sources) - len(admitted_sources),
            "drafts": draft_summary.get("items", []),
            "draft_count": draft_summary.get("total", 0),
            "draft_authority_level": "draft",
            # Additive Project Handbook projection.  Existing context fields
            # above remain unchanged for older clients.
            **(handbook_projection or {}),
            **({"project_explainer": explainer_projection["explainer"], "explainer": explainer_projection["explainer"]} if explainer_projection and explainer_projection.get("explainer") else {}),
            "project_soul_status": project_soul_status,
            "soul_schema_version": project_soul_status.get("soul_schema_version"),
            "soul_quality": project_soul_status.get("soul_quality"),
            "missing_or_partial_concepts": project_soul_status.get("missing_or_partial_concepts", []),
            "bootstrap_state": project_soul_status.get("bootstrap_state"),
            **brain_projection,
        }

def today_view() -> dict[str, Any]:
    day = business_today().isoformat()
    with connect() as conn:
        daily = conn.execute("SELECT * FROM daily WHERE date=?", (day,)).fetchone()
        captures = _project_capture_rows(conn, daily["id"]) if daily else []
        todos = [
            dict(x) for x in conn.execute(
                "SELECT * FROM todo WHERE status NOT IN ('done','cancelled') "
                "ORDER BY CASE WHEN due_at IS NULL THEN 1 ELSE 0 END, due_at ASC, created_at DESC "
                "LIMIT 100"
            ).fetchall()
        ]
        pending_review_count = 0
        try:
            r = conn.execute("SELECT COUNT(*) AS c FROM candidate WHERE status = 'pending'").fetchone()
            if r:
                pending_review_count = int(r["c"])
        except Exception:
            pass
        return {
            "date": day,
            "daily": row_dict(daily),
            "captures": captures,
            "todos": todos,
            "pending_review_count": pending_review_count,
        }


def daily_view(day: str | None = None) -> dict[str, Any]:
    """Read a Daily and its captures without creating an empty Daily row."""
    selected = parse_date(day, field="date") if day else business_today()
    selected_text = selected.isoformat()
    with connect() as conn:
        daily = conn.execute("SELECT * FROM daily WHERE date=?", (selected_text,)).fetchone()
        captures = _project_capture_rows(conn, daily["id"]) if daily else []
        return {
            "date": selected_text,
            "daily": row_dict(daily),
            "captures": captures,
            "read_only": True,
        }


def daily_calendar(month: str) -> dict[str, Any]:
    start, end = parse_month(month)
    with connect() as conn:
        rows = conn.execute(
            "SELECT d.date, COUNT(c.id) AS capture_count "
            "FROM daily d JOIN capture c ON c.daily_id=d.id "
            "WHERE d.date>=? AND d.date<? GROUP BY d.date ORDER BY d.date",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    return {"month": month, "days": {str(row["date"]): int(row["capture_count"]) for row in rows}}


def _capture_snippet(raw_text: str, query: str, radius: int = 100) -> str:
    folded_text = raw_text.casefold()
    folded_query = query.casefold()
    index = folded_text.find(folded_query)
    if index < 0:
        return raw_text[: radius * 2].strip()
    start = max(0, index - radius)
    end = min(len(raw_text), index + len(query) + radius)
    prefix = "…" if start else ""
    suffix = "…" if end < len(raw_text) else ""
    return f"{prefix}{raw_text[start:end].strip()}{suffix}"


def search_captures(
    query: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    keyword = query.strip()
    if not keyword:
        raise ValueError("q must not be empty")
    if len(keyword) > 200:
        raise ValueError("q is too long")
    clauses = ["instr(lower(c.raw_text), lower(?)) > 0"]
    base_args: list[Any] = [keyword]
    if from_date:
        clauses.append("d.date>=?")
        base_args.append(parse_date(from_date, field="from").isoformat())
    if to_date:
        clauses.append("d.date<?")
        base_args.append(date_window_end(parse_date(to_date, field="to")).isoformat())
    where = " AND ".join(clauses)
    try:
        offset = max(0, int(cursor or "0"))
    except ValueError as exc:
        raise ValueError("cursor must be a non-negative integer") from exc
    with connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM capture c JOIN daily d ON d.id=c.daily_id "
            f"WHERE {where}",
            base_args,
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT c.id,c.daily_id,c.raw_text,c.captured_at,c.source,d.date "
            "FROM capture c JOIN daily d ON d.id=c.daily_id "
            f"WHERE {where} ORDER BY c.captured_at DESC, c.id DESC LIMIT ? OFFSET ?",
            [*base_args, limit + 1, offset],
        ).fetchall()
    has_more = len(rows) > limit
    visible = rows[:limit]
    return {
        "query": keyword,
        "items": [
            {
                "capture_id": row["id"],
                "daily_id": row["daily_id"],
                "date": row["date"],
                "captured_at": row["captured_at"],
                "source": row["source"],
                "snippet": _capture_snippet(str(row["raw_text"]), keyword),
            }
            for row in visible
        ],
        "next_cursor": str(offset + limit) if has_more else None,
        "total": int(total),
    }

def list_wiki(limit: int = 200) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM wiki_page ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

def get_wiki(page_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        return row_dict(conn.execute("SELECT * FROM wiki_page WHERE id=?", (page_id,)).fetchone())


KNOWLEDGE_CONTENT_MAX_BYTES = 200_000


def _canonical_content(content: dict[str, Any]) -> str:
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > KNOWLEDGE_CONTENT_MAX_BYTES:
        raise ValueError("content_json exceeds the 200000 byte limit")
    return encoded


def _validate_knowledge_links(conn, source_links, project_links) -> None:
    seen_sources: set[str] = set()
    for link in source_links or []:
        source_id = str(link["source_page_id"])
        if source_id in seen_sources:
            raise ValueError("source_links contains a duplicate source_page_id")
        seen_sources.add(source_id)
        if conn.execute("SELECT 1 FROM wiki_page WHERE id=?", (source_id,)).fetchone() is None:
            raise ValueError(f"Source page not found: {source_id}")

    seen_projects: set[str] = set()
    for link in project_links or []:
        project_id = str(link["project_id"])
        if project_id in seen_projects:
            raise ValueError("project_links contains a duplicate project_id")
        seen_projects.add(project_id)
        if conn.execute("SELECT 1 FROM project WHERE id=?", (project_id,)).fetchone() is None:
            raise ValueError(f"Project not found: {project_id}")


def list_knowledge(
    *,
    status: str | None = None,
    knowledge_type: str | None = None,
    domain_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    where: list[str] = []
    args: list[Any] = []
    if status:
        where.append("kp.status=?")
        args.append(status)
    if knowledge_type:
        where.append("kp.knowledge_type=?")
        args.append(knowledge_type)
    if domain_id:
        where.append("kp.domain_id=?")
        args.append(domain_id)
    predicate = f"WHERE {' AND '.join(where)}" if where else ""
    args.append(limit)
    with connect() as conn:
        rows = conn.execute(
            "SELECT kp.*, "
            "(SELECT COUNT(*) FROM knowledge_source_link ksl WHERE ksl.knowledge_id=kp.id) AS source_count, "
            "(SELECT COUNT(*) FROM knowledge_project_link kpl WHERE kpl.knowledge_id=kp.id) AS project_count, "
            "COALESCE((SELECT MAX(revision_no) FROM knowledge_revision kr "
            "          WHERE kr.knowledge_id=kp.id), 0) AS latest_revision_no, "
            # TD-003: the latest revision body is needed ONLY to derive a
            # deterministic business summary for the card.  It is projected
            # down to a bounded, provenance-free subset before it leaves.
            "(SELECT lr.content_json FROM knowledge_revision lr "
            "          WHERE lr.knowledge_id=kp.id "
            "          ORDER BY lr.revision_no DESC LIMIT 1) AS latest_content_json "
            "FROM knowledge_page kp "
            f"{predicate} ORDER BY kp.updated_at DESC, kp.id DESC LIMIT ?",
            args,
        ).fetchall()
        results = []
        for row in rows:
            record = dict(row)
            # Backend canonical projection: the SINGLE authority rule the
            # frontend must consume. CURRENT_CONFIRMED additionally requires
            # at least one verifiable evidence link (Evidence First).
            authority = knowledge_authority_state(record)
            if authority == "CURRENT_CONFIRMED" and int(record.get("source_count") or 0) < 1:
                authority = "BLOCKED_EVIDENCE"
            record["authority_state"] = authority
            # TD-003: bounded deterministic projection of the latest revision
            # body.  The classifier itself is NOT duplicated here — the
            # frontend runs its audited grammar over this projection.
            record["content_business_fields"] = project_summary_content(
                record.pop("latest_content_json", None))
            results.append(record)
        return results


def search_knowledge_pages(
    query: str,
    *,
    limit: int = 10,
    include_provisional: bool = False,
) -> dict[str, Any]:
    """Read-only Knowledge search executed in SQLite.

    The search runs in SQL over ``knowledge_page.title``,
    ``knowledge_page.one_line_summary`` and the **latest**
    ``knowledge_revision.content_json``.  It never pre-limits the row set in
    Python, so Knowledge past position 200 and terms that only exist in a
    revision body are reachable.

    Authority is decided by the single canonical
    ``knowledge_authority_state`` policy: by default only CURRENT_CONFIRMED
    rows are returned; ``include_provisional=True`` also returns draft /
    needs_review / deprecated / unconfirmed rows, each labelled with its
    ``authority_state``.  No external service and no new dependency.
    """
    text = (query or "").strip()
    if not text:
        return {"items": [], "count": 0, "matched_total": 0, "excluded_provisional_count": 0,
                "authority_scope": "current_confirmed_only", "governance_columns_present": True}
    like = f"%{text}%"
    with connect() as conn:
        columns = {str(r[1]) for r in conn.execute("PRAGMA table_info(knowledge_page)").fetchall()}
        governed = "confirmed_revision_no" in columns
        if not governed and not include_provisional:
            # Fail closed: without the Knowledge Confirmation Gate the latest
            # revision can never be proven confirmed, so nothing is canonical.
            return {"items": [], "count": 0, "matched_total": 0, "excluded_provisional_count": 0,
                    "authority_scope": "current_confirmed_only",
                    "governance_columns_present": False,
                    "fail_closed_reason": "KNOWLEDGE_GOVERNANCE_COLUMNS_ABSENT"}

        latest = (
            "LEFT JOIN (SELECT knowledge_id, MAX(revision_no) AS latest_revision_no "
            "           FROM knowledge_revision GROUP BY knowledge_id) lrm "
            "  ON lrm.knowledge_id = kp.id "
            "LEFT JOIN knowledge_revision lr "
            "  ON lr.knowledge_id = kp.id AND lr.revision_no = lrm.latest_revision_no"
        )
        match = (
            "kp.title LIKE ? OR kp.one_line_summary LIKE ? "
            "OR COALESCE(lr.content_json, '') LIKE ?"
        )
        confirmed = (
            "kp.status='current' AND kp.confirmed_revision_no IS NOT NULL "
            "AND kp.confirmed_revision_no = COALESCE(lrm.latest_revision_no, 0)"
        )
        select = (
            "SELECT kp.id, kp.title, kp.knowledge_type, kp.status, kp.one_line_summary, "
            "kp.updated_at, "
            + ("kp.confirmed_revision_no" if governed else "NULL AS confirmed_revision_no")
            + ", COALESCE(lrm.latest_revision_no, 0) AS latest_revision_no "
            "FROM knowledge_page kp " + latest
        )
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM knowledge_page kp {latest} WHERE {match}",
            (like, like, like),
        ).fetchone()["n"]
        # `canonical` is what the admission policy admits; `scope` is what this
        # call is allowed to return.  Counting stays on `canonical` so
        # `excluded_provisional_count` is meaningful in both modes.
        canonical_predicate = f"({match}) AND ({confirmed})" if governed else f"({match}) AND 0"
        scope_predicate = f"({match})" if (include_provisional or not governed) else canonical_predicate
        rows = conn.execute(
            f"{select} WHERE {scope_predicate} ORDER BY kp.updated_at DESC, kp.id LIMIT ?",
            (like, like, like, int(limit)),
        ).fetchall()
        admitted_total = conn.execute(
            f"SELECT COUNT(*) AS n FROM knowledge_page kp {latest} WHERE {canonical_predicate}",
            (like, like, like),
        ).fetchone()["n"]

    items: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        authority = knowledge_authority_state(record)
        if not include_provisional and authority != "CURRENT_CONFIRMED":
            # Defence in depth: SQL already filtered, the policy decides again.
            continue
        record["authority_state"] = authority
        items.append(record)
    return {
        "items": items,
        "count": len(items),
        "matched_total": total,
        "admitted_total": admitted_total,
        "excluded_provisional_count": max(0, total - admitted_total),
        "authority_scope": "current_confirmed_only" if not include_provisional else "includes_provisional",
        "governance_columns_present": governed,
    }


def get_knowledge(knowledge_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        page = conn.execute("SELECT * FROM knowledge_page WHERE id=?", (knowledge_id,)).fetchone()
        if page is None:
            return None
        revision = conn.execute(
            "SELECT * FROM knowledge_revision WHERE knowledge_id=? "
            "ORDER BY revision_no DESC LIMIT 1",
            (knowledge_id,),
        ).fetchone()
        sources = conn.execute(
            "SELECT w.id, w.title, w.category, w.wps_document_id, "
            "ksl.source_role, ksl.note "
            "FROM knowledge_source_link ksl JOIN wiki_page w ON w.id=ksl.source_page_id "
            "WHERE ksl.knowledge_id=? ORDER BY w.title",
            (knowledge_id,),
        ).fetchall()
        projects = conn.execute(
            "SELECT p.id, p.name, kpl.relation_type "
            "FROM knowledge_project_link kpl JOIN project p ON p.id=kpl.project_id "
            "WHERE kpl.knowledge_id=? ORDER BY p.name",
            (knowledge_id,),
        ).fetchall()
        page_data = dict(page)
        page_data["source_count"] = len(sources)
        page_data["project_count"] = len(projects)
        page_data["latest_revision_no"] = revision["revision_no"] if revision else 0
        revision_data = None
        if revision is not None:
            try:
                content = json.loads(revision["content_json"])
            except (TypeError, json.JSONDecodeError):
                content = {}
            revision_data = dict(revision)
            revision_data["content_json"] = content
        return {
            "page": page_data,
            "latest_revision": revision_data,
            "sources": [dict(row) for row in sources],
            "projects": [dict(row) for row in projects],
        }


def _replace_knowledge_links(conn, knowledge_id: str, source_links, project_links, ts: str) -> None:
    if source_links is not None:
        conn.execute("DELETE FROM knowledge_source_link WHERE knowledge_id=?", (knowledge_id,))
        conn.executemany(
            "INSERT INTO knowledge_source_link(knowledge_id,source_page_id,source_role,note,created_at) "
            "VALUES(?,?,?,?,?)",
            [
                (knowledge_id, link["source_page_id"], link.get("source_role") or "supporting", link.get("note"), ts)
                for link in source_links
            ],
        )
    if project_links is not None:
        conn.execute("DELETE FROM knowledge_project_link WHERE knowledge_id=?", (knowledge_id,))
        conn.executemany(
            "INSERT INTO knowledge_project_link(knowledge_id,project_id,relation_type,created_at) VALUES(?,?,?,?)",
            [
                (knowledge_id, link["project_id"], link.get("relation_type") or "primary", ts)
                for link in project_links
            ],
        )


def upsert_knowledge(data: dict[str, Any]) -> dict[str, Any]:
    """Create/update one Knowledge page and append a revision only on content change."""
    # The router supplies this sentinel because model_dump() includes defaults.
    # Direct repository callers retain the explicit-key fallback for backwards
    # compatibility with seed scripts and tests.
    fields_set = set(data.get("_fields_set", data.keys()))
    content_json_present = bool(data.get("content_json_present", "content_json" in fields_set))
    content_json = data.get("content_json")
    if content_json_present:
        if not isinstance(content_json, dict) or not content_json:
            raise KnowledgeContentError(
                "EMPTY_KNOWLEDGE_CONTENT",
                "content_json must be a non-empty object when provided",
            )
        canonical = _canonical_content(content_json)
    else:
        canonical = None
    target_status = data.get("status") or "draft"
    source_links = data.get("source_links")
    project_links = data.get("project_links")
    human_confirmed = bool(data.get("human_confirmed"))
    ts = now_iso()

    with connect() as conn:
        domain_id = data.get("domain_id")
        if domain_id is not None and conn.execute("SELECT 1 FROM domain WHERE id=?", (domain_id,)).fetchone() is None:
            raise ValueError(f"Domain not found: {domain_id}")
        existing = conn.execute(
            "SELECT * FROM knowledge_page WHERE slug=?", (data["slug"],)
        ).fetchone()
        if existing is not None and "status" not in fields_set:
            target_status = existing["status"]
        if target_status == "current" and "status" in fields_set:
            raise KnowledgeContentError(
                "KNOWLEDGE_CONFIRMATION_REQUIRED",
                "status=current requires the dedicated confirmation endpoint; generic upsert cannot publish Knowledge",
            )
        if source_links is not None or project_links is not None:
            _validate_knowledge_links(conn, source_links or [], project_links or [])

        if existing is None:
            if not content_json_present:
                raise KnowledgeContentError(
                    "EMPTY_KNOWLEDGE_CONTENT",
                    "content_json is required when creating Knowledge",
                )
            knowledge_id = uid()
            conn.execute(
                "INSERT INTO knowledge_page(" 
                "id,domain_id,title,slug,knowledge_type,one_line_summary,applicability,status," 
                "last_compiled_at,last_reviewed_at,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    knowledge_id, data.get("domain_id"), data["title"], data["slug"], data.get("knowledge_type") or "project",
                    data.get("one_line_summary") or "", data.get("applicability"), target_status,
                    ts, None, ts, ts,
                ),
            )
            revision_no = 1
            conn.execute(
                "INSERT INTO knowledge_revision(" 
                "id,knowledge_id,revision_no,content_json,change_summary,compiled_at,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (uid(), knowledge_id, revision_no, canonical, data.get("change_summary"), ts, ts),
            )
        else:
            knowledge_id = str(existing["id"])
            latest = conn.execute(
                "SELECT * FROM knowledge_revision WHERE knowledge_id=? ORDER BY revision_no DESC LIMIT 1",
                (knowledge_id,),
            ).fetchone()
            old_canonical = None
            if latest is not None:
                try:
                    old_canonical = _canonical_content(json.loads(latest["content_json"]))
                except (TypeError, json.JSONDecodeError):
                    old_canonical = latest["content_json"]
            if not content_json_present:
                if latest is None:
                    raise KnowledgeContentError(
                        "KNOWLEDGE_CONTENT_NOT_FOUND",
                        "content_json is required because this Knowledge has no revision",
                    )
                changed = False
            else:
                changed = latest is None or old_canonical != canonical
            if (
                changed
                and existing["status"] == "current"
                and "status" not in fields_set
            ):
                # A confirmed Revision must not silently cover new content:
                # publishing Revision N+1 demotes the page to needs_review
                # until the new Revision is confirmed again.  An explicit
                # caller-provided status (draft/needs_review/deprecated) is
                # never overridden.
                target_status = "needs_review"
            # An omitted optional metadata field must not erase its existing
            # value during a metadata/links-only request.
            existing_domain_id = existing["domain_id"]
            next_domain_id = data.get("domain_id") if data.get("domain_id") is not None else existing_domain_id
            next_knowledge_type = data.get("knowledge_type") if "knowledge_type" in fields_set else existing["knowledge_type"] or "project"
            next_summary = data.get("one_line_summary") if "one_line_summary" in fields_set else existing["one_line_summary"]
            next_applicability = data.get("applicability") if "applicability" in fields_set else existing["applicability"]
            conn.execute(
                "UPDATE knowledge_page SET domain_id=?, title=?, knowledge_type=?, one_line_summary=?, applicability=?, "
                "status=?, updated_at=? WHERE id=?",
                (
                    next_domain_id, data["title"], next_knowledge_type, next_summary or "",
                    next_applicability, target_status, ts, knowledge_id,
                ),
            )
            if changed:
                revision_no = int(latest["revision_no"]) + 1 if latest is not None else 1
                conn.execute(
                    "INSERT INTO knowledge_revision(" 
                    "id,knowledge_id,revision_no,content_json,change_summary,compiled_at,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (uid(), knowledge_id, revision_no, canonical, data.get("change_summary"), ts, ts),
                )
                conn.execute(
                    "UPDATE knowledge_page SET last_compiled_at=? WHERE id=?",
                    (ts, knowledge_id),
                )
        _replace_knowledge_links(conn, knowledge_id, source_links, project_links, ts)
        conn.commit()
    result = get_knowledge(knowledge_id)
    if result is None:
        raise RuntimeError("Knowledge upsert lost the created page")
    return result


def confirm_knowledge(knowledge_id: str, confirmed_by: str) -> dict[str, Any] | None:
    """Human-confirm the latest Revision of a Knowledge page as current.

    This is the only path allowed to set status=current.  Confirmation is
    bound to the concrete latest revision; a later content revision demotes
    the page back to needs_review inside upsert_knowledge.
    """
    confirmer = (confirmed_by or "").strip()
    if not confirmer:
        raise KnowledgeContentError(
            "KNOWLEDGE_CONFIRMATION_IDENTITY_REQUIRED",
            "confirmed_by must be a non-empty identity string",
        )
    ts = now_iso()
    with connect() as conn:
        page = conn.execute(
            "SELECT * FROM knowledge_page WHERE id=?", (knowledge_id,)
        ).fetchone()
        if page is None:
            return None
        latest = conn.execute(
            "SELECT * FROM knowledge_revision WHERE knowledge_id=? ORDER BY revision_no DESC LIMIT 1",
            (knowledge_id,),
        ).fetchone()
        if latest is None:
            raise KnowledgeContentError(
                "KNOWLEDGE_REVISION_REQUIRED",
                "cannot confirm Knowledge without any revision",
            )
        latest_no = int(latest["revision_no"])
        already = (
            page["status"] == "current"
            and page["confirmed_revision_no"] is not None
            and int(page["confirmed_revision_no"]) == latest_no
        )
        if not already:
            conn.execute(
                "UPDATE knowledge_page SET status='current', confirmed_revision_no=?, "
                "confirmed_at=?, confirmed_by=?, updated_at=? WHERE id=?",
                (latest_no, ts, confirmer, ts, knowledge_id),
            )
            conn.commit()
    result = get_knowledge(knowledge_id)
    if result is None:
        raise RuntimeError("Knowledge confirmation lost the page")
    return {"already_confirmed": already, "knowledge": result}


# --- Source Registry Registration ---

_IMAGE_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp",
    ".svg", ".heic", ".heif", ".tif", ".tiff",
})


class SourceRegistrationError(ValueError):
    """A safe, machine-readable validation failure for source registration."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def register_source(
    title: str,
    canonical_location: str,
    source_origin: str,
    content_hash: str,
    file_ext: str,
    source_kind: str,
    canonical_state: str,
    size: int | None = None,
    mtime: str | None = None,
) -> dict[str, Any]:
    """Register or update a synchronized or locally scanned source.

    This is a metadata-only operation.  It never reads file contents,
    creates Knowledge, or modifies existing Knowledge links.

    The source_page_id is deterministically derived from
    source_origin + canonical_location, so repeated calls for the same
    source are idempotent.
    """
    if source_origin not in {"office_sync", "local_scan"}:
        raise SourceRegistrationError(
            "SOURCE_ORIGIN_NOT_ALLOWED",
            f"source_origin must be 'office_sync' or 'local_scan', got '{source_origin}'",
        )

    ext = file_ext.lower() if file_ext else ""
    if ext in _IMAGE_EXTENSIONS:
        raise SourceRegistrationError(
            "SOURCE_IMAGE_NOT_ALLOWED",
            f"image files are not allowed in this registry path: '{file_ext}'",
        )

    if source_kind.lower() == "image":
        raise SourceRegistrationError(
            "SOURCE_IMAGE_NOT_ALLOWED",
            "source_kind='image' is not allowed in this registry path",
        )

    stable_key = f"{source_origin}:{canonical_location}"
    source_page_id = "src-" + hashlib.sha256(stable_key.encode("utf-8")).hexdigest()[:24]
    slug = "source-" + source_page_id[-16:]
    ts = now_iso()

    with connect() as conn:
        existing = conn.execute(
            "SELECT * FROM wiki_page WHERE id=?", (source_page_id,)
        ).fetchone()

        if existing is not None:
            conn.execute(
                "UPDATE wiki_page SET title=?, source_path=?, source_origin=?, "
                "source_kind=?, canonical_state=?, content_hash=?, "
                "wps_updated_at=?, updated_at=? WHERE id=?",
                (
                    title, canonical_location, source_origin,
                    source_kind, canonical_state, content_hash,
                    mtime, ts, source_page_id,
                ),
            )
            conn.commit()
            created = False
        else:
            conn.execute(
                "INSERT INTO wiki_page("
                "id, title, slug, summary, source_path, source_origin, "
                "source_kind, canonical_state, content_hash, "
                "wps_updated_at, knowledge_status, created_at, updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    source_page_id, title, slug, "", canonical_location,
                    source_origin, source_kind, canonical_state, content_hash,
                    mtime, "current", ts, ts,
                ),
            )
            conn.commit()
            created = True

    result = get_wiki(source_page_id)
    if result is None:
        raise RuntimeError("Source registration lost the created page")
    result["_created"] = created
    return result


def _draft_repo():
    from paw_luna_draft_store import draft_counts, get_draft, list_drafts, migrate as migrate_drafts

    return get_draft, list_drafts, draft_counts, migrate_drafts


def _project_brain_projection(project_id: str, *, project_name: str | None = None) -> dict[str, Any]:
    try:
        from gpt_brain_read_api import build_project_page_projection

        payload = build_project_page_projection(project_id, include_data=True, redact=True)
        if not payload.get("found"):
            return {
                "brain_projection_found": False,
                "draft_context": None,
                "data_summary": None,
            }
        draft = payload.get("draft_context") or {}
        draft_topics = [
            {
                "topic": item.get("topic"),
                "authority_level": "draft",
                "is_canonical": False,
                "source_version_ids": item.get("source_version_ids") or [],
            }
            for item in (draft.get("linked_knowledge_topics") or [])
            if isinstance(item, dict)
        ]
        return {
            "brain_projection_found": True,
            "draft_context": draft,
            "draft_knowledge_topics": draft_topics,
            "data_summary": payload.get("data_summary"),
            "timeline": draft.get("timeline") or [],
            "draft_decisions": draft.get("decisions") or [],
            "draft_risks": draft.get("risks") or [],
            "draft_conflicts": draft.get("conflicts") or [],
            "draft_ambiguities": draft.get("ambiguities") or [],
            "source_evidence": draft.get("evidence") or [],
            "authority_level": "draft",
            "is_canonical": False,
            "canonical_project": draft.get("formal_project"),
        }
    except Exception:
        return {
            "brain_projection_found": False,
            "draft_context": None,
            "data_summary": None,
        }


def project_brain_data_summary(
    project_id: str,
    *,
    observation_limit: int = 20,
    asset_limit: int = 20,
    conflict_limit: int = 20,
) -> dict[str, Any] | None:
    with connect() as conn:
        project = conn.execute("SELECT * FROM project WHERE id=?", (project_id,)).fetchone()
        if not project:
            return None
    try:
        from gpt_brain_read_api import _live_data_layer

        layer = _live_data_layer()
        return layer.project_data_summary(
            str(dict(project)["name"]),
            observation_limit=observation_limit,
            asset_limit=asset_limit,
            conflict_limit=conflict_limit,
            redact=True,
        )
    except Exception:
        return {
            "project": dict(project)["name"],
            "observation_total": 0,
            "data_asset_total": 0,
            "conflict_total": 0,
            "authority_level": "draft",
            "is_canonical": False,
            "analysis_plane": True,
        }


def ensure_luna_draft_schema() -> dict[str, Any]:
    _, _, _, migrate_drafts = _draft_repo()
    return migrate_drafts()


def list_luna_drafts(
    *,
    project_id: str | None = None,
    status: str | None = None,
    source_version_id: str | None = None,
    query: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    _, list_drafts, _, _ = _draft_repo()
    return list_drafts(
        project_id=project_id,
        status=status,
        source_version_id=source_version_id,
        query=query,
        limit=limit,
        offset=offset,
    )


def get_luna_draft(draft_id: str | None = None, source_version_id: str | None = None) -> dict[str, Any] | None:
    get_draft, _, _, _ = _draft_repo()
    return get_draft(draft_id=draft_id, source_version_id=source_version_id)


def luna_draft_metrics() -> dict[str, Any]:
    _, _, draft_counts, _ = _draft_repo()
    return draft_counts()
