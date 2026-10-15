#!/usr/bin/env python3
"""Patrol-bound integration contracts.

Frozen operating rule:
  RAW_INGEST_IMMEDIATE=YES
  SEMANTIC_PROCESSING_ON_INGEST=NO
  TODO_UPDATE_ON_INGEST=NO
  KNOWLEDGE_UPDATE_ON_INGEST=NO
  PROJECT_UPDATE_ON_INGEST=NO

Mirror files and Quick Capture enter a durable input batch only. Semantic
Todo / Knowledge / Project writes happen on the next GPT Patrol, never at
save time and never immediately after a user receipt.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping

from gpt_brain_common import utc_now

BATCH_SCHEMA = "paw-patrol-input-batch/1"
BUNDLE_SCHEMA = "paw-patrol-semantic-integration-bundle/1"
RECEIPT_SCHEMA = "paw-patrol-user-decision-receipt/1"
ACTOR = "paw-gpt-patrol"
FORMAL_STATE_BYPASS = 0

RAW_INGEST_IMMEDIATE = "YES"
SEMANTIC_PROCESSING_ON_INGEST = "NO"
TODO_UPDATE_ON_INGEST = "NO"
KNOWLEDGE_UPDATE_ON_INGEST = "NO"
PROJECT_UPDATE_ON_INGEST = "NO"

KIND_SOURCE = "source"
KIND_CAPTURE = "capture"
KIND_DECISION_RECEIPT = "decision_receipt"

STATUS_OPEN = "OPEN"
STATUS_FROZEN = "FROZEN"
STATUS_CONSUMED = "CONSUMED"
STATUS_PARTIAL_APPLY = "PARTIAL_APPLY"
STATUS_APPLIED = "APPLIED"
STATUS_HELD = "HELD"

USER_DECISION_REQUIRED = "USER_DECISION_REQUIRED"
USER_DECISION_RECEIVED_WAITING_NEXT_PATROL = "USER_DECISION_RECEIVED_WAITING_NEXT_PATROL"
QUEUED_FOR_PATROL = "QUEUED_FOR_PATROL"
PATROL_READY = "PATROL_READY"
WEAK_EVIDENCE_HOLD = "WEAK_EVIDENCE_HOLD"
PROJECT_BOUNDARY_RECOMMENDATION = "PROJECT_BOUNDARY_RECOMMENDATION"

TEMPORAL_CURRENT = "CURRENT"
TEMPORAL_SUPERSEDED = "SUPERSEDED"
TEMPORAL_CONFLICT = "CONFLICT"
TEMPORAL_UNKNOWN = "UNKNOWN"
TEMPORAL_STATES = frozenset({TEMPORAL_CURRENT, TEMPORAL_SUPERSEDED, TEMPORAL_CONFLICT, TEMPORAL_UNKNOWN})

DELTA_KEYS = (
    "knowledge_delta",
    "todo_delta",
    "project_semantic_delta",
    "project_explainer_delta",
    "project_handbook_delta",
    "evidence_delta",
    "risk_conflict_delta",
    "unresolved_items",
    "provenance",
)

BOUNDARY_OPERATIONS = frozenset({
    "create_project",
    "rename_project",
    "merge_project",
    "archive_project",
    "split_project",
    "change_project_boundary",
})


class PatrolContractError(ValueError):
    """Fail-closed contract violation."""


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def operating_rules() -> dict[str, str]:
    return {
        "RAW_INGEST_IMMEDIATE": RAW_INGEST_IMMEDIATE,
        "SEMANTIC_PROCESSING_ON_INGEST": SEMANTIC_PROCESSING_ON_INGEST,
        "TODO_UPDATE_ON_INGEST": TODO_UPDATE_ON_INGEST,
        "KNOWLEDGE_UPDATE_ON_INGEST": KNOWLEDGE_UPDATE_ON_INGEST,
        "PROJECT_UPDATE_ON_INGEST": PROJECT_UPDATE_ON_INGEST,
        "IMMEDIATE_EXECUTION_AFTER_USER_REVIEW": "NO",
        "FULL_SOURCE_RESCAN": "NO",
        "FULL_KNOWLEDGE_RESCAN": "NO",
        "GPT_PATROL_AUTHORITATIVE_OWNER": "YES",
        "FORMAL_STATE_BYPASS": str(FORMAL_STATE_BYPASS),
    }


def blank_batch(*, batch_id: str, created_at: str | None = None) -> dict[str, Any]:
    now = created_at or utc_now()
    return {
        "schema": BATCH_SCHEMA,
        "batch_id": _text(batch_id),
        "status": STATUS_OPEN,
        "cutoff_at": None,
        "frozen": False,
        "created_at": now,
        "updated_at": now,
        "inputs": [],
        "decision_receipts": [],
        "semantic_processing": False,
        "todo_written": False,
        "knowledge_written": False,
        "project_written": False,
        "formal_state_bypass": FORMAL_STATE_BYPASS,
        "full_source_rescan": False,
        "full_knowledge_rescan": False,
        "operating_rules": operating_rules(),
    }


def input_ref(
    *,
    kind: str,
    ref_id: str,
    ingested_at: str | None = None,
    payload_ref: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    kind = _text(kind)
    if kind not in {KIND_SOURCE, KIND_CAPTURE, KIND_DECISION_RECEIPT}:
        raise PatrolContractError("unsupported_input_kind:" + kind)
    ref_id = _text(ref_id)
    if not ref_id:
        raise PatrolContractError("input_ref_id_required")
    item = {
        "kind": kind,
        "ref_id": ref_id,
        "ingested_at": ingested_at or utc_now(),
        "payload_ref": _text(payload_ref) or None,
        "semantic_processed": False,
        "formal_state_bypass": FORMAL_STATE_BYPASS,
    }
    if extra:
        item["extra"] = dict(extra)
    return item


def freeze_batch(batch: Mapping[str, Any], *, cutoff_at: str | None = None) -> dict[str, Any]:
    data = dict(batch)
    if data.get("frozen") is True and _text(data.get("cutoff_at")):
        if cutoff_at and _text(cutoff_at) != _text(data.get("cutoff_at")):
            raise PatrolContractError("patrol_cutoff_immutable")
        return data
    cutoff = _text(cutoff_at) or utc_now()
    data["cutoff_at"] = cutoff
    data["frozen"] = True
    data["status"] = STATUS_FROZEN
    data["updated_at"] = utc_now()
    data["semantic_processing"] = False
    data["formal_state_bypass"] = FORMAL_STATE_BYPASS
    return data


def validate_batch(batch: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if _text(batch.get("schema")) != BATCH_SCHEMA:
        errors.append("batch_schema_mismatch")
    if not _text(batch.get("batch_id")):
        errors.append("batch_id_required")
    if batch.get("formal_state_bypass") not in (0, "0", None):
        errors.append("formal_state_bypass_must_be_zero")
    if batch.get("semantic_processing") is True:
        errors.append("batch_must_not_semantically_process")
    if batch.get("todo_written") or batch.get("knowledge_written") or batch.get("project_written"):
        errors.append("batch_must_not_write_formal_state")
    if batch.get("full_source_rescan") or batch.get("full_knowledge_rescan"):
        errors.append("full_rescan_forbidden")
    seen: set[tuple[str, str]] = set()
    for raw in _list(batch.get("inputs")):
        item = _mapping(raw)
        key = (_text(item.get("kind")), _text(item.get("ref_id")))
        if not key[0] or not key[1]:
            errors.append("input_ref_incomplete")
            continue
        if key in seen:
            errors.append("duplicate_input_ref:" + key[0] + ":" + key[1])
        seen.add(key)
        if item.get("semantic_processed") is True:
            errors.append("input_must_remain_raw:" + key[1])
    if batch.get("frozen") is True and not _text(batch.get("cutoff_at")):
        errors.append("frozen_batch_requires_cutoff")
    return {"passed": not errors, "errors": errors, "formal_state_bypass": FORMAL_STATE_BYPASS}


def blank_bundle(
    *,
    patrol_run_id: str,
    batch_id: str,
    authored_by: str = ACTOR,
) -> dict[str, Any]:
    now = utc_now()
    return {
        "schema": BUNDLE_SCHEMA,
        "patrol_run_id": _text(patrol_run_id),
        "batch_id": _text(batch_id),
        "authored_by": _text(authored_by) or ACTOR,
        "authored_at": now,
        "knowledge_delta": [],
        "todo_delta": [],
        "project_semantic_delta": [],
        "project_explainer_delta": [],
        "project_handbook_delta": [],
        "evidence_delta": [],
        "risk_conflict_delta": [],
        "unresolved_items": [],
        "provenance": {
            "authoritative_owner": "GPT_PATROL",
            "formal_state_bypass": FORMAL_STATE_BYPASS,
            "python_generated_summary": False,
            "stale_write_plan": False,
        },
        "write_plan": [],
        "write_plan_fingerprint": None,
        "stale_write_plan_fingerprints": [],
        "project_boundary_recommendations": [],
        "apply_journal": [],
        "formal_state_bypass": FORMAL_STATE_BYPASS,
    }


def _delta_item_errors(name: str, items: Iterable[Any]) -> list[str]:
    errors: list[str] = []
    for index, raw in enumerate(items):
        item = _mapping(raw)
        if not item:
            errors.append(f"{name}[{index}]:empty")
            continue
        if name != "unresolved_items" and not _list(item.get("evidence_refs")) and not _text(item.get("evidence_locator")):
            if _text(item.get("claim_strength")) in {"explicit fact", "EXPLICIT", "STRONG_INFERENCE"}:
                errors.append(f"{name}[{index}]:evidence_required")
        op = _text(item.get("operation") or item.get("target_operation")).lower()
        if op in BOUNDARY_OPERATIONS or item.get("project_boundary_change") is True or item.get("creates_project") is True:
            # Recommendations may appear on the bundle; apply() refuses them.
            continue
        temporal = _text(item.get("temporal_state") or item.get("state")).upper()
        if temporal and temporal not in TEMPORAL_STATES:
            errors.append(f"{name}[{index}]:invalid_temporal_state")
    return errors


def validate_bundle(bundle: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if _text(bundle.get("schema")) != BUNDLE_SCHEMA:
        errors.append("bundle_schema_mismatch")
    if not _text(bundle.get("patrol_run_id")):
        errors.append("patrol_run_id_required")
    if not _text(bundle.get("batch_id")):
        errors.append("batch_id_required")
    owner = _text(bundle.get("authored_by"))
    if owner and owner not in {ACTOR, "gpt_brain_patrol", "GPT_PATROL", "chatgpt-workspace-agent"}:
        errors.append("bundle_must_be_gpt_patrol_authored")
    if bundle.get("formal_state_bypass") not in (0, "0", None):
        errors.append("formal_state_bypass_must_be_zero")
    provenance = _mapping(bundle.get("provenance"))
    if provenance.get("python_generated_summary") is True:
        errors.append("python_generated_summary_forbidden")
    if provenance.get("stale_write_plan") is True:
        errors.append("stale_write_plan_forbidden")
    for key in DELTA_KEYS:
        if key == "provenance":
            continue
        errors.extend(_delta_item_errors(key, _list(bundle.get(key))))
    if _text(bundle.get("write_plan_fingerprint")):
        expected = fingerprint(bundle.get("write_plan") or [])
        if _text(bundle.get("write_plan_fingerprint")) != expected:
            errors.append("write_plan_fingerprint_mismatch")
    stale = {_text(x) for x in _list(bundle.get("stale_write_plan_fingerprints")) if _text(x)}
    current = _text(bundle.get("write_plan_fingerprint")) or fingerprint(bundle.get("write_plan") or [])
    if current and current in stale:
        errors.append("stale_write_plan_replay")
    return {"passed": not errors, "errors": errors, "formal_state_bypass": FORMAL_STATE_BYPASS}


def mark_write_plan_stale(item: dict[str, Any]) -> dict[str, Any]:
    plans = [dict(x) for x in _list(item.get("write_plan")) if isinstance(x, Mapping)]
    fp = fingerprint(plans) if plans else None
    item["stale_write_plan"] = plans
    item["stale_write_plan_fingerprint"] = fp
    item["write_plan_stale"] = True
    item["user_decision_hold"] = USER_DECISION_RECEIVED_WAITING_NEXT_PATROL
    item["next_action"] = "WAIT_NEXT_GPT_PATROL"
    return item


def is_stale_write_plan(item: Mapping[str, Any], write_plan: Iterable[Any] | None = None) -> bool:
    plans = [dict(x) for x in _list(write_plan if write_plan is not None else item.get("write_plan")) if isinstance(x, Mapping)]
    fp = fingerprint(plans) if plans else ""
    stale = _text(item.get("stale_write_plan_fingerprint"))
    if not fp or not stale:
        return bool(item.get("write_plan_stale") and plans and fingerprint(plans) == fingerprint(item.get("stale_write_plan") or []))
    return fp == stale


def user_decision_receipt(
    *,
    receipt_id: str,
    subject_kind: str,
    subject_id: str,
    decision: str,
    original_input_ref: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": RECEIPT_SCHEMA,
        "receipt_id": _text(receipt_id),
        "subject_kind": _text(subject_kind),
        "subject_id": _text(subject_id),
        "decision": _text(decision).upper(),
        "original_input_ref": _text(original_input_ref) or _text(subject_id),
        "note": _text(note) or None,
        "status": USER_DECISION_RECEIVED_WAITING_NEXT_PATROL,
        "immediate_execution": False,
        "recorded_at": utc_now(),
        "formal_state_bypass": FORMAL_STATE_BYPASS,
    }


def journal_entry(*, op_id: str, target_type: str, status: str = "PENDING", detail: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "op_id": _text(op_id),
        "target_type": _text(target_type),
        "status": _text(status).upper() or "PENDING",
        "detail": dict(detail or {}),
        "updated_at": utc_now(),
    }
