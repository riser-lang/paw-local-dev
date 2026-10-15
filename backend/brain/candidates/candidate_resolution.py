"""Candidate Resolution store — additive review semantics (Phase E / Review C2).

Design (per PAW_LONGRUN_REVIEW_AND_TRUSTED_SOURCE_PIPELINE_V1):

- The sealed Candidate state machine (pending → accepted/rejected/applied) is
  UNTOUCHED. A clarification/conflict candidate that the user resolved stays
  status='pending' in `candidate` but gets an independent resolution row, so it
  leaves the "pending review" list without ever being mislabelled 'accepted'.
- Resolution semantic statuses: open → resolved | ignored. A failed follow-up
  analysis is retained as `reanalysis_failed` diagnostics, but a valid user
  response is still closed to the human review queue and handed to GPT patrol.
- Storage is a NEW additive table `candidate_resolution` — no destructive
  migration, no change to `candidate` columns.

Schema (additive):

    candidate_resolution (
        id              TEXT PRIMARY KEY,
        candidate_id    TEXT NOT NULL,
        resolution_type TEXT NOT NULL,      -- clarification | conflict
        resolution_status TEXT NOT NULL,    -- open | resolved | ignored
        user_response   TEXT,               -- user clarification / custom response
        selected_option TEXT,               -- conflict: A | B | NEITHER
        reanalysis_run_id TEXT,             -- analysis_run of the follow-up pass
        resolved_by     TEXT,
        created_at      TEXT NOT NULL,
        resolved_at     TEXT
    )

This module is read/write on the workspace DB for the resolution table only.
It never touches Todo / Project / Knowledge / Capture and never calls the
canonical writers.
"""

from __future__ import annotations

from pathlib import Path
import os


import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

PRODUCTION_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))

VALID_RESOLUTION_TYPES = ("clarification", "conflict")
VALID_RESOLUTION_STATUSES = ("open", "resolved", "ignored", "reanalysis_failed")
VALID_CONFLICT_OPTIONS = ("A", "B", "NEITHER")

# Clarification/conflict candidate types that use resolution semantics.
CLARIFICATION_TYPES = ("ambiguity", "question")
CONFLICT_TYPES = ("conflict",)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_table(db_path: str = PRODUCTION_DB) -> None:
    """Additive CREATE TABLE IF NOT EXISTS — safe to run on production."""
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            """CREATE TABLE IF NOT EXISTS candidate_resolution (
                id TEXT PRIMARY KEY,
                candidate_id TEXT NOT NULL,
                resolution_type TEXT NOT NULL,
                resolution_status TEXT NOT NULL,
                user_response TEXT,
                selected_option TEXT,
                reanalysis_run_id TEXT,
                resolved_by TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            )"""
        )
        columns = {row[1] for row in con.execute("PRAGMA table_info(candidate_resolution)")}
        if "reanalysis_error" not in columns:
            con.execute("ALTER TABLE candidate_resolution ADD COLUMN reanalysis_error TEXT")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_candres_candidate ON candidate_resolution(candidate_id)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_candres_status ON candidate_resolution(resolution_status)"
        )
        con.commit()
    finally:
        con.close()


def create_resolution(
    db_path: str,
    candidate_id: str,
    resolution_type: str,
    resolution_status: str = "resolved",
    user_response: Optional[str] = None,
    selected_option: Optional[str] = None,
    resolved_by: str = "user",
) -> dict:
    """Create a resolution row for a candidate.

    One resolution per candidate: if a non-open resolution already exists,
    returns already_resolved (idempotent guard — the re-analysis must never
    duplicate). If an 'open' row exists, it is upgraded to the given status.
    """
    ensure_table(db_path)
    if resolution_type not in VALID_RESOLUTION_TYPES:
        return {"error": f"invalid resolution_type: {resolution_type}"}
    if resolution_status not in VALID_RESOLUTION_STATUSES:
        return {"error": f"invalid resolution_status: {resolution_status}"}
    if resolution_type == "conflict" and selected_option not in VALID_CONFLICT_OPTIONS:
        return {"error": f"invalid selected_option: {selected_option}. Allowed: A|B|NEITHER"}

    existing = get_resolution(db_path, candidate_id)
    if existing:
        existing_status = existing["resolution_status"]
        has_response = bool(str(existing.get("user_response") or "").strip()) or existing.get("selected_option") is not None
        # A durable human response stays closed even if later reanalysis failed.
        # Never overwrite the saved answer just because GPT retry is pending.
        if has_response and existing_status in ("resolved", "ignored", "reanalysis_failed"):
            return {"error": "already_resolved", "resolution": existing}
        if existing_status not in ("open", "reanalysis_failed"):
            return {"error": "already_resolved", "resolution": existing}
        # Upgrade open → resolved/ignored (or retry an empty failed row)
        now = _now()
        con = sqlite3.connect(db_path)
        try:
            con.execute(
                """UPDATE candidate_resolution SET resolution_status = ?, user_response = ?,
                   selected_option = ?, resolved_by = ?, resolved_at = ? WHERE candidate_id = ?""",
                (resolution_status, user_response, selected_option, resolved_by, now, candidate_id),
            )
            con.commit()
        finally:
            con.close()
        resolved = get_resolution(db_path, candidate_id)
        return {"resolution": resolved, "upgraded": True}

    rid = "res-" + uuid.uuid4().hex[:12]
    now = _now()
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            """INSERT INTO candidate_resolution
               (id, candidate_id, resolution_type, resolution_status, user_response,
                selected_option, reanalysis_run_id, resolved_by, created_at, resolved_at)
               VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)""",
                (rid, candidate_id, resolution_type, resolution_status, user_response,
                 selected_option, resolved_by, now, now if resolution_status not in ("open", "reanalysis_failed") else None),
        )
        con.commit()
    finally:
        con.close()
    return {"resolution": get_resolution(db_path, candidate_id)}


