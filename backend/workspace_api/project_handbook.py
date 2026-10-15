"""Project Handbook outcome layer.

This module is intentionally deterministic.  It projects the already persisted
Project Outcome, Project Relation, Knowledge refs, evidence refs, metrics,
timeline, decisions, risks and canonical Todos into a business-facing Project
Handbook.  It does not read Source content and it never invokes Luna.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .db import DB_PATH, _ensure_project_handbook_schema

HANDBOOK_SCHEMA_VERSION = "paw-project-handbook/1"
GENERATOR = "project-handbook-governor"
SEMANTIC_HANDBOOK_MARKERS = (
    "why_and_background", "historical_evolution", "current_effective_rules",
    "risks_blockers_ambiguities",
)

SECTION_FIELDS: dict[str, tuple[str, ...]] = {
    "project_overview": (
        "project_name", "project_background", "business_purpose", "current_stage",
        "project_owner", "scope", "non_scope",
    ),
    "project_subject": (
        "core_objectives", "key_workstreams", "main_entities", "operating_model",
        "important_rules", "current_effective_methodology",
    ),
    "historical_trajectory": (
        "historical_timeline", "milestones", "historical_metrics", "key_changes",
        "previous_decisions", "source_evidence_references",
    ),
    "current_state": (
        "current_progress", "completed_items", "active_items", "current_metrics",
        "current_risks", "blockers", "dependencies",
    ),
    "future_plan": (
        "next_actions", "planned_milestones", "recommended_next_steps",
        "pending_decisions", "required_resources",
    ),
    "blockers": (
        "evidence_conflicts", "unresolved_questions", "terra_pending", "opus_pending",
        "user_decisions", "dependencies_blocking_progress",
    ),
    "todos": ("todo_refs", "owners", "priority", "due_date", "status"),
}

CHANGE_SECTIONS: dict[str, tuple[str, ...]] = {
    "source": ("project_overview", "historical_trajectory", "current_state"),
    "knowledge": ("project_overview", "project_subject", "current_state"),
    "data": ("historical_trajectory", "current_state"),
    "metric": ("historical_trajectory", "current_state"),
    "timeline": ("historical_trajectory", "current_state", "future_plan"),
    "decision": ("project_subject", "historical_trajectory", "current_state", "future_plan"),
    "rule": ("project_subject", "current_state"),
    "todo": ("future_plan", "todos", "current_state"),
    "conflict": ("blockers", "current_state"),
    "risk": ("current_state", "blockers"),
    "relation": ("current_state", "blockers"),
}

# These are canonical Projects in the registry, not automatically-created
# Handbook subjects.  Personal tasks are intentionally outside the business
# dossier; the archived WPS reconstruction Project is an evidence/container
# record and is never an active Handbook.
NON_BUSINESS_PROJECT_NAMES = frozenset({"个人生活"})
EVIDENCE_CONTAINER_NAME_MARKERS = ("WPS→Personal Workspace", "WPS->Personal Workspace")


def is_archived_evidence_project(project: Mapping[str, Any]) -> bool:
    name = str(project.get("name") if hasattr(project, "get") else project["name"] or "")
    return any(marker in name for marker in EVIDENCE_CONTAINER_NAME_MARKERS)


def is_active_business_project(project: Mapping[str, Any]) -> bool:
    return (
        str(project.get("status") or "").lower() == "active"
        and str(project.get("name") or "") not in NON_BUSINESS_PROJECT_NAMES
        and not is_archived_evidence_project(project)
    )


def handbook_is_semantic_artifact(sections: Mapping[str, Any] | None) -> bool:
    """True when a Handbook was authored as a semantic artifact, not field stitcher output."""
    if not isinstance(sections, Mapping):
        return False
    if "why_and_background" in sections and "historical_evolution" in sections:
        return True
    rules = sections.get("current_effective_rules")
    return (
        isinstance(rules, list)
        and bool(rules)
        and isinstance(rules[0], Mapping)
        and "state" in rules[0]
        and "text" in rules[0]
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _unique_refs(refs: Iterable[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in refs:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        key = _canonical(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _source_refs(evidence_refs: Iterable[Mapping[str, Any]]) -> list[str]:
    values: list[str] = []
    for ref in evidence_refs:
        for key in ("source_version_id", "source_id", "source_ref"):
            value = ref.get(key)
            if value and str(value) not in values:
                values.append(str(value))
    return values


def _fact(value: Any, evidence_refs: list[dict[str, Any]], source_refs: list[str], *, status: str | None = None) -> dict[str, Any]:
    present = value not in (None, "", [], {})
    return {
        "value": value,
        "evidence_refs": evidence_refs if present else [],
        "source_refs": source_refs if present else [],
        "evidence_status": status or ("evidenced" if present and evidence_refs else "evidence_insufficient"),
    }


def _mapped_facts(value: Any, section_refs: list[dict[str, Any]], source_refs: list[str]) -> Any:
    """Preserve existing machine facts while making their provenance explicit."""
    if not value:
        return _fact([], [], [], status="evidence_insufficient")
    if isinstance(value, list):
        rows = []
        for item in value:
            if isinstance(item, Mapping):
                row = dict(item)
                # Keep item-level provenance when the semantic layer supplied
                # it.  Section-level refs already live once at the section
                # root; copying a large ref list onto every metric/topic
                # caused oversized Handbook rows and obscured the read plane.
                refs = _unique_refs(item.get("evidence_refs") or [])
                row.setdefault("evidence_refs", refs)
                row.setdefault("source_refs", _source_refs(refs))
                row.setdefault("evidence_status", "evidenced" if refs else "section_evidence")
                rows.append(row)
            else:
                rows.append(_fact(item, [], [], status="section_evidence"))
        return rows
    if isinstance(value, Mapping):
        return {str(k): _mapped_facts(v, section_refs, source_refs) if isinstance(v, (list, dict)) else v for k, v in value.items()}
    return _fact(value, section_refs, source_refs)


def _item_text(item: Any) -> str:
    """Render persisted semantic material as short readable dossier prose."""
    if isinstance(item, Mapping):
        for key in ("summary", "text", "description", "title", "name", "decision", "question", "metric"):
            value = item.get(key)
            if value not in (None, "", [], {}):
                if key == "metric" and item.get("value") is not None:
                    unit = f" {item.get('unit')}" if item.get("unit") else ""
                    period = f"（{item.get('period')}）" if item.get("period") else ""
                    return f"{value}={item.get('value')}{unit}{period}"
                return str(value).strip()
        return ""
    return str(item).strip() if item not in (None, "") else ""


def _readable_items(value: Any, *, limit: int = 8) -> list[str]:
    if isinstance(value, Mapping):
        value = [value]
    if not isinstance(value, list):
        value = [value] if value not in (None, "") else []
    result: list[str] = []
    for item in value:
        text = _item_text(item)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _join_sentences(parts: Iterable[str], *, insufficient: str) -> str:
    values = [str(part).strip() for part in parts if str(part).strip()]
    return "；".join(values) if values else insufficient


def _meaningful_count(value: Any) -> int:
    """Count business payload, excluding provenance and empty placeholders."""
    if value in (None, "", [], {}):
        return 0
    if isinstance(value, Mapping):
        if "value" in value and set(value).intersection({"evidence_refs", "source_refs", "evidence_status"}):
            return _meaningful_count(value.get("value"))
        return sum(_meaningful_count(v) for k, v in value.items() if k not in {"evidence_refs", "source_refs", "_meta"})
    if isinstance(value, list):
        return sum(_meaningful_count(item) for item in value)
    return 1


def _section(fields: Mapping[str, Any], refs: list[dict[str, Any]], sources: list[str], *, revision: int, generated_at: str, confidence: str) -> dict[str, Any]:
    quality = "PASS" if refs else "PASS_EVIDENCE_INSUFFICIENT"
    out = dict(fields)
    # These are duplicated at the section root for simple API consumers; the
    # same values are also available under _meta for schema-aware clients.
    out.update({
        "evidence_refs": refs,
        "source_refs": sources,
        "generated_at": generated_at,
        "revision": revision,
        "confidence": confidence,
        "quality_gate": quality,
        "_meta": {
            "evidence_refs": refs,
            "source_refs": sources,
            "generated_at": generated_at,
            "revision": revision,
            "confidence": confidence,
            "quality_gate": quality,
        },
    })
    return out


def _load_context(conn: sqlite3.Connection, project_id: str) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]] | None:
    project = conn.execute("SELECT * FROM project WHERE id=?", (project_id,)).fetchone()
    outcome = conn.execute("SELECT * FROM project_outcome WHERE project_id=?", (project_id,)).fetchone()
    if project is None or outcome is None:
        return None
    todos = [dict(r) for r in conn.execute("SELECT id,title,description,status,priority,due_at,project_id,updated_at FROM todo WHERE project_id=? ORDER BY created_at,id", (project_id,)).fetchall()]
    # P1: the default Current Handbook reads only temporal_state='current'
    # relations.  superseded / tombstoned rows stay in the database and remain
    # readable through history-aware readers - they are never deleted.
    relations = [dict(r) for r in conn.execute("SELECT * FROM project_relation WHERE (from_project_id=? OR to_project_id=?) AND temporal_state='current' ORDER BY relation_id", (project_id, project_id)).fetchall()]
    return dict(project), dict(outcome), todos, relations


def _build_sections(project: Mapping[str, Any], outcome: Mapping[str, Any], todos: list[dict[str, Any]], relations: list[dict[str, Any]], *, revision: int, generated_at: str) -> tuple[dict[str, Any], dict[str, Any]]:
    refs = _unique_refs(_json(outcome.get("source_evidence_refs_json"), []))
    sources = _source_refs(refs)
    knowledge = _json(outcome.get("canonical_knowledge_refs_json"), [])
    unresolved = _json(outcome.get("unresolved_items_json"), {})
    progress = _json(outcome.get("latest_progress_json"), [])
    milestones = _json(outcome.get("completed_milestones_json"), [])
    open_items = _json(outcome.get("open_items_json"), [])
    decisions = _json(outcome.get("key_decisions_json"), [])
    rules = _json(outcome.get("current_effective_rules_json"), [])
    risks = _json(outcome.get("risks_json"), [])
    blockers = _json(outcome.get("blockers_json"), [])
    ambiguities = _json(outcome.get("ambiguities_json"), [])
    metrics = _json(outcome.get("key_metrics_json"), [])
    scope = _json(outcome.get("scope_json"), {})
    scope_map = scope if isinstance(scope, Mapping) else {}
    objectives = _json(outcome.get("objectives_json"), [])
    related = _json(outcome.get("related_projects_json"), [])
    # A Handbook may be grounded by canonical Knowledge even when a source
    # locator is not available in the outcome.  Source refs remain empty in
    # that case; we never manufacture them.  A title/count-only payload still
    # fails the quality gate below.
    semantic_payload = [outcome.get("executive_summary"), objectives, scope, progress,
                        milestones, open_items, decisions, rules, risks, blockers,
                        ambiguities, metrics, knowledge]
    evidence_sufficient = bool(refs or knowledge) and _meaningful_count(semantic_payload) > 1
    confidence = "HIGH" if refs else ("MEDIUM" if knowledge else "LOW")
    insufficient = not evidence_sufficient

    def f(value: Any, *, local_refs: list[dict[str, Any]] | None = None) -> Any:
        return _mapped_facts(value, local_refs or refs, sources)

    active_todos = [t for t in todos if t.get("status") not in {"done", "cancelled", "completed", "archived"}]
    completed_todos = [t for t in todos if t.get("status") in {"done", "completed"}]
    todo_refs = [{"todo_id": t["id"], "title": t.get("title"), "evidence_status": "canonical_todo"} for t in todos]
    relation_refs = [{
        "relation_id": r.get("relation_id"),
        "project_id": r.get("to_project_id") if r.get("from_project_id") == project.get("id") else r.get("from_project_id"),
        "relation_type": r.get("relation_type"),
        "summary": r.get("summary"),
        "evidence_refs": _json(r.get("evidence_refs_json"), []),
    } for r in relations]

    overview_summary = outcome.get("executive_summary") or project.get("description")
    if insufficient:
        overview_summary = "证据不足：当前没有已持久化的语义/知识证据形成项目背景综合。"
    body_summary = _join_sentences(
        [
            *([f"目标：{'、'.join(_readable_items(objectives))}"] if _readable_items(objectives) else []),
            *([f"范围：{_item_text(scope)}"] if scope else []),
            *([f"已记录知识主题：{'、'.join(_readable_items(knowledge))}"] if _readable_items(knowledge) else []),
            *([f"当前有效规则：{'、'.join(_readable_items(rules))}"] if _readable_items(rules) else []),
        ],
        insufficient="证据不足：尚未记录足够的项目目标、范围或运作方式。",
    )
    historical_summary = _join_sentences(
        [
            *([f"进展：{'；'.join(_readable_items(progress))}"] if _readable_items(progress) else []),
            *([f"里程碑：{'；'.join(_readable_items(milestones))}"] if _readable_items(milestones) else []),
            *([f"历史决策：{'；'.join(_readable_items(decisions))}"] if _readable_items(decisions) else []),
            *([f"历史数据/信号：已记录 {len(metrics)} 项指标。"] if metrics else []),
        ],
        insufficient="证据不足：尚未记录可复述的历史轨迹、里程碑或历史数据。",
    )
    current_summary = _join_sentences(
        [
            *([f"阶段：{outcome.get('phase') or project.get('stage')}"] if outcome.get("phase") or project.get("stage") else []),
            *([f"最新进展：{'；'.join(_readable_items(progress))}"] if _readable_items(progress) else []),
            *([f"当前有效规则：{'；'.join(_readable_items(rules))}"] if _readable_items(rules) else []),
            *([f"当前指标：已记录 {len(metrics)} 项，保留原始期间/单位/定义。"] if metrics else []),
        ],
        insufficient="证据不足：当前状态、有效规则与指标尚未形成可核验综合。",
    )
    blocker_summary = _join_sentences(
        [
            *([f"阻塞项：{'；'.join(_readable_items(blockers))}"] if _readable_items(blockers) else []),
            *([f"风险：{'；'.join(_readable_items(risks))}"] if _readable_items(risks) else []),
            *([f"未决歧义：{'；'.join(_readable_items(ambiguities))}"] if _readable_items(ambiguities) else []),
        ],
        insufficient="当前未在已持久化输入中记录 blocker、风险或未决歧义；不代表不存在。",
    )
    future_summary = _join_sentences(
        [
            *([f"下一步：{'；'.join(_readable_items(open_items))}"] if _readable_items(open_items) else []),
            *([f"待办：{'；'.join(_readable_items(active_todos))}"] if active_todos else []),
            *([f"待决策：{'；'.join(_readable_items(decisions))}"] if _readable_items(decisions) else []),
        ],
        insufficient="证据不足：尚未记录可核验的未来行动或计划里程碑。",
    )

    # Do not invent business purpose, owners, decisions, milestones or
    # resources.  Missing evidence is represented explicitly and remains empty.
    sections = {
        "project_overview": _section({
            "section_summary": _fact(overview_summary, refs, sources, status="evidence_insufficient" if insufficient else None),
            "project_name": _fact(project.get("name"), refs, sources, status="evidenced" if refs else "canonical_project_metadata"),
            "project_background": f(outcome.get("executive_summary")) if refs and outcome.get("executive_summary") else _fact(None, [], [], status="evidence_insufficient"),
            "business_purpose": f(scope_map.get("purpose") or scope_map.get("business_purpose")),
            "current_stage": f(outcome.get("phase") or project.get("stage")),
            "project_owner": _fact(None, [], [], status="evidence_insufficient"),
            "scope": f(scope),
            "non_scope": f(scope_map.get("non_scope")),
        }, refs, sources, revision=revision, generated_at=generated_at, confidence=confidence),
        "project_subject": _section({
            "section_summary": _fact(body_summary, refs, sources, status="evidence_insufficient" if insufficient else None),
            "core_objectives": f(objectives),
            "key_workstreams": f(knowledge),
            "main_entities": f(related),
            "operating_model": f(scope_map.get("operating_model")),
            "important_rules": f(rules),
            "current_effective_methodology": f(knowledge),
        }, refs, sources, revision=revision, generated_at=generated_at, confidence=confidence),
        "historical_trajectory": _section({
            "section_summary": _fact(historical_summary, refs, sources, status="evidence_insufficient" if insufficient else None),
            "historical_timeline": f(progress),
            "milestones": f(milestones),
            "historical_metrics": f(metrics),
            "key_changes": f(progress),
            "previous_decisions": f(decisions),
            "source_evidence_references": _fact(refs, refs, sources, status="evidenced" if refs else "evidence_insufficient"),
        }, refs, sources, revision=revision, generated_at=generated_at, confidence=confidence),
        "current_state": _section({
            "section_summary": _fact(current_summary, refs, sources, status="evidence_insufficient" if insufficient else None),
            "status": f(outcome.get("current_status") or project.get("status")),
            "phase": f(outcome.get("phase") or project.get("stage")),
            "current_progress": f(progress),
            "completed_items": f([*milestones, *completed_todos]),
            "active_items": f([*open_items, *active_todos]),
            "current_metrics": f(metrics),
            "current_effective_rules": f(rules),
            "current_indicators": f(metrics),
            "current_risks": f(risks),
            "blockers": f(blockers),
            "dependencies": f(relation_refs),
        }, refs, sources, revision=revision, generated_at=generated_at, confidence=confidence),
        "future_plan": _section({
            "section_summary": _fact(future_summary, refs, sources, status="evidence_insufficient" if insufficient else None),
            "next_actions": f([*open_items, *active_todos]),
            "planned_milestones": f([]),
            "recommended_next_steps": f([]),
            "pending_decisions": f(decisions),
            "required_resources": _fact(None, [], [], status="evidence_insufficient"),
        }, refs, sources, revision=revision, generated_at=generated_at, confidence=confidence),
        "blockers": _section({
            "section_summary": _fact(blocker_summary, refs, sources, status="evidence_insufficient" if insufficient else None),
            "risks": f(risks),
            "evidence_conflicts": f([x for x in [*blockers, *ambiguities] if x]),
            "unresolved_questions": f(ambiguities),
            "terra_pending": f(unresolved.get("terra", []) if isinstance(unresolved, Mapping) else []),
            "opus_pending": f(unresolved.get("opus", []) if isinstance(unresolved, Mapping) else []),
            "user_decisions": f(unresolved.get("user", []) if isinstance(unresolved, Mapping) else []),
            "dependencies_blocking_progress": f(relation_refs),
        }, refs, sources, revision=revision, generated_at=generated_at, confidence=confidence),
        "todos": _section({
            "section_summary": _fact(
                "；".join(f"{t.get('title')}（{t.get('status')}）" for t in todos if t.get("title"))
                if todos else "没有已持久化的项目 Todo。",
                refs, sources, status="canonical_todo" if todos else "evidence_insufficient",
            ),
            "todo_refs": _fact(todo_refs, refs, sources, status="canonical_todo" if todo_refs else "evidence_insufficient"),
            "owners": _fact(None, [], [], status="evidence_insufficient"),
            "priority": _fact([{"todo_id": t["id"], "priority": t.get("priority")} for t in todos if t.get("priority")], refs, sources),
            "due_date": _fact([{"todo_id": t["id"], "due_at": t.get("due_at")} for t in todos if t.get("due_at")], refs, sources),
            "status": _fact([{"todo_id": t["id"], "status": t.get("status")} for t in todos], refs, sources, status="canonical_todo" if todos else "evidence_insufficient"),
        }, refs, sources, revision=revision, generated_at=generated_at, confidence=confidence),
    }
    meta = {
        "schema_version": HANDBOOK_SCHEMA_VERSION,
        "project_id": project.get("id"),
        "evidence_sufficient": evidence_sufficient,
        "evidence_refs_count": len(refs),
        "source_refs_count": len(sources),
        "knowledge_refs_count": len(knowledge),
        "relation_refs_count": len(relations),
        "todo_refs_count": len(todo_refs),
        "source_knowledge_project_causality": "Source -> Knowledge -> Project Handbook",
        "full_corpus_reread": False,
        "luna_invoked": False,
        "empty_template": False,
        "title_only_summary": False,
        "material_business_decisions_added_by_ai": False,
        "evidence_insufficient": insufficient,
        "project_status": project.get("status"),
        "active_business_project": is_active_business_project(project),
        "canonical_knowledge_refs": knowledge,
        "source_evidence_refs": refs,
    }
    return sections, meta


def _assemble_handbook(sections: Mapping[str, Any], meta: Mapping[str, Any]) -> dict[str, Any]:
    """Expose the frozen section names plus the Handbook contract aliases."""
    out = dict(sections)
    subject = dict(sections.get("project_subject") or {})
    subject.setdefault("objectives", subject.get("core_objectives"))
    subject.setdefault("scope", sections.get("project_overview", {}).get("scope"))
    subject.setdefault("operating_model", subject.get("operating_model"))
    subject.setdefault("key_workstreams", subject.get("key_workstreams"))
    out["project_body"] = subject

    history = dict(sections.get("historical_trajectory") or {})
    history.setdefault("historical_decisions", history.get("previous_decisions"))
    history.setdefault("historical_data_signals", history.get("historical_metrics"))
    out["historical_trajectory"] = history

    current = dict(sections.get("current_state") or {})
    current.setdefault("current_effective_rules", current.get("current_effective_rules"))
    current.setdefault("current_indicators", current.get("current_indicators"))
    out["current_state"] = current

    blockers = dict(sections.get("blockers") or {})
    blockers.setdefault("risks", current.get("current_risks"))
    out["blockers_and_risks"] = blockers
    out["forward_plan"] = dict(sections.get("future_plan") or {})
    out["evidence_and_knowledge"] = {
        "knowledge_refs": _fact(meta.get("canonical_knowledge_refs", []), [], [], status="canonical_knowledge" if meta.get("canonical_knowledge_refs") else "evidence_insufficient"),
        "source_evidence_refs": _fact(meta.get("source_evidence_refs", []), meta.get("source_evidence_refs", []), _source_refs(meta.get("source_evidence_refs", [])), status="evidenced" if meta.get("source_evidence_refs") else "evidence_insufficient"),
        "traceability": "Source -> Knowledge -> Project Outcome -> Project Handbook",
    }
    out["metadata"] = dict(meta)
    out["_meta"] = dict(meta)
    return out


def validate_handbook(handbook: Mapping[str, Any], *, project_name: str | None = None) -> dict[str, Any]:
    errors: list[str] = []
    for section_name, fields in SECTION_FIELDS.items():
        section = handbook.get(section_name)
        if not isinstance(section, Mapping):
            errors.append(f"missing_section:{section_name}")
            continue
        for field in fields:
            if field not in section:
                errors.append(f"missing_field:{section_name}.{field}")
        for key in ("evidence_refs", "source_refs", "generated_at", "revision", "confidence", "quality_gate"):
            if key not in section:
                errors.append(f"missing_metadata:{section_name}.{key}")

    overview = handbook.get("project_overview") or {}
    name_value = ((overview.get("project_name") or {}).get("value") if isinstance(overview.get("project_name"), Mapping) else overview.get("project_name"))
    business_values = 0
    for section_name, section in handbook.items():
        if section_name not in SECTION_FIELDS or not isinstance(section, Mapping):
            continue
        for field in SECTION_FIELDS[section_name]:
            value = section.get(field)
            if not isinstance(value, Mapping):
                continue
            fact_value = value.get("value")
            if fact_value in (None, "", [], {}):
                continue
            if field != "project_name":
                business_values += _meaningful_count(fact_value)
            if fact_value == name_value and field not in {"project_name"}:
                errors.append(f"title_only_summary:{section_name}.{field}")
            if not value.get("evidence_refs") and value.get("evidence_status") not in {"evidence_insufficient", "canonical_project_metadata", "canonical_todo"}:
                errors.append(f"untraceable_fact:{section_name}.{field}")
    if name_value and business_values == 0:
        errors.append("title_only_summary:handbook")
    if not handbook.get("_meta", {}).get("empty_template", False) and not handbook:
        errors.append("empty_template")
    return {"passed": not errors, "errors": errors}


def _input_payload(project: Mapping[str, Any], outcome: Mapping[str, Any], todos: list[dict[str, Any]], relations: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "project_id": project.get("id"),
        "project_name": project.get("name"),
        "project": {key: project.get(key) for key in (
            "description", "status", "stage", "priority", "due_date",
            "next_action_summary", "latest_progress_summary", "updated_at",
        )},
        "outcome": {key: value for key, value in outcome.items() if key.endswith("_json") or key in {"input_watermark", "evidence_fingerprint", "synthesis_revision", "quality_gate"}},
        "todos": todos,
        "relations": relations,
    }


def _ensure_local_schema(conn: sqlite3.Connection) -> None:
    _ensure_project_handbook_schema(conn)


def _governed_write(conn: sqlite3.Connection, *, project_id: str, handbook: dict[str, Any], meta: dict[str, Any], revision: int, watermark: str, baseline_hash: str, event_id: str | None, idempotency_key: str, changed_sections: list[str]) -> dict[str, Any]:
    existing = conn.execute("SELECT * FROM project_handbook WHERE project_id=?", (project_id,)).fetchone()
    if existing is not None and existing["input_watermark"] == watermark:
        return {"status": "IDEMPOTENT", "idempotent": True, "formal_state_bypass": 0, "revision": existing["revision"]}
    project = conn.execute("SELECT updated_at FROM project WHERE id=?", (project_id,)).fetchone()
    if project is None:
        raise ValueError(f"Project not found: {project_id}")

    # The existing Committer accepts project.update and owns the durable
    # transaction.  The nested projection is an additive read-plane payload;
    # it does not update the Project row.
    payload = {
        "id": project_id,
        "project_handbook": {
            "project_id": project_id,
            "schema_version": HANDBOOK_SCHEMA_VERSION,
            "sections": handbook,
            "section_metadata": meta,
            "generated_at": meta.get("generated_at") or _now(),
            "revision": revision,
            "input_watermark": watermark,
            "evidence_fingerprint": str(meta.get("evidence_fingerprint") or ""),
            "quality_gate": str(meta.get("quality_gate") or "PASS"),
            "evidence_sufficient": bool(meta.get("evidence_sufficient")),
            "baseline_input_hash": baseline_hash,
        },
    }
    try:
        gateway = "/opt/paw-mcp-gateway"
        if gateway not in sys.path:
            sys.path.insert(0, gateway)
        import committer

        previous_db = committer.DB_PATH
        committer.DB_PATH = str(conn.execute("PRAGMA database_list").fetchone()[2])
        try:
            prior = conn.execute("SELECT * FROM approved_change WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if prior is not None and prior["status"] == "committed":
                committed = committer.get_commit_record(prior["change_id"])
                if committed and committed.get("result") == "success" and existing is not None and existing["baseline_input_hash"] == baseline_hash:
                    return {"status": "IDEMPOTENT", "idempotent": True, "formal_state_bypass": 0, "revision": existing["revision"], "change_id": prior["change_id"], "commit_id": committed["id"]}

            # Candidate is an explicit derived-projection candidate.  It is
            # not business content and is never sent to a semantic model.
            candidate_id = _ensure_handbook_candidate(
                conn, project_id=project_id, payload=payload,
                source_refs=list(meta.get("source_refs") or []), idempotency_key=idempotency_key,
            )
            conn.commit()
            rd_id, change_id = committer.create_review_decision_atomic(
                candidate_id=candidate_id,
                decision="approve",
                reviewer=GENERATOR,
                review_channel="derived_handbook_projection",
                approval_source="candidate_review",
                original_payload=payload,
                reviewed_payload=payload,
                review_notes="Deterministic projection of already persisted Project Outcome/Knowledge/evidence/Todo inputs; no semantic judgment.",
                target_type="project",
                operation="update",
                source_refs=list(meta.get("source_refs") or []),
                expected_target_version=project[0],
                create_approved_change=True,
                idempotency_key=idempotency_key,
            )
            committed = committer.commit_approved_change(change_id)
            if committed.get("result") != "success":
                raise ValueError(committed.get("error") or "Handbook Committer failed")
            return {
                "status": "COMMITTED", "idempotent": False, "formal_state_bypass": 0,
                "revision": revision, "candidate_id": candidate_id,
                "review_decision_id": rd_id, "change_id": change_id,
                "commit_id": committed.get("commit_id"),
            }
        finally:
            committer.DB_PATH = previous_db
    except Exception:
        conn.rollback()
        raise


def _ensure_handbook_candidate(
    conn: sqlite3.Connection, *, project_id: str, payload: Mapping[str, Any],
    source_refs: list[str], idempotency_key: str,
) -> str:
    """Create/reuse the governance candidate without touching business state."""
    candidate_id = "handbook-candidate:" + _hash(idempotency_key)[:24]
    try:
        existing = conn.execute("SELECT id FROM candidate WHERE id=?", (candidate_id,)).fetchone()
    except sqlite3.OperationalError:
        raise ValueError("Handbook governance requires the canonical Candidate table")
    if existing is not None:
        return candidate_id
    now = _now()
    run_id = "handbook-run:" + _hash({"project_id": project_id})[:24]
    conn.execute(
        "INSERT OR IGNORE INTO analysis_run(id,trigger_type,raw_text,raw_input_refs,analysis_model,started_at,completed_at,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (run_id, "project_handbook_projection", None, _canonical(source_refs), GENERATOR, now, now, now),
    )
    conn.execute(
        """INSERT INTO candidate
           (id,analysis_run_id,candidate_type,source_refs,raw_excerpt,proposed_change,
            confidence,inference_level,recommended_action,analysis_model,status,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            candidate_id, run_id, "project_update", _canonical(source_refs),
            f"Project Handbook projection for {project_id}", _canonical(payload),
            "HIGH", "EXPLICIT", "AUTO_WRITE", GENERATOR, "pending", now,
        ),
    )
    return candidate_id


