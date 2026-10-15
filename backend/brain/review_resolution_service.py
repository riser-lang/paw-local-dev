"""Review Resolution orchestration — Phase E (REVIEW_RESOLUTION_AND_EDITING_V1).

Layered on top of the SEALED review machinery:

- edit_draft()        → updates the Candidate *draft* only (status stays pending)
- resolve_clarification() → creates resolution + schedules re-analysis
- resolve_conflict()      → creates resolution (A/B/NEITHER) + schedules re-analysis
- ignore_candidate()      → creates ignored resolution, NO re-analysis
- run_reanalysis()        → follow-up analysis: original evidence + user response
                            via the EXISTING engine (two_pass_analysis) and the
                            EXISTING candidate repository (pending drafts only)

Canonical safety: nothing here calls Todo/Project/Knowledge writers. Re-analysis
only persists Candidate(status=pending) — the Review → Accept → Apply gate is
unchanged. AUTO_WRITE_ENABLED stays FALSE.
"""

from __future__ import annotations

from pathlib import Path
import os


import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from brain.candidates import candidate_resolution as resolution_store
from brain.candidates.repository import (
    PRODUCTION_DB,
    create_candidate as _create_candidate,
    get_candidate as _get_candidate,
)
from brain.analysis import engine as _engine
from brain.analysis import repository as _analysis_repo

log = logging.getLogger("paw.review_resolution")

PRODUCTION_DB_PATH = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))

ACTIONABLE_TYPES = ("todo", "project_update", "knowledge", "fact")
CLARIFICATION_TYPES = ("ambiguity", "question")
CONFLICT_TYPES = ("conflict",)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_capture_text(db_path: str, source_refs: Any) -> str:
    """Resolve the first capture ref to its raw text (read-only evidence).

    Fact priority is preserved downstream: RAW Capture/Daily > explicit user
    clarification > Canonical > historical AI interpretation. The capture text
    is the 'original evidence' side of the re-analysis input.
    """
    refs: list = []
    if isinstance(source_refs, str):
        try:
            source_refs = json.loads(source_refs)
        except (json.JSONDecodeError, TypeError):
            source_refs = {}
    if isinstance(source_refs, dict):
        refs = source_refs.get("refs", []) if isinstance(source_refs.get("refs"), list) else []
    elif isinstance(source_refs, list):
        refs = source_refs
    if not refs:
        return ""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    try:
        first = str(refs[0])
        row = con.execute("SELECT raw_text FROM capture WHERE id = ?", (first,)).fetchone()
        return row[0] if row and row[0] else ""
    except sqlite3.Error:
        return ""
    finally:
        con.close()


def _value(item: Any) -> Any:
    """Unwrap enum-like values (Confidence/InferenceLevel/WriteAction)."""
    return getattr(item, "value", item)


def _proposed_change_for(candidate_type: str, item: Any) -> dict:
    proposed = getattr(item, "proposed_change", None)
    statement = str(getattr(item, "statement", "") or "")
    # The analysis prompt's template is "proposed_change":{}, so an empty
    # object means the model gave no structure; keep the statement instead.
    if isinstance(proposed, dict) and proposed:
        if candidate_type == "project_update" and statement and "statement" not in proposed:
            return {**proposed, "statement": statement}
        return proposed
    if candidate_type == "todo":
        return {"title": statement, "description": None}
    return {"statement": statement}


def _persist_pending_candidates(
    db_path: str, analysis_run_id: str, output: Any, source_refs: Any
) -> list[str]:
    """Persist Candidate drafts (status=pending) via the sealed repository.

    The engine itself never persists. Fingerprint dedup inside the repository
    prevents duplicate candidates from repeated re-analysis.
    """
    from brain.analysis.engine import DEFAULT_MODEL

    groups = [
        (getattr(output, "todo_candidates", []), "todo"),
        (getattr(output, "project_update_candidates", []), "project_update"),
        (getattr(output, "knowledge_candidates", []), "knowledge"),
        (getattr(output, "conflicts", []), "conflict"),
        (getattr(output, "ambiguities", []), "ambiguity"),
        (getattr(output, "questions", []), "question"),
    ]
    persisted: list[str] = []
    if isinstance(source_refs, str):
        try:
            source_refs = json.loads(source_refs)
        except Exception:
            source_refs = [source_refs]
    if isinstance(source_refs, dict):
        source_refs = source_refs.get("refs", [])
    if not isinstance(source_refs, list) or not source_refs:
        source_refs = [analysis_run_id]

    for items, ctype in groups:
        for item in items:
            result = _create_candidate(
                db_path=db_path,
                analysis_run_id=analysis_run_id,
                candidate_type=ctype,
                source_refs=source_refs,
                proposed_change=_proposed_change_for(ctype, item),
                confidence=str(_value(getattr(item, "confidence", None)) or "LOW"),
                inference_level=str(_value(getattr(item, "inference_level", None)) or "WEAK_INFERENCE"),
                recommended_action=str(_value(getattr(item, "recommended_action", None)) or "DRAFT_ONLY"),
                analysis_model=DEFAULT_MODEL,
            )
            if isinstance(result, dict) and result.get("id"):
                persisted.append(result["id"])
    return persisted


