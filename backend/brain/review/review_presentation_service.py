"""Review Presentation Service — deterministic, NON-LLM projection.

Transforms formal Candidate + Effective Review State into a human-readable
ReviewPresentationDTO for the Review UI.

Presentation layer ONLY — never modifies formal Candidate payload.
"""

from __future__ import annotations

from pathlib import Path
import os


import json
import re
import sqlite3
from collections.abc import Mapping
from typing import Any, Optional
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PRODUCTION_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))
BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")

# Review Mode constants
DECISION_ONLY = "DECISION_ONLY"
EXECUTION_READY = "EXECUTION_READY"

# Type labels (Chinese)
TYPE_LABELS = {
    "todo": "待办调整",
    "knowledge": "知识调整",
    "project_update": "项目更新",
    "ambiguity": "需要补充信息",
    "conflict": "冲突需要解决",
    "question": "需要回答问题",
    "fact": "事实确认",
}

# Confidence labels (Chinese)
CONFIDENCE_LABELS = {
    "HIGH": "高置信度",
    "MEDIUM": "中等置信度",
    "LOW": "低置信度",
}

# Inference level labels
INFERENCE_LABELS = {
    "EXPLICIT": "明确陈述",
    "STRONG_INFERENCE": "强推断",
    "WEAK_INFERENCE": "弱推断",
}

# Recommended action labels
ACTION_LABELS = {
    "AUTO_WRITE": "自动写入",
    "DRAFT_ONLY": "仅草稿",
    "ASK_USER": "需要你确认",
    "DO_NOT_WRITE": "不写入",
}

# Candidate.review_display_json is an additive, frozen user projection.  Keep
# its contract check here as well as in the migration module because this
# compiler is the production read boundary (and must fail closed if a caller
# supplies a malformed display blob).
_DISPLAY_SCHEMA = "paw-review-display/3"
_DISPLAY_LOCALE = "zh-CN"
_DISPLAY_FIELDS = (
    "title", "situation", "evidence_summary", "affected_items",
    "decision_question", "recommendation", "approval_effect",
    "conflict_explanation", "next_action",
)
_DISPLAY_TECHNICAL_WORDS = frozenset({
    "GPT", "Opus", "Terra", "Luna", "PAW", "MCP", "ID", "FY", "KPI",
})


