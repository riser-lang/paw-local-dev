"""Staging Brain Router V1 — FastAPI router for Brain API endpoints.

⚠️  STAGING ONLY — DO NOT IMPORT TO PRODUCTION main.py

This module defines the future Brain API contract:
  GET  /v1/brain/projects/{project_id}          — Project Brain context (read-only)
  GET  /v1/brain/candidates                      — List pending candidates
  GET  /v1/brain/candidates/{candidate_id}       — Candidate detail
  POST /v1/brain/candidates/{candidate_id}/decision — Candidate decision (staging mock)
  POST /v1/brain/chat                            — Create chat session
  POST /v1/brain/chat/{session_id}/messages       — Send message to Agnes
  GET  /v1/brain/chat/{session_id}                — Get session info
  DELETE /v1/brain/chat/{session_id}              — End session

Safety:
  - No canonical write capability
  - Candidate decision returns staging mock result (DO_NOT_APPLY_CANONICAL)
  - Chat sessions use namespace: paw-brain:*
  - Auth dependency placeholder
  - OpenClaw 18789 remains loopback-only
"""
from __future__ import annotations
from typing import Any, Optional
from enum import Enum
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, status

router = APIRouter(prefix="/v1/brain", tags=["brain"])


# ── Auth Dependency Placeholder ────────────────────────────────────────────
async def verify_brain_auth(authorization: str = ""):
    """Placeholder auth dependency.
    Future: verify Bearer token against PAW_OPS_API_KEY.
    Currently: always pass (staging only).
    """
    # In production: verify bearer token
    # if not authorization.startswith("Bearer "):
    #     raise HTTPException(status_code=401, detail="Invalid auth")
    return {"authenticated": True, "staging": True}


# ── Agnes Mode Separation ──────────────────────────────────────────────────
class AgnesMode(str, Enum):
    workspace_chat = "workspace_chat"
    workspace_extract = "workspace_extract"
    workspace_simulate = "workspace_simulate"
    server_ops = "server_ops"


# ── Tool Permission Matrix ─────────────────────────────────────────────────
TOOL_PERMISSION_MATRIX = {
    AgnesMode.workspace_chat: {"shell_write": False, "canonical_write": False, "read_only": True},
    AgnesMode.workspace_extract: {"shell_write": False, "canonical_write": False, "read_only": True},
    AgnesMode.workspace_simulate: {"shell_write": False, "canonical_write": False, "read_only": True},
    AgnesMode.server_ops: {"shell_write": True, "canonical_write": False, "read_only": False},
}


# ── Session Namespace ──────────────────────────────────────────────────────
SESSION_NAMESPACE = "paw-brain"


def generate_session_key(session_id: str) -> str:
    """Generate namespaced session key to avoid polluting main Server Ops session."""
    return f"{SESSION_NAMESPACE}:{session_id}"


# ── Request/Response Models ────────────────────────────────────────────────

class ProjectBrainResponse(BaseModel):
    """Response for GET /v1/brain/projects/{project_id}."""
    project: Optional[dict[str, Any]] = None
    open_todos: list[dict[str, Any]] = Field(default_factory=list)
    recent_done: list[dict[str, Any]] = Field(default_factory=list)
    draft_knowledge: list[dict[str, Any]] = Field(default_factory=list)
    confirmed_knowledge: list[dict[str, Any]] = Field(default_factory=list)
    knowledge_source_summary: dict[str, Any] = Field(default_factory=dict)
    recent_daily: list[dict[str, Any]] = Field(default_factory=list)
    recent_capture: list[dict[str, Any]] = Field(default_factory=list)
    candidate_summary: dict[str, Any] = Field(default_factory=dict)
    known_conflicts: list[dict[str, Any]] = Field(default_factory=list)
    open_questions: list[dict[str, Any]] = Field(default_factory=list)
    latest_activity: Optional[dict[str, Any]] = None


class CandidateListResponse(BaseModel):
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    total: int = 0
    pending: int = 0


class CandidateDetailResponse(BaseModel):
    candidate: Optional[dict[str, Any]] = None


class CandidateDecisionRequest(BaseModel):
    decision: str = Field(..., pattern="^(accept|reject)$")
    reviewed_by: str = Field(..., min_length=1)
    notes: Optional[str] = None


class CandidateDecisionResponse(BaseModel):
    """Staging response — DO NOT apply to canonical."""
    candidate_id: str
    decision: str
    status: str = "accepted_for_future_apply"
    canonical_applied: bool = False
    message: str = "Staging mode: decision recorded but NOT applied to canonical workspace"


class ChatCreateRequest(BaseModel):
    project_id: Optional[str] = None
    mode: AgnesMode = AgnesMode.workspace_chat
    context_hint: Optional[str] = None


class ChatCreateResponse(BaseModel):
    session_id: str
    session_key: str
    mode: AgnesMode
    tool_permissions: dict[str, bool]
    openclaw_exposed: bool = False  # OpenClaw 18789 never directly exposed


class ChatMessageRequest(BaseModel):
    message: str = Field(..., min_length=1)
    pass_strategy: str = Field(default="auto", pattern="^(single|two_pass|auto)$")


