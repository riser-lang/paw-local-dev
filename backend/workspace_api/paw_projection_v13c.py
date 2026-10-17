#!/usr/bin/env python3
"""PAW v1.3C Lean Knowledge Serving - read-only projection layer.

Composes EXISTING deterministic services (v1.3B Retrieval Core +
Temporal Authority + workspace repository) into page DTOs.

Contract:
- Projection is presentation, NOT truth.
- NEW_TABLES = 0, NEW_DAEMONS = 0, MODEL_CALLS = 0.
- No business content is rewritten or generated.
- Authority / lineage / evidence states are disclosed, never flattened.
- Read-only: this module never writes to Formal State.
"""

from __future__ import annotations

import os


import sys
from pathlib import Path
from typing import Any

# v1.3B retrieval core lives in the current PAW release runtime.
_candidate_paths = [
    Path(__file__).resolve().parents[1 if Path(__file__).parent.name == "backend" else 2] / "runtime",
    Path(getattr(sys, "_MEIPASS", "")) / "backend" / "runtime",
    Path(getattr(sys, "_MEIPASS", "")) / "runtime",
]
for p in _candidate_paths:
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    import paw_retrieval_core as rc  # noqa: E402
except ImportError:
    from backend.runtime import paw_retrieval_core as rc  # noqa: E402

from . import repository as repo  # noqa: E402
# TD-002: the projection search route resolves recall through the single
# Retrieval Contract too, so every user-callable search entry point shares
# one recall implementation (including the deterministic phrase retry).
from . import retrieval_contract as _retrieval_contract  # noqa: E402

SCHEMA = "paw.projection.v13c/1"
DB_PATH = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))

# Readable Chinese presentation states (contract section 18).
AUTHORITY_LABELS = {
    "EFFECTIVE_CURRENT": "当前有效",
    "FUTURE_EFFECTIVE": "未来生效",
    "HISTORICAL": "历史版本",
    "ARCHIVED": "已归档",
    "UNRESOLVED": "待确认",
    "TEMPORAL_HISTORY_INCOMPLETE": "历史信息不完整",
}
EVIDENCE_LABELS = {
    "LOCATOR_READY": "证据可追溯",
    "PARTIAL": "来源可追溯 · 证据定位尚不完整",
    "NONE": "来源依据不可证明",
}


def _authority_label(state: str | None) -> str:
    return AUTHORITY_LABELS.get(state or "", "状态待确认")


def _evidence_label(state: str | None) -> str:
    return EVIDENCE_LABELS.get(state or "", "证据状态待确认")


def _freshness_label(updated_at: str | None) -> str:
    if not updated_at:
        return "信息待刷新"
    return "信息已同步"


