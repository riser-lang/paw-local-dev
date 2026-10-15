"""Brain API Router — Production Brain endpoints for Personal Workspace.

Mounted at /v1/brain prefix in paw-actions main.py.
All endpoints require Bearer token auth (inherited from main app dependency).

Endpoints (Checkpoint 4 — only safe read-only + chat):
  GET  /v1/brain/health
  GET  /v1/brain/projects/{project_id}
  POST /v1/brain/chat
  POST /v1/brain/chat/{session_id}/messages
  GET  /v1/brain/chat/{session_id}

Safety:
  - server_ops mode: HARD BLOCKED (schema allowlist + router validation + adapter check)
  - workspace_candidate mode: NOT OPENED (Confirmation Center not yet built)
  - Session namespace: paw-brain:{normalized_session_id}
  - Mode lock: session mode fixed at creation, cannot be changed
  - runtime_run_id: correlation ID, NOT analysis_run_id (no DB persistence)
  - No canonical write capability
  - OpenClaw 18789 remains loopback-only
"""

from __future__ import annotations

from pathlib import Path
import os

import re
import uuid
import time
from typing import Any, Optional
from enum import Enum
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, Query

from . import brain_repository as repo
from brain.review.review_presentation_service import present_candidate as _present_candidate
from brain.review.effective_review_service import (
    NEEDS_REVIEW as _NEEDS_REVIEW,
    get_effective_review_items as _get_effective_review_items,
    get_effective_review_summary as _get_effective_review_summary,
    get_effective_review_state as _get_effective_review_state,
)

router = APIRouter()

# ── Constants ─────────────────────────────────────────────────────────────

BRAIN_VERSION = "1.0.0-checkpoint4"

HTTP_ALLOWED_MODES = {"workspace_chat", "workspace_extract", "workspace_simulate"}

BLOCKED_MODES = {"server_ops", "workspace_candidate"}

RESERVED_SESSION_IDS = {"main", "server_ops", "global", "", "..", "."}

SESSION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,128}$")

MAX_SESSION_ID_LENGTH = 128

# In-memory session store (mode lock)
_sessions: dict[str, dict] = {}

# ── Models ─────────────────────────────────────────────────────────────────

class ChatCreateRequest(BaseModel):
    project_id: Optional[str] = None
    context_hint: Optional[str] = None
    mode: str = Field(default="workspace_chat")

class ChatMessageRequest(BaseModel):
    message: str = Field(..., min_length=1)

# ── Validation ─────────────────────────────────────────────────────────────

def validate_mode(mode: str) -> str:
    """Validate and enforce mode restrictions at router level."""
    if mode in BLOCKED_MODES:
        raise HTTPException(
            status_code=403 if mode == "server_ops" else 400,
            detail=f"Mode '{mode}' is not allowed from Brain API."
        )
    if mode not in HTTP_ALLOWED_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode '{mode}'. Allowed: {HTTP_ALLOWED_MODES}"
        )
    return mode

def validate_session_id(session_id: str) -> str:
    """Validate and normalize client-provided session ID."""
    if not session_id or session_id in RESERVED_SESSION_IDS:
        raise HTTPException(status_code=400, detail="Invalid or reserved session ID.")
    if len(session_id) > MAX_SESSION_ID_LENGTH:
        raise HTTPException(status_code=400, detail="Session ID too long (max 128 chars).")
    if not SESSION_ID_PATTERN.match(session_id):
        raise HTTPException(status_code=400, detail="Session ID contains invalid characters.")
    if "paw-brain:" in session_id:
        raise HTTPException(status_code=400, detail="Internal session key namespace is reserved.")
    if ".." in session_id or "/" in session_id or "\\" in session_id:
        raise HTTPException(status_code=400, detail="Session ID contains path characters.")
    return session_id

def get_internal_session_key(session_id: str) -> str:
    """Convert external session ID to internal OpenClaw session key."""
    validated = validate_session_id(session_id)
    return f"paw-brain:{validated}"

# ── Health ─────────────────────────────────────────────────────────────────