def get_resolution(db_path: str, candidate_id: str) -> Optional[dict]:
    """Return the resolution row for a candidate, or None."""
    ensure_table(db_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT * FROM candidate_resolution WHERE candidate_id = ? ORDER BY created_at DESC LIMIT 1",
            (candidate_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def closed_resolution_ids(db_path: str = PRODUCTION_DB) -> set[str]:
    """Ids of candidates closed to human review (including failed reanalysis).

    Used by the web review list to exclude them from "pending" without touching
    the sealed candidate state machine.
    """
    ensure_table(db_path)
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT candidate_id FROM candidate_resolution WHERE resolution_status IN ('resolved','ignored','reanalysis_failed')"
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        con.close()


def attach_reanalysis(db_path: str, candidate_id: str, analysis_run_id: str) -> None:
    """Link the follow-up analysis_run to the resolution row (update in place)."""
    ensure_table(db_path)
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "UPDATE candidate_resolution SET reanalysis_run_id = ?, resolution_status = 'resolved', reanalysis_error = NULL WHERE candidate_id = ?",
            (analysis_run_id, candidate_id),
        )
        con.commit()
    finally:
        con.close()


def mark_reanalysis_failed(db_path: str, candidate_id: str, error: str) -> None:
    """Retain failure diagnostics while keeping the user response GPT-owned."""
    ensure_table(db_path)
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "UPDATE candidate_resolution SET resolution_status = 'reanalysis_failed', reanalysis_error = ?, resolved_at = COALESCE(resolved_at, ?) WHERE candidate_id = ?",
            (str(error)[:500], _now(), candidate_id),
        )
        con.commit()
    finally:
        con.close()


def pending_closed_counts(db_path: str = PRODUCTION_DB) -> dict:
    """Counts of still-'pending' candidates whose resolution is closed.

    The sealed candidate_summary counts by status only; the web layer subtracts
    these so resolved clarifications/conflicts leave "needs attention".
    """
    ensure_table(db_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """SELECT r.candidate_id, c.candidate_type
               FROM candidate_resolution r JOIN candidate c ON c.id = r.candidate_id
               WHERE r.resolution_status IN ('resolved','ignored','reanalysis_failed') AND c.status = 'pending'"""
        ).fetchall()
        total = len(rows)
        by_type: dict[str, int] = {}
        for row in rows:
            ct = row["candidate_type"] or "unknown"
            by_type[ct] = by_type.get(ct, 0) + 1
        return {"total": total, "by_type": by_type}
    finally:
        con.close()


def gpt_handoff_items(db_path: str = PRODUCTION_DB, limit: int = 100) -> list[dict]:
    """Return durable human responses awaiting GPT patrol/recovery.

    This is an additive handoff projection. It deliberately exposes only the
    response and safe lifecycle diagnostics needed by GPT; it never mutates a
    Candidate or bypasses Review → GPT → Commit.
    """
    ensure_table(db_path)
    try:
        bounded_limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        bounded_limit = 100
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """SELECT r.candidate_id, c.candidate_type, c.status AS candidate_status,
                      r.resolution_type, r.resolution_status, r.user_response,
                      r.selected_option, r.reanalysis_run_id, r.reanalysis_error,
                      r.created_at, r.resolved_at
                 FROM candidate_resolution r
                 JOIN candidate c ON c.id = r.candidate_id
                WHERE r.resolution_status IN ('resolved','reanalysis_failed')
                  AND (NULLIF(TRIM(COALESCE(r.user_response, '')), '') IS NOT NULL
                       OR r.selected_option IS NOT NULL)
                ORDER BY COALESCE(r.resolved_at, r.created_at) DESC
                LIMIT ?""",
            (bounded_limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        con.close()
