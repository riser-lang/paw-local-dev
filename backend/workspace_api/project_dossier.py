"""Deterministic Project Dossier collector.

This module only gathers, deduplicates, time-sorts and identity-tags existing
Project / Knowledge / Todo / Luna / Source references.  It does not write
business summaries, classify projects, decide rule supersession, invent
history, or generate a Handbook.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import DB_PATH

DOSSIER_SCHEMA = "paw-project-dossier/1"
COLLECTOR = "project-dossier-collector"


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _trust(
    source_version_id: Any,
    knowledge_id: Any = None,
    *,
    knowledge_status: Any = None,
    confirmed_revision_no: Any = None,
    latest_revision_no: Any = None,
) -> str:
    """P0-6: evidence trust must reflect the Knowledge authority state.

    A Knowledge reference is TRUSTED only when the page is ``current`` AND its
    confirmation covers the latest revision - a bare id is never sufficient.
    ``source_version_id`` is a SourceVersion ID only; a WPS document id is not
    a SourceVersion ID and must not be passed here.
    """
    if knowledge_id:
        from .current_context_policy import is_current_confirmed_knowledge

        return "TRUSTED" if is_current_confirmed_knowledge({
            "status": knowledge_status,
            "confirmed_revision_no": confirmed_revision_no,
            "latest_revision_no": latest_revision_no,
        }) else "PROVISIONAL"
    if source_version_id:
        return "PROVISIONAL"
    return "UNTRUSTED"


def collect_project_dossier(project_id: str, *, db_path: str | None = None) -> dict[str, Any]:
    """Collect a Project Dossier.  No semantic synthesis."""
    conn = sqlite3.connect(db_path or str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        project = conn.execute("SELECT * FROM project WHERE id=?", (project_id,)).fetchone()
        if project is None:
            raise ValueError(f"Project not found: {project_id}")
        outcome = conn.execute("SELECT * FROM project_outcome WHERE project_id=?", (project_id,)).fetchone()
        handbook = conn.execute(
            "SELECT project_id,revision,quality_gate,evidence_sufficient,generated_at,updated_at FROM project_handbook WHERE project_id=?",
            (project_id,),
        ).fetchone()
        todos = [dict(r) for r in conn.execute(
            "SELECT id,title,description,status,priority,due_at,created_at,updated_at FROM todo WHERE project_id=? ORDER BY created_at,id",
            (project_id,),
        ).fetchall()]
        # P0-6: the authority columns come from the Knowledge Confirmation
        # Gate migration, which a schema-replay database does not carry.  The
        # read degrades instead of raising; degrading is fail closed because a
        # row without confirmed_revision_no can never be CURRENT_CONFIRMED.
        _knowledge_governed = """SELECT kp.id,kp.title,kp.status,kp.updated_at,kp.confirmed_revision_no,kpl.relation_type,
                      (SELECT MAX(revision_no) FROM knowledge_revision kr WHERE kr.knowledge_id=kp.id) AS latest_revision_no
               FROM knowledge_page kp
               JOIN knowledge_project_link kpl ON kpl.knowledge_id=kp.id
               WHERE kpl.project_id=?
               ORDER BY kp.updated_at DESC, kp.id"""
        _knowledge_base = """SELECT kp.id,kp.title,kp.status,kp.updated_at,kpl.relation_type
               FROM knowledge_page kp
               JOIN knowledge_project_link kpl ON kpl.knowledge_id=kp.id
               WHERE kpl.project_id=?
               ORDER BY kp.updated_at DESC, kp.id"""
        try:
            knowledge = [dict(r) for r in conn.execute(_knowledge_governed, (project_id,)).fetchall()]
        except sqlite3.OperationalError:
            knowledge = [dict(r) for r in conn.execute(_knowledge_base, (project_id,)).fetchall()]
        sources = [dict(r) for r in conn.execute(
            """SELECT w.id,w.title,w.category,w.wps_document_id,w.source_origin,w.canonical_state,w.updated_at
               FROM knowledge_project_link kpl
               JOIN knowledge_source_link ksl ON ksl.knowledge_id=kpl.knowledge_id
               JOIN wiki_page w ON w.id=ksl.source_page_id
               WHERE kpl.project_id=?
               GROUP BY w.id
               ORDER BY w.updated_at DESC, w.id""",
            (project_id,),
        ).fetchall()]
        # P1-1: Current Project Context admits only temporal_state='current'
        # relations.  Non-current relations are counted for disclosure rather
        # than silently dropped, and are never deleted.
        relations = [dict(r) for r in conn.execute(
            "SELECT relation_id,from_project_id,to_project_id,relation_type,summary,confidence,temporal_state FROM project_relation WHERE (from_project_id=? OR to_project_id=?) AND temporal_state='current' ORDER BY relation_id",
            (project_id, project_id),
        ).fetchall()]
        relations_excluded_non_current = conn.execute(
            "SELECT COUNT(*) FROM project_relation WHERE (from_project_id=? OR to_project_id=?) AND temporal_state<>'current'",
            (project_id, project_id),
        ).fetchone()[0]
        drafts: list[dict[str, Any]] = []
        try:
            drafts = [dict(r) for r in conn.execute(
                """SELECT draft_id,source_version_id,source_title,summary,status,updated_at
                   FROM luna_draft_projection
                   WHERE project_id=?
                   ORDER BY updated_at DESC
                   LIMIT 50""",
                (project_id,),
            ).fetchall()]
        except sqlite3.OperationalError:
            drafts = []

        evidence = []
        seen: set[str] = set()
        for kn in knowledge:
            key = f"knowledge:{kn['id']}"
            if key in seen:
                continue
            seen.add(key)
            evidence.append({
                "knowledge_id": kn["id"],
                "title": kn.get("title"),
                "source_version_id": None,
                "locator": "knowledge_revision.latest",
                "trust": _trust(
                    None,
                    kn["id"],
                    knowledge_status=kn.get("status"),
                    confirmed_revision_no=kn.get("confirmed_revision_no"),
                    latest_revision_no=kn.get("latest_revision_no"),
                ),
                "updated_at": kn.get("updated_at"),
            })
        for src in sources:
            key = f"source:{src['id']}"
            if key in seen:
                continue
            seen.add(key)
            evidence.append({
                "knowledge_id": None,
                "title": src.get("title"),
                # P0-6: wps_document_id is a WPS document identifier, NOT a
                # SourceVersion ID.  It is reported under its own key and must
                # never be presented as source_version_id.
                "source_version_id": None,
                "wps_document_id": src.get("wps_document_id"),
                "locator": f"wiki_page:{src['id']}",
                "trust": "PROVISIONAL" if src.get("canonical_state") in {None, "unclassified", "historical"} else "TRUSTED",
                "updated_at": src.get("updated_at"),
            })
        evidence.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)

        return {
            "schema_version": DOSSIER_SCHEMA,
            "collector": COLLECTOR,
            "project_id": project_id,
            "project": {
                "id": project["id"],
                "name": project["name"],
                "status": project["status"],
                "stage": project["stage"],
                "updated_at": project["updated_at"],
                "latest_progress_summary": project["latest_progress_summary"] if "latest_progress_summary" in project.keys() else None,
                "next_action_summary": project["next_action_summary"] if "next_action_summary" in project.keys() else None,
            },
            "canonical_outcome_meta": {
                "synthesis_revision": outcome["synthesis_revision"] if outcome else None,
                "quality_gate": outcome["quality_gate"] if outcome else None,
                "evidence_sufficient": bool(outcome["evidence_sufficient"]) if outcome else False,
            } if outcome else None,
            "canonical_handbook_meta": dict(handbook) if handbook else None,
            "todos": todos,
            "knowledge_refs": knowledge,
            "source_refs": sources,
            "luna_draft_refs": drafts,
            "relations": relations,
            "relations_excluded_non_current": relations_excluded_non_current,
            "evidence": evidence,
            "semantic_synthesis": None,
            "business_summary": None,
            "rule_supersession": None,
            "future_direction": None,
            "handbook": None,
        }
    finally:
        conn.close()


__all__ = ["COLLECTOR", "DOSSIER_SCHEMA", "collect_project_dossier"]
