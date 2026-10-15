"""Project Soul/Template v1 and governed new-Project bootstrap.

This module is deliberately additive.  It treats the existing Project row as
the canonical boundary, the existing Handbook/Explainer/Outcome artifacts as
the semantic read plane, and the existing Candidate -> ReviewDecision ->
ApprovedChange -> Committer chain as the only formal write path.

The validator is moderately constrained: it checks semantic concept coverage
and provenance/uncertainty, but does not prescribe wording, order, length, or
project type.  ``install_committer_integration`` is an explicit runtime hook;
it does not expose a new MCP CRUD operation.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from paw_patrol_contract import FORMAL_STATE_BYPASS, USER_DECISION_REQUIRED

SOUL_SCHEMA_VERSION = "paw-project-soul/1"
BOOTSTRAP_SCHEMA_VERSION = "paw-project-bootstrap/1"
PROJECT_CREATE_CANDIDATE_TYPE = "project_update"
PROJECT_CREATE_OPERATION = "create"
PROJECT_CREATE_SAFE_AUTO_APPLY = False
UNKNOWN_STATES = frozenset({"UNKNOWN", "INSUFFICIENT_EVIDENCE", "NOT_YET_APPLICABLE"})
QUALITY_STATES = frozenset({"PASS", "PASS_PARTIAL", "NEEDS_COMPLETION", "INVALID"})
REVIEW_MODE_EXECUTION_READY = "EXECUTION_READY"
_CJK_SUFFIXES = ("培养体系", "管理体系", "工作体系", "运营体系", "体系", "项目", "计划", "工程", "方案", "平台")
_PAST_ACHIEVEMENT_RE = re.compile(
    r"(?:"
    r"已(?:完成|实现|达成|交付|上线|落地)|已经完成|已全部|"
    r"(?:完成了|实现了|达成了|已.+里程碑)|"
    r"\b(?:completed|achieved|delivered|in production)\b"
    r")",
    re.IGNORECASE,
)
_FUTURE_GOAL_RE = re.compile(
    r"(?:"
    r"^(?:完成|实现|达成|交付|建设|打造|形成|建立)(?!已)|"
    r"(?:预计|计划|目标|拟|将|待).+|"
    r"(?:建设|形成|建立|打造|机制|分层|体系化)$"
    r")",
)

CORE_CONCEPTS: tuple[str, ...] = (
    "identity",
    "why_and_background",
    "goals",
    "scope",
    "current_state",
    "key_results_or_expected_outcomes",
    "next_step_or_open_items",
    "risks_blockers_ambiguities",
    "evidence_provenance",
)

CONCEPT_ALIASES: dict[str, tuple[str, ...]] = {
    "identity": ("identity", "one_sentence", "project_identity", "definition", "project_name"),
    "why_and_background": ("why_and_background", "problem_it_solves", "background", "project_background", "business_purpose"),
    "goals": ("goals", "goal", "objectives", "core_objectives", "current_stage_goal", "ultimate_outcome"),
    "scope": ("scope", "in_scope", "boundary", "project_scope", "non_scope", "out_of_scope"),
    "current_state": ("current_state", "current_phase_and_status", "current_phase", "status", "phase", "current_progress"),
    "key_results_or_expected_outcomes": ("key_results", "expected_outcomes", "outcomes", "key_results_or_expected_outcomes"),
    "next_step_or_open_items": ("next_step", "open_items", "next_actions", "next_action", "forward_plan"),
    "risks_blockers_ambiguities": ("risks_blockers_ambiguities", "risks", "blockers", "ambiguities", "risk", "open_risks"),
    "evidence_provenance": ("evidence_provenance", "evidence", "evidence_refs", "source_refs", "provenance", "decision_basis"),
}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _nonempty(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, (list, tuple, set, dict)):
        return any(_nonempty(x) for x in value) if not isinstance(value, dict) else any(_nonempty(v) for v in value.values())
    return bool(_text(value))


def _is_unknown(value: Any) -> bool:
    if isinstance(value, Mapping):
        state = _text(value.get("state") or value.get("status") or value.get("evidence_status")).upper()
        if state in UNKNOWN_STATES:
            return True
        return _is_unknown(value.get("value")) if "value" in value else False
    if isinstance(value, list):
        return bool(value) and all(_is_unknown(item) for item in value)
    return _text(value).upper() in UNKNOWN_STATES


def _flatten_text(value: Any) -> str:
    if isinstance(value, Mapping):
        return " ".join(_flatten_text(v) for k, v in value.items() if k not in {"evidence_refs", "source_refs", "provenance"})
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_text(v) for v in value)
    return _text(value)


def _unwrap(document: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(document)
    for key in ("project_soul", "soul", "project_explainer"):
        nested = raw.get(key)
        if isinstance(nested, Mapping):
            merged = dict(raw)
            merged.pop(key, None)
            merged.update(nested)
            raw = merged
    sections = raw.get("sections")
    if isinstance(sections, Mapping):
        merged = dict(raw)
        merged.pop("sections", None)
        for key, value in sections.items():
            merged.setdefault(key, value)
        raw = merged
    return raw


def _find_value(document: Mapping[str, Any], aliases: Iterable[str]) -> tuple[str, Any] | None:
    for alias in aliases:
        if alias in document and _nonempty(document[alias]):
            return alias, document[alias]
    # Existing Handbook section mappings are common.  Search one level down,
    # without inventing a value from unrelated Project columns.
    for section_name, section in document.items():
        if not isinstance(section, Mapping):
            continue
        for alias in aliases:
            if alias in section and _nonempty(section[alias]):
                return f"{section_name}.{alias}", section[alias]
    return None


def _evidence_refs(document: Mapping[str, Any]) -> list[Any]:
    refs: list[Any] = []
    for key in ("evidence_refs", "source_refs", "evidence", "provenance", "decision_basis"):
        value = document.get(key)
        if isinstance(value, Mapping) and "value" in value:
            value = value.get("value")
        if isinstance(value, list):
            refs.extend(value)
        elif _nonempty(value) and not _is_unknown(value):
            refs.append(value)
    return refs


def _tautological(name: str, one_sentence: Any) -> bool:
    name_norm = re.sub(r"[^\w\u4e00-\u9fff]+", "", _text(name).casefold())
    sentence = re.sub(r"[^\w\u4e00-\u9fff]+", "", _text(one_sentence).casefold())
    if not sentence:
        return False
    if name_norm and sentence == name_norm:
        return True
    return bool(name_norm and len(sentence) < 18 and name_norm in sentence and re.search(r"(项目|project|体系|计划)$", sentence))


def validate_project_soul(document: Mapping[str, Any], *, project_name: str | None = None, project_kind: str = "mature") -> dict[str, Any]:
    """Validate semantic coverage without enforcing prose/layout.

    ``project_kind='new'`` accepts expected outcomes and does not require
    historical results.  Explicit unknown/insufficient states count as
    ``PASS_PARTIAL`` rather than as model-invented completion.
    """
    raw = _unwrap(document)
    name = _text(project_name or raw.get("project_name") or raw.get("name"))
    concepts: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    partial: list[str] = []
    missing: list[str] = []

    for concept in CORE_CONCEPTS:
        found = _find_value(raw, CONCEPT_ALIASES[concept])
        if found is None and concept == "risks_blockers_ambiguities":
            for alias in CONCEPT_ALIASES[concept]:
                if alias in raw and raw[alias] == []:
                    found = (alias, [])
                    break
        state = "PASS"
        value = found[1] if found else None
        if found is None:
            state = "NEEDS_COMPLETION"
            missing.append(concept)
        elif _is_unknown(value):
            state = "PASS_PARTIAL"
            partial.append(concept)
        concepts[concept] = {"state": state, "field": found[0] if found else None, "has_content": found is not None, "unknown_or_insufficient": _is_unknown(value)}

    identity = _find_value(raw, CONCEPT_ALIASES["identity"])
    one_sentence = identity[1] if identity else None
    if identity is not None and _tautological(name, one_sentence):
        concepts["identity"]["state"] = "INVALID"
        errors.append("tautological_one_sentence")

    evidence = _evidence_refs(raw)
    if not evidence:
        # Evidence can be explicitly absent on a new/legacy Project, but the
        # contract must make that state visible rather than imply support.
        if concepts["evidence_provenance"]["state"] == "NEEDS_COMPLETION":
            concepts["evidence_provenance"]["state"] = "PASS_PARTIAL"
            concepts["evidence_provenance"]["unknown_or_insufficient"] = True
            partial.append("evidence_provenance")

    outcome_value = _find_value(raw, CONCEPT_ALIASES["key_results_or_expected_outcomes"])
    if project_kind.lower() == "new" and outcome_value is not None:
        outcome_items: list[str]
        raw_outcomes = outcome_value[1]
        if isinstance(raw_outcomes, list):
            outcome_items = [_flatten_text(item) for item in raw_outcomes if _nonempty(item)]
        else:
            outcome_items = [_flatten_text(raw_outcomes)]
        fake_result = any(_claims_past_achievement(item) for item in outcome_items if item)
        if fake_result and not evidence:
            concepts["key_results_or_expected_outcomes"]["state"] = "NEEDS_COMPLETION"
            partial.append("key_results_or_expected_outcomes")
            errors.append("new_project_claims_completed_result_without_evidence")

    # Claims that look like unsupported KPIs/personnel/policies are held when
    # they have neither refs nor an explicit uncertainty marker.
    if not evidence:
        optional_claims = raw.get("metrics") or raw.get("kpi") or raw.get("stakeholders") or raw.get("policy")
        if _nonempty(optional_claims) and not _is_unknown(optional_claims):
            errors.append("unsupported_optional_claim_without_evidence")

    # A project may legitimately have no risks; the category is present when
    # explicitly empty or unknown, but absent content remains completion work.
    risk_found = _find_value(raw, CONCEPT_ALIASES["risks_blockers_ambiguities"])
    if risk_found is not None and isinstance(risk_found[1], list) and not risk_found[1]:
        concepts["risks_blockers_ambiguities"]["state"] = "PASS"

    invalid_concepts = [k for k, v in concepts.items() if v["state"] == "INVALID"]
    if invalid_concepts:
        quality = "INVALID"
    elif missing:
        # Existing mature Projects are backward compatible: absence of a
        # preferred v1 field is a partial readiness state, while a new
        # proposal must complete the contract before review/creation.
        quality = "PASS_PARTIAL" if project_kind.lower() != "new" else "NEEDS_COMPLETION"
    elif partial:
        quality = "PASS_PARTIAL"
    else:
        quality = "PASS"
    return {
        "schema_version": SOUL_SCHEMA_VERSION,
        "quality": quality,
        "concepts": concepts,
        "missing_or_partial_concepts": list(dict.fromkeys([*missing, *partial])),
        "invalid_concepts": invalid_concepts,
        "errors": list(dict.fromkeys(errors)),
        "project_kind": project_kind,
        "evidence_state": "SUFFICIENT" if evidence else "INSUFFICIENT_EVIDENCE",
        "optional_extensions_preserved": sorted(k for k in raw if k not in {"name", "project_name", *CONCEPT_ALIASES.keys()}),
        "formal_state_bypass": FORMAL_STATE_BYPASS,
    }


def _soul_value(soul: Mapping[str, Any], concept: str, default: Any = None) -> Any:
    found = _find_value(_unwrap(soul), CONCEPT_ALIASES[concept])
    return found[1] if found else default


def project_description_from_soul(soul: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return a derived description and its source metadata."""
    for concept, source in (("identity", "project_soul.identity"), ("current_state", "project_soul.current_state"), ("why_and_background", "project_soul.why_and_background")):
        value = _soul_value(soul, concept)
        text = _flatten_text(value)
        if text and not _is_unknown(value):
            return text[:2000], {"source": source, "schema_version": SOUL_SCHEMA_VERSION, "derived": True}
    return "Evidence insufficient: project summary is not yet established.", {"source": "project_soul.evidence_state", "schema_version": SOUL_SCHEMA_VERSION, "derived": True}


