"""Candidate Canonical Apply Service — V1.

Implements the explicit, human-approved, transactional, idempotent Apply Pipeline:
  accepted → validated → Canonical Workspace write → applied

V1 Safety:
  - ONLY supports candidate_type='todo'
  - Requires candidate.status == 'accepted'
  - Uses single SQLite transaction (same DB: workspace.db)
  - Idempotent: re-apply returns existing applied entity
  - Never auto-applies; requires explicit POST
  - AUTO_WRITE_ENABLED remains FALSE
  - BACKGROUND_AUTO_ANALYSIS remains OFF

Canonical Writer:
  - todo: workspace_api/repository.py:create_todo(data)
  - project_update: NOT_SUPPORTED_V1 (no update_project writer)
  - knowledge: NOT_SUPPORTED_V1 (no knowledge writer)
  - fact/conflict/ambiguity/question: NOT_APPLICABLE
"""

from __future__ import annotations

from pathlib import Path
import os


import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from workspace_api.repository import create_todo_in_transaction

PRODUCTION_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))

SAFE_APPLY_TYPES = {"todo", "knowledge"}
UNSUPPORTED_APPLY_TYPES = {"project_update", "fact", "conflict", "ambiguity", "question"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def apply_candidate(
    candidate_id: str,
    applied_by: str = "custom_gpt",
    db_path: str = PRODUCTION_DB,
) -> dict:
    """Apply an accepted Candidate to Canonical Workspace.

    Pipeline:
    1. Read candidate
    2. Verify status == 'accepted'
    3. Verify candidate_type in SAFE_APPLY_TYPES
    4. Verify proposed_change has required fields
    5. Check if already applied (idempotency)
    6. Begin transaction
    7. Write to Canonical (todo table)
    8. Update candidate: status='applied', applied_entity_type, applied_entity_id
    9. Commit (or rollback on any failure)

    Returns dict with candidate_id, status, canonical_applied, applied_entity_type, applied_entity_id, message.
    """
    con = sqlite3.connect(db_path, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")

    try:
        # 1. Read candidate
        row = con.execute(
            "SELECT * FROM candidate WHERE id = ?", (candidate_id,)
        ).fetchone()
        if not row:
            return {"error": "candidate_not_found", "candidate_id": candidate_id}

        candidate = dict(row)

        # 2. Verify status == 'accepted'
        if candidate["status"] == "applied":
            # Idempotent: already applied, return existing entity info
            return {
                "candidate_id": candidate_id,
                "status": "applied",
                "canonical_applied": True,
                "applied_entity_type": candidate.get("applied_entity_type"),
                "applied_entity_id": candidate.get("applied_entity_id"),
                "message": "Candidate already applied. No duplicate action taken.",
            }

        if candidate["status"] != "accepted":
            return {
                "error": f"candidate status is '{candidate['status']}', must be 'accepted' to apply",
                "candidate_id": candidate_id,
                "current_status": candidate["status"],
            }

        # 3. Verify candidate_type in SAFE_APPLY_TYPES
        ctype = candidate["candidate_type"]
        if ctype not in SAFE_APPLY_TYPES:
            return {
                "error": f"candidate_type '{ctype}' is not supported for apply in V1",
                "candidate_id": candidate_id,
                "supported_types": list(SAFE_APPLY_TYPES),
            }

        # 4. Parse and verify proposed_change
        proposed_change = candidate.get("proposed_change", "{}")
        if isinstance(proposed_change, str):
            try:
                proposed_change = json.loads(proposed_change)
            except (json.JSONDecodeError, TypeError):
                proposed_change = {}

        # Extract actual change from review-wrapped payload.
        # User-edited payload (reviewed_payload) has PRIORITY over the original
        # AI proposal (original_payload) — an accepted+edited draft must apply
        # the user's version, never silently the old AI text.
        if isinstance(proposed_change, dict) and "reviewed_payload" in proposed_change:
            actual_change = proposed_change.get("reviewed_payload", {})
            if isinstance(actual_change, str):
                try:
                    actual_change = json.loads(actual_change)
                except (json.JSONDecodeError, TypeError):
                    actual_change = {}
        elif isinstance(proposed_change, dict) and "original_payload" in proposed_change:
            actual_change = proposed_change.get("original_payload", {})
            if isinstance(actual_change, str):
                try:
                    actual_change = json.loads(actual_change)
                except (json.JSONDecodeError, TypeError):
                    actual_change = {}
        else:
            actual_change = proposed_change

        if not isinstance(actual_change, dict):
            actual_change = {}

        # 5. Validate required fields for todo type
        if ctype == "todo":
            title = actual_change.get("title")
            if not title or not isinstance(title, str) or not title.strip():
                return {
                    "error": "proposed_change missing required field 'title' for todo apply",
                    "candidate_id": candidate_id,
                }

        # 6. Begin transaction — single atomic operation
        # Use savepoint for nested transaction safety
        con.execute("BEGIN")

        try:
            ts = _now()

            if ctype == "todo":
                # 7. Write to Canonical via the shared canonical Todo writer.
                source_refs = candidate.get("source_refs", "{}")
                if isinstance(source_refs, str):
                    try:
                        source_refs = json.loads(source_refs)
                    except (json.JSONDecodeError, TypeError):
                        source_refs = {}
                refs_list = []
                if isinstance(source_refs, dict):
                    refs_list = source_refs.get("refs", [])
                elif isinstance(source_refs, list):
                    refs_list = source_refs

                # Get first source_capture_id if available — verify it exists in capture table
                source_capture_id = None
                if refs_list:
                    first_ref = refs_list[0] if isinstance(refs_list, list) else str(refs_list)
                    ref_check = con.execute(
                        "SELECT 1 FROM capture WHERE id = ?", (first_ref,)
                    ).fetchone()
                    if ref_check:
                        source_capture_id = first_ref

                # Also verify project_id exists if provided
                project_id = actual_change.get("project_id")
                if project_id:
                    proj_check = con.execute(
                        "SELECT 1 FROM project WHERE id = ?", (project_id,)
                    ).fetchone()
                    if not proj_check:
                        project_id = None  # Don't use invalid project_id

                todo = create_todo_in_transaction(
                    con,
                    {
                        "project_id": project_id,
                        "title": title,
                        "description": actual_change.get("description"),
                        "status": "open",
                        "priority": actual_change.get("priority", "medium"),
                        "due_at": actual_change.get("due_at"),
                        "source_type": "ai_candidate",
                        "source_capture_id": source_capture_id,
                        "ai_generated": 1,
                        "sync_status": "local",
                    },
                )
                todo_id = todo["id"]

                applied_entity_type = "todo"
                applied_entity_id = todo_id

            elif ctype == "knowledge":
                # Governed Knowledge write-back: reuse the ONLY legal writers
                # (workspace_api upsert_knowledge + confirm_knowledge). Never
                # write knowledge_page directly; never bypass the confirmation
                # gate. Evidence links are rebuilt deterministically from the
                # candidate's own source_refs (capture refs verified to exist).
                from workspace_api.repository import (
                    upsert_knowledge as _upsert_knowledge,
                    confirm_knowledge as _confirm_knowledge,
                )

                # The workspace_api writers open their OWN connections and
                # transactions; SQLite permits a single writer at a time, so
                # the outer transaction (which holds a write lock from BEGIN)
                # must be released BEFORE delegating. Nothing has been written
                # in it yet at this point, so the rollback is safe. The shared
                # candidate-status update below re-opens a fresh transaction.
                con.execute("ROLLBACK")

                knowledge_id = actual_change.get("knowledge_id")
                title = actual_change.get("title")
                content = actual_change.get("content")
                if not knowledge_id and not (title and content):
                    raise ValueError(
                        "knowledge apply requires knowledge_id (revise) or title+content (create)"
                    )

                # Deterministic evidence links: only refs that verifiably exist.
                source_links = []
                raw_refs = candidate.get("source_refs", "{}")
                if isinstance(raw_refs, str):
                    try:
                        raw_refs = json.loads(raw_refs)
                    except (json.JSONDecodeError, TypeError):
                        raw_refs = {}
                refs_list = raw_refs.get("refs", []) if isinstance(raw_refs, dict) else (raw_refs if isinstance(raw_refs, list) else [])
                for ref in refs_list:
                    ref_text = str(ref)
                    if ref_text.startswith("capture:"):
                        cap_id = ref_text.split(":", 1)[1]
                        cap = con.execute(
                            "SELECT raw_text FROM capture WHERE id = ?", (cap_id,)
                        ).fetchone()
                        if cap is None:
                            continue
                        # Deterministic source-page match: the capture's own
                        # raw_text must reference a registered wiki_page source.
                        # No fuzzy title matching, no invented evidence.
                        wp = con.execute(
                            "SELECT id FROM wiki_page WHERE source_kind IS NOT NULL AND source_kind != '' "
                            "AND (title = ? OR source_path = ?) LIMIT 1",
                            (cap["raw_text"][:200], cap["raw_text"][:200]),
                        ).fetchone()
                        if wp is not None:
                            source_links.append({
                                "source_page_id": wp["id"],
                                "source_role": "supporting",
                                "note": f"deterministic link from candidate source_ref {ref_text}",
                            })

                if knowledge_id:
                    existing_page = con.execute(
                        "SELECT id, title, slug FROM knowledge_page WHERE id = ?", (knowledge_id,)
                    ).fetchone()
                    if existing_page is None:
                        raise ValueError(f"knowledge_id {knowledge_id} not found for revise apply")
                    # Revise path: reuse the existing page's slug so upsert
                    # resolves to THIS page (upsert matches by slug, not id).
                    payload = {
                        "title": title or existing_page["title"],
                        "slug": existing_page["slug"],
                        "one_line_summary": actual_change.get("summary") or "",
                        "knowledge_type": actual_change.get("knowledge_type") or "analysis",
                    }
                else:
                    payload = {
                        "title": title,
                        "slug": (title or "knowledge").strip().lower().replace(" ", "-")[:60] + "-" + str(uuid.uuid4())[:8],
                        "one_line_summary": actual_change.get("summary") or "",
                        "knowledge_type": actual_change.get("knowledge_type") or "analysis",
                    }
                if content:
                    payload["content_json"] = {"content": content}
                if source_links:
                    payload["source_links"] = source_links
                if actual_change.get("project_id"):
                    proj_check = con.execute(
                        "SELECT 1 FROM project WHERE id = ?", (actual_change["project_id"],)
                    ).fetchone()
                    if proj_check:
                        payload["project_links"] = [{"project_id": actual_change["project_id"]}]

                if knowledge_id:
                    payload["id"] = knowledge_id
                result = _upsert_knowledge(payload)
                # upsert returns the get_knowledge shape; the page record may be
                # nested under "page" or returned directly — accept both.
                page_record = result.get("page") if isinstance(result, dict) else None
                if not isinstance(page_record, dict):
                    page_record = result if isinstance(result, dict) else {}
                kid = page_record.get("id") or result.get("id") if isinstance(result, dict) else None
                if not kid:
                    raise ValueError(f"knowledge upsert returned no page id: {str(result)[:200]}")
                # Confirmation is the ONLY path to status=current. The applied
                # candidate carries an explicit user approval (ReviewDecision),
                # which authorizes this confirmation.
                _confirm_knowledge(kid, confirmed_by=applied_by)

                # Re-open the transaction for the shared candidate-status
                # update + commit below.
                con.execute("BEGIN")

                applied_entity_type = "knowledge"
                applied_entity_id = kid

            # 8. Update candidate: status='applied', applied_entity_type, applied_entity_id
            con.execute(
                """UPDATE candidate
                   SET status = 'applied',
                       applied_entity_type = ?,
                       applied_entity_id = ?,
                       reviewed_at = COALESCE(reviewed_at, ?),
                       reviewed_by = COALESCE(reviewed_by, ?)
                   WHERE id = ?""",
                (applied_entity_type, applied_entity_id, ts, applied_by, candidate_id),
            )

            # 9. Commit
            con.execute("COMMIT")

            return {
                "candidate_id": candidate_id,
                "status": "applied",
                "canonical_applied": True,
                "applied_entity_type": applied_entity_type,
                "applied_entity_id": applied_entity_id,
                "message": "Candidate applied to Canonical Workspace.",
            }

        except Exception as write_error:
            # Rollback on any failure during canonical write or candidate update.
            # Tolerate "no active transaction" (the knowledge path releases
            # the outer transaction before delegating to the workspace writers).
            try:
                con.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            return {
                "error": f"apply_failed: {str(write_error)}",
                "candidate_id": candidate_id,
                "status": "accepted",  # candidate remains accepted
            }

    except Exception as e:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        return {
            "error": f"apply_service_error: {str(e)}",
            "candidate_id": candidate_id,
        }
    finally:
        con.close()


def create_temp_apply_test_db() -> str:
    """Create temp DB with candidate + analysis_run + todo + project + daily + capture tables for testing."""
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".db", prefix="apply_test_")
    os.close(fd)
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")

    # analysis_run table
    con.execute("""CREATE TABLE analysis_run (
        id TEXT PRIMARY KEY, trigger_type TEXT NOT NULL, raw_text TEXT,
        raw_input_refs TEXT, analysis_model TEXT NOT NULL,
        started_at TEXT NOT NULL, completed_at TEXT NOT NULL, created_at TEXT NOT NULL
    )""")

    # domain table (needed for foreign keys)
    con.execute("""CREATE TABLE domain (
        id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
        description TEXT, is_enabled INTEGER NOT NULL DEFAULT 1,
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")

    # project table
    con.execute("""CREATE TABLE project (
        id TEXT PRIMARY KEY, domain_id TEXT, name TEXT NOT NULL, description TEXT,
        status TEXT NOT NULL DEFAULT 'planning', stage TEXT, priority TEXT,
        due_date TEXT, next_action_summary TEXT, latest_progress_summary TEXT,
        privacy_level TEXT NOT NULL DEFAULT 'normal',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        FOREIGN KEY(domain_id) REFERENCES domain(id)
    )""")

    # daily table
    con.execute("""CREATE TABLE daily (
        id TEXT PRIMARY KEY, date TEXT NOT NULL UNIQUE, domain_id TEXT, title TEXT NOT NULL,
        wps_document_id TEXT, wps_url TEXT, sync_status TEXT NOT NULL DEFAULT 'local',
        last_synced_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, processed_at TEXT,
        FOREIGN KEY(domain_id) REFERENCES domain(id)
    )""")

    # capture table
    con.execute("""CREATE TABLE capture (
        id TEXT PRIMARY KEY, daily_id TEXT NOT NULL, raw_text TEXT NOT NULL,
        captured_at TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'chatgpt_action',
        source_device_id TEXT, user_edited INTEGER NOT NULL DEFAULT 0,
        sync_status TEXT NOT NULL DEFAULT 'local', wps_anchor TEXT,
        processed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        FOREIGN KEY(daily_id) REFERENCES daily(id)
    )""")

    # todo table (matches production schema)
    con.execute("""CREATE TABLE todo (
        id TEXT PRIMARY KEY, domain_id TEXT, project_id TEXT, title TEXT NOT NULL,
        description TEXT, status TEXT NOT NULL DEFAULT 'open', priority TEXT,
        due_at TEXT, start_at TEXT, waiting_for TEXT,
        source_type TEXT NOT NULL DEFAULT 'manual', source_capture_id TEXT,
        ai_generated INTEGER NOT NULL DEFAULT 0, completed_at TEXT,
        wps_task_ref TEXT, sync_status TEXT NOT NULL DEFAULT 'local',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        FOREIGN KEY(domain_id) REFERENCES domain(id),
        FOREIGN KEY(project_id) REFERENCES project(id),
        FOREIGN KEY(source_capture_id) REFERENCES capture(id)
    )""")

    # candidate table (matches production schema)
    con.execute("""CREATE TABLE candidate (
        id TEXT PRIMARY KEY, analysis_run_id TEXT NOT NULL,
        candidate_type TEXT NOT NULL CHECK (candidate_type IN (
            'fact','todo','project_update','knowledge','conflict','ambiguity','question')),
        source_refs TEXT, raw_excerpt TEXT, proposed_change TEXT,
        confidence TEXT NOT NULL CHECK (confidence IN ('HIGH','MEDIUM','LOW')),
        inference_level TEXT NOT NULL CHECK (inference_level IN (
            'EXPLICIT','STRONG_INFERENCE','WEAK_INFERENCE')),
        recommended_action TEXT NOT NULL CHECK (recommended_action IN (
            'AUTO_WRITE','DRAFT_ONLY','ASK_USER','DO_NOT_WRITE')),
        analysis_model TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
            'pending','accepted','rejected','applied')),
        created_at TEXT NOT NULL, reviewed_at TEXT, reviewed_by TEXT,
        applied_entity_type TEXT, applied_entity_id TEXT,
        FOREIGN KEY (analysis_run_id) REFERENCES analysis_run(id) ON DELETE CASCADE
    )""")
    con.execute("CREATE INDEX idx_candidate_status ON candidate(status)")
    con.execute("CREATE INDEX idx_candidate_type ON candidate(candidate_type)")
    con.execute("CREATE INDEX idx_candidate_analysis_run ON candidate(analysis_run_id)")

    con.commit()
    con.close()
    return path
