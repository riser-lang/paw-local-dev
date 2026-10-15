"""PAW Desktop Local Edition - Database Operations Tool.

Supports:
- init: Initialize schema
- backup: Safe SQLite VACUUM INTO snapshot
- restore: Safe restore from snapshot
- verify: Integrity check and table schema audit
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Setup paths
root_path = Path(__file__).resolve().parent.parent
backend_path = root_path / "backend"
sys.path.insert(0, str(root_path))
sys.path.insert(0, str(backend_path))

from backend.core.config import BACKUPS_DIR, DB_PATH, DEFAULT_DB_PATH
from backend.workspace_api.db import SCHEMA_SQL, migrate


def backup_db(tag: str = "") -> Path:
    """Create a consistent online backup using SQLite VACUUM INTO."""
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    tag_str = f"_{tag}" if tag else ""
    target_path = BACKUPS_DIR / f"workspace_backup_{timestamp}{tag_str}.db"

    if not DB_PATH.is_file():
        raise FileNotFoundError(f"Database file not found: {DB_PATH}")

    # Use SQLite VACUUM INTO for online, tear-free backup
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(f"VACUUM INTO '{str(target_path).replace(chr(92), '/')}'")
    finally:
        conn.close()

    print(f"[Backup] Successfully backed up database to: {target_path} ({target_path.stat().st_size} bytes)")
    return target_path


def restore_db(backup_path: Path) -> None:
    """Restore database from backup file safely."""
    backup_file = Path(backup_path)
    if not backup_file.is_file():
        raise FileNotFoundError(f"Backup file not found: {backup_file}")

    # Verify integrity of backup file before restoring
    conn = sqlite3.connect(str(backup_file))
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"Corrupt backup file: integrity_check returned '{integrity}'")
    finally:
        conn.close()

    # Pre-restore safety snapshot of current DB if exists
    if DB_PATH.is_file():
        pre_restore_bak = BACKUPS_DIR / f"pre_restore_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.db"
        shutil.copy2(DB_PATH, pre_restore_bak)
        print(f"[Restore] Pre-restore safety backup created: {pre_restore_bak}")

    # Replace DB file
    shutil.copy2(backup_file, DB_PATH)
    print(f"[Restore] Successfully restored database from: {backup_file}")


def verify_db() -> dict:
    """Verify integrity and audit schema tables."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        counts = {
            "projects": conn.execute("SELECT count(*) FROM project").fetchone()[0],
            "knowledge": conn.execute("SELECT count(*) FROM knowledge_page").fetchone()[0],
            "todos": conn.execute("SELECT count(*) FROM todo").fetchone()[0],
            "candidates": conn.execute("SELECT count(*) FROM candidate").fetchone()[0],
            "approved_changes": conn.execute("SELECT count(*) FROM approved_change").fetchone()[0],
            "commit_records": conn.execute("SELECT count(*) FROM commit_record").fetchone()[0],
        }
        res = {
            "path": str(DB_PATH),
            "integrity": integrity,
            "table_count": len(tables),
            "tables": tables,
            "entity_counts": counts,
        }
        print(f"[Verify] Integrity: {integrity} | Tables: {len(tables)} | Counts: {counts}")
        return res
    finally:
        conn.close()


def init_db(clean: bool = False) -> None:
    """Initialize SQLite database schema."""
    if clean and DB_PATH.is_file():
        backup_db(tag="pre_init_clean")
        DB_PATH.unlink()
        print(f"[Init] Removed existing database: {DB_PATH}")

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.executescript(SCHEMA_SQL)
        migrate(conn)
        conn.commit()
    finally:
        conn.close()

    print(f"[Init] Database initialized at: {DB_PATH}")
    verify_db()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PAW Desktop Database Operations")
    parser.add_argument("action", choices=["verify", "backup", "restore", "init"], default="verify")
    parser.add_argument("--tag", default="")
    parser.add_argument("--backup-file", default="")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    if args.action == "verify":
        verify_db()
    elif args.action == "backup":
        backup_db(tag=args.tag)
    elif args.action == "restore":
        restore_db(Path(args.backup_file))
    elif args.action == "init":
        init_db(clean=args.clean)