def _normal_name(value: Any) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", _text(value).casefold())


def _compact_name(value: Any) -> str:
    return _normal_name(value)


def _strip_name_suffixes(value: str) -> str:
    stem = _compact_name(value)
    for suffix in _CJK_SUFFIXES:
        if stem.endswith(suffix) and len(stem) > len(suffix) + 1:
            return stem[: -len(suffix)]
    return stem


def _ordered_subsequence(short: str, long: str) -> bool:
    if not short or not long:
        return False
    if len(short) > len(long):
        short, long = long, short
    index = 0
    for ch in short:
        pos = long.find(ch, index)
        if pos < 0:
            return False
        index = pos + 1
    return True


def _cjk_bigram_jaccard(left: str, right: str) -> float:
    def _bigrams(text: str) -> set[str]:
        chars = [ch for ch in text if ("\u4e00" <= ch <= "\u9fff") or ch.isalnum()]
        if len(chars) < 2:
            return {"".join(chars)} if chars else set()
        return {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}

    a = _bigrams(left)
    b = _bigrams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _claims_past_achievement(text: str) -> bool:
    value = _text(text)
    if not value:
        return False
    if _FUTURE_GOAL_RE.search(value) and not _PAST_ACHIEVEMENT_RE.search(value):
        return False
    return bool(_PAST_ACHIEVEMENT_RE.search(value))


