#!/usr/bin/env python3
"""POST_BOOTSTRAP_INCREMENTAL_PIPELINE.

This module is the durable server-side consumer for the WPS incremental
handoff.  It deliberately does not import or modify the Bootstrap runner.
The mirror owns discovery and Reader owns materialisation; this module owns
only the post-Reader state machine and adapters to the shared risk router,
Codex Luna, Terra's patrol queue, Opus/Agy and the governed committer.

The state file is a recoverable journal, not chat context.  Every transition
is forward-only (except explicit retry/reclaim transitions), all writes are
atomic, and ``formal_state_bypass`` is always zero.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from paw_evidence_materializer import materialize_evidence_refs
except Exception:  # pragma: no cover - fallback keeps the module importable
    materialize_evidence_refs = None  # type: ignore[assignment]

from gpt_brain_common import FileLeaseLock, LockedJsonStore, atomic_write_json, hash_payload, utc_now


SCHEMA = "paw-post-bootstrap-incremental-pipeline/1"
HANDOFF_SCHEMA = "paw-wps-incremental-source-queue/1"
ACTOR = "paw-post-bootstrap-incremental-pipeline"
LUNA_MODEL = "gpt-5.6-luna"
LUNA_REASONING = "high"
OPUS_MODEL = "claude-opus-4-6-thinking"
FORMAL_STATE_BYPASS = 0
MAX_ATTEMPTS = 3
# PAW v1.2 phase 3b (technical debt I): capacity / scheduling errors returned
# by codex_operator.submit_task describe the *workspace*, not this item.
# Treating them as a terminal failure turned a bounded Patrol pass into a mass
# EXPLICIT_HOLD: with MAX_CONCURRENT_WRITE_JOBS=3 the fourth submit of any
# batch returns WORKSPACE_BUSY.  They stay retryable, and never burn an attempt.
TRANSIENT_LUNA_SUBMIT_ERRORS = frozenset({
    "WORKSPACE_BUSY",
    "WORKSPACE_UNAVAILABLE",
    "LUNA_CAPACITY",
    "EXECUTION_IDENTITY_UNAVAILABLE",
})
DEFAULT_TERRA_LEASE_SECONDS = 1800
DEFAULT_OPUS_LEASE_SECONDS = 3600

DISCOVERY_OWNER = "WPS_INCREMENTAL_MIRROR"
PRIMARY_ANALYSIS_OWNER = "CHATGPT_GPT_AGENT"
INCREMENTAL_LUNA_PREANALYSIS = "DISABLED"
BOOTSTRAP_LUNA_HISTORY = "PRESERVED"
SERVER_SIDE_GPT_TRIGGER = "DISABLED"
MANUAL_CHATGPT_TRIGGER_SUPPORTED = "YES"
GPT_PATROL_TRIGGER_OWNER = "CHATGPT_GPT_AGENT_SCHEDULED_TASK"
NO_MODEL_CALL_IN_PREPARE = True
RISK_ROUTER_OWNER = "DETERMINISTIC_SHARED_ROUTER"
LOW_RISK_REVIEW_OWNER = "NONE_DETERMINISTIC_GATE"
TERRA_GOVERNANCE_IDENTITY = "CHATGPT_WORKSPACE_AGENT"
TERRA_MODEL_CONFIG = "GPT-5.6 Terra Medium"
MEDIUM_RISK_REVIEW_OWNER = TERRA_GOVERNANCE_IDENTITY
HIGH_RISK_REVIEW_OWNER = "CLAUDE_OPUS_4_6_THINKING"
USER_REVIEW_OWNER = "USER_MATERIAL_DECISIONS_ONLY"
CANONICAL_WRITE_OWNER = "DETERMINISTIC_COMMITTER_GOVERNED_WRITE_PATH"

SAFE_AUTO_APPLY = "SAFE_AUTO_APPLY"
TERRA_REVIEW_REQUIRED = "TERRA_REVIEW_REQUIRED"
OPUS_REVIEW_REQUIRED = "OPUS_REVIEW_REQUIRED"
USER_DECISION_REQUIRED = "USER_DECISION_REQUIRED"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

DISCOVERED = "DISCOVERED"
READER_READY = "READER_READY"
LUNA_PENDING = "LUNA_PENDING"
LUNA_RUNNING = "LUNA_RUNNING"
LUNA_COMPLETE = "LUNA_COMPLETE"
ROUTED = "ROUTED"
SAFE_APPLY_PENDING = "SAFE_APPLY_PENDING"
TERRA_PENDING = "TERRA_PENDING"
TERRA_RUNNING = "TERRA_RUNNING"
TERRA_COMPLETE = "TERRA_COMPLETE"
OPUS_PENDING = "OPUS_PENDING"
OPUS_RUNNING = "OPUS_RUNNING"
OPUS_COMPLETE = "OPUS_COMPLETE"
USER_DECISION_PENDING = "USER_DECISION_PENDING"
USER_DECISION_RECEIVED_WAITING_NEXT_PATROL = "USER_DECISION_RECEIVED_WAITING_NEXT_PATROL"
PATROL_READY = "PATROL_READY"
EXPLICIT_HOLD = "EXPLICIT_HOLD"
APPLYING = "APPLYING"
WRITE_COMMITTED = "WRITE_COMMITTED"
FINALIZED = "FINALIZED"

TERMINAL_STATES = frozenset({
    WRITE_COMMITTED, FINALIZED, USER_DECISION_PENDING, EXPLICIT_HOLD,
    PATROL_READY, USER_DECISION_RECEIVED_WAITING_NEXT_PATROL,
})
ROUTES = frozenset({SAFE_AUTO_APPLY, TERRA_REVIEW_REQUIRED, OPUS_REVIEW_REQUIRED,
                    USER_DECISION_REQUIRED, INSUFFICIENT_EVIDENCE, "NO_ACTION"})


class IncrementalError(RuntimeError):
    """Fail-closed incremental pipeline error."""


class RouterUnavailable(IncrementalError):
    pass


class LunaAdapter(Protocol):
    def submit(self, item: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def poll(self, item: Mapping[str, Any]) -> Mapping[str, Any]: ...


class OpusAdapter(Protocol):
    def submit(self, cluster: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def poll(self, cluster: Mapping[str, Any]) -> Mapping[str, Any]: ...


class Committer(Protocol):
    def commit(self, item: Mapping[str, Any], write_plan: list[Mapping[str, Any]]) -> Mapping[str, Any]: ...


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _now_epoch() -> float:
    return time.time()


def _future_iso(seconds: int) -> str:
    return datetime.fromtimestamp(_now_epoch() + max(1, int(seconds)), timezone.utc).isoformat()


def default_paths() -> dict[str, Path]:
    root = Path(os.environ.get("PAW_INCREMENTAL_STATE_DIR", "/var/lib/paw-mcp-gateway"))
    return {
        "state": root / "paw_incremental_pipeline.json",
        "lock": root / "paw_incremental_pipeline.lock",
        "results": root / "paw-incremental-luna-results",
        "handoff": Path(os.environ.get(
            "PAW_WPS_0600_HANDOFF_PATH",
            "/var/lib/paw-mcp-gateway/paw_wps_incremental_source_queue.json",
        )),
        "brain": Path(os.environ.get("PAW_GPT_BRAIN_STATE_PATH", "/var/lib/paw-mcp-gateway/gpt_brain_bootstrap.json")),
        "patrol_batch": Path(os.environ.get(
            "PAW_PATROL_BATCH_STORE",
            "/var/lib/paw-mcp-gateway/paw_patrol_input_batch.json",
        )),
    }


def _blank_state() -> dict[str, Any]:
    now = utc_now()
    return {
        "schema": SCHEMA,
        "status": "CREATED",
        "created_at": now,
        "updated_at": now,
        "items": {},
        "events": [],
        "metrics": {},
        "formal_state_bypass": FORMAL_STATE_BYPASS,
        "full_corpus_reread": False,
        "enabled": False,
        "enable_condition": "bootstrap.status=COMPLETED and patrol.enabled=true",
    }


def _cluster_key(item: Mapping[str, Any]) -> str:
    project = _text(item.get("project_id")) or "unresolved-project"
    topic = _text(item.get("canonical_topic")) or "unresolved-topic"
    conflict = _text(item.get("conflict_type")) or _text(item.get("version_relation")) or "none"
    metric = _text(item.get("metric_identity")) or "none"
    return "incremental:" + hash_payload({"project": project, "topic": topic, "conflict": conflict, "metric": metric})[:24]


def _source_id(item: Mapping[str, Any]) -> str:
    sid = _text(item.get("source_version_id"))
    if not sid:
        raise IncrementalError("source_version_id is required")
    return sid


def _new_item(source: Mapping[str, Any]) -> dict[str, Any]:
    sid = _source_id(source)
    now = utc_now()
    reader_path = _text(source.get("reader_result_path"))
    ready = bool(reader_path and Path(reader_path).is_file())
    item = {
        "source_version_id": sid,
        "object_id": _text(source.get("object_id")) or None,
        "source_version": _text(source.get("source_version")) or None,
        "reader_result_path": reader_path,
        "analysis_state": READER_READY if ready else DISCOVERED,
        "luna_result_ref": None,
        "risk_route": None,
        "risk_reason_codes": [],
        "cluster_key": None,
        "project_id": _text(source.get("project_id")) or None,
        "canonical_topic": _text(source.get("canonical_topic")) or None,
        "next_action": "WAIT_GPT_PATROL" if ready else "WAIT_READER",
        "attempt_count": 0,
        "claim_owner": None,
        "lease_expires_at": None,
        "last_error": None,
        "checkpoint": "reader_handoff_ingested",
        "created_at": now,
        "updated_at": now,
        "change_type": _text(source.get("change_type")),
        "sha256": _text(source.get("sha256")),
        "artifact_path": _text(source.get("artifact_path")),
        "content_fingerprint": _text(source.get("content_fingerprint")),
        "luna_job_id": None,
        "luna_token": f"PAW_LUNA_INCREMENTAL:{sid}",
        "opus_job_id": None,
        "opus_token": None,
        "terra_response": None,
        "opus_response": None,
        "commit_ref": None,
        "formal_state_bypass": FORMAL_STATE_BYPASS,
    }
    # Test/canary adapters may carry a fixture hint. Production Luna input is
    # otherwise restricted to the mirror contract fields above.
    if "test_route" in source:
        item["test_route"] = source.get("test_route")
    return item


def _transition(item: dict[str, Any], target: str, *, reason: str = "") -> None:
    current = _text(item.get("analysis_state"))
    allowed: dict[str, set[str]] = {
        DISCOVERED: {READER_READY, EXPLICIT_HOLD},
        READER_READY: {LUNA_PENDING, PATROL_READY, EXPLICIT_HOLD},
        LUNA_PENDING: {LUNA_RUNNING, LUNA_COMPLETE, EXPLICIT_HOLD},
        LUNA_RUNNING: {LUNA_COMPLETE, LUNA_PENDING, EXPLICIT_HOLD},
        LUNA_COMPLETE: {ROUTED, EXPLICIT_HOLD},
        ROUTED: {SAFE_APPLY_PENDING, PATROL_READY, TERRA_PENDING, OPUS_PENDING, USER_DECISION_PENDING, EXPLICIT_HOLD, FINALIZED},
        SAFE_APPLY_PENDING: {APPLYING, EXPLICIT_HOLD, FINALIZED},
        APPLYING: {WRITE_COMMITTED, SAFE_APPLY_PENDING, EXPLICIT_HOLD},
        TERRA_PENDING: {TERRA_RUNNING, EXPLICIT_HOLD},
        TERRA_RUNNING: {TERRA_COMPLETE, TERRA_PENDING, EXPLICIT_HOLD},
        TERRA_COMPLETE: {SAFE_APPLY_PENDING, PATROL_READY, OPUS_PENDING, USER_DECISION_PENDING, EXPLICIT_HOLD, FINALIZED},
        OPUS_PENDING: {OPUS_RUNNING, EXPLICIT_HOLD},
        OPUS_RUNNING: {OPUS_COMPLETE, OPUS_PENDING, EXPLICIT_HOLD},
        OPUS_COMPLETE: {SAFE_APPLY_PENDING, PATROL_READY, USER_DECISION_PENDING, EXPLICIT_HOLD, FINALIZED},
        USER_DECISION_PENDING: {USER_DECISION_PENDING, SAFE_APPLY_PENDING, USER_DECISION_RECEIVED_WAITING_NEXT_PATROL, EXPLICIT_HOLD, FINALIZED},
        USER_DECISION_RECEIVED_WAITING_NEXT_PATROL: {USER_DECISION_RECEIVED_WAITING_NEXT_PATROL, PATROL_READY, EXPLICIT_HOLD, FINALIZED},
        PATROL_READY: {PATROL_READY, ROUTED, TERRA_PENDING, OPUS_PENDING, USER_DECISION_PENDING, WRITE_COMMITTED, FINALIZED, EXPLICIT_HOLD},
        EXPLICIT_HOLD: {EXPLICIT_HOLD},
        FINALIZED: {FINALIZED},
        WRITE_COMMITTED: {WRITE_COMMITTED},
    }
    if current == target:
        return
    if target not in allowed.get(current, set()):
        raise IncrementalError(f"invalid forward transition {current} -> {target}")
    item["analysis_state"] = target
    item["checkpoint"] = reason or target
    item["updated_at"] = utc_now()
    if target == PATROL_READY:
        item['patrol_ready_at'] = item['updated_at']


def _materialize_evidence(
    reader_data: dict[str, Any],
    *,
    sid: str,
    reader_path: str,
    artifact_hash: str,
    content_fingerprint: str,
) -> list[dict[str, Any]]:
    """Local seam over the shared Evidence Materializer.

    Thin by design: no if/else type dispatch lives here, all capability logic is
    in `paw_evidence_materializer.materialize_evidence_refs`. This wrapper only
    exists so a missing module degrades explicitly rather than raising.

    Returns [] when nothing is materializable -> callers keep fail-closed.
    """
    if materialize_evidence_refs is None:
        raise IncrementalError("evidence_materializer_unavailable")
    return materialize_evidence_refs(
        reader_data,
        sid=sid,
        reader_path=reader_path,
        artifact_hash=artifact_hash,
        content_fingerprint=content_fingerprint,
    )


def _validate_evidence_refs(refs: list[dict[str, Any]], sid: str) -> None:
    """Evidence Identity Gate: every ref must carry this item's exact
    source_version_id and a non-empty durable locator. Cross-source or
    empty-locator refs are rejected (never silently dropped into GPT)."""
    for ref in refs:
        if _text(ref.get("source_version_id")) != sid:
            raise IncrementalError("evidence_identity_gate:cross_source_ref_rejected")
        if not _text(ref.get("evidence_locator")):
            raise IncrementalError("evidence_identity_gate:empty_locator_rejected")


def _read_json(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


USER_REVIEW_CHANNEL = "chatgpt_explicit_user"
USER_RECEIPT_SOURCE = "direct_user"
POLICY_REVIEWER = "gpt_brain_policy_engine"


def _json_mapping(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return dict(value) if isinstance(value, Mapping) else {}
    return {}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_value(raw: Any) -> Any:
    if isinstance(raw, str) and raw.strip().startswith(("[", "{")):
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return raw
    return raw


def _authorization_payload_hash(value: Mapping[str, Any]) -> str:
    # Matches the existing Committer V1.1 payload hash contract.
    return hashlib.sha256(json.dumps(dict(value), sort_keys=True).encode()).hexdigest()[:16]


def _plan_source_refs(plan: Mapping[str, Any], source_id: str = "") -> list[str]:
    raw = _json_value(plan.get("source_refs"))
    if raw is None and source_id:
        raw = [f"source_version:{source_id}"]
    if isinstance(raw, str):
        raw = [raw]
    return [_text(value) for value in (raw or []) if _text(value)]


def _plan_signature(plan: Mapping[str, Any], source_id: str = "") -> dict[str, Any]:
    """The frozen execution identity, including canonical source binding."""
    return {
        "target_type": _text(plan.get("target_type")).lower(),
        "operation": _text(plan.get("operation")).lower(),
        "payload": dict(plan.get("payload") or {}) if isinstance(plan.get("payload"), Mapping) else {},
        "source_refs": _plan_source_refs(plan, source_id),
    }


def _receipt_plans(payload: Mapping[str, Any], source_id: str = "") -> list[dict[str, Any]]:
    raw = payload.get("write_plan")
    if isinstance(raw, list):
        return [_plan_signature(x, source_id) for x in raw if isinstance(x, Mapping)]
    execution = payload.get("execution")
    if isinstance(execution, Mapping) and _text(execution.get("target_type")):
        return [_plan_signature(execution, source_id)]
    if _text(payload.get("target_type")) and _text(payload.get("operation")):
        return [_plan_signature(payload, source_id)]
    return []


def _source_tokens(value: Any) -> set[str]:
    if isinstance(value, str) and value.strip().startswith(("[", "{")):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            pass
    if isinstance(value, Mapping):
        values = [value.get("source_version_id"), *(value.get("source_version_ids") or []), *(value.get("source_refs") or [])]
    else:
        values = value if isinstance(value, (list, tuple, set)) else [value]
    result: set[str] = set()
    for raw in values:
        token = _text(raw)
        if token.startswith("source_version:"):
            token = token[len("source_version:"):]
        if token:
            result.add(token)
    return result


def _receipt_source_ids(*containers: Mapping[str, Any]) -> tuple[set[str], bool]:
    """Return source ids and whether any explicit source binding disagreed."""
    found: set[str] = set()
    mismatch = False
    for container in containers:
        if not isinstance(container, Mapping):
            continue
        for key in ("source_version_id", "source_version_ids", "source_refs"):
            if key not in container:
                continue
            values = _source_tokens(container.get(key))
            if key == "source_refs":
                values = {x for x in values if x and not x.startswith(("project:", "finding:", "candidate:"))}
            found.update(values)
    return found, mismatch


def _validate_user_receipt(
    *, db_path: str, item: Mapping[str, Any], decision: str, receipt_id: str,
    expected_plan: Mapping[str, Any] | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Verify a real direct-user ReviewDecision and its frozen execution.

    This is deliberately read-only.  The caller may only advance the durable
    incremental state after this canonical governance lookup succeeds.
    """
    if not receipt_id:
        return False, "user_approval_ref_required", {}
    sid = _text(item.get("source_version_id"))
    expected_plans = [_plan_signature(x, sid) for x in (item.get("write_plan") or []) if isinstance(x, Mapping)]
    if expected_plan is not None:
        expected_plans = [_plan_signature(expected_plan, sid)]
    if not sid or not expected_plans:
        return False, "pending_write_plan_binding_missing", {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rd = conn.execute("SELECT * FROM review_decision WHERE id = ?", (receipt_id,)).fetchone()
        if rd is None:
            return False, "user_approval_not_found", {}
        if _text(rd["approval_source"]) != USER_RECEIPT_SOURCE:
            return False, "approval_is_not_direct_user", {}
        if _text(rd["review_channel"]) != USER_REVIEW_CHANNEL or _text(rd["reviewer"]) in {"", POLICY_REVIEWER}:
            return False, "approval_is_not_explicit_user_review", {}
        if _text(rd["reviewer"]) != "chatgpt":
            return False, "approval_reviewer_not_trusted_user_session", {}
        if _text(rd["candidate_id"]):
            return False, "direct_user_approval_has_candidate_lineage", {}
        expected_decision = "reject" if decision == "REJECT" else {"APPROVE": "approve", "REJECT": "reject"}[decision]
        if _text(rd["decision"]) != expected_decision and not (decision == "APPROVE" and _text(rd["decision"]) == "edit_and_approve"):
            return False, "approval_decision_mismatch", {}

        original = _json_mapping(rd["original_payload"])
        reviewed = _json_mapping(rd["reviewed_payload"])
        payload = reviewed or original
        auth_hash = _text(rd["authorization_payload_hash"])
        if not auth_hash or auth_hash != _authorization_payload_hash(payload):
            return False, "approval_payload_hash_mismatch", {}
        receipt_plans = _receipt_plans(payload, sid)
        receipt_sources, _ = _receipt_source_ids(original, reviewed, payload)

        candidate = None
        candidate_id = _text(rd["candidate_id"])
        if candidate_id:
            candidate = conn.execute("SELECT * FROM candidate WHERE id = ?", (candidate_id,)).fetchone()
            if candidate is None:
                return False, "approval_candidate_not_found", {}
            candidate_refs = candidate["source_refs"]
            if isinstance(candidate_refs, str):
                try:
                    candidate_refs = json.loads(candidate_refs)
                except (TypeError, ValueError):
                    candidate_refs = []
            candidate_sources, _ = _receipt_source_ids(_json_mapping(candidate["proposed_change"]), {"source_refs": candidate_refs})
            receipt_sources.update(candidate_sources)
            candidate_payload = _json_mapping(candidate["proposed_change"])
            candidate_plans = _receipt_plans(candidate_payload, sid)
            if candidate_plans and candidate_plans != expected_plans:
                return False, "candidate_payload_mismatch", {}

        approved_plans: list[dict[str, Any]] = []
        approved_sources: set[str] = set()
        try:
            approved_rows = conn.execute("SELECT * FROM approved_change WHERE approval_ref = ? ORDER BY created_at", (receipt_id,)).fetchall()
        except sqlite3.OperationalError:
            return False, "approved_change_schema_unavailable", {}
        for row in approved_rows:
            approved_plans.append(_plan_signature({
                "target_type": row["target_type"],
                "operation": row["operation"],
                "payload": _json_mapping(row["payload"]),
                "source_refs": _json_value(row["source_refs"]) or [],
            }, sid))
            approved_sources.update(
                token for token in _source_tokens(row["source_refs"])
                if not token.startswith(("project:", "finding:", "candidate:"))
            )
        receipt_sources.update(approved_sources)
        if receipt_sources != {sid}:
            return False, "approval_source_version_mismatch", {}
        if receipt_plans and receipt_plans != expected_plans:
            return False, "approval_payload_mismatch", {}
        if len(approved_plans) != 1:
            return False, "approved_change_count_mismatch", {}
        if approved_plans != expected_plans:
            return False, "approved_change_payload_mismatch", {}
        if receipt_plans and receipt_plans != expected_plans:
            return False, "approval_payload_mismatch", {}
        if not approved_plans:
            return False, "approval_has_no_frozen_write_binding", {}
        return True, "user_receipt_verified", {
            "approval_ref": receipt_id,
            "reviewer": _text(rd["reviewer"]),
            "review_channel": _text(rd["review_channel"]),
            "approval_source": USER_RECEIPT_SOURCE,
            "decision": _text(rd["decision"]),
            "approved_change_id": _text(approved_rows[0]["change_id"]),
            "source_refs": _source_tokens(approved_rows[0]["source_refs"]),
        }
    except (OSError, sqlite3.Error, KeyError, TypeError, ValueError) as exc:
        return False, "user_receipt_lookup_failed:" + str(exc)[:160], {}
    finally:
        try:
            conn.close()
        except UnboundLocalError:
            pass


def _normalize_approval_refs(
    *, approval_ref: str | None, approval_refs: Any, write_plan: list[Mapping[str, Any]],
) -> list[str] | None:
    """Normalize one legacy receipt or one receipt per frozen write plan."""
    count = len(write_plan)
    if approval_refs is None:
        return [_text(approval_ref)] if count == 1 and _text(approval_ref) else None
    if approval_ref and approval_refs not in ({approval_ref}, [approval_ref], approval_ref):
        return None
    if isinstance(approval_refs, Mapping):
        values = []
        for index, plan in enumerate(write_plan):
            keys = [str(index), index, _text(plan.get("id")), _text(plan.get("write_id"))]
            value = next((approval_refs.get(key) for key in keys if key in approval_refs), None)
            if isinstance(value, Mapping):
                value = value.get("approval_ref") or value.get("review_decision_id") or value.get("user_receipt_id")
            values.append(_text(value))
        return values
    if isinstance(approval_refs, (list, tuple)):
        values = []
        for value in approval_refs:
            if isinstance(value, Mapping):
                value = value.get("approval_ref") or value.get("review_decision_id") or value.get("user_receipt_id")
            values.append(_text(value))
        return values
    return [_text(approval_refs)] if count == 1 and _text(approval_refs) else None


class SharedRiskRouterAdapter:
    """Compatibility adapter for Cursor's shared deterministic router.

    It prefers a future ``classify_incremental`` method, then adapts the
    currently present ``classify_bundle`` contract.  The adapter never owns
    risk rules.  If neither contract is available it raises, causing an
    explicit hold rather than silently inventing a second rule set.
    """

    def __init__(self, router_module: Any | None = None):
        self.module = router_module

    def _module(self) -> Any:
        if self.module is None:
            try:
                self.module = importlib.import_module("paw_bootstrap_risk_router")
            except Exception as exc:
                raise RouterUnavailable(str(exc)) from exc
        return self.module

    def classify(self, item: Mapping[str, Any], preanalysis: Mapping[str, Any]) -> dict[str, Any]:
        module = self._module()
        direct = getattr(module, "classify_incremental", None)
        if callable(direct):
            result = direct(preanalysis=dict(preanalysis), source_item=dict(item))
            return self._normalize(result, module)
        classify_bundle = getattr(module, "classify_bundle", None)
        if not callable(classify_bundle):
            raise RouterUnavailable("shared router exposes neither classify_incremental nor classify_bundle")
        bundle = dict(preanalysis.get("bundle") or {})
        bundle.setdefault("id", "incremental:" + _source_id(item))
        bundle.setdefault("source_version_id", _source_id(item))
        bundle.setdefault("state", "REVIEW_PENDING")
        bundle.setdefault("coverage_state", preanalysis.get("coverage_state") or "REVIEW_REQUIRED")
        bundle.setdefault("evidence_refs", preanalysis.get("evidence_refs") or [])
        brain = dict(preanalysis.get("brain_context") or {})
        source_state = dict(preanalysis.get("source_state") or {"state": bundle.get("coverage_state")})
        try:
            result = classify_bundle(bundle, brain, source_state=source_state)
        except TypeError:
            result = classify_bundle(bundle, brain)
        return self._normalize(result, module)

    @staticmethod
    def _normalize(result: Mapping[str, Any] | None, module: Any) -> dict[str, Any]:
        if not isinstance(result, Mapping):
            raise RouterUnavailable("shared router returned a non-object decision")
        route = _text(result.get("route"))
        aliases = {
            _text(getattr(module, "ROUTE_SAFE_AUTO_APPLY", SAFE_AUTO_APPLY)): SAFE_AUTO_APPLY,
            _text(getattr(module, "ROUTE_NO_ACTION", "NO_ACTION")): "NO_ACTION",
            _text(getattr(module, "ROUTE_TERRA_REVIEW", TERRA_REVIEW_REQUIRED)): TERRA_REVIEW_REQUIRED,
            _text(getattr(module, "ROUTE_GPT_REVIEW", "GPT_REVIEW_REQUIRED")): TERRA_REVIEW_REQUIRED,
            _text(getattr(module, "ROUTE_OPUS_REVIEW", OPUS_REVIEW_REQUIRED)): OPUS_REVIEW_REQUIRED,
            _text(getattr(module, "ROUTE_USER_DECISION", USER_DECISION_REQUIRED)): USER_DECISION_REQUIRED,
            _text(getattr(module, "ROUTE_INSUFFICIENT_EVIDENCE", INSUFFICIENT_EVIDENCE)): INSUFFICIENT_EVIDENCE,
            _text(getattr(module, "ROUTE_ALREADY_FINALIZED", "ALREADY_FINALIZED")): SAFE_AUTO_APPLY,
        }
        normalized = dict(result)
        normalized["route"] = aliases.get(route, route)
        if normalized["route"] not in ROUTES:
            raise RouterUnavailable(f"shared router returned unsupported route: {route}")
        reasons = [_text(x) for x in _as_list(result.get("reason_codes")) if _text(x)]
        if not reasons:
            raise RouterUnavailable("shared router returned no machine-readable reason_codes")
        normalized["reason_codes"] = reasons
        normalized["cluster_key"] = _text(result.get("cluster_key")) or None
        normalized["write_plan"] = [dict(x) for x in _as_list(result.get("write_plan")) if isinstance(x, Mapping)]
        return normalized


class SystemEvidenceGovernedGate:
    """Narrow authorization envelope for unattended safe evidence writes.

    This is intentionally narrower than the existing standing GPT policy:
    no new Project, no destructive operation, no rule replacement, no merge,
    no material ambiguity, and only an existing project/evidence-derived plan.
    The actual commit still goes through the existing governed committer.
    """

    POLICY_ID = "SYSTEM_EVIDENCE_GOVERNED_WRITE_V1"
    ALLOWED = frozenset({
        ("project", "update"),
        ("data", "create"),
        ("data", "revise"),
        ("knowledge", "update"),
        ("knowledge_project_link", "link"),
    })

    def validate(self, item: Mapping[str, Any], write_plan: Iterable[Mapping[str, Any]]) -> tuple[bool, str]:
        user_approved = isinstance(item.get("user_decision_approval"), Mapping) and _text(item.get("risk_route")) == USER_DECISION_REQUIRED
        if _text(item.get("risk_route")) != SAFE_AUTO_APPLY and not user_approved:
            return False, "safe_gate_route_mismatch"
        if int(item.get("formal_state_bypass") or 0) != 0:
            return False, "formal_state_bypass"
        decision = item.get("risk_decision") if isinstance(item.get("risk_decision"), Mapping) else {}
        if _text(decision.get("route")) != SAFE_AUTO_APPLY and not user_approved:
            return False, "shared_safe_route_revalidation_failed"
        if not _text(item.get("project_id")):
            return False, "existing_project_required"
        analysis = item.get("luna_result") if isinstance(item.get("luna_result"), Mapping) else {}
        # A recorded user material decision is the explicit human approval
        # that unblocks the material-decision flags; project creation and rule
        # replacement stay forbidden even with user approval.
        if bool(analysis.get("changes_effective_rule") or analysis.get("creates_project")):
            return False, "rule_or_project_creation_forbidden"
        if not user_approved and bool(analysis.get("material_business_decision") or analysis.get("unresolved_conflict")):
            return False, "material_decision_or_conflict"
        if not user_approved and bool(analysis.get("material_business_decision") or analysis.get("kpi_change") or analysis.get("policy_change") or analysis.get("personnel_or_org_choice")):
            return False, "material_meaning_change_forbidden"
        refs = item.get("evidence_refs") or analysis.get("evidence_refs") or []
        if not isinstance(refs, list) or not refs:
            return False, "immutable_evidence_refs_required"
        for ref in refs:
            if not isinstance(ref, Mapping) or _text(ref.get("source_version_id")) != _text(item.get("source_version_id")) or not _text(ref.get("evidence_locator")):
                return False, "evidence_ref_identity_or_locator_invalid"
        plans = list(write_plan)
        for plan in plans:
            target = _text(plan.get("target_type")).lower()
            operation = _text(plan.get("operation")).lower()
            if (target, operation) not in self.ALLOWED:
                return False, f"operation_not_system_allowlisted:{target}.{operation}"
            if _text(plan.get("proposal_action") or (plan.get("payload") or {}).get("action")).upper() in {"DELETE", "ARCHIVE", "RETIRE", "MERGE", "REPLACE_RULE", "CREATE_PROJECT"}:
                return False, "material_operation_marker"
            plan_refs = plan.get("evidence_refs") or refs
            if not isinstance(plan_refs, list) or not plan_refs:
                return False, "write_plan_evidence_refs_required"
            if any(_text(x.get("source_version_id")) != _text(item.get("source_version_id")) for x in plan_refs if isinstance(x, Mapping)):
                return False, "write_plan_source_identity_mismatch"
            trusted = {(_text(r.get("source_version_id")), _text(r.get("evidence_locator"))): r
                       for r in refs if isinstance(r, Mapping)}
            for r in plan_refs:
                if not isinstance(r, Mapping):
                    return False, "write_plan_evidence_refs_malformed"
                original = trusted.get((_text(r.get("source_version_id")), _text(r.get("evidence_locator"))))
                if original is None:
                    return False, "write_plan_evidence_not_validated"
                if any(k in r and r[k] != original.get(k) for k in (
                    "raw_artifact_sha256", "content_fingerprint", "reader_result_ref", "artifact_sha256")):
                    return False, "write_plan_evidence_identity_mismatch"
            payload = plan.get("payload") if isinstance(plan.get("payload"), Mapping) else {}
            if target == "project" and not _text(payload.get("id")):
                return False, "project_update_requires_existing_id"
            if target == "project" and _text(payload.get("id")) != _text(item.get("project_id")):
                return False, "project_identity_mismatch"
        return True, "system_evidence_gate_passed"


class ExistingGovernedCommitter:
    """Adapter to the existing ApprovedChange -> Committer -> CommitRecord path."""

    def __init__(self, *, db_path: str | None = None, store_path: Path | None = None):
        self.db_path = db_path or os.environ.get("PAW_WORKSPACE_DB", os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db")))
        self.store_path = store_path

    def commit(self, item: Mapping[str, Any], write_plan: list[Mapping[str, Any]]) -> Mapping[str, Any]:
        if isinstance(item.get("user_decision_approval"), Mapping):
            return self._commit_direct_user_approved_changes(item, write_plan)
        try:
            module = importlib.import_module("paw_gpt_brain_policy_committer")
            bundle_module = importlib.import_module("paw_gpt_review_bundle")
            policy_module = importlib.import_module("paw_gpt_clear_auto_write_policy")
            store = bundle_module.ReviewBundleStore(self.store_path)
            results = []
            for index, plan in enumerate(write_plan):
                source_id = _text(item.get("source_version_id"))
                refs = plan.get("evidence_refs") or item.get("evidence_refs") or []
                write_item = {
                    "id": "incremental-write:" + hash_payload({"source_version_id": item.get("source_version_id"), "index": index, "plan": plan})[:24],
                    "bundle_id": "incremental:" + _text(item.get("source_version_id")),
                    "target_type": plan.get("target_type"),
                    "operation": plan.get("operation"),
                    "payload": plan.get("payload") or {},
                    "source_refs": [f"source_version:{source_id}"],
                    "evidence_refs": refs,
                    # This branch is the standing SAFE_AUTO_APPLY policy
                    # path.  User-approved items return through the direct
                    # ApprovedChange branch above and never reach here.
                    "approval_source": "risk_router_safe_auto_apply_v1",
                    "preanalysis_model": _text(item.get("luna_model")) or LUNA_MODEL,
                    "preanalysis_provider": _text(item.get("luna_provider")) or "codex",
                    "authoritative_model": "deterministic-system-evidence-gate",
                    "system_authorization_class": SystemEvidenceGovernedGate.POLICY_ID,
                }
                envelope = {"state": "CLEAR", "coverage_state": "CONSOLIDATED", "source_version_id": source_id, "source_refs": write_item["source_refs"], "evidence_refs": refs}
                evaluated = policy_module.evaluate_write_change(write_item, bundle=envelope, source_state={"state": "CONSOLIDATED"})
                if not evaluated.get("allowed"):
                    return {"status": "FAILED_CLOSED", "reason": "policy_boundary:" + _text(evaluated.get("reason")), "formal_state_bypass": 0}
                # The existing committer marks this exact policy-write envelope
                # COMMITTED after verifying ApprovedChange/CommitRecord.  Seed
                # only the sidecar READY record; no canonical state is touched.
                normalized = evaluated.get("normalized") or {}
                if normalized.get("target_type") == "project":
                    target_id = _text((normalized.get("payload") or {}).get("id"))
                    try:
                        with sqlite3.connect(self.db_path) as conn:
                            exists = conn.execute("SELECT 1 FROM project WHERE id = ?", (target_id,)).fetchone()
                    except sqlite3.Error as exc:
                        return {"status": "FAILED_CLOSED", "reason": "existing_project_lookup_failed:" + str(exc)[:300], "formal_state_bypass": 0}
                    if exists is None:
                        return {"status": "FAILED_CLOSED", "reason": "existing_project_not_found", "formal_state_bypass": 0}
                with store.store.mutate() as sidecar:
                    ready = sidecar.setdefault("policy_writes", {}).get(write_item["id"])
                    if not isinstance(ready, Mapping):
                        sidecar["policy_writes"][write_item["id"]] = {
                            **write_item,
                            "status": "READY",
                            "policy_id": policy_module.POLICY_ID,
                            "target_type": normalized.get("target_type"),
                            "operation": normalized.get("operation"),
                            "payload": normalized.get("payload") or {},
                            "source_refs": normalized.get("source_refs") or write_item["source_refs"],
                            "evidence_refs": normalized.get("evidence_refs") or refs,
                            "created_at": utc_now(),
                            "commit_ref": None,
                        }
                result = module.commit_policy_write(write_item, db_path=self.db_path, store=store, bundle=envelope, source_state={"state": "CONSOLIDATED"})
                results.append(dict(result))
                if _text(result.get("status")) != "COMMITTED" or not result.get("commit_record_id"):
                    return {"status": "FAILED_CLOSED", "reason": result.get("reason") or "governed commit failed", "results": results}
            return {"status": "COMMITTED", "commit_record_id": results[-1].get("commit_record_id") if results else None, "change_id": results[-1].get("change_id") if results else None, "results": results, "canonical_readback": all(bool(x.get("commit_record_id")) for x in results), "formal_state_bypass": 0}
        except Exception as exc:
            return {"status": "FAILED_CLOSED", "reason": str(exc)[:1000], "formal_state_bypass": 0}

    def _commit_direct_user_approved_changes(
        self, item: Mapping[str, Any], write_plan: list[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        """Commit the already-authorized direct-user ApprovedChanges.

        This branch intentionally never imports or calls the policy committer:
        the canonical user ReviewDecision and its ApprovedChange are the
        authorization boundary, and committer.commit_approved_change is the
        only execution step.
        """
        try:
            source_id = _text(item.get("source_version_id"))
            approval = item.get("user_decision_approval") or {}
            refs_raw = approval.get("approval_refs")
            if isinstance(refs_raw, Mapping):
                refs = [_text(refs_raw.get(str(index)) or refs_raw.get(index)) for index in range(len(write_plan))]
            elif isinstance(refs_raw, list):
                refs = [_text(value.get("approval_ref") if isinstance(value, Mapping) else value) for value in refs_raw]
            else:
                refs = [_text(approval.get("approval_ref"))]
            ids_raw = approval.get("approved_change_ids")
            if isinstance(ids_raw, Mapping):
                change_ids = [_text(ids_raw.get(str(index)) or ids_raw.get(index)) for index in range(len(write_plan))]
            elif isinstance(ids_raw, list):
                change_ids = [_text(value) for value in ids_raw]
            else:
                change_ids = []
            if len(refs) != len(write_plan) or any(not ref for ref in refs) or len(set(refs)) != len(refs):
                return {"status": "FAILED_CLOSED", "reason": "direct_user_approval_plan_coverage_mismatch", "formal_state_bypass": 0}

            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            rows: list[sqlite3.Row] = []
            try:
                for index, (plan, receipt_ref) in enumerate(zip(write_plan, refs)):
                    change_id = change_ids[index] if index < len(change_ids) else ""
                    if change_id:
                        row = conn.execute("SELECT * FROM approved_change WHERE change_id = ?", (change_id,)).fetchone()
                    else:
                        candidates = conn.execute(
                            "SELECT * FROM approved_change WHERE approval_ref = ? ORDER BY created_at", (receipt_ref,)
                        ).fetchall()
                        row = candidates[0] if len(candidates) == 1 else None
                    if row is None or _text(row["approval_ref"]) != receipt_ref:
                        return {"status": "FAILED_CLOSED", "reason": "direct_user_approved_change_not_found_or_unbound", "formal_state_bypass": 0}
                    expected = _plan_signature(plan, source_id)
                    actual = _plan_signature({
                        "target_type": row["target_type"],
                        "operation": row["operation"],
                        "payload": _json_mapping(row["payload"]),
                        "source_refs": _json_value(row["source_refs"]) or [],
                    }, source_id)
                    if actual != expected:
                        return {"status": "FAILED_CLOSED", "reason": "direct_user_approved_change_binding_mismatch", "formal_state_bypass": 0}
                    rows.append(row)
            finally:
                conn.close()

            from paw_source_version_governance import commit_approved_change as governance_commit_approved_change

            results: list[dict[str, Any]] = []
            lineage: list[dict[str, Any]] = []
            for row in rows:
                change_id = _text(row["change_id"])
                result = dict(governance_commit_approved_change(change_id, db_path=self.db_path))
                results.append(result)
                if _text(result.get("result")) not in {"success"}:
                    return {"status": "FAILED_CLOSED", "reason": result.get("error") or "direct_user_commit_failed", "change_id": change_id, "results": results, "formal_state_bypass": 0}
                check = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
                check.row_factory = sqlite3.Row
                try:
                    committed = check.execute("SELECT * FROM approved_change WHERE change_id = ?", (change_id,)).fetchone()
                    record = check.execute(
                        "SELECT * FROM commit_record WHERE change_id = ? AND result = 'success' ORDER BY committed_at DESC LIMIT 1",
                        (change_id,),
                    ).fetchone()
                finally:
                    check.close()
                if committed is None or _text(committed["status"]) != "committed" or record is None:
                    return {"status": "FAILED_CLOSED", "reason": "direct_user_commit_readback_failed", "change_id": change_id, "results": results, "formal_state_bypass": 0}
                lineage.append({
                    "approved_change_id": change_id,
                    "review_decision_id": _text(committed["approval_ref"]),
                    "commit_record_id": _text(record["id"]),
                })
            return {
                "status": "COMMITTED",
                "change_id": lineage[-1]["approved_change_id"] if lineage else None,
                "change_ids": [x["approved_change_id"] for x in lineage],
                "commit_record_id": lineage[-1]["commit_record_id"] if lineage else None,
                "results": results,
                "lineage": lineage,
                "canonical_readback": len(lineage) == len(write_plan),
                "formal_state_bypass": 0,
            }
        except Exception as exc:
            return {"status": "FAILED_CLOSED", "reason": str(exc)[:1000], "formal_state_bypass": 0}


class CodexLunaIncrementalAdapter:
    """Pollable adapter over the fixed server-side Codex CLI operator."""

    slot = "luna"
    provider = "codex"

    def __init__(self, results_dir: Path | None = None):
        self.results_dir = results_dir or default_paths()["results"]
        self.operator: Any | None = None

    def _operator(self) -> Any:
        if self.operator is None:
            sys.path.insert(0, "/opt/paw-chatgpt-mcp-r13")
            self.operator = importlib.import_module("codex_operator")
        return self.operator

    @staticmethod
    def _prompt(item: Mapping[str, Any]) -> str:
        return f"""You are PAW's server-side incremental preanalysis worker. Use ONLY the Reader result JSON at {item.get('reader_result_path')}.
SourceVersion identity: {_text(item.get('source_version_id'))}
This is POST_BOOTSTRAP/INCREMENTAL work. Do not read frozen historical 710, do not make a business decision, do not write canonical state.
Return exactly one JSON object with source_version_id, coverage_state, summary, evidence_refs, findings, knowledge_proposals, project_consolidations, metric_proposals, data_assets, project_id, canonical_topic, risk_hints, write_plan.
Preserve evidence locators and uncertainty. A write_plan is only a proposal; the deterministic shared risk router and governed committer decide whether it may execute."""

    def submit(self, item: Mapping[str, Any]) -> Mapping[str, Any]:
        operator = self._operator()
        token = _text(item.get("luna_token"))
        # The durable pipeline records the token and job before polling. The
        # fixed Codex operator itself owns the process lifecycle.
        result = operator.submit_task(self._prompt(item), title=f"PAW incremental Luna {_text(item.get('source_version_id'))}", network_access=False, timeout_seconds=operator.DEFAULT_TIMEOUT_SECONDS)
        if result.get("error"):
            return {"status": "FAILED", "error": result.get("error"), "token": token, "provider": self.provider, "slot": self.slot, "model": operator.FIXED_MODEL}
        import paw_semantic_slots as _paw_slots
        gen = _paw_slots.config_generation()
        binding = _paw_slots.make_slot_binding("luna", "codex", operator.FIXED_MODEL, gen, result.get("job_id"))
        return {
            "status": "RUNNING",
            "job_id": result.get("job_id"),
            "token": token,
            "model": operator.FIXED_MODEL,
            "reasoning_effort": LUNA_REASONING,
            "provider": self.provider,
            "slot": self.slot,
            "generation": gen,
            "binding": binding,
        }

    def poll(self, item: Mapping[str, Any]) -> Mapping[str, Any]:
        operator = self._operator()
        job_id = _text(item.get("luna_job_id"))
        if not job_id:
            return {"status": "FAILED", "error": "missing_luna_job_id", "provider": self.provider, "slot": self.slot, "model": operator.FIXED_MODEL}
        status = operator.task_status(job_id)
        lifecycle = _text(status.get("status"))
        if lifecycle in {"QUEUED", "RUNNING"}:
            return {"status": "RUNNING", "job_id": job_id, "model": operator.FIXED_MODEL, "provider": self.provider, "slot": self.slot}
        if lifecycle != "COMPLETED" or status.get("exit_code") not in (0, None):
            return {"status": "FAILED", "job_id": job_id, "error": f"codex_{lifecycle.lower()}", "provider": self.provider, "slot": self.slot, "model": operator.FIXED_MODEL}
        import paw_semantic_slots as _paw_slots
        normalized = _paw_slots.read_and_normalize_operator_result(operator, "codex", job_id)
        if normalized.parsed_json is None:
            return {"status": "FAILED", "job_id": job_id, "error": "luna_returned_no_json", "provider": self.provider, "slot": self.slot, "model": operator.FIXED_MODEL}
        parsed = dict(normalized.parsed_json)
        parsed["status"] = "COMPLETED"
        parsed["job_id"] = job_id
        parsed["model"] = operator.FIXED_MODEL
        parsed["reasoning_effort"] = LUNA_REASONING
        parsed["provider"] = self.provider
        parsed["slot"] = self.slot
        return parsed


class AgyIncrementalOpusAdapter:
    """Adapter to the shared durable Opus dispatcher; no canonical write."""

    slot = "opus"
    provider = "agy"

    def __init__(self):
        self.agy: Any | None = None

    def _agy(self) -> Any:
        if self.agy is None:
            sys.path.insert(0, "/opt/paw-chatgpt-mcp-r13")
            self.agy = importlib.import_module("agy_operator")
        return self.agy

    @staticmethod
    def _prompt(cluster: Mapping[str, Any]) -> str:
        return f"""Review one PAW incremental expert cluster once. Read only the supplied cluster JSON at {cluster.get('input_path')}.
cluster_key={cluster.get('cluster_key')}; one-time token={cluster.get('opus_token')}
Do not write canonical state, do not make a user business choice, and do not re-review the full source. Return JSON: cluster_key, outcome (SAFE_TO_APPLY|USER_DECISION_REQUIRED|INSUFFICIENT_EVIDENCE), reason, review."""

    def submit(self, cluster: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            dispatcher = importlib.import_module("paw_gpt_brain_opus_dispatcher")
            return dispatcher.dispatch_incremental_cluster(cluster)
        except (ImportError, AttributeError):
            pass
        agy = self._agy()
        result = agy.submit_task(self._prompt(cluster), title=f"PAW incremental Opus {_text(cluster.get('cluster_key'))}", model=OPUS_MODEL, mode="analyze", sandbox=False, timeout_seconds=min(int(agy.MAX_TIMEOUT_SECONDS), 90 * 60), profile="semantic_review")
        if result.get("error"):
            return {"status": "FAILED", "error": result.get("error"), "provider": self.provider, "slot": self.slot, "model": OPUS_MODEL}
        effective = result.get("effective_model") or result.get("model") or OPUS_MODEL
        import paw_semantic_slots as _paw_slots
        gen = _paw_slots.config_generation()
        binding = _paw_slots.make_slot_binding("opus", "agy", effective, gen, result.get("job_id"))
        return {
            "status": "RUNNING",
            "job_id": result.get("job_id"),
            "model": effective,
            "provider": self.provider,
            "slot": self.slot,
            "generation": gen,
            "binding": binding,
        }

    def poll(self, cluster: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            dispatcher = importlib.import_module("paw_gpt_brain_opus_dispatcher")
            # The shared dispatcher recovers the durable token and polls the
            # same job; this keeps incremental and bootstrap model checks in
            # one runtime implementation.
            return dispatcher.dispatch_incremental_cluster(cluster)
        except (ImportError, AttributeError):
            pass
        agy = self._agy()
        job_id = _text(cluster.get("opus_job_id"))
        status = agy.task_status(job_id)
        if _text(status.get("status")) in {"QUEUED", "RUNNING"}:
            return {"status": "RUNNING", "job_id": job_id, "model": status.get("model"), "provider": self.provider, "slot": self.slot}
        if _text(status.get("status")) != "COMPLETED":
            return {"status": "FAILED", "job_id": job_id, "error": f"agy_{_text(status.get('status')).lower()}", "provider": self.provider, "slot": self.slot, "model": OPUS_MODEL}
        check = agy.verify_semantic_review_runtime_models("semantic_review", command_model=OPUS_MODEL, status_model=status.get("model"), result_model=OPUS_MODEL, require_all=True)
        if not check.get("ok"):
            return {"status": "FAILED", "job_id": job_id, "error": "opus_effective_model_mismatch", "provider": self.provider, "slot": self.slot, "model": OPUS_MODEL}
        import paw_semantic_slots as _paw_slots
        normalized = _paw_slots.read_and_normalize_operator_result(agy, "agy", job_id)
        if normalized.parsed_json is None:
            return {"status": "FAILED", "job_id": job_id, "error": "opus_returned_no_json", "provider": self.provider, "slot": self.slot, "model": OPUS_MODEL}
        parsed = dict(normalized.parsed_json)
        parsed.update({"status": "COMPLETED", "job_id": job_id, "model": OPUS_MODEL, "provider": self.provider, "slot": self.slot})
        return parsed


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    for candidate in (raw.strip(),):
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except ValueError:
            pass
        decoder = json.JSONDecoder()
        for index, char in enumerate(candidate):
            if char != "{":
                continue
            try:
                value, _ = decoder.raw_decode(candidate[index:])
            except ValueError:
                continue
            if isinstance(value, dict):
                return value
    return None


def _normalize_preanalysis(result: Mapping[str, Any], source_id: str) -> dict[str, Any]:
    if _text(result.get("source_version_id")) not in {"", source_id}:
        raise IncrementalError("Luna SourceVersion identity mismatch")
    out = dict(result)
    out["source_version_id"] = source_id
    out["coverage_state"] = _text(result.get("coverage_state")) or "REVIEW_REQUIRED"
    refs = result.get("evidence_refs") if isinstance(result.get("evidence_refs"), list) else []
    out["evidence_refs"] = [dict(x) for x in refs if isinstance(x, Mapping)]
    out["findings"] = [dict(x) for x in _as_list(result.get("findings")) if isinstance(x, Mapping)]
    out["knowledge_proposals"] = [dict(x) for x in _as_list(result.get("knowledge_proposals")) if isinstance(x, Mapping)]
    out["project_consolidations"] = [dict(x) for x in _as_list(result.get("project_consolidations")) if isinstance(x, Mapping)]
    out["metric_proposals"] = [dict(x) for x in _as_list(result.get("metric_proposals")) if isinstance(x, Mapping)]
    out["write_plan"] = [dict(x) for x in _as_list(result.get("write_plan")) if isinstance(x, Mapping)]
    return out


class IncrementalPipeline:
    def __init__(self, *, state_path: Path | None = None, handoff_path: Path | None = None, results_dir: Path | None = None,
                 router: Any | None = None, luna: LunaAdapter | None = None, opus: OpusAdapter | None = None,
                 committer: Committer | None = None, patrol_bound: bool = True, patrol_store_path: Path | None = None):
        paths = default_paths()
        self.state_store = LockedJsonStore(state_path or paths["state"])
        self._loaded_snapshot = None
        self.handoff_path = handoff_path or paths["handoff"]
        self.results_dir = results_dir or ((state_path.parent / "paw-incremental-luna-results") if state_path is not None else paths["results"])
        self.router = router or SharedRiskRouterAdapter()
        import paw_semantic_slots as _paw_slots  # v1.3D / v2 slot config layer
        self._injected_luna = luna is not None
        self._injected_opus = opus is not None
        self.luna = luna or _paw_slots.resolve(
            "luna", default_factory=lambda: CodexLunaIncrementalAdapter(self.results_dir),
            results_dir=self.results_dir)
        self.opus = opus or _paw_slots.resolve(
            "opus", default_factory=AgyIncrementalOpusAdapter,
            results_dir=self.results_dir)
        self._config_gen = _paw_slots.config_generation()
        self.committer = committer or ExistingGovernedCommitter()
        self.safe_gate = SystemEvidenceGovernedGate()
        self.db_path = getattr(self.committer, "db_path", os.environ.get("PAW_WORKSPACE_DB", os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))))
        self.patrol_bound = bool(patrol_bound)
        self.patrol_store_path = patrol_store_path or ((state_path.parent / "paw_patrol_input_batch.json") if state_path is not None else paths["patrol_batch"])

    def _refresh_slots(self) -> None:
        """Hot reload slot configuration if mtime/config changed."""
        import paw_semantic_slots as _paw_slots
        cur_gen = _paw_slots.config_generation()
        if cur_gen != self._config_gen:
            try:
                if not self._injected_luna:
                    self.luna = _paw_slots.resolve(
                        "luna",
                        default_factory=lambda: CodexLunaIncrementalAdapter(self.results_dir),
                        results_dir=self.results_dir,
                    )
                if not self._injected_opus:
                    self.opus = _paw_slots.resolve(
                        "opus",
                        default_factory=AgyIncrementalOpusAdapter,
                        results_dir=self.results_dir,
                    )
                self._config_gen = cur_gen
            except _paw_slots.SlotResolutionError as exc:
                _paw_slots.audit_event("slot_config_reload_error", {"error": str(exc)[:200]})
                if not self._injected_luna:
                    self.luna = _paw_slots.DisabledAdapter("luna", "unknown", f"reload_error: {str(exc)[:100]}")
                if not self._injected_opus:
                    self.opus = _paw_slots.DisabledAdapter("opus", "unknown", f"reload_error: {str(exc)[:100]}")
                self._config_gen = cur_gen

    def _luna_adapter_for(self, item: Mapping[str, Any]) -> Any:
        """Return the bound adapter for an in-flight Luna item, preserving binding."""
        if self._injected_luna:
            return self.luna
        binding = item.get("luna_binding")
        if isinstance(binding, Mapping) and binding.get("provider"):
            import paw_semantic_slots as _paw_slots
            try:
                return _paw_slots.resolve_adapter_for_binding(
                    binding,
                    results_dir=self.results_dir,
                    default_luna_factory=lambda: CodexLunaIncrementalAdapter(self.results_dir),
                )
            except Exception:
                pass
        prov = item.get("luna_provider")
        if prov and prov != getattr(self.luna, "provider", None):
            import paw_semantic_slots as _paw_slots
            try:
                return _paw_slots.resolve_adapter_for_binding(
                    {"slot": "luna", "provider": prov, "effective_model": item.get("luna_model") or LUNA_MODEL},
                    results_dir=self.results_dir,
                    default_luna_factory=lambda: CodexLunaIncrementalAdapter(self.results_dir),
                )
            except Exception:
                pass
        return self.luna

    def _opus_adapter_for(self, item: Mapping[str, Any], default_adapter: OpusAdapter | None = None) -> Any:
        """Return the bound adapter for an in-flight Opus cluster, preserving binding."""
        if default_adapter is not None:
            return default_adapter
        if self._injected_opus:
            return self.opus
        binding = item.get("opus_binding")
        if isinstance(binding, Mapping) and binding.get("provider"):
            import paw_semantic_slots as _paw_slots
            try:
                return _paw_slots.resolve_adapter_for_binding(
                    binding,
                    results_dir=self.results_dir,
                    default_opus_factory=AgyIncrementalOpusAdapter,
                )
            except Exception:
                pass
        prov = item.get("opus_provider")
        if prov and prov != getattr(self.opus, "provider", None):
            import paw_semantic_slots as _paw_slots
            try:
                return _paw_slots.resolve_adapter_for_binding(
                    {"slot": "opus", "provider": prov, "effective_model": item.get("opus_model") or OPUS_MODEL},
                    results_dir=self.results_dir,
                    default_opus_factory=AgyIncrementalOpusAdapter,
                )
            except Exception:
                pass
        return self.opus

    def load(self) -> dict[str, Any]:
        data = self.state_store.load()
        if not data:
            data = _blank_state()
        data.setdefault("items", {})
        data["formal_state_bypass"] = FORMAL_STATE_BYPASS
        data["full_corpus_reread"] = False
        self._loaded_snapshot = copy.deepcopy(data)
        return data

    def _save(self, data: dict[str, Any]) -> None:
        data["schema"] = SCHEMA
        data["updated_at"] = utc_now()
        data["formal_state_bypass"] = FORMAL_STATE_BYPASS
        data["full_corpus_reread"] = False
        # Slow Reader/model/Committer work stays outside the short file lock.
        # Compare changed items against the snapshot we actually read, then
        # merge them into fresh state. A stale worker must never erase a claim,
        # accepted submit, or another item's progress.
        base = self._loaded_snapshot
        if base is None:
            base = self.state_store.load() or _blank_state()
        before = base.get("items") or {}
        after = data.get("items") or {}
        changed = {sid: value for sid, value in after.items() if value != before.get(sid)}
        with self.state_store.mutate() as current:
            current.setdefault("items", {})
            for sid, value in changed.items():
                live = current["items"].get(sid)
                if live != before.get(sid) and live != value:
                    raise IncrementalError(f"state_conflict:retry_from_fresh_state:{sid}")
            for sid, value in changed.items():
                current["items"][sid] = copy.deepcopy(value)
            for key, value in data.items():
                if key not in {"items", "events", "metrics"} and (key not in base or value != base.get(key)):
                    current[key] = copy.deepcopy(value)
            old_events = base.get("events") or []
            additions = [e for e in (data.get("events") or []) if e not in old_events]
            current["events"] = ((current.get("events") or []) + additions)[-200:]
            if "metrics" in data:
                current["metrics"] = copy.deepcopy(data["metrics"])
            current.update(schema=SCHEMA, updated_at=data["updated_at"],
                           formal_state_bypass=0, full_corpus_reread=False)
        self._loaded_snapshot = copy.deepcopy(data)

    def _event(self, data: dict[str, Any], kind: str, **details: Any) -> None:
        data.setdefault("events", []).append({"at": utc_now(), "type": kind, **details})
        data["events"] = data["events"][-200:]

    def ingest_handoff(self, handoff: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(handoff or _read_json(self.handoff_path))
        if _text(payload.get("schema")) != HANDOFF_SCHEMA:
            return {"ingested": 0, "skipped": 0, "reason": "handoff_schema_unavailable", "formal_state_bypass": 0}
        data = self.load()
        ingested = skipped = 0
        for raw in (payload.get("items") or {}).values():
            if not isinstance(raw, Mapping):
                continue
            try:
                sid = _source_id(raw)
            except IncrementalError:
                skipped += 1
                continue
            existing = data["items"].get(sid)
            if existing:
                if _text(existing.get("sha256")) and _text(raw.get("sha256")) and existing.get("sha256") != raw.get("sha256"):
                    existing["last_error"] = "source_version_identity_reused_with_different_sha256"
                    _transition(existing, EXPLICIT_HOLD, reason="source_identity_integrity_hold")
                    self._event(data, "identity_hold", source_version_id=sid)
                else:
                    skipped += 1
                continue
            item = _new_item(raw)
            item["next_action"] = "WAIT_GPT_PATROL" if item.get("analysis_state") == READER_READY else item.get("next_action")
            data["items"][sid] = item
            ingested += 1
            self._event(data, "source_discovered", source_version_id=sid, state=item["analysis_state"])
            if self.patrol_bound:
                try:
                    from paw_patrol_orchestrator import PatrolOrchestrator
                    PatrolOrchestrator(self.patrol_store_path, db_path=self.db_path).enqueue_source(
                        sid, payload_ref=_text(raw.get("reader_result_path")) or None,
                    )
                except Exception as exc:
                    item["patrol_enqueue_error"] = str(exc)[:300]
        data["status"] = "INGESTED"
        self._save(data)
        return {"ingested": ingested, "skipped": skipped, "item_count": len(data["items"]), "semantic_processing": False, "formal_state_bypass": 0}

    def _persist_result(self, item: dict[str, Any], result: Mapping[str, Any]) -> None:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        path = self.results_dir / (hash_payload({"source_version_id": item["source_version_id"], "luna_job_id": item.get("luna_job_id")})[:32] + ".json")
        atomic_write_json(path, dict(result))
        item["luna_result_ref"] = str(path)

    def _result(self, item: Mapping[str, Any]) -> dict[str, Any]:
        value = _read_json(_text(item.get("luna_result_ref"))) if _text(item.get("luna_result_ref")) else {}
        return value

    def _hold(self, item: dict[str, Any], reason: str) -> None:
        item["last_error"] = reason[:1000]
        try:
            _transition(item, EXPLICIT_HOLD, reason=reason[:200])
        except IncrementalError:
            item["analysis_state"] = EXPLICIT_HOLD
            item["checkpoint"] = reason[:200]
        item["next_action"] = "EXPLICIT_HOLD"

    def _deterministic_prepare(self, data: dict[str, Any], item: dict[str, Any]) -> None:
        """Deterministic Evidence Packet Builder. No LLM call.

        Validates Reader Result, Evidence Locator, Artifact availability,
        performs duplicate/idempotency and formal lineage checks,
        deterministic Project ID lookup, builds a bounded packet,
        persists it, and transitions READER_READY → PATROL_READY.
        """
        sid = item["source_version_id"]
        # Step 1: Verify Reader Result availability
        reader_path = _text(item.get("reader_result_path"))
        if not reader_path or not Path(reader_path).is_file():
            if item["analysis_state"] == DISCOVERED:
                item["next_action"] = "WAIT_READER"
                return
            self._hold(item, "reader_result_unavailable")
            return
        # Step 2: Transition DISCOVERED → READER_READY if needed
        if item["analysis_state"] == DISCOVERED:
            _transition(item, READER_READY, reason="reader_result_verified")
        # Step 3: Only proceed if READER_READY
        if item["analysis_state"] != READER_READY:
            return
        # Step 4: Duplicate / idempotency check
        if item.get("analysis_state") == PATROL_READY:
            return
        # Step 5: Existing formal lineage check
        existing_lineage = []
        try:
            brain_data = _read_json(self.state_store.path.parent / "gpt_brain_bootstrap.json")
            for change in _as_list(brain_data.get("approved_changes")):
                if isinstance(change, Mapping) and _text(change.get("source_version_id")) == sid:
                    existing_lineage.append(dict(change))
        except Exception:
            pass
        # Step 6: Deterministic Project ID lookup
        project_id = _text(item.get("project_id"))
        if not project_id:
            try:
                reader_data = _read_json(reader_path)
                project_id = _text(reader_data.get("project_id"))
            except Exception:
                pass
        # Step 7: Build bounded Evidence Packet
        reader_data = _read_json(reader_path)
        # P0.1: derive evidence_refs deterministically from the Reader Result
        # (reader-evidence/3 contract). The Reader Result IS the formal evidence
        # carrier; text_blocks / tables are durable, hash-bound technical
        # fragments. Locator format follows the existing array-index convention
        # (e.g. text_blocks[0], tables[0]) used by visual_pages[{idx}].
        artifact_hash = _text(reader_data.get("raw_artifact_sha256")) or _text(item.get("sha256"))
        # Technical debt fix (EVIDENCE_MATERIALIZATION_CONTRACT_ENHANCEMENT):
        # evidence derivation is delegated to the shared Evidence Materializer so
        # that reader families whose evidence does not live in text_blocks (XLSX
        # structured_semantics.sheets[].cells, image-only PDF visual_pages) can
        # enter the governance chain, while DOCX/PDF/PPTX keep their exact
        # previous semantics. The two paths (prepare + rebuild) now share one
        # implementation and cannot diverge.
        evidence_refs = _materialize_evidence(
            reader_data,
            sid=sid,
            reader_path=reader_path,
            artifact_hash=artifact_hash,
            content_fingerprint=_text(reader_data.get("content_fingerprint")) or _text(item.get("content_fingerprint")),
        )
        # Evidence Identity Gate (shared with rebuild path).
        _validate_evidence_refs(evidence_refs, sid)
        evidence_state = "READY" if evidence_refs else "MISSING"
        if evidence_state != "READY":
            # Fail closed: no formal evidence carrier content available.
            item["next_action"] = "WAIT_EVIDENCE_MATERIALIZATION"
            item["evidence_state"] = evidence_state
            return
        artifact_path = _text(item.get("artifact_path"))
        artifact_available = bool(artifact_path and Path(artifact_path).is_file())
        packet = {
            "source_version_id": sid,
            "reader_result_ref": reader_path,
            "artifact_ref": artifact_path or None,
            "artifact_availability": artifact_available,
            "raw_artifact_available": artifact_available,
            "reader_evidence_available": True,
            "evidence_state": evidence_state,
            "evidence_refs": evidence_refs,
            "evidence_schema_version": _text(reader_data.get("evidence_schema_version")) or None,
            "project_candidates_deterministic": [project_id] if project_id else [],
            "formal_lineage_refs": existing_lineage,
            "bounded_context_refs": {
                "canonical_topic": _text(item.get("canonical_topic")) or _text(reader_data.get("canonical_topic")),
                "change_type": _text(item.get("change_type")),
                "sha256": _text(item.get("sha256")),
                "content_fingerprint": _text(item.get("content_fingerprint")),
            },
            "freshness": {
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "packet_built_at": utc_now(),
            },
            "packet_revision": 1,
            "no_model_call": True,
            "no_semantic_analysis": True,
        }
        # Step 8: Persist packet
        self.results_dir.mkdir(parents=True, exist_ok=True)
        packet_path = self.results_dir / (hash_payload({"source_version_id": sid, "packet": "deterministic_prepare"})[:32] + ".json")
        atomic_write_json(packet_path, packet)
        item["evidence_packet_ref"] = str(packet_path)
        item["evidence_packet_revision"] = 1
        item["evidence_state"] = evidence_state
        # Step 9: Set lease metadata for GPT claim
        item["claim_owner"] = None
        item["lease_expires_at"] = None
        item["lease_expires_epoch"] = None
        # Step 10: Transition READER_READY → PATROL_READY
        _transition(item, PATROL_READY, reason="deterministic_evidence_packet_built")
        item["next_action"] = "WAIT_GPT_PATROL_SEMANTIC_BUNDLE"
        item["checkpoint"] = "deterministic_prepare_complete"

    def _luna_step(self, data: dict[str, Any], item: dict[str, Any]) -> None:
        self._refresh_slots()
        sid = item["source_version_id"]
        if item["analysis_state"] == DISCOVERED:
            if not Path(_text(item.get("reader_result_path"))).is_file():
                item["next_action"] = "WAIT_READER"
                return
            _transition(item, READER_READY, reason="reader_result_verified")
        if item["analysis_state"] == READER_READY:
            _transition(item, LUNA_PENDING, reason="reader_ready_luna_pending")
        if item["analysis_state"] == LUNA_PENDING:
            if item.get("attempt_count", 0) >= MAX_ATTEMPTS:
                self._hold(item, "luna_attempt_limit")
                return
            response = dict(self.luna.submit(item))
            status = _text(response.get("status"))
            eff_model = response.get("model") or getattr(self.luna, "effective_model", LUNA_MODEL)
            prov = response.get("provider") or getattr(self.luna, "provider", "codex")
            binding = response.get("binding")
            if status == "COMPLETED":
                item["attempt_count"] = int(item.get("attempt_count") or 0) + 1
                result = _normalize_preanalysis(response.get("result") if isinstance(response.get("result"), Mapping) else response, sid)
                self._persist_result(item, result)
                item["luna_model"] = eff_model
                item["luna_provider"] = prov
                if binding:
                    item["luna_binding"] = dict(binding)
                item["luna_reasoning_effort"] = response.get("reasoning_effort") or LUNA_REASONING
                _transition(item, LUNA_COMPLETE, reason="luna_preanalysis_persisted")
                return
            if status != "RUNNING":
                error = _text(response.get("error")) or "luna_submit_failed"
                if error in TRANSIENT_LUNA_SUBMIT_ERRORS:
                    # PAW v1.2 phase 3b (technical debt I): a capacity condition is
                    # not this item's fault.  Stay in LUNA_PENDING (no transition:
                    # LUNA_PENDING -> LUNA_PENDING is not an allowed edge), keep the
                    # attempt count untouched, and let the next Patrol pass retry.
                    item["last_error"] = error[:1000]
                    item["checkpoint"] = "luna_submit_deferred"
                    item["next_action"] = "WAIT_LUNA_CAPACITY"
                    return
                item["attempt_count"] = int(item.get("attempt_count") or 0) + 1
                self._hold(item, error)
                return
            item["attempt_count"] = int(item.get("attempt_count") or 0) + 1
            item["luna_job_id"] = _text(response.get("job_id")) or None
            item["luna_model"] = eff_model
            item["luna_provider"] = prov
            if binding:
                item["luna_binding"] = dict(binding)
            item["luna_reasoning_effort"] = response.get("reasoning_effort") or LUNA_REASONING
            _transition(item, LUNA_RUNNING, reason="luna_job_submitted")
            return
        if item["analysis_state"] != LUNA_RUNNING:
            return
        adapter = self._luna_adapter_for(item)
        response = dict(adapter.poll(item))
        if _text(response.get("status")) == "RUNNING":
            item["checkpoint"] = "luna_job_running"
            return
        if _text(response.get("status")) != "COMPLETED":
            error_msg = _text(response.get("error")) or "luna_poll_failed"
            if "model_mismatch" in error_msg:
                self._hold(item, error_msg)
                return
            if int(item.get("attempt_count") or 0) < MAX_ATTEMPTS:
                item["last_error"] = error_msg
                _transition(item, LUNA_PENDING, reason="luna_retryable_failure")
            else:
                self._hold(item, error_msg)
            return
        expected_model = _text(item.get("luna_model")) or getattr(adapter, "effective_model", LUNA_MODEL)
        actual_model = _text(response.get("model")) or expected_model
        if actual_model != expected_model:
            self._hold(item, f"luna_model_mismatch: expected {expected_model}, got {actual_model}")
            return
        result = _normalize_preanalysis(response, sid)
        self._persist_result(item, result)
        item["luna_model"] = actual_model
        item["luna_provider"] = _text(response.get("provider")) or _text(item.get("luna_provider")) or getattr(adapter, "provider", "codex")
        item["luna_reasoning_effort"] = response.get("reasoning_effort") or LUNA_REASONING
        _transition(item, LUNA_COMPLETE, reason="luna_preanalysis_persisted")

    def _route(self, data: dict[str, Any], item: dict[str, Any]) -> None:
        if item["analysis_state"] != LUNA_COMPLETE:
            return
        result = self._result(item)
        item["luna_result"] = {k: result.get(k) for k in ("summary", "coverage_state", "evidence_refs", "findings", "knowledge_proposals", "project_consolidations", "metric_proposals", "write_plan", "risk_hints", "material_business_decision", "unresolved_conflict", "changes_effective_rule", "creates_project") if k in result}
        item["evidence_refs"] = result.get("evidence_refs") or []
        item["project_id"] = _text(result.get("project_id")) or item.get("project_id")
        item["canonical_topic"] = _text(result.get("canonical_topic")) or item.get("canonical_topic")
        try:
            decision = dict(self.router.classify(item, result))
        except Exception as exc:
            self._hold(item, "shared_router_unavailable:" + str(exc))
            return
        route = _text(decision.get("route"))
        item["risk_route"] = route
        item["risk_reason_codes"] = [_text(x) for x in _as_list(decision.get("reason_codes")) if _text(x)]
        item["cluster_key"] = _text(decision.get("cluster_key")) or _cluster_key({**item, **result, **decision})
        item["write_plan"] = [dict(x) for x in _as_list(decision.get("write_plan")) if isinstance(x, Mapping)]
        item["risk_decision"] = dict(decision)
        if route not in ROUTES:
            self._hold(item, "unsupported_router_route:" + route)
            return
        _transition(item, ROUTED, reason="shared_router_decision")
        safe_target = PATROL_READY if self.patrol_bound else SAFE_APPLY_PENDING
        targets = {
            SAFE_AUTO_APPLY: safe_target,
            TERRA_REVIEW_REQUIRED: TERRA_PENDING,
            OPUS_REVIEW_REQUIRED: OPUS_PENDING,
            USER_DECISION_REQUIRED: USER_DECISION_PENDING,
            INSUFFICIENT_EVIDENCE: EXPLICIT_HOLD,
            "NO_ACTION": FINALIZED,
        }
        target = targets[route]
        _transition(item, target, reason="route:" + route)
        item["next_action"] = {
            SAFE_APPLY_PENDING: "GOVERNED_DETERMINISTIC_APPLY",
            PATROL_READY: "WAIT_GPT_PATROL_SEMANTIC_BUNDLE",
            TERRA_PENDING: "WAIT_TERRA_PATROL",
            OPUS_PENDING: "OPUS_CLUSTER_DISPATCH",
            USER_DECISION_PENDING: "WAIT_USER_MATERIAL_DECISION",
            EXPLICIT_HOLD: "WAIT_ADDITIONAL_EVIDENCE",
            FINALIZED: "FINALIZED_NO_ACTION",
        }[target]
        if target == EXPLICIT_HOLD:
            item["last_error"] = ";".join(item["risk_reason_codes"])[:1000] or "insufficient_evidence"

    def _safe_apply(self, item: dict[str, Any]) -> None:
        if item["analysis_state"] != SAFE_APPLY_PENDING:
            return
        # P0-A pre-flight: the PROJECT-first runtime must be importable and
        # its workspace store present BEFORE any Source/Knowledge governed
        # write.  An import failure fails closed here; the write never runs.
        try:
            importlib.import_module("paw_project_outcome")
        except Exception as exc:
            item["project_synthesis"] = {"status": "BLOCKED", "reason": "paw_project_outcome_unavailable:" + str(exc)[:200], "project_first_runtime_health": "FAIL"}
            self._hold(item, "project_first_runtime_unhealthy:import_failed")
            return
        if not Path(self.db_path).is_file():
            item["project_synthesis"] = {"status": "BLOCKED", "reason": "workspace_db_missing", "project_first_runtime_health": "FAIL"}
            self._hold(item, "project_first_runtime_unhealthy:workspace_db_missing")
            return
        plan = [dict(x) for x in item.get("write_plan") or []]
        if not plan:
            semantic = item.get("luna_result") or {}
            if any(semantic.get(key) for key in ("knowledge_proposals", "project_consolidations", "metric_proposals")):
                # A proposal is not an executable approved change. Never
                # silently discard it or invent a plan from its prose.
                self._hold(item, "compilation_write_plan_required:unapplied_proposals")
                return
            _transition(item, FINALIZED, reason="safe_no_action_finalized")
            item["next_action"] = "FINALIZED_NO_ACTION"
            return
        # Preserve analysis in the validation view without writing it into the
        # permanent result file again.
        item["luna_result"] = {**(item.get("luna_result") or {}), "evidence_refs": item.get("evidence_refs") or []}
        allowed, reason = self.safe_gate.validate(item, plan)
        if not allowed:
            self._hold(item, "safe_gate:" + reason)
            return
        _transition(item, APPLYING, reason="system_evidence_gate_passed")
        result = dict(self.committer.commit(item, plan))
        if _text(result.get("status")) == "COMMITTED" and result.get("commit_record_id"):
            item["commit_ref"] = result
            self._project_first_synthesis(item, result)
            health = _text((item.get("project_synthesis") or {}).get("project_first_runtime_health"))
            if health != "PASS":
                # PROJECT-first fail closed: never let a SAFE_AUTO_APPLY item
                # finish silently when Project synthesis could not run.  The
                # governed write stays committed and idempotent; the item
                # remains BLOCKED / RETRYABLE_ERROR until synthesis is healthy.
                item["last_error"] = "project_first_runtime_unhealthy:" + (health or "UNKNOWN")
                _transition(item, SAFE_APPLY_PENDING, reason="project_synthesis_blocked_retryable_error")
                item["next_action"] = "BLOCKED_RETRYABLE_ERROR_PROJECT_SYNTHESIS"
                return
            _transition(item, WRITE_COMMITTED, reason="commit_record_readback_verified")
            item["next_action"] = "TERMINAL"
        else:
            item["last_error"] = _text(result.get("reason")) or "governed_commit_failed"
            _transition(item, SAFE_APPLY_PENDING, reason="commit_retryable_or_failed_closed")

    def _project_first_synthesis(self, item: dict[str, Any], commit_result: Mapping[str, Any]) -> None:
        """Run Project Outcome and Cross-Project synthesis after a commit.

        Impact resolution consumes existing Project/Knowledge links and the
        persisted Luna Project Builder index.  Every canonical mutation below
        is sent through the policy committer; this method never writes SQL.
        PROJECT-first is fail closed: when the Project Outcome runtime cannot
        run, the item is marked PROJECT_FIRST_RUNTIME_HEALTH=FAIL and must
        never silently finish after a SAFE_AUTO_APPLY commit.
        """
        try:
            outcome_mod = importlib.import_module("paw_project_outcome")
            policy_mod = importlib.import_module("paw_gpt_brain_policy_committer")
            if not Path(self.db_path).is_file():
                raise IncrementalError("project_first_runtime_unhealthy:workspace_db_missing")
            semantic_path = Path(os.environ.get("PAW_GPT_BRAIN_PROJECT_BUILDER_PATH", "/var/lib/paw-mcp-gateway/gpt_brain_project_builder.json"))
            try:
                semantic = _read_json(str(semantic_path))
            except Exception:
                semantic = {}
            ids = outcome_mod.identify_impacted_projects(self.db_path, item, semantic)
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "project_outcome" not in tables:
                    raise IncrementalError("project_first_runtime_unhealthy:project_outcome_schema_missing")
                project_columns = {str(r[1]) for r in conn.execute("PRAGMA table_info(project)").fetchall()}
                project_select = "id,name" + (",status" if "status" in project_columns else "")
                project_rows = {r[0]: dict(r) for r in conn.execute(f"SELECT {project_select} FROM project").fetchall()}
                # P0 secondary boundary defence: even if
                # identify_impacted_projects regresses, an ineligible Project
                # must never reach merge_incremental_outcome, an Outcome write
                # or relation synthesis.  Fail closed with disclosure.
                ineligible_ids = [
                    pid for pid in ids
                    if not outcome_mod.is_current_business_project(project_rows.get(pid))
                ]
                if ineligible_ids:
                    excluded = set(ineligible_ids)
                    ids = [pid for pid in ids if pid not in excluded]
                if not ids:
                    item["project_synthesis"] = {"status": "SKIPPED", "reason": "no_existing_impacted_project", "impacted_project_ids": [], "excluded_ineligible_project_ids": ineligible_ids, "project_first_runtime_health": "PASS"}
                    item["cross_project_synthesis"] = {"status": "SKIPPED", "reason": "no_existing_impacted_project", "relation_count": 0, "excluded_ineligible_project_ids": ineligible_ids, "formal_state_bypass": 0}
                    return
                result = item.get("luna_result") if isinstance(item.get("luna_result"), Mapping) else {}
                synthesis_results = []
                for pid in ids:
                    current = outcome_mod.read_outcome(self.db_path, pid)
                    if not current:
                        synthesis_results.append({"project_id": pid, "status": "SKIPPED", "reason": "outcome_baseline_missing"})
                        continue
                    updated, changed = outcome_mod.merge_incremental_outcome(current, project_rows[pid], item)
                    if not changed:
                        synthesis_results.append({"project_id": pid, "status": "NO_CHANGE", "input_watermark": current.get("input_watermark")})
                        continue
                    refs = updated["source_evidence_refs"][-20:]
                    write = {"id": "incremental-project-synthesis:" + hash_payload({"project_id": pid, "watermark": updated["input_watermark"]})[:32], "target_type": "project", "operation": "update", "payload": outcome_mod.outcome_payload(updated), "source_refs": ["source_version:" + _text(item.get("source_version_id"))], "evidence_refs": refs, "approval_source": "SYSTEM_EVIDENCE_GOVERNED_WRITE_V1", "preanalysis_model": _text(item.get("luna_model")) or LUNA_MODEL, "preanalysis_provider": _text(item.get("luna_provider")) or "codex", "authoritative_model": "deterministic-project-synthesizer-v1"}
                    committed = policy_mod.commit_policy_write(write, db_path=self.db_path, bundle={"id": write["id"], "state": "CLEAR", "coverage_state": "CONSOLIDATED", "source_refs": write["source_refs"], "evidence_refs": refs}, mark_policy=False)
                    synthesis_results.append({"status": committed.get("status"), "project_id": pid, "commit_record_id": committed.get("commit_record_id"), "input_watermark": updated["input_watermark"], "canonical_readback": bool(outcome_mod.read_outcome(self.db_path, pid))})
                    # Phase-2 realtime closure: the Outcome just committed must
                    # be readable through the human-readable Handbook too, not
                    # only through project_outcome.  Additive + idempotent; the
                    # handbook builder never overwrites authored sections.
                    try:
                        handbook_mod = importlib.import_module("paw_project_handbook")
                        handbook_result = handbook_mod.close_handbook(
                            self.db_path, pid,
                            commit_policy_write=policy_mod.commit_policy_write)
                    except Exception as exc:  # noqa: BLE001 - fail closed, never silent
                        handbook_result = {"status": "FAILED_CLOSED", "reason": str(exc)[:300]}
                    synthesis_results[-1]["handbook"] = handbook_result
                    if _text(handbook_result.get("status")) not in {"COMMITTED", "ALREADY_FRESH"}:
                        item["handbook_synthesis"] = handbook_result
                failed_synthesis = [x for x in synthesis_results if _text(x.get("status")) not in {"COMMITTED", "NO_CHANGE", "SKIPPED"}]
                item["project_synthesis"] = {"status": "EXECUTED" if not failed_synthesis else "FAILED_CLOSED", "impacted_project_ids": ids, "results": synthesis_results, "project_first_runtime_health": "FAIL" if failed_synthesis else "PASS"}
                if failed_synthesis:
                    item["cross_project_synthesis"] = {"status": "FAILED_CLOSED", "reason": "project_synthesis_failed", "formal_state_bypass": 0}
                    return
                # P0-B: bounded relation neighbourhood (impacted projects +
                # existing relation neighbours + semantic candidate
                # neighbours); never a full Project graph scan.
                scope_ids = outcome_mod.relation_scope(self.db_path, ids, semantic)
                # P0 secondary boundary defence for the relation scope too.
                scope_ids = [
                    pid for pid in scope_ids
                    if pid in project_rows and outcome_mod.is_current_business_project(project_rows[pid])
                ]
                scope_rows = [project_rows[pid] for pid in scope_ids]
                scope_outcomes = {pid: outcome_mod.read_outcome(self.db_path, pid) for pid in scope_ids}
                scope_outcomes = {pid: value for pid, value in scope_outcomes.items() if value}
                desired_relations = outcome_mod.build_relations(scope_rows, scope_outcomes)
                existing_relations = outcome_mod.read_relations(self.db_path, scope_ids)
                relations = outcome_mod.relation_lifecycle(existing_relations, desired_relations)
            finally:
                conn.close()
            relation_results = []
            for relation in relations:
                refs = list(relation.get("evidence_refs") or [])
                source_refs = ["source_version:" + _text(x.get("source_version_id")) for x in refs if _text(x.get("source_version_id"))]
                write = {"id": "incremental-project-relation:" + relation["idempotency_key"][:32], "target_type": "project", "operation": "update", "payload": {"id": relation["from_project_id"], "project_relation": relation}, "source_refs": source_refs or ["project:" + relation["from_project_id"], "project:" + relation["to_project_id"]], "evidence_refs": refs, "approval_source": "SYSTEM_EVIDENCE_GOVERNED_WRITE_V1", "preanalysis_model": "deterministic-project-synthesizer-v1", "authoritative_model": "deterministic-project-relation-v1"}
                committed = policy_mod.commit_policy_write(write, db_path=self.db_path, bundle={"id": write["id"], "state": "CLEAR", "coverage_state": "CONSOLIDATED", "source_refs": write["source_refs"], "evidence_refs": refs}, mark_policy=False)
                relation_results.append({"relation_id": relation["relation_id"], "status": committed.get("status"), "commit_record_id": committed.get("commit_record_id")})
            failed_relations = [x for x in relation_results if _text(x.get("status")) != "COMMITTED"]
            item["cross_project_synthesis"] = {"status": "EXECUTED" if not failed_relations else "FAILED_CLOSED", "relation_count": len(relations), "relation_writes": relation_results, "formal_state_bypass": 0}
            if failed_relations:
                item["project_synthesis"] = {**(item.get("project_synthesis") or {}), "project_first_runtime_health": "FAIL"}
        except Exception as exc:
            item["project_synthesis"] = {"status": "FAILED_CLOSED", "reason": str(exc)[:500], "project_first_runtime_health": "FAIL"}
            item["cross_project_synthesis"] = {"status": "FAILED_CLOSED", "reason": str(exc)[:500], "formal_state_bypass": 0}

    def run_once(self, *, enabled: bool = True, limit: int = 5, handoff: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self._refresh_slots()
        ingest = self.ingest_handoff(handoff)
        data = self.load()
        data["enabled"] = bool(enabled)
        if not enabled:
            data["status"] = "DEFERRED_BOOTSTRAP_NOT_COMPLETE"
            self._save(data)
            return {"status": data["status"], "ingest": ingest, "processed": 0, "semantic_processing": False, "formal_state_bypass": 0}
        processed = 0
        # R1 fix: eligibility-first selection. Terminal / paused / waiting-
        # external-semantic items no longer consume the per-round budget, so a
        # queue head of terminal records cannot starve actionable work behind
        # them. Bounded scanning is preserved (we stop at the budget); waiting
        # and hold semantics are unchanged (they are skipped, not mutated).
        budget = max(1, int(limit))
        scanned = 0
        for item in list(data["items"].values()):
            scanned += 1
            if not isinstance(item, dict) or item["analysis_state"] in TERMINAL_STATES:
                continue
            if processed >= budget:
                break
            try:
                if self.patrol_bound:
                    if item["analysis_state"] in {DISCOVERED, READER_READY}:
                        self._deterministic_prepare(data, item)
                    elif item["analysis_state"] in {LUNA_PENDING, LUNA_RUNNING, LUNA_COMPLETE}:
                        item["next_action"] = "WAIT_GPT_PATROL_LEGACY_LUNA_COMPAT"
                    elif item["analysis_state"] == SAFE_APPLY_PENDING:
                        # Gate-0 fix (category C): drive the deterministic governed
                        # apply for GPT SAFE decisions in patrol-bound mode too.
                        # The semantic decision already came from GPT; this path is
                        # pure deterministic Candidate/ApprovedChange/Committer.
                        self._safe_apply(item)
                else:
                    if item["analysis_state"] in {DISCOVERED, READER_READY, LUNA_PENDING, LUNA_RUNNING}:
                        self._luna_step(data, item)
                    if item["analysis_state"] == LUNA_COMPLETE:
                        self._route(data, item)
                    if item["analysis_state"] == SAFE_APPLY_PENDING and not self.patrol_bound:
                        self._safe_apply(item)
                processed += 1
            except Exception as exc:
                self._hold(item, "pipeline_exception:" + str(exc))
            self._save(data)
        data["status"] = "RUNNING" if processed else "IDLE"
        self._save(data)
        return {"status": data["status"], "ingest": ingest, "processed": processed, "states": self.state_counts(data), "semantic_processing": (not self.patrol_bound), "formal_state_bypass": 0, "full_corpus_reread": False}

    def _luna_capacity_available(self) -> bool:
        """Preflight for the Codex/Luna submit path (technical debt I).

        ``prepare_patrol_items`` is a bounded batch, but the Codex operator only
        admits ``MAX_CONCURRENT_WRITE_JOBS`` jobs at a time.  Without a preflight
        the tail of every batch walked into ``WORKSPACE_BUSY``.  Asking first
        lets the batch stop cleanly and leave the rest parked for the next Patrol
        pass.  A preflight must never be able to break the pass, so every failure
        mode degrades to "assume capacity".
        """
        operator = getattr(self.luna, "_operator", None)
        if not callable(operator):
            return True
        try:
            op = operator()
        except Exception:  # noqa: BLE001
            return True
        limit = getattr(op, "MAX_CONCURRENT_WRITE_JOBS", None)
        counter = getattr(op, "_count_active_jobs", None)
        if not isinstance(limit, int) or not callable(counter):
            return True
        try:
            return int(counter()) < limit
        except Exception:  # noqa: BLE001
            return True

    def prepare_patrol_items(self, *, limit: int = 10) -> dict[str, Any]:
        """Deterministic Evidence Packet Builder. No LLM call. No Formal State write.

        P0 architecture correction: this method is now a pure deterministic
        prepare step. It verifies Reader Result availability, Evidence Locator
        availability, artifact availability, performs duplicate/idempotency
        checks, existing formal lineage checks, impacted Project ID deterministic
        lookup, builds a bounded packet, persists it, and transitions
        READER_READY → PATROL_READY.

        Bounded batch: ``limit`` counts *attempts*, not storage order.  The
        whole candidate set is scanned and non-preparable items (terminal
        states, or anything that is not DISCOVERED / READER_READY) are
        skipped, so terminal items at the head of the state file can no
        longer starve the READER_READY backlog behind them.

        Forbidden: Codex, Luna, Agy, DeepSeek, Opus, Workspace Agent API,
        any LLM, semantic summary, findings, knowledge proposal,
        project consolidation, metric proposal, write plan,
        semantic project attribution, semantic risk classification,
        semantic NO_ACTION.
        """
        data = self.load()
        budget = max(1, int(limit))
        scanned = 0
        skipped_terminal = 0
        skipped_not_ready = 0
        promoted_reader_ready = 0
        deferred = 0
        deferred_reasons: dict[str, int] = {}
        deferred_actions: dict[str, int] = {}
        processed = 0
        # Technical debt fix (READER_READY_QUEUE_HEAD_BLOCKING): the previous
        # implementation took ``list(data["items"].values())[:limit]`` - the
        # first N *dict-order* entries - and skipped terminal states inside that
        # window.  When the head of the state file held terminal items
        # (EXPLICIT_HOLD / FINALIZED / PATROL_READY) the real READER_READY
        # backlog behind them was never even inspected: ``processed`` stayed at
        # 0 while the same N terminal items were re-examined on every pass.
        #
        # The batch is bounded by *attempts*, not by storage order: walk the
        # whole candidate set in order, skip anything that is not preparable,
        # and stop once ``budget`` items have been attempted.  The per-item
        # deterministic gate (_deterministic_prepare) is unchanged, so
        # fail-closed behaviour is preserved: an item whose reader result or
        # evidence carrier is unavailable still stays READER_READY /
        # WAIT_EVIDENCE_MATERIALIZATION and is never forced forward.
        for item in list(data["items"].values()):
            if processed >= budget:
                break
            if not isinstance(item, dict):
                continue
            scanned += 1
            if item["analysis_state"] in {WRITE_COMMITTED, FINALIZED, EXPLICIT_HOLD, PATROL_READY, USER_DECISION_PENDING, USER_DECISION_RECEIVED_WAITING_NEXT_PATROL, TERRA_PENDING, TERRA_RUNNING, TERRA_COMPLETE, OPUS_PENDING, OPUS_RUNNING, OPUS_COMPLETE, SAFE_APPLY_PENDING, APPLYING, ROUTED}:
                skipped_terminal += 1
                continue
            if item["analysis_state"] not in {DISCOVERED, READER_READY}:
                skipped_not_ready += 1
                continue
            before_state = item["analysis_state"]
            try:
                self._deterministic_prepare(data, item)
                if item["analysis_state"] == PATROL_READY:
                    processed += 1
                else:
                    # Fail-closed: no packet could be built (missing reader
                    # result or empty evidence carrier).  The item is left
                    # exactly as the gate set it - never forced forward - and
                    # does not consume the batch budget.
                    deferred += 1
                    deferred_reasons[item["analysis_state"]] = (
                        deferred_reasons.get(item["analysis_state"], 0) + 1
                    )
                    if _text(item.get("next_action")):
                        deferred_actions[item["next_action"]] = (
                            deferred_actions.get(item["next_action"], 0) + 1
                        )
                    # DISCOVERED -> READER_READY is a real forward step and is
                    # counted as an attempt (though not as a PATROL promotion).
                    if before_state != READER_READY and item["analysis_state"] == READER_READY:
                        promoted_reader_ready += 1
            except Exception as exc:
                self._hold(item, "deterministic_prepare_exception:" + str(exc))
                deferred += 1
            self._save(data)
        return {"status": "PREPARED", "processed": processed, "capacity_exhausted": False, "scanned": scanned, "limit": budget, "skipped_terminal": skipped_terminal, "skipped_not_ready": skipped_not_ready, "promoted_reader_ready": promoted_reader_ready, "deferred": deferred, "deferred_reasons": deferred_reasons, "deferred_actions": deferred_actions, "states": self.state_counts(data), "formal_state_writes": False, "formal_state_bypass": 0, "no_model_call_in_prepare": True}

    @classmethod
    def read_semantic_items(cls, *, state_path: Path | None = None, limit: int = 10) -> dict[str, Any]:
        """Read the queue without constructing model/committer adapters.

        Slot resolution can emit audit records or create shadow directories.
        A read-only MCP listing must not perform that initialization, even
        when a slot is disabled, misconfigured, or shadow-enabled.
        """
        store = LockedJsonStore(state_path or default_paths()["state"])
        return cls._semantic_items_view(store.load(), limit=limit)

    @staticmethod
    def _semantic_items_view(data: Mapping[str, Any], *, limit: int) -> dict[str, Any]:
        ready = [item for item in (data.get("items") or {}).values()
                 if isinstance(item, dict) and item.get("analysis_state") == PATROL_READY]
        # Sort/select metadata before opening packets. Do not read unreturned
        # evidence, truncate a selected packet, or change the queue's order.
        ready.sort(key=lambda item: item.get("created_at") or "")
        items = []
        for item in ready[:max(1, min(int(limit), 50))]:
            packet_ref = _text(item.get("evidence_packet_ref"))
            packet = _read_json(packet_ref) if packet_ref and Path(packet_ref).is_file() else {}
            items.append({
                "source_version_id": item.get("source_version_id"),
                "analysis_state": item.get("analysis_state"),
                "evidence_packet": packet,
                "claim_owner": item.get("claim_owner"),
                "lease_expires_at": item.get("lease_expires_at"),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
            })
        return {"schema": "paw-gpt-primary-semantic-queue/1", "items": items, "count": len(ready), "semantic_owner": "CHATGPT_GPT_AGENT", "formal_state_bypass": 0}

    def list_semantic_items(self, *, limit: int = 10) -> dict[str, Any]:
        """List PATROL_READY items for GPT primary semantic review.

        Returns bounded Evidence Packets without any server-side LLM output.
        GPT (ChatGPT Workspace Agent / Scheduled Task) is the primary semantic owner.
        """
        return self._semantic_items_view(self.load(), limit=limit)

    def claim_semantic_items(self, *, source_version_ids: list[str], lease_seconds: int = 3600,
                             worker_id: str | None = None, resume_existing: bool = False) -> dict[str, Any]:
        """Claim PATROL_READY items for GPT primary semantic review.

        Sets a lease so only one GPT Agent turn processes each item.
        No LLM call. No Formal State write.

        R3 fix: the read-check-update cycle now runs inside the store's
        cross-process mutate() context (flock + atomic write), and each claim
        mints a unique claim_token bound to (item, owner, lease).  submit
        verifies the token so a stale claimant cannot submit after the lease
        expires and the item is re-claimed.
        """
        claimed = []
        skipped = []
        claimant = _text(worker_id)
        if len(claimant) > 160:
            raise IncrementalError("worker_id_too_long")
        if resume_existing and not claimant:
            raise IncrementalError("claim_resume_requires_worker_id")
        duration = max(60, min(int(lease_seconds), 3600))
        with self.state_store.mutate() as data:
            if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
                data.clear()
                data.update(_blank_state())
            for sid in list(dict.fromkeys(source_version_ids))[:50]:
                item = data["items"].get(sid)
                if not isinstance(item, dict):
                    continue
                now = _now_epoch()
                if item.get("analysis_state") == PATROL_READY:
                    # P0.1 Evidence Claim Gate: an item whose evidence is not READY
                    # must not enter normal GPT semantic claim.
                    if _text(item.get("evidence_state")) and item.get("evidence_state") != "READY":
                        continue
                    if item.get("claim_owner") and float(item.get("lease_expires_epoch") or 0) >= now:
                        if not (resume_existing and claimant == item.get("claim_worker_id") and item.get("claim_token")):
                            skipped.append({"source_version_id": sid, "reason": "active_lease",
                                            "lease_expires_at": item.get("lease_expires_at")})
                            continue
                        resumed = True
                    else:
                        resumed = False
                        item["claim_owner"] = PRIMARY_ANALYSIS_OWNER
                        item["claim_worker_id"] = claimant or None
                        item["lease_expires_epoch"] = now + duration
                        item["lease_expires_at"] = datetime.fromtimestamp(now + duration, timezone.utc).isoformat()
                        item["claim_token"] = uuid.uuid4().hex
                    packet_ref = _text(item.get("evidence_packet_ref"))
                    packet = _read_json(packet_ref) if packet_ref and Path(packet_ref).is_file() else {}
                    if not resumed:
                        item["claim_packet_fingerprint"] = hash_payload(packet)
                    claimed.append({
                        "source_version_id": sid,
                        "claim_token": item["claim_token"],
                        "worker_id": item.get("claim_worker_id"),
                        "lease_expires_at": item["lease_expires_at"],
                        "resumed": resumed,
                        "evidence_packet": packet,
                    })
        # Preserve the complete legacy packets, but deliver the small audit
        # receipts before them. A large Evidence Packet must not bury the
        # credentials required by the caller to submit or resume safely.
        receipts = [{k: row[k] for k in ("source_version_id", "claim_token", "worker_id", "lease_expires_at", "resumed")}
                    for row in claimed]
        return {"claim_receipts": receipts, "claimed": claimed, "skipped": skipped, "count": len(claimed), "semantic_owner": "CHATGPT_GPT_AGENT", "formal_state_bypass": 0}

    def submit_semantic_decision(self, *, source_version_id: str, route: str,
                                 findings: list[Mapping[str, Any]] | None = None,
                                 knowledge_proposals: list[Mapping[str, Any]] | None = None,
                                 project_proposals: list[Mapping[str, Any]] | None = None,
                                 metric_proposals: list[Mapping[str, Any]] | None = None,
                                 evidence_refs: list[Mapping[str, Any]] | None = None,
                                 uncertainty: str | None = None,
                                 conflicts: list[Mapping[str, Any]] | None = None,
                                 project_attribution: str | None = None,
                                 business_significance: str | None = None,
                                 reason_codes: list[str] | None = None,
                                 write_plan: list[Mapping[str, Any]] | None = None,
                                 claim_token: str | None = None) -> dict[str, Any]:
        """Accept GPT semantic decision and route through deterministic router.

        GPT is the primary semantic owner. The router is deterministic and
        does not write Formal State. Governance (Terra/Opus/User/Safe) follows.
        """
        route = _text(route).upper()
        if route not in ROUTES:
            raise IncrementalError(f"unsupported_route:{route}")
        data = self.load()
        item = data["items"].get(source_version_id)
        if not isinstance(item, dict):
            raise KeyError(source_version_id)
        # R3: lease/credential verification
        now = _now_epoch()
        lease_active = bool(item.get("claim_owner")) and float(item.get("lease_expires_epoch") or 0) >= now
        if item.get("analysis_state") == PATROL_READY and item.get("claim_token") and not lease_active:
            raise IncrementalError("claim_lease_expired:reclaim_required")
        if lease_active:
            if not _text(claim_token):
                raise IncrementalError("claim_token_required:active_lease")
            if _text(claim_token) != _text(item.get("claim_token")):
                raise IncrementalError("claim_token_mismatch:stale_or_foreign_claim")
        # Persist GPT semantic decision
        decision = {
            "source_version_id": source_version_id,
            "route": route,
            "findings": [dict(x) for x in _as_list(findings) if isinstance(x, Mapping)],
            "knowledge_proposals": [dict(x) for x in _as_list(knowledge_proposals) if isinstance(x, Mapping)],
            "project_proposals": [dict(x) for x in _as_list(project_proposals) if isinstance(x, Mapping)],
            "metric_proposals": [dict(x) for x in _as_list(metric_proposals) if isinstance(x, Mapping)],
            "evidence_refs": [dict(x) for x in _as_list(evidence_refs) if isinstance(x, Mapping)],
            "uncertainty": _text(uncertainty) or None,
            "conflicts": [dict(x) for x in _as_list(conflicts) if isinstance(x, Mapping)],
            "project_attribution": _text(project_attribution) or None,
            "business_significance": _text(business_significance) or None,
            "reason_codes": [_text(x) for x in (reason_codes or []) if _text(x)],
            "write_plan": [dict(x) for x in _as_list(write_plan) if isinstance(x, Mapping)],
            "submitted_by": "CHATGPT_GPT_AGENT",
            "submitted_at": utc_now(),
        }
        proposals_present = any(decision[key] for key in (
            "knowledge_proposals", "project_proposals", "metric_proposals"))
        if route == "NO_ACTION" and (proposals_present or decision["write_plan"]):
            raise IncrementalError("semantic_no_action_conflicts_with_change_proposals_or_plan")
        if route == SAFE_AUTO_APPLY and proposals_present and not decision["write_plan"]:
            raise IncrementalError("semantic_compilation_write_plan_required")
        decision_hash = hash_payload({k: v for k, v in decision.items() if k != "submitted_at"})
        if item.get("analysis_state") != PATROL_READY:
            if item.get("semantic_decision_hash") == decision_hash and _text(claim_token) == _text(item.get("claim_token")):
                return {"source_version_id": source_version_id, "analysis_state": item["analysis_state"],
                        "risk_route": item.get("risk_route"), "routed": True, "idempotent": True,
                        "formal_state_bypass": 0}
            raise IncrementalError(f"item_not_patrol_ready:{item.get('analysis_state')}")
        packet = _read_json(_text(item.get("evidence_packet_ref")))
        if lease_active and item.get("claim_packet_fingerprint") and item["claim_packet_fingerprint"] != hash_payload(packet):
            raise IncrementalError("claim_evidence_packet_changed:reclaim_required")
        refs = decision["evidence_refs"]
        if route != INSUFFICIENT_EVIDENCE and not refs:
            raise IncrementalError("semantic_evidence_refs_required")
        _validate_evidence_refs(refs, source_version_id)
        allowed = {(str(r.get("source_version_id")), str(r.get("evidence_locator"))): r
                   for r in packet.get("evidence_refs", []) if isinstance(r, Mapping)}
        # Validate nested plans as well as top-level refs before routing.
        def nested_evidence(value):
            if isinstance(value, Mapping):
                for key, child in value.items():
                    if key == "evidence_refs":
                        if not isinstance(child, list) or any(not isinstance(r, Mapping) for r in child):
                            raise IncrementalError("semantic_evidence_refs_malformed")
                        yield from child
                    elif key == "notes" and isinstance(child, str):
                        # Metric notes are a JSON object inside the payload.
                        # Its refs must pass the SAME claimed-packet checks;
                        # opaque prose is not interpreted as evidence.
                        try:
                            parsed_notes = json.loads(child)
                        except (ValueError, TypeError):
                            continue
                        if isinstance(parsed_notes, Mapping):
                            yield from nested_evidence(parsed_notes)
                    else:
                        yield from nested_evidence(child)
            elif isinstance(value, list):
                for child in value:
                    yield from nested_evidence(child)

        submitted_refs = list(nested_evidence(decision))
        _validate_evidence_refs(submitted_refs, source_version_id)
        for ref in submitted_refs:
            original = allowed.get((str(ref.get("source_version_id")), str(ref.get("evidence_locator"))))
            if original is None:
                raise IncrementalError("semantic_evidence_not_in_claimed_packet")
            for key in ("raw_artifact_sha256", "content_fingerprint", "reader_result_ref", "artifact_sha256"):
                if key in ref and ref[key] != original.get(key):
                    raise IncrementalError(f"semantic_evidence_identity_mismatch:{key}")
        self.results_dir.mkdir(parents=True, exist_ok=True)
        decision_path = self.results_dir / (hash_payload({"source_version_id": source_version_id, "decision_hash": decision_hash})[:32] + ".json")
        atomic_write_json(decision_path, decision)
        item["gpt_semantic_decision_ref"] = str(decision_path)
        item["semantic_decision_hash"] = decision_hash
        item["risk_route"] = route
        # R2 fix: normalize the decision object for the downstream safe gate.
        # The legacy _route path writes item["risk_decision"] = dict(decision);
        # the GPT semantic submit path wrote only risk_route, so the safe gate's
        # revalidation (risk_decision.route == SAFE_AUTO_APPLY) rejected legal
        # safe writes.  This is a thin adapter: the caller-supplied route is an
        # INPUT to the deterministic validation below, never an approval
        # credential — every safe-gate check (evidence, allowed operations,
        # target identity, conflicts, material decisions) still applies.
        item["risk_decision"] = {
            "route": route,
            "reason_codes": decision["reason_codes"],
            "write_plan": decision["write_plan"],
            "decision_source": "gpt_semantic_submit",
        }
        item["risk_reason_codes"] = decision["reason_codes"]
        item["evidence_refs"] = decision["evidence_refs"]
        item["project_id"] = _text(project_attribution) or item.get("project_id")
        item["write_plan"] = decision["write_plan"]
        item["cluster_key"] = _cluster_key({**item, **decision})
        # Transition through deterministic router
        _transition(item, ROUTED, reason="gpt_semantic_decision_routed")
        # Gate-0 fix (category C): a GPT SAFE decision must NOT loop back to
        # PATROL_READY in patrol-bound mode. The GPT semantic decision IS the
        # patrol authorization; the governed apply is deterministic downstream.
        safe_target = SAFE_APPLY_PENDING
        # Gate-0 fix (category B): adapt the GPT semantic decision into the
        # downstream semantic envelope contract (luna_result shape) so SAFE
        # gate, Terra view and project synthesis consume GPT output, not the
        # disabled legacy Luna pre-analysis.
        item["luna_result"] = {
            "summary": decision.get("business_significance"),
            "findings": decision["findings"],
            "evidence_refs": decision["evidence_refs"],
            "knowledge_proposals": decision["knowledge_proposals"],
            "project_consolidations": decision["project_proposals"],
            "metric_proposals": decision["metric_proposals"],
            "write_plan": decision["write_plan"],
            "risk_hints": decision["reason_codes"],
            "unresolved_conflict": bool(decision["conflicts"]),
            "semantic_owner": "CHATGPT_GPT_AGENT",
            "gpt_semantic_decision_ref": str(decision_path),
        }
        targets = {
            SAFE_AUTO_APPLY: safe_target,
            TERRA_REVIEW_REQUIRED: TERRA_PENDING,
            OPUS_REVIEW_REQUIRED: OPUS_PENDING,
            USER_DECISION_REQUIRED: USER_DECISION_PENDING,
            INSUFFICIENT_EVIDENCE: EXPLICIT_HOLD,
            "NO_ACTION": FINALIZED,
        }
        target = targets[route]
        _transition(item, target, reason="route:" + route)
        item["next_action"] = {
            SAFE_APPLY_PENDING: "GOVERNED_DETERMINISTIC_APPLY",
            PATROL_READY: "WAIT_GPT_PATROL_SEMANTIC_BUNDLE",
            TERRA_PENDING: "WAIT_TERRA_PATROL",
            OPUS_PENDING: "OPUS_CLUSTER_DISPATCH",
            USER_DECISION_PENDING: "WAIT_USER_MATERIAL_DECISION",
            EXPLICIT_HOLD: "WAIT_ADDITIONAL_EVIDENCE",
            FINALIZED: "FINALIZED_NO_ACTION",
        }[target]
        if target == EXPLICIT_HOLD:
            item["last_error"] = ";".join(item["risk_reason_codes"])[:1000] or "insufficient_evidence"
        item["claim_owner"] = None
        item["lease_expires_at"] = None
        item["lease_expires_epoch"] = None
        self._save(data)
        return {"source_version_id": source_version_id, "analysis_state": item["analysis_state"], "risk_route": item["risk_route"], "routed": True, "formal_state_bypass": 0}

    def rebuild_patrol_evidence_packet(self, *, source_version_id: str) -> dict[str, Any]:
        """Narrow maintenance: deterministically rebuild a PATROL_READY Evidence Packet.

        P0.1 constraints: only PATROL_READY items; semantic state and route are
        unchanged; no Formal State write; no model call; packet revision +1;
        idempotent (rebuilding an already-current packet is a no-op that still
        reports the current revision).
        """
        data = self.load()
        item = data["items"].get(source_version_id)
        if not isinstance(item, dict):
            raise KeyError(source_version_id)
        if item.get("analysis_state") != PATROL_READY:
            raise IncrementalError(f"rebuild_requires_patrol_ready:{item.get('analysis_state')}")
        sid = item["source_version_id"]
        reader_path = _text(item.get("reader_result_path"))
        if not reader_path or not Path(reader_path).is_file():
            raise IncrementalError("reader_result_unavailable")
        reader_data = _read_json(reader_path)
        # Deterministic evidence derivation (same rules as _deterministic_prepare).
        artifact_hash = _text(reader_data.get("raw_artifact_sha256")) or _text(item.get("sha256"))
        # Same shared Evidence Materializer as _deterministic_prepare: the two
        # evidence-generation paths must not fork.
        evidence_refs = _materialize_evidence(
            reader_data,
            sid=sid,
            reader_path=reader_path,
            artifact_hash=artifact_hash,
            content_fingerprint=_text(reader_data.get("content_fingerprint")) or _text(item.get("content_fingerprint")),
        )
        _validate_evidence_refs(evidence_refs, sid)
        if not evidence_refs:
            raise IncrementalError("evidence_state_missing:rebuild_refused")
        artifact_path = _text(item.get("artifact_path"))
        artifact_available = bool(artifact_path and Path(artifact_path).is_file())
        old_revision = int(item.get("evidence_packet_revision") or 1)
        new_revision = old_revision + 1
        packet = {
            "source_version_id": sid,
            "reader_result_ref": reader_path,
            "artifact_ref": artifact_path or None,
            "artifact_availability": artifact_available,
            "raw_artifact_available": artifact_available,
            "reader_evidence_available": True,
            "evidence_state": "READY",
            "evidence_refs": evidence_refs,
            "evidence_schema_version": _text(reader_data.get("evidence_schema_version")) or None,
            "project_candidates_deterministic": [_text(item.get("project_id"))] if _text(item.get("project_id")) else [],
            "formal_lineage_refs": item.get("formal_lineage_refs") or [],
            "bounded_context_refs": {
                "canonical_topic": _text(item.get("canonical_topic")),
                "change_type": _text(item.get("change_type")),
                "sha256": _text(item.get("sha256")),
                "content_fingerprint": _text(item.get("content_fingerprint")),
            },
            "freshness": {
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "packet_built_at": utc_now(),
                "rebuilt": True,
            },
            "packet_revision": new_revision,
            "no_model_call": True,
            "no_semantic_analysis": True,
        }
        self.results_dir.mkdir(parents=True, exist_ok=True)
        packet_path = self.results_dir / (hash_payload({"source_version_id": sid, "packet": "deterministic_prepare"})[:32] + ".json")
        atomic_write_json(packet_path, packet)
        # Semantic state, route, claim_owner and lease are NOT touched.
        item["evidence_packet_ref"] = str(packet_path)
        item["evidence_packet_revision"] = new_revision
        item["evidence_state"] = "READY"
        self._save(data)
        return {
            "source_version_id": sid,
            "packet_revision": new_revision,
            "evidence_ref_count": len(evidence_refs),
            "analysis_state": item.get("analysis_state"),
            "risk_route": item.get("risk_route"),
            "claim_owner": item.get("claim_owner"),
            "lease_expires_at": item.get("lease_expires_at"),
            "formal_state_bypass": 0,
            "no_model_call": True,
        }

    @staticmethod
    def state_counts(data: Mapping[str, Any]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in (data.get("items") or {}).values():
            state = _text(item.get("analysis_state")) if isinstance(item, Mapping) else "UNKNOWN"
            counts[state] = counts.get(state, 0) + 1
        return counts

    @classmethod
    def read_terra_items(cls, *, state_path: Path | None = None, limit: int = 5) -> dict[str, Any]:
        """Read Terra work without model/committer initialization or lease writes."""
        store = LockedJsonStore(state_path or default_paths()["state"])
        return cls._terra_items_view(store.load(), limit=limit)

    @classmethod
    def _terra_items_view(cls, data: Mapping[str, Any], *, limit: int) -> dict[str, Any]:
        now = _now_epoch()
        items = []
        for item in (data.get("items") or {}).values():
            if not isinstance(item, dict):
                continue
            if item.get("analysis_state") == TERRA_RUNNING and float(item.get("lease_expires_epoch") or 0) < now:
                # Expiry is a read projection only. The actual transition and
                # checkpoint belong to the separately authorized claim path.
                item = dict(item, analysis_state=TERRA_PENDING)
            if item.get("analysis_state") == TERRA_PENDING:
                items.append(cls._terra_view(item))
        items.sort(key=lambda x: x.get("created_at") or "")
        return {"schema": "paw-incremental-terra-review/1", "items": items[:max(1, min(int(limit), 50))], "count": len(items), "bounded_batch": True, "formal_state_bypass": 0}

    def list_terra(self, *, limit: int = 5) -> dict[str, Any]:
        return self._terra_items_view(self.load(), limit=limit)

    @staticmethod
    def _terra_view(item: Mapping[str, Any]) -> dict[str, Any]:
        result = item.get("luna_result") or {}
        return {
            "source_version_id": item.get("source_version_id"),
            "reader_result_path": item.get("reader_result_path"),
            "luna_summary": result.get("summary"),
            "findings": result.get("findings") or [],
            "evidence_refs": item.get("evidence_refs") or result.get("evidence_refs") or [],
            "project_id": item.get("project_id"),
            "canonical_topic": item.get("canonical_topic"),
            "risk_route": item.get("risk_route"),
            "risk_reason_codes": item.get("risk_reason_codes") or [],
            "cluster_key": item.get("cluster_key"),
            "analysis_state": item.get("analysis_state"),
            "source_item_key": item.get("source_version_id"),
        }

    @staticmethod
    def _terra_packet_hash(item: Mapping[str, Any]) -> str:
        # Lease/state metadata is excluded; the actual review input is bound.
        return hash_payload({key: item.get(key) for key in (
            "source_version_id", "sha256", "reader_result_path", "luna_result",
            "evidence_refs", "project_id", "canonical_topic", "risk_route",
            "risk_reason_codes", "cluster_key")})

    def claim_terra(self, *, source_version_ids: list[str], worker_id: str, lease_seconds: int = DEFAULT_TERRA_LEASE_SECONDS,
                    resume_existing: bool = False) -> dict[str, Any]:
        worker_id = _text(worker_id)
        if not worker_id:
            raise IncrementalError("worker_id is required")
        claimed = []
        with self.state_store.mutate() as data:
            data.setdefault("items", {})
            for sid in list(dict.fromkeys(source_version_ids))[:50]:
                item = data["items"].get(sid)
                if not isinstance(item, dict):
                    continue
                now = _now_epoch()
                if item.get("analysis_state") == TERRA_RUNNING and float(item.get("lease_expires_epoch") or 0) <= now:
                    _transition(item, TERRA_PENDING, reason="terra_lease_expired_reclaimed")
                    item["claim_owner"] = None
                if item.get("analysis_state") == TERRA_RUNNING:
                    if not (resume_existing and item.get("claim_owner") == worker_id
                            and item.get("terra_claim_token")):
                        continue
                    if item.get("terra_claim_packet_hash") != self._terra_packet_hash(item):
                        raise IncrementalError("Terra evidence packet changed:reclaim_after_expiry")
                    claimed.append(dict(self._terra_view(item),
                        claim_token=item["terra_claim_token"], worker_id=worker_id,
                        lease_expires_at=item.get("lease_expires_at"), resumed=True,
                        evidence_packet_hash=item["terra_claim_packet_hash"]))
                    continue
                if item.get("analysis_state") != TERRA_PENDING:
                    continue
                _transition(item, TERRA_RUNNING, reason="terra_claimed")
                item["claim_owner"] = worker_id
                duration = max(60, min(int(lease_seconds), DEFAULT_TERRA_LEASE_SECONDS))
                item["lease_expires_epoch"] = now + duration
                item["lease_expires_at"] = datetime.fromtimestamp(item["lease_expires_epoch"], timezone.utc).isoformat()
                item["terra_claim_token"] = uuid.uuid4().hex
                item["terra_claim_packet_hash"] = self._terra_packet_hash(item)
                claimed.append(dict(self._terra_view(item),
                    claim_token=item["terra_claim_token"], worker_id=worker_id,
                    lease_expires_at=item["lease_expires_at"], resumed=False,
                    evidence_packet_hash=item["terra_claim_packet_hash"]))
            data.update(schema=SCHEMA, updated_at=utc_now(),
                        formal_state_bypass=0, full_corpus_reread=False)
        self._loaded_snapshot = copy.deepcopy(data)
        receipts = [{key: row[key] for key in ("source_version_id", "claim_token", "worker_id",
                    "lease_expires_at", "resumed", "evidence_packet_hash")} for row in claimed]
        return {"claimed": claimed, "claim_receipts": receipts, "worker_id": worker_id, "formal_state_bypass": 0}

    def submit_terra(self, *, source_version_id: str, worker_id: str | None, outcome: str, reason_codes: list[str] | None = None,
                     review: Mapping[str, Any] | None = None, claim_token: str | None = None) -> dict[str, Any]:
        outcome = _text(outcome).upper()
        safe_target = PATROL_READY if self.patrol_bound else SAFE_APPLY_PENDING
        mapping = {"SAFE_TO_APPLY": safe_target, "OPUS_REQUIRED": OPUS_PENDING, "USER_DECISION_REQUIRED": USER_DECISION_PENDING, "INSUFFICIENT_EVIDENCE": EXPLICIT_HOLD, "NO_ACTION": FINALIZED}
        if outcome not in mapping:
            raise IncrementalError("unsupported Terra outcome")
        reasons = [_text(x) for x in (reason_codes or []) if _text(x)]
        payload = {"source_version_id": source_version_id, "outcome": outcome,
                   "reason_codes": reasons, "review": dict(review or {})}
        payload_hash = hash_payload(payload)
        token = _text(claim_token)
        worker = _text(worker_id)
        with self.state_store.mutate() as data:
            item = (data.get("items") or {}).get(source_version_id)
            if not isinstance(item, dict):
                raise KeyError(source_version_id)
            if item.get("analysis_state") != TERRA_RUNNING:
                prior = item.get("terra_response") if isinstance(item.get("terra_response"), Mapping) else {}
                if (token and token == prior.get("claim_token")
                        and payload_hash == prior.get("payload_hash")
                        and (not worker or worker == prior.get("worker_id"))):
                    return {"source_version_id": source_version_id, "analysis_state": item.get("analysis_state"),
                            "risk_route": item.get("risk_route"), "idempotent": True, "formal_state_bypass": 0}
                raise IncrementalError("Terra claim/lease mismatch or replay conflict")
            if float(item.get("lease_expires_epoch") or 0) <= _now_epoch():
                raise IncrementalError("Terra lease expired")
            # Untokened legacy leases fail closed until their normal expiry.
            if not token or not item.get("terra_claim_token"):
                raise IncrementalError("Terra claim_token required")
            if token != item["terra_claim_token"] or (worker and worker != item.get("claim_owner")):
                raise IncrementalError("Terra claim/lease mismatch:stale_or_foreign_token")
            if item.get("terra_claim_packet_hash") != self._terra_packet_hash(item):
                raise IncrementalError("Terra evidence packet changed:reclaim_after_expiry")
            item["terra_response"] = dict(payload, submitted_at=utc_now(),
                claim_token=token, worker_id=item["claim_owner"], payload_hash=payload_hash,
                evidence_packet_hash=item["terra_claim_packet_hash"])
            _transition(item, TERRA_COMPLETE, reason="terra_review_recorded")
            _transition(item, mapping[outcome], reason="terra_outcome:" + outcome)
            item["risk_route"] = {safe_target: SAFE_AUTO_APPLY, OPUS_PENDING: OPUS_REVIEW_REQUIRED, USER_DECISION_PENDING: USER_DECISION_REQUIRED, EXPLICIT_HOLD: INSUFFICIENT_EVIDENCE, FINALIZED: "NO_ACTION"}[mapping[outcome]]
            item["risk_reason_codes"] = reasons
            item["claim_owner"] = None
            item["lease_expires_at"] = None
            item["lease_expires_epoch"] = None
            data.update(schema=SCHEMA, updated_at=utc_now(), formal_state_bypass=0, full_corpus_reread=False)
            result = {"source_version_id": source_version_id, "analysis_state": item["analysis_state"],
                      "risk_route": item["risk_route"], "formal_state_bypass": 0}
        self._loaded_snapshot = copy.deepcopy(data)
        return result

    def list_opus_clusters(self, *, limit: int = 5) -> dict[str, Any]:
        data = self.load()
        groups: dict[str, list[dict[str, Any]]] = {}
        for item in data["items"].values():
            if isinstance(item, dict) and item.get("analysis_state") == OPUS_PENDING:
                groups.setdefault(_text(item.get("cluster_key")) or _cluster_key(item), []).append(item)
        clusters = []
        for key, items in sorted(groups.items()):
            token = "PAW_OPUS_INCREMENTAL:" + key
            clusters.append({"cluster_key": key, "opus_token": token, "source_version_ids": [x.get("source_version_id") for x in items], "project_id": items[0].get("project_id"), "canonical_topic": items[0].get("canonical_topic"), "reason_codes": sorted({c for x in items for c in x.get("risk_reason_codes") or []}), "items": [self._terra_view(x) for x in items]})
        return {"schema": "paw-incremental-opus-cluster-queue/1", "clusters": clusters[:max(1, min(int(limit), 20))], "count": len(clusters), "one_time_only": True, "formal_state_bypass": 0}

    def dispatch_opus_once(self, *, limit: int = 1, adapter: OpusAdapter | None = None) -> dict[str, Any]:
        self._refresh_slots()
        active_adapter = adapter or self.opus
        data = self.load()
        # First drain already-submitted jobs.  This makes cancellation or a
        # worker restart recoverable without submitting a second Opus review.
        completed = []
        running = [x for x in data["items"].values() if isinstance(x, dict) and x.get("analysis_state") == OPUS_RUNNING]
        for item in running:
            bound_adapter = self._opus_adapter_for(item, default_adapter=adapter)
            cluster_key = _text(item.get("cluster_key"))
            cluster = {"cluster_key": cluster_key, "opus_token": item.get("opus_token"), "opus_job_id": item.get("opus_job_id"), "source_version_ids": [item.get("source_version_id")], "items": [self._terra_view(item)]}
            response = dict(bound_adapter.poll(cluster))
            if _text(response.get("status")) == "RUNNING":
                continue
            if _text(response.get("status")) != "COMPLETED":
                error_msg = _text(response.get("error")) or "opus_poll_failed"
                if "model_mismatch" in error_msg:
                    self._hold(item, error_msg)
                    continue
                if int(item.get("attempt_count") or 0) < MAX_ATTEMPTS:
                    _transition(item, OPUS_PENDING, reason="opus_retryable_failure")
                    item["last_error"] = error_msg
                else:
                    self._hold(item, error_msg)
                continue
            expected_model = _text(item.get("opus_model")) or getattr(bound_adapter, "effective_model", OPUS_MODEL)
            actual_model = _text(response.get("model")) or expected_model
            if actual_model != expected_model:
                self._hold(item, f"opus_model_mismatch: expected {expected_model}, got {actual_model}")
                continue
            outcome = _text(response.get("outcome")) or _text(response.get("review", {}).get("outcome") if isinstance(response.get("review"), Mapping) else "")
            # Grouped cluster delivery is handled below by submit_opus_result;
            # a single-member completion can be recorded immediately.
            self.submit_opus_result(cluster_key=cluster_key, effective_model=actual_model, outcome=outcome, review=response.get("review") if isinstance(response.get("review"), Mapping) else response, reason_codes=[_text(x) for x in _as_list(response.get("reason_codes")) if _text(x)])
            data = self.load()
            completed.append(cluster_key)
        pending = self.list_opus_clusters(limit=limit)["clusters"]
        processed = [{"cluster_key": key, "status": "COMPLETED"} for key in completed]
        for cluster in pending:
            key = cluster["cluster_key"]
            members = [data["items"].get(sid) for sid in cluster["source_version_ids"]]
            members = [x for x in members if isinstance(x, dict) and x.get("analysis_state") == OPUS_PENDING]
            if not members:
                continue
            input_path = self.results_dir / ("opus-" + hash_payload(cluster)[:24] + ".json")
            atomic_write_json(input_path, cluster)
            cluster["input_path"] = str(input_path)
            eff_model = getattr(active_adapter, "effective_model", OPUS_MODEL)
            prov = getattr(active_adapter, "provider", "agy")
            for item in members:
                item["opus_token"] = cluster["opus_token"]
                item["claim_owner"] = ACTOR
                item["lease_expires_epoch"] = _now_epoch() + DEFAULT_OPUS_LEASE_SECONDS
                item["lease_expires_at"] = _future_iso(DEFAULT_OPUS_LEASE_SECONDS)
                _transition(item, OPUS_RUNNING, reason="opus_cluster_claimed")
            self._save(data)
            response = dict(active_adapter.submit(cluster))
            eff_model = response.get("model") or eff_model
            prov = response.get("provider") or prov
            binding = response.get("binding")
            for item in members:
                item["opus_job_id"] = _text(response.get("job_id")) or None
                item["opus_model"] = eff_model
                item["opus_provider"] = prov
                if binding:
                    item["opus_binding"] = dict(binding)
            if _text(response.get("status")) != "RUNNING":
                for item in members:
                    self._hold(item, _text(response.get("error")) or "opus_submit_failed")
                self._save(data)
                processed.append({"cluster_key": key, "status": "FAILED"})
                continue
            self._save(data)
            processed.append({"cluster_key": key, "status": "RUNNING", "job_id": response.get("job_id"), "model": response.get("model")})
        return {"processed": processed, "formal_state_bypass": 0}

    def submit_opus_result(self, *, cluster_key: str, effective_model: str, outcome: str, review: Mapping[str, Any] | None = None, reason_codes: list[str] | None = None) -> dict[str, Any]:
        data = self.load()
        members = [x for x in data["items"].values() if isinstance(x, dict) and x.get("cluster_key") == cluster_key and x.get("analysis_state") == OPUS_RUNNING]
        if not members:
            return {"status": "ALREADY_FINALIZED_OR_NOT_CLAIMED", "cluster_key": cluster_key, "formal_state_bypass": 0}
        first_item = members[0]
        expected_model = _text(first_item.get("opus_model")) or OPUS_MODEL
        if _text(effective_model) != expected_model:
            raise IncrementalError(f"effective Opus model mismatch: expected {expected_model}, got {effective_model}")
        outcome = _text(outcome).upper()
        mapping = {"SAFE_TO_APPLY": (PATROL_READY if self.patrol_bound else SAFE_APPLY_PENDING), "USER_DECISION_REQUIRED": USER_DECISION_PENDING, "INSUFFICIENT_EVIDENCE": EXPLICIT_HOLD}
        if outcome not in mapping:
            raise IncrementalError("unsupported Opus outcome")
        target = mapping[outcome]
        for item in members:
            item["opus_response"] = {
                "outcome": outcome,
                "effective_model": effective_model,
                "provider": _text(item.get("opus_provider")) or "agy",
                "review": dict(review or {}),
                "reason_codes": reason_codes or [],
                "recorded_at": utc_now(),
            }
            _transition(item, OPUS_COMPLETE, reason="opus_review_recorded_once")
            _transition(item, target, reason="opus_outcome:" + outcome)
            item["risk_route"] = {SAFE_APPLY_PENDING: SAFE_AUTO_APPLY, PATROL_READY: SAFE_AUTO_APPLY, USER_DECISION_PENDING: USER_DECISION_REQUIRED, EXPLICIT_HOLD: INSUFFICIENT_EVIDENCE}[target]
            item["risk_reason_codes"] = [_text(x) for x in (reason_codes or []) if _text(x)]
            item["claim_owner"] = None
        self._save(data)
        return {"status": "RECORDED", "cluster_key": cluster_key, "source_version_ids": [x["source_version_id"] for x in members], "analysis_state": target, "one_time_only": True, "formal_state_bypass": 0}

    def submit_user_decision(
        self, *, source_version_id: str, decision: str, decided_by: str | None = None,
        note: str | None = None, approval_ref: str | None = None,
        review_decision_id: str | None = None, user_receipt_id: str | None = None,
        approval_refs: list[str] | Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a user material decision. Patrol-bound mode stores the receipt
        only; the next GPT Patrol must synthesize a new bundle. Immediate
        execution of the frozen write_plan is forbidden in that mode.
        """
        decision = _text(decision).upper()
        if decision not in {"APPROVE", "REJECT"}:
            raise IncrementalError("unsupported user decision")
        data = self.load()
        item = data["items"].get(_text(source_version_id))
        if not isinstance(item, dict) or item.get("analysis_state") != USER_DECISION_PENDING:
            return {"status": "ALREADY_FINALIZED_OR_NOT_PENDING", "source_version_id": _text(source_version_id), "formal_state_bypass": 0}
        refs = [_text(x) for x in (approval_ref, review_decision_id, user_receipt_id) if _text(x)]
        if len(set(refs)) > 1:
            item["last_error"] = "user_receipt_reference_conflict"
            self._save(data)
            return {"status": "DENIED", "reason": "user_receipt_reference_conflict", "source_version_id": _text(source_version_id), "analysis_state": USER_DECISION_PENDING, "formal_state_bypass": 0}
        plans = [x for x in (item.get("write_plan") or []) if isinstance(x, Mapping)]
        normalized_refs = _normalize_approval_refs(
            approval_ref=refs[0] if refs else None,
            approval_refs=approval_refs,
            write_plan=plans,
        )
        if normalized_refs is None or len(normalized_refs) != len(plans) or any(not ref for ref in normalized_refs) or len(set(normalized_refs)) != len(normalized_refs):
            item["last_error"] = "direct_user_approval_plan_coverage_mismatch"
            self._save(data)
            return {"status": "DENIED", "reason": "direct_user_approval_plan_coverage_mismatch", "source_version_id": _text(source_version_id), "analysis_state": USER_DECISION_PENDING, "formal_state_bypass": 0}
        receipts: list[dict[str, Any]] = []
        for plan, receipt_id in zip(plans, normalized_refs):
            valid, reason, receipt = _validate_user_receipt(
                db_path=self.db_path, item=item, decision=decision,
                receipt_id=receipt_id, expected_plan=plan,
            )
            if not valid:
                item["last_error"] = reason[:1000]
                self._save(data)
                return {"status": "DENIED", "reason": reason, "source_version_id": _text(source_version_id), "analysis_state": USER_DECISION_PENDING, "formal_state_bypass": 0}
            receipts.append(receipt)
        receipt_id = normalized_refs[0]
        approved_change_ids = [receipt["approved_change_id"] for receipt in receipts]
        recorded_at = utc_now()
        item["user_decision"] = {"decision": decision, "decided_by": receipts[0].get("reviewer"), "note": _text(note), "recorded_at": recorded_at, "approval_ref": receipt_id, "approval_refs": normalized_refs, "receipt_review_channel": receipts[0].get("review_channel")}
        if decision == "APPROVE":
            item["user_decision_approval"] = {
                "decision": "APPROVE", "approval_ref": receipt_id,
                "approval_refs": {str(index): value for index, value in enumerate(normalized_refs)},
                "approved_change_ids": approved_change_ids,
                "recorded_at": recorded_at,
                "approval_source": "direct_user_approved_change_v1",
                "review_channel": receipts[0].get("review_channel"),
                "lineage": [
                    {"approved_change_id": receipt["approved_change_id"], "review_decision_id": receipt["approval_ref"]}
                    for receipt in receipts
                ],
            }
            if self.patrol_bound:
                from paw_patrol_contract import mark_write_plan_stale
                mark_write_plan_stale(item)
                try:
                    from paw_patrol_orchestrator import PatrolOrchestrator
                    PatrolOrchestrator(self.patrol_store_path, db_path=self.db_path).record_user_decision_receipt(
                        receipt_id=receipt_id,
                        subject_kind="source",
                        subject_id=_text(source_version_id),
                        decision=decision,
                        original_input_ref=_text(source_version_id),
                        note=note,
                    )
                except Exception as exc:
                    item["patrol_enqueue_error"] = str(exc)[:300]
                _transition(item, USER_DECISION_RECEIVED_WAITING_NEXT_PATROL, reason="user_decision_received_waiting_next_patrol")
                item["next_action"] = "WAIT_NEXT_GPT_PATROL"
                item["immediate_execution_after_user_review"] = False
            else:
                _transition(item, SAFE_APPLY_PENDING, reason="user_decision_approved")
                item["next_action"] = "GOVERNED_DETERMINISTIC_APPLY"
        else:
            _transition(item, EXPLICIT_HOLD, reason="user_decision_rejected")
            item["next_action"] = "EXPLICIT_HOLD"
            item["last_error"] = "user_decision_rejected"
        self._save(data)
        return {"status": "RECORDED", "source_version_id": _text(source_version_id), "decision": decision, "analysis_state": item["analysis_state"], "immediate_execution": False if self.patrol_bound else True, "one_time_only": True, "formal_state_bypass": 0}

    def status(self) -> dict[str, Any]:
        data = self.load()
        return {"schema": SCHEMA, "status": data.get("status"), "enabled": data.get("enabled"), "state_counts": self.state_counts(data), "item_count": len(data.get("items") or {}), "formal_state_bypass": 0, "full_corpus_reread": False}


def is_incremental_enabled(brain_path: Path | None = None) -> tuple[bool, str]:
    brain = _read_json(brain_path or default_paths()["brain"])
    bootstrap = brain.get("bootstrap") or {}
    legacy_ready = (
        _text(bootstrap.get("status")) == "COMPLETED"
        and bool((brain.get("patrol") or {}).get("enabled"))
    )
    # PROJECT_FIRST is an explicit, separately auditable gate.  It allows
    # incremental processing after the machine-actionable historical backlog
    # is closed while legitimate Terra/Opus/User/insufficient queues remain
    # durable.  Test/alternate Brain paths stay on the legacy gate.
    gate_ready = False
    if brain_path is None or Path(brain_path) == default_paths()["brain"]:
        gate_path = Path(os.environ.get("PAW_PROJECT_FIRST_GATE_PATH", "/var/lib/paw-mcp-gateway/paw_project_first_incremental_gate.json"))
        try:
            gate = _read_json(gate_path)
            gate_ready = (
                _text(gate.get("status")) == "ENABLED"
                and int(gate.get("machine_actionable_backlog_remaining") or 0) == 0
                and int(gate.get("baseline_projects") or 0) > 0
                and int(gate.get("baseline_readback_pass") or 0) == int(gate.get("baseline_projects") or 0)
                and gate.get("full_corpus_reread") is False
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            gate_ready = False
    if legacy_ready or gate_ready:
        return True, "ready_project_first_gate" if gate_ready and not legacy_ready else "ready"
    if _text(bootstrap.get("status")) != "COMPLETED":
        return False, "bootstrap_not_completed"
    return False, "patrol_not_enabled"


def read_only_handoff_sample(*, limit: int = 10, handoff_path: Path | None = None) -> dict[str, Any]:
    """Inspect real mirror handoff rows without claiming, routing or writing."""
    payload = _read_json(handoff_path or default_paths()["handoff"])
    items = [x for x in (payload.get("items") or {}).values() if isinstance(x, Mapping)]
    sample = items[: max(1, min(int(limit), 50))]
    return {
        "schema": payload.get("schema"),
        "sample_count": len(sample),
        "handoff_item_count": len(items),
        "reader_paths_exist": sum(1 for x in sample if Path(_text(x.get("reader_result_path"))).is_file()),
        "source_version_ids": [_text(x.get("source_version_id")) for x in sample],
        "change_types": sorted({_text(x.get("change_type")) for x in sample}),
        "frozen_710_untouched": payload.get("frozen_710_untouched") is True,
        "formal_state_bypass": 0,
        "mutated": False,
    }


def list_incremental_terra_review(*, limit: int = 5, state_path: Path | None = None) -> dict[str, Any]:
    return IncrementalPipeline.read_terra_items(state_path=state_path, limit=limit)


def claim_incremental_terra_review(*, source_version_ids: list[str], worker_id: str, lease_seconds: int = DEFAULT_TERRA_LEASE_SECONDS, state_path: Path | None = None, resume_existing: bool = False) -> dict[str, Any]:
    return IncrementalPipeline(state_path=state_path).claim_terra(source_version_ids=source_version_ids, worker_id=worker_id, lease_seconds=lease_seconds, resume_existing=resume_existing)


def submit_incremental_terra_review(*, source_version_id: str, worker_id: str | None, outcome: str, reason_codes: list[str] | None = None, review: Mapping[str, Any] | None = None, state_path: Path | None = None, claim_token: str | None = None) -> dict[str, Any]:
    return IncrementalPipeline(state_path=state_path).submit_terra(source_version_id=source_version_id, worker_id=worker_id, outcome=outcome, reason_codes=reason_codes, review=review, claim_token=claim_token)


def submit_incremental_user_decision(*, source_version_id: str, decision: str, decided_by: str | None = None, note: str | None = None, approval_ref: str | None = None, review_decision_id: str | None = None, user_receipt_id: str | None = None, approval_refs: list[str] | Mapping[str, Any] | None = None, state_path: Path | None = None) -> dict[str, Any]:
    return IncrementalPipeline(state_path=state_path).submit_user_decision(source_version_id=source_version_id, decision=decision, decided_by=decided_by, note=note, approval_ref=approval_ref, review_decision_id=review_decision_id, user_receipt_id=user_receipt_id, approval_refs=approval_refs)

def list_incremental_opus_clusters(*, limit: int = 5, state_path: Path | None = None) -> dict[str, Any]:
    return IncrementalPipeline(state_path=state_path).list_opus_clusters(limit=limit)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PAW POST_BOOTSTRAP_INCREMENTAL_PIPELINE")
    parser.add_argument("command", choices=["once", "status", "terra-list", "opus-list", "enable-check"])
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--enabled", action="store_true", help="test/canary override; production uses enable-check")
    parser.add_argument("--state-path", type=Path)
    args = parser.parse_args(argv)
    pipeline = IncrementalPipeline(state_path=args.state_path)
    if args.command == "once":
        enabled, reason = is_incremental_enabled()
        result = pipeline.run_once(enabled=(args.enabled or enabled), limit=args.limit)
        if not (args.enabled or enabled):
            result["enable_condition"] = reason
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "status":
        print(json.dumps(pipeline.status(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "terra-list":
        print(json.dumps(pipeline.list_terra(limit=args.limit), ensure_ascii=False, indent=2))
        return 0
    if args.command == "opus-list":
        print(json.dumps(pipeline.list_opus_clusters(limit=args.limit), ensure_ascii=False, indent=2))
        return 0
    enabled, reason = is_incremental_enabled()
    print(json.dumps({"enabled": enabled, "reason": reason, "formal_state_bypass": 0}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
