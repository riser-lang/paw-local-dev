"""Brain Repository — Database operations for Brain API.

Handles:
- analysis_run CRUD
- candidate CRUD
- Daily/Capture processed_at management
- AI activity records
- Confirmation center queries

Uses same DB_PATH as workspace_api.
"""

from __future__ import annotations

import os

import sqlite3
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from workspace_api.repository import create_todo as _canonical_create_todo

# ── Effective Review Service (single source of truth) ──────────────────────
from brain.review.effective_review_service import (
    get_effective_review_items as _effective_review_items,
    get_effective_review_summary as _effective_review_summary,
)

DB_PATH = Path(os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db")))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ── Analysis Run ───────────────────────────────────────────────────────────

def _build_run_metadata(raw_input_refs: list = None, status: str = "RUNNING",
                           outcome: str = None, error: str = None) -> str:
    """Build backward-compatible raw_input_refs JSON with lifecycle metadata.

    Format: {"refs": [...], "status": "RUNNING|COMPLETED|FAILED", "outcome": "...", "error": null}
    Old format [] is read as COMPLETED for backward compatibility.
    """
    return json.dumps({
        "refs": raw_input_refs or [],
        "status": status,
        "outcome": outcome,
        "error": error,
    })


def _parse_run_metadata(raw_input_refs_json: str) -> dict:
    """Parse raw_input_refs JSON, backward-compatible with old format []."""
    if not raw_input_refs_json:
        return {"refs": [], "status": "COMPLETED", "outcome": None, "error": None}
    try:
        data = json.loads(raw_input_refs_json)
    except (ValueError, TypeError):
        return {"refs": [], "status": "COMPLETED", "outcome": None, "error": None}
    if isinstance(data, list):
        # Old format: plain list of refs → assume COMPLETED
        return {"refs": data, "status": "COMPLETED", "outcome": None, "error": None}
    if isinstance(data, dict):
        return {
            "refs": data.get("refs", []),
            "status": data.get("status", "COMPLETED"),
            "outcome": data.get("outcome"),
            "error": data.get("error"),
        }
    return {"refs": [], "status": "COMPLETED", "outcome": None, "error": None}


def create_analysis_run(trigger_type: str, raw_text: str = "",
                         raw_input_refs: list = None,
                         analysis_model: str = "unknown") -> dict:
    run_id = str(uuid.uuid4())
    ts = now_iso()
    metadata_json = _build_run_metadata(raw_input_refs, status="RUNNING")
    with connect() as conn:
        conn.execute(
            "INSERT INTO analysis_run (id, trigger_type, raw_text, raw_input_refs, "
            "analysis_model, started_at, completed_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, trigger_type, raw_text, metadata_json,
             analysis_model, ts, ts, ts)
        )
        conn.commit()
    return {"id": run_id, "trigger_type": trigger_type, "started_at": ts, "status": "RUNNING"}


def complete_analysis_run(run_id: str, status: str = "completed",
                             outcome: str = None) -> dict:
    """Mark analysis run as COMPLETED with optional outcome.

    Args:
        status: kept for backward compatibility (ignored, always COMPLETED)
        outcome: CANDIDATE_CREATED | NO_ACTION | None
    """
    ts = now_iso()
    with connect() as conn:
        # Read current metadata
        row = conn.execute(
            "SELECT raw_input_refs FROM analysis_run WHERE id = ?", (run_id,)
        ).fetchone()
        if row:
            meta = _parse_run_metadata(row["raw_input_refs"] if isinstance(row, dict) else row[0])
            meta["status"] = "COMPLETED"
            meta["outcome"] = outcome or meta.get("outcome")
            new_metadata = json.dumps(meta)
            conn.execute(
                "UPDATE analysis_run SET completed_at = ?, raw_input_refs = ? WHERE id = ?",
                (ts, new_metadata, run_id)
            )
            conn.commit()
    return {"id": run_id, "completed_at": ts, "status": "COMPLETED", "outcome": outcome}


def fail_analysis_run(run_id: str, error: str = "") -> dict:
    """Mark analysis run as FAILED. Does NOT overwrite raw_text."""
    ts = now_iso()
    with connect() as conn:
        row = conn.execute(
            "SELECT raw_input_refs FROM analysis_run WHERE id = ?", (run_id,)
        ).fetchone()
        if row:
            meta = _parse_run_metadata(row["raw_input_refs"] if isinstance(row, dict) else row[0])
            meta["status"] = "FAILED"
            meta["outcome"] = "FAILED"
            meta["error"] = error[:500] if error else None
            new_metadata = json.dumps(meta)
            conn.execute(
                "UPDATE analysis_run SET completed_at = ?, raw_input_refs = ? WHERE id = ?",
                (ts, new_metadata, run_id)
            )
            conn.commit()
    return {"id": run_id, "failed_at": ts, "status": "FAILED", "error": error}