def _format_evidence_ref(ref: Any) -> str:
    if isinstance(ref, Mapping):
        locator = _text(ref.get("evidence_locator") or ref.get("source_version_id") or ref.get("id"))
        if locator:
            return locator
    return _text(ref)


def find_existing_project_matches(project_name: str, projects: Iterable[Mapping[str, Any]], *, soul: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Find exact/semantic candidates; filenames or folders are never enough."""
    target = _compact_name(project_name)
    target_stem = _strip_name_suffixes(project_name)
    target_tokens = set(re.findall(r"[\w\u4e00-\u9fff]{2,}", _text(project_name).casefold()))
    soul_tokens = set(re.findall(r"[\w\u4e00-\u9fff]{3,}", _flatten_text(soul or {}).casefold()))
    matches: list[dict[str, Any]] = []
    for project in projects:
        name = _text(project.get("name") or project.get("project_name"))
        if not name:
            continue
        candidate = _compact_name(name)
        candidate_stem = _strip_name_suffixes(name)
        candidate_tokens = set(re.findall(r"[\w\u4e00-\u9fff]{2,}", name.casefold()))
        overlap = len(target_tokens & candidate_tokens) / max(1, len(target_tokens | candidate_tokens))
        exact = bool(target and target == candidate)
        stem_exact = bool(target_stem and target_stem == candidate_stem)
        shorter, longer = (target, candidate) if len(target) <= len(candidate) else (candidate, target)
        strong_containment = (
            len(shorter) >= 4
            and _ordered_subsequence(shorter, longer)
            and (len(longer) - len(shorter)) <= 6
        )
        bigram_overlap = _cjk_bigram_jaccard(target, candidate)
        context = " ".join(_text(project.get(key)) for key in ("description", "definition", "stage", "latest_progress_summary"))
        context_tokens = set(re.findall(r"[\w\u4e00-\u9fff]{3,}", context.casefold()))
        semantic_overlap = len(soul_tokens & context_tokens) / max(1, len(soul_tokens)) if soul_tokens and context_tokens else 0.0
        if exact or stem_exact:
            match_type = "EXACT_NAME"
            confidence = "HIGH"
        elif strong_containment or bigram_overlap >= 0.72:
            match_type = "PROBABLE_DUPLICATE"
            confidence = "HIGH"
        elif overlap >= 0.6:
            match_type = "SEMANTIC_NAME"
            confidence = "MEDIUM"
        elif semantic_overlap >= 0.35:
            match_type = "SEMANTIC_CONTEXT"
            confidence = "MEDIUM"
        else:
            continue
        matches.append(
            {
                "project_id": _text(project.get("id") or project.get("project_id")),
                "project_name": name,
                "match_type": match_type,
                "confidence": confidence,
                "reason": "existing canonical Project identity/context match; requires update-v-create decision",
            }
        )
    return matches


def discover_project_create_proposal(analysis: Mapping[str, Any], projects: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Turn incremental analysis into a reviewable candidate, never a Project."""
    raw = dict(analysis)
    name = _text(raw.get("proposed_name") or raw.get("project_name") or raw.get("name"))
    soul = dict(raw.get("project_soul") or raw.get("soul") or raw)
    quality = validate_project_soul(soul, project_name=name, project_kind="new")
    matches = find_existing_project_matches(name, projects, soul=soul)
    if matches:
        disposition = USER_DECISION_REQUIRED
        reason = "Possible existing Project match; choose update-existing versus new boundary."
    elif quality["quality"] in {"INVALID", "NEEDS_COMPLETION"}:
        disposition = "NEEDS_COMPLETION"
        reason = "Project Soul proposal is incomplete or invalid; no canonical boundary is created."
    else:
        disposition = USER_DECISION_REQUIRED
        reason = "New boundary is plausible; explicit human approval is required before Project.create."
    proposal_id = _text(raw.get("proposal_id")) or "project-proposal:" + _hash({"name": name, "soul": soul, "evidence": raw.get("evidence_refs")})[:28]
    return {
        "proposal_id": proposal_id,
        "candidate_type": PROJECT_CREATE_CANDIDATE_TYPE,
        "operation": PROJECT_CREATE_OPERATION,
        "project_boundary_intent": "CREATE",
        "proposed_name": name,
        "project_soul": soul,
        "soul_validation": quality,
        "evidence_refs": list(raw.get("evidence_refs") or raw.get("source_refs") or []),
        "related_existing_project_checks": matches,
        "why_new_project_not_update": _text(raw.get("why_new_project_not_update") or raw.get("boundary_rationale") or "") or "Not established by model; human review must confirm boundary rationale.",
        "disposition": disposition,
        "review_reason": reason,
        "user_decision_required": True,
        "formal_state_bypass": FORMAL_STATE_BYPASS,
    }


def project_create_candidate_payload(proposal: Mapping[str, Any]) -> dict[str, Any]:
    """Human-readable Candidate proposed_change payload for existing review UI."""
    proposal_id = _text(proposal.get("proposal_id"))
    name = _text(proposal.get("proposed_name"))
    soul = dict(proposal.get("project_soul") or {})
    project_id = _text(proposal.get("project_id")) or "project:" + _hash({"proposal_id": proposal_id, "name": name})[:28]
    description, projection = project_description_from_soul(soul)
    return {
        "create_intent": True,
        "proposal_id": proposal_id,
        "id": project_id,
        "name": name,
        "description": description,
        "description_projection": projection,
        "status": _text(proposal.get("status")) or "planning",
        "stage": _text(proposal.get("stage") or _soul_value(soul, "current_state", "")),
        "next_action_summary": _flatten_text(_soul_value(soul, "next_step_or_open_items", ""))[:1000],
        "project_soul": soul,
        "expected_outcomes": _soul_value(soul, "key_results_or_expected_outcomes", []),
        "evidence_refs": list(proposal.get("evidence_refs") or []),
        "related_existing_project_checks": list(proposal.get("related_existing_project_checks") or []),
        "why_new_project_not_update": _text(proposal.get("why_new_project_not_update")),
        "bootstrap": True,
        "bootstrap_schema_version": BOOTSTRAP_SCHEMA_VERSION,
    }


def merge_project_soul(existing: Mapping[str, Any], incoming: Mapping[str, Any], *, impacted_concepts: Iterable[str] | None = None) -> dict[str, Any]:
    """Incrementally update only impacted concepts and preserve all else."""
    result = dict(existing)
    incoming_raw = _unwrap(incoming)
    allowed = set(impacted_concepts or CORE_CONCEPTS)
    for concept in allowed:
        aliases = CONCEPT_ALIASES.get(concept, (concept,))
        found = _find_value(incoming_raw, aliases)
        if found:
            # Store under the stable contract key; other extensions remain as-is.
            result[concept] = found[1]
    for key, value in incoming_raw.items():
        if key not in {alias for aliases in CONCEPT_ALIASES.values() for alias in aliases} and key not in {"name", "project_name"}:
            result[key] = value
    return result


def ensure_bootstrap_schema(conn: sqlite3.Connection) -> None:
    """Add only the provenance ledger needed by the bootstrap hook."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS project_bootstrap_provenance (
            bootstrap_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            candidate_id TEXT,
            review_decision_id TEXT,
            approved_change_id TEXT NOT NULL UNIQUE,
            commit_record_id TEXT,
            soul_schema_version TEXT NOT NULL,
            bootstrap_schema_version TEXT NOT NULL,
            input_watermark TEXT NOT NULL,
            artifact_revision INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_project_bootstrap_project ON project_bootstrap_provenance(project_id);
        """
    )