def build_project_operating_projection(project_id: str) -> dict[str, Any] | None:
    """Deterministic Project Operating Page projection.
    Composes existing project context + v1.3B services. Read-only."""
    ctx = repo.project_context(project_id)
    if ctx is None:
        return None
    project = ctx.get("project", {})

    # Current rules: project-linked knowledge via v1.3B current-safe search
    # is too query-heavy for a page; use the already-admitted knowledge list
    # from the context (P0-2 admission policy) and enrich each with the
    # deterministic retrieval-safety gate.
    current_rules = []
    for k in (ctx.get("knowledge") or [])[:30]:
        kid = k.get("id")
        if not kid:
            continue
        safety = rc.evaluate_knowledge_retrieval_safety(kid, db_path=DB_PATH)
        current_rules.append({
            "knowledge_id": kid,
            "title": k.get("title") or "",
            "one_line_summary": k.get("one_line_summary") or "",
            "knowledge_type": k.get("knowledge_type"),
            "authority_state": safety.get("authority_state"),
            "authority_label": _authority_label(safety.get("authority_state")),
            "source_lineage_state": safety.get("source_lineage_state"),
            "evidence_state": safety.get("evidence_state"),
            "evidence_label": _evidence_label(safety.get("evidence_state")),
            "canonical": bool(safety.get("canonical")),
            "source_version_ids": safety.get("source_version_ids") or [],
        })

    # Formal metrics for this project (governed numbers only).
    # Prioritize metrics WITH governed observations so key governed
    # numbers (e.g. 运营导师固定补贴标准) are visible despite a large
    # definition-only catalogue (994 active definitions on this project).
    formal_metrics = []
    try:
        import sqlite3
        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as db:
            mids = [r[0] for r in db.execute(
                "SELECT md.id FROM metric_definition md"
                " LEFT JOIN metric_observation mo ON mo.metric_definition_id = md.id"
                " WHERE md.project_id = ? AND md.status = 'active'"
                " GROUP BY md.id"
                " ORDER BY COUNT(mo.id) DESC, md.name ASC LIMIT 30",
                (project_id,))]
        for mid in mids:
            m = rc.query_formal_metrics(metric_id=mid, db_path=DB_PATH)["results"][0]
            if not m.get("found"):
                continue
            observations = m.get("effective_current_observations") or []
            if not observations and m.get("effective_current_value") is None:
                continue  # definition without any governed number: skip card
            definition = m.get("definition") or {}
            metric_name = (definition.get("name") if isinstance(definition, dict) else None) or m.get("name") or mid
            dims = []
            for obs in observations[:12]:
                dimensions = obs.get("dimensions") if isinstance(obs, dict) else None
                dims.append({
                    "dimensions": dimensions if isinstance(dimensions, dict) else {},
                    "value": obs.get("value"),
                    "observed_at": obs.get("observed_at"),
                })
            formal_metrics.append({
                "metric_id": mid,
                "name": metric_name,
                "unit": m.get("unit") or (definition.get("unit") if isinstance(definition, dict) else None),
                "definition_authority": m.get("definition_authority"),
                "effective_current_value": m.get("effective_current_value"),
                "is_multidimensional": m.get("effective_current_value") is None and bool(dims),
                "dimension_rows": dims,
                "evidence_completeness": m.get("evidence_completeness"),
            })
    except Exception:
        formal_metrics = []

    # Recent business changes (v1.3B derived query; AI runs excluded).
    recent_changes = []
    try:
        changes = rc.get_recent_changes(
            project_id=project_id, limit=10, db_path=DB_PATH)
        for c in (changes.get("items") or []):
            recent_changes.append({
                "change_type": c.get("change_type"),
                "entity_type": c.get("entity_type"),
                "committed_at": c.get("committed_at"),
                "change_ref": c.get("change_ref"),
            })
    except Exception:
        recent_changes = []

    # Open items: project todos not done.
    open_items = [{
        "todo_id": t.get("id"),
        "title": t.get("title") or "",
        "status": t.get("status"),
        "due_at": t.get("due_at"),
    } for t in (ctx.get("todos") or [])
        if (t.get("status") or "") not in ("done", "cancelled")][:20]

    # Recent decisions from the existing decision read model.
    recent_decisions = [{
        "decision_id": d.get("id"),
        "title": d.get("title") or d.get("content") or "",
        "status": d.get("status"),
        "created_at": d.get("created_at"),
    } for d in (ctx.get("decisions") or [])[:10]]

    # Related projects via existing project_relation.
    related_projects = []
    try:
        import sqlite3
        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as db:
            rows = db.execute(
                "SELECT pr.relation_type, p.id, p.name, p.status FROM project_relation pr"
                " JOIN project p ON p.id = CASE WHEN pr.project_id = ? THEN pr.related_project_id"
                " ELSE pr.project_id END"
                " WHERE pr.project_id = ? OR pr.related_project_id = ? LIMIT 20",
                (project_id, project_id, project_id)).fetchall()
        related_projects = [
            {"project_id": r[1], "name": r[2], "status": r[3], "relation_type": r[0]}
            for r in rows if r[1] != project_id]
    except Exception:
        related_projects = []

    # History entry: honest lineage counts, no fabricated snapshot.
    history = {
        "handbook_revision": ctx.get("handbook_revision"),
        "knowledge_provisional_count": ctx.get("knowledge_provisional_count"),
        "knowledge_total": len(ctx.get("knowledge_all") or []),
        "valid_time_disclosure": "历史有效期信息不完整"
        if not _project_valid_time_proven(project_id) else None,
    }

    return {
        "schema": SCHEMA,
        "project": {
            "project_id": project_id,
            "name": project.get("name") or "",
            "description": project.get("description") or "",
            "status": project.get("status"),
            "status_label": _project_status_label(project.get("status")),
            "updated_at": project.get("updated_at"),
            "freshness_label": _freshness_label(project.get("updated_at")),
        },
        "current_rules": current_rules,
        "formal_metrics": formal_metrics,
        "recent_changes": recent_changes,
        "open_items": open_items,
        "recent_decisions": recent_decisions,
        "related_projects": related_projects,
        "history": history,
        "projection_metadata": {
            "deterministic": True,
            "model_call": False,
            "read_only": True,
        },
    }


