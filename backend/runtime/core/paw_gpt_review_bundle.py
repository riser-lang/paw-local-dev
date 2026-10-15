#!/usr/bin/python3
"""Durable GPT ReviewBundle plane for post-analysis governance.

Sidecar store (not Formal State, not Cursor artifacts). Indexes staged
Knowledge/Project/Data proposals per SourceVersion so outer ChatGPT can
review even when findings are empty. Cursor/Luna/Agy cannot record CLEAR.
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
GATEWAY = "/opt/paw-mcp-gateway"
if GATEWAY not in sys.path:
    sys.path.insert(0, GATEWAY)

from gpt_brain_common import LockedJsonStore, hash_payload, safe_copy, text, unique, utc_now
from paw_gpt_clear_auto_write_policy import (
    APPROVAL_GPT_CLEAR,
    APPROVAL_OPUS_CLEAR,
    APPROVAL_RISK_ROUTER,
    APPROVAL_USER_RECEIPT,
    POLICY_ID,
    evaluate_write_changes,
)
from paw_review_display_normalization import (
    normalize_review_display,
    normalize_user_decision_card,
    validate_review_display,
)

SCHEMA = "paw-gpt-review-bundle/1"
DEFAULT_STORE = "/var/lib/paw-mcp-gateway/paw_gpt_review_bundles.json"
DEFAULT_BRAIN = "/var/lib/paw-mcp-gateway/gpt_brain_bootstrap.json"
CLAIM_LEASE_SECONDS = 30 * 60
MAX_CLAIM = 8
MAX_TRANSITION_HISTORY = 40
OPUS_MODEL_ID = "claude-opus-4-6-thinking"
HISTORICAL_MAIN_REVIEW_DRAIN_BLOCKED_REASON = "historical_main_review_retired_project_first_v2"

PERSIST_PERSISTED = "PERSISTED"
PERSIST_ALREADY = "ALREADY_PERSISTED"
PERSIST_STATE_CHANGED = "STATE_CHANGED"
PERSIST_INVALID_CLAIM = "INVALID_CLAIM"
PERSIST_CLAIM_EXPIRED = "CLAIM_EXPIRED"
PERSIST_BLOCKED_BEFORE_CAS = "BLOCKED_BEFORE_CAS"

REVIEW_PENDING = "REVIEW_PENDING"
CLEAR = "CLEAR"
NO_ACTION = "NO_ACTION"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
OPUS_REQUIRED = "OPUS_REQUIRED"
OPUS_REVIEWED = "OPUS_REVIEWED"
POST_OPUS_PENDING = "POST_OPUS_PENDING"
POST_OPUS_CLEAR = "POST_OPUS_CLEAR"
USER_DECISION_REQUIRED = "USER_DECISION_REQUIRED"
USER_DECIDED = "USER_DECIDED"
POLICY_WRITE_READY = "POLICY_WRITE_READY"
WRITTEN = "WRITTEN"
FINALIZED = "FINALIZED"
BLOCKED = "BLOCKED"

BUNDLE_OUTCOMES = frozenset({
    CLEAR, NO_ACTION, OPUS_REQUIRED, USER_DECISION_REQUIRED, INSUFFICIENT_EVIDENCE,
})
POST_OPUS_OUTCOMES = frozenset({CLEAR, "FINALIZE", USER_DECISION_REQUIRED, NO_ACTION, INSUFFICIENT_EVIDENCE})

_ALLOWED: dict[str, frozenset[str]] = {
    REVIEW_PENDING: frozenset({CLEAR, NO_ACTION, INSUFFICIENT_EVIDENCE, OPUS_REQUIRED, USER_DECISION_REQUIRED}),
    CLEAR: frozenset({POLICY_WRITE_READY, FINALIZED}),
    POST_OPUS_CLEAR: frozenset({POLICY_WRITE_READY, FINALIZED}),
    NO_ACTION: frozenset({FINALIZED}),
    INSUFFICIENT_EVIDENCE: frozenset({FINALIZED}),
    OPUS_REQUIRED: frozenset({OPUS_REVIEWED}),
    OPUS_REVIEWED: frozenset({POST_OPUS_PENDING}),
    POST_OPUS_PENDING: frozenset({POST_OPUS_CLEAR, USER_DECISION_REQUIRED, NO_ACTION, INSUFFICIENT_EVIDENCE, FINALIZED}),
    USER_DECISION_REQUIRED: frozenset({USER_DECIDED}),
    USER_DECIDED: frozenset({POLICY_WRITE_READY, FINALIZED}),
    POLICY_WRITE_READY: frozenset({WRITTEN, BLOCKED}),
    WRITTEN: frozenset(),
    FINALIZED: frozenset(),
    BLOCKED: frozenset({POLICY_WRITE_READY, FINALIZED}),
}

WRITABLE_COVERAGE = frozenset({
    "CONSOLIDATED", "DATA_EXTRACTED", "PROJECT_CONTEXT_ONLY",
    "REVIEW_REQUIRED", "INSUFFICIENT_EVIDENCE", "VISUAL_PENDING",
})
FORBIDDEN_CLEAR_TOKENS = ("luna", "cursor", "agy", "codex", "grok", "opus-dispatcher", "policy-committer")
RISK_ROUTER_WORKER = "chatgpt-risk-router"
RISK_ROUTER_MODEL = "deterministic-risk-router-v1"


def _risk_router_drain_enabled() -> bool:
    return text(os.environ.get("PAW_BOOTSTRAP_RISK_ROUTER_ENABLED", "0")).lower() not in {"0", "false", "no"}


def default_store_path() -> Path:
    return Path(os.environ.get("PAW_GPT_REVIEW_BUNDLE_PATH", DEFAULT_STORE))


def default_brain_path() -> Path:
    return Path(os.environ.get("PAW_GPT_BRAIN_STATE_PATH", DEFAULT_BRAIN))


def worker_may_record_clear(worker_id: str) -> bool:
    worker = text(worker_id).lower()
    if not worker.startswith("chatgpt"):
        return False
    return not any(token in worker for token in FORBIDDEN_CLEAR_TOKENS)


def _parse_utc(value: Any) -> datetime | None:
    raw = text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _blank_metrics() -> dict[str, int]:
    return {
        "review_pending": 0,
        "clear": 0,
        "opus_required": 0,
        "user_decision": 0,
        "finalized": 0,
        "policy_write_ready": 0,
        "written": 0,
        "no_action": 0,
        "insufficient_evidence": 0,
        "migrated_bundles": 0,
        "opus_clusters": 0,
        "user_decision_clusters": 0,
    }


def _blank() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "policy_id": POLICY_ID,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "bundles": {},
        "source_index": {},
        "opus_clusters": {},
        "user_decision_clusters": {},
        "policy_writes": {},
        "metrics": _blank_metrics(),
        "released_finding_claims": [],
        "events": [],
    }


class ReviewBundleStore:
    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.store = LockedJsonStore(path or default_store_path())

    def load(self) -> dict[str, Any]:
        data = self.store.load()
        if not data:
            return _blank()
        data.setdefault("bundles", {})
        data.setdefault("source_index", {})
        data.setdefault("opus_clusters", {})
        data.setdefault("user_decision_clusters", {})
        data.setdefault("policy_writes", {})
        data.setdefault("metrics", _blank_metrics())
        data.setdefault("released_finding_claims", [])
        return data

    def save(self, data: Mapping[str, Any]) -> None:
        payload = dict(data)
        payload["updated_at"] = utc_now()
        payload["schema"] = SCHEMA
        self.store.save(payload)


def _proposal_summaries(brain: Mapping[str, Any], source_version_id: str) -> dict[str, list[dict[str, Any]]]:
    knowledge = []
    for item in (brain.get("knowledge_topics") or {}).values():
        if isinstance(item, Mapping) and source_version_id in (item.get("source_version_ids") or []):
            knowledge.append({
                "id": item.get("id"),
                "fingerprint": item.get("topic_fingerprint"),
                "canonical_topic": item.get("canonical_topic"),
                "action": item.get("action"),
                "project_or_domain": item.get("project_or_domain"),
                "evidence_ids": list(item.get("evidence_ids") or []),
            })
    projects = []
    for item in (brain.get("project_consolidations") or {}).values():
        if isinstance(item, Mapping) and source_version_id in (item.get("source_version_ids") or []):
            projects.append({
                "id": item.get("id"),
                "project_id": item.get("project_id"),
                "project_name": item.get("project_name"),
                "operation": item.get("operation"),
                "user_decision_required": bool(item.get("user_decision_required")),
                "evidence_ids": list(item.get("evidence_ids") or []),
            })
    metrics = []
    for item in (brain.get("metric_proposals") or {}).values():
        if isinstance(item, Mapping) and source_version_id in (item.get("source_version_ids") or []):
            metrics.append({
                "id": item.get("id"),
                "fingerprint": item.get("metric_fingerprint"),
                "project": item.get("project"),
                "metric": item.get("metric"),
                "period": item.get("period"),
                "unit": item.get("unit"),
                "evidence_ids": list(item.get("evidence_ids") or []),
            })
    findings = []
    for item in (brain.get("findings") or {}).values():
        if isinstance(item, Mapping) and source_version_id in (item.get("source_version_ids") or []):
            findings.append({
                "id": item.get("id"),
                "fingerprint": item.get("fingerprint"),
                "lineage_fingerprint": item.get("lineage_fingerprint"),
                "finding_type": item.get("finding_type"),
                "project_or_domain": item.get("project_or_domain"),
                "affected_targets": list(item.get("affected_targets") or []),
                "proposed_operation": item.get("proposed_operation"),
                "proposed_target": item.get("proposed_target"),
                "assertion": item.get("assertion"),
                "state": item.get("state"),
            })
    return {"knowledge": knowledge, "projects": projects, "metrics": metrics, "findings": findings}


def _evidence_refs_for(brain: Mapping[str, Any], evidence_ids: list[str], source_version_id: str) -> list[dict[str, Any]]:
    nodes = brain.get("evidence_nodes") or {}
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for eid in evidence_ids:
        node = nodes.get(eid) if isinstance(nodes, Mapping) else None
        if not isinstance(node, Mapping):
            continue
        locator = text(node.get("evidence_locator"))
        sv = text(node.get("source_version_id")) or source_version_id
        key = f"{sv}|{locator}"
        if not locator or key in seen:
            continue
        seen.add(key)
        item: dict[str, Any] = {"source_version_id": sv, "evidence_locator": locator, "evidence_id": eid}
        if node.get("claim"):
            item["claim"] = node.get("claim")
        refs.append(item)
    return refs


def _fingerprint(source_version_id: str, summaries: Mapping[str, list]) -> str:
    return hash_payload({
        "source_version_id": source_version_id,
        "knowledge_ids": [x.get("id") for x in summaries.get("knowledge") or []],
        "project_ids": [x.get("id") for x in summaries.get("projects") or []],
        "metric_ids": [x.get("id") for x in summaries.get("metrics") or []],
        "finding_ids": [x.get("id") for x in summaries.get("findings") or []],
    })


def bundle_id_for(source_version_id: str, fingerprint: str) -> str:
    return "bundle:" + hash_payload({"source_version_id": source_version_id, "fingerprint": fingerprint})[:24]


def _eligible(source_state: Mapping[str, Any], summaries: Mapping[str, list]) -> bool:
    coverage = text(source_state.get("state")).upper()
    if coverage not in WRITABLE_COVERAGE:
        return False
    return bool(summaries.get("knowledge") or summaries.get("projects") or summaries.get("metrics") or summaries.get("findings"))


def _cluster_key_from_finding(finding: Mapping[str, Any]) -> str:
    lineage = text(finding.get("lineage_fingerprint"))
    if lineage:
        return lineage
    return hash_payload({
        "finding_type": text(finding.get("finding_type")),
        "affected_targets": list(finding.get("affected_targets") or []),
        "proposed_operation": text(finding.get("proposed_operation")),
        "project_or_domain": text(finding.get("project_or_domain")),
        "proposed_target": text(finding.get("proposed_target")),
    })


def _bundle_needs_gpt_review(bundle: Mapping[str, Any], brain_data: Mapping[str, Any] | None = None) -> bool:
    """True when bundle should remain in GPT authoritative review queue."""
    risk = bundle.get("risk_route") if isinstance(bundle.get("risk_route"), Mapping) else None
    if risk:
        return text(risk.get("route")) in {"", "GPT_REVIEW_REQUIRED"}
    try:
        from paw_bootstrap_risk_router import ROUTE_GPT_REVIEW, classify_bundle
        brain = brain_data if isinstance(brain_data, Mapping) else _load_brain_snapshot(brain_data)
        sid = text(bundle.get("source_version_id"))
        source_state = ((brain.get("bootstrap") or {}).get("source_states") or {}).get(sid) or {}
        decision = classify_bundle(bundle, brain, source_state=source_state)
        return text(decision.get("route")) == ROUTE_GPT_REVIEW
    except Exception:
        return True


def _cluster_key_from_bundle(bundle: Mapping[str, Any]) -> str:
    risk = bundle.get("risk_route") if isinstance(bundle.get("risk_route"), Mapping) else None
    if risk and bundle.get("risk_decision"):
        try:
            from paw_bootstrap_risk_router import risk_cluster_key
            return risk_cluster_key(bundle.get("risk_decision"))
        except Exception:
            pass
    if risk:
        try:
            from paw_bootstrap_risk_router import risk_cluster_key
            return risk_cluster_key({
                "project_id": risk.get("project_id"),
                "canonical_topic": risk.get("canonical_topic"),
                "conflict_flags": risk.get("conflict_flags") or [],
                "version_relation": risk.get("version_relation"),
                "route": risk.get("route"),
            })
        except Exception:
            pass
    findings = bundle.get("findings") or []
    if findings:
        keys = unique(_cluster_key_from_finding(f) for f in findings if isinstance(f, Mapping))
        if keys:
            return keys[0]
    knowledge = bundle.get("knowledge_proposals") or []
    if knowledge:
        return hash_payload({
            "kind": "knowledge",
            "topics": [text(x.get("canonical_topic")) for x in knowledge if isinstance(x, Mapping)],
            "actions": [text(x.get("action")) for x in knowledge if isinstance(x, Mapping)],
        })
    projects = bundle.get("project_proposals") or []
    if projects:
        return hash_payload({
            "kind": "project",
            "project_ids": [text(x.get("project_id")) for x in projects if isinstance(x, Mapping)],
        })
    return hash_payload({"source_version_id": bundle.get("source_version_id"), "kind": "source"})


def _recount(data: dict[str, Any]) -> None:
    metrics = _blank_metrics()
    metrics["migrated_bundles"] = int((data.get("metrics") or {}).get("migrated_bundles") or 0)
    for bundle in (data.get("bundles") or {}).values():
        state = text(bundle.get("state"))
        mapping = {
            REVIEW_PENDING: "review_pending",
            CLEAR: "clear",
            POST_OPUS_CLEAR: "clear",
            OPUS_REQUIRED: "opus_required",
            USER_DECISION_REQUIRED: "user_decision",
            FINALIZED: "finalized",
            POLICY_WRITE_READY: "policy_write_ready",
            WRITTEN: "written",
            NO_ACTION: "no_action",
            INSUFFICIENT_EVIDENCE: "insufficient_evidence",
        }
        key = mapping.get(state)
        if key:
            metrics[key] += 1
    metrics["opus_clusters"] = len(data.get("opus_clusters") or {})
    metrics["user_decision_clusters"] = len(data.get("user_decision_clusters") or {})
    data["metrics"] = metrics


def _claim_expired(claim: Mapping[str, Any] | None) -> bool:
    if not claim:
        return True
    expiry = _parse_utc(claim.get("claim_expires_at") or claim.get("expires_at"))
    if expiry is None:
        return True
    return datetime.now(timezone.utc) >= expiry


def _ensure_cas_fields(bundle: dict[str, Any]) -> None:
    """Lazy migration for legacy bundles: revision, audit, bounded history."""
    if "state_revision" not in bundle:
        history = bundle.get("state_history") or []
        bundle["state_revision"] = max(0, len(history) - 1) if history else 0
    bundle.setdefault("last_transition_actor", "migration")
    bundle.setdefault("last_transition_at", bundle.get("created_at") or utc_now())
    bundle.setdefault("last_transition_reason", "lazy_cas_migration")
    if not bundle.get("transition_history"):
        migrated: list[dict[str, Any]] = []
        prev = None
        for idx, item in enumerate(bundle.get("state_history") or []):
            if not isinstance(item, Mapping):
                continue
            state = text(item.get("state"))
            migrated.append({
                "from_state": prev,
                "to_state": state,
                "revision": idx,
                "actor": "legacy_state_history",
                "reason": "migrated",
                "at": item.get("at") or bundle.get("created_at"),
                "claim_id": None,
            })
            prev = state
        bundle["transition_history"] = migrated[-MAX_TRANSITION_HISTORY:]


def _active_main_claim(bundle: Mapping[str, Any]) -> dict[str, Any] | None:
    claim = bundle.get("claim") if isinstance(bundle.get("claim"), Mapping) else None
    if not claim or _claim_expired(claim):
        return None
    if text(claim.get("kind") or "main_review") != "main_review":
        return None
    return dict(claim)


def _claim_owner(claim: Mapping[str, Any]) -> str:
    return text(claim.get("claim_owner") or claim.get("worker_id"))


def _main_claim_blocks(actor: str, bundle: Mapping[str, Any], *, bypass: bool = False) -> bool:
    """True when a non-owner must not mutate main review state."""
    if bypass:
        return False
    active = _active_main_claim(bundle)
    if not active:
        return False
    owner = _claim_owner(active)
    return bool(owner) and text(actor) != owner


def _append_transition_history(
    bundle: dict[str, Any],
    *,
    from_state: str,
    to_state: str,
    revision: int,
    actor: str,
    reason: str,
    claim_id: str | None = None,
) -> None:
    history = bundle.setdefault("transition_history", [])
    history.append({
        "from_state": from_state,
        "to_state": to_state,
        "revision": revision,
        "actor": text(actor) or "unknown",
        "reason": text(reason)[:500] or "transition",
        "at": utc_now(),
        "claim_id": text(claim_id) or None,
    })
    if len(history) > MAX_TRANSITION_HISTORY:
        bundle["transition_history"] = history[-MAX_TRANSITION_HISTORY:]


def _transition_bundle(
    bundle: dict[str, Any],
    target: str,
    *,
    actor: str,
    reason: str,
    claim_id: str | None = None,
    bypass_claim_guard: bool = False,
) -> None:
    _ensure_cas_fields(bundle)
    current = text(bundle.get("state")) or REVIEW_PENDING
    if target == current:
        return
    if current == REVIEW_PENDING and _main_claim_blocks(actor, bundle, bypass=bypass_claim_guard):
        raise ValueError(
            f"active main review claim held by {_claim_owner(_active_main_claim(bundle) or {})}; "
            f"transition {current} -> {target} blocked for actor {actor}"
        )
    if target == POLICY_WRITE_READY and not bundle.get("is_authoritative"):
        raise ValueError("POLICY_WRITE_READY transition requires authoritative approval; Luna pre-analysis cannot self-authorize")
    allowed = _ALLOWED.get(current, frozenset())
    if target not in allowed:
        raise ValueError(f"invalid review bundle transition: {current} -> {target}")
    next_revision = int(bundle.get("state_revision") or 0) + 1
    bundle["state"] = target
    bundle["state_revision"] = next_revision
    bundle["last_transition_actor"] = text(actor) or "unknown"
    bundle["last_transition_at"] = utc_now()
    bundle["last_transition_reason"] = text(reason)[:500] or "transition"
    bundle["updated_at"] = bundle["last_transition_at"]
    bundle.setdefault("state_history", []).append({"state": target, "at": bundle["updated_at"]})
    _append_transition_history(
        bundle,
        from_state=current,
        to_state=target,
        revision=next_revision,
        actor=actor,
        reason=reason,
        claim_id=claim_id,
    )


def _transition(bundle: dict[str, Any], target: str, *, actor: str = "internal", reason: str = "legacy_transition") -> None:
    _transition_bundle(bundle, target, actor=actor, reason=reason)


def _review_result_key(outcome: str, reason: str, write_changes: list[Any], action: str) -> str:
    return hash_payload({
        "outcome": text(outcome).upper(),
        "reason": text(reason),
        "action_on_luna_proposal": text(action).upper(),
        "write_changes": write_changes or [],
    })


def _persist_response(status: str, *, bundle: Mapping[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": status, "formal_state_bypass": 0, **extra}
    if bundle is not None:
        payload["bundle_id"] = bundle.get("id")
        payload["state"] = bundle.get("state")
        payload["state_revision"] = bundle.get("state_revision")
    return payload


def _state_changed_details(bundle: Mapping[str, Any], expected_revision: int | None = None) -> dict[str, Any]:
    return {
        "current_state": bundle.get("state"),
        "current_revision": int(bundle.get("state_revision") or 0),
        "expected_revision": expected_revision,
        "last_transition_actor": bundle.get("last_transition_actor"),
        "last_transition_at": bundle.get("last_transition_at"),
        "last_transition_reason": bundle.get("last_transition_reason"),
    }


def _review_claim_policy() -> dict[str, Any]:
    try:
        from app.gpt_brain_bootstrap import configured_max_active_claims
        max_active, single_flight = configured_max_active_claims()
    except Exception:
        max_active, single_flight = 3, False
    return {
        "max_active_claims": int(max_active),
        "global_single_flight": bool(single_flight),
        "claim_scope": "review_bundle_main_review",
    }


def _load_brain_snapshot(brain_data: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(brain_data, Mapping):
        return {
            "bootstrap": dict(brain_data.get("bootstrap") or {}),
            "patrol": dict(brain_data.get("patrol") or {}),
            "findings": dict(brain_data.get("findings") or {}),
        }
    try:
        brain = _load_live_brain()
        data = brain._data()
        return {
            "bootstrap": dict(data.get("bootstrap") or {}),
            "patrol": dict(data.get("patrol") or {}),
            "findings": dict(data.get("findings") or {}),
        }
    except Exception:
        return {"bootstrap": {}, "patrol": {}, "findings": {}}


def _active_review_claim_diagnostics(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for bundle in (data.get("bundles") or {}).values():
        if not isinstance(bundle, Mapping):
            continue
        claim = _active_main_claim(bundle)
        if not claim:
            continue
        claims.append({
            **safe_copy(claim),
            "bundle_id": bundle.get("id"),
            "state": bundle.get("state"),
            "state_revision": int(bundle.get("state_revision") or 0),
        })
    return claims


def _bootstrap_source_coverage_ready(bootstrap: Mapping[str, Any]) -> bool:
    status = text(bootstrap.get("status")).upper()
    if status not in {"ACTIVE", "COMPLETED"}:
        return False
    unprocessed = int(bootstrap.get("unprocessed_source") or 0)
    visual_pending = int(bootstrap.get("visual_pending_count") or 0)
    return unprocessed == 0 and visual_pending == 0


def _bootstrap_completion_blockers(snapshot: Mapping[str, Any]) -> list[str]:
    bootstrap = snapshot.get("bootstrap") or {}
    if text(bootstrap.get("status")).upper() == "COMPLETED":
        return []
    blockers: list[str] = []
    states = bootstrap.get("source_states") or {}
    try:
        from app.gpt_brain_bootstrap import COVERAGE_FINAL_STATES
        final_states = COVERAGE_FINAL_STATES
    except Exception:
        final_states = frozenset({
            "CONSOLIDATED", "NO_KNOWLEDGE_IMPACT", "DATA_EXTRACTED",
            "PROJECT_CONTEXT_ONLY", "INSUFFICIENT_EVIDENCE", "FAILED",
        })
    unfinished = [
        sid for sid, state in states.items()
        if isinstance(state, Mapping) and text(state.get("state")) not in final_states
    ]
    if unfinished:
        blockers.append(f"unfinished_sources:{len(unfinished)}")
    failed_sources = [
        sid for sid, state in states.items()
        if isinstance(state, Mapping) and text(state.get("state")) == "FAILED"
    ]
    if failed_sources:
        blockers.append(f"failed_sources:{len(failed_sources)}")
    unresolved = [
        fid for fid, finding in (snapshot.get("findings") or {}).items()
        if isinstance(finding, Mapping) and finding.get("state") not in {"FINALIZED", "WRITE_READY", "WRITTEN"}
    ]
    if unresolved:
        blockers.append(f"unresolved_findings:{len(unresolved)}")
    opus_pending = list((bootstrap.get("opus") or {}).get("pending_items") or [])
    if opus_pending:
        blockers.append(f"opus_pending:{len(opus_pending)}")
    return blockers


def compute_review_drain_gate(
    *,
    store: ReviewBundleStore | None = None,
    brain_data: Mapping[str, Any] | None = None,
    bundle_id: str | None = None,
    claim_id: str | None = None,
    worker_id: str | None = None,
) -> dict[str, Any]:
    """Machine-readable ReviewBundle drain / record gate derived from live server state.

    Historical Luna semantic ReviewBundles are retained for diagnostics and
    downstream post-Opus/user-decision work, but the ordinary main-review
    claim/record path is permanently fail-closed.  This is intentionally a
    runtime gate, not a counter reset or a state migration.
    """
    store = store or ReviewBundleStore()
    snapshot = _load_brain_snapshot(brain_data)
    bootstrap = snapshot.get("bootstrap") or {}
    policy = _review_claim_policy()
    data = store.load()
    review_claims = _active_review_claim_diagnostics(data)
    review_active_count = len(review_claims)
    bootstrap_status = text(bootstrap.get("status")).upper() or "UNKNOWN"
    bootstrap_source_claim_count = int(bootstrap.get("active_claim_count") or 0)
    source_coverage_ready = _bootstrap_source_coverage_ready(bootstrap)
    # Do not derive this from queue counts or bootstrap completion.  The
    # historical Source Luna main-review routine is retired even when its
    # backlog is non-zero; counters remain truthful for audit/readback.
    authoritative_review_drain_enabled = False
    drain_blocked_reason = HISTORICAL_MAIN_REVIEW_DRAIN_BLOCKED_REASON

    max_active = int(policy["max_active_claims"])
    concurrency_gate_passed = review_active_count < max_active
    concurrency_gate_reason = "review_concurrency_available"
    record_allowed_for_owned_claim = False
    record_gate_reason = "claim_context_not_provided"

    worker = text(worker_id)
    bundle = (data.get("bundles") or {}).get(text(bundle_id)) if bundle_id else None
    if isinstance(bundle, Mapping) and worker and text(claim_id):
        claim = bundle.get("claim") if isinstance(bundle.get("claim"), Mapping) else {}
        owned = (
            text(bundle.get("state")) == REVIEW_PENDING
            and claim
            and not _claim_expired(claim)
            and text(claim.get("claim_id")) == text(claim_id)
            and _claim_owner(claim) == worker
        )
        if owned:
            concurrency_gate_passed = True
            concurrency_gate_reason = "owned_claim_completion_allowed"
            if authoritative_review_drain_enabled:
                record_allowed_for_owned_claim = True
                record_gate_reason = "owned_claim_forward_record_allowed"
            else:
                record_gate_reason = drain_blocked_reason or "authoritative_review_drain_disabled"
        elif not claim or _claim_expired(claim):
            record_gate_reason = "missing_or_expired_claim"
        elif text(claim.get("claim_id")) != text(claim_id):
            record_gate_reason = "claim_id_mismatch"
        elif _claim_owner(claim) != worker:
            record_gate_reason = "claim_owner_mismatch"
        else:
            record_gate_reason = "bundle_not_review_pending"
    elif review_active_count >= max_active:
        concurrency_gate_passed = False
        concurrency_gate_reason = "review_active_claim_limit"

    return {
        "authoritative_review_drain_enabled": authoritative_review_drain_enabled,
        "authoritative_review_drain_blocked_reason": drain_blocked_reason,
        "concurrency_gate_passed": concurrency_gate_passed,
        "concurrency_gate_reason": concurrency_gate_reason,
        "review_active_claim_count": review_active_count,
        "bootstrap_source_active_claim_count": bootstrap_source_claim_count,
        "active_claim_scope_note": "bootstrap.active_claim_count is source-consolidation only; review_active_claim_count is ReviewBundle main_review only",
        "max_active_claims": max_active,
        "global_single_flight": bool(policy["global_single_flight"]),
        "record_allowed_for_owned_claim": record_allowed_for_owned_claim,
        "record_gate_reason": record_gate_reason,
        "review_record_gate": "PASS" if record_allowed_for_owned_claim else "FAIL",
        "bootstrap_status": bootstrap_status or None,
        "bootstrap_unprocessed_source": int(bootstrap.get("unprocessed_source") or 0),
        "bootstrap_visual_pending_count": int(bootstrap.get("visual_pending_count") or 0),
        "formal_state_bypass": 0,
    }


def compute_patrol_resume_gate(*, brain_data: Mapping[str, Any] | None = None) -> dict[str, Any]:
    snapshot = _load_brain_snapshot(brain_data)
    bootstrap = snapshot.get("bootstrap") or {}
    patrol = snapshot.get("patrol") or {}
    bootstrap_status = text(bootstrap.get("status")).upper() or "UNKNOWN"
    source_coverage_ready = _bootstrap_source_coverage_ready(bootstrap)
    completion_blockers = _bootstrap_completion_blockers(snapshot)
    project_first_gate = {}
    try:
        project_first_gate = json.loads(Path("/var/lib/paw-mcp-gateway/paw_project_first_incremental_gate.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        project_first_gate = {}
    project_first_enabled = (
        text(project_first_gate.get("status")).upper() == "ENABLED"
        and int(project_first_gate.get("machine_actionable_backlog_remaining") or 0) == 0
        and int(project_first_gate.get("baseline_projects") or 0) > 0
        and int(project_first_gate.get("baseline_readback_pass") or 0) == int(project_first_gate.get("baseline_projects") or 0)
        and project_first_gate.get("full_corpus_reread") is False
    )
    incremental_enabled = (bootstrap_status == "COMPLETED" and bool(patrol.get("enabled"))) or project_first_enabled
    patrol_blocked_reason = None
    if not incremental_enabled:
        if project_first_enabled:
            patrol_blocked_reason = None
        elif bootstrap_status != "COMPLETED":
            patrol_blocked_reason = "bootstrap_not_completed"
            if completion_blockers:
                patrol_blocked_reason = "bootstrap_not_completed:" + ",".join(completion_blockers[:4])
        elif not patrol.get("enabled"):
            patrol_blocked_reason = "patrol_not_enabled"
    daily_capture_enabled = source_coverage_ready and bootstrap_status in {"ACTIVE", "COMPLETED"}
    daily_capture_blocked_reason = None
    if not daily_capture_enabled:
        if bootstrap_status in {"", "PREPARED", "WAITING_CURSOR"}:
            daily_capture_blocked_reason = "bootstrap_not_started"
        else:
            daily_capture_blocked_reason = "bootstrap_source_queue_incomplete"
    return {
        "patrol": {
            "enabled": bool(patrol.get("enabled")),
            "blocked_reason": patrol_blocked_reason,
            "checkpoint": patrol.get("checkpoint"),
            "full_corpus_reads_blocked": int(patrol.get("full_corpus_reads_blocked") or 0),
        },
        "incremental_mirror_patrol_enabled": incremental_enabled,
        "incremental_mirror_patrol_blocked_reason": patrol_blocked_reason,
        "daily_capture_patrol_enabled": daily_capture_enabled,
        "daily_capture_patrol_blocked_reason": daily_capture_blocked_reason,
        "bootstrap_completion_blockers": completion_blockers,
        "project_first_incremental_gate": project_first_gate,
        "no_full_corpus_reread_after_bootstrap": True,
        "formal_state_bypass": 0,
    }


def enrich_bootstrap_status_with_review_gates(status: Mapping[str, Any]) -> dict[str, Any]:
    enriched = safe_copy(dict(status))
    try:
        full = _load_live_brain()._data()
    except Exception:
        full = {}
    brain_data = {
        "bootstrap": enriched.get("bootstrap") or full.get("bootstrap") or {},
        "patrol": enriched.get("patrol") or full.get("patrol") or {},
        "findings": full.get("findings") or {},
    }
    review_gate = compute_review_drain_gate(brain_data=brain_data)
    patrol_gate = compute_patrol_resume_gate(brain_data=brain_data)
    enriched["review_drain"] = review_gate
    enriched["patrol"] = {**(enriched.get("patrol") or {}), **(patrol_gate.get("patrol") or {})}
    enriched["daily_capture_patrol"] = {
        "enabled": patrol_gate.get("daily_capture_patrol_enabled"),
        "blocked_reason": patrol_gate.get("daily_capture_patrol_blocked_reason"),
    }
    enriched["incremental_mirror_patrol"] = {
        "enabled": patrol_gate.get("incremental_mirror_patrol_enabled"),
        "blocked_reason": patrol_gate.get("incremental_mirror_patrol_blocked_reason"),
        "no_full_corpus_reread": patrol_gate.get("no_full_corpus_reread_after_bootstrap"),
    }
    bootstrap = enriched.setdefault("bootstrap", {})
    if isinstance(bootstrap, dict):
        bootstrap["review_bundle_active_claim_count"] = review_gate.get("review_active_claim_count")
        bootstrap["bootstrap_source_active_claim_scope"] = "source_consolidation_only"
    try:
        from paw_runtime_state_projection import compact_preflight, project_runtime_state
        projection = project_runtime_state(
            bootstrap_status=brain_data,
            patrol_gate=patrol_gate,
        )
        preflight = compact_preflight(projection)
        hist = projection.get("historical_bootstrap_status") or {}
        enriched["operational_plane"] = projection.get("operational_plane")
        enriched["historical_bootstrap_status"] = hist
        enriched["historical_review_counts"] = projection.get("historical_review_counts")
        enriched["current_incremental_patrol_status"] = projection.get("current_incremental_patrol_status")
        enriched["current_incremental_backlog"] = projection.get("current_incremental_backlog")
        enriched["current_capture_backlog"] = projection.get("current_capture_backlog")
        enriched["historical_unresolved_counts"] = projection.get("historical_unresolved_counts")
        enriched["patrol_preflight"] = preflight
        if isinstance(bootstrap, dict):
            bootstrap["lifecycle"] = hist.get("lifecycle")
            bootstrap["counts_as_current_production_work"] = hist.get("counts_as_current_production_work")
            bootstrap["raw_status"] = bootstrap.get("status")
    except Exception:
        pass
    enriched["formal_state_bypass"] = 0
    return enriched


def _attach_review_gate_fields(payload: dict[str, Any], *, gate: Mapping[str, Any]) -> dict[str, Any]:
    payload.update({
        "authoritative_review_drain_enabled": gate.get("authoritative_review_drain_enabled"),
        "authoritative_review_drain_blocked_reason": gate.get("authoritative_review_drain_blocked_reason"),
        "concurrency_gate_passed": gate.get("concurrency_gate_passed"),
        "concurrency_gate_reason": gate.get("concurrency_gate_reason"),
        "review_active_claim_count": gate.get("review_active_claim_count"),
        "bootstrap_source_active_claim_count": gate.get("bootstrap_source_active_claim_count"),
        "max_active_claims": gate.get("max_active_claims"),
        "record_allowed_for_owned_claim": gate.get("record_allowed_for_owned_claim"),
        "record_gate_reason": gate.get("record_gate_reason"),
        "review_record_gate": gate.get("review_record_gate"),
        "formal_state_bypass": 0,
    })
    return payload


def _flatten_ids(*groups: list) -> list[str]:
    values: list[str] = []
    for group in groups:
        for item in group:
            if isinstance(item, Mapping):
                values.extend(item.get("evidence_ids") or [])
    return unique(values)


def _bundle_source_state(bundle: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Derive policy source_state from bundle sidecar data without loading Brain."""
    if not isinstance(bundle, Mapping):
        return None
    luna = bundle.get("luna_preanalysis") if isinstance(bundle.get("luna_preanalysis"), Mapping) else {}
    provenance = bundle.get("provenance") if isinstance(bundle.get("provenance"), Mapping) else {}
    if not provenance and isinstance(luna.get("provenance"), Mapping):
        provenance = dict(luna.get("provenance") or {})
    coverage = text(bundle.get("coverage_state") or luna.get("coverage_state"))
    if not coverage and not provenance:
        return None
    return {
        "state": coverage,
        "processor_provenance": provenance,
        "source_version_id": text(bundle.get("source_version_id")),
    }


