#!/usr/bin/env python3
"""supplemental_mirror_state.py — SQLite state store & checkpointing for supplemental mirror."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SupplementalMirrorStateStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS supplemental_items (
                    object_id TEXT NOT NULL,
                    source_version TEXT NOT NULL,
                    name TEXT NOT NULL,
                    family TEXT NOT NULL,
                    canonical_artifact_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    artifact_path TEXT NOT NULL,
                    format_validation TEXT NOT NULL,
                    semantic_complete INTEGER NOT NULL,
                    two_pass_stability TEXT NOT NULL,
                    capture_backend TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    PRIMARY KEY (object_id, source_version)
                )
            """)
            conn.commit()

    def upsert_item(
        self,
        object_id: str,
        source_version: str,
        name: str,
        family: str,
        canonical_artifact_type: str,
        status: str,
        sha256: str,
        size: int,
        artifact_path: str,
        format_validation: str,
        semantic_complete: bool,
        two_pass_stability: str,
        capture_backend: str,
        details: dict,
    ):
        now = utc_now()
        details_str = json.dumps(details, ensure_ascii=False)
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO supplemental_items (
                    object_id, source_version, name, family, canonical_artifact_type,
                    status, sha256, size, artifact_path, format_validation,
                    semantic_complete, two_pass_stability, capture_backend,
                    created_at, updated_at, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(object_id, source_version) DO UPDATE SET
                    name=excluded.name,
                    family=excluded.family,
                    canonical_artifact_type=excluded.canonical_artifact_type,
                    status=excluded.status,
                    sha256=excluded.sha256,
                    size=excluded.size,
                    artifact_path=excluded.artifact_path,
                    format_validation=excluded.format_validation,
                    semantic_complete=excluded.semantic_complete,
                    two_pass_stability=excluded.two_pass_stability,
                    capture_backend=excluded.capture_backend,
                    updated_at=excluded.updated_at,
                    details_json=excluded.details_json
            """, (
                str(object_id),
                str(source_version),
                name,
                family,
                canonical_artifact_type,
                status,
                sha256,
                int(size),
                artifact_path,
                format_validation,
                1 if semantic_complete else 0,
                two_pass_stability,
                capture_backend,
                now,
                now,
                details_str
            ))
            conn.commit()

    def get_item(self, object_id: str, source_version: str) -> Optional[dict]:
        with self._get_conn() as conn:
            cur = conn.execute(
                "SELECT * FROM supplemental_items WHERE object_id = ? AND source_version = ?",
                (str(object_id), str(source_version))
            )
            row = cur.fetchone()
            if row:
                d = dict(row)
                d["details"] = json.loads(d.pop("details_json", "{}"))
                return d
            return None

    def get_all_items(self) -> List[dict]:
        with self._get_conn() as conn:
            cur = conn.execute("SELECT * FROM supplemental_items ORDER BY object_id ASC")
            items = []
            for row in cur.fetchall():
                d = dict(row)
                d["details"] = json.loads(d.pop("details_json", "{}"))
                items.append(d)
            return items


def save_checkpoint(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    temp_path.replace(path)


def load_checkpoint(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