def _project_valid_time_proven(project_id: str) -> bool:
    """True only when explicit valid-time metadata exists for this project's
    knowledge (knowledge_revision_authority rows). Never infers from
    timestamps."""
    try:
        import sqlite3
        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as db:
            n = db.execute(
                "SELECT COUNT(*) FROM knowledge_revision_authority kra"
                " JOIN knowledge_project_link kpl ON kpl.knowledge_id = kra.knowledge_id"
                " WHERE kpl.project_id = ?", (project_id,)).fetchone()[0]
        return n > 0
    except sqlite3.Error:
        return False


def _project_status_label(status: str | None) -> str:
    return {
        "active": "进行中", "in_progress": "进行中", "planning": "计划中",
        "paused": "暂停", "archived": "已归档", "done": "已完成",
        "completed": "已完成",
    }.get(status or "", "状态待确认")


def build_knowledge_detail_projection(knowledge_id: str) -> dict[str, Any] | None:
    """Deterministic Knowledge Detail projection: authority, valid time,
    revisions, evidence disclosure, related projects. Read-only."""
    data = repo.get_knowledge(knowledge_id)
    if data is None:
        return None
    page = data.get("page", {})
    safety = rc.evaluate_knowledge_retrieval_safety(
        knowledge_id, db_path=DB_PATH)

    # Historical revisions from the existing revision reader.
    revisions = []
    try:
        import sqlite3
        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as db:
            rows = db.execute(
                "SELECT id, revision_no, created_at FROM knowledge_revision"
                " WHERE knowledge_id = ? ORDER BY revision_no DESC LIMIT 50",
                (knowledge_id,)).fetchall()
        confirmed_no = page.get("confirmed_revision_no")
        for r in rows:
            is_current = confirmed_no is not None and r[1] == confirmed_no
            revisions.append({
                "revision_id": r[0], "revision_no": r[1],
                "created_at": r[2],
                "state_label": "当前版本" if is_current else "历史版本",
                "is_current": is_current,
            })
    except sqlite3.Error:
        revisions = []

    # Valid time: explicit metadata only, never inferred.
    valid_time = None
    valid_time_disclosure = "未记录明确业务生效时间"
    try:
        import sqlite3
        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as db:
            row = db.execute(
                "SELECT valid_from, valid_to FROM knowledge_revision_authority"
                " WHERE knowledge_id = ? AND revision_no = ?",
                (knowledge_id, page.get("confirmed_revision_no"))).fetchone()
        if row and (row[0] or row[1]):
            valid_time = {"valid_from": row[0], "valid_to": row[1]}
            valid_time_disclosure = None
    except sqlite3.Error:
        pass

    return {
        "schema": SCHEMA,
        "knowledge_id": knowledge_id,
        "title": page.get("title") or "",
        "one_line_summary": page.get("one_line_summary") or "",
        "knowledge_type": page.get("knowledge_type"),
        "status": page.get("status"),
        "authority_state": safety.get("authority_state"),
        "authority_label": _authority_label(safety.get("authority_state")),
        "source_lineage_state": safety.get("source_lineage_state"),
        "evidence_state": safety.get("evidence_state"),
        "evidence_label": _evidence_label(safety.get("evidence_state")),
        "canonical": bool(safety.get("canonical")),
        "provisional_reason": safety.get("provisional_reason"),
        "source_version_ids": safety.get("source_version_ids") or [],
        "evidence_refs": safety.get("evidence_refs") or [],
        "valid_time": valid_time,
        "valid_time_disclosure": valid_time_disclosure,
        "revisions": revisions,
        "sources": data.get("sources") or [],
        "related_projects": [
            {"project_id": p.get("id"), "name": p.get("name"),
             "relation_type": p.get("relation_type")}
            for p in (data.get("projects") or [])],
        "projection_metadata": {"deterministic": True, "model_call": False,
                                "read_only": True},
    }


