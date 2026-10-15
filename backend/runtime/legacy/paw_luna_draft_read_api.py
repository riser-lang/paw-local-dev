"""Read-only Luna Draft Projection API handlers."""
from __future__ import annotations

from typing import Any, Mapping

from paw_luna_draft_materializer import materialize_source
from paw_luna_draft_store import draft_counts, ensure_schema, get_draft, list_drafts, migrate


def h_list_drafts(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    return list_drafts(
        project_id=params.get("project_id"),
        status=params.get("status"),
        source_version_id=params.get("source_version_id"),
        query=params.get("query"),
        limit=int(params.get("limit") or 100),
        offset=int(params.get("offset") or 0),
        db_path=params.get("db_path"),
    )


def h_get_draft(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    draft_id = params.get("draft_id")
    source_version_id = params.get("source_version_id")
    if not draft_id and not source_version_id:
        raise ValueError("draft_id or source_version_id is required")
    record = get_draft(draft_id=draft_id, source_version_id=source_version_id, db_path=params.get("db_path"))
    if record is None:
        return {"found": False, "formal_state_mutated": False}
    return {"found": True, "draft": record, "authority_level": "draft", "is_canonical": False, "formal_state_mutated": False}


def h_search_drafts(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    result = list_drafts(
        project_id=params.get("project_id"),
        status=params.get("status"),
        query=params.get("query"),
        limit=int(params.get("limit") or 50),
        offset=int(params.get("offset") or 0),
        db_path=params.get("db_path"),
    )
    return {
        "items": result["items"],
        "total": result["total"],
        "authority_level": "draft",
        "is_canonical": False,
        "formal_state_mutated": False,
    }


def h_draft_counts(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    return {"counts": draft_counts(params.get("db_path")), "formal_state_mutated": False}


def h_sync_draft_status(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Lazy refresh for one source — read-only status sync, no review consumption."""
    params = params or {}
    source_version_id = params.get("source_version_id")
    if not source_version_id:
        raise ValueError("source_version_id is required")
    import json
    import os
    from pathlib import Path
    from paw_gpt_review_bundle import ReviewBundleStore, default_brain_path
    from paw_luna_draft_semantic import LunaSemanticReader

    brain = json.loads(Path(params.get("brain_path") or default_brain_path()).read_text(encoding="utf-8"))
    reader = LunaSemanticReader()
    queue_map = {item["source_version_id"]: item for item in reader.queue_sources()}
    record = materialize_source(
        str(source_version_id),
        brain=brain,
        bundle_store=ReviewBundleStore(),
        reader=reader,
        db_path=str(params.get("db_path") or os.environ.get("PAW_WORKSPACE_DB", os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db")))),
        queue_entry=queue_map.get(str(source_version_id)),
    )
    return {"synced": True, "draft": record, "formal_state_mutated": False}


def h_ensure_schema(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    return ensure_schema(params.get("db_path"))
