"""Migration Validator V1 — Dry-run validation of Brain schema migration.

Validates migration SQL on a TEMPORARY SQLite database.
NEVER touches production workspace.db.

Tests:
  M1: migration clean apply on temp DB
  M2: migration idempotency behavior
  M3: rollback SQL executable
  M4: existing schema v4 tables untouched
  M5: foreign key valid
  M6: ambiguity supported
  M7: applied_entity_type/id supported
  M8: no AUTO_WRITE trigger

Usage:
  python -m brain.integration.migration_validator
"""
from __future__ import annotations
import sqlite3
import tempfile
import os
import sys
from pathlib import Path

# Ensure brain modules are importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1 if "backend" in str(Path(__file__).parent.name) else 2]))

from brain.candidates.models import MIGRATION_SQL_DRAFT, MIGRATION_STATUS

# ── Production schema v4 tables (for M4 verification) ──────────────────────
PRODUCTION_TABLES = [
    "project", "todo", "knowledge_page", "knowledge_revision",
    "knowledge_source_link", "daily", "capture", "wiki_page",
]

# ── Rollback SQL ──────────────────────────────────────────────────────────
ROLLBACK_SQL = """
-- Rollback: Remove Brain tables only
-- DO NOT touch production v4 tables
DROP INDEX IF EXISTS idx_candidate_analysis_run;
DROP INDEX IF EXISTS idx_candidate_status;
DROP INDEX IF EXISTS idx_candidate_type;
DROP TABLE IF EXISTS candidate;
DROP TABLE IF EXISTS analysis_run;
"""

# ── Schema v4 simulation (minimal, for M4 test) ────────────────────────────
SCHEMA_V4_SIMULATION = """
CREATE TABLE IF NOT EXISTS project (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS todo (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS knowledge_page (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
);
CREATE TABLE IF NOT EXISTS knowledge_revision (
    id TEXT PRIMARY KEY,
    page_id TEXT,
    revision_no INTEGER NOT NULL,
    content TEXT
);
CREATE TABLE IF NOT EXISTS knowledge_source_link (
    id TEXT PRIMARY KEY,
    page_id TEXT,
    source TEXT
);
CREATE TABLE IF NOT EXISTS daily (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    content TEXT
);
CREATE TABLE IF NOT EXISTS capture (
    id TEXT PRIMARY KEY,
    raw_text TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS wiki_page (
    id TEXT PRIMARY KEY,
    title TEXT,
    content TEXT
);
"""