def knowledge_search(query: str, *, limit: int = 20,
                     include_provisional: bool = False) -> dict[str, Any]:
    """User-facing knowledge search backed by v1.3B full-corpus
    current-safe retrieval (FTS trigram + bounded LIKE fallback + authority
    revalidation + lineage gate)."""
    r = _retrieval_contract.knowledge_search(
        query, limit=limit, include_provisional=include_provisional,
        db_path=DB_PATH)
    items = [{
        "knowledge_id": i.get("id") or i.get("entity_id"),
        "title": i.get("title") or "",
        "snippet": i.get("snippet") or "",
        "authority_state": i.get("authority_state"),
        "authority_label": _authority_label(i.get("authority_state")),
        "source_lineage_state": i.get("source_lineage_state"),
        "evidence_state": i.get("evidence_state"),
        "evidence_label": _evidence_label(i.get("evidence_state")),
        "canonical": bool(i.get("canonical")),
        "provisional_reason": i.get("provisional_reason"),
    } for i in (r.get("items") or [])]
    return {
        "schema": SCHEMA,
        "query": r.get("query"),
        "items": items,
        "count": r.get("count", 0),
        "incomplete_candidate_count": r.get("incomplete_candidate_count", 0),
        "untraceable_candidate_count": r.get("untraceable_candidate_count", 0),
        "stale_candidate_count": r.get("stale_candidate_count", 0),
        "include_provisional": include_provisional,
        "model_call": False,
        # TD-002: which retrieval contract served this search, and whether
        # the deterministic phrase retry was needed for an FTS-operator
        # query.  Disclosed, never flattened.
        "retrieval_contract": r.get("retrieval_contract"),
        "retrieval_query_mode": r.get("retrieval_query_mode"),
    }


def source_document_name(source_version_id: str) -> dict[str, Any]:
    """TD-004: the Source Document Name recorded for a source_version_id.

    Two RECORDED joins, tried in order, and nothing else:
      1. governance tier
           source_version_governance.source_version_id
             -> logical_document.logical_document_id -> display_name
      2. Source Registry tier
           wiki_page.wps_document_id = source_version_id -> title

    A name is never inferred from the object id, from a path, from a
    filename convention, or from the knowledge title.  ``source``
    discloses which recorded tier answered, so the surface is auditable.
    When neither records a name the state is NOT_RECORDED and the caller
    must show ``未记录关联资料``.
    """
    sv = (source_version_id or "").strip()
    if not sv:
        return {"name": None, "state": "NOT_RECORDED", "source": None}
    import sqlite3
    try:
        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as db:
            # Tier 1 - governance.
            row = db.execute(
                "SELECT ld.display_name FROM source_version_governance g "
                " JOIN logical_document ld "
                "   ON ld.logical_document_id = g.logical_document_id "
                " WHERE g.source_version_id = ?", (sv,)).fetchone()
            if row and row[0] and str(row[0]).strip():
                return {"name": str(row[0]).strip(), "state": "RECORDED",
                        "source": "source_version_governance"}
            # Tier 2 - Source Registry.  WPS-backed source versions record
            # their document name here (515 distinct ids, 0 ambiguous).
            row = db.execute(
                "SELECT title FROM wiki_page WHERE wps_document_id = ? "
                " AND title IS NOT NULL AND title <> ''", (sv,)).fetchone()
            if row and str(row[0]).strip():
                return {"name": str(row[0]).strip(), "state": "RECORDED",
                        "source": "source_registry"}
    except sqlite3.Error:
        return {"name": None, "state": "NOT_RECORDED", "source": None}
    return {"name": None, "state": "NOT_RECORDED", "source": None}


def evidence_lookup(source_version_id: str, locator: str) -> dict[str, Any]:
    """Controlled evidence drill-down through the v1.3B structural locator.
    Returns controlled content only; never exposes filesystem paths to the
    browser."""
    e = rc.get_evidence(source_version_id, locator, db_path=DB_PATH)
    # TD-004: the Source Document Name is only ever a RECORDED mapping.
    doc = source_document_name(source_version_id)
    out = {
        "schema": SCHEMA,
        "status": e.get("status"),
        "reason": e.get("reason"),
        "source_version_id": e.get("source_version_id"),
        "source_document_name": doc["name"],
        "source_document_name_state": doc["state"],
        "source_document_name_source": doc.get("source"),
        "source_document_name_label": (
            doc["name"] if doc["name"] else "未记录关联资料"),
        "locator": e.get("locator"),
        "block_type": e.get("block_type"),
        "block_index": e.get("block_index"),
        "excerpt": e.get("excerpt"),
        "identity_verification": e.get("identity_verification"),
        "raw_artifact_available": e.get("raw_artifact_available"),
        "fidelity": e.get("fidelity"),
        "model_call": False,
    }
    # Security: strip internal reader_result_path from the browser response.
    return out


__all__ = [
    "SCHEMA",
    "build_project_operating_projection",
    "build_knowledge_detail_projection",
    "knowledge_search",
    "evidence_lookup",
    "source_document_name",
]
