from __future__ import annotations

import sqlite3

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from .db import status as db_status
from .schemas import (
    CaptureCreate,
    CaptureSearchResponse,
    DailyCalendarResponse,
    DailyHistoryResponse,
    KnowledgeConfirmRequest,
    KnowledgeConfirmResponse,
    KnowledgeDetailResponse,
    KnowledgeListResponse,
    KnowledgeUpsertRequest,
    ProjectCreate,
    SourceRegisterRequest,
    TodoCreate,
    TodoUpdate,
    WorkspaceBridgeStatus,
    WpsDocumentPreviewRequest,
    WpsDocumentPreviewResponse,
    WpsInventoryPreviewResponse,
    WpsListPreviewRequest,
    WpsSearchPreviewRequest,
)
from . import repository as repo
from .daily_sync import DEVICE_ID, DailySyncError, _run_job, sync_capture
from . import daily_sync as _daily_sync_mod
from .snapshot_export import export_workspace_snapshot

router = APIRouter()


@router.get("/snapshot/export", operation_id="exportWorkspaceSnapshot")
def snapshot_export():
    """Return a consistent, read-only JSON business snapshot."""
    try:
        return export_workspace_snapshot()
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail={"code": "SNAPSHOT_EXPORT_FAILED", "message": "Workspace snapshot export unavailable"}) from exc

def dump_model(model):
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()

def dump_model_unset(model):
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=True)
    return model.dict(exclude_unset=True)


def dump_knowledge_upsert(model):
    """Dump the request while preserving whether content_json was supplied."""
    data = dump_model(model)
    if hasattr(model, "model_fields_set"):
        fields_set = model.model_fields_set
    else:
        fields_set = getattr(model, "__fields_set__", set())
    data["content_json_present"] = "content_json" in fields_set
    data["_fields_set"] = set(fields_set)
    return data

@router.get("/health", operation_id="workspaceHealth")
def health():
    return {"ok": True, "database": db_status(), "phase": "1A"}

@router.get("/today", operation_id="getWorkspaceToday")
def today():
    return repo.today_view()


