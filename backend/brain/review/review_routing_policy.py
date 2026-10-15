"""PAW Review Router V2 — deterministic routing policy (single source of truth).

Phase: PAW Review Router + Agy Native Chat V2 (pre-launch close-out).

DESIGN PRINCIPLES
-----------------
* "needs model analysis" != "needs user review".  Only content whose correct
  business meaning cannot be determined after Source / Evidence / Current State
  / Project Context analysis should reach the user.
* A user can only act on an item they can READ.  An item whose only content is
  an internal machine token (``project_user_decision_required``,
  ``finding_opus:CONFLICT;...``) is not a user decision yet — it is a
  system-side brief-regeneration job.  It must not occupy the user's queue.
* The router decides ROUTING only.  It never decides formal truth and never
  writes.  AUTO_FLOW_SAFE still travels the full governed chain
  Candidate -> ReviewDecision -> ApprovedChange -> Committer.
* Every rule is a deterministic predicate over structured fields.  No LLM
  inference is used to decide a route.
* Conservative fallback: anything that matches no rule is USER_REVIEW_REQUIRED.

Route precedence (evaluated in this exact order)::

    1. structural no-value   -> AUTO_CLOSE_NO_VALUE   (never destructive)
    2. genuine hard block    -> USER_REVIEW_REQUIRED
    3. every AUTO_FLOW_SAFE condition satisfied -> AUTO_FLOW_SAFE
    4. otherwise             -> USER_REVIEW_REQUIRED (conservative)

This module is pure: no I/O, no database access, no imports of the runtime.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# --------------------------------------------------------------------------
# Routes (exactly three)
# --------------------------------------------------------------------------
AUTO_FLOW_SAFE = "AUTO_FLOW_SAFE"
AUTO_CLOSE_NO_VALUE = "AUTO_CLOSE_NO_VALUE"
USER_REVIEW_REQUIRED = "USER_REVIEW_REQUIRED"
ROUTES = (AUTO_FLOW_SAFE, AUTO_CLOSE_NO_VALUE, USER_REVIEW_REQUIRED)

# --------------------------------------------------------------------------
# Targets
# --------------------------------------------------------------------------
T_KNOWLEDGE = "Knowledge"
T_PROJECT_OUTCOME = "ProjectOutcome"
T_PROJECT_HANDBOOK = "ProjectHandbook"
T_PROJECT_DATA = "ProjectData"
T_PROJECT_RELATION = "ProjectRelation"
T_SOURCE_GOVERNANCE = "SourceGovernance"
T_TODO = "Todo"
T_NO_FORMAL_WRITE = "NoFormalWrite"
TARGETS = (T_KNOWLEDGE, T_PROJECT_OUTCOME, T_PROJECT_HANDBOOK, T_PROJECT_DATA,
           T_PROJECT_RELATION, T_SOURCE_GOVERNANCE, T_TODO, T_NO_FORMAL_WRITE)

# --------------------------------------------------------------------------
# Risk classes
# --------------------------------------------------------------------------
RISK_LOW = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH = "HIGH"
FANOUT_LOCAL = "LOCAL"
FANOUT_BOUNDED = "BOUNDED"
FANOUT_BROAD = "BROAD"

#: recommended review decisions (mirrors review_decision.decision CHECK)
D_APPROVE = "approve"
D_EDIT_AND_APPROVE = "edit_and_approve"
D_REJECT = "reject"
D_DEFER = "defer"

# --------------------------------------------------------------------------
# Structural no-value reason codes (AUTO_CLOSE_NO_VALUE)
# --------------------------------------------------------------------------
R_EMPTY_CANDIDATE = "EMPTY_CANDIDATE"
R_SYSTEM_DIAGNOSTIC = "SYSTEM_DIAGNOSTIC_ONLY"
R_DUPLICATE_PROPOSAL = "DUPLICATE_PROPOSAL"
R_ALREADY_RESOLVED = "ALREADY_HAS_CLOSED_RESOLUTION"
R_SUPERSEDED = "SUPERSEDED_BY_NEWER_FORMAL_STATE"
R_TARGET_INVALID = "TARGET_NO_LONGER_VALID"
R_NO_EVIDENCE_NO_VALUE = "NO_EVIDENCE_AND_NO_BUSINESS_VALUE"
R_INTERNAL_REPAIR_ONLY = "INTERNAL_REPAIR_SIGNAL_ONLY"
R_LEGACY_MECHANICAL = "LEGACY_MECHANICAL_PROPOSAL"
R_EMPTY_PLACEHOLDER = "EMPTY_KNOWLEDGE_PLACEHOLDER"
R_NO_SOURCE_ATTACHMENT = "NO_SOURCE_ATTACHED"
R_SYSTEM_EVIDENCE = "SYSTEM_EVIDENCE_DEFICIENCY_ONLY"
R_UNRENDERABLE = "PRESENTATION_UNRENDERABLE"

#: reason codes whose disposition is "hand to the system-side remediation
#: worklist" rather than "genuinely valueless".  Both stay out of the user
#: queue; neither deletes or rejects anything.
SYSTEM_REMEDIATION_REASONS = frozenset({R_SYSTEM_EVIDENCE, R_UNRENDERABLE,
                                        R_EMPTY_PLACEHOLDER, R_NO_SOURCE_ATTACHMENT})

# --------------------------------------------------------------------------
# Hard-block reason codes (USER_REVIEW_REQUIRED)
# --------------------------------------------------------------------------
R_PROJECT_BOUNDARY = "PROJECT_BOUNDARY_CHANGE"
R_BUSINESS_CURRENT = "BUSINESS_CURRENT_JUDGMENT"
R_CURRENT_VS_HISTORICAL = "CURRENT_VS_HISTORICAL_VERSION"
R_BUSINESS_AUTHORITY = "BUSINESS_AUTHORITY_CHANGE"
R_SOURCE_CONFLICT = "SOURCE_AUTHORITY_CONFLICT"
R_FACT_CONFLICT = "FACT_CONFLICT"
R_ATTRIBUTION = "ATTRIBUTION_OR_ROLE_AMBIGUITY"
R_RELATION_AMBIGUOUS = "RELATION_MEANING_UNCERTAIN"
R_PERSONNEL = "PERSONNEL_ATTRIBUTION"
R_COMPENSATION = "COMPENSATION"
R_PERFORMANCE = "PERFORMANCE"
R_POLICY = "POLICY_DECISION"
R_MAJOR_CALIBER = "MAJOR_BUSINESS_CALIBER"
R_DESTRUCTIVE = "DESTRUCTIVE_OR_IRREVERSIBLE"
R_BROAD_FANOUT = "BROAD_FANOUT"
R_TARGET_NOT_UNIQUE = "TARGET_NOT_UNIQUE"
R_UNCERTAIN_ENVELOPE = "EXECUTION_ENVELOPE_UNCERTAIN"
R_EVIDENCE_GAP_HIGH_VALUE = "EVIDENCE_GAP_HIGH_VALUE"
R_NEEDS_USER_FACT = "REQUIRES_USER_SUPPLIED_FACT"
R_UNMATCHED = "NO_RULE_MATCHED_CONSERVATIVE"

# --------------------------------------------------------------------------
# AUTO_FLOW_SAFE qualifier reason codes
# --------------------------------------------------------------------------
R_IDEMPOTENT_LINK = "IDEMPOTENT_UNIQUE_TARGET_LINK"
R_DETERMINISTIC_PARSER = "DETERMINISTIC_PARSER_RESULT"
R_APPROVED_SUCCESSOR = "USER_ALREADY_APPROVED_SUCCESSOR"
R_MECHANICAL_EXECUTION = "MECHANICAL_EXECUTION_OF_APPROVED_DECISION"
R_DETERMINISTIC_IDENTITY = "DETERMINISTIC_LINEAGE_IDENTITY"

# --------------------------------------------------------------------------
# Candidate-type -> default target mapping
# --------------------------------------------------------------------------
TYPE_DEFAULT_TARGET = {
    "knowledge": T_KNOWLEDGE,
    "project_update": T_PROJECT_OUTCOME,
    "todo": T_TODO,
    "fact": T_KNOWLEDGE,
    "conflict": T_NO_FORMAL_WRITE,
    "ambiguity": T_NO_FORMAL_WRITE,
    "question": T_NO_FORMAL_WRITE,
}

# --------------------------------------------------------------------------
# Marker vocabularies (from the real analysis engine's why_unresolved field)
# --------------------------------------------------------------------------
#: an unresolved marker that is purely a SYSTEM deficiency: the system lacks
#: evidence / extraction / coverage.  Not a user business decision.
SYSTEM_DEFICIENCY_MARKERS = (
    "coverage_review_required",
    "finding_insufficient:",
    "DATA_EXTRACTION_REVIEW_REQUIRED",
    "EVIDENCE_LIMIT", "EVIDENCE_GAP", "DATA_GAP",
)

#: an unresolved marker that genuinely requires a human business decision
USER_DECISION_MARKERS = (
    "finding_user_decision:",
    "project_user_decision_required",
    "user_decision_required",
    "AMBIGUITY",
)

#: an unresolved marker denoting a real conflict
CONFLICT_MARKERS = (
    "CONFLICT", "conflict", "irreconcilable", "INCONSISTENT",
)

#: a token shaped like an internal reason marker rather than business prose
_TOKEN = r"[a-z][a-z0-9_]*(?::[A-Za-z0-9_]+)?"
_MARKER_LINE_RE = re.compile(r"^%s(;%s)*$" % (_TOKEN, _TOKEN))
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

HARD_BLOCK_MARKERS = {
    R_PROJECT_BOUNDARY: ("project_create", "project_split", "project_merge",
                         "project_boundary_change", "创建项目", "拆分项目",
                         "合并项目"),
    R_BUSINESS_CURRENT: ("business_current", "current_business", "当前业务口径"),
    R_CURRENT_VS_HISTORICAL: ("canonical_topic_version_conflict",
                              "current_vs_historical", "supersede", "取代",
                              "哪个继续生效", "历史版本"),
    R_BUSINESS_AUTHORITY: ("business_authority", "authority_change"),
    R_PERSONNEL: ("personnel", "人员归因"),
    R_ATTRIBUTION: ("ROLE_AMBIGUITY", "IDENTITY_AMBIGUITY",
                    "IDENTITY_AND_ROLE", "身份归属"),
    R_COMPENSATION: ("compensation", "薪酬", "薪资"),
    R_PERFORMANCE: ("performance", "绩效"),
    R_POLICY: ("policy", "政策", "制度决策"),
    R_MAJOR_CALIBER: ("metric_unit_conflict", "统计口径", "计量口径",
                      "口径不一致", "单位不一致"),
    R_DESTRUCTIVE: ("delete", "删除", "archive", "归档", "irreversible", "不可逆"),
}

#: operations that are never auto-flow safe
FORBIDDEN_OPERATIONS = frozenset({
    "create", "archive", "delete", "split", "merge", "revoke", "purge",
})


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _contains_any(haystack: str, needles: Iterable[str]) -> bool:
    low = haystack.casefold()
    return any(n.casefold() in low for n in needles)


def looks_like_marker(text: str) -> bool:
    """True when ``text`` is an internal reason marker, not business prose."""
    t = _s(text).strip()
    if not t:
        return False
    return bool(_MARKER_LINE_RE.match(t))


def is_renderable(*texts: str) -> bool:
    """True when at least one primary field carries readable business prose.

    A field counts as readable when it is non-empty and is NOT a bare internal
    marker token.  Chinese business text and English narrative prose both count.
    """
    for t in texts:
        t = _s(t).strip()
        if t and not _MARKER_LINE_RE.match(t):
            return True
    return False


def split_markers(ctx: dict[str, Any]) -> tuple[list[str], str]:
    """Return (markers, narrative) from the candidate's unresolved explanation."""
    raw = _s(ctx.get("why_unresolved")).strip()
    if not raw:
        return [], ""
    if _MARKER_LINE_RE.match(raw):
        return [t.strip() for t in raw.split(";") if t.strip()], ""
    return [], raw


