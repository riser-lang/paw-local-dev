#!/usr/bin/env python3
"""The single user-facing language boundary for PAW review data.

Review state, evidence, candidates, and lineage are deliberately not changed
here.  This module only derives a zh-CN display projection and keeps the input
under an explicit ``internal_evidence`` envelope for audit/reviewer use.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Iterable, Mapping

DISPLAY_SCHEMA = "paw-review-display/3"
DISPLAY_LOCALE = "zh-CN"

REQUIRED_DISPLAY_FIELDS = (
    "title",
    "situation",
    "evidence_summary",
    "affected_items",
    "decision_question",
    "recommendation",
    "approval_effect",
    "conflict_explanation",
    "next_action",
)

MACHINE_STATE_EXPLANATIONS = {
    "REVIEW_PENDING": "等待评审",
    "OPUS_REQUIRED": "需要专家复核",
    "OPUS_REVIEWED": "专家复核已完成，等待后续归并",
    "POST_OPUS_PENDING": "等待专家复核后的归并评审",
    "USER_DECISION_REQUIRED": "需要用户决定",
    "USER_DECIDED": "用户决定已记录",
    "INSUFFICIENT_DATA": "证据不足，无法安全判断",
    "INSUFFICIENT_EVIDENCE": "证据不足，无法安全判断",
    "INDETERMINATE": "当前无法确定",
    "CHILD_EXHAUSTED": "子任务已耗尽，仍需人工确认",
    "CLEAR": "已确认可以继续",
    "FINALIZE": "已确认可以收束",
    "NO_ACTION": "确认无需执行动作",
    "FINALIZED": "评审已收束",
    "POLICY_WRITE_READY": "已具备正式写入条件",
}

# These are intentionally conservative phrase substitutions.  Unknown model
# prose is never copied into the user projection; it remains in raw_output.
_PHRASES = (
    ("post-opus consolidation", "专家复核后归并"),
    ("post-opus", "专家复核后"),
    ("gpt independent review", "GPT独立评审"),
    ("independent review", "独立评审"),
    ("review", "评审"),
    ("the source", "该来源"),
    ("source provides", "来源提供"),
    ("source has", "来源存在"),
    ("knowledge/project/data", "知识/项目/数据"),
    ("knowledge", "知识"),
    ("project", "项目"),
    ("data", "数据"),
    ("update", "更新"),
    ("create", "创建"),
    ("generic", "通用"),
    ("required", "需要"),
    ("cannot", "无法"),
    ("current", "当前"),
    ("existing", "已有"),
    ("no formal-state effect", "不影响正式状态"),
    ("USER_DECISION_REQUIRED", "需要用户决定"),
    ("INSUFFICIENT_DATA", "证据不足，无法安全判断"),
    ("INSUFFICIENT_EVIDENCE", "证据不足，无法安全判断"),
    ("INDETERMINATE", "当前无法确定"),
    ("CHILD_EXHAUSTED", "子任务已耗尽"),
    ("cross-document conflict", "跨文档内容存在冲突"),
    ("cross document conflict", "跨文档内容存在冲突"),
    ("conflict", "冲突"),
    ("ambiguity", "歧义"),
    ("ambiguous", "存在歧义"),
    ("unresolved", "尚未解决"),
    ("requires user decision", "需要用户决定"),
    ("user decision is required", "需要用户决定"),
    ("no safe write", "没有安全写入动作"),
    ("write is blocked", "写入已阻止"),
    ("canonical write is blocked", "规范写入已阻止"),
    ("evidence is insufficient", "证据不足"),
    ("insufficient evidence", "证据不足"),
    ("source content", "来源内容"),
    ("source version", "来源版本"),
    ("project boundary", "项目边界"),
    ("business decision", "业务决定"),
    ("model output", "模型输出"),
    ("review required", "需要评审"),
    ("pending", "待处理"),
    ("accepted", "已接受"),
    ("reject", "拒绝"),
    ("rejected", "已拒绝"),
    ("revise", "修订"),
    ("accept", "接受"),
    ("clear", "确认通过"),
    ("finalize", "收束"),
    ("true", "是"),
    ("false", "否"),
)


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (list, tuple, set)):
        return "、".join(_text(item) for item in value if _text(item))
    if isinstance(value, Mapping):
        return "；".join(f"{key}：{_text(item)}" for key, item in value.items() if _text(item))
    return str(value).strip()


def has_chinese(value: Any) -> bool:
    """Return whether a display value contains at least one CJK character."""
    return bool(re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", _text(value)))


def _translate_known(value: Any) -> str:
    source = _text(value)
    if not source:
        return ""
    result = source
    for needle, replacement in _PHRASES:
        result = re.sub(re.escape(needle), replacement, result, flags=re.IGNORECASE)
    return result.strip(" \t\r\n:;，,。")


_ALLOWED_TECHNICAL_WORDS = frozenset({
    "GPT", "Opus", "Terra", "Luna", "PAW", "MCP", "ID", "FY", "KPI",
    "SourceVersion", "Knowledge", "Metric",
})


def _has_untranslated_prose(value: Any) -> bool:
    text = _text(value)
    for word in re.findall(r"[A-Za-z]{3,}", text):
        if word in _ALLOWED_TECHNICAL_WORDS or word.upper() == word:
            continue
        # A predominantly-Chinese sentence with scattered technical tokens
        # is user-readable business prose, not untranslated English prose.
        cjk_count = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", text))
        ascii_word_count = len(re.findall(r"[A-Za-z]{3,}", text))
        if cjk_count >= 10 and cjk_count >= ascii_word_count * 3:
            return False
        return True
    return False


def _zh(value: Any, fallback: str) -> str:
    translated = _translate_known(value)
    if has_chinese(translated) and not _has_untranslated_prose(translated):
        return translated
    # A predominantly-Chinese sentence that merely carries scattered
    # technical tokens (SourceVersion 488785093193@1, FY27, Opus) is already
    # user-readable business prose.  Degrading it to generic copy would
    # destroy the concrete facts the user needs to decide.
    cjk_count = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", translated))
    ascii_word_count = len(re.findall(r"[A-Za-z]{3,}", translated))
    if cjk_count >= 10 and cjk_count >= ascii_word_count * 3:
        return translated
    # Do not leak arbitrary English model prose into a user card.  The exact
    # prose is still available under internal_evidence.raw_output.
    # Never project bare numbers (IDs, version suffixes, cell coordinates,
    # counts) as user-facing 关联要素: only CJK fragments carry business
    # meaning on their own.  A locator like cells[coordinate=J9] must never
    # degrade into "0、9".
    hints: list[str] = []
    for hint in re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]{2,}", _text(value)):
        if hint not in hints:
            hints.append(hint)
    suffix = f" 关联要素：{'、'.join(hints[:6])}。" if hints else ""
    return fallback.rstrip("。") + "。" + suffix


def _as_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _display_evidence(raw: Mapping[str, Any]) -> list[str]:
    candidates = raw.get("evidence_summary")
    if candidates is None:
        candidates = raw.get("evidence_refs")
    if candidates is None:
        candidates = raw.get("evidence")
    result: list[str] = []
    for item in _as_items(candidates):
        if isinstance(item, Mapping):
            value = item.get("claim") or item.get("evidence_locator") or item.get("label") or item.get("id")
        else:
            value = item
        text = _zh(value, "来源证据已关联，原始内容保留在内部证据中。")
        if text:
            result.append(text)
    return result or ["已关联来源证据；具体原始内容保留在内部证据中。"]


def _display_affected(raw: Mapping[str, Any]) -> list[dict[str, str]]:
    candidates = raw.get("affected_items")
    if candidates is None:
        candidates = raw.get("affected_entities")
    if candidates is None:
        candidates = raw.get("affected_targets")
    result: list[dict[str, str]] = []
    for item in _as_items(candidates):
        if isinstance(item, Mapping):
            label = item.get("label") or item.get("name") or item.get("id") or item.get("target")
            current = item.get("current_state") or item.get("current")
            proposed = item.get("proposed_state") or item.get("proposed") or item.get("action")
        else:
            label, current, proposed = item, "待确认", "用户决定后的规范方向"
        label_zh = _zh(label, "待确认事项")
        current_zh = _zh(current, "当前状态待核对")
        proposed_zh = _zh(proposed, "待用户确认后确定")
        result.append({"label": label_zh, "current_state": current_zh, "proposed_state": proposed_zh})
    return result or [{
        "label": "待确认事项",
        "current_state": "当前状态待核对",
        "proposed_state": "待用户确认后确定",
    }]


def _state(raw: Mapping[str, Any], explicit: str | None) -> tuple[str, str]:
    code = (explicit or raw.get("machine_state") or raw.get("state") or raw.get("outcome") or "REVIEW_PENDING")
    code = _text(code).upper() or "REVIEW_PENDING"
    return code, MACHINE_STATE_EXPLANATIONS.get(code, "当前状态需要人工核对")


def validate_review_display(display: Mapping[str, Any], *, require_all: bool = True) -> list[str]:
    """Return contract violations; an empty list means the display is valid."""
    errors: list[str] = []
    if display.get("schema") != DISPLAY_SCHEMA:
        errors.append("schema")
    if display.get("locale") != DISPLAY_LOCALE:
        errors.append("locale")
    for field in REQUIRED_DISPLAY_FIELDS:
        value = display.get(field)
        if value is None or (isinstance(value, (str, list, tuple, dict)) and not value):
            if require_all:
                errors.append(f"missing:{field}")
            continue
        if not has_chinese(value):
            errors.append(f"not_zh_cn:{field}")
        elif field not in {"evidence_summary", "affected_items"} and _has_untranslated_prose(value):
            errors.append(f"contains_untranslated_prose:{field}")
    if not has_chinese(display.get("machine_state_explanation")):
        errors.append("not_zh_cn:machine_state_explanation")
    return errors


def assert_user_decision_display(display: Mapping[str, Any]) -> None:
    errors = validate_review_display(display)
    if errors:
        raise ValueError("USER_DECISION_REQUIRED display language contract failed: " + ",".join(errors))


# Generic placeholder copy that never constitutes a decidable user review
# question on its own.  A field made only of these phrases (plus the
# 关联要素 suffix) is NOT user reviewable.
_GENERIC_PLACEHOLDER_PHRASES = (
    "请根据关联证据确认业务归属和处理方向",
    "当前状态待核对",
    "待用户确认后确定",
    "需要用户决定的评审事项",
    "来源证据已关联，原始内容保留在内部证据中",
    "该事项存在需要人工核对的评审信息",
    "存在尚未消除的冲突或歧义，需要人工确认",
    "请核对证据并提交用户决定",
    "建议暂不执行正式写入，等待用户确认",
    "用户确认后才可继续正式审批流程；不会改变现有审批状态",
    "已关联来源证据；具体原始内容保留在内部证据中",
    "待确认事项",
    "用户决定后的规范方向",
    "待确认",
)

# Signals that the underlying problem is a system/technical deficiency, not
# a business choice a user can make.
_SYSTEM_REMEDIATION_SIGNALS = (
    "no substantive finding evidence",
    "structural identifiers but no substantive",
    "调度结构元数据",
    "仅含调度",
    "finding payload missing",
    "payload missing",
    "parser failure",
    "locator validation failure",
    "CHILD_EXHAUSTED",
    "automation contract validation failure",
    "INDETERMINATE because the cluster supplied",
    "evidence 没有传到",
    "无法验证",
    "finding_insufficient:EVIDENCE_LIMITATION",
)


def _strip_hint_suffix(value: str) -> str:
    return re.sub(r"\s*关联要素：[^。]*。?", "", value).strip()


def _is_generic_only(value: Any) -> bool:
    """True when a display field carries no business content beyond generic copy."""
    text = _strip_hint_suffix(_text(value))
    if not text:
        return True
    remainder = text
    for phrase in _GENERIC_PLACEHOLDER_PHRASES:
        remainder = remainder.replace(phrase, "")
    remainder = remainder.strip("。；;，, \t\r\n")
    # If nothing concrete survives after removing known generic copy, the
    # field is a placeholder.
    return len(remainder) < 4


def _looks_like_internal_locator(value: Any) -> bool:
    """True when evidence text is only internal locator identifiers."""
    text = _text(value)
    if not text:
        return True
    # Internal locator identifiers always carry one of these technical
    # markers.  Pure business prose (Chinese or translated) is never one.
    if not re.search(r"(structured_semantics|canonical_semantic_evidence|evidence_graph|text_blocks|slides\[|cells\[|coordinate=)", text):
        return False
    stripped = re.sub(
        r"(structured_semantics|canonical_semantic_evidence|evidence_graph)[\w.\[\]=]*",
        "",
        text,
    )
    # Strip only ASCII technical characters; CJK business words must survive.
    stripped = re.sub(r"[A-Za-z0-9\[\]=._@;；,，\s]+", "", stripped)
    return len(stripped) == 0


def is_user_reviewable(display: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Decide whether a display constitutes a real, decidable user review.

    A USER_DECISION_REQUIRED item must at minimum offer:
      * a concrete business decision_question (not generic copy);
      * a concrete current_state (not just 待核对);
      * at least one concrete proposed_state / alternative / option;
      * business-readable evidence (not internal locator identifiers);
    and must not ask the user to fix internal technical deficiencies.
    Returns (reviewable, reasons).
    """
    reasons: list[str] = []
    question = _text(display.get("decision_question"))
    if not question or _is_generic_only(question):
        reasons.append("decision_question_not_concrete")
    situation = _text(display.get("situation"))
    if not situation or _is_generic_only(situation):
        reasons.append("situation_not_concrete")
    affected = display.get("affected_items") or []
    has_concrete_state = False
    for item in affected:
        if not isinstance(item, Mapping):
            continue
        current = _text(item.get("current_state"))
        proposed = _text(item.get("proposed_state"))
        if current and not _is_generic_only(current):
            has_concrete_state = True
        elif proposed and not _is_generic_only(proposed):
            has_concrete_state = True
    options = display.get("decision_options") or display.get("alternatives") or []
    has_concrete_option = bool(options)
    if not has_concrete_option:
        for item in affected:
            if isinstance(item, Mapping) and _text(item.get("proposed_state")) and not _is_generic_only(item.get("proposed_state")):
                has_concrete_option = True
                break
    if not has_concrete_state and not has_concrete_option:
        reasons.append("no_concrete_current_or_proposed_state")
    evidence = display.get("evidence_summary")
    evidence_items = evidence if isinstance(evidence, list) else ([evidence] if evidence else [])
    readable = [item for item in evidence_items if item and not _is_generic_only(item) and not _looks_like_internal_locator(item)]
    if not readable:
        reasons.append("evidence_not_business_readable")
    return (not reasons), reasons


