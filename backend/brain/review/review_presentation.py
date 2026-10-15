"""PAW Review Presentation — turn a decision into human-readable copy.

Every USER_REVIEW_REQUIRED item must be answerable by a business user without
reading JSON, reason codes, UUIDs or internal field names.  Those all go into
``technical_details`` (a collapsed section on the frontend).

The frontend contract (spec §8) requires::

    review_title / what_happened / why_user_needed / current_state /
    evidence_summary / proposed_change / impact_summary / recommended_choice /
    options(2-3) / user_question / technical_details

Forbidden as primary UI: candidate_id, reason_code, raw JSON, source_version
UUID, internal DB fields, and raw analysis-engine markers
(``project_user_decision_required``, ``finding_opus:CONFLICT;...``).

Pure module: no I/O, no LLM.  It composes copy from structured fields that are
already present, and never invents facts.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .review_routing_policy import (
    R_ATTRIBUTION, R_BUSINESS_AUTHORITY, R_COMPENSATION,
    R_CURRENT_VS_HISTORICAL, R_FACT_CONFLICT, R_MAJOR_CALIBER, R_PERFORMANCE,
    R_PERSONNEL, R_POLICY, R_PROJECT_BOUNDARY, R_SOURCE_CONFLICT,
)

#: internal names that must NEVER appear in a primary (non-technical) field
FORBIDDEN_IN_PRIMARY = (
    "candidate_id", "reason_code", "source_version_id", "source_version_uuid",
    "review_brief_json", "review_display_json", "proposed_change",
    "candidate_type", "inference_level", "dedup_fingerprint", "why_unresolved",
    "decision_cluster_id", "bundle_ids", "finding_opus", "finding_user_decision",
    "finding_insufficient", "knowledge_action", "project_user_decision_required",
    "coverage_review_required", "DATA_EXTRACTION_REVIEW_REQUIRED",
    "canonical_topic_version_conflict", "metric_unit_conflict",
)

#: reason code -> business-facing question
_QUESTION_BY_REASON = {
    R_PROJECT_BOUNDARY: "这个项目边界要不要按系统建议调整？",
    R_CURRENT_VS_HISTORICAL: "这几个版本里，哪一个应当作为当前正式版本？",
    R_SOURCE_CONFLICT: "两个来源都可能有效，你希望以哪一个为准？",
    R_FACT_CONFLICT: "不同来源给出的数值或说法不一致，哪一个应作为当前正式口径？",
    R_MAJOR_CALIBER: "这个统计口径应按哪一个作为正式标准？",
    R_ATTRIBUTION: "这条内容涉及人员/角色归属，请确认归属是否正确。",
    R_PERSONNEL: "这条内容涉及人员归属，请确认归属是否正确。",
    R_COMPENSATION: "这条内容涉及薪酬，需要你确认是否按此执行。",
    R_PERFORMANCE: "这条内容涉及绩效，需要你确认是否按此执行。",
    R_POLICY: "这条内容涉及制度或政策，需要你确认处理方向。",
    R_BUSINESS_AUTHORITY: "这条内容会改变业务口径的权威版本，需要你确认。",
}

#: conflict kind -> Chinese noun phrase used in the generated title
_KIND_LABEL = {
    "headcount": "人数口径",
    "metric": "指标口径",
    "budget": "预算口径",
    "version": "版本口径",
    "attribution": "人员归属",
    "compensation": "薪酬口径",
    "document": "文件用途",
    "evidence": "证据充分性",
    "generic": "业务口径",
}

#: a decisive reason code pins the kind before any content sniffing
_KIND_BY_REASON = (
    (R_CURRENT_VS_HISTORICAL, "version"),
    (R_MAJOR_CALIBER, "metric"),
    (R_COMPENSATION, "compensation"),
    (R_ATTRIBUTION, "attribution"),
    (R_PERSONNEL, "attribution"),
    (R_PROJECT_BOUNDARY, "version"),
)

_KIND_HINTS = (
    ("headcount", ("employment-status", "headcount", "人数", "在职", "名册",
                   "台账", "roster")),
    ("metric", ("metric", "indicator", "指标", "签单", "使用率", "满意率",
                "转化")),
    ("budget", ("budget", "expenditure", "预算", "支出", "费用", "付款")),
    ("document", ("reminder document", "mail count", "文件", "邮件", "用途",
                  "作用")),
    ("version", ("version", "版本", "supersede", "取代", "生效")),
    ("attribution", ("attribution", "identity", "归属", "角色", "履历",
                     "岗位")),
    ("evidence", ("evidence", "locator", "finding_ids", "证据", "定位")),
)

_IMPACT_DEFAULT = "确认后系统会按你的选择继续后续流程，不会直接改写正式数据。"

#: stored titles that carry no business meaning — never use them as primary copy
GENERIC_TITLE_MARKERS = (
    "需要用户决定的评审事项", "需要你确认的评审事项", "需要人工核对",
    "系统证据不足", "待系统补齐", "有一条内容需要你确认",
    "请确认这项事项的处理方向",
)

_NUM_RE = re.compile(r"(?P<label>[A-Za-z\u4e00-\u9fff][^;；]{0,24}?)"
                     r"[:：=＝]\s*(?P<num>\d[\d,]*(?:\.\d+)?)")


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value.strip().startswith(("{", "[")):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return None


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in _s(text))


def _is_marker(text: str) -> bool:
    from .review_routing_policy import looks_like_marker
    return looks_like_marker(text)


def _is_generic_title(text: str) -> bool:
    """True when a stored title is placeholder copy with no business meaning."""
    t = _s(text).strip()
    return bool(t) and any(m in t for m in GENERIC_TITLE_MARKERS)


def _clean(text: str) -> str:
    """Strip anything that leaks internal identifiers into primary copy."""
    out = _s(text)
    for token in FORBIDDEN_IN_PRIMARY:
        out = re.sub(re.escape(token), "", out, flags=re.IGNORECASE)
    # drop stray UUIDs and raw cluster/bundle ids
    out = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
                 "", out)
    out = re.sub(r"\b(?:decision-cluster|bundle|decision-cand):[0-9a-f]+\b", "", out)
    return " ".join(out.split())


def _first_readable(*texts: str, require_cjk: bool = False) -> str:
    for t in texts:
        t = _s(t).strip()
        if not t or _is_marker(t):
            continue
        if require_cjk and not _has_cjk(t):
            continue
        return t
    return ""


def _entities(ctx: dict[str, Any]) -> list[str]:
    proposed = _json(ctx.get("proposed_change")) or {}
    ents = proposed.get("affected_entities") or []
    out: list[str] = []
    if isinstance(ents, list):
        for e in ents:
            if isinstance(e, dict):
                name = _s(e.get("label") or e.get("title") or e.get("name"))
            else:
                name = _s(e)
            name = name.strip()
            if name and name not in out:
                out.append(name)
    return out


def _conflict_kind(ctx: dict[str, Any], reasons: list[str] | None = None) -> str:
    for code, kind in _KIND_BY_REASON:
        if code in (reasons or []):
            return kind
    blob = " ".join([_s(ctx.get("raw_excerpt")),
                     _s(ctx.get("why_unresolved")),
                     _s(ctx.get("why_unresolved_narrative")),
                     json.dumps(_json(ctx.get("proposed_change")) or {},
                                ensure_ascii=False)]).casefold()
    for kind, hints in _KIND_HINTS:
        if any(h.casefold() in blob for h in hints):
            return kind
    return "generic"


def _extract_numbers(ctx: dict[str, Any]) -> list[tuple[str, str]]:
    """Deterministically pull (label, value) pairs out of the stored display."""
    display = _json(ctx.get("review_display_json")) or {}
    brief = _json(ctx.get("review_brief_json")) or {}
    found: list[tuple[str, str]] = []
    for src in (display.get("situation"), brief.get("situation"),
                ctx.get("raw_excerpt")):
        text = _s(src)
        if not text:
            continue
        for m in _NUM_RE.finditer(text):
            label = _clean(m.group("label")).strip("　 。，,（(")
            val = m.group("num")
            if not label or len(label) > 20:
                continue
            if (label, val) not in found:
                found.append((label, val))
        if found:
            break
    return found[:4]


def _build_title(ctx: dict[str, Any], kind: str, ents: list[str]) -> str:
    if ents:
        head = ents[0]
        if len(ents) > 1:
            return "「%s」等 %d 项内容的%s需要你确认" % (head, len(ents),
                                                        _KIND_LABEL[kind])
        return "「%s」的%s需要你确认" % (head, _KIND_LABEL[kind])
    return "有一项%s需要你确认处理方向" % _KIND_LABEL[kind]


def _build_options(ctx: dict[str, Any], numbers: list[tuple[str, str]],
                   ents: list[str]) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    if len(numbers) >= 2:
        for i, (label, val) in enumerate(numbers[:3]):
            options.append({"key": chr(ord("A") + i),
                            "label": "以「%s」的 %s 为准" % (label, val),
                            "detail": "把 %s = %s 作为当前正式数值" % (label, val)})
        options.append({"key": "D", "label": "以上都不是，我来说明",
                        "detail": "我会补充正确的口径或数值"})
    elif ents:
        options = [
            {"key": "A", "label": "按当前内容为准，继续后续处理",
             "detail": "确认「%s」按现有内容生效" % ents[0]},
            {"key": "B", "label": "先不改，保留现状",
             "detail": "暂不处理，保持现有正式数据不变"},
            {"key": "C", "label": "我来补充说明", "detail": "我会提供缺失的业务信息"},
        ]
    else:
        options = [
            {"key": "A", "label": "按系统列出的方向处理",
             "detail": "接受上述处理方向"},
            {"key": "B", "label": "先不改，保留现状",
             "detail": "暂不处理，保持现有正式数据不变"},
            {"key": "C", "label": "我来补充说明", "detail": "我会提供缺失的业务信息"},
        ]
    return options


def _evidence_summary(ctx: dict[str, Any]) -> str:
    labels = ctx.get("evidence_labels") or []
    refs = ctx.get("evidence_refs") or []
    if labels:
        return "；".join(_s(x) for x in labels[:5])
    if refs:
        return "系统找到 %d 条相关依据。" % len(refs)
    return "系统暂未找到可直接引用的依据。"


def build_presentation(ctx: dict[str, Any], decision: dict[str, Any],
                       *, options: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Return the §8 presentation block for one routed candidate."""
    reasons = list(decision.get("reason_codes") or [])
    brief = _json(ctx.get("review_brief_json")) or {}
    display = _json(ctx.get("review_display_json")) or {}
    proposed = _json(ctx.get("proposed_change")) or {}

    ents = _entities(ctx)
    kind = _conflict_kind(ctx, reasons)
    numbers = _extract_numbers(ctx)

    # --- title: prefer stored Chinese copy, else generate deterministically --
    title = ""
    for cand in (display.get("title"), brief.get("title")):
        c = _s(cand).strip()
        if c and _has_cjk(c) and not _is_marker(c) and not _is_generic_title(c):
            title = c
            break
    if not title:
        title = _build_title(ctx, kind, ents)
    title = _clean(title)[:120] or _build_title(ctx, kind, ents)

    # --- what happened -----------------------------------------------------
    what = _first_readable(display.get("situation"), brief.get("situation"),
                           require_cjk=True)
    if not what:
        if ents:
            what = ("系统在核对「%s」时发现多个来源给出的内容不一致，"
                    "无法确定哪一个是当前权威口径。" % ents[0])
        else:
            what = "系统在分析资料时发现一处无法自行判定的业务问题。"
    what = _clean(what)[:400]

    # --- question ----------------------------------------------------------
    question = ""
    for r in reasons:
        if r in _QUESTION_BY_REASON:
            question = _QUESTION_BY_REASON[r]
            break
    if not question:
        question = _clean(_first_readable(display.get("decision_question"),
                                          brief.get("decision_question"))) \
            or "请确认这条内容应当如何处理。"
    question = _clean(question)

    # --- proposed change / current state / impact --------------------------
    proposed_change = _first_readable(display.get("proposed_change"),
                                      brief.get("proposed_change"),
                                      proposed.get("update_summary"))
    if not proposed_change:
        proposed_change = ("系统希望以你确认的口径作为正式标准，"
                           "本次不会直接改写正式数据。")
    proposed_change = _clean(proposed_change)[:300]

    current_state = _first_readable(display.get("current_state"),
                                    brief.get("current_state"),
                                    require_cjk=True)
    if not current_state:
        current_state = "当前正式数据保持不变。"
    current_state = _clean(current_state)[:200]

    impact = _first_readable(display.get("impact"), brief.get("impact"),
                             display.get("approval_effect"),
                             require_cjk=True)
    if not impact:
        impact = _IMPACT_DEFAULT
    impact = _clean(impact)[:300]

    if options is None:
        options = _build_options(ctx, numbers, ents)

    presentation = {
        "review_title": _clean(title),
        "what_happened": _clean(what),
        "why_user_needed": (
            "系统已经查过来源、证据、当前状态和项目上下文，仍然无法确定正确的业务含义，"
            "所以需要你的业务判断。"),
        "current_state": _clean(current_state),
        "evidence_summary": _clean(_evidence_summary(ctx)),
        "proposed_change": _clean(proposed_change),
        "impact_summary": _clean(impact),
        "recommended_choice": decision.get("recommended_review_decision", ""),
        "options": options,
        "user_question": _clean(question),
        "technical_details": {
            "candidate_id": ctx.get("candidate_id"),
            "candidate_type": ctx.get("candidate_type"),
            "reason_codes": reasons,
            "risk_class": decision.get("risk_class"),
            "target_type": decision.get("target_type"),
            "confidence": ctx.get("confidence"),
            "inference_level": ctx.get("inference_level"),
            "created_at": ctx.get("created_at"),
            "route": decision.get("route"),
            "matched_rule": decision.get("matched_rule"),
        },
    }
    presentation["primary_clean"] = not primary_fields_are_clean(presentation)
    return presentation


def primary_fields_are_clean(presentation: dict[str, Any]) -> list[str]:
    """Return the internal tokens that leaked into primary copy.

    An empty list means the item is readable by a non-technical user.
    """
    leaks: list[str] = []
    primary_keys = ("review_title", "what_happened", "why_user_needed",
                    "current_state", "evidence_summary", "proposed_change",
                    "impact_summary", "user_question")
    for key in primary_keys:
        text = _s(presentation.get(key))
        for token in FORBIDDEN_IN_PRIMARY:
            if token.lower() in text.lower():
                leaks.append("%s:%s" % (key, token))
        if re.search(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}", text):
            leaks.append("%s:uuid" % key)
        if _is_marker(text):
            leaks.append("%s:raw_marker" % key)
    for opt in presentation.get("options") or []:
        for v in opt.values():
            if isinstance(v, str):
                for token in FORBIDDEN_IN_PRIMARY:
                    if token.lower() in v.lower():
                        leaks.append("options:%s" % token)
    return leaks
