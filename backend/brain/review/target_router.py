"""PAW Target Router (spec §9) — decide the correct formal write target.

The original ``candidate_type`` is NOT automatically the right write target.
This module maps a candidate to the target that its *content* implies, and
reports ``TARGET_MISMATCH`` when that differs from the declared type.

Pure module: no I/O, no writes, no LLM.
"""
from __future__ import annotations

from typing import Any

from .review_routing_policy import (
    T_KNOWLEDGE, T_NO_FORMAL_WRITE, T_PROJECT_DATA, T_PROJECT_HANDBOOK,
    T_PROJECT_OUTCOME, T_PROJECT_RELATION, T_SOURCE_GOVERNANCE, T_TODO,
    TYPE_DEFAULT_TARGET,
)

#: substrings that indicate the real target regardless of candidate_type
CONTENT_TARGET_HINTS = (
    (T_SOURCE_GOVERNANCE, ("source_version", "source version", "source_identity",
                           "来源版本", "来源身份", "权威版本", "crosswalk")),
    (T_PROJECT_HANDBOOK, ("handbook", "项目手册", "手册")),
    (T_PROJECT_DATA, ("metric", "指标", "数据表", "台账数值", "统计口径")),
    (T_PROJECT_RELATION, ("relation", "关联到项目", "项目关联", "关系")),
    (T_PROJECT_OUTCOME, ("outcome", "项目产出", "项目结论", "update_summary",
                         "项目更新")),
    (T_KNOWLEDGE, ("knowledge", "知识页", "知识条目")),
    (T_TODO, ("todo", "待办")),
)

#: operations that must never produce a formal write
NO_WRITE_MARKERS = ("diagnostic", "diagnosis", "系统诊断", "占位", "placeholder",
                    "log", "日志", "test", "测试", "验收", "acceptance")


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def resolve_target(ctx: dict[str, Any]) -> dict[str, Any]:
    """Return ``{target_type, declared_target, target_mismatch, why}``."""
    declared = TYPE_DEFAULT_TARGET.get(_s(ctx.get("candidate_type")), T_NO_FORMAL_WRITE)
    blob = " ".join(_s(ctx.get(k)) for k in
                    ("proposed_change_text", "update_summary", "raw_excerpt",
                     "review_brief_text", "title", "operation"))

    # 1. an explicit target on the proposed change wins
    explicit = _s(ctx.get("declared_target"))
    if explicit:
        return {"target_type": explicit, "declared_target": declared,
                "target_mismatch": explicit != declared,
                "why": "explicit target declared on the proposed change"}

    # 2. no-value shapes never produce a formal write
    if ctx.get("system_diagnostic_only") or ctx.get("internal_repair_only"):
        return {"target_type": T_NO_FORMAL_WRITE, "declared_target": declared,
                "target_mismatch": declared != T_NO_FORMAL_WRITE,
                "why": "diagnostic / internal repair signal carries no formal write"}
    if any(m in blob.casefold() for m in NO_WRITE_MARKERS) and ctx.get("candidate_type") in ("ambiguity", "question"):
        return {"target_type": T_NO_FORMAL_WRITE, "declared_target": declared,
                "target_mismatch": declared != T_NO_FORMAL_WRITE,
                "why": "placeholder / system-state question, no formal write value"}

    # 3. content hints override the declared type
    low = blob.casefold()
    for target, hints in CONTENT_TARGET_HINTS:
        if any(h.casefold() in low for h in hints):
            return {"target_type": target, "declared_target": declared,
                    "target_mismatch": target != declared,
                    "why": "content indicates %s" % target}

    return {"target_type": declared, "declared_target": declared,
            "target_mismatch": False, "why": "declared type is consistent"}