# ── Candidate ──────────────────────────────────────────────────────────────

def create_candidate(data: dict) -> dict:
    from brain.review.presentation_builder import build_candidate_presentation
    source_refs = data.get("source_refs", [])
    proposed_change = data.get("proposed_change", {})
    if build_candidate_presentation({
        "candidate_type": data.get("candidate_type", ""),
        "proposed_change": proposed_change,
        "source_refs": source_refs,
        "raw_excerpt": data.get("raw_excerpt", ""),
    }) is None:
        return {"error": "PRESENTATION_INVALID: candidate lacks concrete review context"}
    cid = str(uuid.uuid4())
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            "INSERT INTO candidate (id, analysis_run_id, candidate_type, "
            "source_refs, raw_excerpt, proposed_change, confidence, "
            "inference_level, recommended_action, analysis_model, "
            "status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, data["analysis_run_id"], data["candidate_type"],
             json.dumps(source_refs),
             data.get("raw_excerpt", ""),
             json.dumps(data.get("proposed_change", {})),
             data.get("confidence", "MEDIUM"),
             data.get("inference_level", "STRONG_INFERENCE"),
             data.get("recommended_action", "DRAFT_ONLY"),
             data.get("analysis_model", "unknown"),
             "pending", ts)
        )
        conn.commit()
    return {"id": cid, "status": "pending", "created_at": ts}


def list_candidates(status: str = "pending", limit: int = 100) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM candidate WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def get_candidate(cid: str) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM candidate WHERE id = ?", (cid,)
        ).fetchone()
        return dict(row) if row else None


def update_candidate_status(cid: str, status: str, reviewed_by: str,
                             notes: str = "") -> Optional[dict]:
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            "UPDATE candidate SET status = ?, reviewed_at = ?, reviewed_by = ? "
            "WHERE id = ?",
            (status, ts, reviewed_by, cid)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM candidate WHERE id = ?", (cid,)).fetchone()
        return dict(row) if row else None


def list_pending_candidates(limit: int = 100) -> list[dict]:
    """Return effective NEEDS_REVIEW candidates with presentation DTOs."""
    from brain.review.review_presentation_service import present_candidate
    items = _effective_review_items(limit=limit)
    for item in items:
        item["presentation"] = present_candidate(item)
    return items


def candidate_summary() -> dict:
    """Return effective review summary (not raw counts)."""
    _eff = _effective_review_summary()
    with connect() as conn:
        by_type = {}
        for r in conn.execute("SELECT candidate_type, COUNT(*) as c FROM candidate GROUP BY candidate_type").fetchall():
            by_type[r["candidate_type"]] = r["c"]
    return {
        "total": _eff["raw"]["total"],
        "pending": _eff["effective"]["needs_review"],
        "resolved": _eff["effective"]["resolved"],
        "reviewed_waiting_gpt": _eff["effective"].get("reviewed_waiting_gpt", 0),
        "accepted": _eff["effective"]["accepted_waiting_commit"],
        "rejected": _eff["effective"]["rejected"],
        "applied": _eff["effective"]["applied_or_committed"],
        "by_type": by_type,
        "raw": _eff["raw"],
    }


# ── Daily/Capture Processed State ──────────────────────────────────────────

def list_unprocessed_daily(limit: int = 100) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, date, title FROM daily WHERE processed_at IS NULL ORDER BY date ASC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def list_unprocessed_captures(limit: int = 100) -> list[dict]:
    import sys
    kworker = "/home/ubuntu/paw-dev/paw-ksheet-worker"
    if kworker not in sys.path:
        sys.path.insert(0, kworker)
    from paw_capture_lifecycle import list_patrol_eligible_captures

    with connect() as conn:
        rows = list_patrol_eligible_captures(conn, limit=limit, order="captured_at ASC")
        return [
            {
                "id": row["id"],
                "daily_id": row.get("daily_id"),
                "raw_text": row.get("raw_text"),
                "captured_at": row.get("captured_at"),
                "source": row.get("source"),
                "patrol_state": row.get("patrol_state"),
                "lifecycle_display_label": row.get("lifecycle_display_label"),
            }
            for row in rows
        ]


def mark_daily_processed(daily_id: str, status: str = "processed") -> bool:
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            "UPDATE daily SET processed_at = ?, updated_at = ? WHERE id = ?",
            (ts, ts, daily_id)
        )
        conn.commit()
        return True


