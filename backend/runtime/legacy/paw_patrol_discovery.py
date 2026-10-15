#!/usr/bin/env python3
"""Authoritative GPT Patrol work discovery / bounded claim.

Scheduled ChatGPT GPT Agent Patrol is the semantic actor. This module never
analyzes Capture text, never sets processed_at, and never writes Formal State.

discover_patrol_work is read-only. claim_patrol_work freezes the OPEN mixed
Source+Capture+Receipt batch idempotently so the next GPT turn has a bounded
handoff. Generic patrol.enabled=false does not hide capture discovery when
the daily Capture lane is enabled.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from paw_patrol_contract import FORMAL_STATE_BYPASS, KIND_CAPTURE, KIND_DECISION_RECEIPT, KIND_SOURCE
from paw_patrol_orchestrator import PatrolOrchestrator
from paw_runtime_state_projection import (
    OPERATIONAL_PLANE,
    compact_preflight,
    project_runtime_state,
)

SCHEMA = "paw-patrol-discovery/1"
CLAIM_SCHEMA = "paw-patrol-claim/1"
AUTHORITATIVE_TOOL = "gpt_patrol_discover_work"
CAPTURE_READ_TOOL = "gpt_patrol_list_unprocessed_captures"
CAPTURE_DECIDE_TOOL = "gpt_patrol_submit_capture_decision"
CLAIM_TOOL = "gpt_patrol_claim_work"
POST_OPUS_TOOL = "gpt_brain_get_post_opus_work"
TERRA_LIST_TOOL = "list_incremental_terra_review"

# ── Provider-neutral canonical patrol capability names (v1.3D) ─────────────
# The canonical spellings are what every Agent consumer (ChatGPT, Gemini
# Spark, Agy, Claude) should use. The gpt_patrol_* spellings REMAIN registered
# MCP tools and are declared here as explicit aliases of the same
# implementation, so an existing ChatGPT session keeps working unchanged and
# no consumer is ever pointed at a tool that does not exist.
SEMANTIC_LIST_TOOL = "patrol_list_semantic_items"
SEMANTIC_CLAIM_TOOL = "patrol_claim_semantic_items"
SEMANTIC_SUBMIT_TOOL = "patrol_submit_semantic_decision"
SEMANTIC_LIST_TOOL_ALIAS = "gpt_patrol_list_semantic_items"
SEMANTIC_CLAIM_TOOL_ALIAS = "gpt_patrol_claim_semantic_items"
SEMANTIC_SUBMIT_TOOL_ALIAS = "gpt_patrol_submit_semantic_decision"
SEMANTIC_TOOL_ALIASES = (
    SEMANTIC_LIST_TOOL_ALIAS,
    SEMANTIC_CLAIM_TOOL_ALIAS,
    SEMANTIC_SUBMIT_TOOL_ALIAS,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _input_refs(batch: Mapping[str, Any] | None) -> dict[str, list[str]]:
    refs = {KIND_SOURCE: [], KIND_CAPTURE: [], KIND_DECISION_RECEIPT: []}
    for item in (batch or {}).get("inputs") or []:
        if not isinstance(item, Mapping):
            continue
        kind = _text(item.get("kind"))
        ref_id = _text(item.get("ref_id"))
        if kind in refs and ref_id:
            refs[kind].append(ref_id)
    return refs


def _passes(capture_n: int, terra_n: int, post_opus: int, source_n: int) -> list[str]:
    passes: list[str] = []
    if source_n or terra_n:
        passes.append("INCREMENTAL_SEMANTIC_PASS")
    if post_opus:
        passes.append("POST_OPUS_PASS")
    if capture_n:
        passes.append("CAPTURE_PASS")
    if passes:
        passes.append("PROJECT_FIRST_PASS")
    return passes


REQUIRED_ACTION_FAMILIES = (
    "SEMANTIC_REVIEW",
    "CAPTURE_REVIEW",
    "PATROL_PREFLIGHT",
)

# Declares the deterministic action families this build can emit. The remaining
# families are reserved vocabulary: they are named here so Agents can negotiate,
# but no code path produces them in v1.
RESERVED_ACTION_FAMILIES = (
    "TERRA_REVIEW",
    "POST_OPUS_REVIEW",
    "APPROVED_WRITE_EXECUTION",
)

ACTION_CONTRACT_VERSION = "paw-patrol-action-contract/1"

# Priority bands. Lower number = handled first. Bands are disjoint and
# far apart so new families can be slotted without renumbering.
_PRIORITY_SEMANTIC_REVIEW = 10
_PRIORITY_CAPTURE_REVIEW = 20
_PRIORITY_PATROL_PREFLIGHT = 90


def _required_action(
    *,
    action_type: str,
    priority: int,
    required: bool,
    pending_count: int,
    entry_tool: str,
    completion_tool: str | None,
    reason_code: str,
    owner: str = "CHATGPT_AGENT",
    aliases: tuple[str, ...] = (),
) -> dict[str, Any]:
    """One deterministic required-action record.

    ``aliases`` lists the older GPT-prefixed spellings that resolve to the
    same implementation as ``entry_tool`` / ``completion_tool``. It exists so
    a provider-neutral consumer can use the canonical name while an existing
    ChatGPT session keeps using its alias, with neither side inventing a tool.
    """
    return {
        "action_type": action_type,
        "priority": priority,
        "required": bool(required),
        "owner": owner,
        "pending_count": int(pending_count),
        "entry_tool": entry_tool,
        "completion_tool": completion_tool,
        "aliases": list(aliases),
        "reason_code": reason_code,
    }


def required_actions(
    *,
    capture_n: int = 0,
    terra_n: int = 0,
    reader_n: int = 0,
    patrol_n: int = 0,
    post_opus: int = 0,
) -> list[dict[str, Any]]:
    """Deterministic Patrol Action Contract. No model call, no state write.

    PAW Core decides WHAT must be handled and in WHAT order. The GPT Agent
    decides HOW to interpret the evidence. This function is the seam between
    the two: it reads only integer queue depths already computed by the
    runtime-state projection and returns a priority-sorted list.

    Emitted in v1:
      SEMANTIC_REVIEW    - PATROL_READY Evidence Packets awaiting GPT review.
      CAPTURE_REVIEW     - raw Captures awaiting a bounded GPT decision.
      PATROL_PREFLIGHT   - always-present anchor when any work exists.

    Not emitted in v1 (reserved vocabulary, see RESERVED_ACTION_FAMILIES):
      TERRA_REVIEW, POST_OPUS_REVIEW, APPROVED_WRITE_EXECUTION.
    """
    capture_n = _int(capture_n)
    terra_n = _int(terra_n)
    reader_n = _int(reader_n)
    patrol_n = _int(patrol_n)
    post_opus = _int(post_opus)

    actions: list[dict[str, Any]] = []
    if patrol_n > 0:
        # Provider-neutral: the canonical patrol_* names are the entries every
        # consumer should use; the gpt_patrol_* spellings are declared aliases
        # of the same implementation, so an existing ChatGPT session is
        # unaffected and no consumer is pointed at a non-existent tool.
        actions.append(_required_action(
            action_type="SEMANTIC_REVIEW",
            priority=_PRIORITY_SEMANTIC_REVIEW,
            required=True,
            pending_count=patrol_n,
            entry_tool=SEMANTIC_LIST_TOOL,
            completion_tool=SEMANTIC_SUBMIT_TOOL,
            reason_code="PATROL_READY_UNREVIEWED",
            aliases=SEMANTIC_TOOL_ALIASES,
        ))
    if capture_n > 0:
        # Capture tools have no canonical counterpart registered yet; keeping
        # the existing names here (rather than inventing one) guarantees the
        # contract never advertises a tool the surface does not expose.
        actions.append(_required_action(
            action_type="CAPTURE_REVIEW",
            priority=_PRIORITY_CAPTURE_REVIEW,
            required=True,
            pending_count=capture_n,
            entry_tool=CAPTURE_READ_TOOL,
            completion_tool=CAPTURE_DECIDE_TOOL,
            reason_code="CAPTURE_UNPROCESSED",
        ))
    # Anchor: any other kind of pending work still requires the Agent to run
    # preflight, so the contract never returns a dead end while work exists.
    if actions or terra_n > 0 or reader_n > 0 or post_opus > 0:
        actions.append(_required_action(
            action_type="PATROL_PREFLIGHT",
            priority=_PRIORITY_PATROL_PREFLIGHT,
            required=True,
            pending_count=max(terra_n, reader_n, post_opus, 0),
            entry_tool=AUTHORITATIVE_TOOL,
            completion_tool=None,
            reason_code="PREFLIGHT_ANCHOR",
        ))
    return sorted(actions, key=lambda item: (item["priority"], item["action_type"]))


def action_contract(*, required_actions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Introspectable description of the action contract."""
    actions = list(required_actions or [])
    return {
        "version": ACTION_CONTRACT_VERSION,
        "emitted_families": list(REQUIRED_ACTION_FAMILIES),
        "reserved_families": list(RESERVED_ACTION_FAMILIES),
        "priority_bands": {
            "SEMANTIC_REVIEW": _PRIORITY_SEMANTIC_REVIEW,
            "CAPTURE_REVIEW": _PRIORITY_CAPTURE_REVIEW,
            "PATROL_PREFLIGHT": _PRIORITY_PATROL_PREFLIGHT,
        },
        "decision_owner": "PAW_CORE",
        "interpretation_owner": "GPT_AGENT",
        "any_required": any(a.get("required") for a in actions),
        "n_actions": len(actions),
    }


