"""Luna Draft Projection contract — analysis plane only, never Canonical."""
from __future__ import annotations

from typing import Any, Mapping

AUTHORITY_DRAFT = "draft"

LUNA_UNREVIEWED = "LUNA_UNREVIEWED"
GPT_ACCEPTED = "GPT_ACCEPTED"
GPT_REVISED = "GPT_REVISED"
GPT_REJECTED = "GPT_REJECTED"
OPUS_PENDING = "OPUS_PENDING"
USER_DECISION_REQUIRED = "USER_DECISION_REQUIRED"
CANONICALIZED = "CANONICALIZED"
NO_SEMANTIC = "NO_SEMANTIC"
PROJECTION_ONLY = "PROJECTION_ONLY"

DRAFT_STATUSES = frozenset({
    LUNA_UNREVIEWED,
    GPT_ACCEPTED,
    GPT_REVISED,
    GPT_REJECTED,
    OPUS_PENDING,
    USER_DECISION_REQUIRED,
    CANONICALIZED,
    NO_SEMANTIC,
    PROJECTION_ONLY,
})

SCHEMA = "paw-luna-draft-projection/1"
DRAFT_ID_PREFIX = "draft:"


def stable_draft_id(source_version_id: str) -> str:
    import hashlib
    digest = hashlib.sha256(f"luna-draft-v1:{source_version_id}".encode("utf-8")).hexdigest()[:24]
    return f"{DRAFT_ID_PREFIX}{digest}"


def map_bundle_state_to_draft_status(
    bundle: Mapping[str, Any] | None,
    *,
    canonical_entity_type: str | None = None,
    canonical_entity_id: str | None = None,
) -> str:
    if canonical_entity_type and canonical_entity_id:
        return CANONICALIZED
    if not bundle:
        return LUNA_UNREVIEWED
    state = str(bundle.get("state") or "REVIEW_PENDING")
    review = bundle.get("review") if isinstance(bundle.get("review"), Mapping) else {}
    outcome = str(review.get("outcome") or "")
    action = str(review.get("action_on_luna_proposal") or "")
    if state in {"OPUS_REQUIRED", "OPUS_REVIEWED", "POST_OPUS_PENDING"}:
        return OPUS_PENDING
    if state == "USER_DECISION_REQUIRED":
        return USER_DECISION_REQUIRED
    if state in {"BLOCKED", "INSUFFICIENT_EVIDENCE"} or outcome == "INSUFFICIENT_EVIDENCE":
        return GPT_REJECTED
    if state in {"CLEAR", "POST_OPUS_CLEAR", "WRITTEN", "FINALIZED", "POLICY_WRITE_READY"} or outcome == "CLEAR":
        auth = bundle.get("gpt_authoritative_consolidation")
        if isinstance(auth, Mapping) and (auth.get("revision_payload") or action in {"REVISE", "REVISED"}):
            return GPT_REVISED
        if bundle.get("is_authoritative") or state in {"WRITTEN", "FINALIZED", "POLICY_WRITE_READY"}:
            return GPT_ACCEPTED
        return GPT_ACCEPTED
    if state == "NO_ACTION" or outcome == "NO_ACTION":
        return GPT_ACCEPTED
    if state == "REVIEW_PENDING":
        return LUNA_UNREVIEWED
    return LUNA_UNREVIEWED


def projection_status_for_coverage(coverage_state: str, has_semantic: bool) -> str:
    if not has_semantic:
        if coverage_state in {"NO_KNOWLEDGE_IMPACT", "PROJECT_CONTEXT_ONLY"}:
            return PROJECTION_ONLY
        return NO_SEMANTIC
    return LUNA_UNREVIEWED


def merge_draft_status(review_status: str, coverage_status: str) -> str:
    priority = {
        CANONICALIZED: 100,
        GPT_REVISED: 90,
        GPT_ACCEPTED: 80,
        GPT_REJECTED: 70,
        OPUS_PENDING: 60,
        USER_DECISION_REQUIRED: 50,
        LUNA_UNREVIEWED: 40,
        PROJECTION_ONLY: 20,
        NO_SEMANTIC: 10,
    }
    if priority.get(review_status, 0) >= priority.get(coverage_status, 0):
        return review_status
    return coverage_status
