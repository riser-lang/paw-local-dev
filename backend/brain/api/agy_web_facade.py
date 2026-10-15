"""Agy Web Session Facade — paw_session-authenticated Antigravity bridge.

Mounted at /v1/web/agy behind `require_workspace_request` (session + CSRF).
Uses PAW_AGY_GROUNDED_CHAT_V1 via brain.context.assistant_workspace_context.

Safety: chat NEVER writes canonical data; NO server ops;
NO Google credential exposure; NO arbitrary argv.

Chat history (§17) is stored in a standalone SQLite file under the agent-jobs
directory — never in the canonical workspace database — and holds only the
visible transcript.  A history-write failure is logged and swallowed so it can
never take down answering; the transcript is a convenience, not a dependency.
"""
from __future__ import annotations

import os
import secrets
import sys
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from brain.runtime.agy_runtime import get_adapter, WEB_MODES
from brain.runtime import agy_chat_history as hist
from brain.context.assistant_workspace_context import build_assistant_message
from brain.runtime.agy_v2_runtime import build_v2_turn, grounding_metadata, FAIL_CLOSED_MESSAGE as V2_FAIL_CLOSED_MESSAGE
from brain.context import agy_answer_contract as agy_v2_answer

# Runtime module root comes from the explicit PAW Runtime Path Contract
# (/etc/paw-runtime-paths.env); no development-tree fallback.
_KSHEET_WORKER = (
    os.environ.get("PAW_KSHEET_WORKER_ROOT")
    or os.environ.get("PAW_RUNTIME_MODULE_ROOT")
)
if not _KSHEET_WORKER:
    raise RuntimeError(
        "PAW_RUNTIME_MODULE_ROOT (or legacy PAW_KSHEET_WORKER_ROOT) is required "
        "(PAW Runtime Path Contract, /etc/paw-runtime-paths.env)"
    )
if _KSHEET_WORKER not in sys.path:
    sys.path.insert(0, _KSHEET_WORKER)

router = APIRouter()
_LOGGER = logging.getLogger(__name__)

MAX_MESSAGE_LEN = 4000
PAW_WEB_FIXED_MODEL = "gemini-3.8-flash-high"


class WebAgyCreateRequest(BaseModel):
    project_id: Optional[str] = None
    mode: str = Field(default="chat")
    model: str = Field(default=PAW_WEB_FIXED_MODEL)


class WebAgyMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=MAX_MESSAGE_LEN)
    mode: Optional[str] = None
    model: Optional[str] = None
    #: supplied by the client so a retry updates the existing row instead of
    #: creating a duplicate user message (spec §17.7)
    client_message_id: Optional[str] = Field(default=None, max_length=128)


def _grounding_response(bundle: dict[str, Any], *, grounded: bool, gate: str = "") -> dict[str, Any]:
    from paw_agy_grounded_chat import grounding_metadata

    meta = grounding_metadata(bundle, grounded=grounded)
    if gate:
        meta["gate"] = gate
    return meta


def _apply_output_gate(response: str, bundle: dict[str, Any]) -> tuple[str, bool, str]:
    from paw_agy_grounded_chat import FAIL_CLOSED_MESSAGE, validate_grounded_response

    ok, reason = validate_grounded_response(response, bundle)
    if ok:
        return response, True, reason
    extension = bool(bundle.get("extension_allowed"))
    strict_suffix = (
        "\n\n【重试约束】上次回答未通过审计："
        f"{reason}。请仅引用有效 evidence_id，"
        + ("不得给出未请求的建议。" if not extension else "建议须标注且有据。")
    )
    return strict_suffix, False, reason


def _persist_web_fixed_model(adapter, session) -> None:
    session.model = PAW_WEB_FIXED_MODEL
    adapter._save_session(session)


#: Context-rotation state owned by the adapter's bounded-context logic.  It is
#: deliberately *not* part of ConversationContext: that dataclass has a fixed
#: field set and filters unknown keys on load, so a plain assignment here would
#: silently drop these and the conversation would rotate on every single turn
#: (the trigger would keep reading epoch_start_turn=0).
_CONTEXT_CARRIED_KEYS = (
    "context_epoch",
    "epoch_start_turn",
    "rotation_count",
    "context_summary",
    "rotated_at",
    "rotation_reason",
)


def _merge_context(session, conv_dict: dict) -> dict:
    """Conversation context plus the rotation state the adapter owns."""
    carried = {k: v for k, v in (session.v2_context or {}).items()
               if k in _CONTEXT_CARRIED_KEYS}
    return {**conv_dict, **carried}