def classify_candidate_routing(
    *,
    candidate_type: str,
    raw_excerpt: str,
    brief: Mapping[str, Any] | None,
    display: Mapping[str, Any] | None,
) -> tuple[str, list[str]]:
    """Classify a pending candidate: USER_REVIEWABLE / SYSTEM_REMEDIATION / MALFORMED.

    System-side evidence deficiencies (missing finding payloads, scheduling
    metadata only, parser/locator failures, INDETERMINATE reviews) must be
    remediated by the system, never pushed onto the user.  Only items whose
    evidence is fully presented yet a genuine business choice remains are
    USER_REVIEWABLE.
    """
    haystack_parts: list[str] = [_text(raw_excerpt)]
    if isinstance(brief, Mapping):
        haystack_parts.extend(_text(value) for value in brief.values())
    haystack = " ".join(value for value in haystack_parts if value)
    for signal in _SYSTEM_REMEDIATION_SIGNALS:
        if signal in haystack:
            return "SYSTEM_REMEDIATION", ["system_remediation_signal:" + signal]
    if not _text(raw_excerpt) and not brief:
        return "MALFORMED", ["no_substantive_content"]
    if display is not None:
        reviewable, reasons = is_user_reviewable(display)
        if reviewable:
            return "USER_REVIEWABLE", []
        return "SYSTEM_REMEDIATION", ["not_user_reviewable:" + ",".join(reasons)]
    return "USER_REVIEWABLE", []