@router.get("/daily", operation_id="getWorkspaceDaily", response_model=DailyHistoryResponse)
def daily(date: str | None = Query(default=None)):
    try:
        return repo.daily_view(date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/daily/calendar", operation_id="getWorkspaceDailyCalendar", response_model=DailyCalendarResponse)
def daily_calendar(month: str = Query(..., pattern=r"^\d{4}-\d{2}$")):
    try:
        return repo.daily_calendar(month)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@router.post("/captures", operation_id="createWorkspaceCapture")
def create_capture(body: CaptureCreate, background_tasks: BackgroundTasks):
    """Persist raw Capture and register it for the next GPT Patrol / Local Cruise.

    processed_at stays NULL until cruise/patrol consumes/resolves the Capture.
    """
    cap = repo.create_capture(body.raw_text, body.captured_at, body.source)
    try:
        from backend.runtime_core.scheduler import scheduler
        background_tasks.add_task(scheduler.process_capture, cap["id"])
    except Exception:
        pass
    return cap

@router.get("/captures/unprocessed", operation_id="getUnprocessedWorkspaceCaptures")
def captures_unprocessed(limit: int = Query(100, ge=1, le=500)):
    return {"items": repo.unprocessed_captures(limit)}


@router.get("/captures/search", operation_id="searchWorkspaceCaptures", response_model=CaptureSearchResponse)
def captures_search(
    q: str = Query(..., min_length=1, max_length=200),
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    limit: int = Query(50, ge=1, le=50),
    cursor: str | None = Query(default=None, max_length=32),
):
    try:
        return repo.search_captures(
            q,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
            cursor=cursor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@router.post("/captures/{capture_id}/processed", operation_id="markWorkspaceCaptureProcessed")
def mark_capture_processed(capture_id: str):
    data = repo.mark_capture_processed(capture_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Capture not found")
    return data

@router.post("/captures/{capture_id}/sync-daily", operation_id="syncWorkspaceCaptureDaily")
def sync_capture_daily(capture_id: str):
    try:
        data = sync_capture(capture_id)
    except DailySyncError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if data is None:
        raise HTTPException(status_code=404, detail="Capture not found")
    return data


@router.get(
    "/bridge/status",
    operation_id="getWorkspaceBridgeStatus",
    response_model=WorkspaceBridgeStatus,
)
def workspace_bridge_status():
    try:
        store = _daily_sync_mod._bridge_store
        if store is not None:
            snapshot = store.snapshot()
        else:
            snapshot = {"devices": []}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    device = next(
        (item for item in snapshot.get("devices", []) if item.get("device_id") == DEVICE_ID),
        None,
    )
    if not device:
        return {
            "bridge_online": False,
            "wps_available": "unknown",
            "capabilities": [],
            "last_seen_at": None,
            "adapter_status": {"state": "unknown"},
        }
    integration = device.get("integration") if isinstance(device.get("integration"), dict) else {}
    read_only = {"wps.search", "wps.list", "wps.read"}
    capabilities = sorted(read_only.intersection(device.get("capabilities") or []))
    bridge_online = bool(device.get("bridge_online", device.get("connected")))
    raw_available = device.get("wps_available", integration.get("wps_available"))
    if isinstance(raw_available, bool):
        wps_available = "available" if raw_available else "unavailable"
    elif raw_available in {"available", "unavailable", "unknown", "quota_limited"}:
        wps_available = raw_available
    elif integration.get("wps_mcp") in {"available", "unavailable", "unknown", "quota_limited"}:
        wps_available = integration["wps_mcp"]
    elif capabilities and bridge_online:
        wps_available = "available"
    else:
        wps_available = "unavailable" if bridge_online else "unknown"
    if not bridge_online and wps_available == "available":
        wps_available = "unknown"
    if not capabilities and wps_available == "available":
        wps_available = "unavailable"
    adapter = device.get("adapter") or integration.get("adapter") or "unknown"
    adapter_status = device.get("adapter_status") or integration.get("adapter_status")
    if isinstance(adapter_status, str):
        adapter_status = {"state": adapter_status}
    if not isinstance(adapter_status, dict):
        adapter_status = {"state": wps_available}
    adapter_status = {key: value for key, value in adapter_status.items() if key in {"state", "detail", "version"}}
    adapter_status.setdefault("state", wps_available)
    adapter_status["adapter"] = str(adapter)
    return {
        "bridge_online": bridge_online,
        "wps_available": wps_available,
        "capabilities": capabilities,
        "last_seen_at": device.get("last_seen_at"),
        "adapter_status": adapter_status,
    }


@router.post(
    "/wps/preview",
    operation_id="previewWpsDocument",
    response_model=WpsDocumentPreviewResponse,
)
def preview_wps_document(body: WpsDocumentPreviewRequest):
    params = {"document_id": body.document_id, "max_chars": body.max_chars}
    if body.cursor:
        params["cursor"] = body.cursor
    data = _run_read_only_job(
        "wps.read",
        params,
    )
    content = data.get("content") if isinstance(data.get("content"), str) else ""
    document_type = "ksheet" if data.get("document_type") == "ksheet" else "document"
    structured = {
        "sheet_index": data.get("sheet_index") if isinstance(data.get("sheet_index"), list) else [],
        "sheets": data.get("sheets") if isinstance(data.get("sheets"), list) else [],
        "tables": data.get("tables") if isinstance(data.get("tables"), list) else [],
    }
    returned_chars = len(content)
    if document_type == "ksheet":
        import json
        returned_chars = len(json.dumps(structured, ensure_ascii=False, separators=(",", ":")))
    return {
        "document_id": body.document_id,
        "title": data.get("title") if isinstance(data.get("title"), str) else None,
        "document_type": document_type,
        "content": content,
        **structured,
        "returned_chars": returned_chars,
        "truncated": bool(data.get("truncated")),
        "next_cursor": data.get("next_cursor") if isinstance(data.get("next_cursor"), str) else None,
    }


@router.post(
    "/wps/list/preview",
    operation_id="previewWpsList",
    response_model=WpsInventoryPreviewResponse,
)
def preview_wps_list(body: WpsListPreviewRequest):
    params = {key: value for key, value in dump_model(body).items() if value is not None}
    return _inventory_response(_run_read_only_job("wps.list", params))


@router.post(
    "/wps/search/preview",
    operation_id="previewWpsSearch",
    response_model=WpsInventoryPreviewResponse,
)
def preview_wps_search(body: WpsSearchPreviewRequest):
    params = {key: value for key, value in dump_model(body).items() if value is not None}
    return _inventory_response(_run_read_only_job("wps.search", params))


def _run_read_only_job(operation: str, params: dict):
    if operation not in {"wps.read", "wps.list", "wps.search"}:
        raise RuntimeError("Workspace WPS operation is not read-only")
    try:
        return _run_job(operation, params)
    except DailySyncError as exc:
        public = {
            "UNSUPPORTED_KSHEET_STRUCTURE": (422, "unsupported_ksheet_structure"),
            "WPS_QUOTA_EXHAUSTED": (429, "quota_limited"),
            "WPS_ADAPTER_UNAVAILABLE": (503, "wps_adapter_unavailable"),
            "WPS_AUTH_UNAVAILABLE": (503, "wps_auth_unavailable"),
            "WORKBUDDY_UNAVAILABLE": (503, "wps_adapter_unavailable"),
            "WORKBUDDY_AUTH_UNAVAILABLE": (503, "wps_auth_unavailable"),
            "MCP_DISCOVERY_FAILED": (503, "wps_adapter_unavailable"),
            "MCP_HANDSHAKE_FAILED": (503, "wps_adapter_unavailable"),
            "MCP_TOOL_NOT_AVAILABLE": (503, "wps_adapter_unavailable"),
            "SHEET_NOT_FOUND": (404, "sheet_not_found"),
            "WPS_READ_FAILED": (502, "read_failed"),
        }
        status, code = public.get(exc.code, (503, "read_failed"))
        messages = {
            "quota_limited": "WPS 配额暂时受限。",
            "wps_adapter_unavailable": "WPS 适配器暂时不可用。",
            "wps_auth_unavailable": "WPS 授权暂时不可用。",
            "unsupported_ksheet_structure": "KSheet 结构暂不支持。",
            "sheet_not_found": "未找到指定工作表。",
            "read_failed": "WPS 读取失败。",
        }
        raise HTTPException(status_code=status, detail={"code": code, "message": messages.get(code, "WPS 操作失败。")}) from exc


def _inventory_response(data: dict):
    allowed = {
        "document_id",
        "drive_id",
        "title",
        "type",
        "parent_id",
        "modified_at",
        "size",
        "shared",
    }
    items = [
        {key: value for key, value in item.items() if key in allowed}
        for item in data.get("items", [])
        if isinstance(item, dict)
    ]
    return {
        "items": items,
        "next_cursor": data.get("next_cursor"),
        "total": data.get("total"),
    }

@router.get("/todos", operation_id="getWorkspaceTodos")
def todos(
    status: str | None = None,
    limit: int = Query(200, ge=1, le=500),
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    project_id: str | None = Query(default=None),
    undated: bool = Query(default=False),
    overdue: bool = Query(default=False),
):
    try:
        return {
            "items": repo.list_todos(
                status,
                limit,
                from_date=from_date,
                to_date=to_date,
                project_id=project_id,
                undated=undated,
                overdue=overdue,
            )
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@router.post("/todos", operation_id="createWorkspaceTodo")
def create_todo(body: TodoCreate):
    return repo.create_todo(dump_model(body))

@router.patch("/todos/{todo_id}", operation_id="updateWorkspaceTodo")
def update_todo(todo_id: str, body: TodoUpdate):
    changes = dump_model_unset(body)
    if not changes:
        raise HTTPException(status_code=400, detail="At least one Todo field is required")
    data = repo.update_todo(todo_id, changes)
    if data is None:
        raise HTTPException(status_code=404, detail="Todo not found")
    return data

@router.delete("/todos/{todo_id}", operation_id="deleteWorkspaceTodo")
def delete_todo(todo_id: str):
    data = repo.delete_todo(todo_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Todo not found")
    return {"deleted": True, "todo": data}

@router.get("/projects", operation_id="getWorkspaceProjects")
def projects(limit: int = Query(200, ge=1, le=500)):
    return {"items": repo.list_projects(limit)}

@router.post("/projects", operation_id="createWorkspaceProject")
def create_project(body: ProjectCreate):
    return repo.create_project(dump_model(body))

@router.get("/projects/{project_id}/context", operation_id="getWorkspaceProjectContext")
def project_context(
    project_id: str,
    include_drafts: bool = Query(True),
    include_brain: bool = Query(True),
):
    data = repo.project_context(project_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not include_drafts:
        data.pop("drafts", None)
        data.pop("draft_count", None)
        data.pop("draft_authority_level", None)
    if not include_brain:
        for key in (
            "brain_projection_found",
            "draft_context",
            "draft_knowledge_topics",
            "data_summary",
            "timeline",
            "draft_decisions",
            "draft_risks",
            "draft_conflicts",
            "draft_ambiguities",
            "source_evidence",
            "canonical_project",
        ):
            data.pop(key, None)
    return data


@router.get("/projects/{project_id}/brain/data", operation_id="getWorkspaceProjectBrainData")
def project_brain_data(
    project_id: str,
    observation_limit: int = Query(20, ge=1, le=100),
    asset_limit: int = Query(20, ge=1, le=100),
    conflict_limit: int = Query(20, ge=1, le=100),
):
    summary = repo.project_brain_data_summary(
        project_id,
        observation_limit=observation_limit,
        asset_limit=asset_limit,
        conflict_limit=conflict_limit,
    )
    if summary is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return summary

@router.get("/drafts", operation_id="getWorkspaceDrafts")
def list_drafts(
    project_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    source_version_id: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    repo.ensure_luna_draft_schema()
    result = repo.list_luna_drafts(
        project_id=project_id,
        status=status,
        source_version_id=source_version_id,
        query=q,
        limit=limit,
        offset=offset,
    )
    return {
        "items": result["items"],
        "total": result["total"],
        "limit": limit,
        "offset": offset,
        "authority_level": "draft",
        "is_canonical": False,
    }


@router.get("/drafts/metrics", operation_id="getWorkspaceDraftMetrics")
def draft_metrics():
    repo.ensure_luna_draft_schema()
    return {"metrics": repo.luna_draft_metrics(), "authority_level": "draft", "is_canonical": False}


@router.get("/drafts/{draft_id}", operation_id="getWorkspaceDraftItem")
def draft_item(draft_id: str):
    repo.ensure_luna_draft_schema()
    data = repo.get_luna_draft(draft_id=draft_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"draft": data, "authority_level": "draft", "is_canonical": False}


@router.get("/knowledge", operation_id="getWorkspaceKnowledge", response_model=KnowledgeListResponse)
def knowledge(
    status: str | None = Query(default=None),
    knowledge_type: str | None = Query(default=None),
    domain_id: str | None = Query(default=None),
    limit: int = Query(200, ge=1, le=1000),
):
    return {
        "items": repo.list_knowledge(
            status=status,
            knowledge_type=knowledge_type,
            domain_id=domain_id,
            limit=limit,
        )
    }


@router.get("/knowledge/{knowledge_id}", operation_id="getWorkspaceKnowledgeItem", response_model=KnowledgeDetailResponse)
def knowledge_item(knowledge_id: str):
    data = repo.get_knowledge(knowledge_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    return data


@router.post(
    "/knowledge/upsert",
    operation_id="upsertWorkspaceKnowledge",
    response_model=KnowledgeDetailResponse,
)
def upsert_knowledge(body: KnowledgeUpsertRequest):
    try:
        return repo.upsert_knowledge(dump_knowledge_upsert(body))
    except repo.KnowledgeContentError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/knowledge/{knowledge_id}/confirm",
    operation_id="confirmWorkspaceKnowledge",
    response_model=KnowledgeConfirmResponse,
)
def confirm_knowledge(knowledge_id: str, body: KnowledgeConfirmRequest):
    if body.human_confirmed is not True:
        raise HTTPException(
            status_code=422,
            detail={"code": "KNOWLEDGE_CONFIRMATION_REQUIRED", "message": "human_confirmed=true is required to publish Knowledge"},
        )
    try:
        data = repo.confirm_knowledge(knowledge_id, body.confirmed_by)
    except repo.KnowledgeContentError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc
    if data is None:
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    return data


@router.get("/sources", operation_id="getWorkspaceSourceRegistry")
def sources(limit: int = Query(200, ge=1, le=500)):
    """Read-only Source Registry view over the legacy wiki_page table."""
    return {"items": repo.list_wiki(limit)}


@router.get("/sources/{page_id}", operation_id="getWorkspaceSourceRegistryItem")
def source_item(page_id: str):
    data = repo.get_wiki(page_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Source item not found")
    return data


@router.post("/sources/register", operation_id="registerWorkspaceSource")
def register_source(body: SourceRegisterRequest):
    """Register or update an office_sync source in the wiki_page registry.

    Metadata-only: never reads file contents or creates Knowledge.
    """
    try:
        return repo.register_source(
            title=body.title,
            canonical_location=body.canonical_location,
            source_origin=body.source_origin,
            content_hash=body.content_hash,
            file_ext=body.file_ext,
            source_kind=body.source_kind,
            canonical_state=body.canonical_state,
            size=body.size,
            mtime=body.mtime,
        )
    except repo.SourceRegistrationError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc


# >>> PAW V1.3C PROJECTION ROUTER BEGIN (read-only, additive)
from . import paw_projection_v13c as _paw_projection_v13c  # noqa: E402


@router.get("/projection/project/{project_id}", operation_id="getProjectOperatingProjection")
def project_operating_projection(project_id: str):
    """v1.3C read-only Project Operating Page projection.
    Composes existing project context + v1.3B retrieval services.
    Deterministic, authority-aware, no model calls."""
    data = _paw_projection_v13c.build_project_operating_projection(project_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return data


@router.get("/projection/knowledge/{knowledge_id}", operation_id="getKnowledgeDetailProjection")
def knowledge_detail_projection(knowledge_id: str):
    """v1.3C read-only Knowledge Detail projection: authority, valid time,
    revisions, evidence disclosure, related projects."""
    data = _paw_projection_v13c.build_knowledge_detail_projection(knowledge_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Knowledge not found")
    return data


@router.get("/projection/knowledge-search", operation_id="searchKnowledgeProjection")
def knowledge_search_projection(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=50),
    include_provisional: bool = Query(False),
):
    """User-facing knowledge search backed by v1.3B full-corpus
    current-safe retrieval (FTS + authority revalidation + lineage gate)."""
    return _paw_projection_v13c.knowledge_search(
        q, limit=limit, include_provisional=include_provisional)


@router.get("/projection/evidence", operation_id="lookupEvidenceProjection")
def evidence_projection(
    source_version_id: str = Query(..., min_length=1),
    locator: str = Query(..., min_length=1),
):
    """Controlled evidence drill-down via the v1.3B structural locator.
    Empty locator fails closed; internal paths are never exposed."""
    return _paw_projection_v13c.evidence_lookup(source_version_id, locator)
# <<< PAW V1.3C PROJECTION ROUTER END