def _display_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        return "；".join(f"{key}：{_display_text(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return "、".join(_display_text(item) for item in value)
    return str(value).strip()


def _display_has_chinese(value: Any) -> bool:
    return bool(re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", _display_text(value)))


def _display_has_untranslated_prose(value: Any) -> bool:
    for word in re.findall(r"[A-Za-z]{3,}", _display_text(value)):
        if word in _DISPLAY_TECHNICAL_WORDS or word.upper() == word:
            continue
        return True
    return False


def _review_display_validation_errors(display: Any) -> list[str]:
    """Validate the stored zh-CN display without changing the candidate."""
    if not isinstance(display, Mapping):
        return ["not_object"]
    # Keep this canonical read boundary independent of the worker import path.
    # The worker's optional normalizer has broader lifecycle routing signals;
    # allowing it to change validation here makes the same production DB
    # project differently depending on process ``sys.path``.  V2 requires one
    # deterministic Effective Review Projection.
    if display.get("schema") != _DISPLAY_SCHEMA:
        return ["schema"]
    if display.get("locale") != _DISPLAY_LOCALE:
        return ["locale"]
    for field in _DISPLAY_FIELDS:
        value = display.get(field)
        if value is None or (isinstance(value, (str, list, tuple, dict)) and not value):
            return [f"missing:{field}"]
        if not _display_has_chinese(value):
            return [f"not_zh_cn:{field}"]
        if field not in {"evidence_summary", "affected_items"} and _display_has_untranslated_prose(value):
            return [f"contains_untranslated_prose:{field}"]
    if not _display_has_chinese(display.get("machine_state_explanation")):
        return ["not_zh_cn:machine_state_explanation"]
    return []


def _validated_review_display(candidate: Mapping[str, Any]) -> dict[str, Any] | None:
    raw = candidate.get("review_display_json")
    if not raw:
        return None
    try:
        display = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return None
    if _review_display_validation_errors(display):
        return None
    return dict(display)


def _connect(db_path: str = PRODUCTION_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_json_parse(value: str | None) -> dict | list | None:
    if not value:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


def _get_project_name(conn: sqlite3.Connection, project_id: str | None) -> str:
    if not project_id:
        return "未分配项目"
    row = conn.execute("SELECT name FROM project WHERE id = ?", (project_id,)).fetchone()
    return row["name"] if row else "未知项目"


def _get_todo_title(conn: sqlite3.Connection, todo_id: str | None) -> str:
    if not todo_id:
        return ""
    row = conn.execute("SELECT title FROM todo WHERE id = ?", (todo_id,)).fetchone()
    return row["title"] if row else "未知待办"


def _get_knowledge_title(conn: sqlite3.Connection, knowledge_id: str | None) -> str:
    if not knowledge_id:
        return ""
    row = conn.execute("SELECT title FROM knowledge_page WHERE id = ?", (knowledge_id,)).fetchone()
    return row["title"] if row else "未知知识页"


def _determine_review_mode(candidate: dict, proposed_change: dict | None) -> str:
    """Determine if this candidate is DECISION_ONLY or EXECUTION_READY.

    EXECUTION_READY requires:
    - Clear target entity (todo_id or knowledge_id)
    - Clear operation (create/update/complete/etc.)
    - Final payload with specific field values
    - No ambiguity in what will be written
    """
    if not proposed_change:
        return DECISION_ONLY

    candidate_type = candidate.get("candidate_type", "")

    if candidate_type == "todo":
        # For todo candidates, check if we have clear todo_id + project_id mapping
        bindings = proposed_change.get("bindings", [])
        if not bindings:
            return DECISION_ONLY
        for b in bindings:
            if not b.get("todo_id") or not b.get("project_id"):
                return DECISION_ONLY
            if not b.get("action"):
                return DECISION_ONLY
        # All bindings have required fields
        return EXECUTION_READY

    if candidate_type == "knowledge":
        # Knowledge candidates typically need a second step to define exact content
        # Unless the proposed_change has exact title + content + project_id
        if (proposed_change.get("title") and 
            proposed_change.get("content") and 
            proposed_change.get("project_id")):
            return EXECUTION_READY
        return DECISION_ONLY

    # Default: ambiguous types are DECISION_ONLY
    return DECISION_ONLY


def _build_situation(candidate: dict, proposed_change: dict | None, conn: sqlite3.Connection) -> str:
    """Build human-readable situation description."""
    candidate_type = candidate.get("candidate_type", "")
    raw_excerpt = candidate.get("raw_excerpt", "") or ""

    if candidate_type == "knowledge":
        change = proposed_change or {}
        target_id = change.get("knowledge_id") or change.get("target_id")
        if target_id:
            title = _get_knowledge_title(conn, target_id)
            return f"系统在审查「{title}」时发现该知识页需要整理或更新。"
        project_id = change.get("project_id")
        if project_id:
            project_name = _get_project_name(conn, project_id)
            return f"系统发现「{project_name}」项目的知识页面存在需要整理的内容。"
        return "系统在巡航中发现知识库存在需要整理的项目。"

    if candidate_type == "todo":
        change = proposed_change or {}
        bindings = change.get("bindings", [])
        if bindings:
            todo_titles = []
            for b in bindings:
                tid = b.get("todo_id")
                if tid:
                    todo_titles.append(_get_todo_title(conn, tid))
            if todo_titles:
                return f"系统发现以下待办缺少项目归属：{'、'.join(todo_titles)}。"
        return "系统发现待办事项需要核对具体的项目归属。"

    if candidate_type == "ambiguity":
        if raw_excerpt:
            return f"系统在分析过程中发现需要补充信息：{raw_excerpt[:200]}"
        return "系统发现需要用户补充信息以继续分析。"

    if candidate_type == "project_update":
        return "系统发现项目信息需要更新。"

    return raw_excerpt[:200] if raw_excerpt else "缺少可核对的具体业务事实，暂不能展示。"


def _build_decision_question(candidate: dict, proposed_change: dict | None, conn: sqlite3.Connection) -> str:
    """Build the decision question for the user."""
    candidate_type = candidate.get("candidate_type", "")

    if candidate_type == "knowledge":
        change = proposed_change or {}
        target_id = change.get("knowledge_id") or change.get("target_id")
        if target_id:
            title = _get_knowledge_title(conn, target_id)
            return f"是否同意对「{title}」进行整理和更新？"
        return "是否同意对相关知识页面进行整理？"

    if candidate_type == "todo":
        change = proposed_change or {}
        bindings = change.get("bindings", [])
        if bindings:
            return "是否确认以下待办的项目归属？"
        return "是否同意调整待办的项目归属？"

    if candidate_type == "ambiguity":
        return "请补充相关信息以帮助系统继续分析。"

    return "请根据页面列出的具体事实和处理方向作出决定。"


def _build_proposed_actions(candidate: dict, proposed_change: dict | None, review_mode: str, conn: sqlite3.Connection) -> list[str]:
    """Build list of actions that will happen if approved."""
    candidate_type = candidate.get("candidate_type", "")
    actions = []

    if candidate_type == "todo":
        change = proposed_change or {}
        bindings = change.get("bindings", [])
        for b in bindings:
            tid = b.get("todo_id")
            pid = b.get("project_id")
            if tid and pid:
                todo_title = _get_todo_title(conn, tid)
                project_name = _get_project_name(conn, pid)
                actions.append(f"将待办「{todo_title}」归属到项目「{project_name}」")
        if not actions:
            actions.append("更新待办的项目归属")

    elif candidate_type == "knowledge":
        if review_mode == DECISION_ONLY:
            actions.append("本次仅确认整理方向，不直接修改知识页面")
            actions.append("Spark 将根据确认方向生成具体的知识页面修改方案")
            actions.append("修改方案将再次提交审核")
        else:
            actions.append("更新知识页面内容")
            actions.append("建立项目与知识页面的关联")

    elif candidate_type == "ambiguity":
        actions.append("记录用户补充的信息")
        actions.append("触发重新分析")

    else:
        actions.append("记录页面列出的具体处理方向")

    return actions


def _build_affected_entities(candidate: dict, proposed_change: dict | None, conn: sqlite3.Connection) -> list[dict]:
    """Build list of affected entities."""
    entities = []
    candidate_type = candidate.get("candidate_type", "")
    change = proposed_change or {}

    if candidate_type == "todo":
        bindings = change.get("bindings", [])
        for b in bindings:
            tid = b.get("todo_id")
            pid = b.get("project_id")
            if tid:
                entities.append({
                    "type": "待办",
                    "id": tid,
                    "title": _get_todo_title(conn, tid),
                    "action": f"归属到「{_get_project_name(conn, pid)}」",
                })

    elif candidate_type == "knowledge":
        target_id = change.get("knowledge_id") or change.get("target_id")
        if target_id:
            entities.append({
                "type": "知识页",
                "id": target_id,
                "title": _get_knowledge_title(conn, target_id),
                "action": "整理和更新",
            })
        project_id = change.get("project_id")
        if project_id:
            entities.append({
                "type": "项目",
                "id": project_id,
                "title": _get_project_name(conn, project_id),
                "action": "关联知识页",
            })

    return entities


def _build_impact_summary(candidate: dict, affected_entities: list) -> str:
    """Build impact summary."""
    if not affected_entities:
        return "影响范围较小，仅涉及个别对象的属性更新。"
    entity_count = len(affected_entities)
    types = set(e["type"] for e in affected_entities)
    type_str = "、".join(types)
    return f"本次调整将影响 {entity_count} 个{type_str}，仅修改项目归属/内容，不涉及删除操作。"


def _build_evidence_summary(candidate: dict, conn: sqlite3.Connection) -> str:
    """Build evidence summary."""
    source_refs = _safe_json_parse(candidate.get("source_refs"))
    raw_excerpt = candidate.get("raw_excerpt", "") or ""
    inference_level = candidate.get("inference_level", "")

    parts = []
    if source_refs and isinstance(source_refs, list):
        parts.append(f"来源引用 {len(source_refs)} 条")
    if raw_excerpt:
        parts.append(f"原始摘录：{raw_excerpt[:100]}")
    if inference_level:
        parts.append(f"推断级别：{INFERENCE_LABELS.get(inference_level, inference_level)}")

    if not parts:
        return "缺少可核对的业务证据。"
    return "；".join(parts)


def _build_recommendation(candidate: dict) -> str:
    """Build recommendation."""
    recommended_action = candidate.get("recommended_action", "")
    confidence = candidate.get("confidence", "")

    if recommended_action == "ASK_USER":
        return f"建议确认。置信度：{CONFIDENCE_LABELS.get(confidence, confidence)}。"
    if recommended_action == "AUTO_WRITE":
        return f"建议执行。置信度：{CONFIDENCE_LABELS.get(confidence, confidence)}。"
    if recommended_action == "DRAFT_ONLY":
        return f"建议草拟方案后确认。置信度：{CONFIDENCE_LABELS.get(confidence, confidence)}。"
    return f"请根据实际情况决定。置信度：{CONFIDENCE_LABELS.get(confidence, confidence)}。"


def _build_source_label_from_candidate(candidate: dict, conn: sqlite3.Connection | None = None) -> str:
    """Build a human-readable source label from candidate source_refs."""
    raw_refs = candidate.get("source_refs")
    if not raw_refs:
        return "来源：PAW 工作台巡航分析"
    if isinstance(raw_refs, str):
        try:
            raw_refs = json.loads(raw_refs)
        except Exception:
            raw_refs = [raw_refs]
    refs = []
    if isinstance(raw_refs, dict):
        refs = raw_refs.get("refs", [])
    elif isinstance(raw_refs, list):
        refs = raw_refs

    if not refs:
        return "来源：PAW 工作台巡航分析"

    source_types = []
    daily_date = None
    capture_date = None

    for ref in refs:
        if not isinstance(ref, str):
            continue
        prefix, ref_id = ref.split(":", 1) if ":" in ref else ("", ref)
        prefix = prefix.lower()
        if conn:
            try:
                if prefix == "capture" or not prefix:
                    row = conn.execute(
                        "SELECT c.captured_at, d.date FROM capture c LEFT JOIN daily d ON c.daily_id = d.id WHERE c.id = ?",
                        (ref_id,)
                    ).fetchone()
                    if row:
                        source_types.append("工作记录")
                        if row["date"]:
                            capture_date = row["date"]
                        elif row["captured_at"]:
                            capture_date = str(row["captured_at"])[:10]
                        continue
                if prefix == "daily" or not prefix:
                    row = conn.execute("SELECT date FROM daily WHERE id = ?", (ref_id,)).fetchone()
                    if row:
                        source_types.append("日志")
                        if row["date"]:
                            daily_date = row["date"]
                        continue
                if prefix == "project" or not prefix:
                    row = conn.execute("SELECT name FROM project WHERE id = ?", (ref_id,)).fetchone()
                    if row:
                        source_types.append("项目")
                        continue
                if prefix == "knowledge" or not prefix:
                    row = conn.execute("SELECT title FROM knowledge_page WHERE id = ?", (ref_id,)).fetchone()
                    if row:
                        source_types.append("知识")
                        continue
                if prefix == "todo" or not prefix:
                    row = conn.execute("SELECT title FROM todo WHERE id = ?", (ref_id,)).fetchone()
                    if row:
                        source_types.append("待办")
                        continue
            except Exception:
                pass

        # Fallback based on prefix
        if prefix == "capture":
            source_types.append("工作记录")
        elif prefix == "daily":
            source_types.append("日志")
        elif prefix == "project":
            source_types.append("项目")
        elif prefix == "knowledge":
            source_types.append("知识")
        elif prefix == "todo":
            source_types.append("待办")

    date_str = capture_date or daily_date
    if date_str:
        if "工作记录" in source_types:
            return f"来源：工作记录 · {date_str}"
        if "日志" in source_types:
            return f"来源：日志 · {date_str}"

    unique_types = []
    for t in source_types:
        if t not in unique_types:
            unique_types.append(t)

    if unique_types:
        return "来源：" + " · ".join(unique_types)

    return "来源：PAW 工作台现有记录"


def _build_source_label(candidate: dict, brief: dict | None = None, conn: sqlite3.Connection | None = None) -> str:
    """Build source label from candidate and optional review_brief."""
    label = _build_source_label_from_candidate(candidate, conn)
    if label and label != "来源：PAW 工作台巡航分析":
        return label

    # Check evidence_summary in brief
    if brief and brief.get("evidence_summary"):
        items = brief["evidence_summary"]
        if isinstance(items, list) and items:
            has_capture = any("Capture" in s or "记录" in s or "抓取" in s for s in items)
            has_knowledge = any("Knowledge" in s or "知识" in s for s in items)
            has_project = any("Project" in s or "项目" in s for s in items)
            has_todo = any("Todo" in s or "待办" in s for s in items)
            parts = []
            if has_capture:
                parts.append("工作记录")
            if has_knowledge:
                parts.append("知识库")
            if has_project:
                parts.append("项目")
            if has_todo:
                parts.append("待办")
            if parts:
                return "来源：" + " · ".join(parts)
            return "来源：业务证据分析"

    return "来源：PAW 工作台巡航分析"


def _build_technical_details(candidate: dict) -> dict:
    """Build technical details for the collapsed section."""
    return {
        "candidate_id": candidate.get("id"),
        "analysis_run_id": candidate.get("analysis_run_id"),
        "candidate_type": candidate.get("candidate_type"),
        "confidence": candidate.get("confidence"),
        "inference_level": candidate.get("inference_level"),
        "recommended_action": candidate.get("recommended_action"),
        "raw_status": candidate.get("status"),
        "source_refs": candidate.get("source_refs"),
        "raw_excerpt": candidate.get("raw_excerpt"),
        "proposed_change": candidate.get("proposed_change"),
        "created_at": candidate.get("created_at"),
        "reviewed_at": candidate.get("reviewed_at"),
        "reviewed_by": candidate.get("reviewed_by"),
        "analysis_model": candidate.get("analysis_model"),
    }


def _build_display_technical_details(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the existing technical_details field without exposing raw prose.

    A valid review_display_json is the user-facing projection boundary.  The
    original Candidate payload remains available through the internal review
    store, but it must not be reintroduced into the DTO's collapsible details
    as English model/payload text.
    """
    details = _build_technical_details(dict(candidate))
    candidate_type = candidate.get("candidate_type", "")
    details.update({
        "candidate_type": TYPE_LABELS.get(candidate_type, "需要审核"),
        "confidence": CONFIDENCE_LABELS.get(candidate.get("confidence", ""), "待核对"),
        "inference_level": INFERENCE_LABELS.get(candidate.get("inference_level", ""), "待核对"),
        "recommended_action": ACTION_LABELS.get(candidate.get("recommended_action", ""), "待核对"),
        "analysis_model": "内部模型信息已隐藏" if candidate.get("analysis_model") else None,
        "raw_excerpt": "原始内容保留在内部证据中。" if candidate.get("raw_excerpt") else None,
        "proposed_change": "原始方案保留在内部证据中。" if candidate.get("proposed_change") else None,
        "source_refs": "来源引用已保留。" if candidate.get("source_refs") else None,
    })
    return details


def _entity_resolver(conn: sqlite3.Connection):
    """Return display-name lookup used by the single presentation compiler."""
    tables = {"project": ("project", "name"), "todo": ("todo", "title"), "knowledge": ("knowledge_page", "title")}

    def resolve(kind: str, identifier: str) -> str:
        table, column = tables.get(kind, ("", ""))
        if not table or not identifier:
            return ""
        row = conn.execute(f"SELECT {column} FROM {table} WHERE id = ?", (identifier,)).fetchone()
        return str(row[0]) if row and row[0] else ""

    return resolve


def _from_review_display(
    candidate: Mapping[str, Any],
    display: Mapping[str, Any],
    *,
    effective_state: Any,
    conn: sqlite3.Connection | None,
    brief: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Compile the stored zh-CN projection into the existing DTO shape."""
    affected_items = display.get("affected_items")
    if not isinstance(affected_items, list):
        affected_items = []
    affected_entities = []
    for item in affected_items:
        if not isinstance(item, Mapping):
            continue
        item_type = item.get("type") or item.get("entity_type") or "实体"
        affected_entities.append({
            "type": item_type,
            "id": item.get("id", ""),
            "title": item.get("label", ""),
            "current_state": item.get("current_state", ""),
            "action": item.get("proposed_state", ""),
        })

    evidence_items = display.get("evidence_summary")
    if not isinstance(evidence_items, list):
        evidence_items = [evidence_items] if evidence_items else []
    evidence_items = [item for item in evidence_items if item is not None]
    evidence_summary = "；".join(_display_text(item) for item in evidence_items)
    proposed_actions = [
        item.get("proposed_state", "") for item in affected_items
        if isinstance(item, Mapping) and item.get("proposed_state")
    ]
    attention_points = display.get("attention_points")
    if not isinstance(attention_points, list):
        # The normalized schema names these two fields explicitly.  The
        # legacy DTO has only attention_points, so expose the conflict note
        # there without inventing a new API field or losing next_action.
        attention_points = []
        if display.get("conflict_explanation"):
            attention_points.append(display["conflict_explanation"])

    # decision_options was not part of paw-review-display/3.  Preserve the
    # existing DTO type, but never let an English legacy brief replace the
    # zh-CN primary projection.  A future display schema may supply it.
    decision_options = display.get("decision_options")
    if not isinstance(decision_options, list):
        decision_options = []

    review_mode = display.get("review_mode") or (brief or {}).get("review_mode", DECISION_ONLY)
    return {
        "candidate_id": candidate.get("id"),
        "presentation_status": "VALID",
        "category_label": TYPE_LABELS.get(candidate.get("candidate_type", ""), candidate.get("candidate_type", "")),
        "display_title": display.get("title", ""),
        "situation": display.get("situation", ""),
        "decision_question": display.get("decision_question", ""),
        "proposed_actions": proposed_actions,
        "affected_entities": affected_entities,
        "impact_summary": display.get("approval_effect", ""),
        "evidence_summary": evidence_summary,
        "evidence_items": evidence_items,
        "recommendation": display.get("recommendation", ""),
        "review_mode": review_mode,
        "review_mode_label": "方向确认" if review_mode == DECISION_ONLY else "可批准执行",
        "confidence_label": CONFIDENCE_LABELS.get(candidate.get("confidence", ""), candidate.get("confidence", "")),
        "source_label": _build_source_label(candidate, brief, conn),
        "approval_effect": display.get("approval_effect", ""),
        "attention_points": attention_points,
        "decision_options": decision_options,
        "routing": display.get("routing") or "USER_REVIEWABLE",
        "effective_review_state": effective_state,
        "technical_details": _build_display_technical_details(candidate),
    }


def present_candidate(candidate: dict | sqlite3.Row, db_path: str = PRODUCTION_DB) -> dict:
    """Transform a formal Candidate into a human-readable ReviewPresentationDTO.

    Priority:
    1. If candidate has valid zh-CN review_display_json → use it as the
       presentation source.
    2. If no valid display exists, use the frozen review_brief/legacy compiler.
    3. If both fail, return a minimal error-safe DTO.

    This is a pure projection — the formal Candidate is never modified.
    """
    if isinstance(candidate, sqlite3.Row):
        candidate = dict(candidate)

    # Check for review_brief
    from brain.review.review_brief_contract import get_review_brief, validate_review_brief
    candidate_id = candidate.get("id")
    brief = None
    if candidate.get("review_brief_json"):
        raw_b = candidate.get("review_brief_json")
        try:
            brief = json.loads(raw_b) if isinstance(raw_b, str) else raw_b
        except Exception:
            brief = None
    elif candidate_id:
        try:
            brief = get_review_brief(candidate_id, db_path)
        except (OSError, sqlite3.Error):
            # Projection helpers are also used with detached candidate dicts
            # in tests/tools; absence of a backing review table is not a
            # reason to replace concrete payload facts with generic copy.
            brief = None

    # Get effective review state
    effective_state = None
    try:
        from brain.review.effective_review_service import get_effective_review_state
        effective_state = get_effective_review_state(candidate_id, db_path)
    except Exception:
        pass

    conn = None
    try:
        conn = _connect(db_path)
    except Exception:
        pass

    try:
        proposed_change = _safe_json_parse(candidate.get("proposed_change")) or {}
        display = _validated_review_display(candidate)
        if display is not None:
            return _from_review_display(
                candidate,
                display,
                effective_state=effective_state,
                conn=conn,
                brief=brief if isinstance(brief, Mapping) else None,
            )
        if brief:
            valid, err = validate_review_brief(brief)
            from brain.review.presentation_builder import _brief_is_usable, _options_for, _statement, _unwrap_change
            parsed_change = _unwrap_change(proposed_change) if isinstance(proposed_change, dict) else {}
            brief_is_concrete = valid and _brief_is_usable(
                brief, candidate.get("candidate_type", ""), parsed_change,
                _statement(candidate, parsed_change),
            )
            if brief_is_concrete:
                affected_items = brief.get("affected_items", [])
                affected_entities = []
                for item in affected_items:
                    if isinstance(item, dict):
                        t = item.get("type", "")
                        type_label = {"knowledge": "知识页", "todo": "待办", "project": "项目", "fact": "事实"}.get(t, TYPE_LABELS.get(t, t or "实体"))
                        affected_entities.append({
                            "type": type_label,
                            "id": item.get("id", ""),
                            "title": item.get("label", ""),
                            "current_state": item.get("current_state", ""),
                            "action": item.get("proposed_state", ""),
                        })

                evidence_summary_raw = brief.get("evidence_summary", [])
                if isinstance(evidence_summary_raw, list):
                    evidence_summary_text = "；".join(evidence_summary_raw)
                else:
                    evidence_summary_text = str(evidence_summary_raw or "")

                review_mode = brief.get("review_mode", DECISION_ONLY)
                category = candidate.get("candidate_type", "")
                category_label = TYPE_LABELS.get(category, category)

                impact_summary = ""
                if affected_entities:
                    impact_summary = f"本次调整涉及 {len(affected_entities)} 个关联对象，不涉及直接删除操作。"
                elif brief.get("approval_effect"):
                    impact_summary = brief.get("approval_effect")

                source_label = _build_source_label(candidate, brief, conn)

                return {
                    "candidate_id": candidate_id,
                    "presentation_status": "VALID",
                    "category_label": category_label,
                    "display_title": brief.get("title", ""),
                    "situation": brief.get("situation", ""),
                    "decision_question": brief.get("decision_question", ""),
                    "proposed_actions": [item.get("proposed_state", "") for item in affected_items if isinstance(item, dict) and item.get("proposed_state")],
                    "affected_entities": affected_entities,
                    "impact_summary": impact_summary,
                    "evidence_summary": evidence_summary_text,
                    "evidence_items": evidence_summary_raw if isinstance(evidence_summary_raw, list) else ([evidence_summary_text] if evidence_summary_text else []),
                    "recommendation": brief.get("recommendation", ""),
                    "review_mode": review_mode,
                    "review_mode_label": "方向确认" if review_mode == DECISION_ONLY else "可批准执行",
                    "confidence_label": CONFIDENCE_LABELS.get(candidate.get("confidence", ""), candidate.get("confidence", "")),
                    "source_label": source_label,
                    "approval_effect": brief.get("approval_effect", ""),
                    "attention_points": brief.get("attention_points", []),
                    "decision_options": _options_for(candidate.get("candidate_type", ""), _statement(candidate, parsed_change)),
                    "effective_review_state": effective_state,
                    "technical_details": _build_display_technical_details(candidate),
                }

        # One deterministic compiler handles both legacy NULL briefs and
        # invalid/generic frozen briefs.  No Candidate row is mutated.
        from brain.review.presentation_builder import build_candidate_presentation
        compiled = build_candidate_presentation(
            {**candidate, "proposed_change": proposed_change},
            brief=None,
            resolver=_entity_resolver(conn) if conn else None,
        )
        if compiled is None:
            return {
                "candidate_id": candidate_id, "presentation_status": "PRESENTATION_INVALID",
                "category_label": TYPE_LABELS.get(candidate.get("candidate_type", ""), "需要审核"),
                "display_title": "审核信息不足",
                "situation": "这条审核记录缺少可核对的业务事实，暂不能进行确认。",
                "decision_question": "请补充具体事实后再审核。", "proposed_actions": [],
                "affected_entities": [], "impact_summary": "不会执行任何操作。",
                "evidence_summary": "缺少足够的业务依据。", "evidence_items": [],
                "recommendation": "暂不确认，先补充事实。", "review_mode": DECISION_ONLY,
                "review_mode_label": "信息不足", "confidence_label": CONFIDENCE_LABELS.get(candidate.get("confidence", ""), candidate.get("confidence", "")),
                "source_label": _build_source_label(candidate, None, conn),
                "approval_effect": "不会执行任何操作。", "attention_points": ["该记录未进入可确认的业务呈现。"],
                "effective_review_state": effective_state, "technical_details": _build_technical_details(candidate),
            }
        source_label = _build_source_label(candidate, None, conn)
        evidence_items = [source_label] if source_label else []
        if compiled.get("evidence_summary"):
            evidence_items.append(compiled["evidence_summary"])
        compiled.update({
            "candidate_id": candidate_id,
            "category_label": TYPE_LABELS.get(candidate.get("candidate_type", ""), compiled.get("category_label", "")),
            "confidence_label": CONFIDENCE_LABELS.get(candidate.get("confidence", ""), candidate.get("confidence", "")),
            "source_label": source_label,
            "evidence_items": evidence_items,
            "evidence_summary": "；".join(evidence_items),
            "effective_review_state": effective_state,
            "technical_details": _build_technical_details(candidate),
        })
        return compiled
    finally:
        if conn:
            conn.close()


def present_candidates(candidates: list[dict], db_path: str = PRODUCTION_DB) -> list[dict]:
    """Transform multiple candidates into presentation DTOs."""
    return [present_candidate(c, db_path) for c in candidates]
