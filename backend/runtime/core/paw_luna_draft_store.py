"""SQLite store for Luna Draft Projections — non-Canonical read plane."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from gpt_brain_common import utc_now
from paw_luna_draft_contract import AUTHORITY_DRAFT, SCHEMA, stable_draft_id

DEFAULT_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))
DRAFT_TABLE = "luna_draft_projection"
SCHEMA_VERSION_KEY = "luna_draft_projection_v1"

DRAFT_COLUMNS = (
    "draft_id", "source_id", "source_version_id", "source_title", "source_path",
    "project_id", "project_name", "project_tier", "summary",
    "facts_json", "decisions_json", "metrics_json", "timeline_json",
    "risks_json", "ambiguities_json", "related_projects_json",
    "evidence_refs_json", "provenance_json",
    "preanalysis_processor", "preanalysis_model", "status",
    "authority_level", "is_canonical", "review_bundle_id",
    "canonical_entity_type", "canonical_entity_id", "coverage_state",
    "semantic_hash", "ingestion_error", "created_at", "updated_at",
)

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {DRAFT_TABLE} (
    draft_id TEXT PRIMARY KEY,
    source_id TEXT,
    source_version_id TEXT NOT NULL UNIQUE,
    source_title TEXT,
    source_path TEXT,
    project_id TEXT,
    project_name TEXT,
    project_tier TEXT,
    summary TEXT,
    facts_json TEXT NOT NULL DEFAULT '[]',
    decisions_json TEXT NOT NULL DEFAULT '[]',
    metrics_json TEXT NOT NULL DEFAULT '[]',
    timeline_json TEXT NOT NULL DEFAULT '[]',
    risks_json TEXT NOT NULL DEFAULT '[]',
    ambiguities_json TEXT NOT NULL DEFAULT '[]',
    related_projects_json TEXT NOT NULL DEFAULT '[]',
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    provenance_json TEXT NOT NULL DEFAULT '{{}}',
    preanalysis_processor TEXT,
    preanalysis_model TEXT,
    status TEXT NOT NULL,
    authority_level TEXT NOT NULL DEFAULT 'draft',
    is_canonical INTEGER NOT NULL DEFAULT 0,
    review_bundle_id TEXT,
    canonical_entity_type TEXT,
    canonical_entity_id TEXT,
    coverage_state TEXT,
    semantic_hash TEXT,
    ingestion_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    lifecycle TEXT NOT NULL DEFAULT 'ACTIVE'
);
CREATE INDEX IF NOT EXISTS idx_luna_draft_project ON {DRAFT_TABLE}(project_id);
CREATE INDEX IF NOT EXISTS idx_luna_draft_status ON {DRAFT_TABLE}(status);
CREATE INDEX IF NOT EXISTS idx_luna_draft_source ON {DRAFT_TABLE}(source_version_id);
CREATE INDEX IF NOT EXISTS idx_luna_draft_updated ON {DRAFT_TABLE}(updated_at);
CREATE INDEX IF NOT EXISTS idx_luna_draft_lifecycle ON {DRAFT_TABLE}(lifecycle);
"""


def default_db_path() -> Path:
    return Path(os.environ.get("PAW_WORKSPACE_DB", DEFAULT_DB))


def connect(db_path: str | os.PathLike[str] | None = None) -> sqlite3.Connection:
    path = Path(db_path or default_db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _schema_meta_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )


def _schema_applied(conn: sqlite3.Connection) -> bool:
    _schema_meta_table(conn)
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key=?",
        (SCHEMA_VERSION_KEY,),
    ).fetchone()
    return bool(row and row[0] == SCHEMA)


def _maybe_backup_once(path: Path) -> str | None:
    """Create at most one pre-luna-draft backup per DB file for this schema rollout."""
    if not path.exists():
        return None
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(backup_dir.glob("workspace.db.pre-luna-draft.*"))
    if existing:
        return str(existing[-1])
    stamp = utc_now().replace(":", "").replace("+00:00", "Z")
    backup_path = backup_dir / f"workspace.db.pre-luna-draft.{stamp}"
    import shutil
    shutil.copy2(path, backup_path)
    return str(backup_path)


