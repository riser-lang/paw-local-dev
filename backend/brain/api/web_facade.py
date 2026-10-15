"""Web Brain Session Facade — paw_session-authenticated read/review bridge.

Mounted at /v1/web/brain behind `require_workspace_request` (session + CSRF),
the exact same auth boundary as /v1/web/workspace. The browser only ever holds
the paw_session HttpOnly cookie; the PAW_OPS bearer never leaves the server and
is never embedded in any JS bundle.

All review/activity semantics are delegated to the sealed brain_router handlers
(shared service call — no HTTP loopback, no duplicated business logic):

  GET    /v1/web/brain/review/candidates                → brain review list
  GET    /v1/web/brain/review/candidates/{id}           → brain review detail
  POST   /v1/web/brain/review/candidates/{id}/accept    → pending → accepted (never applies)
  POST   /v1/web/brain/review/candidates/{id}/reject    → pending → rejected
  POST   /v1/web/brain/review/candidates/{id}/apply     → legacy API-reviewed apply only
  POST   /v1/web/brain/review/candidates/{id}/edit      → edit pending draft (Phase E)
  POST   /v1/web/brain/review/candidates/{id}/clarify   → clarification resolution + re-analysis
  POST   /v1/web/brain/review/candidates/{id}/ignore    → ignore (no re-analysis)
  POST   /v1/web/brain/review/candidates/{id}/conflict  → conflict A/B/NEITHER + re-analysis
  GET    /v1/web/brain/review/candidates/{id}/resolution→ resolution status
  GET    /v1/web/brain/activity                         → brain activity feed
  GET    /v1/web/brain/activity/summary                 → brain activity summary (resolved excluded)

Phase E additions are ADDITIVE: the sealed candidate state machine and Apply
Gate are untouched. Clarification/conflict resolution lives in the independent
`candidate_resolution` table; resolved/ignored items leave the pending list and
the "needs attention" counts without ever being marked accepted.

This module only: session auth wiring, CSRF (inherited), SAFE DTO pass-through,
and normalized error mapping {code, message}. It does NOT re-implement accept /
reject / apply semantics, the candidate state machine, or the Canonical Apply
Gate — those remain sealed in brain_router / apply_service / resolution service.
"""

from __future__ import annotations

from pathlib import Path
import os


from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from web_auth import require_session

# Shared service call: reuse the exact sealed handlers (single source of truth).
from brain.api.brain_router import (
    ReviewAcceptRequest,
    ReviewApplyRequest,
    ReviewRejectRequest,
    activity_summary as _brain_activity_summary,
    list_activity as _brain_list_activity,
    review_accept_candidate as _brain_review_accept,
    review_apply_candidate as _brain_review_apply,
    review_candidate_detail as _brain_review_detail,
    review_list_candidates as _brain_review_list,
    review_reject_candidate as _brain_review_reject,
)
from brain.candidates.candidate_resolution import (
    closed_resolution_ids as _res_closed_ids,
    ensure_table as _res_ensure_table,
    get_resolution as _res_get,
    pending_closed_counts as _res_closed_counts,
)
from brain.review_resolution_service import (
    edit_draft as _res_edit_draft,
    ignore_candidate as _res_ignore,
    resolve_clarification as _res_clarify,
    resolve_conflict as _res_conflict,
    run_reanalysis as _res_reanalyze,
)

router = APIRouter()

# Production DB path for the additive resolution store. Tests patch this to an
# isolated temp DB path.
RESOLUTION_DB_PATH = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))


class EditDraftRequest(BaseModel):
    edited_payload: dict = Field(..., description="Safe business fields of the candidate proposal")
    edited_by: Optional[str] = Field(default=None, max_length=80)


class ClarifyRequest(BaseModel):
    user_response: str = Field(..., min_length=1, max_length=4000)


class ConflictRequest(BaseModel):
    selected_option: str = Field(..., pattern="^(A|B|NEITHER)$")
    user_response: Optional[str] = Field(default=None, max_length=4000)


class WebReviewAcceptRequest(BaseModel):
    """Durable user feedback; acceptance itself never applies a candidate."""
    review_notes: Optional[str] = Field(default=None, max_length=500)
    user_instruction: Optional[str] = Field(default=None, max_length=4000)


def _map_error(exc: HTTPException) -> HTTPException:
    """Normalize sealed-handler errors into the SAFE web shape {code, message}.

    Sealed handlers raise HTTPException with detail either as a plain string
    or as {"detail", "code"}; the browser needs a stable, machine-readable
    shape so the UI can tell 401/403/404/409/unsupported-type apart.
    """
    detail = exc.detail
    if isinstance(detail, dict):
        code = str(detail.get("code") or "error")
        message = detail.get("message") or detail.get("detail") or "操作失败。"
        return HTTPException(status_code=exc.status_code, detail={"code": code, "message": str(message)})
    return HTTPException(status_code=exc.status_code, detail={"code": "error", "message": str(detail)})