#: A rejected answer is retried once.  A generic "follow the contract" nudge is
#: useless when the failure is specific, so each gate reason gets the repair
#: instruction that actually fixes it.  The suffix is appended to the bounded
#: prompt, so it stays short.
_RETRY_REPAIR = {
    "empty_response":
        "上次回答为空。请直接输出完整回答正文。",
    "missing_evidence_map":
        "上次回答缺少机器证据映射。必须在正文之后追加一行："
        "<<<EVIDENCE_MAP {\"claims\":[...]} EVIDENCE_MAP>>>。",
    "malformed_evidence_map":
        "EVIDENCE_MAP 必须是合法 JSON，形如 "
        "{\"claims\":[{\"kind\":\"fact\",\"text\":\"...\",\"evidence\":[\"ID\"]}]}。",
    "unbacked_analysis":
        "被拒的 claim 属于分析类，必须给出 derived_from（所依据事实的 evidence id）。",
    "unbacked_recommendation":
        "被拒的 claim 属于建议类，必须给出 derived_from（所依据事实的 evidence id）。",
    "recommendation_as_fact":
        "不得把建议或分析表述成 PAW 已确认的决定；改写为「建议/可以考虑/我的判断」"
        "并给出 derived_from。",
    "uncited_paw_claim":
        "被拒的 claim 是 PAW 事实断言，必须给出 evidence（对应 evidence id）。",
    "unsolicited_advice":
        "用户未请求建议，请删除面向用户的建议表述，只陈述事实与判断。",
    "draft_stated_as_confirmed":
        "草稿内容必须标注为「草稿/待确认」，不得表述为已确认事实。",
}


def _retry_constraint(reason: str, detail: Any, contract: dict[str, Any]) -> str:
    """Build the retry instruction for one specific gate failure."""
    if isinstance(reason, str) and reason.startswith("invalid_evidence_ref"):
        ids = [e["evidence_id"] for e in contract.get("evidence_index") or []]
        sample = "、".join(ids[:20])
        repair = ("回答中引用了不存在的 evidence id，只能使用给定的 id："
                  "%s%s。" % (sample, " 等" if len(ids) > 20 else ""))
    elif isinstance(reason, str) and reason.startswith("metric_value_mismatch"):
        repair = "指标数值必须与给定值完全一致，不得改写或估算。"
    else:
        repair = _RETRY_REPAIR.get(
            reason,
            "请按回答契约重新输出机器证据映射，并只引用给定 evidence id。")
    claim = ""
    if isinstance(detail, dict) and detail.get("claim"):
        claim = "问题 claim：「%s」。 " % str(detail["claim"])[:60]
    return "\n\n【重试约束】上次回答未通过 PAW Agy V2 审计：%s。%s%s" % (
        reason, claim, repair)


# ---------------------------------------------------------------- history
def _history_record_user(session_id: str, message: str,
                         client_message_id: Optional[str]) -> Optional[str]:
    try:
        hist.create_session(session_id)
        return hist.add_message(
            session_id, hist.ROLE_USER, message,
            status=hist.STATUS_PENDING,
            message_id=("u:%s" % client_message_id) if client_message_id else None,
            set_title=True)
    except Exception:                                            # noqa: BLE001
        _LOGGER.warning("agy chat history: user message not recorded",
                        exc_info=True)
        return None


def _history_finish_user(message_id: Optional[str], status: str) -> None:
    if not message_id:
        return
    try:
        hist.set_message_status(message_id, status)
    except Exception:                                            # noqa: BLE001
        _LOGGER.warning("agy chat history: user status not updated",
                        exc_info=True)


def _history_record_answer(session_id: str, answer: str, *, grounded: bool,
                           intent: str, project_id: Optional[str],
                           evidence_count: int) -> None:
    try:
        hist.add_message(
            session_id, hist.ROLE_AGY, answer,
            status=hist.STATUS_SUCCESS, project_id=project_id,
            intent=intent, grounded=grounded, evidence_count=evidence_count)
    except Exception:                                            # noqa: BLE001
        _LOGGER.warning("agy chat history: answer not recorded", exc_info=True)


def _history_sync_session(session) -> None:
    try:
        hist.touch_session(
            session.session_id, project_id=session.project_id or None,
            conversation_id=session.conversation_id or None,
            model=session.model, mode=session.mode)
    except Exception:                                            # noqa: BLE001
        _LOGGER.warning("agy chat history: session not synced", exc_info=True)