def get_bundle_preanalysis(bundle: Mapping[str, Any], source_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Derive or extract non-authoritative Luna pre-analysis representation from bundle."""
    existing = bundle.get("luna_preanalysis")
    if isinstance(existing, Mapping):
        return safe_copy(existing)
    prov = dict(bundle.get("provenance") or {})
    if source_state and not prov:
        prov = dict(source_state.get("processor_provenance") or {})
    model = prov.get("model") or "gpt-5.6-luna"
    actor = prov.get("actor") or "paw-codex-luna-bootstrap"
    return {
        "model": model,
        "actor": actor,
        "role": "luna_preanalysis",
        "is_authoritative": False,
        "coverage_state": text((source_state or {}).get("state") or bundle.get("coverage_state")),
        "knowledge_proposals": safe_copy(bundle.get("knowledge_proposals") or []),
        "project_proposals": safe_copy(bundle.get("project_proposals") or []),
        "metric_proposals": safe_copy(bundle.get("metric_proposals") or []),
        "findings": safe_copy(bundle.get("findings") or []),
        "evidence_refs": safe_copy(bundle.get("evidence_refs") or []),
        "provenance": prov,
    }


def get_bundle_authoritative_consolidation(bundle: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return authoritative consolidation record if present, else None."""
    auth = bundle.get("gpt_authoritative_consolidation")
    return safe_copy(auth) if isinstance(auth, Mapping) else None


def _bundle_review_display(bundle: Mapping[str, Any], *, stage: str = "review") -> dict[str, Any]:
    """Derive the only user-facing projection; raw review data stays internal."""
    review = bundle.get("review") if isinstance(bundle.get("review"), Mapping) else {}
    post = bundle.get("post_opus") if isinstance(bundle.get("post_opus"), Mapping) else {}
    authoritative = bundle.get("gpt_authoritative_consolidation") if isinstance(bundle.get("gpt_authoritative_consolidation"), Mapping) else {}
    reason = post.get("reason") or review.get("reason") or authoritative.get("reason")
    raw = {
        "title": bundle.get("title") or bundle.get("canonical_topic") or bundle.get("source_version_id"),
        "situation": bundle.get("situation") or reason,
        "reason": reason,
        "decision_question": bundle.get("decision_question"),
        "recommendation": bundle.get("recommendation"),
        "approval_effect": bundle.get("approval_effect"),
        "conflict_explanation": bundle.get("conflict_explanation") or reason,
        "next_action": bundle.get("next_action"),
        "affected_items": bundle.get("affected_items") or bundle.get("affected_targets"),
        "evidence_refs": bundle.get("evidence_refs") or [],
        "state": bundle.get("state"),
        "outcome": post.get("outcome") or review.get("outcome") or bundle.get("state"),
    }
    display = normalize_review_display(
        raw,
        machine_state=text(bundle.get("state")) or None,
        stage=stage,
        raw_output={"review": safe_copy(review), "post_opus": safe_copy(post), "authoritative": safe_copy(authoritative)},
    )
    display["contract_errors"] = validate_review_display(display)
    return display


def _ensure_bundle_from_source(
    data: dict[str, Any],
    brain: Mapping[str, Any],
    source_version_id: str,
    source_state: Mapping[str, Any],
) -> dict[str, Any] | None:
    summaries = _proposal_summaries(brain, source_version_id)
    if not _eligible(source_state, summaries):
        return None
    fingerprint = _fingerprint(source_version_id, summaries)
    existing_id = (data.get("source_index") or {}).get(source_version_id)
    existing = (data.get("bundles") or {}).get(existing_id) if existing_id else None
    if isinstance(existing, dict) and existing.get("fingerprint") == fingerprint:
        _ensure_cas_fields(existing)
        return existing
    evidence_ids = _flatten_ids(summaries["knowledge"], summaries["projects"], summaries["metrics"])
    evidence_refs = _evidence_refs_for(brain, evidence_ids, source_version_id)
    luna_pre = {
        "model": (source_state.get("processor_provenance") or {}).get("model") or "gpt-5.6-luna",
        "actor": (source_state.get("processor_provenance") or {}).get("actor") or "paw-codex-luna-bootstrap",
        "role": "luna_preanalysis",
        "is_authoritative": False,
        "coverage_state": text(source_state.get("state")),
        "knowledge_proposals": summaries["knowledge"],
        "project_proposals": summaries["projects"],
        "metric_proposals": summaries["metrics"],
        "findings": summaries["findings"],
        "evidence_refs": evidence_refs,
        "provenance": safe_copy(source_state.get("processor_provenance") or {}),
        "created_at": utc_now(),
    }
    payload = {
        "fingerprint": fingerprint,
        "luna_preanalysis": luna_pre,
        "gpt_authoritative_consolidation": None,
        "is_authoritative": False,
        "knowledge_proposals": summaries["knowledge"],
        "project_proposals": summaries["projects"],
        "metric_proposals": summaries["metrics"],
        "findings": summaries["findings"],
        "finding_ids": [x.get("id") for x in summaries["findings"]],
        "proposal_ids": [x.get("id") for x in summaries["knowledge"] + summaries["projects"] + summaries["metrics"]],
        "evidence_ids": evidence_ids,
        "evidence_refs": evidence_refs,
        "coverage_state": text(source_state.get("state")),
        "provenance": safe_copy(source_state.get("processor_provenance") or {}),
        "updated_at": utc_now(),
    }
    if isinstance(existing, dict) and existing.get("state") == REVIEW_PENDING:
        if _active_main_claim(existing):
            _ensure_cas_fields(existing)
            return existing
        existing.update(payload)
        _ensure_cas_fields(existing)
        return existing
    bundle_id = bundle_id_for(source_version_id, fingerprint)
    now = utc_now()
    record = {
        "id": bundle_id,
        "source_version_id": source_version_id,
        "derived_from": existing.get("id") if isinstance(existing, dict) else None,
        "state": REVIEW_PENDING,
        "state_revision": 0,
        "state_history": [{"state": REVIEW_PENDING, "at": now}],
        "transition_history": [{
            "from_state": None,
            "to_state": REVIEW_PENDING,
            "revision": 0,
            "actor": "migrate_from_brain",
            "reason": "bundle_created",
            "at": now,
            "claim_id": None,
        }],
        "last_transition_actor": "migrate_from_brain",
        "last_transition_at": now,
        "last_transition_reason": "bundle_created",
        "source_refs": [f"source_version:{source_version_id}"],
        "claim": None,
        "review": None,
        "luna_preanalysis": luna_pre,
        "gpt_authoritative_consolidation": None,
        "is_authoritative": False,
        "opus_cluster_id": None,
        "user_decision_cluster_id": None,
        "write_changes": [],
        "policy_write_ids": [],
        "created_at": now,
        "migrated": True,
        **payload,
    }
    record["review_display"] = _bundle_review_display(record, stage="review_pending")
    data.setdefault("bundles", {})[bundle_id] = record
    data.setdefault("source_index", {})[source_version_id] = bundle_id
    return record


def migrate_from_brain(
    brain_data: Mapping[str, Any],
    *,
    store: ReviewBundleStore | None = None,
    release_finding_claims: bool = True,
    brain: Any | None = None,
) -> dict[str, Any]:
    """Idempotently index staged Source results. Does not rerun Luna or rewrite semantics."""
    store = store or ReviewBundleStore()
    with store.store.mutate() as data:
        if not data.get("schema"):
            data.update(_blank())
        source_states = ((brain_data.get("bootstrap") or {}).get("source_states") or {})
        created = 0
        reused = 0
        for sid, state in source_states.items():
            if not isinstance(state, Mapping):
                continue
            before = (data.get("source_index") or {}).get(sid)
            record = _ensure_bundle_from_source(data, brain_data, text(sid), state)
            if record is None:
                continue
            if before == record.get("id"):
                reused += 1
            else:
                created += 1
        released: list[dict[str, Any]] = []
        if release_finding_claims:
            released = _collect_release_claims(brain_data)
            existing = data.setdefault("released_finding_claims", [])
            seen = {x.get("finding_id") for x in existing if isinstance(x, Mapping)}
            for item in released:
                if item.get("finding_id") not in seen:
                    existing.append(item)
        data["metrics"]["migrated_bundles"] = int(data["metrics"].get("migrated_bundles") or 0) + created
        _recount(data)
        result = {
            "status": "migrated",
            "created": created,
            "reused": reused,
            "review_pending": data["metrics"]["review_pending"],
            "total_bundles": len(data.get("bundles") or {}),
            "released_finding_claims": len(released),
            "formal_state_bypass": 0,
        }
    if release_finding_claims and brain is not None and released:
        release_finding_claims_on_brain(brain, released)
    return result


def _collect_release_claims(brain_data: Mapping[str, Any]) -> list[dict[str, Any]]:
    released: list[dict[str, Any]] = []
    for finding in (brain_data.get("findings") or {}).values():
        if not isinstance(finding, Mapping):
            continue
        if text(finding.get("state")) != "GPT_ANALYZED" or finding.get("self_review"):
            continue
        claim = finding.get("self_review_claim")
        if not claim:
            continue
        released.append({
            "finding_id": finding.get("id"),
            "claim": safe_copy(claim),
            "released_at": utc_now(),
            "completed": False,
        })
    return released


def release_finding_claims_on_brain(brain: Any, released: list[Mapping[str, Any]]) -> int:
    if not released:
        return 0
    with brain.store.exclusive_lock():
        data = brain._data()
        count = 0
        for item in released:
            finding = data.get("findings", {}).get(item.get("finding_id"))
            if not isinstance(finding, dict):
                continue
            if finding.get("state") != "GPT_ANALYZED" or finding.get("self_review"):
                continue
            if finding.get("self_review_claim"):
                finding["self_review_claim"] = None
                finding["self_review_claim_released_at"] = utc_now()
                count += 1
        if count:
            brain._save(data)
        return count


def index_recorded_source(source_version_id: str, *, brain_data: Mapping[str, Any], store: ReviewBundleStore | None = None) -> dict[str, Any] | None:
    store = store or ReviewBundleStore()
    state = ((brain_data.get("bootstrap") or {}).get("source_states") or {}).get(source_version_id) or {}
    with store.store.mutate() as data:
        if not data.get("schema"):
            data.update(_blank())
        record = _ensure_bundle_from_source(data, brain_data, source_version_id, state)
        _recount(data)
        return safe_copy(record) if record else None


def queue_stats(store: ReviewBundleStore | None = None) -> dict[str, Any]:
    store = store or ReviewBundleStore()
    data = store.load()
    _recount(data)
    gate = compute_review_drain_gate(store=store)
    patrol_gate = compute_patrol_resume_gate()
    bundles = data.get("bundles") or {}
    state_counts: dict[str, int] = {}
    for bundle in bundles.values():
        if isinstance(bundle, Mapping):
            key = text(bundle.get("state")) or "UNKNOWN"
            state_counts[key] = state_counts.get(key, 0) + 1
    from paw_runtime_state_projection import HISTORICAL_REVIEW_ARCHIVE, historical_review_projection
    archive = historical_review_projection(
        total_bundles=len(bundles),
        state_counts=state_counts,
        metrics=data.get("metrics") or {},
    )
    payload = {
        "schema": SCHEMA,
        "policy_id": POLICY_ID,
        "review_pending": data["metrics"]["review_pending"],
        "clear": data["metrics"]["clear"],
        "opus_required": data["metrics"]["opus_required"],
        "user_decision": data["metrics"]["user_decision"],
        "finalized": data["metrics"]["finalized"],
        "policy_write_ready": data["metrics"]["policy_write_ready"],
        "written": data["metrics"]["written"],
        "no_action": data["metrics"]["no_action"],
        "insufficient_evidence": data["metrics"]["insufficient_evidence"],
        "total_bundles": len(bundles),
        "opus_clusters": data["metrics"]["opus_clusters"],
        "user_decision_clusters": data["metrics"]["user_decision_clusters"],
        "formal_state_bypass": 0,
        **gate,
        "daily_capture_patrol_enabled": patrol_gate.get("daily_capture_patrol_enabled"),
        "daily_capture_patrol_blocked_reason": patrol_gate.get("daily_capture_patrol_blocked_reason"),
        "incremental_mirror_patrol_enabled": patrol_gate.get("incremental_mirror_patrol_enabled"),
        "incremental_mirror_patrol_blocked_reason": patrol_gate.get("incremental_mirror_patrol_blocked_reason"),
        "lifecycle": HISTORICAL_REVIEW_ARCHIVE,
        "historical_review_archive": archive,
        "historical_review_counts": archive,
        "historical_unresolved_counts": archive["historical_unresolved_counts"],
        "counts_as_current_production_backlog": False,
        "current_post_opus_claimable": archive["current_post_opus_claimable"],
    }
    store_path = str(getattr(getattr(store, "store", None), "path", "") or "")
    if not store_path or store_path == str(default_store_path()):
        try:
            from paw_runtime_state_projection import project_runtime_state
            live = project_runtime_state(patrol_gate=patrol_gate)
            payload["operational_plane"] = live.get("operational_plane")
            payload["current_incremental_patrol_status"] = live.get("current_incremental_patrol_status")
            payload["current_incremental_backlog"] = live.get("current_incremental_backlog")
            payload["current_capture_backlog"] = live.get("current_capture_backlog")
            payload["historical_bootstrap_status"] = live.get("historical_bootstrap_status")
        except Exception:
            payload["current_capture_backlog"] = None
    return payload


def _public_bundle(bundle: Mapping[str, Any], claim: Mapping[str, Any] | None = None) -> dict[str, Any]:
    pre = get_bundle_preanalysis(bundle)
    display = bundle.get("review_display") if isinstance(bundle.get("review_display"), Mapping) else _bundle_review_display(bundle)
    # Internal evidence is retained in the sidecar record but is not part of
    # the user projection rendered by clients.
    user_display = {k: safe_copy(v) for k, v in display.items() if k != "internal_evidence"}
    return {
        "bundle_id": bundle.get("id"),
        "source_version_id": bundle.get("source_version_id"),
        "state": bundle.get("state"),
        "coverage_state": bundle.get("coverage_state"),
        "fingerprint": bundle.get("fingerprint"),
        "role": "luna_preanalysis",
        "is_authoritative": bool(bundle.get("is_authoritative")),
        "luna_preanalysis": pre,
        "gpt_authoritative_consolidation": safe_copy(bundle.get("gpt_authoritative_consolidation")),
        "proposal_ids": list(bundle.get("proposal_ids") or []),
        "finding_ids": list(bundle.get("finding_ids") or []),
        "knowledge_proposals": safe_copy(bundle.get("knowledge_proposals") or []),
        "project_proposals": safe_copy(bundle.get("project_proposals") or []),
        "metric_proposals": safe_copy(bundle.get("metric_proposals") or []),
        "findings": safe_copy(bundle.get("findings") or []),
        "evidence_refs": safe_copy(bundle.get("evidence_refs") or []),
        "source_refs": list(bundle.get("source_refs") or []),
        "provenance": safe_copy(bundle.get("provenance") or {}),
        "opus_cluster_id": bundle.get("opus_cluster_id"),
        "user_decision_cluster_id": bundle.get("user_decision_cluster_id"),
        "claim": safe_copy(claim or bundle.get("claim")),
        "state_revision": int(bundle.get("state_revision") or 0),
        "last_transition_actor": bundle.get("last_transition_actor"),
        "last_transition_at": bundle.get("last_transition_at"),
        "last_transition_reason": bundle.get("last_transition_reason"),
        "zero_findings": not bool(bundle.get("finding_ids")),
        "has_writable_proposals": bool(bundle.get("proposal_ids")),
        "review_display": user_display,
        "review_display_language_contract": "PASS" if not validate_review_display(user_display) else "FAIL",
        "raw_evidence_preserved": True,
        "internal_evidence_ref": "bundle.review_display.internal_evidence.raw_output",
    }


def _new_claim(worker_id: str, kind: str, *, claimed_state_revision: int = 0) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=CLAIM_LEASE_SECONDS)
    return {
        "claim_id": uuid.uuid4().hex,
        "worker_id": worker_id,
        "claim_owner": worker_id,
        "claimed_at": now.isoformat(),
        "claim_started_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "claim_expires_at": expires.isoformat(),
        "claimed_state_revision": int(claimed_state_revision),
        "kind": kind,
    }


