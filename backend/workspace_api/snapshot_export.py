"""Consistent read-only Workspace business snapshot export."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .db import DB_PATH


SENSITIVE_KEYS = {"token", "refresh_token", "authorization", "bearer", "cookie", "session", "secret", "password"}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "<REDACTED>" if key.casefold() in SENSITIVE_KEYS else _redact(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_redact(child) for child in value]
    return value


def _rows(conn: sqlite3.Connection, table: str, columns: str = "*") -> list[dict[str, Any]]:
    rows = conn.execute(f'SELECT {columns} FROM "{table}"').fetchall()
    result = []
    for row in rows:
        item = dict(row)
        if table == "knowledge_revision":
            try:
                item["content_json"] = json.loads(item["content_json"])
            except (TypeError, json.JSONDecodeError):
                pass
        result.append(_redact(item))
    return result


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def export_workspace_snapshot() -> dict[str, Any]:
    """Read all business tables from one SQLite read transaction.

    The connection is opened read-only, so this endpoint cannot migrate or
    write the Workspace database.  Capture body text is intentionally omitted;
    the export contains capture metadata only.
    """
    uri = f"file:{DB_PATH.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        schema_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        knowledge = {
            "knowledge_page": _rows(conn, "knowledge_page"),
            "knowledge_revision": _rows(conn, "knowledge_revision"),
            "knowledge_source_link": _rows(conn, "knowledge_source_link"),
            "knowledge_project_link": _rows(conn, "knowledge_project_link"),
        }
        projects = _rows(conn, "project")
        todos = _rows(conn, "todo")
        decisions = _rows(conn, "decision")
        pending_items = _rows(conn, "pending_item")
        wiki = _rows(conn, "wiki_page")
        daily = _rows(conn, "daily")
        captures = _rows(
            conn,
            "capture",
            "id,daily_id,captured_at,source,source_device_id,user_edited,sync_status,wps_anchor,processed_at,created_at,updated_at",
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    files = {
        "knowledge.json": knowledge,
        "projects.json": {
            "projects": projects,
            "contexts": [
                {
                    "project_id": project.get("id"),
                    "todos": [todo for todo in todos if todo.get("project_id") == project.get("id")],
                    "decisions": [decision for decision in decisions if decision.get("project_id") == project.get("id")],
                    "pending_items": [item for item in pending_items if item.get("project_id") == project.get("id")],
                }
                for project in projects
            ],
        },
        "todos.json": todos,
        "wiki.json": wiki,
        "daily.json": daily,
        "capture.json": captures,
    }
    sha256 = {name: hashlib.sha256(_canonical_bytes(value)).hexdigest() for name, value in files.items()}
    record_count = {
        "knowledge_page": len(knowledge["knowledge_page"]),
        "knowledge_revision": len(knowledge["knowledge_revision"]),
        "knowledge_source_link": len(knowledge["knowledge_source_link"]),
        "knowledge_project_link": len(knowledge["knowledge_project_link"]),
        "project": len(projects),
        "todo": len(todos),
        "decision": len(decisions),
        "pending_item": len(pending_items),
        "wiki_page": len(wiki),
        "daily": len(daily),
        "capture_metadata": len(captures),
    }
    return {
        "export_time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "schema_version": 1,
        "database_schema_version": schema_version,
        "record_count": record_count,
        "sha256": sha256,
        "files": files,
    }