# --------------------------------------------------------------------------
# Predicates
# --------------------------------------------------------------------------
def structural_no_value_reasons(ctx: dict[str, Any]) -> list[str]:
    """Unambiguous signals that the item must not occupy the user's queue.

    Two families live here, both non-destructive (nothing is deleted, rejected
    or closed in the database):

    * genuinely valueless      — closed / duplicate / superseded / empty
    * system-side remediation  — the item cannot be presented as a business
      question yet (unrenderable brief, or a pure evidence/extraction gap), so
      the SYSTEM must act first.  See ``SYSTEM_REMEDIATION_REASONS``.
    """
    reasons: list[str] = []

    proposed = ctx.get("proposed_change")
    has_proposed = isinstance(proposed, dict) and bool(proposed)
    has_excerpt = bool(_s(ctx.get("raw_excerpt")).strip())
    has_evidence = bool(ctx.get("evidence_refs"))

    if not has_proposed and not has_excerpt and not has_evidence:
        reasons.append(R_EMPTY_CANDIDATE)
    if ctx.get("closed_resolution"):
        reasons.append(R_ALREADY_RESOLVED)
    if ctx.get("duplicate_of"):
        reasons.append(R_DUPLICATE_PROPOSAL)
    if ctx.get("superseded_by"):
        reasons.append(R_SUPERSEDED)
    if ctx.get("target_invalid"):
        reasons.append(R_TARGET_INVALID)
    if ctx.get("internal_repair_only"):
        reasons.append(R_INTERNAL_REPAIR_ONLY)
    if ctx.get("legacy_mechanical"):
        reasons.append(R_LEGACY_MECHANICAL)
    if ctx.get("system_diagnostic_only"):
        reasons.append(R_SYSTEM_DIAGNOSTIC)

    # --- empty knowledge placeholder: nothing to decide, housekeeping only --
    if ctx.get("empty_placeholder"):
        reasons.append(R_EMPTY_PLACEHOLDER)
        if not has_evidence:
            reasons.append(R_NO_SOURCE_ATTACHMENT)

    # --- the user cannot act on what the user cannot read -------------------
    if ctx.get("presentation_renderable") is False:
        reasons.append(R_UNRENDERABLE)

    # --- pure system deficiency: the SYSTEM must gather evidence ------------
    elif ctx.get("system_deficiency_only"):
        reasons.append(R_SYSTEM_EVIDENCE)

    if not has_evidence and not ctx.get("business_value"):
        reasons.append(R_NO_EVIDENCE_NO_VALUE)

    # de-duplicate, keep order
    seen: set[str] = set()
    out: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def hard_block_reasons(ctx: dict[str, Any]) -> list[str]:
    """Return the hard-block reason codes that force USER_REVIEW_REQUIRED.

    Reached only for items that ARE renderable; an unrenderable item is already
    diverted to system-side remediation by ``structural_no_value_reasons``.
    """
    reasons: list[str] = []
    ctype = _s(ctx.get("candidate_type"))
    markers, narrative = split_markers(ctx)
    marker_blob = ";".join(markers)
    blob = " ".join([
        _s(ctx.get("proposed_change_text")), _s(ctx.get("raw_excerpt")),
        _s(ctx.get("review_brief_text")), _s(ctx.get("update_summary")),
        _s(ctx.get("title")), marker_blob, narrative,
    ])
    op = _s(ctx.get("operation")).casefold()

    if ctype == "conflict" or ctx.get("semantic_conflict"):
        reasons.append(R_FACT_CONFLICT)
    if ctype == "ambiguity":
        # reached only for a RENDERABLE ambiguity: a real business question
        reasons.append(R_NEEDS_USER_FACT)
    if ctx.get("authority_conflict"):
        reasons.append(R_SOURCE_CONFLICT)
    if ctx.get("multiple_versions_unresolved"):
        reasons.append(R_CURRENT_VS_HISTORICAL)
    if ctx.get("relation_meaning_uncertain"):
        reasons.append(R_RELATION_AMBIGUOUS)
    if ctx.get("requires_user_supplied_fact"):
        reasons.append(R_NEEDS_USER_FACT)
    if ctx.get("target_not_unique"):
        reasons.append(R_TARGET_NOT_UNIQUE)
    if ctx.get("fanout_scope") == FANOUT_BROAD:
        reasons.append(R_BROAD_FANOUT)
    if op in FORBIDDEN_OPERATIONS or ctx.get("destructive_change"):
        reasons.append(R_DESTRUCTIVE)
    if ctx.get("evidence_gap_high_value"):
        reasons.append(R_EVIDENCE_GAP_HIGH_VALUE)
    if ctx.get("project_boundary_change"):
        reasons.append(R_PROJECT_BOUNDARY)
    if any(k in marker_blob for k in CONFLICT_MARKERS) and R_FACT_CONFLICT not in reasons:
        reasons.append(R_FACT_CONFLICT)
    if any(k in marker_blob for k in USER_DECISION_MARKERS) \
            and not (set(reasons) & {R_NEEDS_USER_FACT, R_FACT_CONFLICT}):
        reasons.append(R_NEEDS_USER_FACT)

    for code, needles in HARD_BLOCK_MARKERS.items():
        if code in reasons:
            continue
        if _contains_any(blob, needles):
            reasons.append(code)

    seen: set[str] = set()
    out: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def auto_flow_safe_reasons(ctx: dict[str, Any]) -> list[str]:
    """Return the qualifier codes when EVERY AUTO_FLOW_SAFE condition holds.

    Empty list means the candidate is not auto-flow safe.
    """
    required_true = ("evidence_complete", "target_unique",
                     "deterministic_envelope")
    for key in required_true:
        if ctx.get(key) is not True:
            return []

    required_false = ("semantic_conflict", "business_authority_change",
                      "destructive_change",
                      "requires_human_semantic_judgment")
    for key in required_false:
        if ctx.get(key) is not False:
            return []

    if ctx.get("fanout_scope") not in (FANOUT_LOCAL, FANOUT_BOUNDED):
        return []
    if ctx.get("model_must_guess_fields") is True:
        return []
    if _s(ctx.get("operation")).casefold() in FORBIDDEN_OPERATIONS:
        return []
    if ctx.get("missing_required_fields"):
        return []
    if ctx.get("presentation_renderable") is False:
        return []

    # source-identity writes need a deterministic key, never a guess
    if ctx.get("touches_source_identity"):
        if ctx.get("deterministic_identity") is not True:
            return []
        kind = _s(ctx.get("identity_key_kind")).casefold()
        if kind not in ("object_id", "source_version", "sha256",
                        "authoritative_provider_id", "crosswalk"):
            return []
        if kind in ("title", "similarity", "model_guess"):
            return []

    why: list[str] = []
    if ctx.get("user_approved_successor"):
        why.append(R_APPROVED_SUCCESSOR)
    if ctx.get("deterministic_parser"):
        why.append(R_DETERMINISTIC_PARSER)
    if ctx.get("idempotent_unique_target_link"):
        why.append(R_IDEMPOTENT_LINK)
    if ctx.get("mechanical_execution"):
        why.append(R_MECHANICAL_EXECUTION)
    if ctx.get("deterministic_identity") is True:
        why.append(R_DETERMINISTIC_IDENTITY)
    if not why:
        why.append(R_IDEMPOTENT_LINK)
    return why


