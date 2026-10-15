"""Analysis Repository — Data access layer for Analysis Engine.

Handles:
  - analysis_run CRUD (temp DB for tests, production read-only)
  - Source fingerprint computation (capture + daily)
  - Idempotency checks
  - Capture/Daily data reading

Safety:
  - NEVER writes to production workspace.db
  - Production reads use mode=ro
  - analysis_run persistence only on temp DB for tests
  - No candidate persistence
"""

from __future__ import annotations

from pathlib import Path

import sqlite3
import json
import hashlib
from datetime import datetime, timezone
from typing import Any, Optional
from dataclasses import dataclass, field

PRODUCTION_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))

# ── Source Fingerprint ────────────────────────────────────────────────────

def compute_capture_fingerprint(capture: dict) -> str:
    """Compute deterministic fingerprint for a capture record.
    Based on: id, raw_text hash, updated_at, user_edited.
    """
    raw_text = capture.get("raw_text", "") or ""
    raw_hash = hashlib.sha256(raw_text.encode()).hexdigest()
    fp_input = f"{capture.get('id','')}|{raw_hash}|{capture.get('updated_at','')}|{capture.get('user_edited',0)}"
    return hashlib.sha256(fp_input.encode()).hexdigest()

def compute_daily_fingerprint(daily: dict, captures: list[dict]) -> str:
    """Compute deterministic fingerprint for a daily aggregate.
    Based on: daily.id, daily.updated_at, capture ids + their fingerprints.
    """
    capture_fps = sorted([compute_capture_fingerprint(c) for c in captures])
    fp_input = f"{daily.get('id','')}|{daily.get('updated_at','')}|{','.join(capture_fps)}"
    return hashlib.sha256(fp_input.encode()).hexdigest()

# ── Production Read (Read-Only) ───────────────────────────────────────────

def _get_ro_connection(db_path: str = PRODUCTION_DB) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row
    return con

