#!/usr/bin/python3
"""GPT_CLEAR_AUTO_WRITE_V1 standing policy.

Deterministic allowlist only. This module makes no semantic judgment.
It checks Brain state, operation allowlist, committer schema, evidence,
visual-pending, and idempotency identity fields. Fail closed.
"""
from __future__ import annotations

import sys
from typing import Any, Mapping

GATEWAY = "/opt/paw-mcp-gateway"
if GATEWAY not in sys.path:
    sys.path.insert(0, GATEWAY)

from app.patrol_discovery_review import validate_evidence_gate
import committer as _committer

POLICY_ID = "GPT_CLEAR_AUTO_WRITE_V1"
POLICY_REVIEWER = "gpt_brain_policy_engine"
POLICY_REVIEW_CHANNEL = "standing_user_policy"
APPROVAL_GPT_CLEAR = "gpt_clear_auto_write_v1"
APPROVAL_OPUS_CLEAR = "opus_clear_auto_write_v1"
APPROVAL_USER_RECEIPT = "user_decision_receipt_v1"
APPROVAL_RISK_ROUTER = "risk_router_safe_auto_apply_v1"
SYSTEM_EVIDENCE_GOVERNED_WRITE_V1 = "SYSTEM_EVIDENCE_GOVERNED_WRITE_V1"

SAFE_OPERATIONS = frozenset({
    ("knowledge", "create"),
    ("knowledge", "update"),
    ("project", "update"),
    ("data", "create"),
    ("data", "revise"),
    ("knowledge_project_link", "link"),
})

DESTRUCTIVE_OPERATIONS = frozenset({
    ("todo", "complete"),
    ("todo", "archive"),
    ("todo", "restore"),
    ("todo", "update"),
    ("todo", "create"),
    ("knowledge", "archive"),
    ("knowledge_project_link", "unlink"),
    ("project", "create"),
    ("project", "delete"),
    ("data", "delete"),
})

PROJECT_UPDATE_ALLOWED_KEYS = frozenset({
    "id",
    "latest_progress_summary",
    "next_action_summary",
    "description",
    # PAW v1.2 Phase-2 fix (2026-09-19): paw_project_outcome.outcome_payload()
    # emits "description_provenance" (provenance metadata for the projected
    # project description).  paw_source_version_governance merges it into the
    # project_description_projection audit_event, and
    # test_source_version_governance asserts its presence.  This allowlist was
    # never updated, so EVERY project.update carrying a Project Outcome was
    # rejected with "project_field_not_allowlisted", which fail-closed the
    # entire Project-First closure and froze Project Outcome / Project
    # Handbook from 2026-09-07 onward.
    "description_provenance",
    # Structured Project Outcome / ProjectRelation records are written by
    # the canonical Project committer in the same ApprovedChange transaction.
    "project_outcome",
    "project_relation",
    # PAW v1.2 Phase-2 (2026-09-19): the governed Project Handbook projection
    # travels as project.update{project_handbook} and is applied by
    # committer._execute_project_handbook_update().  Without this key the
    # Handbook could never follow the Outcome in real time, so the Project
    # read model stayed frozen while formal state kept moving.
    "project_handbook",
    "knowledge_project_link",
})

KNOWLEDGE_FORBIDDEN_STATUS = frozenset({"deprecated", "archived", "retired", "deleted"})
FORBIDDEN_ACTION_MARKERS = frozenset({
    "DELETE", "ARCHIVE", "RETIRE", "MERGE", "UNLINK", "COMPLETE",
})


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _payload(raw: Any) -> dict[str, Any]:
    return dict(raw) if isinstance(raw, Mapping) else {}


def _approval_source_for_state(bundle_state: str, bundle: Mapping[str, Any] | None = None) -> str:
    if isinstance(bundle, Mapping):
        auth = bundle.get("gpt_authoritative_consolidation")
        if isinstance(auth, Mapping) and _text(auth.get("role")) == "risk_router_decision":
            return APPROVAL_RISK_ROUTER
    if bundle_state in {"POST_OPUS_CLEAR", "OPUS_REVIEWED"}:
        return APPROVAL_OPUS_CLEAR
    if bundle_state in {"USER_DECIDED"}:
        return APPROVAL_USER_RECEIPT
    return APPROVAL_GPT_CLEAR


