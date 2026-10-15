"""Agnes Web Session Facade — backward-compatible alias to grounded /v1/web/agy.

Mounted at /v1/web/agnes behind `require_workspace_request`. Delegates all chat
operations to brain.api.agy_web_facade (PAW_AGY_GROUNDED_CHAT_V1 + fixed Gemini).

Legacy browser assets and sessionStorage UUID sessions are normalized automatically.
"""
from __future__ import annotations

import re
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from brain.api.agy_web_facade import (
    PAW_WEB_FIXED_MODEL,
    WebAgyCreateRequest,
    WebAgyMessageRequest,
    create_chat_session as _agy_create,
    get_chat_session as _agy_get,
    send_chat_message as _agy_send,
)
from brain.runtime.agy_runtime import WEB_MODES, get_adapter
from web_auth import require_session

router = APIRouter()

MAX_MESSAGE_LEN = 4000
LEGACY_MODE = "workspace_chat"
_LEGACY_MODE_MAP = {LEGACY_MODE: "chat"}
_UUID_SESSION_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class WebAgnesCreateRequest(BaseModel):
    project_id: Optional[str] = None
    mode: str = Field(default=LEGACY_MODE)


class WebAgnesMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=MAX_MESSAGE_LEN)


def _map_mode(mode: str) -> str:
    mapped = _LEGACY_MODE_MAP.get(mode, mode)
    return mapped if mapped in WEB_MODES else "chat"


def _to_legacy_mode(mode: str) -> str:
    if mode == "chat":
        return LEGACY_MODE
    return mode


def _is_recoverable_session_id(session_id: str) -> bool:
    sid = (session_id or "").strip()
    if not sid:
        return True
    if sid.startswith("agy:"):
        return False
    return bool(_UUID_SESSION_RE.match(sid)) or True


def _recover_agy_session(*, project_id: str | None = None) -> str:
    adapter = get_adapter()
    session_id = f"agy:{secrets.token_urlsafe(16)}"
    adapter.create_session(
        session_id,
        model=PAW_WEB_FIXED_MODEL,
        mode="chat",
        project_id=project_id,
    )
    return session_id


def _resolve_session_id(session_id: str, *, project_id: str | None = None) -> tuple[str, bool]:
    adapter = get_adapter()
    if adapter.get_session(session_id):
        return session_id, False
    if _is_recoverable_session_id(session_id):
        return _recover_agy_session(project_id=project_id), True
    raise HTTPException(status_code=404, detail={"code": "session_not_found", "message": "Session not found"})


def _normalize_error(exc: HTTPException) -> HTTPException:
    detail = exc.detail
    if isinstance(detail, dict):
        code = str(detail.get("code") or "error")
        message = detail.get("message") or detail.get("detail") or "请求失败，请稍后重试。"
        return HTTPException(status_code=exc.status_code, detail={"code": code, "message": str(message)})
    text = str(detail)
    if exc.status_code == 504 or "timeout" in text.casefold():
        return HTTPException(status_code=504, detail={"code": "timeout", "message": "Agy 响应超时，请稍后重试。"})
    if exc.status_code >= 500:
        return HTTPException(status_code=502, detail={"code": "agy_unavailable", "message": "Agy 暂时不可用，请稍后重试。"})
    return HTTPException(status_code=exc.status_code, detail={"code": "error", "message": text})