def discover_patrol_work(
    *,
    db_path: str | None = None,
    store_path: str | Path | None = None,
    projection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read-only patrol preflight. No semantic analysis. No processed_at write."""
    kwargs: dict[str, Any] = {}
    if db_path:
        kwargs["db_path"] = db_path
    if store_path:
        kwargs["batch_path"] = store_path
    proj = dict(projection or project_runtime_state(**kwargs))
    pre = compact_preflight(proj)
    hist = proj.get("historical_bootstrap_status") or {}
    review = proj.get("historical_review_counts") or {}
    current = proj.get("current_incremental_patrol_status") or {}
    batch = current.get("open_batch") or {}
    capture_n = _int(pre.get("pending_capture_count"))
    terra_n = _int(pre.get("current_terra_pending_count"))
    reader_n = _int(pre.get("current_reader_ready_count"))
    patrol_n = _int(pre.get("current_patrol_ready_count"))
    post_opus = _int(pre.get("current_post_opus_claimable"))
    source_n = _int(batch.get("source_count"))
    next_actions: list[dict[str, Any]] = [
        {"tool": AUTHORITATIVE_TOOL, "role": "preflight", "required": True}
    ]
    if capture_n > 0:
        next_actions.append({
            "tool": CLAIM_TOOL,
            "role": "freeze_open_batch",
            "required": False,
            "note": "Idempotent freeze of mixed Source+Capture batch. Does not mark captures processed.",
        })
        next_actions.append({
            "tool": CAPTURE_READ_TOOL,
            "role": "read_raw_captures",
            "required": True,
            "pending_capture_count": capture_n,
        })
        next_actions.append({
            "tool": CAPTURE_DECIDE_TOOL,
            "role": "record_gpt_decision",
            "required": True,
            "decisions": ["NO_ACTION", "USER_DECISION_REQUIRED", "GOVERNED_WRITE"],
        })
    if reader_n > 0:
        next_actions.append({"tool": "gpt_patrol_prepare_semantic_items", "role": "incremental_semantic_prepare", "pending": reader_n})
    if patrol_n > 0:
        next_actions.append({"tool": "gpt_patrol_list_semantic_items", "role": "gpt_primary_semantic_review", "pending": patrol_n})
    if terra_n > 0:
        next_actions.append({"tool": TERRA_LIST_TOOL, "role": "incremental_terra", "pending": terra_n})
    if post_opus > 0:
        next_actions.append({"tool": POST_OPUS_TOOL, "role": "post_opus", "pending": post_opus})
    daily = bool(pre.get("daily_capture_patrol_enabled"))
    generic = bool(pre.get("generic_patrol_enabled"))
    _required = required_actions(
        capture_n=capture_n,
        terra_n=terra_n,
        reader_n=reader_n,
        patrol_n=patrol_n,
        post_opus=post_opus,
    )
    _patrol_status = "WORK_AVAILABLE" if _required else "NO_WORK"
    # Discovery has just completed. Point to executable work, never back to
    # ourselves while work exists (legacy consumers follow only this field).
    next_work = (SEMANTIC_LIST_TOOL_ALIAS if patrol_n > 0 else
                 CAPTURE_READ_TOOL if capture_n > 0 else
                 'gpt_patrol_prepare_semantic_items' if reader_n > 0 else
                 TERRA_LIST_TOOL if terra_n > 0 else
                 POST_OPUS_TOOL if post_opus > 0 else None)
    return {
        "schema": SCHEMA,
        "authoritative_tool": AUTHORITATIVE_TOOL,
        "operational_plane": proj.get("operational_plane") or OPERATIONAL_PLANE,
        "historical_bootstrap_status": {
            "lifecycle": hist.get("lifecycle"),
            "raw_status": hist.get("raw_status"),
            "coverage_complete": hist.get("coverage_complete"),
            "counts_as_current_production_work": False,
        },
        "historical_review_counts": {
            "total_bundles": review.get("total_bundles"),
            "historical_unresolved_counts": review.get("historical_unresolved_counts"),
            "lifecycle": review.get("lifecycle"),
            "counts_as_current_production_backlog": False,
        },
        "current_incremental_patrol_status": {
            "status": current.get("status"),
            "incremental_mirror_patrol_enabled": current.get("incremental_mirror_patrol_enabled"),
            "backlog": current.get("current_incremental_backlog"),
        },
        "current_incremental_backlog": proj.get("current_incremental_backlog"),
        "current_reader_ready_count": reader_n,
        "reader_ready_work_available": reader_n > 0,
        "current_patrol_ready_count": patrol_n,
        "patrol_ready_work_available": patrol_n > 0,
        "current_capture_backlog": capture_n,
        "historical_unresolved_counts": proj.get("historical_unresolved_counts"),
        "pending_capture_count": capture_n,
        "capture_work_available": capture_n > 0,
        "daily_capture_patrol_enabled": daily,
        "generic_patrol_enabled": generic,
        "generic_patrol_enabled_blocks_capture_discovery": False,
        "open_batch": {
            "batch_id": batch.get("batch_id"),
            "frozen": batch.get("frozen"),
            "source_count": source_n,
            "capture_count": _int(batch.get("capture_count")),
            "decision_receipt_count": _int(batch.get("decision_receipt_count")),
            "mixed_source_and_capture": bool(batch.get("mixed_source_and_capture")),
        },
        "passes": _passes(capture_n, terra_n, post_opus, source_n),
        "patrol_work_available": bool(pre.get("patrol_work_available")),
        "next_patrol_action": next_work,
        "next_read_action": next_work,
        "preflight_completed": True,
        "preflight_is_not_semantic_completion": True,
        "next_actions": next_actions,
        "semantic_processing": False,
        "semantic_analysis_performed": False,
        "processed_at_written": False,
        "formal_state_bypass": FORMAL_STATE_BYPASS,
        "gpt_patrol_trigger_owner": "CHATGPT_GPT_AGENT_SCHEDULED_TASK",
        "server_side_gpt_trigger": "DISABLED",
        # --- Patrol Action Contract (v1.3D, additive) --------------------
        # `next_actions` above stays byte-identical for existing Agents. The
        # fields below are the deterministic contract PAW Core publishes so the
        # Agent is no longer the one deciding whether the queue gets processed.
        "action_contract_version": ACTION_CONTRACT_VERSION,
        "patrol_status": _patrol_status,
        "required_actions": _required,
        "action_contract": action_contract(required_actions=_required),
    }


def _batch_capture_ids(batch: Mapping[str, Any] | None) -> set[str]:
    ids: set[str] = set()
    for item in (batch or {}).get("inputs") or []:
        if isinstance(item, Mapping) and _text(item.get("kind")) == KIND_CAPTURE:
            ref = _text(item.get("ref_id"))
            if ref:
                ids.add(ref)
    return ids


def _covering_frozen_batch(data: Mapping[str, Any], capture_ids: set[str]) -> dict[str, Any] | None:
    if not capture_ids:
        return None
    latest: dict[str, Any] | None = None
    for batch in (data.get("batches") or {}).values():
        if not isinstance(batch, Mapping) or not batch.get("frozen"):
            continue
        if capture_ids <= _batch_capture_ids(batch):
            latest = dict(batch)
    return latest


def claim_patrol_work(
    *,
    db_path: str | None = None,
    store_path: str | Path | None = None,
    cutoff_at: str | None = None,
) -> dict[str, Any]:
    """Freeze the OPEN patrol batch. Idempotent. No semantic analysis."""
    orch = PatrolOrchestrator(store_path=store_path, db_path=db_path)
    data = orch.load()
    pending_ids: set[str] = set()
    try:
        import sys
        kworker = (
            os.environ.get("PAW_RUNTIME_MODULE_ROOT")
)
        if not kworker:
            raise RuntimeError(
                "PAW_RUNTIME_MODULE_ROOT is required "
                "(PAW Runtime Path Contract, /etc/paw-runtime-paths.env)"
            )
        if kworker not in sys.path:
            sys.path.insert(0, kworker)
        from paw_capture_lifecycle import list_patrol_eligible_captures

        conn = sqlite3.connect(orch.db_path)
        conn.row_factory = sqlite3.Row
        try:
            pending_ids = {str(row["id"]) for row in list_patrol_eligible_captures(conn)}
        finally:
            conn.close()
    except Exception:
        pending_ids = set()
    covered = _covering_frozen_batch(data, pending_ids) if pending_ids else None
    before_id = None
    already_frozen = False
    if covered:
        frozen = covered
        status = "IDEMPOTENT"
        already_frozen = True
        before_id = _text(frozen.get("batch_id"))
    else:
        orch.merge_pending_captures()
        before = orch.current_batch() or {}
        before_id = _text(before.get("batch_id"))
        already_frozen = bool(before.get("frozen"))
        inputs = list(before.get("inputs") or [])
        if not inputs and not already_frozen:
            return {
                "schema": CLAIM_SCHEMA,
                "status": "NO_WORK",
                "batch_id": before.get("batch_id"),
                "frozen": False,
                "idempotent": True,
                "bounded": True,
                "source_ref_ids": [],
                "capture_ref_ids": [],
                "decision_receipt_ref_ids": [],
                "source_count": 0,
                "capture_count": 0,
                "mixed_source_and_capture": False,
                "queue_preserved": True,
                "semantic_processing": False,
                "processed_at_written": False,
                "formal_state_bypass": FORMAL_STATE_BYPASS,
                "next_read_action": None,
                "next_actions": [{"tool": AUTHORITATIVE_TOOL, "role": "recheck"}],
            }
        if already_frozen and before_id:
            frozen = dict(before)
            status = "IDEMPOTENT"
        else:
            frozen = orch.freeze_open_batch(cutoff_at=cutoff_at)
            status = "FROZEN"
    refs = _input_refs(frozen)
    replay = orch.get_batch(_text(frozen.get("batch_id"))) or frozen
    replay_refs = _input_refs(replay)
    return {
        "schema": CLAIM_SCHEMA,
        "status": status,
        "batch_id": frozen.get("batch_id"),
        "frozen": True,
        "idempotent": status == "IDEMPOTENT" or bool(already_frozen and before_id == frozen.get("batch_id")),
        "bounded": True,
        "source_ref_ids": refs[KIND_SOURCE],
        "capture_ref_ids": refs[KIND_CAPTURE],
        "decision_receipt_ref_ids": refs[KIND_DECISION_RECEIPT],
        "source_count": len(refs[KIND_SOURCE]),
        "capture_count": len(refs[KIND_CAPTURE]),
        "mixed_source_and_capture": bool(refs[KIND_SOURCE] and refs[KIND_CAPTURE]),
        "queue_preserved": replay_refs == refs,
        "semantic_processing": False,
        "processed_at_written": False,
        "formal_state_bypass": FORMAL_STATE_BYPASS,
        "next_read_action": CAPTURE_READ_TOOL if refs[KIND_CAPTURE] else None,
        "next_actions": [
            {"tool": CAPTURE_READ_TOOL, "pending_capture_count": len(refs[KIND_CAPTURE])}
            if refs[KIND_CAPTURE] else {"tool": AUTHORITATIVE_TOOL, "role": "recheck"}
        ],
    }


def mcp_discover_patrol_work(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    try:
        return discover_patrol_work(
            db_path=params.get("db_path"),
            store_path=params.get("store_path"),
        )
    except Exception as exc:
        return {
            "error": "patrol discovery unavailable",
            "code": "patrol_discovery_error",
            "detail": str(exc)[:300],
            "semantic_processing": False,
            "formal_state_bypass": FORMAL_STATE_BYPASS,
            "next_patrol_action": AUTHORITATIVE_TOOL,
        }


def mcp_claim_patrol_work(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    try:
        return claim_patrol_work(
            db_path=params.get("db_path"),
            store_path=params.get("store_path"),
            cutoff_at=params.get("cutoff_at"),
        )
    except Exception as exc:
        return {
            "error": "patrol claim unavailable",
            "code": "patrol_claim_error",
            "detail": str(exc)[:300],
            "semantic_processing": False,
            "formal_state_bypass": FORMAL_STATE_BYPASS,
        }


__all__ = [
    "SCHEMA",
    "ACTION_CONTRACT_VERSION",
    "AUTHORITATIVE_TOOL",
    "REQUIRED_ACTION_FAMILIES",
    "RESERVED_ACTION_FAMILIES",
    "action_contract",
    "discover_patrol_work",
    "required_actions",
    "claim_patrol_work",
    "mcp_discover_patrol_work",
    "mcp_claim_patrol_work",
]