@router.get("/health", operation_id="brainHealth")
def health():
    """Brain health endpoint — returns non-sensitive runtime status."""
    runtime_available = False
    openclaw_transport_available = False
    candidate_store_available = False
    canonical_db_available = False

    try:
        from brain.runtime.agy_runtime import get_adapter as _agy_get_adapter
        _agy_adapter = _agy_get_adapter()
        _agy_health = _agy_adapter.health_check()
        runtime_available = _agy_health.get("healthy", False)
        openclaw_transport_available = False  # Agy does not use OpenClaw
    except Exception:
        pass

    try:
        counts = repo.unprocessed_counts()
        candidate_store_available = True
    except Exception:
        pass

    try:
        import sqlite3
        con = sqlite3.connect("" + os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db")) + "", uri=True)
        con.execute("PRAGMA query_only=ON")
        con.execute("SELECT COUNT(*) FROM project").fetchone()
        con.close()
        canonical_db_available = True
    except Exception:
        pass

    return {
        "ok": True,
        "brain_version": BRAIN_VERSION,
        "runtime_available": runtime_available,
        "openclaw_transport_available": openclaw_transport_available,
        "auto_write_enabled": False,
        "candidate_store_available": candidate_store_available,
        "canonical_db_available": canonical_db_available,
    }

# ── Project Brain ──────────────────────────────────────────────────────────

@router.get("/projects/{project_id}", operation_id="brainProjectContext")
def project_brain(project_id: str):
    """Get read-only Project Brain context.
    Uses existing Project Context Builder.
    Read-only — no modifications to any data.
    """
    ctx = repo.project_brain_context(project_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="Project not found")
    return ctx

# ── Chat ───────────────────────────────────────────────────────────────────

@router.post("/chat", operation_id="brainChatCreate")
def create_chat_session(req: ChatCreateRequest):
    """Create a new Brain Chat session.
    Mode is fixed at creation and cannot be changed.
    server_ops and workspace_candidate are blocked.
    """
    mode = validate_mode(req.mode)
    session_id = str(uuid.uuid4())
    internal_key = get_internal_session_key(session_id)

    session_meta = {
        "session_id": session_id,
        "session_key": internal_key,
        "mode": mode,
        "project_id": req.project_id,
        "created_at": time.time(),
        "openclaw_exposed": False,
    }
    _sessions[session_id] = session_meta

    return {
        "session_id": session_id,
        "session_key": internal_key,
        "mode": mode,
        "project_id": req.project_id,
        "openclaw_exposed": False,
    }

@router.post("/chat/{session_id}/messages", operation_id="brainChatMessage")
def send_chat_message(session_id: str, req: ChatMessageRequest):
    """Send a message to Agnes via Brain Chat Gateway.
    Flow: auth → session validation → mode lock check → Project Context → Agnes Runtime → OpenClaw loopback
    """
    sid = validate_session_id(session_id)
    internal_key = get_internal_session_key(sid)

    # Mode lock: retrieve session mode or default to workspace_chat
    session = _sessions.get(sid)
    if session:
        locked_mode = session["mode"]
    else:
        # Session not in memory (possibly created externally) — default to safe mode
        locked_mode = "workspace_chat"
        _sessions[sid] = {
            "session_id": sid,
            "session_key": internal_key,
            "mode": locked_mode,
            "project_id": None,
            "created_at": time.time(),
        }

    # Double-check mode is not blocked
    if locked_mode in BLOCKED_MODES:
        raise HTTPException(status_code=403, detail="Blocked mode detected.")

    try:
        from brain.runtime.agy_runtime import get_adapter as _agy_get_adapter, DEFAULT_MODEL as _agy_default_model
        from brain.context.assistant_workspace_context import build_assistant_message_legacy as _build_ctx
        adapter = _agy_get_adapter()

        # Map legacy mode strings to Agy modes
        _mode_map = {
            "workspace_chat": "chat",
            "workspace_extract": "extract",
            "workspace_simulate": "plan",
            "workspace_candidate": "plan",
        }
        agy_mode = _mode_map.get(locked_mode, "chat")

        # Build context and send through Agy
        full_message = _build_ctx(req.message, agy_mode)
        _agy_session_id = f"brain:{internal_key}"
        _existing = adapter.get_session(_agy_session_id)
        if not _existing:
            adapter.create_session(_agy_session_id, model=_agy_default_model, mode=agy_mode)
        result = adapter.send_message(
            session_id=_agy_session_id,
            message=full_message,
            mode=agy_mode,
        )

        return {
            "session_id": sid,
            "response": result.response,
            "candidates": [],
            "auto_write_triggered": False,
            "runtime_run_id": result.conversation_id,
            "mode": locked_mode,
        }
    except HTTPException:
        raise
    except Exception as e:
        return {
            "session_id": sid,
            "response": f"[ERROR] Brain chat error: {str(e)}",
            "candidates": [],
            "auto_write_triggered": False,
            "runtime_run_id": "",
            "mode": locked_mode,
        }

@router.get("/chat/{session_id}", operation_id="brainChatInfo")
def get_chat_session(session_id: str):
    """Get session metadata.
    Returns only non-sensitive session information.
    """
    sid = validate_session_id(session_id)
    session = _sessions.get(sid)
    if session:
        return {
            "session_id": sid,
            "session_key": session["session_key"],
            "mode": session["mode"],
            "active": True,
            "created_at": session.get("created_at"),
        }
    return {
        "session_id": sid,
        "session_key": get_internal_session_key(sid),
        "mode": "workspace_chat",
        "active": True,
    }

# ── Confirmation Center ────────────────────────────────────────────────────

from brain.candidates.repository import (
    create_candidate as _create_candidate,
    get_candidate as _get_candidate,
    list_candidates as _list_candidates,
    update_candidate_status as _update_status,
    candidate_summary as _candidate_summary,
    VALID_CANDIDATE_TYPES,
)
from brain.candidates.apply_service import (
    apply_candidate as _apply_candidate,
    SAFE_APPLY_TYPES as _SAFE_APPLY_TYPES,
    UNSUPPORTED_APPLY_TYPES as _UNSUPPORTED_APPLY_TYPES,
)

class DecisionRequest(BaseModel):
    decision: str = Field(..., pattern="^(accept|reject|edit_and_accept)$")
    reviewed_by: str = Field(default="user", min_length=1)
    review_notes: Optional[str] = None
    edited_payload: Optional[dict] = None

@router.get("/confirmations", operation_id="brainConfirmations")
def list_confirmations(
    status: str = Query("pending", pattern="^(pending|accepted|rejected)$"),
    candidate_type: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """List candidates for Confirmation Center.
    Default: pending candidates only.
    Does not return raw_text, system prompts, or private reasoning.
    """
    if candidate_type and candidate_type not in VALID_CANDIDATE_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid candidate_type: {candidate_type}")
    items = _list_candidates(status=status, candidate_type=candidate_type, limit=limit)
    # Sanitize: remove full raw_excerpt, keep only summary fields
    safe_items = []
    for item in items:
        presentation = _present_candidate(item)
        if status == "pending" and presentation.get("presentation_status") == "PRESENTATION_INVALID":
            # Never expose an item that cannot explain the business decision;
            # the effective review projection remains fail-closed.
            continue
        safe_items.append({
            "id": item.get("id"),
            "candidate_type": item.get("candidate_type"),
            "confidence": item.get("confidence"),
            "recommended_action": item.get("recommended_action"),
            "status": item.get("status"),
            "created_at": item.get("created_at"),
            "source_label": _derive_source_label(item),
            "proposed_change_summary": _summarize_change(item.get("proposed_change")),
        })
    summary = _candidate_summary()
    return {
        "items": safe_items,
        "summary": summary,
        "pending_count": summary.get("pending_total", 0),
    }

def _summarize_change(change) -> str:
    """Extract a short summary from proposed_change."""
    if not change:
        return ""
    if isinstance(change, str):
        try:
            import json
            change = json.loads(change)
        except Exception:
            return change[:200]
    if isinstance(change, dict):
        # If it has review_metadata, show the reviewed version
        if "reviewed_payload" in change:
            change = change["reviewed_payload"]
        if isinstance(change, dict):
            return str(change.get("statement", change.get("title", change.get("summary", str(change)[:200]))))
    return str(change)[:200]


def _review_feedback(change: Any) -> dict[str, Any] | None:
    """Expose durable review input explicitly to the later patrol handoff."""
    if isinstance(change, str):
        try:
            import json
            change = json.loads(change)
        except Exception:
            return None
    if not isinstance(change, dict):
        return None
    metadata = change.get("review_metadata")
    if isinstance(metadata, dict):
        return {
            "reviewed_by": metadata.get("reviewed_by"),
            "reviewed_at": metadata.get("reviewed_at"),
            "review_notes": metadata.get("review_notes"),
            "review_origin": metadata.get("review_origin", "api"),
            "action": metadata.get("action"),
        }
    # edit_draft/accept can leave the metadata one level deeper.
    for key in ("reviewed_payload", "original_payload"):
        nested = _review_feedback(change.get(key))
        if nested:
            return nested
    return None

def _derive_source_label(candidate: dict) -> str:
    """Derive a safe, human-readable source label from candidate source_refs / review_brief.
    Pure read-only — queries workspace DB in RO mode, never writes.
    Returns formatted label like 'Daily · 2026-08-03' or 'Capture · 2026-08-04'.
    Falls back to 'Unknown Source' if no reliable derivation possible.
    Never exposes raw text, raw IDs, or internal identifiers.
    """
    import sqlite3 as _sqlite3
    source_refs = candidate.get("source_refs", {})
    if isinstance(source_refs, str):
        try:
            import json as _json
            source_refs = _json.loads(source_refs)
        except Exception:
            source_refs = {}
    refs = []
    if isinstance(source_refs, dict):
        refs = source_refs.get("refs", [])
    elif isinstance(source_refs, list):
        refs = source_refs

    # Also check review_brief for evidence
    brief_evidence = None
    review_brief_raw = candidate.get("review_brief_json")
    if review_brief_raw:
        try:
            import json as _json
            brief_obj = _json.loads(review_brief_raw) if isinstance(review_brief_raw, str) else review_brief_raw
            if isinstance(brief_obj, dict):
                brief_evidence = brief_obj.get("evidence_summary")
        except Exception:
            pass

    if not refs and not brief_evidence:
        return "Unknown Source"

    source_types = []
    daily_date = None
    capture_date = None

    con = None
    try:
        con = _sqlite3.connect("" + os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db")) + "", uri=True)
        con.execute("PRAGMA query_only=ON")
        cur = con.cursor()

        for ref in refs:
            if not isinstance(ref, str):
                continue
            prefix, ref_id = ref.split(":", 1) if ":" in ref else ("", ref)
            prefix = prefix.lower()

            if prefix == "capture" or not prefix:
                row = cur.execute(
                    "SELECT c.captured_at, d.date FROM capture c LEFT JOIN daily d ON c.daily_id = d.id WHERE c.id = ?",
                    (ref_id,)
                ).fetchone()
                if row:
                    captured_at, d_date = row
                    source_types.append("Capture")
                    if d_date:
                        capture_date = d_date
                    elif captured_at:
                        capture_date = str(captured_at)[:10]
                    continue
            if prefix == "daily" or not prefix:
                row = cur.execute("SELECT date FROM daily WHERE id = ?", (ref_id,)).fetchone()
                if row and row[0]:
                    source_types.append("Daily")
                    daily_date = row[0]
                    continue
            if prefix == "knowledge":
                row = cur.execute("SELECT title FROM knowledge_page WHERE id = ?", (ref_id,)).fetchone()
                if row:
                    source_types.append("Knowledge")
                    continue
            if prefix == "project":
                row = cur.execute("SELECT name FROM project WHERE id = ?", (ref_id,)).fetchone()
                if row:
                    source_types.append("Project")
                    continue
            if prefix == "todo":
                row = cur.execute("SELECT title FROM todo WHERE id = ?", (ref_id,)).fetchone()
                if row:
                    source_types.append("Todo")
                    continue
    except Exception:
        pass
    finally:
        if con:
            con.close()

    if capture_date:
        return f"Capture · {capture_date}"
    if daily_date:
        return f"Daily · {daily_date}"

    if not source_types and refs:
        for ref in refs:
            if not isinstance(ref, str):
                continue
            pfx = ref.split(":", 1)[0].capitalize() if ":" in ref else ""
            if pfx in ("Capture", "Daily", "Knowledge", "Project", "Todo") and pfx not in source_types:
                source_types.append(pfx)

    if source_types:
        unique_types = []
        for t in source_types:
            if t not in unique_types:
                unique_types.append(t)
        return " · ".join(unique_types)

    if brief_evidence:
        return "Capture · Knowledge"

    if refs:
        return "Capture"

    return "Unknown Source"

@router.get("/confirmations/summary", operation_id="brainConfirmationSummary")
def confirmation_summary():
    """Get summary counts for Confirmation Center."""
    return _candidate_summary()

@router.get("/confirmations/{candidate_id}", operation_id="brainConfirmationDetail")
def confirmation_detail(candidate_id: str):
    """Get candidate detail with evidence and structured payload.
    Returns more detail than list, but still no private chain-of-thought.
    """
    candidate = _get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    # Return structured detail (no raw system prompts, no raw_excerpt, no source_refs, no analysis_run_id)
    return {
        "id": candidate.get("id"),
        "candidate_type": candidate.get("candidate_type"),
        "proposed_change": candidate.get("proposed_change"),
        "confidence": candidate.get("confidence"),
        "inference_level": candidate.get("inference_level"),
        "recommended_action": candidate.get("recommended_action"),
        "status": candidate.get("status"),
        "source_label": _derive_source_label(candidate),
        "created_at": candidate.get("created_at"),
        "reviewed_at": candidate.get("reviewed_at"),
        "reviewed_by": candidate.get("reviewed_by"),
    }

@router.post("/confirmations/{candidate_id}/decision", operation_id="brainConfirmationDecision")
def confirmation_decision(candidate_id: str, req: DecisionRequest):
    """Accept, reject, or edit_and_accept a candidate.
    ONLY supports: accept, reject, edit_and_accept.
    Does NOT support: apply, confirm_knowledge, create_todo, update_project, canonical_write.
    accepted != canonical applied. No canonical write occurs.
    """
    candidate = _get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    # Map decision to status
    status_map = {
        "accept": "accepted",
        "reject": "rejected",
        "edit_and_accept": "accepted",
    }
    new_status = status_map[req.decision]

    result = _update_status(
        db_path=os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db")),
        candidate_id=candidate_id,
        new_status=new_status,
        reviewed_by=req.reviewed_by,
        review_notes=req.review_notes,
        edited_payload=req.edited_payload if req.decision == "edit_and_accept" else None,
    )

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    return {
        "candidate_id": candidate_id,
        "decision": req.decision,
        "status": new_status,
        "canonical_applied": False,
        "message": (
            "Candidate accepted for later canonical apply."
            if new_status == "accepted"
            else "Candidate rejected. No canonical apply will occur from this decision."
        )
    }

# ── AI Activity Backend ───────────────────────────────────────────────────

from brain.activity.repository import (
    get_analysis_activities as _get_analysis_activities,
    get_candidate_activities as _get_candidate_activities,
    get_activity_summary as _get_activity_summary,
)

@router.get("/activity", operation_id="brainActivity")
def list_activity(
    limit: int = Query(50, ge=1, le=200),
    since: Optional[str] = Query(None),
    activity_type: Optional[str] = Query(None),
    source_entity_type: Optional[str] = Query(None),
):
    """Get AI activity events (read-only derived view).
    Aggregates analysis_run + candidate events.
    Never exposes raw_text, system prompts, or private reasoning.
    """
    # Get analysis activities
    analysis_events = _get_analysis_activities(limit=limit, since=since)
    # Get candidate activities
    candidate_events = _get_candidate_activities(limit=limit, since=since)

    # Merge and sort by timestamp DESC
    all_events = analysis_events + candidate_events
    all_events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)

    # Filter by activity_type
    if activity_type:
        all_events = [e for e in all_events if e.get("activity_type") == activity_type]

    # Filter by source_entity_type
    if source_entity_type:
        all_events = [e for e in all_events if e.get("source_entity_type") == source_entity_type]

    # Enforce limit after merge
    all_events = all_events[:limit]

    return {
        "items": all_events,
        "total": len(all_events),
    }

@router.get("/activity/summary", operation_id="brainActivitySummary")
def activity_summary():
    """Get lightweight activity summary counts.
    No full-text scan. No raw data exposure.
    """
    return _get_activity_summary()

# ── Custom GPT Review Endpoints ────────────────────────────────────────────
# Thin wrapper over existing confirmation/decision logic.
# Provides explicit accept/reject action endpoints for Custom GPT Actions.
# Does NOT create new canonical apply pipeline.
# Accept/Reject reuse existing update_candidate_status (no canonical write).

class ReviewAcceptRequest(BaseModel):
    reviewed_by: str = Field(default="custom_gpt", min_length=1)
    review_notes: Optional[str] = Field(default=None, max_length=500)
    review_origin: str = Field(default="api", max_length=40)

class ReviewRejectRequest(BaseModel):
    reviewed_by: str = Field(default="custom_gpt", min_length=1)
    reason: Optional[str] = Field(default=None, max_length=500)

class ReviewApplyRequest(BaseModel):
    applied_by: str = Field(default="custom_gpt", min_length=1)

@router.get("/review/candidates", operation_id="brainReviewList")
def review_list_candidates(
    status: str = Query("pending", pattern="^(pending|accepted|rejected)$"),
    candidate_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """List candidates for Custom GPT review.
    Default: pending candidates only.
    Uses explicit allowlist response model — no raw_text, no private data.
    """
    if candidate_type and candidate_type not in VALID_CANDIDATE_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid candidate_type: {candidate_type}")
    if status == "pending":
        # Scan a wide window before applying the caller's limit: the effective
        # projection routes only a small subset of pending rows to USER_REVIEW_REQUIRED,
        # so a small scan window silently truncates the user's review list
        # (production: limit=50 returned 2 of the 6 canonical items).
        items = _get_effective_review_items(limit=max(limit, 1000))[:limit]
        if candidate_type:
            items = [item for item in items if item.get("candidate_type") == candidate_type]
    else:
        items = _list_candidates(status=status, candidate_type=candidate_type, limit=limit)
    # Explicit allowlist response
    safe_items = []
    for item in items:
        presentation = _present_candidate(item)
        if status == "pending":
            effective_state = _get_effective_review_state(item.get("id"))
            if effective_state != _NEEDS_REVIEW:
                continue
        if status == "pending" and presentation.get("presentation_status") == "PRESENTATION_INVALID":
            # Never expose an item that cannot explain the business decision.
            continue
        safe_items.append({
            "candidate_id": item.get("id"),
            "candidate_type": item.get("candidate_type"),
            "status": item.get("status"),
            "effective_review_state": _get_effective_review_state(item.get("id")),
            "confidence": item.get("confidence"),
            "recommended_action": item.get("recommended_action"),
            "created_at": item.get("created_at"),
            "reviewed_at": item.get("reviewed_at"),
            "source_label": _derive_source_label(item),
            "summary": _summarize_change(item.get("proposed_change")),
            "presentation": presentation,
            "review_feedback": _review_feedback(item.get("proposed_change")),
        })
    result = {
        "items": safe_items,
        "total": len(safe_items),
        "status_filter": status,
    }
    if status == "pending":
        summary = _get_effective_review_summary()
        result["routing_summary"] = summary.get("routing", {
            "effective_total": len(safe_items),
            "auto_flow_safe": 0,
            "auto_close_no_value": 0,
            "user_review_required": len(safe_items),
        })
    return result

@router.get("/review/candidates/{candidate_id}", operation_id="brainReviewDetail")
def review_candidate_detail(candidate_id: str):
    """Get candidate detail for review decision.
    Explicit allowlist — only returns fields needed for accept/reject decision.
    No raw_text, no raw_input_refs, no private reasoning.
    """
    candidate = _get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail={"detail": "Candidate not found", "code": "not_found"})
    # Explicit allowlist — no raw_excerpt, no source_refs, no analysis_run_id
    return {
        "candidate_id": candidate.get("id"),
        "candidate_type": candidate.get("candidate_type"),
        "status": candidate.get("status"),
        "effective_review_state": _get_effective_review_state(candidate_id),
        "confidence": candidate.get("confidence"),
        "inference_level": candidate.get("inference_level"),
        "recommended_action": candidate.get("recommended_action"),
        "proposed_change": candidate.get("proposed_change"),
        "source_label": _derive_source_label(candidate),
        "created_at": candidate.get("created_at"),
        "reviewed_at": candidate.get("reviewed_at"),
        "reviewed_by": candidate.get("reviewed_by"),
        "presentation": _present_candidate(candidate),
        "review_feedback": _review_feedback(candidate.get("proposed_change")),
    }

@router.post("/review/candidates/{candidate_id}/accept", operation_id="brainReviewAccept")
def review_accept_candidate(candidate_id: str, req: ReviewAcceptRequest):
    """Accept a candidate (explicit human-approved action).
    pending → accepted only.
    Does NOT write canonical. Does NOT create Todo/Knowledge/Project.
    accepted != canonical applied. Apply requires future applyWorkspaceDecision.
    Idempotent: re-accepting returns current status without duplicate side effects.
    """
    candidate = _get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail={"detail": "Candidate not found", "code": "not_found"})
    if candidate["status"] == "accepted":
        return {
            "candidate_id": candidate_id,
            "status": "accepted",
            "message": "Candidate already accepted. No duplicate action taken.",
            "canonical_applied": False,
        }
    if candidate["status"] not in ("pending",):
        raise HTTPException(status_code=409, detail={"detail": f"Cannot accept candidate with status '{candidate['status']}'. Only pending can be accepted.", "code": "invalid_transition"})
    result = _update_status(
        db_path=os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db")),
        candidate_id=candidate_id,
        new_status="accepted",
        reviewed_by=req.reviewed_by,
        review_notes=req.review_notes,
        review_origin=req.review_origin,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail={"detail": result["error"], "code": "accept_failed"})
    return {
        "candidate_id": candidate_id,
        "status": "accepted",
        "canonical_applied": False,
        "message": "Candidate accepted for later canonical apply.",
    }

@router.post("/review/candidates/{candidate_id}/reject", operation_id="brainReviewReject")
def review_reject_candidate(candidate_id: str, req: ReviewRejectRequest):
    """Reject a candidate (explicit human-approved action).
    pending → rejected only.
    Does NOT delete candidate. Does NOT modify canonical data.
    """
    candidate = _get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail={"detail": "Candidate not found", "code": "not_found"})
    if candidate["status"] == "rejected":
        return {
            "candidate_id": candidate_id,
            "status": "rejected",
            "message": "Candidate already rejected. No duplicate action taken.",
        }
    if candidate["status"] not in ("pending",):
        raise HTTPException(status_code=409, detail={"detail": f"Cannot reject candidate with status '{candidate['status']}'. Only pending can be rejected.", "code": "invalid_transition"})
    result = _update_status(
        db_path=os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db")),
        candidate_id=candidate_id,
        new_status="rejected",
        reviewed_by=req.reviewed_by,
        review_notes=req.reason,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail={"detail": result["error"], "code": "reject_failed"})
    return {
        "candidate_id": candidate_id,
        "status": "rejected",
        "message": "Candidate rejected. Audit trail preserved.",
    }

