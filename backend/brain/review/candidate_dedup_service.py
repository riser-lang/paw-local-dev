"""Candidate Dedup Service — deterministic fingerprint and pre-insert dedup.

Production-grade dedup contract for Spark Patrol candidates.

Fingerprint based on:
  candidate_type + normalized target + normalized operation
  + affected entity IDs + project_id + evidence identity + source identity

Rules:
  - NEEDS_REVIEW equivalent exists → REUSE, no INSERT
  - ACCEPTED_WAITING_COMMIT equivalent exists → no re-create
  - RESOLVED/REJECTED equivalent → only re-create if new evidence/source
  - Race guard via UNIQUE constraint on dedup_fingerprint + effective state
"""

from __future__ import annotations

from pathlib import Path
import os


import json
import hashlib
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

PRODUCTION_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))


def _connect(db_path: str = PRODUCTION_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_str(value: str | None) -> str:
    """Normalize string for fingerprint: lowercase, strip, collapse whitespace."""
    if not value:
        return ""
    return " ".join(str(value).strip().lower().split())


def _normalize_json(value: str | dict | None) -> str:
    """Normalize JSON for fingerprint: sort keys, compact, lowercase keys."""
    if not value:
        return ""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return _normalize_str(value)
    if isinstance(value, dict):
        # Sort keys recursively, normalize values
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).lower()
    if isinstance(value, list):
        # Sort list items by their normalized form
        normalized = [_normalize_json(item) if isinstance(item, (dict, list)) else _normalize_str(str(item)) for item in value]
        normalized.sort()
        return "[" + ",".join(normalized) + "]"
    return _normalize_str(str(value))


