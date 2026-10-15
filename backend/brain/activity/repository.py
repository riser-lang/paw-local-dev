"""AI Activity Repository — Read-only aggregation layer.

Derives activity events from analysis_run + candidate tables.
NEVER writes to any table. NEVER exposes raw_text or private data.

Activity is a derived operational view, NOT canonical state.
"""

from __future__ import annotations

from pathlib import Path
import os

import sqlite3
import json
from datetime import datetime
from typing import Any, Optional

PRODUCTION_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))

# Fields that must NEVER be exposed in activity responses
PRIVATE_FIELDS = {"raw_text", "system_prompt", "system_context", "chain_of_thought", "private_reasoning"}


def _get_ro_connection(db_path: str = PRODUCTION_DB) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row
    return con


def _parse_refs(raw_input_refs: str) -> dict:
    """Safely parse raw_input_refs JSON. Returns metadata dict."""
    if not raw_input_refs:
        return {}
    try:
        data = json.loads(raw_input_refs)
        if isinstance(data, dict):
            return data
        elif isinstance(data, list):
            return {"refs": data}
        return {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _derive_analysis_status(row: dict) -> str:
    """Derive analysis status from available metadata."""
    refs_meta = _parse_refs(row.get("raw_input_refs", ""))
    if isinstance(refs_meta, dict) and "status" in refs_meta:
        return refs_meta["status"]
    # Fallback: use completed_at
    completed_at = row.get("completed_at", "")
    started_at = row.get("started_at", "")
    if completed_at and completed_at.strip():
        return "completed"
    if started_at and started_at.strip():
        return "unknown"
    return "unknown"


def _safe_source_label(trigger_type: str, refs_meta: dict) -> tuple[str, Optional[str]]:
    """Derive safe source entity type and label from trigger_type + refs.
    Returns (source_entity_type, source_label).
    NEVER returns raw_text.
    """
    source_entity_type = None
    source_label = None

    if trigger_type == "capture":
        source_entity_type = "capture"
        source_label = "Capture"
    elif trigger_type == "daily":
        source_entity_type = "daily"
        source_label = "Daily"
    elif trigger_type in ("canary_capture", "auto_capture"):
        source_entity_type = "capture"
        source_label = "Capture"
    elif trigger_type in ("auto_daily",):
        source_entity_type = "daily"
        source_label = "Daily"
    elif trigger_type == "manual_review":
        source_entity_type = None
        source_label = "Manual Review"
    else:
        source_label = trigger_type

    return source_entity_type, source_label


def _make_analysis_activity_id(analysis_run_id: str, status: str) -> str:
    """Deterministic activity ID for analysis events."""
    return f"analysis:{analysis_run_id}:{status}"


def _make_candidate_activity_id(candidate_id: str, status: str) -> str:
    """Deterministic activity ID for candidate events."""
    return f"candidate:{candidate_id}:{status}"


def get_analysis_activities(
    limit: int = 50,
    since: Optional[str] = None,
    db_path: str = PRODUCTION_DB,
) -> list[dict]:
    """Get analysis activity events from analysis_run table.
    Read-only. Never exposes raw_text.
    """
    try:
        con = _get_ro_connection(db_path) if db_path == PRODUCTION_DB else sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        query = "SELECT id, trigger_type, raw_input_refs, analysis_model, started_at, completed_at, created_at FROM analysis_run"
        params = []
        if since:
            query += " WHERE created_at >= ?"
            params.append(since)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = con.execute(query, params).fetchall()
        con.close()

        activities = []
        for row in rows:
            r = dict(row)
            status = _derive_analysis_status(r)
            refs_meta = _parse_refs(r.get("raw_input_refs", ""))
            source_entity_type, source_label = _safe_source_label(r.get("trigger_type", ""), refs_meta)

            # Determine activity_type
            if status == "completed":
                activity_type = "analysis_completed"
            elif status == "failed":
                activity_type = "analysis_failed"
            elif status == "running":
                activity_type = "analysis_started"
            else:
                activity_type = "analysis_completed"  # Safe default for legacy data

            timestamp = r.get("completed_at") or r.get("started_at") or r.get("created_at", "")

            activities.append({
                "activity_id": _make_analysis_activity_id(r["id"], status),
                "activity_type": activity_type,
                "timestamp": timestamp,
                "analysis_run_id": r["id"],
                "trigger_type": r.get("trigger_type", ""),
                "source_entity_type": source_entity_type,
                "source_label": source_label,
                "analysis_model": r.get("analysis_model", ""),
                "status": status,
                "candidate_count": None,  # Will be filled by service if needed
                "needs_user_attention": False,
                "project_id": refs_meta.get("project_id") if isinstance(refs_meta, dict) else None,
                "summary": f"{source_label or 'Analysis'} — {status}",
            })
        return activities
    except Exception:
        return []


def get_candidate_activities(
    limit: int = 50,
    since: Optional[str] = None,
    db_path: str = PRODUCTION_DB,
) -> list[dict]:
    """Get candidate activity events from candidate table.
    Read-only. Never exposes raw_excerpt or full payload.
    """
    try:
        con = _get_ro_connection(db_path) if db_path == PRODUCTION_DB else sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        query = "SELECT id, candidate_type, source_refs, proposed_change, confidence, recommended_action, status, created_at, reviewed_at, reviewed_by FROM candidate"
        params = []
        conditions = []
        if since:
            conditions.append("created_at >= ?")
            params.append(since)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = con.execute(query, params).fetchall()
        con.close()

        activities = []
        for row in rows:
            r = dict(row)
            status = r.get("status", "pending")
            refs_raw = r.get("source_refs", "")
            refs_meta = {}
            if refs_raw:
                try:
                    refs_meta = json.loads(refs_raw)
                except (json.JSONDecodeError, TypeError):
                    refs_meta = {}

            source_refs_list = refs_meta.get("refs", []) if isinstance(refs_meta, dict) else []

            # Determine activity_type
            if status == "pending":
                activity_type = "candidate_pending"
                timestamp = r.get("created_at", "")
            elif status == "accepted":
                activity_type = "candidate_accepted"
                timestamp = r.get("reviewed_at") or r.get("created_at", "")
            elif status == "rejected":
                activity_type = "candidate_rejected"
                timestamp = r.get("reviewed_at") or r.get("created_at", "")
            else:
                activity_type = "candidate_pending"
                timestamp = r.get("created_at", "")

            # Check edit_and_accept
            review_modified = False
            change_raw = r.get("proposed_change", "")
            if change_raw and status == "accepted":
                try:
                    change = json.loads(change_raw)
                    if isinstance(change, dict) and "reviewed_payload" in change:
                        review_modified = True
                except (json.JSONDecodeError, TypeError):
                    pass

            # needs_user_attention
            recommended_action = r.get("recommended_action", "")
            candidate_type = r.get("candidate_type", "")
            needs_attention = (
                status == "pending" and (
                    candidate_type in ("conflict", "ambiguity", "question") or
                    recommended_action == "ASK_USER"
                )
            )

            # Safe summary (minimized)
            summary_text = f"{candidate_type} — {status}"
            if review_modified:
                summary_text += " (edited)"

            activities.append({
                "activity_id": _make_candidate_activity_id(r["id"], status),
                "activity_type": activity_type,
                "timestamp": timestamp,
                "candidate_id": r["id"],
                "candidate_type": candidate_type,
                "status": status,
                "recommended_action": recommended_action,
                "confidence": r.get("confidence", ""),
                "project_id": None,  # Not force-bound
                "source_refs_summary": f"{len(source_refs_list)} source(s)",
                "summary": summary_text,
                "needs_user_attention": needs_attention,
                "review_modified": review_modified,
            })
        return activities
    except Exception:
        return []


def get_activity_summary(db_path: str = PRODUCTION_DB) -> dict:
    """Get lightweight activity summary counts."""
    try:
        con = _get_ro_connection(db_path) if db_path == PRODUCTION_DB else sqlite3.connect(db_path)
        cur = con.cursor()

        analysis_total = cur.execute("SELECT COUNT(*) FROM analysis_run").fetchone()[0]
        candidate_total = cur.execute("SELECT COUNT(*) FROM candidate").fetchone()[0]
        pending_total = cur.execute("SELECT COUNT(*) FROM candidate WHERE status='pending'").fetchone()[0]
        accepted_total = cur.execute("SELECT COUNT(*) FROM candidate WHERE status='accepted'").fetchone()[0]
        rejected_total = cur.execute("SELECT COUNT(*) FROM candidate WHERE status='rejected'").fetchone()[0]

        # needs_user_attention
        needs_attention = cur.execute(
            "SELECT COUNT(*) FROM candidate WHERE status='pending' AND (candidate_type IN ('conflict','ambiguity','question') OR recommended_action='ASK_USER')"
        ).fetchone()[0]

        # last_activity_at
        last_analysis = cur.execute("SELECT MAX(completed_at) FROM analysis_run").fetchone()[0] or ""
        last_candidate = cur.execute("SELECT MAX(created_at) FROM candidate").fetchone()[0] or ""
        last_activity = max(last_analysis, last_candidate) if (last_analysis or last_candidate) else None

        con.close()
        return {
            "total_recent": analysis_total + candidate_total,
            "analysis_total": analysis_total,
            "candidate_total": candidate_total,
            "pending_confirmation_total": pending_total,
            "needs_user_attention": needs_attention,
            "completed_analysis_total": analysis_total,  # All existing are completed (legacy)
            "failed_analysis_total": 0,
            "accepted_total": accepted_total,
            "rejected_total": rejected_total,
            "last_activity_at": last_activity,
        }
    except Exception as e:
        return {"error": str(e)}


def create_temp_activity_db() -> str:
    """Create temp DB with analysis_run + candidate tables for testing."""
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".db", prefix="activity_test_")
    os.close(fd)
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE analysis_run (
        id TEXT PRIMARY KEY, trigger_type TEXT NOT NULL, raw_text TEXT,
        raw_input_refs TEXT, analysis_model TEXT NOT NULL,
        started_at TEXT NOT NULL, completed_at TEXT NOT NULL, created_at TEXT NOT NULL
    )""")
    con.execute("""CREATE TABLE candidate (
        id TEXT PRIMARY KEY, analysis_run_id TEXT NOT NULL,
        candidate_type TEXT NOT NULL, source_refs TEXT, raw_excerpt TEXT,
        proposed_change TEXT, confidence TEXT NOT NULL, inference_level TEXT NOT NULL,
        recommended_action TEXT NOT NULL, analysis_model TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL,
        reviewed_at TEXT, reviewed_by TEXT, applied_entity_type TEXT, applied_entity_id TEXT
    )""")
    con.commit()
    con.close()
    return path