def _create_artifact_tables(conn: sqlite3.Connection) -> None:
    """Ensure clean synthetic DBs can exercise the same bootstrap contract."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS project_outcome (
            project_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL, current_status TEXT, phase TEXT,
            executive_summary TEXT NOT NULL, objectives_json TEXT NOT NULL DEFAULT '[]', scope_json TEXT NOT NULL DEFAULT '{}',
            latest_progress_json TEXT NOT NULL DEFAULT '[]', completed_milestones_json TEXT NOT NULL DEFAULT '[]', open_items_json TEXT NOT NULL DEFAULT '[]',
            key_decisions_json TEXT NOT NULL DEFAULT '[]', current_effective_rules_json TEXT NOT NULL DEFAULT '[]', risks_json TEXT NOT NULL DEFAULT '[]',
            blockers_json TEXT NOT NULL DEFAULT '[]', ambiguities_json TEXT NOT NULL DEFAULT '[]', key_metrics_json TEXT NOT NULL DEFAULT '[]',
            canonical_knowledge_refs_json TEXT NOT NULL DEFAULT '[]', source_evidence_refs_json TEXT NOT NULL DEFAULT '[]', related_projects_json TEXT NOT NULL DEFAULT '[]',
            unresolved_items_json TEXT NOT NULL DEFAULT '{}', synthesized_at TEXT NOT NULL, input_watermark TEXT NOT NULL,
            synthesis_revision INTEGER NOT NULL DEFAULT 1, evidence_fingerprint TEXT NOT NULL, evidence_sufficient INTEGER NOT NULL DEFAULT 0,
            quality_gate TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS project_handbook (
            project_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL, sections_json TEXT NOT NULL DEFAULT '{}',
            section_metadata_json TEXT NOT NULL DEFAULT '{}', generated_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
            input_watermark TEXT NOT NULL, evidence_fingerprint TEXT NOT NULL DEFAULT '', quality_gate TEXT NOT NULL,
            evidence_sufficient INTEGER NOT NULL DEFAULT 0, baseline_input_hash TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        """
    )