def claim_review_bundles(
    *,
    limit: int = 4,
    worker_id: str = "chatgpt-main-review",
    store: ReviewBundleStore | None = None,
    brain_data: Mapping[str, Any] | None = None,
    respect_risk_router: bool | None = None,
) -> dict[str, Any]:
    store = store or ReviewBundleStore()
    limit = max(1, min(int(limit), MAX_CLAIM))
    worker_id = text(worker_id) or "chatgpt-main-review"
    if respect_risk_router is None:
        respect_risk_router = _risk_router_drain_enabled()
    gate = compute_review_drain_gate(store=store, brain_data=brain_data, worker_id=worker_id)
    if not gate.get("authoritative_review_drain_enabled"):
        # Ordinary MCP calls must not migrate/claim historical bundles.  Keep
        # the existing sidecar untouched and return the stable gate reason.
        result = {
            "items": [],
            "count": 0,
            "worker_id": worker_id,
            "formal_state_bypass": 0,
            "blocked": True,
            "reason": gate.get("authoritative_review_drain_blocked_reason"),
        }
        return _attach_review_gate_fields(result, gate=gate)
    if brain_data is not None:
        migrate_from_brain(brain_data, store=store, release_finding_claims=False)
    policy = _review_claim_policy()
    max_active = int(policy["max_active_claims"])
    with store.store.mutate() as data:
        items: list[dict[str, Any]] = []
        active_claims = _active_review_claim_diagnostics(data)
        for bundle in data.get("bundles", {}).values():
            if len(items) >= limit:
                break
            if not isinstance(bundle, dict):
                continue
            _ensure_cas_fields(bundle)
            if bundle.get("state") != REVIEW_PENDING:
                continue
            if respect_risk_router and not _bundle_needs_gpt_review(bundle, brain_data):
                continue
            claim = bundle.get("claim") if isinstance(bundle.get("claim"), dict) else None
            if claim and not _claim_expired(claim):
                if _claim_owner(claim) != worker_id:
                    continue
            elif claim and _claim_expired(claim):
                claim = None
            if not claim:
                if not gate.get("authoritative_review_drain_enabled"):
                    continue
                if len(active_claims) >= max_active:
                    continue
                revision = int(bundle.get("state_revision") or 0)
                claim = _new_claim(worker_id, "main_review", claimed_state_revision=revision)
                bundle["claim"] = claim
                bundle["updated_at"] = utc_now()
                active_claims = _active_review_claim_diagnostics(data)
            items.append(_public_bundle(bundle, claim))
        _recount(data)
        result = {"items": items, "count": len(items), "worker_id": worker_id, "formal_state_bypass": 0}
        if not items and gate.get("authoritative_review_drain_enabled") and len(active_claims) >= max_active:
            result["blocked"] = True
            result["reason"] = "review_active_claim_limit"
        return _attach_review_gate_fields(result, gate=compute_review_drain_gate(
            store=store, brain_data=brain_data, worker_id=worker_id,
        ))