class ChatMessageResponse(BaseModel):
    session_id: str
    response: str
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    pass_strategy_used: str = "single"
    auto_write_triggered: bool = False


class ChatSessionInfoResponse(BaseModel):
    session_id: str
    session_key: str
    mode: AgnesMode
    active: bool
    created_at: Optional[str] = None
    message_count: int = 0


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get(
    "/projects/{project_id}",
    response_model=ProjectBrainResponse,
    summary="Get Project Brain context (read-only)",
)
async def get_project_brain(
    project_id: str,
    auth: dict = Depends(verify_brain_auth),
):
    """Returns full project brain context for review.
    Read-only — no canonical write capability.
    """
    # Future: call brain.context.builder.build_project_context(project_id)
    # Staging: return empty structure
    return ProjectBrainResponse(
        project={"id": project_id, "staging": True},
    )


@router.get(
    "/candidates",
    response_model=CandidateListResponse,
    summary="List pending candidates",
)
async def list_candidates(
    status_filter: str = "pending",
    limit: int = 50,
    auth: dict = Depends(verify_brain_auth),
):
    """List candidates by status. Default: pending."""
    # Future: query candidate store
    return CandidateListResponse(candidates=[], total=0, pending=0)


@router.get(
    "/candidates/{candidate_id}",
    response_model=CandidateDetailResponse,
    summary="Get candidate detail",
)
async def get_candidate(
    candidate_id: str,
    auth: dict = Depends(verify_brain_auth),
):
    """Get full candidate detail including source_refs and raw_excerpt."""
    return CandidateDetailResponse(candidate=None)


@router.post(
    "/candidates/{candidate_id}/decision",
    response_model=CandidateDecisionResponse,
    summary="Submit candidate decision (staging — no canonical write)",
)
async def candidate_decision(
    candidate_id: str,
    req: CandidateDecisionRequest,
    auth: dict = Depends(verify_brain_auth),
):
    """Submit accept/reject decision for a candidate.
    ⚠️ Staging mode: decision is recorded but NOT applied to canonical workspace.
    """
    return CandidateDecisionResponse(
        candidate_id=candidate_id,
        decision=req.decision,
        status="accepted_for_future_apply",
        canonical_applied=False,
    )


@router.post(
    "/chat",
    response_model=ChatCreateResponse,
    summary="Create Brain chat session",
)
async def create_chat_session(
    req: ChatCreateRequest,
    auth: dict = Depends(verify_brain_auth),
):
    """Create a new Brain chat session.
    Session namespace: paw-brain:* (separated from Server Ops session).
    OpenClaw 18789 is never directly exposed to external clients.
    """
    import uuid
    session_id = str(uuid.uuid4())
    session_key = generate_session_key(session_id)
    perms = TOOL_PERMISSION_MATRIX.get(req.mode, {})
    return ChatCreateResponse(
        session_id=session_id,
        session_key=session_key,
        mode=req.mode,
        tool_permissions=perms,
        openclaw_exposed=False,
    )


@router.post(
    "/chat/{session_id}/messages",
    response_model=ChatMessageResponse,
    summary="Send message to Agnes via Brain Gateway",
)
async def send_chat_message(
    session_id: str,
    req: ChatMessageRequest,
    auth: dict = Depends(verify_brain_auth),
):
    """Send a message to Agnes via Brain Chat Gateway.
    Flow: auth → validation → Workspace Context → Agnes Runtime.
    OpenClaw 18789 remains loopback-only.
    """
    return ChatMessageResponse(
        session_id=session_id,
        response="[STAGING] Agnes runtime not yet bound. DRY_RUN mode.",
        candidates=[],
        pass_strategy_used="single",
        auto_write_triggered=False,
    )


@router.get(
    "/chat/{session_id}",
    response_model=ChatSessionInfoResponse,
    summary="Get chat session info",
)
async def get_chat_session(
    session_id: str,
    auth: dict = Depends(verify_brain_auth),
):
    """Get session information."""
    return ChatSessionInfoResponse(
        session_id=session_id,
        session_key=generate_session_key(session_id),
        mode=AgnesMode.workspace_chat,
        active=False,
    )


@router.delete(
    "/chat/{session_id}",
    summary="End chat session",
)
async def end_chat_session(
    session_id: str,
    auth: dict = Depends(verify_brain_auth),
):
    """End a Brain chat session."""
    return {"session_id": session_id, "ended": True}


# ── Safety Constants ───────────────────────────────────────────────────────

AUTO_WRITE_ENABLED = False
OPENCLAW_LOOPBACK_ONLY = True
BRAIN_CHAT_DEFAULT_READ_ONLY = True
SESSION_NAMESPACE_SEPARATED = True


def get_safety_status() -> dict[str, bool]:
    """Return safety invariant status for verification."""
    return {
        "auto_write_enabled": AUTO_WRITE_ENABLED,
        "openclaw_loopback_only": OPENCLAW_LOOPBACK_ONLY,
        "brain_chat_default_read_only": BRAIN_CHAT_DEFAULT_READ_ONLY,
        "session_namespace_separated": SESSION_NAMESPACE_SEPARATED,
    }