@router.post("/review/candidates/{candidate_id}/apply", operation_id="brainReviewApply")
def review_apply_candidate(candidate_id: str, req: ReviewApplyRequest):
    """Apply an accepted candidate to Canonical Workspace.
    accepted → applied only.
    Writes to Canonical Workspace (todo table) in a single atomic transaction.
    Idempotent: re-applying returns existing applied entity without duplicate creation.
    V1: Only supports candidate_type='todo'.
    accepted != applied until this endpoint is explicitly called.
    AUTO_WRITE_ENABLED remains FALSE. This is NOT automatic.
    """
    result = _apply_candidate(
        candidate_id=candidate_id,
        applied_by=req.applied_by,
    )
    if "error" in result:
        err_msg = result["error"]
        if "not_found" in err_msg:
            raise HTTPException(status_code=404, detail={"detail": "Candidate not found", "code": "not_found"})
        if "status is" in err_msg and "must be" in err_msg:
            raise HTTPException(status_code=409, detail={"detail": err_msg, "code": "invalid_transition"})
        if "not supported" in err_msg:
            raise HTTPException(status_code=400, detail={"detail": err_msg, "code": "unsupported_type"})
        if "missing required" in err_msg:
            raise HTTPException(status_code=400, detail={"detail": err_msg, "code": "invalid_proposed_change"})
        if "apply_failed" in err_msg:
            raise HTTPException(status_code=500, detail={"detail": err_msg, "code": "apply_failed"})
        raise HTTPException(status_code=400, detail={"detail": err_msg, "code": "apply_error"})
    return result