def mark_capture_processed(capture_id: str, status: str = "processed") -> bool:
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            "UPDATE capture SET processed_at = ?, updated_at = ? WHERE id = ?",
            (ts, ts, capture_id)
        )
        conn.commit()
        return True


def mark_analysis_failed(daily_id: str = None, capture_id: str = None) -> bool:
    """Mark as failed — do NOT set processed_at, keep unprocessed for retry."""
    # Do not mark processed — leave for retry
    return True


def unprocessed_counts() -> dict:
    import sys
    kworker = "/home/ubuntu/paw-dev/paw-ksheet-worker"
    if kworker not in sys.path:
        sys.path.insert(0, kworker)
    from paw_capture_lifecycle import count_patrol_eligible_captures

    with connect() as conn:
        daily = conn.execute(
            "SELECT COUNT(*) FROM daily WHERE processed_at IS NULL"
        ).fetchone()[0]
        capture = count_patrol_eligible_captures(conn)
        return {"daily": daily, "capture": capture, "total": daily + capture}


# ── AI Activity ────────────────────────────────────────────────────────────

def list_ai_activity(limit: int = 50) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT ar.id, ar.trigger_type, ar.analysis_model, ar.started_at, ar.completed_at, "
            "(SELECT COUNT(*) FROM candidate c WHERE c.analysis_run_id = ar.id) as candidate_count "
            "FROM analysis_run ar ORDER BY ar.started_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


# ── Todo Operations ────────────────────────────────────────────────────────

def create_todo(data: dict) -> dict:
    """Create a todo in the existing todo table.

    Delegates to the single canonical Todo writer
    (workspace_api/repository.py:create_todo_in_transaction) so there is
    exactly one real INSERT INTO todo implementation.
    """
    return _canonical_create_todo(data)


def find_duplicate_todo(title: str, project_id: str = None) -> bool:
    with connect() as conn:
        if project_id:
            row = conn.execute(
                "SELECT id FROM todo WHERE title = ? AND project_id = ? AND status != 'done'",
                (title, project_id)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM todo WHERE title = ? AND status != 'done'",
                (title,)
            ).fetchone()
        return row is not None


# ── Project Context ────────────────────────────────────────────────────────

def project_brain_context(project_id: str) -> Optional[dict]:
    with connect() as conn:
        proj = conn.execute(
            "SELECT * FROM project WHERE id = ?", (project_id,)
        ).fetchone()
        if not proj:
            return None
        open_todos = conn.execute(
            "SELECT id, title, status, priority FROM todo WHERE project_id = ? AND status != 'done' ORDER BY created_at DESC",
            (project_id,)
        ).fetchall()
        recent_done = conn.execute(
            "SELECT id, title, completed_at FROM todo WHERE project_id = ? AND status = 'done' ORDER BY completed_at DESC LIMIT 10",
            (project_id,)
        ).fetchall()
        domain_id = proj["domain_id"] if "domain_id" in proj.keys() else None
        draft_knowledge = conn.execute(
            "SELECT id, title FROM knowledge_page WHERE domain_id = ? AND status = 'draft'",
            (domain_id,)
        ).fetchall() if domain_id else []
        confirmed_knowledge = conn.execute(
            "SELECT id, title FROM knowledge_page WHERE domain_id = ? AND status = 'current'",
            (domain_id,)
        ).fetchall() if domain_id else []
        source_links = conn.execute(
            "SELECT COUNT(*) as total, COUNT(DISTINCT source_page_id) as unique_sources "
            "FROM knowledge_source_link WHERE knowledge_id IN "
            "(SELECT id FROM knowledge_page WHERE domain_id = ?)",
            (domain_id,)
        ).fetchone() if domain_id else {"total": 0, "unique_sources": 0}
        candidates = conn.execute(
            "SELECT COUNT(*) as total, SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) as pending "
            "FROM candidate WHERE proposed_change LIKE ?",
            (f'%{project_id}%',)
        ).fetchone()
        return {
            "project": dict(proj),
            "open_todos": [dict(r) for r in open_todos],
            "recent_done": [dict(r) for r in recent_done],
            "draft_knowledge": [dict(r) for r in draft_knowledge],
            "confirmed_knowledge": [dict(r) for r in confirmed_knowledge],
            "knowledge_source_summary": {
                "link_total": source_links["total"],
                "unique_source_total": source_links["unique_sources"],
            },
            "candidate_summary": {
                "total": candidates["total"],
                "pending": candidates["pending"],
            },
        }