def route(ctx: dict[str, Any]) -> dict[str, Any]:
    """Deterministic route for one candidate context (see module docstring)."""
    structural = structural_no_value_reasons(ctx)
    if structural:
        return {"route": AUTO_CLOSE_NO_VALUE, "reason_codes": structural,
                "recommended_review_decision": D_DEFER,
                "risk_class": RISK_LOW,
                "matched_rule": "STRUCTURAL_NO_VALUE",
                "system_remediation":
                    bool(set(structural) & SYSTEM_REMEDIATION_REASONS)}

    blocks = hard_block_reasons(ctx)
    if blocks:
        return {"route": USER_REVIEW_REQUIRED, "reason_codes": blocks,
                "recommended_review_decision": D_EDIT_AND_APPROVE,
                "risk_class": RISK_HIGH,
                "matched_rule": "HARD_BLOCK",
                "system_remediation": False}

    flow = auto_flow_safe_reasons(ctx)
    if flow:
        return {"route": AUTO_FLOW_SAFE, "reason_codes": flow,
                "recommended_review_decision": D_APPROVE,
                "risk_class": RISK_LOW,
                "matched_rule": "AUTO_FLOW_SAFE",
                "system_remediation": False}

    return {"route": USER_REVIEW_REQUIRED, "reason_codes": [R_UNMATCHED],
            "recommended_review_decision": D_EDIT_AND_APPROVE,
            "risk_class": RISK_MEDIUM,
            "matched_rule": "CONSERVATIVE_FALLBACK",
            "system_remediation": False}