def normalize_review_display(
    raw: Mapping[str, Any] | None,
    *,
    machine_state: str | None = None,
    stage: str = "review",
    raw_output: Any | None = None,
    source_refs: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Build a Chinese-only user projection while preserving raw review input."""
    source = dict(raw or {})
    code, state_explanation = _state(source, machine_state)
    reason = source.get("reason") or source.get("why_unresolved") or source.get("conflict_explanation")
    question = source.get("decision_question") or source.get("question")
    title = source.get("title") or source.get("name") or question or reason
    evidence_source = source.get("evidence_summary")
    if evidence_source is None and source_refs is not None:
        source = {**source, "evidence_refs": list(source_refs)}
    display = {
        "schema": DISPLAY_SCHEMA,
        "locale": DISPLAY_LOCALE,
        "stage": stage,
        "machine_state": code,
        "machine_state_explanation": state_explanation,
        "title": _zh(title, f"{state_explanation}的评审事项"),
        "situation": _zh(source.get("situation") or reason, "该事项存在需要人工核对的评审信息。"),
        "evidence_summary": _display_evidence(source),
        "affected_items": _display_affected(source),
        "decision_question": _zh(question, "请根据关联证据确认业务归属和处理方向。"),
        "recommendation": _zh(source.get("recommendation"), "建议暂不执行正式写入，等待用户确认。"),
        "approval_effect": _zh(source.get("approval_effect") or source.get("impact"), "用户确认后才可继续正式审批流程；不会改变现有审批状态。"),
        "conflict_explanation": _zh(reason or source.get("conflict"), "存在尚未消除的冲突或歧义，需要人工确认。"),
        "next_action": _zh(source.get("next_action") or source.get("action"), "请核对证据并提交用户决定。"),
        "internal_evidence_ref": "internal_evidence.raw_output",
        "normalization_status": "PASS",
    }
    # Concrete decision options supplied by the caller are part of the
    # user-facing projection: without them a card is not decidable.
    if isinstance(source.get("decision_options"), list) and source.get("decision_options"):
        display["decision_options"] = [
            dict(option) for option in source["decision_options"] if isinstance(option, Mapping)
        ]
    raw_value = _copy(source if raw_output is None else raw_output)
    display["internal_evidence"] = {"raw_output": raw_value, "raw_output_preserved": True}
    errors = validate_review_display(display)
    if errors:
        # This is defensive: generated fallbacks above must keep the contract.
        raise ValueError("review display language contract failed: " + ",".join(errors))
    return display


def normalize_user_decision_card(cluster: Mapping[str, Any]) -> dict[str, Any]:
    raw_cluster = {key: _copy(value) for key, value in cluster.items() if key != "review_display"}
    raw = {
        "title": cluster.get("title") or cluster.get("decision_question"),
        "situation": cluster.get("situation") or cluster.get("why_unresolved"),
        "decision_question": cluster.get("decision_question"),
        "recommendation": cluster.get("recommendation"),
        "approval_effect": cluster.get("approval_effect") or cluster.get("impact"),
        "conflict_explanation": cluster.get("why_unresolved"),
        "next_action": cluster.get("next_action"),
        "affected_entities": cluster.get("affected_entities"),
        "evidence_refs": cluster.get("evidence_refs"),
        "state": "USER_DECISION_REQUIRED" if cluster.get("status") == "OPEN" else cluster.get("status"),
    }
    display = normalize_review_display(raw, machine_state="USER_DECISION_REQUIRED", stage="user_decision", raw_output=raw_cluster)
    assert_user_decision_display(display)
    return display


def normalize_terra_item(item: Mapping[str, Any]) -> dict[str, Any]:
    raw = {
        "title": item.get("title") or item.get("canonical_topic") or item.get("kind"),
        "situation": item.get("situation") or item.get("reason"),
        "evidence_summary": item.get("evidence_summary") or [f"关联证据数量：{item.get('evidence_count', 0)}"],
        "affected_items": item.get("affected_items") or [item.get("project_id") or item.get("canonical_topic") or item.get("kind")],
        "decision_question": item.get("decision_question"),
        "recommendation": item.get("recommendation"),
        "approval_effect": item.get("approval_effect"),
        "conflict_explanation": item.get("conflict_explanation") or item.get("reason_codes"),
        "next_action": item.get("next_action"),
        "state": item.get("status") or "REVIEW_PENDING",
    }
    return normalize_review_display(raw, machine_state="USER_DECISION_REQUIRED", stage="terra_user", raw_output=item)


__all__ = [
    "DISPLAY_LOCALE",
    "DISPLAY_SCHEMA",
    "REQUIRED_DISPLAY_FIELDS",
    "assert_user_decision_display",
    "classify_candidate_routing",
    "has_chinese",
    "is_user_reviewable",
    "normalize_review_display",
    "normalize_terra_item",
    "normalize_user_decision_card",
    "validate_review_display",
]
