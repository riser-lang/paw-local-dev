#!/usr/bin/python3
"""Deterministic Bootstrap Risk Router for ReviewBundle and Brain findings.

Classifies staged Luna/Brain proposals into forward-only routes without GPT
per-bundle review. Fail closed: uncertain cases escalate to Opus, user, or GPT.
"""
from __future__ import annotations

import re
import sys
from typing import Any, Mapping

ROOT = __import__("pathlib").Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gpt_brain_common import hash_payload, text, unique, utc_now

ROUTE_SAFE_AUTO_APPLY = "SAFE_AUTO_APPLY"
ROUTE_TERRA_REVIEW = "TERRA_REVIEW_REQUIRED"
ROUTE_OPUS_REVIEW = "OPUS_REVIEW_REQUIRED"
ROUTE_USER_DECISION = "USER_DECISION_REQUIRED"
ROUTE_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
ROUTE_ALREADY_FINALIZED = "ALREADY_FINALIZED"
ROUTE_NO_ACTION = "NO_ACTION"
ROUTE_GPT_REVIEW = "GPT_REVIEW_REQUIRED"

RISK_LOW = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH = "HIGH"
RISK_BLOCKED = "BLOCKED"

RISK_ROUTER_MODEL = "deterministic-risk-router-v1"
RISK_ROUTER_WORKER = "chatgpt-risk-router"
RISK_ROUTER_APPROVAL = "risk_router_safe_auto_apply_v1"

INCREMENTAL_ROUTES = frozenset({
    ROUTE_SAFE_AUTO_APPLY,
    ROUTE_TERRA_REVIEW,
    ROUTE_OPUS_REVIEW,
    ROUTE_USER_DECISION,
    ROUTE_INSUFFICIENT_EVIDENCE,
    ROUTE_NO_ACTION,
})

TERMINAL_BUNDLE_STATES = frozenset({
    "FINALIZED", "WRITTEN", "BLOCKED", "CLEAR", "POST_OPUS_CLEAR",
    "POLICY_WRITE_READY", "NO_ACTION", "INSUFFICIENT_EVIDENCE",
})

HIGH_RISK_KNOWLEDGE_ACTIONS = frozenset({"CONFLICT", "MERGE", "RETIRE"})
OPUS_FINDING_TYPES = frozenset({
    "CONFLICT", "CONTENT_CONFLICT", "CROSS_SHEET_CONFLICT", "RULE_CONFLICT",
    "POLICY_CONFLICT", "COVERAGE_CONFLICT", "DATA_QUALITY_CONFLICT",
    "PARSER_CONFLICT", "PIPELINE_CONFLICT", "STRUCTURE_CONFLICT",
    "SEMANTIC_CONFLICT", "TEMPLATE_CONFLICT", "DOCUMENT_CONFLICT",
    "NAMING_CONFLICT", "TERMINOLOGY_CONFLICT", "TRAINING_CONFLICT",
    "OPERATIONAL_CONFLICT", "RECORD_REMARK_CONFLICT", "DATA_RENDERING_CONFLICT",
    "REVIEW_REQUIRED", "DATA_EXTRACTION_REVIEW_REQUIRED", "METRIC_REVIEW_REQUIRED",
    "CONSOLIDATION_BLOCKER", "CONSOLIDATION_REVIEW",
})
USER_DECISION_FINDING_TYPES = frozenset({
    "DECISION", "PROJECT_DECISION", "DECISION_OR_PLAN", "DECISION_RULE",
    "DECISION_PLAN", "IDENTITY_AND_ROLE_AMBIGUITY", "ROLE_AMBIGUITY",
    "PROJECT_PROPOSAL", "NEW_PROJECT", "KPI_SET",
})
INSUFFICIENT_FINDING_TYPES = frozenset({
    "EVIDENCE_LIMITATION", "EVIDENCE_LIMIT", "EVIDENCE_GAP", "DATA_GAP",
    "INCOMPLETE_EVIDENCE", "INSUFFICIENT_EVIDENCE", "NO_SUBSTANTIVE_DATA",
    "EVIDENCE_ACCESS_RISK",
})
PROJECT_CREATE_MARKERS = frozenset({
    "CREATE", "NEW", "PROPOSE", "INITIATE", "REGISTER", "DEFINE",
})
PROJECT_CONTEXT_OPS = frozenset({
    "ADD_PROJECT_CONTEXT", "ADD_CONTEXT", "ADD_SOURCE_CONTEXT", "APPEND_CONTEXT",
    "CONSOLIDATE", "CONSOLIDATE_CONTEXT", "CONSOLIDATE_SOURCE", "CONSOLIDATE_SOURCE_CONTEXT",
    "CONSOLIDATE_PROJECT_CONTEXT", "CONSOLIDATE_DOCUMENT_CONTEXT", "CONSOLIDATE_SOURCE_VERSION",
    "ATTACH_SOURCE_CONTEXT", "ATTACH_SOURCE_VERSION", "ATTACH_DOCUMENT_CONTEXT",
    "ATTACH", "LINK_SOURCE_CONTEXT", "LINK_DOCUMENT_CONTEXT", "LINK_EXISTING_PROJECT",
    "ASSOCIATE_SOURCE", "ASSOCIATE_SOURCE_VERSION", "ADD_SOURCE", "ADD_SOURCE_VERSION",
    "ADD_SOURCE_TO_PROJECT", "ADD_DOCUMENT", "ADD_DOCUMENT_CONTEXT", "RECORD_SOURCE_CONTEXT",
    "RECORD_DOCUMENT_CONTEXT", "RECORD_VERSION", "RECORD_SOURCE_VERSION", "RECORD_SOURCE_IDENTITY",
    "UPSERT", "UPSERT_PROJECT_CONTEXT", "UPSERT_SOURCE_VERSION", "UPDATE_PROJECT_CONTEXT",
    "UPDATE_TIMELINE", "PROJECT_CONTEXT_ONLY", "NO_SUPERSESSION", "LINK_SOURCE_TO_PROJECT",
    "ADD_SOURCE_EVIDENCE", "ADD_DOCUMENT_EVIDENCE", "ADD_DATASET_CONTEXT",
})
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _is_new_project_operation(operation: str) -> bool:
    """Recognize project creation, not harmless source/document registration."""
    op = text(operation).upper()
    if not op:
        return False
    if op in {"CREATE", "NEW", "PROPOSE", "INITIATE", "DEFINE"}:
        return True
    if op.endswith("_PROJECT"):
        return op.split("_", 1)[0] in {"CREATE", "NEW", "PROPOSE", "INITIATE", "DEFINE", "REGISTER"}
    return False