def _outcome_from_soul(project_id: str, soul: Mapping[str, Any], refs: list[Any], watermark: str, now: str) -> dict[str, Any]:
    identity, _ = project_description_from_soul(soul)
    goals = _soul_value(soul, "goals", [])
    scope = _soul_value(soul, "scope", {})
    outcomes = _soul_value(soul, "key_results_or_expected_outcomes", [])
    current = _soul_value(soul, "current_state", "INSUFFICIENT_EVIDENCE")
    next_items = _soul_value(soul, "next_step_or_open_items", [])
    risks = _soul_value(soul, "risks_blockers_ambiguities", "NOT_YET_APPLICABLE")
    return {
        "project_id": project_id, "schema_version": "paw-project-outcome/1", "current_status": "planning", "phase": _flatten_text(current),
        "executive_summary": identity, "objectives": goals if isinstance(goals, list) else [goals], "scope": scope if isinstance(scope, Mapping) else {"in_scope": scope},
        "latest_progress": [], "completed_milestones": [], "open_items": next_items if isinstance(next_items, list) else [next_items],
        "key_decisions": [], "current_effective_rules": [], "risks": risks if isinstance(risks, list) else [risks], "blockers": [],
        "ambiguities": [], "key_metrics": [], "canonical_knowledge_refs": [], "source_evidence_refs": refs,
        "related_projects": list(soul.get("related_existing_project_checks") or []), "unresolved_items": {"expected_outcomes": outcomes},
        "synthesized_at": now, "input_watermark": watermark, "synthesis_revision": 1, "evidence_fingerprint": _hash(refs),
        "evidence_sufficient": bool(refs), "quality_gate": "PASS" if refs else "PASS_EVIDENCE_INSUFFICIENT", "created_at": now, "updated_at": now,
    }


def bootstrap_project_artifacts(conn: sqlite3.Connection, *, project_id: str, payload: Mapping[str, Any], candidate_id: str | None, review_decision_id: str | None, approved_change_id: str, commit_record_id: str | None = None) -> dict[str, Any]:
    """Materialize the already-approved Soul into Explainer/Handbook/Outcome."""
    ensure_bootstrap_schema(conn)
    _create_artifact_tables(conn)
    soul = dict(payload.get("project_soul") or {})
    refs = list(payload.get("evidence_refs") or [])
    quality = validate_project_soul(soul, project_name=_text(payload.get("name")), project_kind="new")
    if quality["quality"] in {"INVALID", "NEEDS_COMPLETION"}:
        raise ValueError("project.create Soul gate failed: " + ",".join(quality["errors"] or quality["missing_or_partial_concepts"]))
    now = _now()
    watermark = "project-create:" + _hash({"approved_change_id": approved_change_id, "payload": payload})[:32]
    description, projection = project_description_from_soul(soul)
    explainer = {
        "schema_version": "paw-project-explainer/1", "one_sentence": _flatten_text(_soul_value(soul, "identity", "INSUFFICIENT_EVIDENCE")),
        "why_and_background": _soul_value(soul, "why_and_background", "INSUFFICIENT_EVIDENCE"), "goals": _soul_value(soul, "goals", "INSUFFICIENT_EVIDENCE"),
        "scope": _soul_value(soul, "scope", "INSUFFICIENT_EVIDENCE"), "current_state": _soul_value(soul, "current_state", "INSUFFICIENT_EVIDENCE"),
        "expected_outcomes": _soul_value(soul, "key_results_or_expected_outcomes", "NOT_YET_APPLICABLE"),
        "next_step": _soul_value(soul, "next_step_or_open_items", "INSUFFICIENT_EVIDENCE"), "risks_blockers_ambiguities": _soul_value(soul, "risks_blockers_ambiguities", "NOT_YET_APPLICABLE"),
        "evidence_refs": refs, "provenance": {"candidate_id": candidate_id, "review_decision_id": review_decision_id, "approved_change_id": approved_change_id, "derived_description": projection},
    }
    handbook = {"project_explainer": explainer, "project_overview": {"project_name": _text(payload.get("name")), "scope": explainer["scope"], "current_stage": explainer["current_state"]}, "future_plan": {"next_actions": explainer["next_step"]}, "evidence_and_knowledge": {"source_evidence_refs": refs}, "_meta": {"schema_version": SOUL_SCHEMA_VERSION, "quality": quality["quality"], "evidence_state": quality["evidence_state"], "initial_bootstrap": True, "history": "NOT_YET_APPLICABLE"}}
    outcome = _outcome_from_soul(project_id, soul, refs, watermark, now)
    conn.execute("INSERT INTO project_outcome (project_id,schema_version,current_status,phase,executive_summary,objectives_json,scope_json,latest_progress_json,completed_milestones_json,open_items_json,key_decisions_json,current_effective_rules_json,risks_json,blockers_json,ambiguities_json,key_metrics_json,canonical_knowledge_refs_json,source_evidence_refs_json,related_projects_json,unresolved_items_json,synthesized_at,input_watermark,synthesis_revision,evidence_fingerprint,evidence_sufficient,quality_gate,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET executive_summary=excluded.executive_summary,phase=excluded.phase,objectives_json=excluded.objectives_json,scope_json=excluded.scope_json,open_items_json=excluded.open_items_json,risks_json=excluded.risks_json,source_evidence_refs_json=excluded.source_evidence_refs_json,unresolved_items_json=excluded.unresolved_items_json,input_watermark=excluded.input_watermark,updated_at=excluded.updated_at", tuple(outcome[k] if not isinstance(outcome[k], (dict, list)) else _json(outcome[k]) for k in ("project_id","schema_version","current_status","phase","executive_summary","objectives","scope","latest_progress","completed_milestones","open_items","key_decisions","current_effective_rules","risks","blockers","ambiguities","key_metrics","canonical_knowledge_refs","source_evidence_refs","related_projects","unresolved_items","synthesized_at","input_watermark","synthesis_revision","evidence_fingerprint","evidence_sufficient","quality_gate","created_at","updated_at")))
    conn.execute("INSERT INTO project_handbook (project_id,schema_version,sections_json,section_metadata_json,generated_at,revision,input_watermark,evidence_fingerprint,quality_gate,evidence_sufficient,baseline_input_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET sections_json=excluded.sections_json,section_metadata_json=excluded.section_metadata_json,generated_at=excluded.generated_at,revision=excluded.revision,input_watermark=excluded.input_watermark,quality_gate=excluded.quality_gate,evidence_sufficient=excluded.evidence_sufficient,baseline_input_hash=excluded.baseline_input_hash,updated_at=excluded.updated_at", (project_id, "paw-project-handbook/1", _json(handbook), _json({"soul_schema_version": SOUL_SCHEMA_VERSION, "candidate_id": candidate_id, "review_decision_id": review_decision_id, "approved_change_id": approved_change_id, "commit_record_id": commit_record_id}), now, 1, watermark, _hash(refs), quality["quality"], int(bool(refs)), _hash({"project_id": project_id, "soul": soul}), now, now))
    conn.execute("INSERT INTO project_bootstrap_provenance (bootstrap_id,project_id,candidate_id,review_decision_id,approved_change_id,commit_record_id,soul_schema_version,bootstrap_schema_version,input_watermark,artifact_revision,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(approved_change_id) DO UPDATE SET commit_record_id=COALESCE(excluded.commit_record_id,project_bootstrap_provenance.commit_record_id),updated_at=excluded.updated_at", ("bootstrap:" + _hash({"approved_change_id": approved_change_id})[:28], project_id, candidate_id, review_decision_id, approved_change_id, commit_record_id, SOUL_SCHEMA_VERSION, BOOTSTRAP_SCHEMA_VERSION, watermark, 1, now, now))
    return {"project_id": project_id, "soul_quality": quality["quality"], "explainer": "COMMITTED", "handbook": "COMMITTED", "outcome": "COMMITTED", "description": description, "input_watermark": watermark, "formal_state_bypass": FORMAL_STATE_BYPASS}