def _enqueue_policy_writes(
    data: dict[str, Any],
    bundle: dict[str, Any],
    normalized_items: list[Mapping[str, Any]],
    *,
    approval_source: str,
    preanalysis_model: str = "gpt-5.6-luna",
    authoritative_model: str = "",
) -> list[str]:
    ids: list[str] = []
    for item in normalized_items:
        normalized = item.get("normalized") if isinstance(item, Mapping) else None
        if not isinstance(normalized, Mapping):
            continue
        write_id = "policy-write:" + hash_payload({
            "bundle_id": bundle.get("id"),
            "target_type": normalized.get("target_type"),
            "operation": normalized.get("operation"),
            "payload": normalized.get("payload"),
        })[:24]
        existing = data.setdefault("policy_writes", {}).get(write_id)
        if existing and existing.get("status") in {"READY", "COMMITTED"}:
            ids.append(write_id)
            continue
        data["policy_writes"][write_id] = {
            "id": write_id,
            "bundle_id": bundle.get("id"),
            "source_version_id": bundle.get("source_version_id"),
            "status": "READY",
            "approval_source": approval_source,
            "policy_id": POLICY_ID,
            "preanalysis_model": preanalysis_model or "gpt-5.6-luna",
            "authoritative_model": authoritative_model,
            "target_type": normalized.get("target_type"),
            "operation": normalized.get("operation"),
            "payload": safe_copy(normalized.get("payload")),
            "source_refs": list(normalized.get("source_refs") or []),
            "evidence_refs": safe_copy(normalized.get("evidence_refs") or []),
            "proposal_id": normalized.get("proposal_id"),
            "expected_target_version": normalized.get("expected_target_version"),
            "created_at": utc_now(),
            "commit_ref": None,
        }
        ids.append(write_id)
    bundle["policy_write_ids"] = unique([*(bundle.get("policy_write_ids") or []), *ids])
    return ids


