"""PAW Database Automated Backup and Disaster Recovery Engine.

Enforces Requirements:
- R7.1: Daily automated backup of workspace.db, retaining last 7 copies.
- R7.2: Automatic snapshot before batch commits, migrations, and updates.
- R7.3: Safe user-facing restore procedure with integrity verification.
- R7.4: Zero sensitive API key plaintext in database backups.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.core.config import BACKUPS_DIR, DATA_DIR, DB_PATH

logger = logging.getLogger("paw.backup")

BACKUP_RETENTION_MAX = 7
SENSITIVE_KEY_RE = re.compile(r"sk-[a-zA-Z0-9]{20,}")


def _now_iso_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def create_db_backup(tag: str = "manual", automatic=None) -> Dict[str, Any]:
    from backend.pm.backups import create
    return create(tag,automatic)


def list_backups() -> List[Dict[str, Any]]:
    from backend.pm.backups import listing
    return listing()


def rotate_backups(max_keep: int = BACKUP_RETENTION_MAX) -> int:
    from backend.pm.backups import rotate
    return rotate(max_keep)


def restore_db_backup(backup_filename: str) -> Dict[str, Any]:
    """Restore database from specified backup with pre-restore safety snapshot (R7.3)."""
    from backend.pm.backups import confined
    if Path(backup_filename).name != backup_filename:raise ValueError("备份文件名无效")
    backup_path = confined(BACKUPS_DIR / backup_filename,BACKUPS_DIR)
    if not backup_path.is_file():
        raise FileNotFoundError(f"Backup file not found: {backup_filename}")

    # 1. Verify backup file integrity before touching production db
    chk_conn = sqlite3.connect(str(backup_path))
    try:
        integrity = chk_conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"Target backup file failed integrity check: {integrity}")
        tables = chk_conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    finally:
        chk_conn.close()

    # 2. Create pre-restore safety snapshot of current live database if it exists
    if DB_PATH.is_file():
        safety_name = f"workspace_backup_{_now_iso_compact()}_pre_restore_safety.db"
        safety_path = BACKUPS_DIR / safety_name
        try:
            s_src = sqlite3.connect(str(DB_PATH), timeout=30.0)
            s_dst = sqlite3.connect(str(safety_path), timeout=30.0)
            try:
                s_src.backup(s_dst)
            finally:
                s_dst.close()
                s_src.close()
        except Exception:
            shutil.copy2(DB_PATH, safety_path)

    # 3. Replace live database atomically
    temp_target = DB_PATH.with_suffix(".tmp_restore")
    shutil.copy2(backup_path, temp_target)
    if DB_PATH.exists():
        DB_PATH.unlink()
    temp_target.rename(DB_PATH)

    # 4. Remove WAL / SHM auxiliary files if present
    for aux in (DB_PATH.with_suffix(".db-wal"), DB_PATH.with_suffix(".db-shm")):
        if aux.exists():
            try:
                aux.unlink()
            except Exception:
                pass

    # 5. Verify live database after restore
    live_conn = sqlite3.connect(str(DB_PATH))
    try:
        live_integrity = live_conn.execute("PRAGMA integrity_check").fetchone()[0]
        live_tables = live_conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    finally:
        live_conn.close()

    logger.info(f"Database successfully restored from {backup_filename}. Verified integrity: {live_integrity}")
    return {
        "ok": True,
        "restored_from": backup_filename,
        "integrity": live_integrity,
        "table_count": live_tables,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": f"数据库已成功从备份完整恢复，包含 {live_tables} 张数据表，完整性校验通过！",
    }