def run_reanalysis(db_path: str, candidate_id: str, user_text: str, trigger_type: str) -> dict:
    """Follow-up analysis on original evidence + explicit user response.

    Uses the existing two_pass_analysis engine and the existing candidate
    repository. Only Candidate(pending) rows are persisted. Failure is recorded
    on the analysis_run (safe metadata only) — the candidate itself is not
    modified and no canonical write can occur.
    """
    candidate = _get_candidate(candidate_id, db_path)
    if not candidate:
        return {"error": "candidate_not_found"}

    original = _load_capture_text(db_path, candidate.get("source_refs"))
    combined = ""
    if original:
        combined += "【原始记录】\n" + original + "\n\n"
    combined += "【用户补充说明（更高优先级事实）】\n" + user_text

    analysis_id = "re-" + uuid.uuid4().hex[:12]
    refs = candidate.get("source_refs", {})
    try:
        _analysis_repo.insert_analysis_run(
            db_path=db_path,
            analysis_id=analysis_id,
            trigger_type=trigger_type,  # user_clarification | user_conflict_resolution
            raw_text=combined[:12000],
            raw_input_refs=[candidate_id],
            analysis_model=_engine.DEFAULT_MODEL,
            started_at=_now(),
        )
        output, _warnings = _engine.two_pass_analysis(
            combined,
            context={"trigger_type": trigger_type, "source_refs": [candidate_id], "user_response": user_text},
            raise_on_model_error=True,
        )
        new_candidates = _persist_pending_candidates(db_path, analysis_id, output, refs)
        _analysis_repo.complete_analysis_run(db_path, analysis_id, _now(), status="completed")
        resolution_store.attach_reanalysis(db_path, candidate_id, analysis_id)
        return {"analysis_run_id": analysis_id, "new_candidates": new_candidates}
    except Exception as exc:  # noqa: BLE001 — classify safe metadata
        safe = {"code": "reanalysis_error", "message": "重分析失败，可稍后重试"}
        _analysis_repo.complete_analysis_run(db_path, analysis_id, _now(), status="failed", error=json.dumps(safe, ensure_ascii=False))
        resolution_store.mark_reanalysis_failed(db_path, candidate_id, safe["code"])
        log.warning("reanalysis failed candidate=%s: %s", candidate_id, type(exc).__name__)
        return {"error": "reanalysis_failed", "analysis_run_id": analysis_id}


def edit_draft(db_path: str, candidate_id: str, edited_payload: dict, edited_by: str = "user") -> dict:
    """Edit a pending candidate's draft. Status stays 'pending' — no accept.

    Only safe business fields from the candidate proposal are editable; this
    function stores original + reviewed payload so the later Apply uses the
    user-edited version (reviewed_payload has priority in apply_service).
    """
    candidate = _get_candidate(candidate_id, db_path)
    if not candidate:
        return {"error": "candidate_not_found"}
    if candidate["status"] != "pending":
        return {"error": "candidate_not_pending", "current_status": candidate["status"]}
    if not isinstance(edited_payload, dict) or not edited_payload:
        return {"error": "invalid_edited_payload"}

    original_change = candidate.get("proposed_change", {})
    if isinstance(original_change, str):
        try:
            original_change = json.loads(original_change)
        except (json.JSONDecodeError, TypeError):
            original_change = {}

    review_meta = {
        "original_payload": original_change,
        "reviewed_payload": edited_payload,
        "review_metadata": {
            "edited_by": edited_by,
            "edited_at": _now(),
            "action": "edit_draft",
        },
    }
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "UPDATE candidate SET proposed_change = ? WHERE id = ?",
            (json.dumps(review_meta, ensure_ascii=False), candidate_id),
        )
        con.commit()
    finally:
        con.close()
    return {
        "candidate_id": candidate_id,
        "status": "pending",
        "edited": True,
        "message": "草稿已更新，尚未接受，也未写入工作台。",
    }