def _apply_clear_or_finalize(
    data: dict[str, Any],
    bundle: dict[str, Any],
    *,
    write_changes: list[Any],
    source_state: Mapping[str, Any] | None,
    approval_source: str,
    to_clear_state: str,
    preanalysis_model: str = "gpt-5.6-luna",
    authoritative_model: str = "",
    actor: str = "chatgpt-main-review",
    claim_id: str | None = None,
) -> dict[str, Any]:
    evaluated = evaluate_write_changes(write_changes, bundle=bundle, source_state=source_state)
    if write_changes and not evaluated.get("allowed"):
        raise ValueError(f"write_changes rejected by {POLICY_ID}: {evaluated.get('reason')}")
    _transition_bundle(
        bundle, to_clear_state, actor=actor, reason=f"clear_or_finalize:{to_clear_state}",
        claim_id=claim_id, bypass_claim_guard=True,
    )
    bundle["write_changes"] = safe_copy(write_changes or [])
    if write_changes:
        _enqueue_policy_writes(
            data,
            bundle,
            evaluated.get("items") or [],
            approval_source=approval_source,
            preanalysis_model=preanalysis_model,
            authoritative_model=authoritative_model,
        )
        _transition_bundle(
            bundle, POLICY_WRITE_READY, actor=actor, reason="policy_write_ready",
            claim_id=claim_id, bypass_claim_guard=True,
        )
    else:
        _transition_bundle(
            bundle, FINALIZED, actor=actor, reason="finalized_without_writes",
            claim_id=claim_id, bypass_claim_guard=True,
        )
    return evaluated