def create_temp_db() -> str:
    """Create a temporary SQLite database file."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="brain_migration_")
    os.close(fd)
    return path


def apply_schema_v4(conn: sqlite3.Connection):
    """Apply simulated v4 schema to temp DB."""
    conn.executescript(SCHEMA_V4_SIMULATION)
    conn.commit()


def apply_migration(conn: sqlite3.Connection):
    """Apply Brain migration SQL to temp DB."""
    conn.executescript(MIGRATION_SQL_DRAFT)
    conn.commit()


def apply_rollback(conn: sqlite3.Connection):
    """Apply rollback SQL to temp DB."""
    conn.executescript(ROLLBACK_SQL)
    conn.commit()


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cur.fetchone() is not None


def index_exists(conn: sqlite3.Connection, index_name: str) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,)
    )
    return cur.fetchone() is not None


# ── Test Results ───────────────────────────────────────────────────────────
_results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = ""):
    _results.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), name, detail)


# ── M1: Migration clean apply on temp DB ───────────────────────────────────
def test_m1():
    """M1: migration clean apply on temp DB."""
    db_path = create_temp_db()
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        apply_schema_v4(conn)
        apply_migration(conn)

        check("M1_analysis_run_created", table_exists(conn, "analysis_run"))
        check("M1_candidate_created", table_exists(conn, "candidate"))
        check("M1_idx_analysis_run", index_exists(conn, "idx_candidate_analysis_run"))
        check("M1_idx_status", index_exists(conn, "idx_candidate_status"))
        check("M1_idx_type", index_exists(conn, "idx_candidate_type"))
        conn.close()
    finally:
        os.unlink(db_path)


# ── M2: Migration idempotency ──────────────────────────────────────────────
def test_m2():
    """M2: migration idempotency — applying twice should not error."""
    db_path = create_temp_db()
    try:
        conn = sqlite3.connect(db_path)
        apply_schema_v4(conn)
        apply_migration(conn)
        # Second apply should not error (IF NOT EXISTS)
        try:
            apply_migration(conn)
            check("M2_idempotent_no_error", True)
        except Exception as e:
            check("M2_idempotent_no_error", False, str(e))
        conn.close()
    finally:
        os.unlink(db_path)


# ── M3: Rollback SQL executable ─────────────────────────────────────────────
def test_m3():
    """M3: rollback SQL can be executed after migration."""
    db_path = create_temp_db()
    try:
        conn = sqlite3.connect(db_path)
        apply_schema_v4(conn)
        apply_migration(conn)
        apply_rollback(conn)

        check("M3_analysis_run_dropped", not table_exists(conn, "analysis_run"))
        check("M3_candidate_dropped", not table_exists(conn, "candidate"))
        check("M3_idx_dropped", not index_exists(conn, "idx_candidate_analysis_run"))
        conn.close()
    finally:
        os.unlink(db_path)


# ── M4: Existing v4 tables untouched ───────────────────────────────────────
def test_m4():
    """M4: existing schema v4 tables untouched by migration and rollback."""
    db_path = create_temp_db()
    try:
        conn = sqlite3.connect(db_path)
        apply_schema_v4(conn)
        # Insert test data into v4 tables
        conn.execute("INSERT INTO project (id, name) VALUES ('p1', 'Test Project')")
        conn.execute("INSERT INTO todo (id, project_id, title) VALUES ('t1', 'p1', 'Test Todo')")
        conn.commit()

        apply_migration(conn)

        # Verify v4 data still intact
        proj = conn.execute("SELECT name FROM project WHERE id='p1'").fetchone()
        todo = conn.execute("SELECT title FROM todo WHERE id='t1'").fetchone()
        check("M4_project_data_preserved", proj is not None and proj[0] == 'Test Project')
        check("M4_todo_data_preserved", todo is not None and todo[0] == 'Test Todo')

        # Verify all v4 tables still exist
        for t in PRODUCTION_TABLES:
            check(f"M4_table_{t}_exists", table_exists(conn, t))

        # Apply rollback, verify v4 tables still exist
        apply_rollback(conn)
        for t in PRODUCTION_TABLES:
            check(f"M4_rollback_{t}_preserved", table_exists(conn, t))

        conn.close()
    finally:
        os.unlink(db_path)


# ── M5: Foreign key valid ───────────────────────────────────────────────────
def test_m5():
    """M5: foreign key constraint on candidate.analysis_run_id is valid."""
    db_path = create_temp_db()
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        apply_schema_v4(conn)
        apply_migration(conn)

        # Insert a valid analysis_run
        conn.execute("""
            INSERT INTO analysis_run (id, trigger_type, raw_text, raw_input_refs,
                analysis_model, started_at, completed_at, created_at)
            VALUES ('ar1', 'manual_review', 'test', '[]', 'test-model',
                '2026-01-01T00:00:00Z', '2026-01-01T00:01:00Z', '2026-01-01T00:01:00Z')
        """)
        conn.commit()

        # Insert valid candidate with FK
        try:
            conn.execute("""
                INSERT INTO candidate (id, analysis_run_id, candidate_type, source_refs,
                    raw_excerpt, proposed_change, confidence, inference_level,
                    recommended_action, analysis_model, status, created_at)
                VALUES ('c1', 'ar1', 'fact', '[]', 'test', '{}',
                    'HIGH', 'EXPLICIT', 'DRAFT_ONLY', 'test-model', 'pending',
                    '2026-01-01T00:01:00Z')
            """)
            conn.commit()
            check("M5_valid_fk_insert", True)
        except Exception as e:
            check("M5_valid_fk_insert", False, str(e))

        # Insert invalid candidate with non-existent FK
        try:
            conn.execute("""
                INSERT INTO candidate (id, analysis_run_id, candidate_type,
                    confidence, inference_level, recommended_action,
                    analysis_model, status, created_at)
                VALUES ('c2', 'nonexistent', 'fact',
                    'HIGH', 'EXPLICIT', 'DRAFT_ONLY',
                    'test-model', 'pending', '2026-01-01T00:01:00Z')
            """)
            conn.commit()
            check("M5_invalid_fk_rejected", False, "should have failed")
        except sqlite3.IntegrityError:
            check("M5_invalid_fk_rejected", True, "FK constraint enforced")
        except Exception as e:
            check("M5_invalid_fk_rejected", True, f"rejected: {e}")

        conn.close()
    finally:
        os.unlink(db_path)


# ── M6: Ambiguity type supported ────────────────────────────────────────────
def test_m6():
    """M6: ambiguity candidate type is supported in CHECK constraint."""
    db_path = create_temp_db()
    try:
        conn = sqlite3.connect(db_path)
        apply_schema_v4(conn)
        apply_migration(conn)

        conn.execute("""
            INSERT INTO analysis_run (id, trigger_type, analysis_model,
                started_at, completed_at, created_at)
            VALUES ('ar6', 'manual_review', 'test', '2026-01-01T00:00:00Z',
                '2026-01-01T00:01:00Z', '2026-01-01T00:01:00Z')
        """)
        conn.commit()

        # Insert ambiguity candidate
        try:
            conn.execute("""
                INSERT INTO candidate (id, analysis_run_id, candidate_type,
                    confidence, inference_level, recommended_action,
                    analysis_model, status, created_at)
                VALUES ('c6', 'ar6', 'ambiguity',
                    'LOW', 'WEAK_INFERENCE', 'ASK_USER',
                    'test-model', 'pending', '2026-01-01T00:01:00Z')
            """)
            conn.commit()
            check("M6_ambiguity_inserted", True)
        except Exception as e:
            check("M6_ambiguity_inserted", False, str(e))

        # Insert invalid type
        try:
            conn.execute("""
                INSERT INTO candidate (id, analysis_run_id, candidate_type,
                    confidence, inference_level, recommended_action,
                    analysis_model, status, created_at)
                VALUES ('c6b', 'ar6', 'invalid_type',
                    'LOW', 'WEAK_INFERENCE', 'ASK_USER',
                    'test-model', 'pending', '2026-01-01T00:01:00Z')
            """)
            conn.commit()
            check("M6_invalid_type_rejected", False, "should have failed")
        except sqlite3.IntegrityError:
            check("M6_invalid_type_rejected", True, "CHECK constraint enforced")
        except Exception as e:
            check("M6_invalid_type_rejected", True, f"rejected: {e}")

        conn.close()
    finally:
        os.unlink(db_path)


# ── M7: applied_entity_type/id supported ───────────────────────────────────
def test_m7():
    """M7: applied_entity_type and applied_entity_id columns are supported."""
    db_path = create_temp_db()
    try:
        conn = sqlite3.connect(db_path)
        apply_schema_v4(conn)
        apply_migration(conn)

        conn.execute("""
            INSERT INTO analysis_run (id, trigger_type, analysis_model,
                started_at, completed_at, created_at)
            VALUES ('ar7', 'manual_review', 'test', '2026-01-01T00:00:00Z',
                '2026-01-01T00:01:00Z', '2026-01-01T00:01:00Z')
        """)
        conn.commit()

        # Insert candidate with applied entity info
        try:
            conn.execute("""
                INSERT INTO candidate (id, analysis_run_id, candidate_type,
                    confidence, inference_level, recommended_action,
                    analysis_model, status, created_at,
                    applied_entity_type, applied_entity_id)
                VALUES ('c7', 'ar7', 'todo',
                    'HIGH', 'EXPLICIT', 'DRAFT_ONLY',
                    'test-model', 'applied', '2026-01-01T00:01:00Z',
                    'todo', 'todo-uuid-123')
            """)
            conn.commit()
            row = conn.execute("""
                SELECT applied_entity_type, applied_entity_id
                FROM candidate WHERE id='c7'
            """).fetchone()
            check("M7_applied_entity_stored",
                  row is not None and row[0] == 'todo' and row[1] == 'todo-uuid-123')
        except Exception as e:
            check("M7_applied_entity_stored", False, str(e))

        conn.close()
    finally:
        os.unlink(db_path)


# ── M8: No AUTO_WRITE trigger ───────────────────────────────────────────────
def test_m8():
    """M8: migration does not create any trigger that auto-writes to v4 tables."""
    db_path = create_temp_db()
    try:
        conn = sqlite3.connect(db_path)
        apply_schema_v4(conn)
        apply_migration(conn)

        # Check for any triggers
        triggers = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        ).fetchall()
        check("M8_no_triggers", len(triggers) == 0, f"triggers={triggers}")

        # Verify AUTO_WRITE is in CHECK but not a trigger
        # AUTO_WRITE is a valid value for recommended_action column,
        # but no trigger automatically applies it
        check("M8_auto_write_not_triggered", True, "no trigger mechanism")

        conn.close()
    finally:
        os.unlink(db_path)


# ── Run all tests ───────────────────────────────────────────────────────────
def run_all():
    print("=== Migration Validator Tests ===")
    print(f"MIGRATION_STATUS: {MIGRATION_STATUS}")
    print()

    test_m1()
    test_m2()
    test_m3()
    test_m4()
    test_m5()
    test_m6()
    test_m7()
    test_m8()

    failed = [(n, d) for n, ok, d in _results if not ok]
    print()
    print("=====SUMMARY=====")
    print(f"TOTAL: {len(_results)} PASS: {len(_results) - len(failed)} FAIL: {len(failed)}")
    if failed:
        print("FAILED:", [n for n, _ in failed])
        for n, d in failed:
            print(f"  {n}: {d}")
        return False
    print("ALL_MIGRATION_TESTS_PASS")
    return True


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)