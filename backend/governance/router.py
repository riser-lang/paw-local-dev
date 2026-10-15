"""Web Brain Review Facade for PAW Desktop Local Edition.

Provides endpoints mounted at /v1/web/brain/review and /v1/web/brain/activity:
- GET  /v1/web/brain/review/candidates
- GET  /v1/web/brain/review/candidates/{id}
- POST /v1/web/brain/review/candidates/{id}/accept
- POST /v1/web/brain/review/candidates/{id}/apply
- POST /v1/web/brain/review/candidates/{id}/reject
- POST /v1/web/brain/review/candidates/{id}/edit
- GET  /v1/web/brain/activity
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.governance.service import governance, get_db


router = APIRouter(prefix="/v1/web/brain", tags=["web-brain-review"])


class AcceptRequest(BaseModel):
    review_notes: Optional[str] = None


class EditRequest(BaseModel):
    edited_payload: Dict[str, Any] = Field(default_factory=dict)
    edited_by: Optional[str] = "LocalUser"


class ClarifyRequest(BaseModel):
    user_response: Optional[str] = ""


class ConflictRequest(BaseModel):
    selected_option: Optional[str] = "NEITHER"
    user_response: Optional[str] = ""


def _format_candidate_for_ui(c: Dict[str, Any]) -> Dict[str, Any]:
    """Format raw candidate dict into the ReviewCandidate DTO expected by frontend."""
    cid = c.get("id")
    c_type = c.get("candidate_type", "knowledge")
    raw_status = c.get("status", "pending")
    
    status_map = {
        "pending": "NEEDS_REVIEW",
        "accepted": "APPLIED_OR_COMMITTED",
        "rejected": "REJECTED",
    }
    eff_state = status_map.get(raw_status, "NEEDS_REVIEW")
    
    proposed_change = c.get("proposed_change") or {}
    display = c.get("review_display_json") or {}
    brief = c.get("review_brief_json") or {}
    evidence_info = c.get("review_internal_evidence_json") or {}
    locators = evidence_info.get("evidence_locators", [])

    title = display.get("title") or (proposed_change.get("payload", {}).get("title") if isinstance(proposed_change, dict) else None) or f"{c_type} 提议"
    desc = display.get("description") or c.get("raw_excerpt", "")

    presentation = {
        "display_title": title,
        "category_label": "知识提炼" if c_type in ("knowledge", "fact") else "任务待办" if c_type == "todo" else "项目更新",
        "situation": desc[:200] if desc else "请根据工作文档提取的事实进行核对。",
        "decision_question": f"是否确认将此条目正式纳入正式知识库/工作区？",
        "approval_effect": f"批准后将通过确定性 Committer 写入正式业务库 (formal_state_bypass=0)",
        "recommendation": brief.get("recommended_action") or c.get("recommended_action") or "建议批准写入",
        "confidence_label": c.get("confidence", "HIGH"),
        "source_label": c.get("source_id") or "本地工作文档",
        "evidence_items": [f"定位符: {loc}" for loc in locators] if locators else [c.get("raw_excerpt", "高保真文档提取")[:100]] if c.get("raw_excerpt") else ["本地办公文档高保真提取"],
        "evidence_summary": [c.get("raw_excerpt", "")[:120]] if c.get("raw_excerpt") else [],
        "affected_entities": [
            {
                "type": c_type,
                "title": title,
                "label": "正式状态",
                "action": c.get("recommended_action", "create"),
            }
        ],
        "presentation_status": "PRESENTATION_VALID",
        "effective_review_state": eff_state,
    }

    return {
        "candidate_id": cid,
        "id": cid,
        "candidate_type": c_type,
        "target_type": proposed_change.get("target_type", c_type) if isinstance(proposed_change, dict) else c_type,
        "payload": proposed_change.get("payload", {}) if isinstance(proposed_change, dict) else {},
        "status": raw_status,
        "effective_review_state": eff_state,
        "confidence": c.get("confidence", "HIGH"),
        "recommended_action": c.get("recommended_action", "AUTO_WRITE"),
        "created_at": c.get("created_at"),
        "reviewed_at": c.get("reviewed_at"),
        "reviewed_by": c.get("reviewed_by"),
        "inference_level": c.get("inference_level", "EXPLICIT"),
        "summary": title,
        "presentation": presentation,
        "proposed_change": proposed_change,
        "raw_excerpt": c.get("raw_excerpt", ""),
    }


@router.get("/review/candidates")
def list_review_candidates(
    status: str = Query(default="pending"),
    limit: int = Query(default=500),
) -> Dict[str, List[Dict[str, Any]]]:
    candidates = governance.list_candidates(status=status, limit=limit)
    return {"items": [_format_candidate_for_ui(c) for c in candidates]}


@router.get("/review/candidates/{candidate_id}")
def get_review_candidate(candidate_id: str) -> Dict[str, Any]:
    c = governance.get_candidate(candidate_id)
    if not c:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return _format_candidate_for_ui(c)


@router.post("/review/candidates/{candidate_id}/accept")
def accept_review_candidate(candidate_id: str, body: Optional[AcceptRequest] = None) -> Dict[str, Any]:
    notes = body.review_notes if body else ""
    try:
        res = governance.accept_and_commit_candidate(candidate_id, reviewer="LocalUser", review_notes=notes)
        return {"ok": True, "result": res}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/review/candidates/{candidate_id}/apply")
def apply_review_candidate(candidate_id: str) -> Dict[str, Any]:
    try:
        res = governance.accept_and_commit_candidate(candidate_id, reviewer="LocalUser")
        return {"ok": True, "result": res}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/review/candidates/{candidate_id}/reject")
def reject_review_candidate(candidate_id: str) -> Dict[str, Any]:
    try:
        res = governance.reject_candidate(candidate_id, reviewer="LocalUser")
        return {"ok": True, "result": res}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/review/candidates/{candidate_id}/edit")
def edit_review_candidate(candidate_id: str, body: EditRequest) -> Dict[str, Any]:
    try:
        res = governance.accept_and_commit_candidate(
            candidate_id, reviewer=body.edited_by or "LocalUser", edited_payload=body.edited_payload
        )
        return {"ok": True, "result": res}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/review/candidates/{candidate_id}/clarify")
def clarify_review_candidate(candidate_id: str, body: Optional[ClarifyRequest] = None) -> Dict[str, Any]:
    notes = body.user_response if body else ""
    try:
        res = governance.reject_candidate(candidate_id, reviewer="LocalUser", notes=f"用户说明: {notes}")
        return {"ok": True, "result": res}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/review/candidates/{candidate_id}/ignore")
def ignore_review_candidate(candidate_id: str) -> Dict[str, Any]:
    try:
        res = governance.reject_candidate(candidate_id, reviewer="LocalUser", notes="用户暂时忽略")
        return {"ok": True, "result": res}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/review/candidates/{candidate_id}/conflict")
def conflict_review_candidate(candidate_id: str, body: Optional[ConflictRequest] = None) -> Dict[str, Any]:
    notes = f"选项: {body.selected_option} 说明: {body.user_response}" if body else "冲突处理"
    try:
        res = governance.reject_candidate(candidate_id, reviewer="LocalUser", notes=notes)
        return {"ok": True, "result": res}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/activity")
def get_activity_feed(limit: int = Query(default=20)) -> Dict[str, Any]:
    conn = get_db()
    try:
        cur = conn.execute(
            """SELECT cr.id, cr.change_id, cr.result, cr.committed_at, ac.target_type, ac.operation, ac.payload
               FROM commit_record cr
               JOIN approved_change ac ON cr.change_id = ac.change_id
               ORDER BY cr.committed_at DESC LIMIT ?""",
            (limit,),
        )
        items = []
        for r in cur.fetchall():
            row = dict(r)
            try:
                pl = json.loads(row.get("payload") or "{}")
            except Exception:
                pl = {}
            items.append({
                "id": row["id"],
                "type": f"{row['target_type']}.{row['operation']}",
                "title": pl.get("title") or pl.get("name") or f"{row['target_type']} {row['operation']}",
                "timestamp": row["committed_at"],
                "result": row["result"],
            })
        return {"items": items}
    finally:
        conn.close()
