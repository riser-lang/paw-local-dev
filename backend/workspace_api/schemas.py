from __future__ import annotations

import json
from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


KnowledgeType = Literal["project", "domain", "rule", "method", "analysis"]
KnowledgeStatus = Literal["draft", "current", "needs_review", "deprecated"]
KnowledgeSourceRole = Literal["primary", "supporting", "data", "historical", "conflicting"]
KnowledgeRelationType = Literal["primary", "related"]


class KnowledgeSourceLinkInput(StrictModel):
    source_page_id: str = Field(min_length=1, max_length=200)
    source_role: KnowledgeSourceRole = "supporting"
    note: Optional[str] = Field(default=None, max_length=2000)


class KnowledgeProjectLinkInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=200)
    relation_type: KnowledgeRelationType = "primary"


class KnowledgeUpsertRequest(StrictModel):
    domain_id: Optional[str] = Field(default=None, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    slug: str = Field(min_length=1, max_length=160, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    knowledge_type: KnowledgeType = "project"
    one_line_summary: str = Field(default="", max_length=2000)
    applicability: Optional[str] = Field(default=None, max_length=5000)
    status: KnowledgeStatus = "draft"
    # None means the caller omitted the field; an explicit {} is handled by
    # the repository as an invalid empty Knowledge document.
    content_json: Optional[dict[str, Any]] = None
    change_summary: Optional[str] = Field(default=None, max_length=2000)
    project_links: Optional[list[KnowledgeProjectLinkInput]] = Field(default=None, max_length=50)
    source_links: Optional[list[KnowledgeSourceLinkInput]] = Field(default=None, max_length=200)
    # A draft-to-current transition is a human-published action, not an AI default.
    human_confirmed: bool = False

    @model_validator(mode="after")
    def validate_content_size(self):
        if self.content_json is None:
            return self
        encoded = json.dumps(self.content_json, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > 200_000:
            raise ValueError("content_json exceeds the 200000 byte limit")
        return self


class KnowledgePageResponse(BaseModel):
    id: str
    domain_id: Optional[str] = None
    title: str
    slug: str
    knowledge_type: KnowledgeType
    one_line_summary: str
    applicability: Optional[str] = None
    status: KnowledgeStatus
    last_compiled_at: Optional[str] = None
    last_reviewed_at: Optional[str] = None
    created_at: str
    updated_at: str
    source_count: int = 0
    project_count: int = 0
    latest_revision_no: int = 0
    confirmed_revision_no: Optional[int] = None
    confirmed_at: Optional[str] = None
    confirmed_by: Optional[str] = None
    # Canonical authority projection produced by workspace_api.repository
    # (CURRENT_CONFIRMED / BLOCKED_EVIDENCE / PROVISIONAL / ...). FastAPI's
    # response_model filters undeclared fields, so this MUST be declared here
    # or the web frontend never receives authority_state.
    authority_state: Optional[str] = None
    # TD-003: bounded deterministic projection of the latest revision body
    # (provenance roots removed, values bounded). The frontend runs its
    # audited classifier over it so the card summary equals the detail
    # summary. Declared here or response_model would drop it.
    content_business_fields: Optional[dict[str, Any]] = None


class KnowledgeListResponse(BaseModel):
    items: list[KnowledgePageResponse]


class KnowledgeRevisionResponse(BaseModel):
    id: str
    knowledge_id: str
    revision_no: int
    content_json: dict[str, Any]
    change_summary: Optional[str] = None
    compiled_at: str
    created_at: str


class KnowledgeSourceResponse(BaseModel):
    id: str
    title: str
    category: Optional[str] = None
    wps_document_id: Optional[str] = None
    source_role: KnowledgeSourceRole
    note: Optional[str] = None


class KnowledgeProjectResponse(BaseModel):
    id: str
    name: str
    relation_type: KnowledgeRelationType


class KnowledgeDetailResponse(BaseModel):
    page: KnowledgePageResponse
    latest_revision: Optional[KnowledgeRevisionResponse] = None
    sources: list[KnowledgeSourceResponse] = Field(default_factory=list)
    projects: list[KnowledgeProjectResponse] = Field(default_factory=list)


class KnowledgeConfirmRequest(StrictModel):
    human_confirmed: bool = False
    confirmed_by: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def validate_confirmation(self):
        if not self.confirmed_by.strip():
            raise ValueError("confirmed_by must be a non-empty identity string")
        return self


class KnowledgeConfirmResponse(BaseModel):
    already_confirmed: bool
    knowledge: KnowledgeDetailResponse

class CaptureCreate(BaseModel):
    raw_text: str = Field(min_length=1, max_length=20000)
    captured_at: Optional[str] = None
    source: str = "chatgpt_action"


class DailyCaptureResponse(BaseModel):
    id: str
    daily_id: str
    raw_text: str
    captured_at: str
    source: str
    processed_at: Optional[str] = None
    patrol_state: Optional[str] = None
    lifecycle_display_label: Optional[str] = None
    patrol_eligible: Optional[bool] = None
    sync_status: str
    created_at: str
    updated_at: str


class DailyHistoryResponse(BaseModel):
    date: str
    daily: Optional[dict[str, Any]] = None
    captures: list[DailyCaptureResponse] = Field(default_factory=list)
    read_only: bool = True


class DailyCalendarResponse(BaseModel):
    month: str
    days: dict[str, int] = Field(default_factory=dict)


class CaptureSearchResult(BaseModel):
    capture_id: str
    daily_id: str
    date: str
    captured_at: str
    source: str
    snippet: str


class CaptureSearchResponse(BaseModel):
    query: str
    items: list[CaptureSearchResult] = Field(default_factory=list)
    next_cursor: Optional[str] = None
    total: int = 0

class TodoCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: Optional[str] = None
    project_id: Optional[str] = None
    priority: Optional[str] = None
    due_at: Optional[str] = None
    source_type: str = "manual"

class TodoUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=500)
    description: Optional[str] = None
    status: Optional[Literal["open", "in_progress", "done", "cancelled"]] = None
    priority: Optional[str] = None
    due_at: Optional[str] = None

class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    description: Optional[str] = None
    status: str = "planning"
    priority: Optional[str] = None
    due_date: Optional[str] = None


ReadOnlyWpsCapability = Literal["wps.search", "wps.list", "wps.read"]
WpsAvailability = Literal["available", "unavailable", "unknown", "quota_limited"]
WpsScope = Literal["authorized", "personal", "team"]


class WorkspaceBridgeStatus(BaseModel):
    bridge_online: bool
    wps_available: WpsAvailability
    capabilities: list[ReadOnlyWpsCapability]
    last_seen_at: Optional[str] = None
    adapter_status: dict[str, Any] = Field(default_factory=dict)


class WpsDocumentPreviewRequest(StrictModel):
    document_id: str = Field(min_length=1, max_length=512)
    max_chars: int = Field(default=20_000, ge=1, le=100_000)
    cursor: Optional[str] = Field(default=None, max_length=2048)


class WpsSheetRange(BaseModel):
    row_from: int
    row_to: int
    col_from: int
    col_to: int


class WpsSheetIndexItem(BaseModel):
    sheet_id: str
    sheet_name: str
    sheet_type: Literal["worksheet"] = "worksheet"
    visible: bool = True
    range: WpsSheetRange


class WpsWorksheetPreview(BaseModel):
    sheet_id: str
    sheet_name: str
    sheet_type: Literal["worksheet"] = "worksheet"
    range: WpsSheetRange
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    next_cursor: Optional[str] = None


class WpsDataTableField(BaseModel):
    name: str
    type: str


class WpsDataTablePreview(BaseModel):
    table_id: str
    table_name: str
    sheet_type: Literal["datatable"] = "datatable"
    fields: list[WpsDataTableField]
    views: list[Any] = Field(default_factory=list)
    records_count: Optional[int] = None
    records: list[dict[str, Any]]
    truncated: bool
    next_cursor: Optional[str] = None


class WpsDocumentPreviewResponse(BaseModel):
    document_id: str
    title: Optional[str] = None
    document_type: Literal["document", "ksheet"] = "document"
    content: str = ""
    sheet_index: list[WpsSheetIndexItem] = Field(default_factory=list)
    sheets: list[WpsWorksheetPreview] = Field(default_factory=list)
    tables: list[WpsDataTablePreview] = Field(default_factory=list)
    returned_chars: int
    truncated: bool
    next_cursor: Optional[str] = None


class WpsListPreviewRequest(StrictModel):
    scope: WpsScope = "personal"
    drive_id: Optional[str] = Field(default=None, max_length=512)
    parent_id: Optional[str] = Field(default=None, max_length=512)
    limit: int = Field(default=20, ge=1, le=50)
    cursor: Optional[str] = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def require_known_drive(self):
        if self.parent_id and not self.drive_id:
            raise ValueError("drive_id is required when parent_id is provided")
        if self.scope != "personal" and not self.drive_id:
            raise ValueError(f"drive_id is required for scope={self.scope}")
        return self


class WpsSearchPreviewRequest(StrictModel):
    query: str = Field(min_length=1, max_length=500)
    scope: WpsScope = "authorized"
    limit: int = Field(default=20, ge=1, le=50)
    cursor: Optional[str] = Field(default=None, max_length=2048)
    drive_ids: Optional[list[str]] = Field(default=None, min_length=1, max_length=20)

    @model_validator(mode="after")
    def require_scoped_drives(self):
        if self.scope != "authorized" and not self.drive_ids:
            raise ValueError(f"drive_ids are required for scope={self.scope}")
        return self


class WpsInventoryItem(BaseModel):
    document_id: Optional[str] = None
    drive_id: Optional[str] = None
    title: Optional[str] = None
    type: Optional[str] = None
    parent_id: Optional[str] = None
    modified_at: Optional[str | int | float] = None
    size: Optional[int] = None
    shared: Optional[bool] = None


class WpsInventoryPreviewResponse(BaseModel):
    items: list[WpsInventoryItem]
    next_cursor: Optional[str] = None
    total: Optional[int] = None


# --- Source Registry Registration ---

IMAGE_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp",
    ".svg", ".heic", ".heif", ".tif", ".tiff",
})


class SourceRegisterRequest(StrictModel):
    """Metadata-only request to register an office_sync source in the wiki_page registry."""
    title: str = Field(min_length=1, max_length=500)
    canonical_location: str = Field(min_length=1, max_length=2000)
    source_origin: Literal["office_sync"]
    content_hash: str = Field(min_length=8, max_length=128)
    size: Optional[int] = Field(default=None, ge=0)
    mtime: Optional[str] = Field(default=None, max_length=64)
    file_ext: str = Field(min_length=1, max_length=20)
    source_kind: str = Field(min_length=1, max_length=50)
    canonical_state: Literal["canonical"]