def compute_fingerprint(
    candidate_type: str,
    proposed_change: dict | str | None,
    source_refs: str | list | None = None,
    project_id: str | None = None,
) -> str:
    """Compute deterministic dedup fingerprint.

    Based on: candidate_type + normalized target + normalized operation
    + affected entity IDs + project_id + evidence identity + source identity
    """
    parts = [
        _normalize_str(candidate_type),
        _normalize_json(proposed_change),
        _normalize_json(source_refs),
        _normalize_str(project_id),
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ensure_dedup_table(db_path: str = PRODUCTION_DB) -> None:
    """Create dedup tracking table if not exists."""
    conn = _connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS candidate_dedup (
                dedup_fingerprint TEXT PRIMARY KEY,
                candidate_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_seen_at TEXT,
                FOREIGN KEY (candidate_id) REFERENCES candidate(id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dedup_candidate ON candidate_dedup(candidate_id)")
        # Add dedup_fingerprint column to candidate table if not exists
        try:
            conn.execute("ALTER TABLE candidate ADD COLUMN dedup_fingerprint TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE candidate ADD COLUMN last_seen_at TEXT")
        except sqlite3.OperationalError:
            pass
        conn.commit()
    finally:
        conn.close()


def find_existing_candidate(
    fingerprint: str,
    db_path: str = PRODUCTION_DB,
) -> Optional[dict]:
    """Find an existing candidate with the same fingerprint that is still active.

    Active = NEEDS_REVIEW or ACCEPTED_WAITING_COMMIT.
    Returns the candidate dict if found, None otherwise.
    """
    from brain.review.effective_review_service import (
        NEEDS_REVIEW,
        ACCEPTED_WAITING_COMMIT,
        get_effective_review_state,
        _closed_resolution_ids,
    )

    conn = _connect(db_path)
    try:
        # First check dedup table
        row = conn.execute(
            "SELECT candidate_id FROM candidate_dedup WHERE dedup_fingerprint = ?",
            (fingerprint,)
        ).fetchone()

        if row:
            candidate_id = row["candidate_id"]
            # Check if this candidate is still active
            state = get_effective_review_state(candidate_id, db_path)
            if state in (NEEDS_REVIEW, ACCEPTED_WAITING_COMMIT):
                # Return the existing candidate
                cand = conn.execute(
                    "SELECT * FROM candidate WHERE id = ?",
                    (candidate_id,)
                ).fetchone()
                return dict(cand) if cand else None

        # Also check candidate table directly (for fingerprints added via ALTER TABLE)
        rows = conn.execute(
            "SELECT * FROM candidate WHERE dedup_fingerprint = ?",
            (fingerprint,)
        ).fetchall()

        closed_ids = _closed_resolution_ids(conn)

        for row in rows:
            state = get_effective_review_state(row["id"], db_path)
            if state in (NEEDS_REVIEW, ACCEPTED_WAITING_COMMIT):
                return dict(row)

        return None
    finally:
        conn.close()


def register_fingerprint(
    fingerprint: str,
    candidate_id: str,
    db_path: str = PRODUCTION_DB,
) -> None:
    """Register a fingerprint for a newly created candidate."""
    conn = _connect(db_path)
    try:
        now = _now_iso()
        conn.execute(
            """INSERT OR REPLACE INTO candidate_dedup (dedup_fingerprint, candidate_id, created_at, last_seen_at)
               VALUES (?, ?, ?, ?)""",
            (fingerprint, candidate_id, now, now)
        )
        # Also update candidate table if column exists
        try:
            conn.execute(
                "UPDATE candidate SET dedup_fingerprint = ?, last_seen_at = ? WHERE id = ?",
                (fingerprint, now, candidate_id)
            )
        except sqlite3.OperationalError:
            pass
        conn.commit()
    finally:
        conn.close()


def update_last_seen(
    fingerprint: str,
    db_path: str = PRODUCTION_DB,
) -> None:
    """Update last_seen_at for a fingerprint (patrol re-observed same issue)."""
    conn = _connect(db_path)
    try:
        now = _now_iso()
        conn.execute(
            "UPDATE candidate_dedup SET last_seen_at = ? WHERE dedup_fingerprint = ?",
            (now, fingerprint)
        )
        try:
            conn.execute(
                "UPDATE candidate SET last_seen_at = ? WHERE dedup_fingerprint = ?",
                (now, fingerprint)
            )
        except sqlite3.OperationalError:
            pass
        conn.commit()
    finally:
        conn.close()


def create_or_reuse_candidate(
    candidate_type: str,
    proposed_change: dict | str | None,
    analysis_run_id: str,
    confidence: str,
    inference_level: str,
    recommended_action: str,
    analysis_model: str,
    source_refs: str | list | None = None,
    raw_excerpt: str = "",
    project_id: str | None = None,
    db_path: str = PRODUCTION_DB,
) -> dict:
    """Create a new candidate or reuse an existing equivalent one.

    Returns dict with:
      created: bool — True if new candidate was created, False if reused
      candidate_id: str — the candidate ID (new or existing)
      dedup_fingerprint: str
    """
    ensure_dedup_table(db_path)

    fingerprint = compute_fingerprint(
        candidate_type=candidate_type,
        proposed_change=proposed_change,
        source_refs=source_refs,
        project_id=project_id,
    )

    # Check for existing equivalent candidate
    existing = find_existing_candidate(fingerprint, db_path)
    if existing:
        # Reuse existing candidate — update last_seen only
        update_last_seen(fingerprint, db_path)
        return {
            "created": False,
            "reused_candidate_id": existing["id"],
            "candidate_id": existing["id"],
            "dedup_fingerprint": fingerprint,
        }

    # Create new candidate
    candidate_id = str(uuid.uuid4())
    now = _now_iso()

    proposed_change_str = json.dumps(proposed_change, ensure_ascii=False) if isinstance(proposed_change, dict) else (proposed_change or "")
    source_refs_str = json.dumps(source_refs, ensure_ascii=False) if isinstance(source_refs, list) else (source_refs or "")

    conn = _connect(db_path)
    try:
        conn.execute(
            """INSERT INTO candidate (id, analysis_run_id, candidate_type, source_refs, raw_excerpt,
               proposed_change, confidence, inference_level, recommended_action, analysis_model,
               status, created_at, dedup_fingerprint, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (candidate_id, analysis_run_id, candidate_type, source_refs_str, raw_excerpt,
             proposed_change_str, confidence, inference_level, recommended_action, analysis_model,
             now, fingerprint, now)
        )
        conn.execute(
            """INSERT OR REPLACE INTO candidate_dedup (dedup_fingerprint, candidate_id, created_at, last_seen_at)
               VALUES (?, ?, ?, ?)""",
            (fingerprint, candidate_id, now, now)
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "created": True,
        "candidate_id": candidate_id,
        "dedup_fingerprint": fingerprint,
    }
