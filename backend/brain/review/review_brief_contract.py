"""Review Brief Contract — formal human-readable review explanation.

review_brief is a frozen audit explanation attached to a Candidate at creation time.
It represents 'the version the user saw and decided upon'.

- Must not be silently overwritten after creation.
- Must not introduce actions not present in proposed_change.
- Must be valid for any Candidate entering NEEDS_REVIEW.

Schema:
  {
    "schema_version": "1",
    "title": "...",
    "situation": "...",
    "decision_question": "...",
    "recommendation": "...",
    "approval_effect": "...",
    "review_mode": "DECISION_ONLY | EXECUTION_READY",
    "affected_items": [{"label", "current_state", "proposed_state"}],
    "decision_options": [{"key", "label", "description", "meaning"}],
    "evidence_summary": ["..."],
    "attention_points": ["..."]
  }
"""

from __future__ import annotations

from pathlib import Path
import os


import json
import sqlite3
from typing import Any, Optional

PRODUCTION_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))

REQUIRED_FIELDS = {
    "schema_version",
    "title",
    "situation",
    "decision_question",
    "recommendation",
    "approval_effect",
    "review_mode",
    "affected_items",
    "evidence_summary",
}

VALID_REVIEW_MODES = {"DECISION_ONLY", "EXECUTION_READY"}


def ensure_review_brief_column(db_path: str = PRODUCTION_DB) -> None:
    """Add review_brief_json column to candidate table if not exists."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("ALTER TABLE candidate ADD COLUMN review_brief_json TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists
    finally:
        conn.close()


def validate_review_brief(brief: dict | str | None) -> tuple[bool, str]:
    """Validate a review_brief against the contract.
    
    Returns (valid, error_message).
    """
    if brief is None:
        return False, "review_brief is required for NEEDS_REVIEW candidates"
    
    if isinstance(brief, str):
        try:
            brief = json.loads(brief)
        except (json.JSONDecodeError, TypeError):
            return False, "review_brief is not valid JSON"
    
    if not isinstance(brief, dict):
        return False, "review_brief must be a JSON object"
    
    # Check required fields
    missing = REQUIRED_FIELDS - set(brief.keys())
    if missing:
        return False, f"review_brief missing required fields: {missing}"
    
    # Check review_mode
    if brief["review_mode"] not in VALID_REVIEW_MODES:
        return False, f"review_mode must be one of {VALID_REVIEW_MODES}, got: {brief['review_mode']}"
    
    # Check non-empty core fields
    for field in ("title", "situation", "decision_question", "recommendation", "approval_effect"):
        if not brief.get(field) or not str(brief[field]).strip():
            return False, f"review_brief.{field} must not be empty"
    
    # Check affected_items is a list
    affected_items = brief.get("affected_items")
    if not isinstance(affected_items, list):
        return False, "review_brief.affected_items must be a list"

    # Check each affected_item has required sub-fields
    for i, item in enumerate(affected_items):
        if not isinstance(item, dict):
            return False, f"review_brief.affected_items[{i}] must be a JSON object"
        for sub_field in ("label", "current_state", "proposed_state"):
            if sub_field not in item:
                return False, f"review_brief.affected_items[{i}] missing required field: {sub_field}"
            if not isinstance(item[sub_field], str):
                return False, f"review_brief.affected_items[{i}].{sub_field} must be a string"
            if not item[sub_field].strip():
                return False, f"review_brief.affected_items[{i}].{sub_field} must not be empty"

    # Check evidence_summary is a list of non-empty strings
    evidence_summary = brief.get("evidence_summary")
    if not isinstance(evidence_summary, list):
        return False, "review_brief.evidence_summary must be a list"
    for i, item in enumerate(evidence_summary):
        if not isinstance(item, str) or not item.strip():
            return False, f"review_brief.evidence_summary[{i}] must be a non-empty string"

    # Check attention_points if present must be a list of non-empty strings
    if "attention_points" in brief:
        attention_points = brief.get("attention_points")
        if not isinstance(attention_points, list):
            return False, "review_brief.attention_points must be a list if present"
        for i, item in enumerate(attention_points):
                if not isinstance(item, str) or not item.strip():
                    return False, f"review_brief.attention_points[{i}] must be a non-empty string"

    # Options are optional for backwards compatibility, but when present
    # they must be concrete and labelled; the UI must never render bare A/B.
    if "decision_options" in brief:
        options = brief.get("decision_options")
        if not isinstance(options, list):
            return False, "review_brief.decision_options must be a list if present"
        for i, option in enumerate(options):
            if not isinstance(option, dict) or not str(option.get("key", "")).strip() or not str(option.get("label", "")).strip():
                return False, f"review_brief.decision_options[{i}] must include non-empty key and label"
            if not str(option.get("meaning", option.get("description", ""))).strip():
                return False, f"review_brief.decision_options[{i}] must include a concrete meaning"

    return True, ""


def get_review_brief(candidate_id: str, db_path: str = PRODUCTION_DB) -> Optional[dict]:
    """Get the review_brief for a candidate."""
    ensure_review_brief_column(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT review_brief_json FROM candidate WHERE id = ?",
            (candidate_id,)
        ).fetchone()
        if not row or not row["review_brief_json"]:
            return None
        try:
            return json.loads(row["review_brief_json"])
        except (json.JSONDecodeError, TypeError):
            return None
    finally:
        conn.close()


def attach_review_brief(candidate_id: str, brief: dict | str, db_path: str = PRODUCTION_DB) -> None:
    """Append a valid review_brief exactly once.

    Frozen review text is part of the user's authorization context.  The
    contract therefore only permits NULL -> valid review_brief and refuses
    silent overwrite of an already-visible brief.
    """
    ensure_review_brief_column(db_path)
    valid, error = validate_review_brief(brief)
    if not valid:
        raise ValueError(f"REVIEW_BRIEF_INVALID: {error}")

    if isinstance(brief, dict):
        brief_text = json.dumps(brief, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    else:
        brief_text = json.dumps(json.loads(brief), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT status, review_brief_json FROM candidate WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise ValueError("CANDIDATE_NOT_FOUND")
        if row[0] != "pending":
            raise ValueError("CANDIDATE_NOT_REVIEWABLE")
        if row[1] is not None:
            raise ValueError("REVIEW_BRIEF_ALREADY_FROZEN")

        cursor = conn.execute(
            "UPDATE candidate SET review_brief_json = ? WHERE id = ? AND review_brief_json IS NULL",
            (brief_text, candidate_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("REVIEW_BRIEF_ALREADY_FROZEN")
        conn.commit()
    finally:
        conn.close()


def is_legacy_candidate(candidate_id: str, db_path: str = PRODUCTION_DB) -> bool:
    """Check if a candidate was created before review_brief contract.
    
    Legacy candidates have review_brief_json = NULL.
    They use the deterministic legacy presenter for presentation.
    """
    ensure_review_brief_column(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT review_brief_json FROM candidate WHERE id = ?",
            (candidate_id,)
        ).fetchone()
        if not row:
            return True
        return row["review_brief_json"] is None
    finally:
        conn.close()