def evaluate_write_change(
    write_change: Mapping[str, Any],
    *,
    bundle: Mapping[str, Any] | None = None,
    source_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return {allowed, reason, code, normalized}. Never infers payload fields."""
    bundle = bundle or {}
    source_state = source_state or {}
    target = _text(write_change.get("target_type")).lower()
    operation = _text(write_change.get("operation")).lower()
    payload = _payload(write_change.get("payload"))
    pair = (target, operation)

    if pair in DESTRUCTIVE_OPERATIONS or operation in {"delete", "archive", "retire", "complete", "unlink"}:
        return _deny("destructive_or_side_effect_operation", f"{target}.{operation} is never auto-written")
    if pair not in SAFE_OPERATIONS:
        return _deny("unsupported_operation", f"{target}.{operation} is not on the GPT_CLEAR_AUTO_WRITE_V1 allowlist")

    coverage = _text((source_state or {}).get("state") or bundle.get("coverage_state")).upper()
    if coverage == "VISUAL_PENDING":
        return _deny("visual_pending", "visual-dependent fact cannot auto-write while VISUAL_PENDING")
    if coverage == "FAILED":
        return _deny("failed_source", "FAILED SourceVersion cannot auto-write")

    action_marker = _text(write_change.get("proposal_action") or payload.get("action")).upper()
    if action_marker in FORBIDDEN_ACTION_MARKERS:
        return _deny("forbidden_proposal_action", f"proposal action {action_marker} cannot auto-write")

    if target == "project":
        extra = set(payload) - PROJECT_UPDATE_ALLOWED_KEYS
        if extra:
            return _deny("project_field_not_allowlisted", f"project.update keys not allowlisted: {sorted(extra)}")
        if not _text(payload.get("id")):
            return _deny("schema", "project.update requires payload.id")
        if not any(_text(payload.get(k)) for k in ("latest_progress_summary", "next_action_summary", "description")) and not isinstance(payload.get("project_outcome"), Mapping) and not isinstance(payload.get("project_relation"), Mapping) and not isinstance(payload.get("knowledge_project_link"), Mapping) and not isinstance(payload.get("project_handbook"), Mapping):
            return _deny("schema", "project.update requires a factual context/timeline/progress field")
        outcome = payload.get("project_outcome")
        if outcome is not None:
            if not isinstance(outcome, Mapping) or _text(outcome.get("project_id")) != _text(payload.get("id")):
                return _deny("identity_conflict", "project_outcome must target the existing Project")
            if not _text(outcome.get("executive_summary")) or not _text(outcome.get("input_watermark")):
                return _deny("schema", "project_outcome requires executive_summary and input_watermark")
        relation = payload.get("project_relation")
        if relation is not None:
            if not isinstance(relation, Mapping) or _text(relation.get("from_project_id")) != _text(payload.get("id")):
                return _deny("identity_conflict", "project_relation must originate from the existing Project")
            if not _text(relation.get("to_project_id")) or not _text(relation.get("relation_type")) or not _text(relation.get("summary")):
                return _deny("schema", "project_relation is incomplete")
        project_link = payload.get("knowledge_project_link")
        if project_link is not None:
            if not isinstance(project_link, Mapping) or _text(project_link.get("project_id")) != _text(payload.get("id")) or not _text(project_link.get("knowledge_id")):
                return _deny("identity_conflict", "knowledge_project_link must target the existing Project")
        # PAW v1.2 Phase-2 (2026-09-19): the Project Handbook is a structured,
        # machine-owned projection carried by the same project.update envelope
        # as project_outcome / project_relation / knowledge_project_link, so it
        # is validated the same way rather than being treated as free text.
        handbook = payload.get("project_handbook")
        if handbook is not None:
            if not isinstance(handbook, Mapping) or _text(handbook.get("project_id")) != _text(payload.get("id")):
                return _deny("identity_conflict", "project_handbook must target the existing Project")
            if not _text(handbook.get("input_watermark")) or not isinstance(handbook.get("sections"), Mapping):
                return _deny("schema", "project_handbook requires input_watermark and sections")

    if target == "knowledge" and operation == "update":
        if _text(payload.get("status")).lower() in KNOWLEDGE_FORBIDDEN_STATUS:
            return _deny("destructive_or_side_effect_operation", "knowledge status retire/archive cannot auto-write")
        if not _text(payload.get("id")):
            return _deny("schema", "knowledge.update requires payload.id")

    if target == "knowledge" and operation == "create":
        if not _text(payload.get("title")) or not _text(payload.get("slug")):
            return _deny("schema", "knowledge.create requires title and slug")

    if target == "data" and operation == "revise":
        if not _text(payload.get("metric_definition_id")):
            return _deny("schema", "data.revise requires metric_definition_id")
        value = payload.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return _deny("data_metric_gate", "data.revise value must be numeric")

    if target == "data" and operation == "create":
        if not _text(payload.get("name")):
            return _deny("schema", "data.create requires name")

    if target == "knowledge_project_link":
        if not _text(payload.get("knowledge_id")) or not _text(payload.get("project_id")):
            return _deny("schema", "knowledge_project_link.link requires knowledge_id and project_id")

    evidence = write_change.get("evidence_refs")
    if not isinstance(evidence, list) or not evidence:
        evidence = bundle.get("evidence_refs") if isinstance(bundle.get("evidence_refs"), list) else []
    gate = validate_evidence_gate(evidence if isinstance(evidence, list) else [])
    if not gate.allowed:
        source_refs = write_change.get("source_refs") if isinstance(write_change.get("source_refs"), list) else []
        locators = write_change.get("evidence_locators") if isinstance(write_change.get("evidence_locators"), list) else []
        if not source_refs:
            source_refs = bundle.get("source_refs") if isinstance(bundle.get("source_refs"), list) else []
        if source_refs and locators:
            gate = validate_evidence_gate(source_version_ids=source_refs, evidence_locators=locators)
    if not gate.allowed:
        return _deny("insufficient_evidence", gate.reason)

    try:
        _committer.validate_change_request(target, operation, payload, write_change.get("source_refs"))
    except ValueError as exc:
        return _deny("schema", str(exc))

    expected_version = write_change.get("expected_target_version")
    target_id = _text(payload.get("id"))
    if expected_version and not target_id:
        return _deny("identity_conflict", "expected_target_version requires payload.id")

    normalized = {
        "target_type": target,
        "operation": operation,
        "payload": dict(payload),
        "source_refs": list(write_change.get("source_refs") or bundle.get("source_refs") or []),
        "evidence_refs": [dict(x) if isinstance(x, Mapping) else x for x in (evidence or [])],
        "proposal_id": _text(write_change.get("proposal_id")) or None,
        "expected_target_version": expected_version,
        "policy_id": POLICY_ID,
    }
    return {
        "allowed": True,
        "reason": "GPT_CLEAR_AUTO_WRITE_V1 allowlist passed",
        "code": "allowed",
        "normalized": normalized,
        "policy_id": POLICY_ID,
        "approval_source": _approval_source_for_state(_text(bundle.get("state")), bundle),
    }


def evaluate_write_changes(
    write_changes: list[Any] | None,
    *,
    bundle: Mapping[str, Any] | None = None,
    source_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fail closed if any change is unsafe. Empty list is allowed (no auto-write)."""
    if write_changes is None:
        write_changes = []
    if not isinstance(write_changes, list):
        return {"allowed": False, "reason": "write_changes must be a list", "code": "schema", "items": []}
    items: list[dict[str, Any]] = []
    for index, raw in enumerate(write_changes):
        if not isinstance(raw, Mapping):
            return {"allowed": False, "reason": f"write_changes[{index}] must be an object", "code": "schema", "items": items}
        result = evaluate_write_change(raw, bundle=bundle, source_state=source_state)
        items.append(result)
        if not result.get("allowed"):
            return {
                "allowed": False,
                "reason": result.get("reason"),
                "code": result.get("code"),
                "items": items,
                "failed_index": index,
            }
    return {"allowed": True, "reason": "all write_changes passed GPT_CLEAR_AUTO_WRITE_V1", "code": "allowed", "items": items}


def _deny(code: str, reason: str) -> dict[str, Any]:
    return {"allowed": False, "reason": reason, "code": code, "normalized": None, "policy_id": POLICY_ID}