def read_project_handbook(project_id: str, *, db_path: str | None = None) -> dict[str, Any] | None:
    conn = sqlite3.connect(db_path or str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        _ensure_local_schema(conn)
        project = conn.execute("SELECT status,name FROM project WHERE id=?", (project_id,)).fetchone()
        if project is None:
            return None
        row = conn.execute("SELECT * FROM project_handbook WHERE project_id=?", (project_id,)).fetchone()
        if row is None:
            return None
        handbook = _json(row["sections_json"], {})
        meta = _json(row["section_metadata_json"], {})
        return {"project_handbook": handbook, "handbook_metadata": meta, "handbook_revision": row["revision"], "handbook_generated_at": row["generated_at"], "handbook_quality_gate": row["quality_gate"], "handbook_input_watermark": row["input_watermark"], "formal_state_bypass": 0}
    finally:
        conn.close()


def lightweight_explainer_projection(explainer: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return the four list-card fields from an authored explainer. No synthesis."""
    if not isinstance(explainer, Mapping):
        return None
    fields = {
        "one_sentence": explainer.get("one_sentence") if isinstance(explainer.get("one_sentence"), str) else None,
        "current_state": explainer.get("current_state") if isinstance(explainer.get("current_state"), str) else None,
        "key_open_issue": explainer.get("key_open_issue") if isinstance(explainer.get("key_open_issue"), str) else None,
        "next_step": explainer.get("next_step") if isinstance(explainer.get("next_step"), str) else None,
    }
    if not any(isinstance(value, str) and value.strip() for value in fields.values()):
        return None
    return fields


def read_project_explainer(project_id: str, *, db_path: str | None = None) -> dict[str, Any] | None:
    """Read the Level-1 Project Explainer.

    Authority: analysis-plane semantic artifact authored into the Project
    Handbook ``project_explainer`` section (or an optional sidecar table).
    This is not Formal State.  Absence means the explainer is not ready;
    callers must not stitch other Project fields as a substitute.
    """
    conn = sqlite3.connect(db_path or str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        from backend.pm.storage import derived
        drafts = derived(conn, project_id)
        if drafts:
            return {'explainer': {k: 'AI 生成：' + v for k, v in drafts[0]['content'].items()},
                    'updated_at': drafts[0]['created_at'], 'authority': 'ai_generated',
                    'formal_state': False, 'source': 'pm_ai_explainer', 'label': 'AI 生成'}
        handbook = conn.execute(
            "SELECT sections_json, updated_at FROM project_handbook WHERE project_id=?",
            (project_id,),
        ).fetchone()
        if handbook is not None:
            sections = _json(handbook["sections_json"], {})
            explainer = sections.get("project_explainer") if isinstance(sections, dict) else None
            if isinstance(explainer, dict) and explainer:
                return {
                    "explainer": explainer,
                    "updated_at": handbook["updated_at"],
                    "authority": explainer.get("authority") or "analysis_plane_semantic_artifact",
                    "formal_state": False,
                    "source": "project_handbook.sections.project_explainer",
                }
        try:
            row = conn.execute("SELECT * FROM project_explainer WHERE project_id=?", (project_id,)).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table: project_explainer" not in str(exc).lower():
                raise
            row = None
        if row is None:
            return None
        return {
            "explainer": _json(row["explainer_json"], {}),
            "updated_at": row["updated_at"],
            "authority": "analysis_plane_semantic_artifact",
            "formal_state": False,
            "source": "project_explainer",
        }
    finally:
        conn.close()


def synthesize_project_handbook(project_id: str, *, db_path: str | None = None, changed_sections: Iterable[str] | None = None, change_type: str = "baseline", event_id: str | None = None, input_watermark: str | None = None) -> dict[str, Any]:
    conn = sqlite3.connect(db_path or str(DB_PATH), timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_local_schema(conn)
        context = _load_context(conn, project_id)
        if context is None:
            raise ValueError(f"Project or Project Outcome not found: {project_id}")
        project, outcome, todos, relations = context
        prior = conn.execute("SELECT * FROM project_handbook WHERE project_id=?", (project_id,)).fetchone()
        if prior is not None and handbook_is_semantic_artifact(_json(prior["sections_json"], {})):
            return {
                "status": "PRESERVED_SEMANTIC_HANDBOOK",
                "idempotent": True,
                "formal_state_bypass": 0,
                "revision": prior["revision"],
                "project_id": project_id,
                "reason": "Deterministic field projector must not overwrite a model-authored semantic handbook.",
            }
        requested = [s for s in (changed_sections or []) if s in SECTION_FIELDS]
        generated_at = _now()
        revision = int(prior["revision"]) + 1 if prior else 1
        sections, meta = _build_sections(project, outcome, todos, relations, revision=revision, generated_at=generated_at)
        if prior is not None and requested:
            old_sections = _json(prior["sections_json"], {})
            sections = {**old_sections, **{name: sections[name] for name in requested}}
            # Incremental synthesis must not rewrite untouched sections.
            for name in SECTION_FIELDS:
                if name not in requested and name in old_sections:
                    sections[name] = old_sections[name]
        # Include every already-persisted input in the fingerprint.  An event
        # watermark is an upstream hint, not permission to hide a Todo,
        # Relation or Knowledge change behind a reused watermark.
        input_payload = _input_payload(project, outcome, todos, relations)
        watermark = _hash({"upstream": input_watermark, "inputs": input_payload})
        baseline_hash = _hash({"watermark": watermark, "sections": sections, "change_type": change_type})
        meta["source_input_watermark"] = input_watermark
        handbook = _assemble_handbook(sections, meta)
        check = validate_handbook(handbook, project_name=str(project.get("name") or ""))
        if not check["passed"]:
            raise ValueError("Project Handbook quality gate failed: " + "; ".join(check["errors"]))
        meta.update({
            "generated_at": generated_at, "revision": revision,
            "input_watermark": watermark,
            "evidence_fingerprint": outcome.get("evidence_fingerprint"),
            "quality_gate": "PASS" if meta.get("evidence_sufficient") else "PASS_EVIDENCE_INSUFFICIENT",
            "source_refs": _source_refs(_unique_refs(_json(outcome.get("source_evidence_refs_json"), []))),
            "changed_sections": requested or list(SECTION_FIELDS), "change_type": change_type,
        })
        # Metadata is updated after the input fingerprint is known; aliases
        # retain section objects so old clients and the additive contract see
        # one coherent revision.
        handbook = _assemble_handbook(sections, meta)
        idem = f"project-handbook:{project_id}:{watermark}:{_hash(requested or list(SECTION_FIELDS))[:16]}"
        result = _governed_write(conn, project_id=project_id, handbook=handbook, meta=meta, revision=revision, watermark=watermark, baseline_hash=baseline_hash, event_id=event_id, idempotency_key=idem, changed_sections=requested)
        conn.commit()
        result.update({"project_id": project_id, "project_name": project.get("name"), "quality_gate": meta["quality_gate"], "handbook": handbook, "handbook_metadata": meta})
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def patrol_project_change(*, project_id: str, change_type: str, event_id: str, db_path: str | None = None, input_watermark: str | None = None) -> dict[str, Any]:
    """Apply one Source/Knowledge/Data change to the direct and related Projects."""
    conn = sqlite3.connect(db_path or str(DB_PATH), timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_local_schema(conn)
        existing = conn.execute("SELECT * FROM project_handbook_event WHERE event_id=?", (event_id,)).fetchone()
        if existing is not None:
            return {"status": "IDEMPOTENT", "event_id": event_id, "impacted_projects": _json(existing["impacted_projects_json"], []), "formal_state_bypass": 0}
        # P1: Patrol must not spread to related Projects through superseded /
        # tombstoned relations.
        relations = conn.execute("SELECT from_project_id,to_project_id FROM project_relation WHERE (from_project_id=? OR to_project_id=?) AND temporal_state='current'", (project_id, project_id)).fetchall()
        impacted = [project_id]
        for row in relations:
            other = row["to_project_id"] if row["from_project_id"] == project_id else row["from_project_id"]
            other_project = conn.execute("SELECT id,name,status FROM project WHERE id=?", (other,)).fetchone()
            if other_project is not None and is_active_business_project(dict(other_project)) and other not in impacted:
                impacted.append(other)
        direct = conn.execute("SELECT id,name,status FROM project WHERE id=?", (project_id,)).fetchone()
        if direct is None or not is_active_business_project(dict(direct)):
            return {"status": "SKIPPED_INELIGIBLE_PROJECT", "event_id": event_id, "impacted_projects": [], "formal_state_bypass": 0}
        sections = list(CHANGE_SECTIONS.get(change_type, ("current_state",)))
        results = []
        for pid in impacted:
            result = synthesize_project_handbook(pid, db_path=db_path, changed_sections=sections, change_type=change_type, event_id=None, input_watermark=input_watermark)
            results.append({"project_id": pid, "status": result["status"], "revision": result.get("revision")})
        now = _now()
        idem = f"project-handbook-patrol:{event_id}"
        conn.execute("INSERT INTO project_handbook_event(event_id,idempotency_key,project_id,change_type,changed_sections_json,input_watermark,impacted_projects_json,status,result_json,processed_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (event_id, idem, project_id, change_type, _canonical(sections), input_watermark, _canonical(impacted), "COMMITTED", _canonical({"results": results}), now))
        conn.commit()
        return {"status": "COMMITTED", "event_id": event_id, "impacted_projects": impacted, "changed_sections": sections, "results": results, "formal_state_bypass": 0}
    finally:
        conn.close()


def baseline_all(*, db_path: str | None = None) -> dict[str, Any]:
    conn = sqlite3.connect(db_path or str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        _ensure_local_schema(conn)
        project_rows = [dict(row) for row in conn.execute("SELECT id,name,status FROM project ORDER BY id").fetchall()]
        project_ids = [row["id"] for row in project_rows if is_active_business_project(row)]
    finally:
        conn.close()
    results = [synthesize_project_handbook(pid, db_path=db_path) for pid in project_ids]
    excluded = [row for row in project_rows if row["id"] not in project_ids]
    return {"generated": sum(r.get("status") == "COMMITTED" for r in results), "idempotent": sum(r.get("status") == "IDEMPOTENT" for r in results), "total": len(results), "results": results, "active_business_projects": project_ids, "excluded_projects": excluded, "formal_state_bypass": 0, "full_corpus_reread": False, "luna_invoked": False}


__all__ = [
    "CHANGE_SECTIONS", "HANDBOOK_SCHEMA_VERSION", "SECTION_FIELDS", "baseline_all",
    "handbook_is_semantic_artifact", "lightweight_explainer_projection",
    "patrol_project_change", "read_project_explainer", "read_project_handbook",
    "synthesize_project_handbook",
    "validate_handbook",
]