def _ensure_opus_cluster(data: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    key = _cluster_key_from_bundle(bundle)
    cluster_id = "opus-cluster:" + hash_payload({"key": key})[:24]
    cluster = data.setdefault("opus_clusters", {}).get(cluster_id)
    if cluster is None:
        cluster = {
            "id": cluster_id,
            "lineage_key": key,
            "bundle_ids": [],
            "finding_ids": [],
            "status": "PENDING",
            "effective_model_required": OPUS_MODEL_ID,
            "opus_review": None,
            "created_at": utc_now(),
        }
        data["opus_clusters"][cluster_id] = cluster
    cluster["bundle_ids"] = unique([*(cluster.get("bundle_ids") or []), bundle.get("id")])
    cluster["finding_ids"] = unique([*(cluster.get("finding_ids") or []), *(bundle.get("finding_ids") or [])])
    bundle["opus_cluster_id"] = cluster_id
    return cluster


def _ensure_user_cluster(data: dict[str, Any], bundle: dict[str, Any], reason: str) -> dict[str, Any]:
    key = _cluster_key_from_bundle(bundle)
    cluster_id = "decision-cluster:" + hash_payload({"key": key})[:24]
    cluster = data.setdefault("user_decision_clusters", {}).get(cluster_id)
    findings = bundle.get("findings") or []
    assertion = ""
    if findings and isinstance(findings[0], Mapping):
        assertion = text(findings[0].get("assertion"))
    question = text(reason) or assertion or "GPT and Opus could not safely resolve this business decision."
    if cluster is None:
        cluster = {
            "id": cluster_id,
            "lineage_key": key,
            "bundle_ids": [],
            "finding_ids": [],
            "status": "OPEN",
            "candidate_id": None,
            "review_decision_id": None,
            "decision_question": question,
            "alternatives": [],
            "why_unresolved": reason,
            "affected_entities": unique([
                *(item for f in findings if isinstance(f, Mapping) for item in (f.get("affected_targets") or [])),
                *(p.get("canonical_topic") for p in (bundle.get("knowledge_proposals") or []) if isinstance(p, Mapping)),
            ]),
            "evidence_refs": safe_copy(bundle.get("evidence_refs") or []),
            "impact": "Canonical Knowledge/Project/Data write is blocked until one user receipt is recorded.",
            "created_at": utc_now(),
        }
        data["user_decision_clusters"][cluster_id] = cluster
    cluster["bundle_ids"] = unique([*(cluster.get("bundle_ids") or []), bundle.get("id")])
    cluster["finding_ids"] = unique([*(cluster.get("finding_ids") or []), *(bundle.get("finding_ids") or [])])
    cluster["review_display"] = normalize_user_decision_card(cluster)
    bundle["user_decision_cluster_id"] = cluster_id
    bundle["review_display"] = _bundle_review_display(bundle, stage="user_decision")
    return cluster


def record_bundle_review(
    *,
    bundle_id: str,
    claim_id: str,
    outcome: str,
    reason: str = "",
    write_changes: list[Any] | None = None,
    worker_id: str | None = None,
    model_identity: str | None = None,
    action_on_luna_proposal: str | None = None,
    expected_revision: int | None = None,
    store: ReviewBundleStore | None = None,
    source_state: Mapping[str, Any] | None = None,
    brain: Any | None = None,
) -> dict[str, Any]:
    store = store or ReviewBundleStore()
    outcome = text(outcome).upper()
    if outcome not in BUNDLE_OUTCOMES:
        raise ValueError("unsupported bundle review outcome")
    write_changes = write_changes or []
    gate = compute_review_drain_gate(
        store=store,
        bundle_id=bundle_id,
        claim_id=claim_id,
        worker_id=worker_id,
    )
    if not gate.get("authoritative_review_drain_enabled"):
        return _persist_response(
            PERSIST_BLOCKED_BEFORE_CAS,
            claim_id=claim_id or None,
            code="review_record_gate_blocked",
            cas_result="BLOCKED_BEFORE_CAS",
            forward_only_mutation_allowed=False,
            **_attach_review_gate_fields({}, gate={**gate, "review_record_gate": "FAIL"}),
        )
    with store.store.mutate() as data:
        bundle = data.get("bundles", {}).get(bundle_id)
        if not isinstance(bundle, dict):
            raise KeyError(bundle_id)
        _ensure_cas_fields(bundle)
        claim = bundle.get("claim") if isinstance(bundle.get("claim"), dict) else {}
        claimed_worker = text(worker_id) or _claim_owner(claim)
        expected = expected_revision
        if expected is None and claim:
            expected = int(claim.get("claimed_state_revision") if claim.get("claimed_state_revision") is not None else bundle.get("state_revision") or 0)

        action = text(action_on_luna_proposal).upper()
        if action not in {"ACCEPT", "REVISE", "REJECT"}:
            if outcome == CLEAR:
                action = "REVISE" if (write_changes and any(isinstance(w, Mapping) and (w.get("revised") or w.get("revision")) for w in write_changes)) else "ACCEPT"
            else:
                action = "REJECT"
        result_key = _review_result_key(outcome, reason, write_changes, action)
        prior_review = bundle.get("review") if isinstance(bundle.get("review"), Mapping) else {}
        prior_claim_id = text((prior_review or {}).get("claim_id") or (bundle.get("persisted_review") or {}).get("claim_id"))
        if prior_review.get("outcome") and prior_claim_id == text(claim_id):
            prior_key = text((bundle.get("persisted_review") or {}).get("result_key"))
            if prior_key == result_key or (
                text(prior_review.get("outcome")).upper() == outcome
                and text(prior_review.get("action_on_luna_proposal")).upper() == action
            ):
                snapshot = safe_copy(bundle)
                return _persist_response(PERSIST_ALREADY, bundle=snapshot, claim_id=claim_id, outcome=outcome)

        if bundle.get("state") != REVIEW_PENDING:
            return _persist_response(
                PERSIST_STATE_CHANGED,
                bundle=bundle,
                claim_id=claim_id,
                **_state_changed_details(bundle, expected),
            )
        if not claim or text(claim.get("claim_id")) != text(claim_id):
            return _persist_response(PERSIST_INVALID_CLAIM, bundle=bundle, claim_id=claim_id)
        if _claim_expired(claim):
            return _persist_response(PERSIST_CLAIM_EXPIRED, bundle=bundle, claim_id=claim_id)
        if _claim_owner(claim) != claimed_worker:
            return _persist_response(PERSIST_INVALID_CLAIM, bundle=bundle, claim_id=claim_id, claim_owner=_claim_owner(claim))
        current_revision = int(bundle.get("state_revision") or 0)
        if expected is not None and current_revision != int(expected):
            return _persist_response(
                PERSIST_STATE_CHANGED,
                bundle=bundle,
                claim_id=claim_id,
                **_state_changed_details(bundle, expected),
            )
        if outcome == CLEAR and not worker_may_record_clear(claimed_worker):
            raise ValueError("CLEAR may only be recorded by the ChatGPT-owned review worker")

        model_id = text(model_identity).strip()
        if not model_id:
            try:
                from paw_gpt_invocation import load_configured_authoritative_model
                cfg_model, _ = load_configured_authoritative_model()
                if cfg_model:
                    model_id = cfg_model
            except Exception:
                pass
        if outcome == CLEAR:
            if not model_id or model_id == "WAITING_GPT":
                raise ValueError("authoritative GPT review requires a real model identity; fail closed")
            try:
                from paw_gpt_invocation import validate_authoritative_model
                model_valid, model_reason = validate_authoritative_model(model_id)
            except Exception:
                model_valid, model_reason = False, "authoritative model validation unavailable"
            if not model_valid:
                raise ValueError(f"forbidden model identity {model_id} cannot act as authoritative GPT reviewer: {model_reason}")
            bundle["is_authoritative"] = True

        pre = get_bundle_preanalysis(bundle, source_state=source_state)
        pre_model = pre.get("model") or "gpt-5.6-luna"

        authoritative_record = {
            "model": model_id,
            "role": "gpt_authoritative_consolidation",
            "is_authoritative": bool(bundle.get("is_authoritative")),
            "outcome": outcome,
            "action_on_luna_proposal": action,
            "reason": text(reason),
            "write_changes": safe_copy(write_changes or []),
            "preanalysis_model": pre_model,
            "recorded_at": utc_now(),
        }
        bundle["gpt_authoritative_consolidation"] = authoritative_record
        bundle["review"] = {
            "outcome": outcome,
            "action_on_luna_proposal": action,
            "reason": text(reason),
            "worker_id": claimed_worker,
            "model_identity": model_id,
            "claim_id": text(claim_id),
            "recorded_at": utc_now(),
        }
        bundle["persisted_review"] = {
            "claim_id": text(claim_id),
            "result_key": result_key,
            "outcome": outcome,
            "recorded_at": utc_now(),
        }
        opus_cluster = None
        user_cluster = None
        transition_actor = claimed_worker or "chatgpt-main-review"
        if outcome == CLEAR:
            _apply_clear_or_finalize(
                data, bundle, write_changes=write_changes, source_state=source_state,
                approval_source=APPROVAL_GPT_CLEAR, to_clear_state=CLEAR,
                preanalysis_model=pre_model, authoritative_model=model_id,
                actor=transition_actor, claim_id=text(claim_id),
            )
        elif outcome == OPUS_REQUIRED:
            _transition_bundle(
                bundle, OPUS_REQUIRED, actor=transition_actor, reason=text(reason) or "opus_required",
                claim_id=text(claim_id), bypass_claim_guard=True,
            )
            opus_cluster = _ensure_opus_cluster(data, bundle)
        elif outcome == USER_DECISION_REQUIRED:
            _transition_bundle(
                bundle, USER_DECISION_REQUIRED, actor=transition_actor, reason=text(reason) or "user_decision_required",
                claim_id=text(claim_id), bypass_claim_guard=True,
            )
            if write_changes:
                evaluated = evaluate_write_changes(write_changes, bundle=bundle, source_state=source_state)
                if not evaluated.get("allowed"):
                    raise ValueError(f"write_changes rejected by {POLICY_ID}: {evaluated.get('reason')}")
                bundle["write_changes"] = safe_copy(write_changes)
            user_cluster = _ensure_user_cluster(data, bundle, text(reason))
        else:
            _transition_bundle(
                bundle, outcome, actor=transition_actor, reason=text(reason) or outcome.lower(),
                claim_id=text(claim_id), bypass_claim_guard=True,
            )
            _transition_bundle(
                bundle, FINALIZED, actor=transition_actor, reason="finalized_after_review",
                claim_id=text(claim_id), bypass_claim_guard=True,
            )
        bundle["review_display"] = _bundle_review_display(bundle, stage="gpt_review_outcome")
        bundle["claim"] = None
        _recount(data)
        snapshot = safe_copy(bundle)
        if opus_cluster is None and bundle.get("opus_cluster_id"):
            opus_cluster = data.get("opus_clusters", {}).get(bundle.get("opus_cluster_id"))
        if user_cluster is None and bundle.get("user_decision_cluster_id"):
            user_cluster = data.get("user_decision_clusters", {}).get(bundle.get("user_decision_cluster_id"))
        opus_cluster = safe_copy(opus_cluster) if opus_cluster else None
        user_cluster = safe_copy(user_cluster) if user_cluster else None
    if brain is not None:
        _sync_brain_after_review(brain, snapshot, opus_cluster)
    result = _persist_response(PERSIST_PERSISTED, bundle=snapshot, claim_id=claim_id, outcome=outcome)
    result.update(safe_copy(snapshot))
    result["opus_cluster"] = opus_cluster
    result["user_decision_cluster"] = user_cluster
    return result


def record_risk_router_outcome(
    *,
    bundle_id: str,
    outcome: str,
    reason: str = "",
    write_changes: list[Any] | None = None,
    risk_decision: Mapping[str, Any] | None = None,
    store: ReviewBundleStore | None = None,
    source_state: Mapping[str, Any] | None = None,
    brain: Any | None = None,
) -> dict[str, Any]:
    """Deterministic risk-router path. No GPT claim/model required. Forward-only."""
    store = store or ReviewBundleStore()
    outcome = text(outcome).upper()
    if outcome not in BUNDLE_OUTCOMES:
        raise ValueError("unsupported risk router outcome")
    write_changes = write_changes or []
    worker_id = RISK_ROUTER_WORKER
    with store.store.mutate() as data:
        bundle = data.get("bundles", {}).get(bundle_id)
        if not isinstance(bundle, dict):
            raise KeyError(bundle_id)
        _ensure_cas_fields(bundle)
        if bundle.get("state") != REVIEW_PENDING:
            return _persist_response(
                PERSIST_STATE_CHANGED,
                bundle=bundle,
                claim_id=None,
                **_state_changed_details(bundle, None),
            )

        action = "ACCEPT"
        if outcome == CLEAR:
            action = "REVISE" if write_changes else "ACCEPT"
        elif outcome in {NO_ACTION, INSUFFICIENT_EVIDENCE, OPUS_REQUIRED, USER_DECISION_REQUIRED}:
            action = "REJECT"

        if risk_decision:
            bundle["risk_route"] = {
                "route": risk_decision.get("route"),
                "risk_level": risk_decision.get("risk_level"),
                "reason_codes": list(risk_decision.get("reason_codes") or []),
                "classified_at": risk_decision.get("classified_at"),
                "downstream_action": risk_decision.get("downstream_action"),
                "write_plan_count": len(risk_decision.get("write_plan") or []),
                "project_id": risk_decision.get("project_id"),
                "canonical_topic": risk_decision.get("canonical_topic"),
                "version_relation": risk_decision.get("version_relation"),
                "conflict_flags": list(risk_decision.get("conflict_flags") or []),
            }
            bundle["risk_decision"] = safe_copy(dict(risk_decision))

        pre = get_bundle_preanalysis(bundle, source_state=source_state)
        pre_model = pre.get("model") or "gpt-5.6-luna"
        if outcome == CLEAR:
            bundle["is_authoritative"] = True

        bundle["gpt_authoritative_consolidation"] = {
            "model": RISK_ROUTER_MODEL,
            "role": "risk_router_decision",
            "is_authoritative": bool(bundle.get("is_authoritative")),
            "outcome": outcome,
            "action_on_luna_proposal": action,
            "reason": text(reason),
            "write_changes": safe_copy(write_changes or []),
            "preanalysis_model": pre_model,
            "recorded_at": utc_now(),
        }
        bundle["review"] = {
            "outcome": outcome,
            "action_on_luna_proposal": action,
            "reason": text(reason),
            "worker_id": worker_id,
            "model_identity": RISK_ROUTER_MODEL,
            "claim_id": None,
            "recorded_at": utc_now(),
            "via": "risk_router",
        }
        bundle["persisted_review"] = {
            "claim_id": None,
            "result_key": _review_result_key(outcome, reason, write_changes, action),
            "outcome": outcome,
            "recorded_at": utc_now(),
        }

        opus_cluster = None
        user_cluster = None
        if outcome == CLEAR:
            _apply_clear_or_finalize(
                data, bundle, write_changes=write_changes, source_state=source_state,
                approval_source=APPROVAL_RISK_ROUTER, to_clear_state=CLEAR,
                preanalysis_model=pre_model, authoritative_model=RISK_ROUTER_MODEL,
                actor=worker_id, claim_id=None,
            )
        elif outcome == OPUS_REQUIRED:
            _transition_bundle(
                bundle, OPUS_REQUIRED, actor=worker_id, reason=text(reason) or "risk_router_opus_required",
                bypass_claim_guard=True,
            )
            opus_cluster = _ensure_opus_cluster(data, bundle)
        elif outcome == USER_DECISION_REQUIRED:
            _transition_bundle(
                bundle, USER_DECISION_REQUIRED, actor=worker_id, reason=text(reason) or "risk_router_user_decision",
                bypass_claim_guard=True,
            )
            user_cluster = _ensure_user_cluster(data, bundle, text(reason))
        else:
            _transition_bundle(
                bundle, outcome, actor=worker_id, reason=text(reason) or outcome.lower(),
                bypass_claim_guard=True,
            )
            _transition_bundle(
                bundle, FINALIZED, actor=worker_id, reason="risk_router_finalized",
                bypass_claim_guard=True,
            )
        bundle["review_display"] = _bundle_review_display(bundle, stage="risk_router_outcome")
        bundle["claim"] = None
        _recount(data)
        snapshot = safe_copy(bundle)
        if opus_cluster is None and bundle.get("opus_cluster_id"):
            opus_cluster = data.get("opus_clusters", {}).get(bundle.get("opus_cluster_id"))
        if user_cluster is None and bundle.get("user_decision_cluster_id"):
            user_cluster = data.get("user_decision_clusters", {}).get(bundle.get("user_decision_cluster_id"))
        opus_cluster = safe_copy(opus_cluster) if opus_cluster else None
        user_cluster = safe_copy(user_cluster) if user_cluster else None
    if brain is not None:
        _sync_brain_after_review(brain, snapshot, opus_cluster)
    result = _persist_response(PERSIST_PERSISTED, bundle=snapshot, claim_id=None, outcome=outcome)
    result.update(safe_copy(snapshot))
    result["opus_cluster"] = opus_cluster
    result["user_decision_cluster"] = user_cluster
    return result


def _brain_lock_path(brain: Any) -> Path:
    lock_path = getattr(brain.store, "lock_path", None)
    if lock_path is not None:
        return Path(lock_path)
    store_path = Path(getattr(brain.store, "path", default_brain_path()))
    return store_path.with_name(f".{store_path.name}.lock")


def _sync_brain_after_review(brain: Any, bundle: Mapping[str, Any], opus_cluster: Mapping[str, Any] | None) -> None:
    """Best-effort Brain metrics/finding sync. Never blocks MCP on Brain flock."""
    if brain is None:
        return
    lock_fh = None
    try:
        lock_path = _brain_lock_path(brain)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fh = open(lock_path, "a+", encoding="utf-8")
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        data = brain._data()
        metrics = data.setdefault("bootstrap", {}).setdefault("metrics", {})
        state = text(bundle.get("state"))
        review_outcome = (bundle.get("review") or {}).get("outcome") if isinstance(bundle.get("review"), Mapping) else None
        post_outcome = (bundle.get("post_opus") or {}).get("outcome") if isinstance(bundle.get("post_opus"), Mapping) else None
        if review_outcome == CLEAR or post_outcome in {CLEAR, "FINALIZE"}:
            metrics["gpt_review_clear"] = int(metrics.get("gpt_review_clear") or 0) + 1
        if state == OPUS_REQUIRED and opus_cluster:
            cid = opus_cluster.get("id")
            opus = data.setdefault("bootstrap", {}).setdefault("opus", {})
            pending = list(opus.get("pending_items") or [])
            if cid and cid not in pending and cid not in (opus.get("completed_items") or []):
                pending.append(cid)
                opus["pending_items"] = pending
                opus["resume_state"] = "PENDING"
                metrics["opus_required"] = int(metrics.get("opus_required") or 0) + 1
        if state == USER_DECISION_REQUIRED:
            metrics["user_decision_required"] = int(metrics.get("user_decision_required") or 0) + 1
        if state == FINALIZED:
            metrics["finalized_items"] = int(metrics.get("finalized_items") or 0) + 1
        if state == POLICY_WRITE_READY:
            metrics["write_ready"] = int(metrics.get("write_ready") or 0) + 1
        for fid in bundle.get("finding_ids") or []:
            try:
                finding = data.get("findings", {}).get(fid)
                if not isinstance(finding, dict) or finding.get("state") != "GPT_ANALYZED":
                    continue
                brain._transition(finding, "GPT_SELF_REVIEWED")
                if state == OPUS_REQUIRED:
                    brain._transition(finding, "EXPERT_REVIEW_REQUIRED")
                elif state == USER_DECISION_REQUIRED:
                    brain._transition(finding, "USER_DECISION_REQUIRED")
                else:
                    brain._transition(finding, "FINALIZED")
            except (KeyError, TypeError, ValueError):
                # A historical finding may already have been advanced by
                # another governed consumer.  Preserve the durable cluster
                # enqueue and fail closed for this individual finding rather
                # than dropping the whole Brain queue update.
                continue
        brain._save(data)
    except Exception:
        return
    finally:
        if lock_fh is not None:
            try:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            lock_fh.close()


def claim_post_opus_work(
    *,
    limit: int = 1,
    worker_id: str = "chatgpt-post-opus",
    store: ReviewBundleStore | None = None,
) -> dict[str, Any]:
    store = store or ReviewBundleStore()
    limit = max(1, min(int(limit), MAX_CLAIM))
    worker_id = text(worker_id) or "chatgpt-post-opus"
    with store.store.mutate() as data:
        items: list[dict[str, Any]] = []
        for bundle in data.get("bundles", {}).values():
            if len(items) >= limit:
                break
            if bundle.get("state") not in {POST_OPUS_PENDING, OPUS_REVIEWED}:
                continue
            if bundle.get("state") == OPUS_REVIEWED:
                _transition_bundle(
                    bundle, POST_OPUS_PENDING, actor=worker_id, reason="claim_post_opus_work",
                )
            claim = bundle.get("post_opus_claim") if isinstance(bundle.get("post_opus_claim"), dict) else None
            if claim and not _claim_expired(claim) and text(claim.get("worker_id")) != worker_id:
                continue
            if not claim or _claim_expired(claim):
                claim = _new_claim(worker_id, "post_opus", claimed_state_revision=int(bundle.get("state_revision") or 0))
                bundle["post_opus_claim"] = claim
            cluster = data.get("opus_clusters", {}).get(bundle.get("opus_cluster_id")) or {}
            item = _public_bundle(bundle, claim)
            item["opus_review"] = safe_copy(cluster.get("opus_review"))
            item["opus_cluster_id"] = cluster.get("id") or bundle.get("opus_cluster_id")
            items.append(item)
        return {"items": items, "count": len(items), "worker_id": worker_id, "formal_state_bypass": 0}


def record_post_opus_bundle(
    *,
    bundle_id: str,
    claim_id: str,
    outcome: str,
    reason: str = "",
    write_changes: list[Any] | None = None,
    worker_id: str | None = None,
    model_identity: str | None = None,
    action_on_luna_proposal: str | None = None,
    store: ReviewBundleStore | None = None,
    source_state: Mapping[str, Any] | None = None,
    brain: Any | None = None,
) -> dict[str, Any]:
    store = store or ReviewBundleStore()
    outcome = text(outcome).upper()
    if outcome not in POST_OPUS_OUTCOMES:
        raise ValueError("unsupported post-Opus bundle outcome")
    write_changes = write_changes or []
    with store.store.mutate() as data:
        bundle = data.get("bundles", {}).get(bundle_id)
        if not isinstance(bundle, dict):
            raise KeyError(bundle_id)
        _ensure_cas_fields(bundle)
        claim = bundle.get("post_opus_claim") if isinstance(bundle.get("post_opus_claim"), dict) else {}
        claimed_worker = text(worker_id) or _claim_owner(claim) or "chatgpt-post-opus"
        if bundle.get("state") == OPUS_REVIEWED:
            _transition_bundle(bundle, POST_OPUS_PENDING, actor=claimed_worker, reason="post_opus_record_prep")
        if bundle.get("state") != POST_OPUS_PENDING:
            raise ValueError("post-Opus consolidation is only allowed from POST_OPUS_PENDING")
        if text(claim.get("claim_id")) != text(claim_id):
            raise ValueError("valid post-Opus claim required")
        if outcome in {CLEAR, "FINALIZE"} and not (
            worker_may_record_clear(claimed_worker) and worker_may_record_clear(text(claim.get("worker_id")))
        ):
            raise ValueError("post-Opus CLEAR may only be recorded by the ChatGPT-owned worker")

        model_id = text(model_identity).strip()
        if not model_id:
            try:
                from paw_gpt_invocation import load_configured_authoritative_model
                cfg_model, _ = load_configured_authoritative_model()
                if cfg_model:
                    model_id = cfg_model
            except Exception:
                pass
        auth_model = model_id or claimed_worker

        bundle["post_opus"] = {"outcome": outcome, "reason": text(reason), "worker_id": claimed_worker, "model_identity": auth_model, "recorded_at": utc_now()}
        user_cluster = None
        pre = get_bundle_preanalysis(bundle, source_state=source_state)
        pre_model = pre.get("model") or "gpt-5.6-luna"
        action = text(action_on_luna_proposal).upper()
        if action not in {"ACCEPT", "REVISE", "REJECT"}:
            if outcome in {CLEAR, "FINALIZE"}:
                action = "REVISE" if (write_changes and any(isinstance(w, Mapping) and (w.get("revised") or w.get("revision")) for w in write_changes)) else "ACCEPT"
            else:
                action = "REJECT"

        if outcome in {CLEAR, "FINALIZE"}:
            bundle["is_authoritative"] = True
            bundle["gpt_authoritative_consolidation"] = {
                "model": auth_model,
                "role": "post_opus_consolidation",
                "is_authoritative": True,
                "outcome": outcome,
                "action_on_luna_proposal": action,
                "reason": text(reason),
                "write_changes": safe_copy(write_changes or []),
                "preanalysis_model": pre_model,
                "recorded_at": utc_now(),
            }
            _apply_clear_or_finalize(
                data, bundle, write_changes=write_changes, source_state=source_state,
                approval_source=APPROVAL_OPUS_CLEAR, to_clear_state=POST_OPUS_CLEAR,
                preanalysis_model=pre_model, authoritative_model=auth_model,
                actor=claimed_worker or "chatgpt-post-opus", claim_id=text(claim_id),
            )
        elif outcome == USER_DECISION_REQUIRED:
            _transition_bundle(
                bundle, USER_DECISION_REQUIRED, actor=claimed_worker or "chatgpt-post-opus",
                reason=text(reason) or "user_decision_required",
            )
            if write_changes:
                evaluated = evaluate_write_changes(write_changes, bundle=bundle, source_state=source_state)
                if not evaluated.get("allowed"):
                    raise ValueError(f"write_changes rejected by {POLICY_ID}: {evaluated.get('reason')}")
                bundle["write_changes"] = safe_copy(write_changes)
            user_cluster = _ensure_user_cluster(data, bundle, text(reason))
        else:
            if outcome in {NO_ACTION, INSUFFICIENT_EVIDENCE}:
                _transition_bundle(
                    bundle, outcome, actor=claimed_worker or "chatgpt-post-opus", reason=text(reason) or outcome.lower(),
                )
            _transition_bundle(
                bundle, FINALIZED, actor=claimed_worker or "chatgpt-post-opus", reason="finalized_after_post_opus",
            )
        bundle["review_display"] = _bundle_review_display(bundle, stage="post_opus_outcome")
        bundle["post_opus_claim"] = None
        _recount(data)
        snapshot = safe_copy(bundle)
        if user_cluster is None and bundle.get("user_decision_cluster_id"):
            user_cluster = data.get("user_decision_clusters", {}).get(bundle.get("user_decision_cluster_id"))
        user_cluster = safe_copy(user_cluster) if user_cluster else None
    if brain is not None:
        _sync_brain_after_review(brain, snapshot, None)
    snapshot["user_decision_cluster"] = user_cluster
    snapshot["formal_state_bypass"] = 0
    return snapshot


def record_opus_cluster_review(
    *,
    cluster_id: str,
    effective_model: str,
    review: Mapping[str, Any],
    store: ReviewBundleStore | None = None,
    brain: Any | None = None,
) -> dict[str, Any]:
    if text(effective_model) != OPUS_MODEL_ID:
        raise ValueError(f"effective_model must be {OPUS_MODEL_ID}")
    store = store or ReviewBundleStore()
    with store.store.mutate() as data:
        cluster = data.get("opus_clusters", {}).get(cluster_id)
        if not isinstance(cluster, dict):
            raise KeyError(cluster_id)
        if cluster.get("opus_review") is not None or cluster.get("status") == "COMPLETED":
            return safe_copy(cluster)
        cluster["opus_review"] = {
            "effective_model": effective_model,
            "review": safe_copy(dict(review)),
            "recorded_at": utc_now(),
        }
        cluster["status"] = "COMPLETED"
        for bid in cluster.get("bundle_ids") or []:
            bundle = data.get("bundles", {}).get(bid)
            if isinstance(bundle, dict) and bundle.get("state") == OPUS_REQUIRED:
                _ensure_cas_fields(bundle)
                _transition_bundle(
                    bundle, OPUS_REVIEWED, actor="paw-gpt-brain-opus-dispatcher", reason=f"opus_cluster_review:{cluster_id}",
                )
                _transition_bundle(
                    bundle, POST_OPUS_PENDING, actor="paw-gpt-brain-opus-dispatcher", reason=f"opus_cluster_review:{cluster_id}",
                )
        _recount(data)
        snapshot = safe_copy(cluster)
    if brain is not None:
        _complete_brain_opus_item(brain, cluster_id)
    snapshot["formal_state_bypass"] = 0
    return snapshot


def _complete_brain_opus_item(brain: Any, cluster_id: str) -> None:
    try:
        with brain.store.exclusive_lock():
            data = brain._data()
            opus = data.setdefault("bootstrap", {}).setdefault("opus", {})
            pending = [x for x in (opus.get("pending_items") or []) if x != cluster_id]
            opus["pending_items"] = pending
            opus["completed_items"] = unique([*(opus.get("completed_items") or []), cluster_id])
            opus["last_item"] = cluster_id
            opus["checkpoint"] = int(opus.get("checkpoint") or 0) + 1
            opus["resume_state"] = "PENDING" if pending else "IDLE"
            data["bootstrap"]["metrics"]["opus_completed"] = int(data["bootstrap"]["metrics"].get("opus_completed") or 0) + 1
            brain._save(data)
    except Exception:
        return


def list_policy_write_ready(*, store: ReviewBundleStore | None = None, limit: int = 20) -> dict[str, Any]:
    store = store or ReviewBundleStore()
    data = store.load()
    items = [x for x in (data.get("policy_writes") or {}).values() if x.get("status") == "READY"]
    items.sort(key=lambda x: text(x.get("created_at")))
    return {"items": safe_copy(items[:limit]), "count": len(items), "formal_state_bypass": 0}


def mark_policy_written(
    write_id: str,
    *,
    commit_ref: Mapping[str, Any],
    store: ReviewBundleStore | None = None,
    brain: Any | None = None,
) -> dict[str, Any]:
    store = store or ReviewBundleStore()
    with store.store.mutate() as data:
        item = data.get("policy_writes", {}).get(write_id)
        if not isinstance(item, dict):
            raise KeyError(write_id)
        if item.get("status") == "COMMITTED":
            existing = item.get("commit_ref") or {}
            if isinstance(existing, Mapping) and existing.get("change_id") and existing.get("change_id") != commit_ref.get("change_id"):
                raise ValueError("policy write already COMMITTED with a different change_id")
            return safe_copy(item)
        if item.get("status") != "READY":
            raise ValueError(f"policy write is not READY: {item.get('status')}")
        if not commit_ref.get("commit_record_id") or not commit_ref.get("change_id"):
            raise ValueError("CommitRecord verification required before WRITTEN")
        item["status"] = "COMMITTED"
        item["commit_ref"] = safe_copy(dict(commit_ref))
        item["written_at"] = utc_now()
        bundle = data.get("bundles", {}).get(item.get("bundle_id"))
        bundle_state = None
        if isinstance(bundle, dict):
            remaining = [
                wid for wid in (bundle.get("policy_write_ids") or [])
                if (data.get("policy_writes") or {}).get(wid, {}).get("status") != "COMMITTED"
            ]
            if not remaining and bundle.get("state") == POLICY_WRITE_READY:
                _ensure_cas_fields(bundle)
                _transition_bundle(
                    bundle, WRITTEN, actor="paw-gpt-brain-policy-committer", reason=f"policy_write_committed:{write_id}",
                )
            bundle_state = bundle.get("state")
        _recount(data)
        snapshot = safe_copy(item)
    if brain is not None and bundle_state == WRITTEN:
        try:
            with brain.store.exclusive_lock():
                bdata = brain._data()
                bdata["bootstrap"]["metrics"]["written"] = int(bdata["bootstrap"]["metrics"].get("written") or 0) + 1
                brain._save(bdata)
        except Exception:
            pass
    snapshot["formal_state_bypass"] = 0
    return snapshot


def mark_policy_failed(write_id: str, reason: str, *, store: ReviewBundleStore | None = None) -> dict[str, Any]:
    store = store or ReviewBundleStore()
    with store.store.mutate() as data:
        item = data.get("policy_writes", {}).get(write_id)
        if not isinstance(item, dict):
            raise KeyError(write_id)
        if item.get("status") == "COMMITTED":
            return safe_copy(item)
        item["status"] = "FAILED_CLOSED"
        item["failed_reason"] = text(reason)[:1000]
        item["failed_at"] = utc_now()
        bundle = data.get("bundles", {}).get(item.get("bundle_id"))
        if isinstance(bundle, dict) and bundle.get("state") == POLICY_WRITE_READY:
            _ensure_cas_fields(bundle)
            _transition_bundle(
                bundle, BLOCKED, actor="paw-gpt-brain-policy-committer", reason=text(reason)[:500] or "policy_failed",
            )
        _recount(data)
        return safe_copy(item)


def retry_failed_policy_writes(
    *,
    store: ReviewBundleStore | None = None,
    limit: int = 100,
    reason_prefix: str = "CHECK constraint failed: approval_source",
) -> dict[str, Any]:
    """Requeue only known retryable policy failures through formal transitions.

    This is a recovery path for the historical committer process that used an
    outdated database approval-source enum.  It never changes a payload,
    creates authorization, or resets a counter: the original policy envelope
    is moved from FAILED_CLOSED back to READY and the owning bundle advances
    BLOCKED -> POLICY_WRITE_READY.  Other failure reasons remain blocked.
    """
    store = store or ReviewBundleStore()
    limit = max(1, min(int(limit), 1000))
    recovered = []
    with store.store.mutate() as data:
        writes = data.setdefault("policy_writes", {})
        for write_id, item in writes.items():
            if len(recovered) >= limit or not isinstance(item, dict):
                continue
            if item.get("status") != "FAILED_CLOSED":
                continue
            if not text(item.get("failed_reason")).startswith(reason_prefix):
                continue
            item["status"] = "READY"
            item["retry_of"] = item.get("failed_at") or item.get("created_at")
            item["requeued_at"] = utc_now()
            item["requeue_reason"] = "committer_approval_source_enum_recovered"
            bundle = data.get("bundles", {}).get(item.get("bundle_id"))
            if isinstance(bundle, dict) and bundle.get("state") == BLOCKED:
                ids = list(bundle.get("policy_write_ids") or [])
                if all((writes.get(wid) or {}).get("status") in {"READY", "COMMITTED"} for wid in ids):
                    _ensure_cas_fields(bundle)
                    _transition_bundle(
                        bundle, POLICY_WRITE_READY,
                        actor="paw-gpt-brain-policy-committer-recovery",
                        reason="retryable_policy_failure_requeued",
                    )
            recovered.append(write_id)
        _recount(data)
    return {"status": "REQUEUED", "count": len(recovered), "write_ids": recovered, "formal_state_bypass": 0}


def finalize_blocked_bundle_after_recovery(
    bundle_id: str,
    *,
    recovery_resolution: Mapping[str, Any],
    store: ReviewBundleStore | None = None,
) -> dict[str, Any]:
    """Close a blocked bundle only after an explicit governed recovery.

    The failed policy write remains in the audit trail.  This transition is
    for safe deduplication or equivalent canonical recovery where the intended
    fact is already represented; it does not manufacture a CommitRecord for
    the failed operation.
    """
    store = store or ReviewBundleStore()
    with store.store.mutate() as data:
        bundle = data.get("bundles", {}).get(bundle_id)
        if not isinstance(bundle, dict):
            raise KeyError(bundle_id)
        if bundle.get("state") != BLOCKED:
            return {"status": "SKIPPED", "bundle_id": bundle_id, "state": bundle.get("state"), "formal_state_bypass": 0}
        if not isinstance(recovery_resolution, Mapping) or not text(recovery_resolution.get("canonical_target")):
            raise ValueError("canonical recovery target is required")
        bundle["recovery_resolution"] = safe_copy(dict(recovery_resolution))
        _ensure_cas_fields(bundle)
        _transition_bundle(
            bundle, FINALIZED,
            actor="paw-governed-recovery",
            reason="blocked_bundle_resolved_by_canonical_recovery",
        )
        _recount(data)
        return {"status": "FINALIZED", "bundle_id": bundle_id, "canonical_target": recovery_resolution.get("canonical_target"), "formal_state_bypass": 0}


def record_user_decision_cluster_receipt(
    *,
    cluster_id: str,
    review_decision_id: str,
    decision: Mapping[str, Any],
    store: ReviewBundleStore | None = None,
    source_states: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    store = store or ReviewBundleStore()
    with store.store.mutate() as data:
        cluster = data.get("user_decision_clusters", {}).get(cluster_id)
        if not isinstance(cluster, dict):
            raise KeyError(cluster_id)
        if cluster.get("review_decision_id"):
            if cluster.get("review_decision_id") != review_decision_id:
                raise ValueError("user decision cluster already has a different receipt")
            return safe_copy(cluster)
        cluster["review_decision_id"] = review_decision_id
        cluster["decision"] = safe_copy(dict(decision))
        cluster["status"] = "DECIDED"
        cluster["decided_at"] = utc_now()
        for bid in cluster.get("bundle_ids") or []:
            bundle = data.get("bundles", {}).get(bid)
            if not isinstance(bundle, dict) or bundle.get("state") != USER_DECISION_REQUIRED:
                continue
            _ensure_cas_fields(bundle)
            bundle["is_authoritative"] = True
            pre = get_bundle_preanalysis(bundle)
            pre_model = pre.get("model") or "gpt-5.6-luna"
            bundle["gpt_authoritative_consolidation"] = {
                "model": "user_receipt",
                "role": "user_authority",
                "is_authoritative": True,
                "outcome": "USER_DECIDED",
                "review_decision_id": review_decision_id,
                "preanalysis_model": pre_model,
                "recorded_at": utc_now(),
            }
            _transition_bundle(bundle, USER_DECIDED, actor="user-decision-receipt", reason=f"cluster:{cluster_id}")
            writes = bundle.get("write_changes") or []
            if writes:
                source_state = (source_states or {}).get(bundle.get("source_version_id")) or {}
                evaluated = evaluate_write_changes(writes, bundle=bundle, source_state=source_state)
                if not evaluated.get("allowed"):
                    _transition_bundle(bundle, FINALIZED, actor="user-decision-receipt", reason="write_changes_rejected")
                    continue
                _enqueue_policy_writes(
                    data,
                    bundle,
                    evaluated.get("items") or [],
                    approval_source=APPROVAL_USER_RECEIPT,
                    preanalysis_model=pre_model,
                    authoritative_model="user_receipt",
                )
                _transition_bundle(bundle, POLICY_WRITE_READY, actor="user-decision-receipt", reason="policy_write_ready")
            else:
                _transition_bundle(bundle, FINALIZED, actor="user-decision-receipt", reason="finalized_without_writes")
        _recount(data)
        snapshot = safe_copy(cluster)
    snapshot["formal_state_bypass"] = 0
    return snapshot


def pending_opus_clusters(store: ReviewBundleStore | None = None) -> list[dict[str, Any]]:
    store = store or ReviewBundleStore()
    data = store.load()
    return [safe_copy(c) for c in (data.get("opus_clusters") or {}).values() if c.get("status") == "PENDING"]


def attach_user_cluster_candidate(cluster_id: str, candidate_id: str, *, store: ReviewBundleStore | None = None) -> dict[str, Any]:
    store = store or ReviewBundleStore()
    with store.store.mutate() as data:
        cluster = data.get("user_decision_clusters", {}).get(cluster_id)
        if not isinstance(cluster, dict):
            raise KeyError(cluster_id)
        if cluster.get("candidate_id") and cluster.get("candidate_id") != candidate_id:
            raise ValueError("decision cluster already bound to a different Candidate")
        cluster["candidate_id"] = candidate_id
        return safe_copy(cluster)


def release_main_review_claim(
    *,
    bundle_id: str,
    claim_id: str,
    worker_id: str,
    store: ReviewBundleStore | None = None,
    reason: str = "claim_released",
) -> dict[str, Any]:
    """Deterministic claim release without authoritative review outcome."""
    store = store or ReviewBundleStore()
    with store.store.mutate() as data:
        bundle = data.get("bundles", {}).get(bundle_id)
        if not isinstance(bundle, dict):
            raise KeyError(bundle_id)
        _ensure_cas_fields(bundle)
        claim = bundle.get("claim") if isinstance(bundle.get("claim"), Mapping) else {}
        if text(claim.get("claim_id")) != text(claim_id):
            return _persist_response(PERSIST_INVALID_CLAIM, bundle=bundle, claim_id=claim_id)
        if _claim_owner(claim) != text(worker_id):
            return _persist_response(PERSIST_INVALID_CLAIM, bundle=bundle, claim_id=claim_id)
        bundle["claim"] = None
        bundle["updated_at"] = utc_now()
        bundle["last_transition_reason"] = text(reason)[:500] or "claim_released"
        return _persist_response("RELEASED", bundle=bundle, claim_id=claim_id)


def check_main_review_transition_blocked(
    *,
    bundle_id: str,
    actor: str,
    store: ReviewBundleStore | None = None,
) -> dict[str, Any]:
    """Read-only guard probe for background writers and canary checks."""
    store = store or ReviewBundleStore()
    bundle = (store.load().get("bundles") or {}).get(bundle_id)
    if not isinstance(bundle, dict):
        raise KeyError(bundle_id)
    _ensure_cas_fields(bundle)
    active = _active_main_claim(bundle)
    blocked = _main_claim_blocks(actor, bundle)
    return {
        "bundle_id": bundle_id,
        "state": bundle.get("state"),
        "state_revision": int(bundle.get("state_revision") or 0),
        "blocked": blocked,
        "active_claim": safe_copy(active) if active else None,
        "formal_state_bypass": 0,
    }


def attempt_blocked_main_transition(
    *,
    bundle_id: str,
    target: str,
    actor: str,
    store: ReviewBundleStore | None = None,
) -> dict[str, Any]:
    """Non-destructive probe: returns blocked/error without mutating when claim guard applies."""
    store = store or ReviewBundleStore()
    try:
        with store.store.mutate() as data:
            bundle = data.get("bundles", {}).get(bundle_id)
            if not isinstance(bundle, dict):
                raise KeyError(bundle_id)
            _ensure_cas_fields(bundle)
            if bundle.get("state") != REVIEW_PENDING:
                return {"blocked": False, "reason": "not_review_pending", "state": bundle.get("state")}
            _transition_bundle(bundle, target, actor=actor, reason="blocked_probe")
        return {"blocked": False, "mutated": True}
    except ValueError as exc:
        return {"blocked": True, "reason": str(exc)[:500]}


def _load_live_brain():
    from app.gpt_brain_bootstrap import AtomicJsonStore, GPTKnowledgeBrain
    return GPTKnowledgeBrain(store=AtomicJsonStore(default_brain_path()))


def mcp_get_review_bundles(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    brain_data = None
    try:
        brain_data = _load_live_brain()._data()
    except Exception:
        brain_data = None
    return claim_review_bundles(
        limit=int(params.get("limit") or 4),
        worker_id=str(params.get("worker_id") or "chatgpt-main-review"),
        brain_data=brain_data,
    )


def mcp_record_bundle_review(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """MCP entry: persist ReviewBundle first; never block on live Brain load/sync."""
    params = params or {}
    writes = params.get("write_changes") or []
    if isinstance(writes, dict):
        writes = [writes]
    bundle_id = str(params.get("bundle_id") or "")
    worker_id = str(params.get("worker_id") or "chatgpt-main-review")
    claim_id = str(params.get("claim_id") or "")
    gate = compute_review_drain_gate(
        bundle_id=bundle_id,
        claim_id=claim_id,
        worker_id=worker_id,
    )
    if not gate.get("authoritative_review_drain_enabled"):
        blocked = _persist_response(
            PERSIST_BLOCKED_BEFORE_CAS,
            claim_id=claim_id or None,
            code="review_record_gate_blocked",
            cas_result="BLOCKED_BEFORE_CAS",
            forward_only_mutation_allowed=False,
            **_attach_review_gate_fields({}, gate={**gate, "review_record_gate": "FAIL"}),
        )
        return blocked
    if not gate.get("record_allowed_for_owned_claim") and not gate.get("concurrency_gate_passed"):
        blocked = _persist_response(
            PERSIST_BLOCKED_BEFORE_CAS,
            claim_id=claim_id or None,
            code="review_record_gate_blocked",
            cas_result="BLOCKED_BEFORE_CAS",
            forward_only_mutation_allowed=False,
            **_attach_review_gate_fields({}, gate={**gate, "review_record_gate": "FAIL"}),
        )
        return blocked
    preview = ReviewBundleStore().load().get("bundles", {}).get(bundle_id)
    source_state = _bundle_source_state(preview) if isinstance(preview, Mapping) else None
    expected_revision = params.get("expected_revision")
    try:
        expected = int(expected_revision) if expected_revision is not None else None
    except (TypeError, ValueError):
        expected = None
    try:
        result = record_bundle_review(
            bundle_id=bundle_id,
            claim_id=claim_id,
            outcome=str(params.get("outcome") or ""),
            reason=str(params.get("reason") or ""),
            write_changes=list(writes or []),
            worker_id=worker_id,
            model_identity=str(params.get("model_identity") or params.get("model") or ""),
            action_on_luna_proposal=params.get("action_on_luna_proposal"),
            expected_revision=expected,
            source_state=source_state,
            brain=None,
        )
        result["cas_result"] = result.get("status")
        result["review_record_gate"] = "PASS" if result.get("status") in {PERSIST_PERSISTED, PERSIST_ALREADY} else "FAIL"
        result["forward_only_mutation_allowed"] = result.get("status") in {PERSIST_PERSISTED, PERSIST_ALREADY}
        return _attach_review_gate_fields(result, gate=gate)
    except Exception as exc:
        return {
            "status": "REJECTED",
            "error": str(exc)[:500],
            "code": "bundle_review_rejected",
            "cas_result": "REJECTED",
            "review_record_gate": "FAIL",
            "formal_state_bypass": 0,
            **gate,
        }


def mcp_get_post_opus_work(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    return claim_post_opus_work(
        limit=int(params.get("limit") or 1),
        worker_id=str(params.get("worker_id") or "chatgpt-post-opus"),
    )


def mcp_record_post_opus_bundle(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    writes = params.get("write_changes") or []
    if isinstance(writes, dict):
        writes = [writes]
    try:
        return record_post_opus_bundle(
            bundle_id=str(params.get("bundle_id") or ""),
            claim_id=str(params.get("claim_id") or ""),
            outcome=str(params.get("outcome") or ""),
            reason=str(params.get("reason") or ""),
            write_changes=list(writes or []),
            worker_id=str(params.get("worker_id") or ""),
        )
    except Exception as exc:
        return {"error": str(exc)[:500], "code": "post_opus_rejected", "formal_state_bypass": 0}


def mcp_review_queue_stats(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return queue_stats()


def mcp_record_user_decision_cluster_receipt(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    try:
        return record_user_decision_cluster_receipt(
            cluster_id=str(params.get("cluster_id") or ""),
            review_decision_id=str(params.get("review_decision_id") or ""),
            decision=dict(params.get("decision") or {"review_decision_id": params.get("review_decision_id")}),
        )
    except Exception as exc:
        return {"error": str(exc)[:500], "code": "user_decision_receipt_rejected", "formal_state_bypass": 0}
