"""Project Context Builder V1 — Read-Only Workspace Context Aggregator.

This module is DESIGN-ONLY. It is not imported by any production service.
It provides structured, read-only project context for AI analysis.

SAFETY: This module has ZERO write capability. It opens the database
in read-only mode with query_only=ON. It cannot modify any data.
"""

from __future__ import annotations

import os


import sqlite3
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db")))


def _connect() -> sqlite3.Connection:
    """Open a strictly read-only connection."""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def build_project_context(project_id: str) -> Optional[dict[str, Any]]:
    """Build a structured, read-only project context for AI consumption.

    Returns None if the project does not exist.
    This function performs ZERO writes. It only aggregates data.
    """
    with _connect() as conn:
        project = conn.execute(
            "SELECT * FROM project WHERE id=?", (project_id,)
        ).fetchone()
        if project is None:
            return None

        open_todos = [
            dict(r) for r in conn.execute(
                "SELECT id, title, status, priority, due_at, waiting_for, "
                "source_type, ai_generated, created_at, updated_at "
                "FROM todo WHERE project_id=? AND status='open' "
                "ORDER BY created_at DESC LIMIT 50",
                (project_id,),
            ).fetchall()
        ]

        recent_done_todos = [
            dict(r) for r in conn.execute(
                "SELECT id, title, status, completed_at, updated_at "
                "FROM todo WHERE project_id=? AND status='done' "
                "ORDER BY updated_at DESC LIMIT 20",
                (project_id,),
            ).fetchall()
        ]

        draft_knowledge = [
            {
                "id": r["id"],
                "title": r["title"],
                "knowledge_type": r["knowledge_type"],
                "status": r["status"],
                "latest_revision_no": r["latest_rev_no"],
                "confirmed_revision_no": r["confirmed_revision_no"] if "confirmed_revision_no" in r.keys() else None,
                "relation_type": r["relation_type"],
            }
            for r in conn.execute(
                "SELECT kp.id, kp.title, kp.knowledge_type, kp.status, "
                "kp.confirmed_revision_no, kpl.relation_type, "
                "(SELECT MAX(revision_no) FROM knowledge_revision kr "
                " WHERE kr.knowledge_id=kp.id) AS latest_rev_no "
                "FROM knowledge_page kp "
                "JOIN knowledge_project_link kpl ON kpl.knowledge_id=kp.id "
                "WHERE kpl.project_id=? AND kp.status='draft' "
                "ORDER BY kp.updated_at DESC",
                (project_id,),
            ).fetchall()
        ]

        confirmed_knowledge = [
            {
                "id": r["id"],
                "title": r["title"],
                "knowledge_type": r["knowledge_type"],
                "status": r["status"],
                "latest_revision_no": r["latest_rev_no"],
                "confirmed_revision_no": r["confirmed_revision_no"] if "confirmed_revision_no" in r.keys() else None,
                "relation_type": r["relation_type"],
            }
            for r in conn.execute(
                "SELECT kp.id, kp.title, kp.knowledge_type, kp.status, "
                "kp.confirmed_revision_no, kpl.relation_type, "
                "(SELECT MAX(revision_no) FROM knowledge_revision kr "
                " WHERE kr.knowledge_id=kp.id) AS latest_rev_no "
                "FROM knowledge_page kp "
                "JOIN knowledge_project_link kpl ON kpl.knowledge_id=kp.id "
                "WHERE kpl.project_id=? AND kp.status='current' "
                "ORDER BY kp.updated_at DESC",
                (project_id,),
            ).fetchall()
        ]

        # Project-level source aggregation: link_total + unique_source_total + distributions
        link_total_row = conn.execute(
            "SELECT COUNT(*) FROM knowledge_source_link ksl "
            "JOIN knowledge_project_link kpl ON kpl.knowledge_id=ksl.knowledge_id "
            "WHERE kpl.project_id=?",
            (project_id,),
        ).fetchone()
        link_total = link_total_row[0] if link_total_row else 0

        unique_source_row = conn.execute(
            "SELECT COUNT(DISTINCT ksl.source_page_id) FROM knowledge_source_link ksl "
            "JOIN knowledge_project_link kpl ON kpl.knowledge_id=ksl.knowledge_id "
            "WHERE kpl.project_id=?",
            (project_id,),
        ).fetchone()
        unique_source_total = unique_source_row[0] if unique_source_row else 0

        def _dist(col):
            return {
                (r[0] if r[0] is not None else "null"): r[1]
                for r in conn.execute(
                    f"SELECT w.{col}, COUNT(*) FROM knowledge_source_link ksl "
                    f"JOIN knowledge_project_link kpl ON kpl.knowledge_id=ksl.knowledge_id "
                    f"JOIN wiki_page w ON w.id=ksl.source_page_id "
                    f"WHERE kpl.project_id=? GROUP BY w.{col}",
                    (project_id,),
                ).fetchall()
            }

        def _role_dist():
            return {
                r[0]: r[1]
                for r in conn.execute(
                    "SELECT ksl.source_role, COUNT(*) "
                    "FROM knowledge_source_link ksl "
                    "JOIN knowledge_project_link kpl ON kpl.knowledge_id=ksl.knowledge_id "
                    "WHERE kpl.project_id=? GROUP BY ksl.source_role",
                    (project_id,),
                ).fetchall()
            }

        knowledge_source_summary = {
            "link_total": link_total,
            "unique_source_total": unique_source_total,
            "by_origin": _dist("source_origin"),
            "by_canonical_state": _dist("canonical_state"),
            "by_role": _role_dist(),
        }

        recent_daily_refs = [
            {"daily_id": r["id"], "date": r["date"], "capture_count": r["cnt"]}
            for r in conn.execute(
                "SELECT d.id, d.date, "
                "(SELECT COUNT(*) FROM capture c WHERE c.daily_id=d.id) AS cnt "
                "FROM daily d ORDER BY d.date DESC LIMIT 5",
            ).fetchall()
        ]

        recent_capture_refs = [
            {"capture_id": r["id"], "captured_at": r["captured_at"],
             "source": r["source"], "processed_at": r["processed_at"]}
            for r in conn.execute(
                "SELECT id, captured_at, source, processed_at "
                "FROM capture ORDER BY captured_at DESC LIMIT 5",
            ).fetchall()
        ]

        latest_activity = conn.execute(
            "SELECT MAX(updated_at) FROM ("
            "  SELECT updated_at FROM todo WHERE project_id=? "
            "  UNION "
            "  SELECT kp.updated_at FROM knowledge_page kp "
            "  JOIN knowledge_project_link kpl ON kpl.knowledge_id=kp.id "
            "  WHERE kpl.project_id=?"
            ")",
            (project_id, project_id),
        ).fetchone()[0]

        return {
            "project": dict(project),
            "open_todos": open_todos,
            "recent_done_todos": recent_done_todos,
            "draft_knowledge": draft_knowledge,
            "confirmed_knowledge": confirmed_knowledge,
            "knowledge_source_summary": knowledge_source_summary,
            "recent_daily_refs": recent_daily_refs,
            "recent_capture_refs": recent_capture_refs,
            "open_questions": [],
            "known_conflicts": [],
            "candidate_summary": [],
            "latest_activity": latest_activity,
        }
