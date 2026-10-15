"""Deterministic compiler for business-facing review presentations.

Candidate payloads are formal data, not UI copy.  This module is the shared
projection for new and legacy candidates.  It fails closed when facts needed
for a decision are absent.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

CATEGORY_LABELS = {
    "todo": "待办事项", "knowledge": "知识库", "project_update": "项目更新",
    "conflict": "业务冲突", "ambiguity": "需要确认", "question": "待澄清问题",
    "fact": "事实记录",
}
OPERATION_LABELS = {
    "create": "新建", "update": "更新", "complete": "完成", "archive": "归档",
    "link": "关联", "unlink": "解除关联", "revise": "修订",
}
FORBIDDEN_UI_TERMS = (
    "analysis engine", "patrol trigger", "candidate", "ambiguity review",
    "conflict review", "payload", "target_type", "operation", "analysis_run",
    "runtime", "schema", "mcp", "api", "create todo", "create knowledge",
    "update todo", "update project", "approvedchange", "committer", "dedup",
    "fingerprint", "brain_repo", "formal_create", "repository",
)
Resolver = Callable[[str, str], str]


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _clean(value: Any, limit: int = 300) -> str:
    return _text(value)[:limit]


def _check_forbidden(value: str) -> bool:
    lowered = (value or "").lower()
    return any(term in lowered for term in FORBIDDEN_UI_TERMS)


def _unwrap_change(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    if isinstance(value.get("reviewed_payload"), dict):
        return value["reviewed_payload"]
    if isinstance(value.get("original_payload"), dict):
        return value["original_payload"]
    return value


def _infer_operation(candidate_type: str, change: dict) -> str:
    explicit = _text(change.get("operation")).lower()
    if explicit in OPERATION_LABELS:
        return explicit
    kind = _text(change.get("type")).upper()
    if candidate_type == "knowledge" and kind in {"CREATE_NEW", "CREATE"}:
        return "create"
    if candidate_type == "knowledge" and kind in {"REVISE", "UPDATE"}:
        return "revise"
    if candidate_type == "todo" and change.get("bindings"):
        return "link"
    if candidate_type == "project_update" and (change.get("project_id") or change.get("update_summary") or change.get("new_summary")):
        return "update"
    if change.get("id") or change.get("target_id") or change.get("knowledge_id"):
        if candidate_type == "knowledge":
            return "revise"
        if candidate_type == "todo" and _text(change.get("status")).lower() in {"done", "completed"}:
            return "complete"
        return "update"
    return "create"


def _statement(candidate: dict, change: dict) -> str:
    for value in (
        change.get("statement"), candidate.get("raw_excerpt"), candidate.get("statement"),
        change.get("update_summary"), change.get("new_summary"), change.get("summary"),
        change.get("description"), change.get("content"), change.get("title"),
    ):
        value = _text(value)
        if value:
            return value
    return ""


def _resolve(resolver: Optional[Resolver], kind: str, identifier: Any, fallback: str = "") -> str:
    identifier = _text(identifier)
    if not identifier:
        return fallback
    if resolver:
        try:
            value = _text(resolver(kind, identifier))
            if value:
                return value
        except Exception:
            pass
    return fallback


def extract_competing_sides(statement: str) -> list[dict[str, str]]:
    """Extract explicitly labelled sides; never manufacture A/B choices."""
    result: list[dict[str, str]] = []
    patterns = (
        r"([^，。；;：:]+?)提供的是([^，。；;：:]+)",
        r"([^，。；;：:]+?)提供的数据口径(?:是|为)([^，。；;：:]+)",
        r"([^，。；;：:]+?)的(?:数据)?口径(?:是|为)([^，。；;：:]+)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, statement):
            label = _text(match.group(1)).strip(" 的")
            value = _text(match.group(2)).strip(" 数据口径")
            if label and value and not any(x["label"] == label for x in result):
                result.append({"label": label, "value": value})
    return result[:4]


def extract_concrete_options(statement: str) -> list[str]:
    """Extract options only when the statement names them (X 还是 Y)."""
    match = re.search(r"(?:是|为)\s*(.+?)\s*(?:还是|或)\s*(.+?)(?:[？?。；;]|$)", statement)
    if not match:
        return []
    values = [_text(match.group(1)).strip("，, "), _text(match.group(2)).strip("，, ")]
    return [value for value in values if value and len(value) <= 120]


def _options_for(candidate_type: str, statement: str) -> list[dict[str, str]]:
    if candidate_type == "conflict":
        sides = extract_competing_sides(statement)
        if len(sides) < 2:
            return []
        return [{
            "key": chr(ord("A") + index), "label": side["label"],
            "description": side["value"],
            "meaning": f"将{side['label']}的{side['value']}作为后续工作/分析口径",
        } for index, side in enumerate(sides)]
    if candidate_type in {"ambiguity", "question"}:
        return [{
            "key": f"OPTION_{index + 1}", "label": value,
            "description": value, "meaning": value,
        } for index, value in enumerate(extract_concrete_options(statement))]
    return []


def _brief_is_usable(brief: Any, candidate_type: str, change: dict, statement: str) -> bool:
    if not isinstance(brief, dict):
        return False
    required = ("title", "situation", "decision_question", "recommendation", "approval_effect")
    if any(not _text(brief.get(key)) for key in required):
        return False
    fields = [brief.get(key, "") for key in required]
    fields += [item.get("proposed_state", "") for item in brief.get("affected_items", []) if isinstance(item, dict)]
    if any(_check_forbidden(_text(value)) for value in fields):
        return False
    joined = " ".join(_text(value) for value in fields)
    concrete = [value for value in (change.get("title"), change.get("content"), change.get("update_summary"), statement) if _text(value)]
    if candidate_type in {"conflict", "ambiguity", "question"} and statement and statement not in joined:
        return False
    if concrete and candidate_type in {"todo", "project_update"}:
        if not any(_text(value)[:30] in joined for value in concrete):
            return False
    return True


def _from_brief(candidate: dict, brief: dict, options: list[dict[str, str]]) -> dict:
    affected = []
    for item in brief.get("affected_items", []):
        if not isinstance(item, dict):
            continue
        affected.append({
            "type": {"knowledge": "知识页", "todo": "待办", "project": "项目", "fact": "事实"}.get(item.get("type", ""), item.get("type", "实体")),
            "id": item.get("id", ""), "title": item.get("label", ""),
            "current_state": item.get("current_state", ""), "action": item.get("proposed_state", ""),
        })
    evidence = brief.get("evidence_summary", [])
    if not isinstance(evidence, list):
        evidence = [str(evidence)] if evidence else []
    return {
        "schema_version": "2", "presentation_status": "VALID",
        "category_label": CATEGORY_LABELS.get(candidate.get("candidate_type", ""), candidate.get("candidate_type", "")),
        "display_title": _text(brief.get("title")), "situation": _text(brief.get("situation")),
        "decision_question": _text(brief.get("decision_question")),
        "proposed_actions": [entity["action"] for entity in affected if entity.get("action")],
        "affected_entities": affected, "impact_summary": _text(brief.get("approval_effect")),
        "evidence_summary": "；".join(evidence), "evidence_items": evidence,
        "recommendation": _text(brief.get("recommendation")), "approval_effect": _text(brief.get("approval_effect")),
        "attention_points": brief.get("attention_points", []) if isinstance(brief.get("attention_points", []), list) else [],
        "decision_options": brief.get("decision_options") or options,
        "review_mode": brief.get("review_mode", "DECISION_ONLY"),
        "review_mode_label": "方向确认" if brief.get("review_mode", "DECISION_ONLY") == "DECISION_ONLY" else "可批准执行",
    }


def build_candidate_presentation(candidate: dict, *, brief: Optional[dict] = None,
                                 resolver: Optional[Resolver] = None) -> Optional[dict]:
    """Compile one candidate into concrete, safe-to-show review fields."""
    candidate_type = _text(candidate.get("candidate_type"))
    change = _unwrap_change(candidate.get("proposed_change"))
    statement = _clean(_statement(candidate, change))
    if not candidate_type or not statement:
        return None
    options = _options_for(candidate_type, statement)
    if _brief_is_usable(brief, candidate_type, change, statement):
        return _from_brief(candidate, brief, options)

    operation = _infer_operation(candidate_type, change)
    operation_label = OPERATION_LABELS.get(operation, "处理")
    title = _text(change.get("title"))
    project_id = change.get("project_id") or change.get("resolved_project_id")
    project_name = _resolve(resolver, "project", project_id, "指定项目" if project_id else "")
    target_id = change.get("id") or change.get("target_id") or change.get("knowledge_id")
    target_title = title
    if candidate_type == "todo" and target_id and not target_title:
        target_title = _resolve(resolver, "todo", target_id, "该待办")
    if candidate_type == "knowledge" and target_id and not target_title:
        target_title = _resolve(resolver, "knowledge", target_id, "该知识页")
    if candidate_type == "project_update":
        if not project_id:
            return None
        if resolver and not project_name:
            return None
        target_title = project_name or title or "指定项目"
    if candidate_type == "knowledge" and not target_title:
        target_title = "未命名知识"

    affected: list[dict[str, str]] = []
    actions: list[str] = []
    attention: list[str] = []

    if candidate_type == "todo":
        bindings = change.get("bindings") if isinstance(change.get("bindings"), list) else []
        if operation == "link" and bindings:
            for binding in bindings:
                todo_id = binding.get("todo_id") if isinstance(binding, dict) else None
                bind_project_id = binding.get("project_id") if isinstance(binding, dict) else None
                todo_title = _resolve(resolver, "todo", todo_id, "已有待办")
                bind_project = _resolve(resolver, "project", bind_project_id, "指定项目")
                affected.append({"type": "待办", "id": todo_id or "", "title": todo_title,
                                 "current_state": "已有待办，项目归属待确认",
                                 "action": f"绑定到项目「{bind_project}」"})
                actions.append(f"将已有待办「{todo_title}」绑定到项目「{bind_project}」")
            decision_question = "是否确认把这些已有待办绑定到所列项目？"
            situation = f"系统发现以下已有待办需要确认项目归属：{statement}"
        else:
            due = _text(change.get("due_at")) or "未指定"
            description = _text(change.get("description")) or "未提供"
            affected.append({"type": "待办", "id": target_id or "", "title": target_title or "未命名待办",
                             "current_state": "尚未创建", "action": f"标题：{target_title or '未命名待办'}；说明：{description}；截止：{due}"})
            actions = [f"新建待办「{target_title or '未命名待办'}」", f"说明：{description}", f"截止时间：{due}"]
            if project_id:
                actions.append(f"归属项目：{project_name}")
            decision_question = f"是否确认新建待办「{target_title or '未命名待办'}」？"
            situation = f"系统识别出一项需要跟进的待办：{statement}"
    elif candidate_type == "project_update":
        summary = _text(change.get("update_summary") or change.get("new_summary") or statement)
        affected.append({"type": "项目", "id": project_id or "", "title": target_title,
                         "current_state": "现有项目信息待更新", "action": summary})
        actions = [f"更新项目「{target_title}」", f"更新内容：{summary}"]
        decision_question = f"「{target_title}」的这项更新是否正确？"
        situation = f"系统发现项目「{target_title}」可能需要更新。具体建议：{summary}"
    elif candidate_type == "knowledge":
        content = _text(change.get("content") or change.get("summary"))
        if not content and statement != title:
            content = statement
        if not content:
            return None
        state = "现有知识内容待修订" if operation == "revise" else "尚未创建知识记录"
        affected.append({"type": "知识页", "id": target_id or "", "title": target_title,
                         "current_state": state, "action": content})
        if project_id:
            affected.append({"type": "项目", "id": project_id, "title": project_name,
                             "current_state": "项目已存在", "action": "作为关联项目"})
        actions = [f"{('修订' if operation == 'revise' else '新建')}知识「{target_title}」", f"拟写入内容：{content}"]
        if project_id:
            actions.append(f"关联项目：{project_name}")
        decision_question = f"是否确认{('修订' if operation == 'revise' else '新建')}知识「{target_title}」？"
        situation = f"系统发现知识主题需要{('补充更新' if operation == 'revise' else '沉淀')}：{content}"
    elif candidate_type == "conflict":
        sides = extract_competing_sides(statement)
        if len(sides) >= 2:
            side_text = "；".join(f"{side['label']}：{side['value']}" for side in sides)
            situation = f"系统发现两方事实/口径不一致：{side_text}。原始冲突说明：{statement}"
            decision_question = "请决定哪一方口径作为后续工作和分析的基准，或补充正确规则。"
            actions = [f"记录你选择的工作口径：{side['label']}（{side['value']}）" for side in sides]
        else:
            situation = f"系统发现以下冲突，但原始说明未安全提供可命名的双方：{statement}"
            decision_question = "请补充这项冲突的正确事实、处理规则或具体的命名选项。"
            actions = ["记录你的冲突解决说明，供后续分析使用"]
            attention.append("当前无法从原始说明安全区分双方，因此不提供未标注的选项。")
    elif candidate_type in {"ambiguity", "question"}:
        situation = f"系统发现以下问题/歧义需要你的事实确认：{statement}"
        if options:
            decision_question = "请在下列已由原始说明明确列出的选项中选择，或补充其他事实。"
            actions = [f"记录你的选择：{option['label']}" for option in options]
        else:
            decision_question = f"需要你补充的信息是：{statement}"
            actions = ["记录你的补充说明，并据此重新分析"]
            attention.append("原始说明没有提供可安全列出的具体选项。")
    else:
        situation = f"系统发现以下事项需要确认：{statement}"
        decision_question = f"请确认是否需要处理这项具体事项：{statement}"
        actions = [f"记录并处理：{statement}"]

    if not actions or not decision_question:
        return None
    source_refs = candidate.get("source_refs") if isinstance(candidate.get("source_refs"), list) else []
    source_count = len([value for value in source_refs if _text(value)])
    evidence_summary = f"业务依据：{statement}"
    if source_count:
        evidence_summary += f"；来源记录：{source_count} 条"
    if candidate_type == "conflict" and options:
        recommendation = "系统建议先由你指定工作/分析口径，因为现有证据同时包含两方互不一致的明确陈述，系统不能替你定案。"
    elif candidate_type in {"ambiguity", "question"}:
        recommendation = "系统建议补充说明，因为当前信息不足以安全确定唯一答案。"
    else:
        recommendation = f"系统建议{operation_label}{target_title or statement}，因为该建议的具体内容已列在本页，便于你核对后决定。"
    if candidate_type in {"conflict", "ambiguity", "question"}:
        approval_effect = "确认后记录你的处理方向/补充事实，并触发后续分析；不会直接修改正式业务数据。"
    else:
        approval_effect = "本次确认仅表示你认可该方向，不会直接修改正式业务数据；如需写入，将另行提交执行方案。"
    fields = [situation, decision_question, *actions, recommendation, approval_effect, *attention]
    if any(_check_forbidden(field) for field in fields):
        return None
    display_title = (
        f"{('新建' if operation == 'create' else operation_label)}待办：{target_title}" if candidate_type == "todo" else
        f"{('新建' if operation == 'create' else operation_label)}知识：{target_title}" if candidate_type == "knowledge" else
        f"更新项目：{target_title}" if candidate_type == "project_update" else
        f"需要确认：{target_title or statement[:40]}"
    )
    return {
        "schema_version": "2", "presentation_status": "VALID",
        "category_label": CATEGORY_LABELS.get(candidate_type, "待确认"), "display_title": display_title,
        "situation": situation, "decision_question": decision_question,
        "proposed_actions": actions, "affected_entities": affected,
        "impact_summary": approval_effect, "evidence_summary": evidence_summary,
        "evidence_items": [evidence_summary], "recommendation": recommendation,
        "approval_effect": approval_effect, "attention_points": attention,
        "decision_options": options, "review_mode": "DECISION_ONLY", "review_mode_label": "方向确认",
    }


def build_presentation(candidate_type: str, statement: str, proposed_change: dict,
                       evidence_refs: list, trigger_type: str = "", analysis_author: str = "") -> Optional[dict]:
    """Backward-compatible entry point used by older callers."""
    return build_candidate_presentation({
        "candidate_type": candidate_type, "raw_excerpt": statement,
        "proposed_change": proposed_change, "source_refs": evidence_refs,
        "analysis_model": analysis_author, "trigger_type": trigger_type,
    })