def get_capture(capture_id: str, db_path: str = PRODUCTION_DB) -> Optional[dict]:
    """Get capture by ID. Returns None if not found or table missing."""
    try:
        con = _get_ro_connection(db_path)
        row = con.execute("SELECT * FROM capture WHERE id = ?", (capture_id,)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        try: con.close()
        except: pass

def get_captures_by_daily(daily_id: str, db_path: str = PRODUCTION_DB) -> list[dict]:
    try:
        con = _get_ro_connection(db_path)
        rows = con.execute("SELECT * FROM capture WHERE daily_id = ? ORDER BY captured_at", (daily_id,)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        try: con.close()
        except: pass

def get_daily(daily_id: str, db_path: str = PRODUCTION_DB) -> Optional[dict]:
    try:
        con = _get_ro_connection(db_path)
        row = con.execute("SELECT * FROM daily WHERE id = ?", (daily_id,)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        try: con.close()
        except: pass

def get_unprocessed_captures(limit: int = 100, db_path: str = PRODUCTION_DB) -> list[dict]:
    con = _get_ro_connection(db_path)
    try:
        rows = con.execute(
            "SELECT * FROM capture WHERE processed_at IS NULL ORDER BY captured_at LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()

def get_all_dailies(db_path: str = PRODUCTION_DB) -> list[dict]:
    con = _get_ro_connection(db_path)
    try:
        rows = con.execute("SELECT * FROM daily ORDER BY date").fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()

def get_existing_analysis_fingerprints(db_path: str = PRODUCTION_DB) -> dict[str, str]:
    """Get fingerprints of existing successful analysis_runs.

    Only COMPLETED + (CANDIDATE_CREATED|NO_ACTION) runs count as successful.
    FAILED and RUNNING runs do NOT block retry.
    Legacy runs without metadata are treated conservatively (completed_at → success).
    Returns {fingerprint: analysis_run_id}
    """
    con = _get_ro_connection(db_path)
    try:
        rows = con.execute(
            "SELECT id, raw_input_refs, completed_at FROM analysis_run"
        ).fetchall()
        result = {}
        for row in rows:
            refs = row["raw_input_refs"] or ""
            completed_at = row["completed_at"] if "completed_at" in row.keys() else None
            try:
                refs_data = json.loads(refs) if refs else {}
            except (json.JSONDecodeError, TypeError):
                continue
            # Determine if this run is truly successful
            is_successful = False
            if isinstance(refs_data, dict):
                status = str(refs_data.get("status", "")).upper()
                outcome = str(refs_data.get("outcome", "")).upper()
                fingerprint = refs_data.get("fingerprint")
                if status == "COMPLETED" and outcome in ("CANDIDATE_CREATED", "NO_ACTION"):
                    is_successful = True
                # Legacy dict without status: conservative — treat as success if completed_at exists
                elif "status" not in refs_data and fingerprint and completed_at:
                    is_successful = True
            elif isinstance(refs_data, list):
                # Old format: plain list of refs, no metadata — conservative success if completed_at
                if completed_at:
                    # No fingerprint in old format, skip
                    pass
            if is_successful and isinstance(refs_data, dict):
                fingerprint = refs_data.get("fingerprint")
                if fingerprint:
                    result[fingerprint] = row["id"]
        return result
    finally:
        con.close()

def check_idempotency(fingerprint: str, db_path: str = PRODUCTION_DB) -> tuple[bool, Optional[str]]:
    """Check if an analysis with this fingerprint already exists.
    Returns (is_duplicate, existing_analysis_id).
    """
    existing = get_existing_analysis_fingerprints(db_path)
    if fingerprint in existing:
        return True, existing[fingerprint]
    return False, None

# ── Analysis Run Lifecycle (Temp DB only for tests) ───────────────────────

def create_temp_analysis_db() -> str:
    """Create a temporary analysis DB for testing.
    Mirrors production analysis_run schema.
    """
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".db", prefix="analysis_test_")
    import os
    os.close(fd)
    con = sqlite3.connect(path)
    con.execute("""
        CREATE TABLE analysis_run (
            id TEXT PRIMARY KEY,
            trigger_type TEXT NOT NULL,
            raw_text TEXT,
            raw_input_refs TEXT,
            analysis_model TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    con.commit()
    con.close()
    return path

def insert_analysis_run(
    db_path: str,
    analysis_id: str,
    trigger_type: str,
    raw_text: Optional[str],
    raw_input_refs: list[str],
    analysis_model: str,
    started_at: str,
    completed_at: str = "",
    fingerprint: Optional[str] = None,
    status: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Insert analysis_run record.
    Uses raw_input_refs JSON to store fingerprint + status + error metadata
    (since production schema has no status column).
    """
    refs_meta = {
        "refs": raw_input_refs,
        "fingerprint": fingerprint,
        "status": (status or ("COMPLETED" if completed_at else "RUNNING")).upper(),
        "error": error,
    }
    con = sqlite3.connect(db_path)
    con.execute(
        """INSERT INTO analysis_run
           (id, trigger_type, raw_text, raw_input_refs, analysis_model,
            started_at, completed_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (analysis_id, trigger_type, raw_text, json.dumps(refs_meta),
         analysis_model, started_at, completed_at, started_at)
    )
    con.commit()
    con.close()

def complete_analysis_run(
    db_path: str,
    analysis_id: str,
    completed_at: str,
    status: str = "completed",
    error: Optional[str] = None,
    outcome: Optional[str] = None,
) -> None:
    """Mark analysis_run as completed (or failed) with optional outcome.

    Args:
        status: completed | failed
        error: error summary (for failed)
        outcome: CANDIDATE_CREATED | NO_ACTION | FAILED | None
    """
    con = sqlite3.connect(db_path)
    row = con.execute("SELECT raw_input_refs FROM analysis_run WHERE id = ?", (analysis_id,)).fetchone()
    if row:
        refs_meta = json.loads(row[0]) if row[0] else {}
        if isinstance(refs_meta, list):
            refs_meta = {"refs": refs_meta}
        refs_meta["status"] = status.upper() if status in ("completed", "failed") else status
        refs_meta["error"] = error
        if outcome:
            refs_meta["outcome"] = outcome
        con.execute(
            "UPDATE analysis_run SET completed_at = ?, raw_input_refs = ? WHERE id = ?",
            (completed_at, json.dumps(refs_meta), analysis_id)
        )
        con.commit()
    con.close()

def get_analysis_run(db_path: str, analysis_id: str) -> Optional[dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT * FROM analysis_run WHERE id = ?", (analysis_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        refs = result.get("raw_input_refs", "")
        if refs:
            try:
                refs_meta = json.loads(refs)
                result["fingerprint"] = refs_meta.get("fingerprint")
                result["status"] = str(refs_meta.get("status", "COMPLETED" if result.get("completed_at") else "RUNNING")).upper()
                result["error"] = refs_meta.get("error")
                result["source_refs"] = refs_meta.get("refs", [])
            except (json.JSONDecodeError, TypeError):
                result["status"] = "COMPLETED" if result.get("completed_at") else "RUNNING"
        return result
    finally:
        con.close()

def get_daily_analysis_status(daily_id: str, db_path: str = PRODUCTION_DB) -> dict:
    """Check if a daily has been analyzed.
    Uses analysis_run raw_input_refs to find daily_id references.
    """
    con = _get_ro_connection(db_path)
    try:
        rows = con.execute(
            "SELECT id, completed_at, raw_input_refs FROM analysis_run WHERE trigger_type = ?",
            ("daily",)
        ).fetchall()
        for row in rows:
            refs = row["raw_input_refs"] or ""
            try:
                refs_data = json.loads(refs) if refs else {}
                refs_list = refs_data.get("refs", []) if isinstance(refs_data, dict) else refs_data
                if daily_id in refs_list:
                    return {
                        "analyzed": True,
                        "analysis_id": row["id"],
                        "completed_at": row["completed_at"],
                    }
            except (json.JSONDecodeError, TypeError):
                continue
        return {"analyzed": False}
    finally:
        con.close()