def _uuid_like(value: str) -> bool:
    raw = text(value)
    return bool(raw) and len(raw) >= 32 and "-" in raw


def _project_ids_from_bundle(bundle: Mapping[str, Any]) -> list[str]:
    ids: list[str] = []
    for item in bundle.get("project_proposals") or []:
        if isinstance(item, Mapping):
            pid = text(item.get("project_id"))
            if _uuid_like(pid):
                ids.append(pid)
    for item in bundle.get("knowledge_proposals") or []:
        if isinstance(item, Mapping):
            pod = text(item.get("project_or_domain"))
            if _uuid_like(pod):
                ids.append(pod)
    return unique(ids)


def _canonical_topics(bundle: Mapping[str, Any]) -> list[str]:
    return unique(
        text(x.get("canonical_topic"))
        for x in (bundle.get("knowledge_proposals") or [])
        if isinstance(x, Mapping) and text(x.get("canonical_topic"))
    )


def _knowledge_actions(bundle: Mapping[str, Any]) -> list[str]:
    return [
        text(x.get("action")).upper()
        for x in (bundle.get("knowledge_proposals") or [])
        if isinstance(x, Mapping) and text(x.get("action"))
    ]


def _finding_types(bundle: Mapping[str, Any]) -> list[str]:
    return [
        text(x.get("finding_type")).upper()
        for x in (bundle.get("findings") or [])
        if isinstance(x, Mapping) and text(x.get("finding_type"))
    ]


def _has_evidence(bundle: Mapping[str, Any]) -> bool:
    refs = bundle.get("evidence_refs")
    if isinstance(refs, list) and refs:
        return True
    for key in ("knowledge_proposals", "project_proposals", "metric_proposals"):
        for item in bundle.get(key) or []:
            if isinstance(item, Mapping) and item.get("evidence_ids"):
                return True
    return False


