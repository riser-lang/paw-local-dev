"""Candidate Repository — Production data access for Candidate Store.

Handles:
  - create_candidate (with validation + deduplication)
  - get_candidate
  - list_candidates (with filtering)
  - update_candidate_status (accept/reject/edit_and_accept)
  - update_candidate_payload
  - candidate_summary

Safety:
  - NEVER sets status=applied (that requires future applyWorkspaceDecision)
  - NEVER writes to canonical tables (todo/project/knowledge/etc)
  - All SQL parameterized
  - Evidence required for every candidate
  - Deduplication via fingerprint
"""

from __future__ import annotations

from pathlib import Path

import sqlite3
import json
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

PRODUCTION_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))

VALID_CANDIDATE_TYPES = {"fact", "todo", "project_update", "knowledge", "conflict", "ambiguity", "question"}
VALID_STATUSES = {"pending", "accepted", "rejected"}  # 'applied' NOT allowed this checkpoint
VALID_WRITE_ACTIONS = {"AUTO_WRITE", "DRAFT_ONLY", "ASK_USER", "DO_NOT_WRITE"}

# Write action rules by type
WRITE_ACTION_RULES = {
    "fact": "DRAFT_ONLY",
    "todo": "DRAFT_ONLY",
    "project_update": "DRAFT_ONLY",
    "knowledge": "DRAFT_ONLY",
    "conflict": "ASK_USER",
    "ambiguity": "ASK_USER",
    "question": "ASK_USER",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_ro_connection(db_path: str = PRODUCTION_DB) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row
    return con


def compute_candidate_fingerprint(
    candidate_type: str,
    source_refs: list[str],
    proposed_change: dict,
    analysis_run_id: str = "",
) -> str:
    """Deterministic fingerprint for deduplication.
    
    NOTE: analysis_run_id is intentionally NOT included in the fingerprint
    because it changes every patrol run. The fingerprint describes the
    'real-world proposal itself', not the specific run that found it.
    """
    refs_sorted = sorted(source_refs or [])
    change_str = json.dumps(proposed_change or {}, sort_keys=True, ensure_ascii=False)
    fp_input = f"{candidate_type}|{','.join(refs_sorted)}|{change_str}"
    return hashlib.sha256(fp_input.encode()).hexdigest()


def create_temp_candidate_db() -> str:
    """Create temp DB with candidate + analysis_run tables for testing."""
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".db", prefix="candidate_test_")
    os.close(fd)
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE analysis_run (
        id TEXT PRIMARY KEY, trigger_type TEXT NOT NULL, raw_text TEXT,
        raw_input_refs TEXT, analysis_model TEXT NOT NULL,
        started_at TEXT NOT NULL, completed_at TEXT NOT NULL, created_at TEXT NOT NULL
    )""")
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


def validate_candidate_input(
    candidate_type: str,
    recommended_action: str,
    source_refs: list[str],
    confidence: str,
    analysis_run_id: str,
    db_path: str = PRODUCTION_DB,
) -> tuple[bool, str]:
    """Validate candidate before persistence."""
    if candidate_type not in VALID_CANDIDATE_TYPES:
        return False, f"Invalid candidate_type: {candidate_type}"
    if recommended_action not in VALID_WRITE_ACTIONS:
        return False, f"Invalid recommended_action: {recommended_action}"
    # Enforce write action rules
    expected_action = WRITE_ACTION_RULES.get(candidate_type, "DRAFT_ONLY")
    if recommended_action != expected_action:
        return False, f"candidate_type={candidate_type} must use {expected_action}, got {recommended_action}"
    if not source_refs:
        return False, "Evidence required: source_refs cannot be empty"
    if confidence not in ("HIGH", "MEDIUM", "LOW"):
        return False, f"Invalid confidence: {confidence}"
    if not analysis_run_id:
        return False, "analysis_run_id required"
    # Check analysis_run exists (only on production DB, skip for temp)
    if db_path == PRODUCTION_DB:
        try:
            con = _get_ro_connection(db_path)
            row = con.execute("SELECT id FROM analysis_run WHERE id = ?", (analysis_run_id,)).fetchone()
            con.close()
            if not row:
                return False, f"analysis_run {analysis_run_id} not found"
        except Exception:
            pass  # Skip if can't verify
    return True, ""


def check_duplicate(
    fingerprint: str,
    db_path: str = PRODUCTION_DB,
) -> tuple[bool, Optional[str]]:
    """Check if a candidate with same fingerprint exists (pending or accepted)."""
    try:
        con = _get_ro_connection(db_path) if db_path == PRODUCTION_DB else sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        # Check source_refs JSON for fingerprint match
        rows = con.execute(
            "SELECT id, status, source_refs FROM candidate WHERE status IN ('pending','accepted')"
        ).fetchall()
        for row in rows:
            refs = row["source_refs"] or ""
            try:
                refs_data = json.loads(refs) if refs else {}
                if isinstance(refs_data, dict) and refs_data.get("fingerprint") == fingerprint:
                    return True, row["id"]
            except (json.JSONDecodeError, TypeError):
                continue
        con.close()
        return False, None
    except Exception:
        return False, None


def create_candidate(
    db_path: str,
    analysis_run_id: str,
    candidate_type: str,
    source_refs: list[str],
    proposed_change: dict,
    confidence: str,
    inference_level: str,
    recommended_action: str,
    analysis_model: str,
    raw_excerpt: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> dict:
    """Create a candidate with validation and production dedup gate.
    
    All candidate writers MUST go through this function.
    Uses candidate_dedup_service for race-safe deduplication.
    Returns dict with id, status, or skipped/duplicate info.
    """
    # Validate
    valid, err = validate_candidate_input(
        candidate_type, recommended_action, source_refs, confidence, analysis_run_id, db_path
    )
    if not valid:
        return {"error": err}

    # Presentation is a creation gate: a pending Candidate must be
    # decision-capable from its immutable payload.  This is a pure check and
    # does not attach or rewrite review_brief_json.
    from brain.review.presentation_builder import build_candidate_presentation
    presentation = build_candidate_presentation({
        "candidate_type": candidate_type,
        "proposed_change": proposed_change,
        "source_refs": source_refs,
        "raw_excerpt": raw_excerpt or "",
    })
    if presentation is None:
        return {"error": "PRESENTATION_INVALID: candidate lacks concrete review context"}
    # USER_REVIEWABLE creation gate (fail closed): a candidate that will be
    # routed to the user (ASK_USER / DECISION_ONLY) must be a real, decidable
    # business question.  System-side evidence deficiencies must not become
    # fake NEEDS_REVIEW items the user cannot decide.  DRAFT_ONLY and other
    # non-user-review candidates are unaffected.
    if str(recommended_action or "").upper() in {"ASK_USER", "DECISION_ONLY"}:
        import os as _os
        import sys as _sys
        # Runtime module root comes from the explicit PAW Runtime Path
        # Contract (/etc/paw-runtime-paths.env).  The original code used a
        # literal development path and never read PAW_KSHEET_WORKER_ROOT,
        # so that name is deliberately not consulted here.
        _path = _os.environ.get("PAW_RUNTIME_MODULE_ROOT")
        if not _path:
            raise RuntimeError(
                "PAW_RUNTIME_MODULE_ROOT is required "
                "(PAW Runtime Path Contract, /etc/paw-runtime-paths.env)"
            )
        if _path not in _sys.path:
            _sys.path.insert(0, _path)
        from paw_review_display_normalization import classify_candidate_routing
        routing, routing_reasons = classify_candidate_routing(
            candidate_type=candidate_type,
            raw_excerpt=raw_excerpt or "",
            brief=None,
            display=presentation,
        )
        if routing != "USER_REVIEWABLE":
            return {
                "error": "NOT_USER_REVIEWABLE: candidate lacks a decidable business question",
                "routing": routing,
                "reasons": routing_reasons,
            }

    # Compute fingerprint if not provided (analysis_run_id excluded)
    if not fingerprint:
        fingerprint = compute_candidate_fingerprint(candidate_type, source_refs, proposed_change)

    # Production Dedup Gate via candidate_dedup_service
    from brain.review.candidate_dedup_service import create_or_reuse_candidate, ensure_dedup_table
    ensure_dedup_table(db_path)
    
    result = create_or_reuse_candidate(
        candidate_type=candidate_type,
        proposed_change=proposed_change,
        analysis_run_id=analysis_run_id,
        confidence=confidence,
        inference_level=inference_level,
        recommended_action=recommended_action,
        analysis_model=analysis_model,
        source_refs=source_refs,
        raw_excerpt=raw_excerpt or "",
        project_id=None,
        db_path=db_path,
    )
    
    if not result.get("created"):
        # Reused existing candidate
        return {
            "skipped": True,
            "reason": "duplicate",
            "existing_id": result.get("candidate_id"),
            "reused_candidate_id": result.get("candidate_id"),
            "fingerprint": fingerprint,
            "created": False,
        }
    
    candidate_id = result["candidate_id"]
    now = _now()
    
    # Also store fingerprint in source_refs JSON for backward compatibility
    refs_meta = {"refs": source_refs, "fingerprint": fingerprint}
    
    # Update source_refs with fingerprint metadata
    con = sqlite3.connect(db_path)
    con.execute(
        "UPDATE candidate SET source_refs = ? WHERE id = ?",
        (json.dumps(refs_meta), candidate_id)
    )
    con.commit()
    con.close()

    return {
        "id": candidate_id,
        "status": "pending",
        "fingerprint": fingerprint,
        "created_at": now,
        "created": True,
        "dedup_fingerprint": result.get("dedup_fingerprint"),
    }


def get_candidate(candidate_id: str, db_path: str = PRODUCTION_DB) -> Optional[dict]:
    """Get candidate by ID."""
    try:
        con = _get_ro_connection(db_path) if db_path == PRODUCTION_DB else sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM candidate WHERE id = ?", (candidate_id,)).fetchone()
        con.close()
        if not row:
            return None
        result = dict(row)
        # Parse JSON fields
        for field in ("source_refs", "proposed_change"):
            if result.get(field):
                try:
                    result[field] = json.loads(result[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        return result
    except Exception:
        return None


def list_candidates(
    status: str = "pending",
    candidate_type: Optional[str] = None,
    limit: int = 100,
    db_path: str = PRODUCTION_DB,
) -> list[dict]:
    """List candidates with filtering."""
    try:
        con = _get_ro_connection(db_path) if db_path == PRODUCTION_DB else sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        query = "SELECT * FROM candidate WHERE status = ?"
        params = [status]
        if candidate_type:
            query += " AND candidate_type = ?"
            params.append(candidate_type)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = con.execute(query, params).fetchall()
        con.close()
        results = []
        for row in rows:
            item = dict(row)
            for field in ("source_refs", "proposed_change"):
                if item.get(field):
                    try:
                        item[field] = json.loads(item[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            results.append(item)
        return results
    except Exception:
        return []


def update_candidate_status(
    db_path: str,
    candidate_id: str,
    new_status: str,
    reviewed_by: str = "user",
    review_notes: Optional[str] = None,
    edited_payload: Optional[dict] = None,
    review_origin: str = "api",
) -> dict:
    """Update candidate status (accept/reject/edit_and_accept).
    NEVER sets status=applied.
    Preserves original payload for audit trail.
    """
    if new_status not in VALID_STATUSES:
        return {"error": f"Invalid status: {new_status}. Allowed: {VALID_STATUSES}"}

    candidate = get_candidate(candidate_id, db_path)
    if not candidate:
        return {"error": "candidate_not_found"}

    if candidate["status"] != "pending":
        return {"error": f"candidate already {candidate['status']}", "current_status": candidate["status"]}

    now = _now()
    con = sqlite3.connect(db_path)

    # If edit_and_accept, preserve original payload
    if new_status == "accepted" and edited_payload is not None:
        # Store original + reviewed in proposed_change
        original_change = candidate.get("proposed_change", {})
        if isinstance(original_change, str):
            try:
                original_change = json.loads(original_change)
            except (json.JSONDecodeError, TypeError):
                original_change = {}

        review_meta = {
            "original_payload": original_change,
            "reviewed_payload": edited_payload,
            "review_metadata": {
            "reviewed_by": reviewed_by,
            "reviewed_at": now,
            "review_notes": review_notes,
            "review_origin": review_origin,
            "action": "edit_and_accept",
            }
        }
        con.execute(
            """UPDATE candidate SET status = ?, reviewed_at = ?, reviewed_by = ?,
               proposed_change = ? WHERE id = ?""",
            (new_status, now, reviewed_by, json.dumps(review_meta, ensure_ascii=False), candidate_id)
        )
    else:
        # Simple accept or reject
        review_meta = {
            "reviewed_by": reviewed_by,
            "reviewed_at": now,
            "review_notes": review_notes,
            "review_origin": review_origin,
            "action": new_status,
        }
        # Store review metadata in proposed_change (preserving original)
        original_change = candidate.get("proposed_change", {})
        if isinstance(original_change, str):
            try:
                original_change = json.loads(original_change)
            except (json.JSONDecodeError, TypeError):
                original_change = {}

        if isinstance(original_change, dict) and "reviewed_payload" in original_change:
            # Already an edited draft (edit_draft flow): preserve the wrapper so
            # Apply keeps using the user-edited payload — never double-wrap.
            original_change["review_metadata"] = review_meta
            updated_change = original_change
        else:
            updated_change = {
                "original_payload": original_change,
                "review_metadata": review_meta,
            }
        con.execute(
            """UPDATE candidate SET status = ?, reviewed_at = ?, reviewed_by = ?,
               proposed_change = ? WHERE id = ?""",
            (new_status, now, reviewed_by, json.dumps(updated_change, ensure_ascii=False), candidate_id)
        )

    con.commit()
    con.close()

    return {
        "candidate_id": candidate_id,
        "status": new_status,
        "reviewed_at": now,
        "reviewed_by": reviewed_by,
        "canonical_applied": False,  # NEVER applied this checkpoint
    }


def candidate_summary(db_path: str = PRODUCTION_DB) -> dict:
    """Get summary counts for Confirmation Center UI."""
    try:
        con = _get_ro_connection(db_path) if db_path == PRODUCTION_DB else sqlite3.connect(db_path)
        cur = con.cursor()
        pending = cur.execute("SELECT COUNT(*) FROM candidate WHERE status='pending'").fetchone()[0]
        accepted = cur.execute("SELECT COUNT(*) FROM candidate WHERE status='accepted'").fetchone()[0]
        rejected = cur.execute("SELECT COUNT(*) FROM candidate WHERE status='rejected'").fetchone()[0]
        # By type (pending only)
        by_type = {}
        for ct in VALID_CANDIDATE_TYPES:
            count = cur.execute(
                "SELECT COUNT(*) FROM candidate WHERE status='pending' AND candidate_type=?", (ct,)
            ).fetchone()[0]
            if count > 0:
                by_type[ct] = count
        con.close()
        return {
            "pending_total": pending,
            "accepted_total": accepted,
            "rejected_total": rejected,
            "by_type": by_type,
            "needs_user_attention": pending,
        }
    except Exception as e:
        return {"error": str(e)}