def _approved_create_check(conn: sqlite3.Connection, change_id: str) -> sqlite3.Row:
    ac = conn.execute("SELECT * FROM approved_change WHERE change_id=?", (change_id,)).fetchone()
    if ac is None:
        raise ValueError("ApprovedChange not found")
    if _text(ac["target_type"]) != "project" or _text(ac["operation"]) != "create":
        raise ValueError("not a project.create ApprovedChange")
    rd = conn.execute("SELECT * FROM review_decision WHERE id=?", (ac["approval_ref"],)).fetchone()
    if rd is None or _text(rd["decision"]) not in {"approve", "edit_and_approve"}:
        raise ValueError("project.create requires approved ReviewDecision")
    if not _text(rd["candidate_id"]):
        raise ValueError("project.create requires Candidate lineage")
    candidate = conn.execute("SELECT * FROM candidate WHERE id=?", (rd["candidate_id"],)).fetchone()
    if candidate is None or _text(candidate["candidate_type"]) != PROJECT_CREATE_CANDIDATE_TYPE:
        raise ValueError("project.create requires project_update Candidate")
    payload = json.loads(ac["payload"])
    if payload.get("create_intent") is not True or not _text(payload.get("proposal_id")) or not _text(payload.get("name")):
        raise ValueError("project.create payload is not an explicit Soul proposal")
    if payload.get("bootstrap") is not True:
        raise ValueError("project.create bootstrap must be explicit")
    quality = validate_project_soul(payload.get("project_soul") or {}, project_name=_text(payload.get("name")), project_kind="new")
    if quality["quality"] in {"INVALID", "NEEDS_COMPLETION"}:
        raise ValueError("project.create Soul gate failed")
    return ac


def _execute_project_create(conn: sqlite3.Connection, payload: dict[str, Any]) -> str:
    project_id = _text(payload.get("id")) or str(uuid.uuid4())
    name = _text(payload.get("name"))
    if not name:
        raise ValueError("project.create requires name")
    existing = conn.execute("SELECT id,name FROM project WHERE id=?", (project_id,)).fetchone()
    if existing is not None:
        if _text(existing["name"]) != name:
            raise ValueError("project.create identity conflict")
        # A replay of a materially different approved proposal must fail closed.
        prior = conn.execute("SELECT approved_change_id FROM project_bootstrap_provenance WHERE project_id=?", (project_id,)).fetchone()
        if prior is None:
            raise ValueError("project.create duplicate identity without bootstrap provenance")
        return project_id
    duplicate = conn.execute("SELECT id,name FROM project WHERE lower(name)=lower(?) LIMIT 1", (name,)).fetchone()
    if duplicate is not None:
        raise ValueError("project.create duplicate canonical Project name")
    now = _now()
    columns = {row[1] for row in conn.execute("PRAGMA table_info(project)").fetchall()}
    values = {"id": project_id, "name": name, "description": project_description_from_soul(payload.get("project_soul") or {})[0], "status": payload.get("status") or "planning", "stage": payload.get("stage"), "next_action_summary": payload.get("next_action_summary"), "priority": payload.get("priority"), "due_date": payload.get("due_date"), "privacy_level": payload.get("privacy_level") or "normal", "created_at": now, "updated_at": now}
    insert = [key for key in values if key in columns]
    conn.execute(f"INSERT INTO project ({','.join(insert)}) VALUES ({','.join('?' for _ in insert)})", tuple(values[key] for key in insert))
    change_id, review_id, candidate_id = _ACTIVE_LINEAGE or ("pending-approved-change", None, None)
    bootstrap_project_artifacts(conn, project_id=project_id, payload=payload, candidate_id=candidate_id, review_decision_id=review_id, approved_change_id=change_id)
    return project_id


_INSTALLED = False
_ORIGINAL_COMMIT = None
_ACTIVE_LINEAGE: tuple[str, str, str] | None = None