def _run(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except HTTPException as exc:  # noqa: PERF203
        raise _normalize_error(exc) from exc


# ── Read-only workspace snapshot (retained for regression tests) ───────────

MAX_SNAPSHOT_TODOS = 8
MAX_SNAPSHOT_CAPTURES = 5
MAX_SNAPSHOT_PROJECTS = 10


def _workspace_snapshot_text() -> str:
    """Compact READ-ONLY snapshot of the current Workspace for legacy tests."""
    try:
        from datetime import date, timedelta

        from workspace_api import repository as ws_repo
        from workspace_api.time_utils import business_today
        from brain.candidates.repository import candidate_summary as _candidate_summary

        today = ws_repo.today_view()
        projects = ws_repo.list_projects(80)
        open_projects = [p for p in projects if p.get("status") != "archived"][:MAX_SNAPSHOT_PROJECTS]
        summary = _candidate_summary()
        unprocessed = ws_repo.unprocessed_captures(5)

        lines = [f"日期：{today.get('date') or ''}"]
        if open_projects:
            names = "、".join(f"{p.get('name')}({p.get('status')})" for p in open_projects)
            lines.append(f"开放项目（{len(open_projects)}）：{names}")
        else:
            lines.append("开放项目：无")
        todos = today.get("todos") or []
        if todos:
            titles = "；".join(str(t.get("title"))[:40] for t in todos[:MAX_SNAPSHOT_TODOS])
            lines.append(f"未完成待办（{len(todos)}）：{titles}")
        else:
            lines.append("未完成待办：无")
        captures = today.get("captures") or []
        if captures:
            preview = "；".join(str(c.get("raw_text"))[:30] for c in captures[:MAX_SNAPSHOT_CAPTURES])
            lines.append(f"今日记录（{len(captures)}）：{preview}")
        if unprocessed:
            lines.append(f"未处理记录（{len(unprocessed)}）")
        today_date_str = today.get("date")
        try:
            base_date = date.fromisoformat(today_date_str) if today_date_str else business_today()
        except Exception:
            base_date = business_today()
        recent = []
        for offset in (1, 2, 3):
            day = (base_date - timedelta(days=offset)).isoformat()
            daily = ws_repo.daily_view(day)
            caps = (daily or {}).get("captures") or []
            if caps:
                recent.append(f"{day}（{len(caps)}条）")
        if recent:
            lines.append("最近Daily：" + "；".join(recent))
        if isinstance(summary, dict):
            lines.append(f"待确认候选：{summary.get('pending_total', 0)}")
        return "；".join(lines)
    except Exception:
        return ""


@router.post("/chat", operation_id="webAgnesCreate")
def web_agnes_create(body: WebAgnesCreateRequest, _username: str = Depends(require_session)):
    """Create grounded Agy session (legacy /agnes compatibility alias)."""
    data = _run(
        _agy_create,
        WebAgyCreateRequest(
            project_id=body.project_id,
            mode=_map_mode(body.mode),
            model=PAW_WEB_FIXED_MODEL,
        ),
    )
    return {
        "session_id": data.get("session_id"),
        "mode": _to_legacy_mode(str(data.get("mode") or "chat")),
        "project_id": data.get("project_id"),
    }


@router.post("/chat/{session_id}/messages", operation_id="webAgnesSend")
def web_agnes_send(session_id: str, body: WebAgnesMessageRequest, _username: str = Depends(require_session)):
    """Send message via grounded Agy path; auto-recover legacy UUID sessions."""
    effective_session_id, recovered = _resolve_session_id(session_id)
    data = _run(
        _agy_send,
        effective_session_id,
        WebAgyMessageRequest(message=body.message, model=PAW_WEB_FIXED_MODEL),
    )
    response_text = data.get("response", "")
    if not isinstance(response_text, str):
        raise HTTPException(
            status_code=502,
            detail={"code": "invalid_response", "message": "Agy 返回了无效响应，请重试。"},
        )
    if not response_text.strip():
        raise HTTPException(
            status_code=502,
            detail={"code": "invalid_response", "message": "Agy 返回了空响应，请重试。"},
        )
    return {
        "session_id": data.get("session_id") or effective_session_id,
        "response": response_text,
        "mode": _to_legacy_mode(str(data.get("mode") or "chat")),
        "recovered_session": recovered,
    }


@router.get("/chat/{session_id}", operation_id="webAgnesSession")
def web_agnes_session(session_id: str, _username: str = Depends(require_session)):
    """Session info; auto-recover legacy UUID sessions."""
    effective_session_id, recovered = _resolve_session_id(session_id)
    data = _run(_agy_get, effective_session_id)
    return {
        "session_id": data.get("session_id") or effective_session_id,
        "mode": _to_legacy_mode(str(data.get("mode") or "chat")),
        "active": data.get("active", True),
        "created_at": data.get("created_at"),
        "recovered_session": recovered,
    }