# ---------------------------------------------------------------- routes
@router.post("/chat")
def create_chat_session(req: WebAgyCreateRequest):
    if req.mode not in WEB_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid mode. Allowed: {sorted(WEB_MODES)}")
    adapter = get_adapter()
    session_id = f"agy:{secrets.token_urlsafe(16)}"
    try:
        session = adapter.create_session(
            session_id, model=PAW_WEB_FIXED_MODEL, mode=req.mode, project_id=req.project_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        hist.create_session(
            session_id, model=session.model, mode=session.mode,
            project_id=session.project_id or None,
            conversation_id=session.conversation_id or None)
    except Exception:                                            # noqa: BLE001
        _LOGGER.warning("agy chat history: session row not created",
                        exc_info=True)
    return {
        "session_id": session.session_id,
        "mode": session.mode,
        "model": session.model,
        "project_id": session.project_id or None,
        "active": True,
    }


@router.post("/chat/{session_id}/messages")
def send_chat_message(session_id: str, req: WebAgyMessageRequest):
    adapter = get_adapter()
    session = adapter.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    effective_mode = req.mode or session.mode
    if effective_mode not in WEB_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid mode. Allowed: {sorted(WEB_MODES)}")

    # §17.7 — the user's turn is recorded before inference, as pending.
    user_msg_id = _history_record_user(session_id, req.message,
                                       req.client_message_id)

    turn = build_v2_turn(
        req.message,
        effective_mode,
        project_id=session.project_id or None,
        conversation=session.v2_context,
    )
    # Persist only the bounded project/topic/intent context.  The adapter will
    # load and preserve this metadata when it updates conversation_id/count.
    session.v2_context = _merge_context(session,
                                        turn["conversation"].to_dict())
    if turn.get("project", {}).get("id"):
        session.project_id = turn["project"]["id"]
    _persist_web_fixed_model(adapter, session)

    intent_name = turn["intent"]["intent"]
    project_id = (turn.get("project") or {}).get("id") or session.project_id or None
    evidence_count = len(turn["contract"].get("evidence_index") or [])

    if turn["fail_closed"]:
        # B4: a refusal is not one thing.  PROJECT_NOT_FOUND must never be
        # reported as "no confirmed evidence", and an ambiguous short form must
        # offer the candidate projects instead of a dead end.  The stable
        # GROUNDED_FAIL_CLOSED status is preserved for existing consumers; the
        # specific reason is exposed separately.
        refusal = turn.get("refusal") or {}
        refusal_status = refusal.get("status") or "NO_EVIDENCE"
        refusal_text = refusal.get("message") or V2_FAIL_CLOSED_MESSAGE
        # A deterministic refusal is still the visible answer for this turn.
        _history_finish_user(user_msg_id, hist.STATUS_SUCCESS)
        _history_record_answer(session_id, refusal_text, grounded=False,
                               intent=intent_name, project_id=project_id,
                               evidence_count=evidence_count)
        # Re-read: send_message() is not called on this path, but the adapter
        # may still have rotated the context in a previous turn.  Syncing the
        # in-memory copy keeps the history row aligned with the live session.
        _history_sync_session(adapter.get_session(session_id) or session)
        return {
            "session_id": session_id,
            "response": refusal_text,
            "model": PAW_WEB_FIXED_MODEL,
            "mode": effective_mode,
            "status": "GROUNDED_FAIL_CLOSED",
            "refusal_status": refusal_status,
            "candidates": refusal.get("candidates") or [],
            "answer_type": "fail_closed",
            "intent": intent_name,
            "usage": {},
            "evidence": agy_v2_answer.build_evidence_footer(turn["contract"]),
            "grounding": grounding_metadata(turn, grounded=False,
                                            gate=refusal_status.lower()),
        }

    try:
        result = adapter.send_message(
            session_id=session_id,
            message=turn["prompt"],
            mode=effective_mode,
            model=PAW_WEB_FIXED_MODEL,
        )
    except ValueError as e:
        _history_finish_user(user_msg_id, hist.STATUS_FAILED)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _history_finish_user(user_msg_id, hist.STATUS_FAILED)
        msg = str(e)[:100]
        if "timeout" in msg.casefold():
            raise HTTPException(
                status_code=504,
                detail={"code": "timeout", "message": "Agy 响应超时，请稍后重试。"},
            )
        raise HTTPException(
            status_code=502,
            detail={"code": "agy_unavailable", "message": "Agy 暂时不可用，请稍后重试。"},
        )

    response_text = result.response
    effective_status = result.status
    effective_usage = result.usage
    if result.status != "SUCCESS":
        _LOGGER.warning("Web Agy V2 inference failed: status=%s error=%s exit_code=%s",
                        result.status, result.error[:240], result.exit_code)
    ok, gate_reason, gate_detail = agy_v2_answer.validate_answer(
        response_text, turn["contract"])
    if not ok:
        first_reason = gate_reason
        retry_suffix = _retry_constraint(gate_reason, gate_detail,
                                         turn["contract"])
        try:
            retry_result = adapter.send_message(
                session_id=session_id,
                message=turn["prompt"] + retry_suffix,
                mode=effective_mode,
                model=PAW_WEB_FIXED_MODEL,
            )
            response_text = retry_result.response
            ok2, gate_reason, gate_detail = agy_v2_answer.validate_answer(
                response_text, turn["contract"])
            if not ok2:
                # The gate reason is what makes a fail-closed turn debuggable;
                # without it the refusal is indistinguishable from a refusal
                # caused by genuinely missing evidence.
                _LOGGER.warning(
                    "Web Agy V2 grounding gate rejected the answer twice "
                    "(intent=%s first=%s retry=%s detail=%s)",
                    intent_name, first_reason, gate_reason,
                    str(gate_detail)[:200])
                response_text = V2_FAIL_CLOSED_MESSAGE
                gate_reason = "fail_closed_after_retry"
                grounded = False
            else:
                grounded = True
                effective_status = retry_result.status
                effective_usage = retry_result.usage
        except Exception:
            response_text = V2_FAIL_CLOSED_MESSAGE
            gate_reason = "fail_closed_after_retry"
            grounded = False
    else:
        grounded = True

    visible, _evidence_map = agy_v2_answer.extract_evidence_map(response_text)
    visible = visible if grounded else response_text

    # §17.7 — only the final user-visible answer is stored; the evidence map and
    # the internal prompt never reach the history store.
    _history_finish_user(user_msg_id, hist.STATUS_SUCCESS)
    _history_record_answer(session_id, visible, grounded=grounded,
                           intent=intent_name, project_id=project_id,
                           evidence_count=evidence_count)
    # send_message() loads its own copy of the session, rotates the context and
    # saves it back.  The in-memory object above is therefore stale; re-reading
    # is what lets a rotation (conversation_id cleared) reach the history row
    # instead of the old id being written back over it.
    _history_sync_session(adapter.get_session(session_id) or session)

    return {
        "session_id": session_id,
        "response": visible,
        "model": PAW_WEB_FIXED_MODEL,
        "mode": effective_mode,
        "status": effective_status,
        "answer_type": "grounded" if grounded else "fail_closed",
        "intent": intent_name,
        "usage": effective_usage,
        "evidence": agy_v2_answer.build_evidence_footer(turn["contract"]),
        "authority_notes": turn["contract"]["authority_notes"],
        "clarification": turn["contract"]["clarification"],
        "grounding": grounding_metadata(turn, grounded=grounded, gate=gate_reason),
    }


@router.get("/chats")
def list_chat_sessions(limit: int = 50):
    """Recent conversations, most-recently-updated first (§17.6)."""
    try:
        sessions = hist.session_summaries(limit)
    except Exception:                                            # noqa: BLE001
        _LOGGER.warning("agy chat history: list failed", exc_info=True)
        sessions = []
    return {"sessions": sessions, "total": len(sessions)}


@router.get("/chat/{session_id}")
def get_chat_session(session_id: str):
    """Session metadata plus the visible transcript (§17.6).

    Falls back to runtime session metadata when no history row exists yet, so
    sessions created before chat history was introduced still resolve.
    """
    adapter = get_adapter()
    session = adapter.get_session(session_id)
    detail: Optional[dict[str, Any]] = None
    try:
        detail = hist.session_detail(session_id)
    except Exception:                                            # noqa: BLE001
        _LOGGER.warning("agy chat history: detail failed", exc_info=True)

    if detail is None and not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if detail is None:
        return {
            "session_id": session.session_id,
            "conversation_id": session.conversation_id,
            "model": session.model,
            "mode": session.mode,
            "project_id": session.project_id or None,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "message_count": session.message_count,
            "title": "",
            "messages": [],
            "history_available": False,
            "active": True,
        }
    return {
        "session_id": detail["session_id"],
        "conversation_id": detail["conversation_id"],
        "model": detail["model"],
        "mode": detail["mode"],
        "project_id": detail["project_id"],
        "created_at": detail["created_at"],
        "updated_at": detail["updated_at"],
        "message_count": detail["message_count"],
        "title": detail["title"],
        "messages": detail["messages"],
        "history_available": True,
        "active": bool(session) or True,
    }


@router.delete("/chat/{session_id}")
def close_chat_session(session_id: str):
    adapter = get_adapter()
    closed = adapter.close_session(session_id)
    # ARCHIVE_SUPPORT = NO (spec §11): a conversation the user is finished with
    # must leave no permanent row behind, otherwise the history list slowly
    # becomes a second knowledge base the user can never actually clear.
    # close_session() already removed the runtime session JSON, so the history
    # delete must not try to remove it again.
    deleted_history = False
    try:
        deleted_history = hist.delete_session(session_id, purge_runtime=False)
    except Exception:                                            # noqa: BLE001
        _LOGGER.warning("agy chat history: session row not deleted",
                        exc_info=True)
    if not closed and not deleted_history:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "closed": True,
            "history_deleted": deleted_history}


@router.get("/models")
def list_models():
    adapter = get_adapter()
    models = adapter.list_models()
    return {"models": models, "default": PAW_WEB_FIXED_MODEL}


@router.get("/status")
def agy_status():
    adapter = get_adapter()
    health = adapter.health_check()
    return health