def install_committer_integration() -> None:
    """Install project.create in the existing deterministic committer.

    This hook is intentionally explicit and keeps project.create out of the
    SAFE_AUTO_APPLY policy module and the routine OAuth tool surface.
    """
    global _INSTALLED, _ORIGINAL_COMMIT
    if _INSTALLED:
        return
    gateway = "/opt/paw-mcp-gateway"
    if gateway not in sys.path:
        sys.path.insert(0, gateway)
    import committer
    committer.PAYLOAD_SCHEMAS.setdefault("project", {})["create"] = ["name", "create_intent", "proposal_id", "project_soul", "bootstrap"]
    committer.EXECUTORS[("project", "create")] = _execute_project_create
    _ORIGINAL_COMMIT = committer.commit_approved_change

    def governed_commit(change_id: str) -> dict[str, Any]:
        global _ACTIVE_LINEAGE
        conn = sqlite3.connect(committer.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            routing = conn.execute("SELECT target_type,operation FROM approved_change WHERE change_id=?", (change_id,)).fetchone()
            if routing is None or (_text(routing["target_type"]) != "project" or _text(routing["operation"]) != "create"):
                return _ORIGINAL_COMMIT(change_id)
            ac = _approved_create_check(conn, change_id)
            review_id = _text(ac["approval_ref"])
            candidate_id = _text(conn.execute("SELECT candidate_id FROM review_decision WHERE id=?", (review_id,)).fetchone()[0])
            _ACTIVE_LINEAGE = (change_id, review_id, candidate_id)
            # The executor reads the frozen ApprovedChange payload; this local
            # validation is the approval boundary.  Payload is not rewritten.
            result = _ORIGINAL_COMMIT(change_id)
            if result.get("result") == "success":
                conn2 = sqlite3.connect(committer.DB_PATH)
                try:
                    ensure_bootstrap_schema(conn2)
                    commit_id = result.get("commit_id")
                    conn2.execute("UPDATE project_bootstrap_provenance SET candidate_id=(SELECT candidate_id FROM review_decision WHERE id=(SELECT approval_ref FROM approved_change WHERE change_id=?)), review_decision_id=(SELECT approval_ref FROM approved_change WHERE change_id=?), commit_record_id=?, updated_at=? WHERE approved_change_id=?", (change_id, change_id, commit_id, _now(), change_id))
                    conn2.execute("UPDATE project_handbook SET section_metadata_json=json_set(section_metadata_json,'$.commit_record_id',?) WHERE project_id=?", (commit_id, result.get("target_id")))
                    conn2.commit()
                finally:
                    conn2.close()
            return result
        finally:
            _ACTIVE_LINEAGE = None
            conn.close()
    committer.commit_approved_change = governed_commit
    _INSTALLED = True


def build_project_create_review_brief(proposal: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    """Build the authoritative zh-CN review brief for a new Project boundary."""
    name = _text(proposal.get("proposed_name") or payload.get("name"))
    soul = dict(proposal.get("project_soul") or payload.get("project_soul") or {})
    quality = dict(proposal.get("soul_validation") or validate_project_soul(soul, project_name=name, project_kind="new"))
    one_sentence = _flatten_text(_soul_value(soul, "identity", "")) or "待补充"
    why = _text(proposal.get("why_new_project_not_update")) or "需人工确认为何不并入现有 Project/Knowledge/Todo。"
    scope = _flatten_text(_soul_value(soul, "scope", "")) or "待明确"
    current_state = _flatten_text(_soul_value(soul, "current_state", "")) or "待明确"
    goals = _flatten_text(_soul_value(soul, "goals", "")) or current_state
    outcomes = _flatten_text(_soul_value(soul, "key_results_or_expected_outcomes", "")) or "待明确"
    evidence = list(proposal.get("evidence_refs") or payload.get("evidence_refs") or [])
    duplicates = list(proposal.get("related_existing_project_checks") or [])
    unknowns = list(quality.get("missing_or_partial_concepts") or [])
    duplicate_text = (
        "未发现明显同名或近似重复项目。"
        if not duplicates
        else "；".join(f"{item.get('project_name')}({item.get('match_type')})" for item in duplicates)
    )
    proposed_state = _json(
        {
            "target_type": "project",
            "operation": "create",
            "project_id": payload.get("id"),
            "name": name,
            "proposal_id": payload.get("proposal_id"),
            "bootstrap": True,
        }
    )
    return {
        "schema_version": "1",
        "title": f"新项目边界决策：{name}",
        "situation": (
            f"系统提出创建新的 canonical Project 边界「{name}」。\n"
            f"一句话定义：{one_sentence}\n"
            f"为何不并入现有 Project/Knowledge/Todo：{why}\n"
            f"初始范围：{scope}\n"
            f"当前目标/状态：{goals}\n"
            f"预期结果：{outcomes}\n"
            f"重复/现有项目检查：{duplicate_text}"
        ),
        "decision_question": (
            f"是否批准创建新的 Project「{name}」，而不是更新现有 Project、Knowledge 或 Todo？"
        ),
        "recommendation": (
            "这是 NEW PROJECT BOUNDARY 决策。系统不会自动创建 Project；"
            "请核对 Soul 语义、证据与重复检查结果后再决定。"
        ),
        "approval_effect": (
            f"批准后将通过 governed Committer 精确执行一次 project.create，"
            f"创建 canonical Project「{name}」并初始化 Explainer/Handbook/Outcome；"
            "拒绝或暂缓则不会有任何 Formal State 写入。"
        ),
        "review_mode": REVIEW_MODE_EXECUTION_READY,
        "affected_items": [
            {
                "label": f"project.create:{name}",
                "current_state": "尚无 canonical Project；执行载荷已冻结在 Candidate.execution 中，等待人工边界决策。",
                "proposed_state": proposed_state,
            }
        ],
        "evidence_summary": [_format_evidence_ref(ref) for ref in evidence if _format_evidence_ref(ref)]
        or ["证据不足或尚未关联；批准前请确认边界依据。"],
        "attention_points": [
            "这是新项目边界决策，不是普通 project.update。",
            f"主要未知/待补概念：{', '.join(unknowns) if unknowns else '无'}",
            f"重复检查：{duplicate_text}",
            "批准前：USER_DECISION_REQUIRED；未批准不会生成 ApprovedChange 或 Project。",
        ],
        "decision_options": [
            {"key": "approve", "label": "批准创建", "meaning": "创建新 canonical Project 并初始化 Soul 工件"},
            {"key": "reject", "label": "拒绝", "meaning": "不创建 Project，保留分析结论"},
            {"key": "defer", "label": "暂缓", "meaning": "维持 pending，待补充证据或边界说明"},
        ],
    }


def _validate_review_brief(brief: Mapping[str, Any]) -> None:
    import sys

    review_path = "/opt/personal-workspace/brain/review"
    if review_path not in sys.path:
        sys.path.insert(0, review_path)
    from review_brief_contract import validate_review_brief

    valid, error = validate_review_brief(dict(brief))
    if not valid:
        raise ValueError(f"REVIEW_BRIEF_INVALID: {error}")


def submit_project_create_candidate(conn: sqlite3.Connection, proposal: Mapping[str, Any]) -> dict[str, Any]:
    """Persist a Candidate only; no ReviewDecision or canonical Project write."""
    if proposal.get("disposition") != USER_DECISION_REQUIRED:
        return {"status": "HELD", "reason": proposal.get("review_reason"), "candidate_id": None, "formal_state_bypass": FORMAL_STATE_BYPASS}
    payload = project_create_candidate_payload(proposal)
    review_brief = build_project_create_review_brief(proposal, payload)
    _validate_review_brief(review_brief)
    candidate_id = "project-create-candidate:" + _hash({"proposal_id": payload["proposal_id"]})[:28]
    exists = conn.execute("SELECT id FROM candidate WHERE id=?", (candidate_id,)).fetchone()
    if exists is None:
        now = _now()
        run_id = "project-discovery-run:" + _hash({"proposal_id": payload["proposal_id"]})[:24]
        conn.execute("INSERT OR IGNORE INTO analysis_run(id,trigger_type,raw_text,raw_input_refs,analysis_model,started_at,completed_at,created_at) VALUES(?,?,?,?,?,?,?,?)", (run_id, "project_boundary_discovery", None, _json(payload.get("evidence_refs") or []), "gpt-project-discovery", now, now, now))
        conn.execute(
            "INSERT INTO candidate (id,analysis_run_id,candidate_type,source_refs,raw_excerpt,proposed_change,confidence,inference_level,recommended_action,analysis_model,status,created_at,review_brief_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                candidate_id,
                run_id,
                PROJECT_CREATE_CANDIDATE_TYPE,
                _json(payload.get("evidence_refs") or []),
                f"Create Project proposal: {payload['name']}",
                _json({"execution": {"target_type": "project", "operation": "create", "payload": payload}, "project_create_proposal": dict(proposal)}),
                "MEDIUM",
                "STRONG_INFERENCE",
                "USER_DECISION_REQUIRED",
                "gpt-project-discovery",
                "pending",
                now,
                _json(review_brief),
            ),
        )
        conn.commit()
    return {
        "status": USER_DECISION_REQUIRED,
        "candidate_id": candidate_id,
        "payload": payload,
        "review_brief": review_brief,
        "formal_state_bypass": FORMAL_STATE_BYPASS,
    }


def read_project_soul_context(db_path: str, project_id: str) -> dict[str, Any]:
    """Additive Soul readiness projection for project context APIs."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        model = project_soul_read_model(conn, project_id)
    finally:
        conn.close()
    if not model.get("found"):
        return {
            "schema_version": SOUL_SCHEMA_VERSION,
            "soul_quality": "INVALID",
            "missing_or_partial_concepts": [],
            "bootstrap_state": "NOT_FOUND",
            "formal_state_bypass": FORMAL_STATE_BYPASS,
        }
    return {
        "schema_version": model.get("soul_schema_version") or SOUL_SCHEMA_VERSION,
        "soul_quality": model.get("soul_quality"),
        "missing_or_partial_concepts": list(model.get("missing_or_partial_concepts") or []),
        "bootstrap_state": model.get("bootstrap_state"),
        "formal_state_bypass": FORMAL_STATE_BYPASS,
    }


def project_soul_read_model(conn: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    """Minimal additive readiness fields for existing Project context APIs."""
    row = conn.execute("SELECT name,status FROM project WHERE id=?", (project_id,)).fetchone()
    if row is None:
        return {"found": False, "project_id": project_id, "formal_state_bypass": FORMAL_STATE_BYPASS}
    soul: dict[str, Any] = {}
    bootstrap_state = "NOT_BOOTSTRAPPED"
    try:
        handbook = conn.execute("SELECT sections_json FROM project_handbook WHERE project_id=?", (project_id,)).fetchone()
        if handbook:
            sections = json.loads(handbook[0] or "{}")
            soul = dict(sections.get("project_explainer") or {})
            bootstrap_state = "BOOTSTRAPPED"
    except (sqlite3.OperationalError, ValueError):
        pass
    project_name = row["name"] if isinstance(row, sqlite3.Row) else row[0]
    quality = validate_project_soul(soul, project_name=project_name, project_kind="mature")
    return {"found": True, "soul_schema_version": SOUL_SCHEMA_VERSION, "soul_quality": quality["quality"], "missing_or_partial_concepts": quality["missing_or_partial_concepts"], "bootstrap_state": bootstrap_state, "formal_state_bypass": FORMAL_STATE_BYPASS}


__all__ = [
    "BOOTSTRAP_SCHEMA_VERSION", "CORE_CONCEPTS", "FORMAL_STATE_BYPASS", "PROJECT_CREATE_SAFE_AUTO_APPLY", "PROJECT_CREATE_CANDIDATE_TYPE", "REVIEW_MODE_EXECUTION_READY", "SOUL_SCHEMA_VERSION",
    "bootstrap_project_artifacts", "build_project_create_review_brief", "discover_project_create_proposal", "ensure_bootstrap_schema", "find_existing_project_matches", "install_committer_integration", "merge_project_soul", "project_create_candidate_payload", "project_description_from_soul", "project_soul_read_model", "read_project_soul_context", "submit_project_create_candidate", "validate_project_soul",
]