def _topic_index(brain: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for item in (brain.get("knowledge_topics") or {}).values():
        if not isinstance(item, Mapping):
            continue
        topic = text(item.get("canonical_topic"))
        if topic:
            index.setdefault(topic, []).append(dict(item))
    return index


def _topic_version_conflict(
    bundle: Mapping[str, Any],
    brain: Mapping[str, Any],
) -> bool:
    index = _topic_index(brain)
    for proposal in bundle.get("knowledge_proposals") or []:
        if not isinstance(proposal, Mapping):
            continue
        topic = text(proposal.get("canonical_topic"))
        action = text(proposal.get("action")).upper()
        if action not in {"CREATE", "REVISE"} or not topic:
            continue
        fp = text(proposal.get("fingerprint"))
        existing = index.get(topic) or []
        for prior in existing:
            prior_fp = text(prior.get("topic_fingerprint"))
            prior_sids = set(prior.get("source_version_ids") or [])
            sid = text(bundle.get("source_version_id"))
            if prior_fp and fp and prior_fp != fp and sid not in prior_sids:
                if action == "CREATE" or prior.get("current_effective"):
                    return True
            if prior.get("supersedes") or prior.get("superseded_by"):
                if action in {"CREATE", "REVISE"} and sid not in prior_sids:
                    return True
    return False


def _metric_identity_conflict(bundle: Mapping[str, Any], brain: Mapping[str, Any]) -> bool:
    sid = text(bundle.get("source_version_id"))
    by_name: dict[str, set[str]] = {}
    for item in (brain.get("metric_proposals") or {}).values():
        if not isinstance(item, Mapping) or sid not in (item.get("source_version_ids") or []):
            continue
        name = text(item.get("metric"))
        unit = text(item.get("unit"))
        if not name:
            continue
        by_name.setdefault(name, set()).add(unit)
    return any(len(units) > 1 for units in by_name.values())


def _slug_from_topic(topic: str, fingerprint: str = "") -> str:
    base = _SLUG_RE.sub("-", text(topic).lower()).strip("-")
    if not base:
        base = "topic"
    if fingerprint:
        return f"{base[:48]}-{fingerprint[:8]}".strip("-")
    return base[:64]


def build_downstream_action(route: str, write_plan: list[Mapping[str, Any]] | None = None) -> str:
    if route == ROUTE_SAFE_AUTO_APPLY:
        if write_plan:
            return "DETERMINISTIC_GOVERNED_WRITE"
        return "FINALIZE_NO_ACTION"
    if route == ROUTE_NO_ACTION:
        return "FINALIZE_NO_ACTION"
    if route == ROUTE_OPUS_REVIEW:
        return "OPUS_CLUSTER"
    if route == ROUTE_USER_DECISION:
        return "USER_DECISION_CLUSTER"
    if route == ROUTE_INSUFFICIENT_EVIDENCE:
        return "HOLD_INSUFFICIENT_EVIDENCE"
    if route == ROUTE_ALREADY_FINALIZED:
        return "SKIP"
    return "GPT_AUTHORITATIVE_REVIEW"


def build_write_plan(
    bundle: Mapping[str, Any],
    brain: Mapping[str, Any],
    *,
    project_catalog: Any | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Build deterministic write_changes from Brain staged records. No invention."""
    sid = text(bundle.get("source_version_id"))
    source_ref = f"source_version:{sid}"
    refs = list(bundle.get("evidence_refs") or [])
    if not refs:
        refs = _evidence_refs_from_brain(brain, bundle)
    if not refs:
        return [], "missing_evidence_refs"

    writes: list[dict[str, Any]] = []
    coverage = text(bundle.get("coverage_state")).upper()

    if coverage == "PROJECT_CONTEXT_ONLY" and not bundle.get("knowledge_proposals"):
        for proposal in bundle.get("project_proposals") or []:
            if not isinstance(proposal, Mapping):
                continue
            pid = text(proposal.get("project_id"))
            if not _uuid_like(pid):
                return [], "project_id_unresolved"
            full = _brain_project(brain, proposal.get("id"))
            # A project name is identity, not evidence-backed progress.  Do
            # not turn a missing context into the historical title-only fake
            # update; the outcome layer will create an evidence-insufficient
            # baseline instead.
            summary = text((full or {}).get("context"))
            if not summary:
                return [], "project_context_missing"
            writes.append({
                "target_type": "project",
                "operation": "update",
                "payload": {"id": pid, "latest_progress_summary": summary[:500]},
                "source_refs": [source_ref],
                "evidence_refs": refs,
                "proposal_id": proposal.get("id"),
            })
        return writes, None

    actions = _knowledge_actions(bundle)
    if actions and all(a == "UNCHANGED" for a in actions):
        return [], None

    for proposal in bundle.get("knowledge_proposals") or []:
        if not isinstance(proposal, Mapping):
            continue
        action = text(proposal.get("action")).upper()
        topic = text(proposal.get("canonical_topic"))
        if not topic:
            return [], "missing_canonical_topic"
        full = _brain_knowledge(brain, proposal.get("id"))
        rationale = text((full or {}).get("rationale"))
        pod = text(proposal.get("project_or_domain"))
        proposal_refs = _refs_for_ids(brain, list(proposal.get("evidence_ids") or []), sid) or refs

        if action == "UNCHANGED":
            continue
        if action in HIGH_RISK_KNOWLEDGE_ACTIONS:
            return [], f"forbidden_action:{action}"
        if action == "INSUFFICIENT_EVIDENCE":
            return [], "knowledge_insufficient_evidence"

        if action == "CREATE":
            slug = _slug_from_topic(topic, text(proposal.get("fingerprint")))
            payload = {
                "title": topic[:200],
                "slug": slug,
                "one_line_summary": (rationale or topic)[:500],
                "content": {
                    "canonical_topic": topic,
                    "evidence_refs": proposal_refs,
                    "source_version_ids": unique(x.get("source_version_id") for x in proposal_refs if isinstance(x, Mapping)),
                    "effective_from": full.get("effective_from") if isinstance(full, Mapping) else None,
                    "effective_to": full.get("effective_to") if isinstance(full, Mapping) else None,
                    "current_effective": True,
                },
            }
            writes.append({
                "target_type": "knowledge",
                "operation": "create",
                "payload": payload,
                "source_refs": [source_ref],
                "evidence_refs": proposal_refs,
                "proposal_id": proposal.get("id"),
                "proposal_action": action,
            })
        elif action == "REVISE":
            knowledge_ref = text((full or {}).get("current_knowledge_ref"))
            if not knowledge_ref:
                return [], "revise_without_current_knowledge_ref"
            payload = {"id": knowledge_ref}
            if rationale:
                payload["one_line_summary"] = rationale[:500]
            writes.append({
                "target_type": "knowledge",
                "operation": "update",
                "payload": payload,
                "source_refs": [source_ref],
                "evidence_refs": proposal_refs,
                "proposal_id": proposal.get("id"),
                "proposal_action": action,
            })
        else:
            return [], f"unsupported_knowledge_action:{action}"

    if coverage == "DATA_EXTRACTED" and not bundle.get("knowledge_proposals"):
        for proposal in bundle.get("metric_proposals") or []:
            if not isinstance(proposal, Mapping):
                continue
            metric = text(proposal.get("metric"))
            if not metric:
                continue
            full = _brain_metric(brain, proposal.get("id"))
            value = (full or {}).get("value")
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return [], "metric_value_not_numeric"
            writes.append({
                "target_type": "data",
                "operation": "create",
                "payload": {
                    "name": metric,
                    "value": value,
                    "unit": text(proposal.get("unit")) or text((full or {}).get("unit")),
                },
                "source_refs": [source_ref],
                "evidence_refs": _refs_for_ids(brain, list(proposal.get("evidence_ids") or []), sid) or refs,
                "proposal_id": proposal.get("id"),
            })
    return writes, None


def _brain_knowledge(brain: Mapping[str, Any], proposal_id: Any) -> dict[str, Any] | None:
    item = (brain.get("knowledge_topics") or {}).get(text(proposal_id))
    return dict(item) if isinstance(item, Mapping) else None


def _brain_project(brain: Mapping[str, Any], proposal_id: Any) -> dict[str, Any] | None:
    item = (brain.get("project_consolidations") or {}).get(text(proposal_id))
    return dict(item) if isinstance(item, Mapping) else None


def _brain_metric(brain: Mapping[str, Any], proposal_id: Any) -> dict[str, Any] | None:
    item = (brain.get("metric_proposals") or {}).get(text(proposal_id))
    return dict(item) if isinstance(item, Mapping) else None


def _refs_for_ids(brain: Mapping[str, Any], evidence_ids: list[str], sid: str) -> list[dict[str, Any]]:
    nodes = brain.get("evidence_nodes") or {}
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for eid in evidence_ids:
        node = nodes.get(eid) if isinstance(nodes, Mapping) else None
        if not isinstance(node, Mapping):
            continue
        locator = text(node.get("evidence_locator"))
        sv = text(node.get("source_version_id")) or sid
        key = f"{sv}|{locator}"
        if not locator or key in seen:
            continue
        seen.add(key)
        refs.append({"source_version_id": sv, "evidence_locator": locator, "evidence_id": eid})
    return refs


def _evidence_refs_from_brain(brain: Mapping[str, Any], bundle: Mapping[str, Any]) -> list[dict[str, Any]]:
    ids: list[str] = []
    for key in ("knowledge_proposals", "project_proposals", "metric_proposals"):
        for item in bundle.get(key) or []:
            if isinstance(item, Mapping):
                ids.extend(list(item.get("evidence_ids") or []))
    return _refs_for_ids(brain, unique(ids), text(bundle.get("source_version_id")))


def classify_bundle(
    bundle: Mapping[str, Any],
    brain: Mapping[str, Any],
    *,
    source_state: Mapping[str, Any] | None = None,
    project_catalog: Any | None = None,
) -> dict[str, Any]:
    """Return machine-readable route decision for one ReviewBundle."""
    state = text(bundle.get("state")) or "REVIEW_PENDING"
    sid = text(bundle.get("source_version_id"))
    coverage = text(bundle.get("coverage_state") or (source_state or {}).get("state")).upper()
    reason_codes: list[str] = []
    conflict_flags: list[str] = []

    if state in TERMINAL_BUNDLE_STATES and state != "REVIEW_PENDING":
        return _result(
            route=ROUTE_ALREADY_FINALIZED,
            risk_level=RISK_LOW,
            reason_codes=["bundle_already_terminal"],
            bundle=bundle,
            brain=brain,
            coverage=coverage,
        )

    if coverage == "VISUAL_PENDING":
        return _result(
            route=ROUTE_INSUFFICIENT_EVIDENCE,
            risk_level=RISK_BLOCKED,
            reason_codes=["visual_pending"],
            bundle=bundle,
            brain=brain,
            coverage=coverage,
        )

    if coverage == "INSUFFICIENT_EVIDENCE":
        return _result(
            route=ROUTE_INSUFFICIENT_EVIDENCE,
            risk_level=RISK_BLOCKED,
            reason_codes=["coverage_insufficient_evidence"],
            bundle=bundle,
            brain=brain,
            coverage=coverage,
        )

    finding_types = _finding_types(bundle)
    knowledge_actions = _knowledge_actions(bundle)

    for ft in finding_types:
        if ft in USER_DECISION_FINDING_TYPES:
            reason_codes.append(f"finding_user_decision:{ft}")
        elif ft in OPUS_FINDING_TYPES:
            reason_codes.append(f"finding_opus:{ft}")
        elif ft in INSUFFICIENT_FINDING_TYPES:
            reason_codes.append(f"finding_insufficient:{ft}")

    for action in knowledge_actions:
        if action in HIGH_RISK_KNOWLEDGE_ACTIONS:
            reason_codes.append(f"knowledge_action:{action}")
            conflict_flags.append(action.lower())

    for proposal in bundle.get("project_proposals") or []:
        if not isinstance(proposal, Mapping):
            continue
        if proposal.get("user_decision_required"):
            reason_codes.append("project_user_decision_required")
        op = text(proposal.get("operation")).upper()
        if _is_new_project_operation(op):
            reason_codes.append(f"project_create_operation:{op}")

    project_ids = _project_ids_from_bundle(bundle)
    if len(project_ids) > 1:
        reason_codes.append("multi_project_ambiguity")
        conflict_flags.append("multi_project")

    if _topic_version_conflict(bundle, brain):
        reason_codes.append("canonical_topic_version_conflict")
        conflict_flags.append("version_conflict")

    if _metric_identity_conflict(bundle, brain):
        reason_codes.append("metric_unit_conflict")
        conflict_flags.append("metric_conflict")

    if coverage == "REVIEW_REQUIRED":
        reason_codes.append("coverage_review_required")

    if any(code.startswith("finding_user_decision") or code == "project_user_decision_required" for code in reason_codes):
        return _result(
            route=ROUTE_USER_DECISION,
            risk_level=RISK_HIGH,
            reason_codes=unique(reason_codes),
            conflict_flags=unique(conflict_flags),
            bundle=bundle,
            brain=brain,
            coverage=coverage,
        )

    if any(code.startswith("project_create_operation") for code in reason_codes):
        return _result(
            route=ROUTE_USER_DECISION,
            risk_level=RISK_HIGH,
            reason_codes=unique(reason_codes),
            conflict_flags=unique(conflict_flags),
            bundle=bundle,
            brain=brain,
            coverage=coverage,
        )

    opus_codes = [c for c in reason_codes if c.startswith(("finding_opus", "knowledge_action", "canonical_topic", "metric_", "coverage_review", "multi_project"))]
    if opus_codes:
        return _result(
            route=ROUTE_OPUS_REVIEW,
            risk_level=RISK_HIGH,
            reason_codes=unique(reason_codes),
            conflict_flags=unique(conflict_flags),
            bundle=bundle,
            brain=brain,
            coverage=coverage,
        )

    if any(code.startswith("finding_insufficient") for code in reason_codes):
        return _result(
            route=ROUTE_INSUFFICIENT_EVIDENCE,
            risk_level=RISK_BLOCKED,
            reason_codes=unique(reason_codes),
            conflict_flags=unique(conflict_flags),
            bundle=bundle,
            brain=brain,
            coverage=coverage,
        )

    if not _has_evidence(bundle):
        return _result(
            route=ROUTE_INSUFFICIENT_EVIDENCE,
            risk_level=RISK_BLOCKED,
            reason_codes=["evidence_refs_missing"],
            bundle=bundle,
            brain=brain,
            coverage=coverage,
        )

    write_plan, plan_error = build_write_plan(bundle, brain, project_catalog=project_catalog)
    if plan_error and coverage in {"PROJECT_CONTEXT_ONLY", "DATA_EXTRACTED"}:
        if coverage == "PROJECT_CONTEXT_ONLY":
            return _result(
                route=ROUTE_NO_ACTION,
                risk_level=RISK_LOW,
                reason_codes=["project_context_no_safe_write", plan_error],
                bundle=bundle,
                brain=brain,
                coverage=coverage,
                write_plan=[],
            )
        return _result(
            route=ROUTE_INSUFFICIENT_EVIDENCE,
            risk_level=RISK_BLOCKED,
            reason_codes=[plan_error],
            bundle=bundle,
            brain=brain,
            coverage=coverage,
        )

    safe_categories = {
        "PROJECT_CONTEXT_ONLY",
        "DATA_EXTRACTED",
        "NO_KNOWLEDGE_IMPACT",
    }
    if coverage in safe_categories or (
        coverage == "CONSOLIDATED"
        and knowledge_actions
        and all(a in {"UNCHANGED", "CREATE", "REVISE"} for a in knowledge_actions)
        and not plan_error
    ):
        if write_plan:
            return _result(
                route=ROUTE_SAFE_AUTO_APPLY,
                risk_level=RISK_LOW,
                reason_codes=["deterministic_write_plan_ready"],
                bundle=bundle,
                brain=brain,
                coverage=coverage,
                write_plan=write_plan,
            )
        if coverage in {"PROJECT_CONTEXT_ONLY", "DATA_EXTRACTED"} or not knowledge_actions:
            return _result(
                route=ROUTE_NO_ACTION,
                risk_level=RISK_LOW,
                reason_codes=["no_material_write_required"],
                bundle=bundle,
                brain=brain,
                coverage=coverage,
                write_plan=[],
            )

    if plan_error:
        reason_codes.append(plan_error)

    # Ordinary unresolved medium-risk work belongs to the durable Terra
    # queue.  Keep ROUTE_GPT_REVIEW as a compatibility constant for old
    # callers, but never emit it from the shared production classifier.
    return _result(
        route=ROUTE_TERRA_REVIEW,
        risk_level=RISK_MEDIUM,
        reason_codes=unique(reason_codes or ["requires_terra_review"]),
        conflict_flags=unique(conflict_flags),
        bundle=bundle,
        brain=brain,
        coverage=coverage,
        write_plan=write_plan if write_plan else [],
    )


def classify_finding(finding: Mapping[str, Any], brain: Mapping[str, Any]) -> dict[str, Any]:
    """Route one Brain finding that is not yet finalized."""
    state = text(finding.get("state"))
    if state in {"FINALIZED", "WRITE_READY", "WRITTEN"}:
        return {
            "route": ROUTE_ALREADY_FINALIZED,
            "risk_level": RISK_LOW,
            "reason_codes": ["finding_already_terminal"],
            "finding_id": finding.get("id"),
            "source_version_ids": list(finding.get("source_version_ids") or []),
            "downstream_action": "SKIP",
            "classified_at": utc_now(),
        }
    ftype = text(finding.get("finding_type")).upper()
    reason_codes = [f"finding_type:{ftype}"]
    # Generic unresolved findings are a durable Terra review workload.  The
    # compatibility GPT constant remains for legacy manifests only.
    route = ROUTE_TERRA_REVIEW
    risk = RISK_MEDIUM
    if ftype in INSUFFICIENT_FINDING_TYPES:
        route = ROUTE_INSUFFICIENT_EVIDENCE
        risk = RISK_BLOCKED
    elif ftype in USER_DECISION_FINDING_TYPES:
        route = ROUTE_USER_DECISION
        risk = RISK_HIGH
    elif ftype in OPUS_FINDING_TYPES:
        route = ROUTE_OPUS_REVIEW
        risk = RISK_HIGH
    elif ftype in {"FACT", "CONTENT", "DOCUMENT_STRUCTURE", "PROJECT_CONTEXT", "DATA_QUALITY"}:
        route = ROUTE_NO_ACTION
        risk = RISK_LOW
        reason_codes.append("informational_finding_finalize")
    return {
        "route": route,
        "risk_level": risk,
        "reason_codes": reason_codes,
        "finding_id": finding.get("id"),
        "project_or_domain": text(finding.get("project_or_domain")),
        "canonical_topic": text(finding.get("proposed_target")),
        "source_version_ids": list(finding.get("source_version_ids") or []),
        "downstream_action": build_downstream_action(route),
        "classified_at": utc_now(),
    }


def _incremental_bool(preanalysis: Mapping[str, Any], source_item: Mapping[str, Any], *names: str) -> bool:
    """Read explicit safety facts from the post-Reader contract only."""
    hints = preanalysis.get("risk_hints") if isinstance(preanalysis.get("risk_hints"), Mapping) else {}
    for container in (preanalysis, hints, source_item):
        for name in names:
            if container.get(name) is True:
                return True
    return False


def _incremental_project_ids(preanalysis: Mapping[str, Any], source_item: Mapping[str, Any]) -> list[str]:
    ids: list[str] = []
    for container in (preanalysis, source_item):
        for key in ("project_id", "existing_project_id"):
            value = text(container.get(key))
            if value:
                ids.append(value)
        for key in ("project_ids", "existing_project_ids"):
            values = container.get(key)
            if isinstance(values, (list, tuple)):
                ids.extend(text(x) for x in values if text(x))
    for proposal in preanalysis.get("project_consolidations") or preanalysis.get("project_proposals") or []:
        if isinstance(proposal, Mapping) and text(proposal.get("project_id")):
            ids.append(text(proposal.get("project_id")))
    return unique(ids)


def _incremental_plan(preanalysis: Mapping[str, Any], source_id: str, evidence: list[Any], project_id: str) -> tuple[list[dict[str, Any]], str | None]:
    raw_plan = preanalysis.get("write_plan")
    if not isinstance(raw_plan, list):
        return [], None
    allowed = {("project", "update"), ("data", "create"), ("data", "revise"),
               ("knowledge", "update"), ("knowledge_project_link", "link")}
    plan: list[dict[str, Any]] = []
    for raw in raw_plan:
        if not isinstance(raw, Mapping):
            return [], "write_plan_item_invalid"
        item = dict(raw)
        target = text(item.get("target_type")).lower()
        operation = text(item.get("operation")).lower()
        if (target, operation) not in allowed:
            return [], f"incremental_operation_not_allowlisted:{target}.{operation}"
        refs = item.get("evidence_refs") or evidence
        if not isinstance(refs, list) or not refs:
            return [], "write_plan_evidence_refs_required"
        for ref in refs:
            if not isinstance(ref, Mapping) or text(ref.get("source_version_id")) != source_id or not text(ref.get("evidence_locator")):
                return [], "write_plan_evidence_identity_invalid"
        payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
        if target == "project" and (not project_id or text(payload.get("id")) != project_id):
            return [], "existing_project_identity_required"
        if target == "knowledge" and not text(payload.get("id")):
            return [], "existing_knowledge_identity_required"
        item["evidence_refs"] = [dict(x) for x in refs]
        item.setdefault("source_refs", [f"source_version:{source_id}"])
        plan.append(item)
    return plan, None


def classify_incremental(
    preanalysis: Mapping[str, Any],
    source_item: Mapping[str, Any],
) -> dict[str, Any]:
    """Classify one versioned post-Reader handoff using the bootstrap rules.

    This is the only incremental risk entrypoint.  It deliberately delegates
    ordinary proposal/finding/conflict handling to ``classify_bundle`` and
    only adds the incremental envelope facts (existing project identity,
    immutable evidence and explicit material-decision flags).
    """
    source_id = text(preanalysis.get("source_version_id") or source_item.get("source_version_id"))
    evidence = preanalysis.get("evidence_refs") if isinstance(preanalysis.get("evidence_refs"), list) else []
    project_ids = _incremental_project_ids(preanalysis, source_item)
    project_id = project_ids[0] if len(project_ids) == 1 else ""
    topic = text(preanalysis.get("canonical_topic"))
    coverage = text(preanalysis.get("coverage_state") or preanalysis.get("source_state", {}).get("state")).upper()
    reasons: list[str] = []
    conflict_flags: list[str] = []

    material = _incremental_bool(preanalysis, source_item, "material_business_decision", "kpi_change", "performance_change", "policy_change", "personnel_or_org_choice", "changes_effective_rule", "creates_project")
    unresolved = _incremental_bool(preanalysis, source_item, "unresolved_conflict", "conflict", "evidence_cannot_uniquely_decide")
    if len(project_ids) > 1:
        material = True
        reasons.append("multiple_existing_project_candidates")
    if _incremental_bool(preanalysis, source_item, "creates_project"):
        reasons.append("new_project_forbidden_without_user_decision")
    if _incremental_bool(preanalysis, source_item, "changes_effective_rule", "policy_change", "kpi_change", "performance_change", "personnel_or_org_choice"):
        reasons.append("material_business_choice_requires_user")
    if _incremental_bool(preanalysis, source_item, "evidence_cannot_uniquely_decide"):
        reasons.append("evidence_cannot_uniquely_decide_current_rule")

    finding_types = _finding_types(preanalysis)
    if any(ft in USER_DECISION_FINDING_TYPES for ft in finding_types):
        material = True
        reasons.extend(f"finding_user_decision:{ft}" for ft in finding_types if ft in USER_DECISION_FINDING_TYPES)
    if any(ft in INSUFFICIENT_FINDING_TYPES for ft in finding_types):
        reasons.extend(f"finding_insufficient:{ft}" for ft in finding_types if ft in INSUFFICIENT_FINDING_TYPES)
    if any(ft in OPUS_FINDING_TYPES for ft in finding_types):
        unresolved = True
        reasons.extend(f"finding_opus:{ft}" for ft in finding_types if ft in OPUS_FINDING_TYPES)

    plan, plan_error = _incremental_plan(preanalysis, source_id, evidence, project_id)
    if not evidence:
        reasons.append("evidence_refs_missing")
    if plan_error:
        reasons.append(plan_error)
    if unresolved:
        conflict_flags.append("incremental_conflict")
        reasons.append("unresolved_conflict_requires_expert_review")

    if material:
        route = ROUTE_USER_DECISION
        risk = RISK_HIGH
    elif not evidence or any(ft in INSUFFICIENT_FINDING_TYPES for ft in finding_types) or coverage in {"VISUAL_PENDING", "INSUFFICIENT_EVIDENCE"}:
        route = ROUTE_INSUFFICIENT_EVIDENCE
        risk = RISK_BLOCKED
    elif unresolved:
        route = ROUTE_USER_DECISION if _incremental_bool(preanalysis, source_item, "evidence_cannot_uniquely_decide", "changes_effective_rule") else ROUTE_OPUS_REVIEW
        risk = RISK_HIGH
    elif plan_error:
        route = ROUTE_TERRA_REVIEW
        risk = RISK_MEDIUM
    elif plan:
        route = ROUTE_SAFE_AUTO_APPLY
        risk = RISK_LOW
        reasons.append("existing_project_evidence_append_plan_ready")
    elif _incremental_bool(preanalysis, source_item, "no_action", "unchanged", "informational_only") or not any(preanalysis.get(k) for k in ("knowledge_proposals", "project_consolidations", "project_proposals", "metric_proposals", "findings", "write_plan")):
        route = ROUTE_NO_ACTION
        risk = RISK_LOW
        reasons.append("no_material_write_required")
    else:
        hints = preanalysis.get("risk_hints") if isinstance(preanalysis.get("risk_hints"), Mapping) else {}
        risk_hint = text(hints.get("risk_level") or preanalysis.get("risk_level")).upper()
        route = ROUTE_OPUS_REVIEW if risk_hint == RISK_HIGH else ROUTE_TERRA_REVIEW
        risk = RISK_HIGH if route == ROUTE_OPUS_REVIEW else RISK_MEDIUM
        reasons.append("non_conflicting_incremental_review_required")

    result = {
        "route": route,
        "risk_level": risk,
        "reason_codes": unique(reasons),
        "project_id": project_id or None,
        "canonical_topic": topic or None,
        "version_relation": text(preanalysis.get("version_relation")) or "incremental_version",
        "conflict_flags": unique(conflict_flags),
        "evidence_count": len(evidence),
        "source_version_ids": [source_id],
        "bundle_id": "incremental:" + source_id,
        "coverage_state": coverage,
        "write_plan": plan,
        "downstream_action": build_downstream_action(route, plan),
        "classified_at": utc_now(),
    }
    result["cluster_key"] = risk_cluster_key(result)
    if route not in INCREMENTAL_ROUTES:
        raise ValueError(f"unsupported incremental route: {route}")
    return result


def risk_cluster_key(decision: Mapping[str, Any]) -> str:
    """Deterministic Opus cluster key from route decision."""
    return hash_payload({
        "project_id": decision.get("project_id"),
        "canonical_topic": decision.get("canonical_topic"),
        "conflict_flags": list(decision.get("conflict_flags") or []),
        "version_relation": decision.get("version_relation"),
        "route": decision.get("route"),
    })


def _primary_project(bundle: Mapping[str, Any]) -> str | None:
    ids = _project_ids_from_bundle(bundle)
    return ids[0] if ids else None


def _primary_topic(bundle: Mapping[str, Any]) -> str | None:
    topics = _canonical_topics(bundle)
    return topics[0] if topics else None


def _version_relation(bundle: Mapping[str, Any], brain: Mapping[str, Any]) -> str:
    actions = _knowledge_actions(bundle)
    if "REVISE" in actions:
        return "revise_existing"
    if "CREATE" in actions:
        topic = _primary_topic(bundle)
        if topic and _topic_index(brain).get(topic):
            return "possible_supersede"
        return "new_topic"
    return "none"


def _result(
    *,
    route: str,
    risk_level: str,
    reason_codes: list[str],
    bundle: Mapping[str, Any],
    brain: Mapping[str, Any],
    coverage: str,
    conflict_flags: list[str] | None = None,
    write_plan: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    plan = list(write_plan or [])
    return {
        "route": route,
        "risk_level": risk_level,
        "reason_codes": list(reason_codes or []),
        "project_id": _primary_project(bundle),
        "canonical_topic": _primary_topic(bundle),
        "version_relation": _version_relation(bundle, brain),
        "conflict_flags": list(conflict_flags or []),
        "evidence_count": len(bundle.get("evidence_refs") or []),
        "source_version_ids": [text(bundle.get("source_version_id"))],
        "bundle_id": bundle.get("id"),
        "coverage_state": coverage,
        "write_plan": plan,
        "downstream_action": build_downstream_action(route, plan),
        "classified_at": utc_now(),
    }


def dry_run_queue(
    *,
    bundles: Mapping[str, Any],
    brain: Mapping[str, Any],
    include_findings: bool = True,
) -> dict[str, Any]:
    """Read-only routing pass over pending bundles and unresolved findings."""
    route_counts: dict[str, int] = {}
    project_counts: dict[str, int] = {}
    manifest: list[dict[str, Any]] = []

    for bundle in bundles.values():
        if not isinstance(bundle, Mapping):
            continue
        if text(bundle.get("state")) != "REVIEW_PENDING":
            continue
        decision = classify_bundle(bundle, brain)
        route = decision["route"]
        route_counts[route] = route_counts.get(route, 0) + 1
        pid = text(decision.get("project_id")) or "unassigned"
        project_counts[pid] = project_counts.get(pid, 0) + 1
        manifest.append(decision)

    finding_counts: dict[str, int] = {}
    finding_manifest: list[dict[str, Any]] = []
    if include_findings:
        for finding in (brain.get("findings") or {}).values():
            if not isinstance(finding, Mapping):
                continue
            if finding.get("state") in {"FINALIZED", "WRITE_READY", "WRITTEN"}:
                continue
            fdec = classify_finding(finding, brain)
            route = fdec["route"]
            finding_counts[route] = finding_counts.get(route, 0) + 1
            finding_manifest.append(fdec)

    return {
        "bundle_route_distribution": route_counts,
        "finding_route_distribution": finding_counts,
        "project_route_distribution": project_counts,
        "bundle_manifest": manifest,
        "finding_manifest": finding_manifest,
        "pending_bundles": len(manifest),
        "unresolved_findings": len(finding_manifest),
        "classified_at": utc_now(),
        "formal_state_bypass": 0,
    }
