"""Widen legacy CHECK constraints on governance tables.

Databases created before project convergence existed reject
candidate_type='project_merge' and approved_change rows for the
project_merge.merge operation, so every merge proposal failed with an
IntegrityError.  ``CREATE TABLE IF NOT EXISTS`` never upgrades an existing
table, so the table is rebuilt once (SQLite's documented 12-step procedure)
with the missing values added to its CHECK lists.  A full backup is taken
before anything is rewritten.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

logger = logging.getLogger("paw.schema_migration")

# table -> [(column, value that must be allowed by the column's CHECK list)]
REQUIRED_CHECK_VALUES: Dict[str, List[Tuple[str, str]]] = {
    "candidate": [("candidate_type", "project_merge")],
    "approved_change": [("target_type", "project_merge"), ("operation", "merge")],
}


def _widen_check(sql: str, column: str, value: str) -> str:
    """Append ``value`` to ``column``'s ``CHECK (column IN (...))`` list if missing."""
    pattern = re.compile(r"(CHECK\s*\(\s*" + re.escape(column) + r"\s+IN\s*\()([^)]*)(\))", re.I | re.S)
    m = pattern.search(sql)
    if not m or f"'{value}'" in m.group(2):
        return sql
    widened = m.group(2).rstrip() + f", '{value}'"
    return sql[:m.start()] + m.group(1) + widened + m.group(3) + sql[m.end():]


def pending_changes(conn: sqlite3.Connection) -> Dict[str, str]:
    """Return {table: new CREATE sql} for tables whose CHECK lists need widening."""
    out = {}
    for table, reqs in REQUIRED_CHECK_VALUES.items():
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        if not row or not row[0]:
            continue
        new_sql = row[0]
        for column, value in reqs:
            new_sql = _widen_check(new_sql, column, value)
        if new_sql != row[0]:
            out[table] = new_sql
    return out


def _backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"workspace_pre_schema_migration_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    src = sqlite3.connect(str(db_path), timeout=30.0)
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return target


def migrate_check_constraints(db_path, backup: bool = True) -> Dict[str, object]:
    """Rebuild governance tables whose CHECK lists are missing required values.

    Idempotent: returns {"migrated": []} when the schema is already current.
    """
    db_path = Path(db_path)
    if not db_path.is_file():
        return {"migrated": [], "reason": "no_database"}

    conn = sqlite3.connect(str(db_path), timeout=60.0)
    conn.isolation_level = None  # explicit transaction control below
    try:
        changes = pending_changes(conn)
        if not changes:
            return {"migrated": []}
        backup_path = str(_backup(db_path)) if backup else None

        counts_before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in changes}
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA legacy_alter_table = ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            for table, new_sql in changes.items():
                tmp = f"{table}__paw_migrate"
                aux = [r[0] for r in conn.execute(
                    "SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL",
                    (table,))]
                create_tmp = re.sub(r"^\s*CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?[\"`\[]?" + re.escape(table) + r"[\"`\]]?",
                                    f"CREATE TABLE {tmp}", new_sql, count=1, flags=re.I)
                conn.execute(f"DROP TABLE IF EXISTS {tmp}")
                conn.execute(create_tmp)
                conn.execute(f"INSERT INTO {tmp} SELECT * FROM {table}")
                conn.execute(f"DROP TABLE {table}")
                conn.execute(f"ALTER TABLE {tmp} RENAME TO {table}")
                for stmt in aux:
                    conn.execute(stmt)
            bad_fk = conn.execute("PRAGMA foreign_key_check").fetchall()
            counts_after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in changes}
            if counts_after != counts_before:
                raise RuntimeError(f"row count changed during migration: {counts_before} -> {counts_after}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.execute("PRAGMA legacy_alter_table = OFF")
            conn.execute("PRAGMA foreign_keys = ON")

        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        logger.info(f"Schema migration done: {list(changes)} rows={counts_before} integrity={integrity} backup={backup_path}")
        return {"migrated": list(changes), "rows": counts_before, "integrity": integrity,
                "backup": backup_path, "foreign_key_issues": len(bad_fk)}
    finally:
        conn.close()