def policy_spec() -> dict[str, Any]:
    """Machine-readable policy, for artifacts and the frontend contract."""
    return {
        "version": "REVIEW_ROUTER_V2",
        "routes": list(ROUTES),
        "route_precedence": ["STRUCTURAL_NO_VALUE", "HARD_BLOCK",
                             "AUTO_FLOW_SAFE", "CONSERVATIVE_FALLBACK"],
        "targets": list(TARGETS),
        "auto_flow_safe_mandatory": {
            "must_be_true": ["evidence_complete", "target_unique",
                             "deterministic_envelope"],
            "must_be_false": ["semantic_conflict", "business_authority_change",
                              "destructive_change",
                              "requires_human_semantic_judgment"],
            "fanout_allowed": [FANOUT_LOCAL, FANOUT_BOUNDED],
            "model_must_guess_fields": False,
            "source_identity_requires": ["object_id", "source_version", "sha256",
                                         "authoritative_provider_id", "crosswalk"],
            "source_identity_forbids": ["title", "similarity", "model_guess"],
        },
        "forbidden_operations": sorted(FORBIDDEN_OPERATIONS),
        "structural_no_value_reason_codes": sorted({
            R_EMPTY_CANDIDATE, R_SYSTEM_DIAGNOSTIC, R_DUPLICATE_PROPOSAL,
            R_ALREADY_RESOLVED, R_SUPERSEDED, R_TARGET_INVALID,
            R_NO_EVIDENCE_NO_VALUE, R_INTERNAL_REPAIR_ONLY, R_LEGACY_MECHANICAL,
            R_EMPTY_PLACEHOLDER, R_NO_SOURCE_ATTACHMENT, R_SYSTEM_EVIDENCE,
            R_UNRENDERABLE}),
        "system_remediation_reason_codes": sorted(SYSTEM_REMEDIATION_REASONS),
        "hard_block_reason_codes": sorted(set(HARD_BLOCK_MARKERS) | {
            R_FACT_CONFLICT, R_SOURCE_CONFLICT, R_NEEDS_USER_FACT,
            R_TARGET_NOT_UNIQUE, R_BROAD_FANOUT, R_DESTRUCTIVE,
            R_EVIDENCE_GAP_HIGH_VALUE, R_RELATION_AMBIGUOUS,
            R_CURRENT_VS_HISTORICAL}),
        "governance_chain_always": ["candidate", "review_decision",
                                    "approved_change", "committer"],
        "auto_reviewer_identity": "auto_router_v2",
        "never_writes_formal_state": True,
    }