def _run(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except HTTPException as exc:  # noqa: PERF203 — only raised on error paths
        raise _map_error(exc) from exc


def _service_error(result: dict) -> None:
    """Map resolution-service dict errors to normalized HTTP errors."""
    err = result.get("error")
    if not err:
        return
    if err == "candidate_not_found":
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "该条目不存在或已被移除。"})
    if err == "candidate_not_pending":
        raise HTTPException(status_code=409, detail={"code": "invalid_transition", "message": "该条目已不在待确认状态，请刷新后重试。"})
    if err in ("already_resolved",):
        raise HTTPException(status_code=409, detail={"code": "already_resolved", "message": "该条目已完成处理，无需重复提交。"})
    if err in ("invalid_candidate_type",):
        raise HTTPException(status_code=400, detail={"code": "unsupported_type", "message": "该类型不支持此操作。"})
    if err in ("user_response_required", "invalid_edited_payload", "invalid_selected_option"):
        raise HTTPException(status_code=400, detail={"code": "invalid_request", "message": "请求内容不完整或格式不正确。"})
    raise HTTPException(status_code=500, detail={"code": "resolution_error", "message": "处理失败，请稍后重试。"})


def _run_reanalysis_bg(candidate_id: str, user_text: str, trigger_type: str) -> None:
    """Background follow-up analysis after a clarification/conflict resolution."""
    _res_reanalyze(RESOLUTION_DB_PATH, candidate_id, user_text, trigger_type)