def _validate_candidate_for_resolution(db_path: str, candidate_id: str, allowed_types: tuple[str, ...]) -> dict:
    candidate = _get_candidate(candidate_id, db_path)
    if not candidate:
        return {"error": "candidate_not_found"}
    if candidate["status"] != "pending":
        return {"error": "candidate_not_pending", "current_status": candidate["status"]}
    ctype = candidate.get("candidate_type")
    if ctype not in allowed_types:
        return {"error": "invalid_candidate_type", "candidate_type": ctype, "allowed": list(allowed_types)}
    return {"ok": True, "candidate": candidate}


def resolve_clarification(db_path: str, candidate_id: str, user_response: str, reviewed_by: str = "user") -> dict:
    """User clarification for ambiguity/question candidates.

    Creates the resolution row (resolved) immediately and schedules the
    follow-up re-analysis (caller runs run_reanalysis, e.g. via BackgroundTasks).
    Never touches canonical data.
    """
    user_response = (user_response or "").strip()
    if not user_response:
        return {"error": "user_response_required"}
    check = _validate_candidate_for_resolution(db_path, candidate_id, CLARIFICATION_TYPES)
    if "error" in check:
        return check
    created = resolution_store.create_resolution(
        db_path, candidate_id, resolution_type="clarification", resolution_status="resolved",
        user_response=user_response, resolved_by=reviewed_by,
    )
    if created.get("error") == "already_resolved":
        existing = created.get("resolution") or {}
        return {
            "candidate_id": candidate_id,
            "resolution_status": existing.get("resolution_status") or "resolved",
            "reanalysis": "not_scheduled",
            "already_resolved": True,
            "message": "说明已记录，等待 AI 巡航。",
        }
    if "error" in created:
        return created
    return {
        "candidate_id": candidate_id,
        "resolution_status": "resolved",
        "reanalysis": "not_scheduled",
        "message": "说明已记录，等待 AI 巡航。",
    }


def resolve_conflict(
    db_path: str, candidate_id: str, selected_option: str, user_response: Optional[str] = None,
    reviewed_by: str = "user",
) -> dict:
    """User resolution for conflict candidates: A / B / NEITHER (+ optional note)."""
    check = _validate_candidate_for_resolution(db_path, candidate_id, CONFLICT_TYPES)
    if "error" in check:
        return check
    if selected_option not in resolution_store.VALID_CONFLICT_OPTIONS:
        return {"error": "invalid_selected_option", "allowed": list(resolution_store.VALID_CONFLICT_OPTIONS)}
    created = resolution_store.create_resolution(
        db_path, candidate_id, resolution_type="conflict", resolution_status="resolved",
        user_response=(user_response or "").strip() or None, selected_option=selected_option,
        resolved_by=reviewed_by,
    )
    if created.get("error") == "already_resolved":
        existing = created.get("resolution") or {}
        return {
            "candidate_id": candidate_id,
            "resolution_status": existing.get("resolution_status") or "resolved",
            "selected_option": existing.get("selected_option") or selected_option,
            "reanalysis": "not_scheduled",
            "already_resolved": True,
            "message": "冲突处理已记录，等待 AI 巡航。",
        }
    if "error" in created:
        return created
    return {
        "candidate_id": candidate_id,
        "resolution_status": "resolved",
        "selected_option": selected_option,
        "reanalysis": "not_scheduled",
        "message": "冲突处理已记录，等待 AI 巡航。",
    }


def ignore_candidate(db_path: str, candidate_id: str, reviewed_by: str = "user") -> dict:
    """Temporarily ignore: resolution_status=ignored, NO re-analysis.

    The candidate leaves the pending review list but stays in the audit trail.
    """
    check = _validate_candidate_for_resolution(
        db_path, candidate_id, CLARIFICATION_TYPES + CONFLICT_TYPES
    )
    if "error" in check:
        return check
    created = resolution_store.create_resolution(
        db_path, candidate_id, resolution_type=check["candidate"].get("candidate_type", "clarification"),
        resolution_status="ignored", resolved_by=reviewed_by,
    )
    if "error" in created:
        return created
    return {"candidate_id": candidate_id, "resolution_status": "ignored", "message": "已暂时忽略。"}