def ensure_schema(db_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Idempotent schema ensure — never creates DB backups."""
    path = Path(db_path or default_db_path())
    with connect(path) as conn:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({DRAFT_TABLE})")}
        if columns and "lifecycle" not in columns:
            conn.execute(f"ALTER TABLE {DRAFT_TABLE} ADD COLUMN lifecycle TEXT NOT NULL DEFAULT 'ACTIVE'")
        conn.executescript(CREATE_SQL)
        _schema_meta_table(conn)
        if not _schema_applied(conn):
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value, updated_at) VALUES(?,?,?)",
                (SCHEMA_VERSION_KEY, SCHEMA, utc_now()),
            )
        conn.commit()
        count = conn.execute(f"SELECT COUNT(*) FROM {DRAFT_TABLE}").fetchone()[0]
    return {
        "schema": SCHEMA,
        "table": DRAFT_TABLE,
        "path": str(path),
        "backup": None,
        "existing_rows": count,
    }


def migrate(db_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """One-time migration with optional single backup before first schema apply."""
    path = Path(db_path or default_db_path())
    backup_note = None
    with connect(path) as conn:
        already_applied = _schema_applied(conn)
    if not already_applied:
        backup_note = _maybe_backup_once(path)
    result = ensure_schema(path)
    result["backup"] = backup_note
    return result


def _json_dump(value: Any) -> str:
    return json.dumps(value if value is not None else ([] if isinstance(value, list) else {}), ensure_ascii=False, separators=(",", ":"))


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key in (
        "facts_json", "decisions_json", "metrics_json", "timeline_json",
        "risks_json", "ambiguities_json", "related_projects_json",
        "evidence_refs_json", "provenance_json",
    ):
        raw = data.get(key)
        try:
            data[key.replace("_json", "")] = json.loads(raw) if raw else ([] if "related" not in key and "provenance" not in key else {})
        except (TypeError, json.JSONDecodeError):
            data[key.replace("_json", "")] = [] if "provenance" not in key else {}
    data["is_canonical"] = bool(data.get("is_canonical"))
    data["authority_level"] = data.get("authority_level") or AUTHORITY_DRAFT
    return data


def upsert_draft(record: Mapping[str, Any], db_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    ensure_schema(db_path)
    source_version_id = str(record["source_version_id"])
    draft_id = str(record.get("draft_id") or stable_draft_id(source_version_id))
    now = utc_now()
    payload = {
        "draft_id": draft_id,
        "source_id": record.get("source_id"),
        "source_version_id": source_version_id,
        "source_title": record.get("source_title"),
        "source_path": record.get("source_path"),
        "project_id": record.get("project_id"),
        "project_name": record.get("project_name"),
        "project_tier": record.get("project_tier"),
        "summary": record.get("summary"),
        "facts_json": _json_dump(record.get("facts") or record.get("facts_json") or []),
        "decisions_json": _json_dump(record.get("decisions") or record.get("decisions_json") or []),
        "metrics_json": _json_dump(record.get("metrics") or record.get("metrics_json") or []),
        "timeline_json": _json_dump(record.get("timeline") or record.get("timeline_json") or []),
        "risks_json": _json_dump(record.get("risks") or record.get("risks_json") or []),
        "ambiguities_json": _json_dump(record.get("ambiguities") or record.get("ambiguities_json") or []),
        "related_projects_json": _json_dump(record.get("related_projects") or record.get("related_projects_json") or []),
        "evidence_refs_json": _json_dump(record.get("evidence_refs") or record.get("evidence_refs_json") or []),
        "provenance_json": _json_dump(record.get("provenance") or record.get("provenance_json") or {}),
        "preanalysis_processor": record.get("preanalysis_processor"),
        "preanalysis_model": record.get("preanalysis_model"),
        "status": record.get("status"),
        "authority_level": AUTHORITY_DRAFT,
        "is_canonical": 0,
        "review_bundle_id": record.get("review_bundle_id"),
        "canonical_entity_type": record.get("canonical_entity_type"),
        "canonical_entity_id": record.get("canonical_entity_id"),
        "coverage_state": record.get("coverage_state"),
        "semantic_hash": record.get("semantic_hash"),
        "ingestion_error": record.get("ingestion_error"),
        "created_at": record.get("created_at") or now,
        "updated_at": now,
    }
    cols = ", ".join(DRAFT_COLUMNS)
    placeholders = ", ".join("?" for _ in DRAFT_COLUMNS)
    updates = ", ".join(f"{col}=excluded.{col}" for col in DRAFT_COLUMNS if col not in {"draft_id", "created_at"})
    values = [payload[col] for col in DRAFT_COLUMNS]
    with connect(db_path) as conn:
        existing = conn.execute(
            f"SELECT draft_id, created_at, semantic_hash, status FROM {DRAFT_TABLE} WHERE source_version_id=?",
            (source_version_id,),
        ).fetchone()
        if existing:
            payload["created_at"] = existing["created_at"]
            values = [payload[col] for col in DRAFT_COLUMNS]
        conn.execute(
            f"INSERT INTO {DRAFT_TABLE} ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(source_version_id) DO UPDATE SET {updates}",
            values,
        )
        conn.commit()
        row = conn.execute(f"SELECT * FROM {DRAFT_TABLE} WHERE source_version_id=?", (source_version_id,)).fetchone()
    result = _row_to_dict(row)
    result["action"] = "updated" if existing else "inserted"
    return result


def get_draft(draft_id: str | None = None, source_version_id: str | None = None, db_path: str | os.PathLike[str] | None = None) -> dict[str, Any] | None:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        if draft_id:
            row = conn.execute(f"SELECT * FROM {DRAFT_TABLE} WHERE draft_id=?", (draft_id,)).fetchone()
        elif source_version_id:
            row = conn.execute(f"SELECT * FROM {DRAFT_TABLE} WHERE source_version_id=?", (source_version_id,)).fetchone()
        else:
            return None
    return _row_to_dict(row) if row else None


def list_drafts(
    *,
    project_id: str | None = None,
    status: str | None = None,
    source_version_id: str | None = None,
    query: str | None = None,
    lifecycle: str | None = "ACTIVE",
    limit: int = 100,
    offset: int = 0,
    db_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """List Luna draft projections.

    Default lifecycle='ACTIVE': historical Bootstrap analysis (ARCHIVED) stays
    out of the current work surface but remains fully queryable via
    lifecycle='ARCHIVED' or lifecycle=None (all).
    """
    ensure_schema(db_path)
    clauses = []
    params: list[Any] = []
    if lifecycle:
        clauses.append("lifecycle=?")
        params.append(lifecycle)
    if project_id:
        clauses.append("project_id=?")
        params.append(project_id)
    if status:
        clauses.append("status=?")
        params.append(status)
    if source_version_id:
        clauses.append("source_version_id=?")
        params.append(source_version_id)
    if query:
        clauses.append("(source_title LIKE ? OR summary LIKE ? OR source_version_id LIKE ?)")
        q = f"%{query}%"
        params.extend([q, q, q])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect(db_path) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM {DRAFT_TABLE} {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM {DRAFT_TABLE} {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    return {"items": [_row_to_dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


def draft_counts(db_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM {DRAFT_TABLE} WHERE lifecycle='ACTIVE'"
        ).fetchone()[0]
        by_status = {
            row[0]: row[1]
            for row in conn.execute(
                f"SELECT status, COUNT(*) FROM {DRAFT_TABLE} WHERE lifecycle='ACTIVE' GROUP BY status"
            ).fetchall()
        }
        # PAW v1.2 phase 3 (technical debt F): ``total`` and ``by_status`` are
        # scoped to ``lifecycle='ACTIVE'``, so ``by_project`` must be too —
        # otherwise the breakdown summed the 698 ARCHIVED drafts (≈710 rows)
        # while ``total`` reported 12, and the three numbers in one payload
        # described two different populations.
        by_project = {
            (row[0] or "(unassigned)"): row[1]
            for row in conn.execute(
                f"SELECT project_id, COUNT(*) FROM {DRAFT_TABLE} "
                "WHERE lifecycle='ACTIVE' GROUP BY project_id ORDER BY COUNT(*) DESC"
            ).fetchall()
        }
    return {"total": total, "by_status": by_status, "by_project": by_project}
