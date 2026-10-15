#!/usr/bin/env python3
"""Project Semantic Coherence Gate for GPT Patrol bundles.

This gate never authors business summaries. It only accepts or rejects a
model-authored Patrol Semantic Integration Bundle against current Todo,
Explainer, Handbook, evidence, and temporal-state claims.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from paw_patrol_contract import (
    BOUNDARY_OPERATIONS,
    FORMAL_STATE_BYPASS,
    PROJECT_BOUNDARY_RECOMMENDATION,
    TEMPORAL_CONFLICT,
    TEMPORAL_CURRENT,
    TEMPORAL_STATES,
    TEMPORAL_SUPERSEDED,
    TEMPORAL_UNKNOWN,
    WEAK_EVIDENCE_HOLD,
    _list,
    _mapping,
    _text,
)

GATE_NAME = "project-semantic-coherence-gate/1"
CLAIM_EXPLICIT = "explicit fact"
CLAIM_STRONG = "strong inference"
CLAIM_WEAK = "weak inference"


def _lower(value: Any) -> str:
    return _text(value).lower()


def _todo_done(status: str) -> bool:
    return _lower(status) in {"done", "completed", "closed", "resolved"}


def _todo_open(status: str) -> bool:
    return _lower(status) in {"open", "todo", "in_progress", "pending", "blocked", "waiting"}


def _refs(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    refs = [dict(x) for x in _list(item.get("evidence_refs")) if isinstance(x, Mapping)]
    locator = _text(item.get("evidence_locator"))
    if locator and not refs:
        refs.append({"evidence_locator": locator, "source_version_id": _text(item.get("source_version_id"))})
    return refs


def _has_provenance(item: Mapping[str, Any]) -> bool:
    for ref in _refs(item):
        if _text(ref.get("evidence_locator")) and (_text(ref.get("source_version_id")) or _text(ref.get("capture_id")) or _text(ref.get("source_ref"))):
            return True
        if _text(ref.get("evidence_locator")):
            return True
    return False


def _temporal(item: Mapping[str, Any]) -> str:
    value = _text(item.get("temporal_state") or item.get("state")).upper()
    if not value:
        if item.get("superseded") is True:
            return TEMPORAL_SUPERSEDED
        if item.get("current") is True:
            return TEMPORAL_CURRENT
        if item.get("conflict") is True:
            return TEMPORAL_CONFLICT
        return TEMPORAL_UNKNOWN
    return value


def _explainer_fields(explainer: Mapping[str, Any] | None) -> dict[str, str]:
    data = _mapping(explainer)
    nested = _mapping(data.get("explainer")) if not data.get("next_step") else data
    return {
        "one_sentence": _text(nested.get("one_sentence") or data.get("one_sentence")),
        "current_state": _text(nested.get("current_state") or data.get("current_state")),
        "key_open_issue": _text(nested.get("key_open_issue") or data.get("key_open_issue")),
        "next_step": _text(nested.get("next_step") or data.get("next_step")),
    }


def _handbook_next_step(handbook: Mapping[str, Any] | None) -> str:
    sections = _mapping(handbook)
    if "sections" in sections:
        sections = _mapping(sections.get("sections"))
    explainer = _mapping(sections.get("project_explainer"))
    if _text(explainer.get("next_step")):
        return _text(explainer.get("next_step"))
    current = _mapping(sections.get("current_stage") or sections.get("project_overview"))
    for key in ("next_step", "next_steps", "current_next_step"):
        value = current.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, Mapping) and _text(value.get("value") or value.get("text")):
            return _text(value.get("value") or value.get("text"))
    return ""


def _boundary_ops(bundle: Mapping[str, Any]) -> list[str]:
    found: list[str] = []
    for key in (
        "knowledge_delta", "todo_delta", "project_semantic_delta",
        "project_explainer_delta", "project_handbook_delta", "write_plan",
    ):
        for raw in _list(bundle.get(key)):
            item = _mapping(raw)
            op = _lower(item.get("operation") or item.get("target_operation"))
            target = _lower(item.get("target_type"))
            if op in BOUNDARY_OPERATIONS or item.get("project_boundary_change") is True:
                found.append(op or "project_boundary_change")
            if target == "project" and op in {"create", "merge", "archive", "rename"}:
                found.append(f"{target}.{op}")
            payload = _mapping(item.get("payload"))
            if payload.get("create_project") is True or item.get("creates_project") is True:
                found.append("create_project")
    return found


def _false_causality(
    *,
    todos: Iterable[Mapping[str, Any]],
    explainer: Mapping[str, str],
    bundle: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    next_step = explainer.get("next_step") or ""
    current_state = explainer.get("current_state") or ""
    for todo in todos:
        title = _text(todo.get("title") or todo.get("id"))
        status = _text(todo.get("status"))
        if not title:
            continue
        if _todo_open(status) and title and title in current_state and any(token in current_state for token in ("已完成", "完成了", "done", "closed")):
            errors.append("false_causality:open_todo_claimed_complete:" + title)
        claimed_done = any(
            _todo_done(_text(_mapping(delta).get("status") or _mapping(_mapping(delta).get("payload")).get("status")))
            for delta in _list(bundle.get("todo_delta"))
            if _text(_mapping(delta).get("id") or _mapping(_mapping(delta).get("payload")).get("id")) == _text(todo.get("id"))
        )
        if claimed_done:
            delta = next(
                (_mapping(x) for x in _list(bundle.get("todo_delta")) if _text(_mapping(x).get("id") or _mapping(_mapping(x).get("payload")).get("id")) == _text(todo.get("id"))),
                {},
            )
            if not _has_provenance(delta) and not _has_provenance(_mapping(delta.get("payload"))):
                errors.append("false_causality:todo_done_without_evidence:" + _text(todo.get("id")))
        if _todo_done(status) and title and title in next_step:
            errors.append("todo_status_disagrees_with_project_next_step:" + title)
    for delta in _list(bundle.get("todo_delta")):
        item = _mapping(delta)
        payload = _mapping(item.get("payload")) or item
        if _todo_open(_text(payload.get("status"))) and _text(payload.get("title")) and _text(payload.get("title")) in current_state:
            if "已完成" in current_state or "完成了" in current_state:
                errors.append("false_causality:delta_open_todo_claimed_complete")
    return errors


def validate_coherence(
    *,
    bundle: Mapping[str, Any],
    todos: Iterable[Mapping[str, Any]] | None = None,
    explainer: Mapping[str, Any] | None = None,
    handbook: Mapping[str, Any] | None = None,
    evidence: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    holds: list[str] = []
    recommendations: list[str] = []
    expl = _explainer_fields(explainer)
    handbook_next = _handbook_next_step(handbook)
    if expl.get("next_step") and handbook_next and expl["next_step"] != handbook_next:
        # Strong disagreement only when both authored strings exist and conflict.
        if expl["next_step"] not in handbook_next and handbook_next not in expl["next_step"]:
            errors.append("explainer_disagrees_with_handbook_next_step")
    todo_rows = [dict(x) for x in (todos or []) if isinstance(x, Mapping)]
    errors.extend(_false_causality(todos=todo_rows, explainer=expl, bundle=bundle))
    for todo in todo_rows:
        title = _text(todo.get("title"))
        if _todo_open(_text(todo.get("status"))) and expl.get("next_step") and title and title in expl["next_step"] and any(tok in expl["next_step"] for tok in ("已完成", "关闭", "done")):
            errors.append("todo_status_disagrees_with_project_next_step")

    seen_current: dict[str, str] = {}
    for collection in (evidence or [], _list(bundle.get("evidence_delta")), _list(bundle.get("project_semantic_delta"))):
        for raw in collection:
            item = _mapping(raw)
            temporal = _temporal(item)
            if temporal and temporal not in TEMPORAL_STATES:
                errors.append("invalid_temporal_state:" + temporal)
            key = _text(item.get("source_version_id") or item.get("id") or item.get("logical_source"))
            if temporal == TEMPORAL_CURRENT and key:
                prior = seen_current.get(key)
                if prior == TEMPORAL_SUPERSEDED:
                    errors.append("temporal_inconsistent_current_and_superseded:" + key)
                seen_current[key] = TEMPORAL_CURRENT
            if temporal == TEMPORAL_SUPERSEDED and key and seen_current.get(key) == TEMPORAL_CURRENT:
                errors.append("temporal_inconsistent_current_and_superseded:" + key)
            if temporal == TEMPORAL_CONFLICT and item.get("current") is True and item.get("superseded") is True:
                errors.append("temporal_conflict_marked_current_and_superseded")
            strength = _lower(item.get("claim_strength") or item.get("inference_level"))
            if strength in {CLAIM_WEAK, "weak", "weak_inference"} and not _has_provenance(item):
                holds.append(WEAK_EVIDENCE_HOLD)
            if strength in {CLAIM_EXPLICIT, CLAIM_STRONG, "explicit", "explicit fact", "strong inference", "high"} and not _has_provenance(item):
                errors.append("evidence_provenance_missing")
            if _list(bundle.get("evidence_delta")) and not _has_provenance(item) and _text(item.get("id") or item.get("summary")):
                if strength not in {CLAIM_WEAK, "weak", "weak_inference", ""}:
                    errors.append("evidence_provenance_missing")

    for key in ("project_explainer_delta", "project_handbook_delta", "project_semantic_delta", "knowledge_delta", "todo_delta"):
        for raw in _list(bundle.get(key)):
            item = _mapping(raw)
            strength = _lower(item.get("claim_strength") or item.get("inference_level"))
            if strength in {CLAIM_EXPLICIT, CLAIM_STRONG, "explicit", "high"} and not _has_provenance(item) and not _has_provenance(_mapping(item.get("payload"))):
                errors.append("evidence_provenance_missing:" + key)
            if strength in {CLAIM_WEAK, "weak", "weak_inference"} and not _has_provenance(item):
                holds.append(WEAK_EVIDENCE_HOLD)

    boundary = _boundary_ops(bundle)
    if boundary:
        recommendations.extend(boundary)
        errors.append(PROJECT_BOUNDARY_RECOMMENDATION)

    if _mapping(bundle.get("provenance")).get("python_generated_summary") is True:
        errors.append("python_generated_summary_forbidden")

    unique_holds = list(dict.fromkeys(holds))
    unique_errors = list(dict.fromkeys(errors))
    passed = not unique_errors and WEAK_EVIDENCE_HOLD not in unique_holds
    return {
        "gate": GATE_NAME,
        "passed": passed,
        "errors": unique_errors,
        "holds": unique_holds,
        "project_boundary_recommendations": list(dict.fromkeys(recommendations)),
        "formal_state_bypass": FORMAL_STATE_BYPASS,
        "automatic_project_boundary_changes": False,
    }