@router.get("/review/candidates", operation_id="webBrainReviewList")
def web_review_list(
    status: str = Query("pending", pattern="^(pending|accepted|rejected)$"),
    candidate_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """List review candidates (pending by default). SAFE allowlisted DTO only.

    Resolved/ignored clarification & conflict candidates, including a response
    whose follow-up reanalysis failed, are excluded from the pending view (their
    Candidate status stays 'pending' — the resolution is tracked independently,
    so the sealed state machine is untouched).
    """
    data = _run(_brain_review_list, status=status, candidate_type=candidate_type, limit=limit)
    if status == "pending":
        closed = _res_closed_ids(RESOLUTION_DB_PATH)
        items = [item for item in data.get("items", []) if item.get("candidate_id") not in closed]
        data = {**data, "items": items, "total": len(items)}
    return data


@router.get("/review/candidates/{candidate_id}", operation_id="webBrainReviewDetail")
def web_review_detail(candidate_id: str):
    """Candidate detail with SAFE evidence (no raw text / private reasoning)."""
    detail = _run(_brain_review_detail, candidate_id)
    resolution = _res_get(RESOLUTION_DB_PATH, candidate_id)
    if resolution:
        detail = {
            **detail,
            "review_feedback": {
                **(detail.get("review_feedback") or {}),
                "user_response": resolution.get("user_response"),
                "selected_option": resolution.get("selected_option"),
                "resolution_status": resolution.get("resolution_status"),
                "reanalysis_run_id": resolution.get("reanalysis_run_id"),
                "reanalysis_error": resolution.get("reanalysis_error"),
            },
        }
    return detail


@router.post("/review/candidates/{candidate_id}/accept", operation_id="webBrainReviewAccept")
def web_review_accept(
    candidate_id: str,
    req: WebReviewAcceptRequest | None = None,
    username: str = Depends(require_session),
):
    """Record Web review + feedback and wait for the next GPT patrol.

    The old Candidate payload is never committed by this endpoint.  The
    feedback is stored in the Candidate review metadata for patrol handoff.
    """
    body = req or WebReviewAcceptRequest()
    notes = (body.review_notes or "").strip()
    instruction = (body.user_instruction or "").strip()
    if notes and instruction:
        notes = f"{notes}\n用户指令：{instruction}"
    elif instruction:
        notes = instruction
    return _run(
        _brain_review_accept,
        candidate_id,
        ReviewAcceptRequest(
            reviewed_by=username or "web",
            review_notes=notes or None,
            review_origin="web",
        ),
    )


@router.post("/review/candidates/{candidate_id}/reject", operation_id="webBrainReviewReject")
def web_review_reject(candidate_id: str, username: str = Depends(require_session)):
    """Reject a pending candidate. No canonical apply."""
    return _run(_brain_review_reject, candidate_id, ReviewRejectRequest(reviewed_by=username or "web", reason=None))


@router.post("/review/candidates/{candidate_id}/apply", operation_id="webBrainReviewApply")
def web_review_apply(candidate_id: str, username: str = Depends(require_session)):
    """Apply only legacy API-reviewed candidates; Web review waits for GPT patrol."""
    detail = _brain_review_detail(candidate_id)
    feedback = detail.get("review_feedback") if isinstance(detail, dict) else None
    if isinstance(feedback, dict) and feedback.get("review_origin") == "web":
        raise HTTPException(
            status_code=409,
            detail={
                "detail": "Web-reviewed write intents must be claimed and normalized by GPT patrol before commit.",
                "code": "REVIEWED_WAITING_GPT",
            },
        )
    return _run(_brain_review_apply, candidate_id, ReviewApplyRequest(applied_by=username or "web"))


# ── Phase E: review resolution & editing (ADDITIVE) ───────────────────────

@router.post("/review/candidates/{candidate_id}/edit", operation_id="webBrainReviewEditDraft")
def web_review_edit_draft(candidate_id: str, req: EditDraftRequest, username: str = Depends(require_session)):
    """Edit a pending candidate's draft. Status stays pending — no accept, no write."""
    result = _res_edit_draft(RESOLUTION_DB_PATH, candidate_id, req.edited_payload, edited_by=req.edited_by or username or "web")
    _service_error(result)
    return result


@router.post("/review/candidates/{candidate_id}/clarify", operation_id="webBrainReviewClarify")
def web_review_clarify(
    candidate_id: str,
    req: ClarifyRequest,
    background_tasks: BackgroundTasks,
    username: str = Depends(require_session),
):
    """Persist the exact user clarification and leave GPT patrol as the next consumer.

    Creates the resolution immediately so the item leaves 待我审核. Does not
    synchronously re-run Mistral/Gemini/Claude/Agy or any side-model analysis.
    """
    result = _res_clarify(RESOLUTION_DB_PATH, candidate_id, req.user_response, reviewed_by=username or "web")
    _service_error(result)
    return result


@router.post("/review/candidates/{candidate_id}/ignore", operation_id="webBrainReviewIgnore")
def web_review_ignore(candidate_id: str, username: str = Depends(require_session)):
    """Temporarily ignore a clarification/conflict candidate. No re-analysis."""
    result = _res_ignore(RESOLUTION_DB_PATH, candidate_id, reviewed_by=username or "web")
    _service_error(result)
    return result


@router.post("/review/candidates/{candidate_id}/conflict", operation_id="webBrainReviewConflict")
def web_review_conflict(
    candidate_id: str,
    req: ConflictRequest,
    background_tasks: BackgroundTasks,
    username: str = Depends(require_session),
):
    """Persist the exact conflict choice. GPT patrol consumes it on the next run."""
    result = _res_conflict(RESOLUTION_DB_PATH, candidate_id, req.selected_option, user_response=req.user_response, reviewed_by=username or "web")
    _service_error(result)
    return result


@router.get("/review/candidates/{candidate_id}/resolution", operation_id="webBrainReviewResolution")
def web_review_resolution(candidate_id: str):
    """Return the resolution row (or null) for a candidate. Read-only."""
    _res_ensure_table(RESOLUTION_DB_PATH)
    resolution = _res_get(RESOLUTION_DB_PATH, candidate_id)
    if resolution is None:
        return {"candidate_id": candidate_id, "resolution": None}
    # SAFE DTO — no internal ids beyond the candidate id.
    return {
        "candidate_id": candidate_id,
        "resolution": {
            "resolution_type": resolution.get("resolution_type"),
            "resolution_status": resolution.get("resolution_status"),
            "user_response": resolution.get("user_response"),
            "selected_option": resolution.get("selected_option"),
            "resolved_at": resolution.get("resolved_at"),
            "reanalysis_run_id": resolution.get("reanalysis_run_id"),
            "reanalysis_error": resolution.get("reanalysis_error"),
        },
    }


@router.get("/activity", operation_id="webBrainActivity")
def web_activity(
    limit: int = Query(50, ge=1, le=200),
    since: Optional[str] = Query(None),
    activity_type: Optional[str] = Query(None),
    source_entity_type: Optional[str] = Query(None),
):
    """AI activity feed (read-only derived view, never exposes private reasoning)."""
    return _run(
        _brain_list_activity,
        limit=limit,
        since=since,
        activity_type=activity_type,
        source_entity_type=source_entity_type,
    )


@router.get("/activity/summary", operation_id="webBrainActivitySummary")
def web_activity_summary():
    """Lightweight AI activity summary counts.

    Resolved/ignored clarification & conflict candidates are subtracted from the
    "needs attention" style counters (their candidate status remains pending).
    """
    summary = _run(_brain_activity_summary)
    if isinstance(summary, dict) and "pending_confirmation_total" in summary:
        closed = _res_closed_counts(RESOLUTION_DB_PATH)
        by_type = closed.get("by_type", {})
        summary["pending_confirmation_total"] = max(0, summary.get("pending_confirmation_total", 0) - closed.get("total", 0))
        attention_types = ("conflict", "ambiguity", "question")
        attention_closed = sum(by_type.get(t, 0) for t in attention_types)
        summary["needs_user_attention"] = max(0, summary.get("needs_user_attention", 0) - attention_closed)
    return summary
