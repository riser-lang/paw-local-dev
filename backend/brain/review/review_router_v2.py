"""PAW Review Router V2 — ReviewRoutingDecision.

Orchestrates: structured signals -> deterministic policy -> target routing ->
human-readable presentation.  READ-ONLY: this module never writes, never
mutates a candidate, and never creates a ReviewDecision.

The actual governed execution (ReviewDecision -> ApprovedChange -> Committer) is
a separate, later step owned by the existing governance chain.  This module only
decides WHERE an item should go.
"""

from __future__ import annotations

from pathlib import Path
import os


import json
import sqlite3
from typing import Any, Optional

from . import review_presentation
from . import review_routing_policy as P
from . import target_router

PRODUCTION_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))

#: signals that make an item a system-side concern rather than a user decision
_SYSTEM_DIAGNOSTIC_MARKERS = (
    "系统诊断", "diagnostic", "internal repair", "内部修复",
    "placeholder", "验收", "acceptance test", "旧测试",
)

#: an empty knowledge page (placeholder row with no content and no source)
_EMPTY_PLACEHOLDER_MARKERS = (
    "当前内容为空", "内容为空", "占位记录", "没有任何 Source 关联",
    "no content", "empty page",
)


def _json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value.strip().startswith(("{", "[")):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return None


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def connect_readonly(db_path: str = PRODUCTION_DB) -> sqlite3.Connection:
    conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    # The production DB is opened read-only.  Keep bounded projection sorts
    # in memory so SQLite never attempts to create a temp file beside it.
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def _target_exists(conn: Optional[sqlite3.Connection], table: str, ident: str) -> bool:
    if conn is None or not ident:
        return False
    if table not in ("project", "knowledge", "todo", "data"):
        return False
    try:
        return conn.execute("SELECT 1 FROM %s WHERE id = ?" % table,
                            (ident,)).fetchone() is not None
    except sqlite3.Error:
        return False


def _count_target_matches(conn: Optional[sqlite3.Connection], table: str,
                          name: str) -> int:
    if conn is None or not name or table not in ("project", "knowledge"):
        return 0
    try:
        return conn.execute("SELECT COUNT(*) FROM %s WHERE name = ?" % table,
                            (name,)).fetchone()[0]
    except sqlite3.Error:
        return 0


def derive_signals(row: dict[str, Any], conn: Optional[sqlite3.Connection] = None,
                   *, closed_resolution: bool = False,
                   presentation: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Derive every structured signal the policy needs, from real fields.

    ``presentation`` is the shipped presentation service output when available;
    it is the authoritative source for whether a human can read the item.
    """
    proposed = _json(row.get("proposed_change")) or {}
    display = _json(row.get("review_display_json")) or {}
    brief = _json(row.get("review_brief_json")) or {}
    refs = _json(row.get("source_refs")) or []
    if isinstance(refs, dict):
        refs = refs.get("refs") or []
    if not isinstance(refs, list):
        refs = []

    internal_evidence = _json(row.get("review_internal_evidence_json")) or {}
    evidence_refs = refs or (internal_evidence.get("evidence_ids") or [])

    ctype = _s(row.get("candidate_type"))
    why_unresolved = _s(proposed.get("why_unresolved"))

    # --- renderability: can a business user actually read this item? --------
    pres = presentation or {}
    pres_title = _s(pres.get("display_title"))
    pres_situation = _s(pres.get("situation"))
    pres_question = _s(pres.get("decision_question"))
    stored_title = _s(display.get("title"))
    stored_situation = _s(display.get("situation"))
    if pres or pres_title or pres_situation:
        renderable = P.is_renderable(pres_title, pres_situation, pres_question)
    else:
        renderable = P.is_renderable(stored_title, stored_situation,
                                     _s(brief.get("title")),
                                     _s(brief.get("situation")))

    blob = " ".join([
        _s(proposed.get("update_summary")), _s(row.get("raw_excerpt")),
        stored_situation, _s(brief.get("situation")), stored_title,
        pres_title, pres_situation,
    ])
    low_blob = blob.casefold()

    markers, narrative = P.split_markers({"why_unresolved": why_unresolved})
    marker_blob = ";".join(markers)
    has_system_deficiency = any(m in marker_blob for m in P.SYSTEM_DEFICIENCY_MARKERS)
    has_user_decision = any(m in marker_blob for m in P.USER_DECISION_MARKERS)
    has_conflict = any(m in marker_blob for m in P.CONFLICT_MARKERS)
    system_deficiency_only = bool(markers) and has_system_deficiency \
        and not has_user_decision and not has_conflict and not narrative

    project_id = _s(proposed.get("project_id") or row.get("project_id"))
    target_table = _s(proposed.get("target_table"))
    if not target_table:
        if project_id:
            target_table = "project"
    target_ident = _s(proposed.get("target_id") or project_id)

    target_exists = _target_exists(conn, target_table, target_ident)
    name_hint = _s(proposed.get("project_name") or proposed.get("target_name"))
    matches = _count_target_matches(conn, target_table, name_hint) if name_hint else 0
    # "no target" is NOT "ambiguous target": only flag non-uniqueness when a
    # target actually exists and resolves to more than one row.
    target_ambiguous = bool(target_ident) and (
        matches > 1 or bool(proposed.get("target_ambiguous")))
    target_unique = (bool(target_ident) and (target_exists or conn is None)
                     and matches <= 1)

    system_diagnostic_only = any(m in low_blob for m in _SYSTEM_DIAGNOSTIC_MARKERS)
    evidence_complete = bool(evidence_refs) and bool(brief or display)
    empty_placeholder = (ctype == "ambiguity"
                         and any(m.casefold() in low_blob
                                 for m in _EMPTY_PLACEHOLDER_MARKERS))

    operation = _s(proposed.get("operation"))
    if not operation:
        if ctype == "project_update":
            operation = "update"
        elif ctype == "knowledge":
            operation = "update"
        elif ctype == "todo":
            operation = "create"

    idempotent_link = ("关联" in blob and bool(project_id))

    signals = {
        "candidate_id": row.get("id"),
        "candidate_type": ctype,
        "confidence": row.get("confidence"),
        "inference_level": row.get("inference_level"),
        "recommended_action": row.get("recommended_action"),
        "created_at": row.get("created_at"),
        "analysis_model": row.get("analysis_model"),
        "raw_excerpt": row.get("raw_excerpt"),
        "proposed_change": proposed,
        "proposed_change_text": json.dumps(proposed, ensure_ascii=False)[:2000],
        "update_summary": _s(proposed.get("update_summary")),
        "review_brief_text": json.dumps(brief, ensure_ascii=False)[:2000],
        "review_brief_json": row.get("review_brief_json"),
        "review_display_json": row.get("review_display_json"),
        "title": stored_title or _s(brief.get("title")),
        "operation": operation,
        "declared_target": _s(proposed.get("target_type")),
        "evidence_refs": evidence_refs,
        "evidence_labels": internal_evidence.get("labels") or [],
        "project_id": project_id or None,
        "target_table": target_table,
        "target_exists": target_exists,
        "target_unique": target_unique,
        "closed_resolution": closed_resolution,
        "duplicate_of": row.get("duplicate_of"),
        "superseded_by": row.get("superseded_by"),
        "target_invalid": bool(target_table) and not target_exists and conn is not None,
        "internal_repair_only": system_diagnostic_only,
        "system_diagnostic_only": system_diagnostic_only,
        "legacy_mechanical": bool(proposed.get("mechanical")) or bool(proposed.get("legacy")),
        "business_value": bool(_s(row.get("raw_excerpt")).strip())
                          and ctype in ("conflict", "ambiguity", "project_update",
                                        "knowledge", "fact"),
        # --- V2 renderability / marker signals ---
        "presentation_renderable": renderable,
        "why_unresolved": why_unresolved,
        "why_unresolved_narrative": narrative,
        "system_deficiency_only": system_deficiency_only,
        "empty_placeholder": empty_placeholder,
        # --- hard-block signals ---
        "semantic_conflict": ctype == "conflict",
        "authority_conflict": bool(internal_evidence.get("authority_conflict")),
        "multiple_versions_unresolved": "canonical_topic_version_conflict" in marker_blob,
        "relation_meaning_uncertain": False,
        "business_authority_change": False,
        "destructive_change": operation.casefold() in P.FORBIDDEN_OPERATIONS,
        "requires_human_semantic_judgment": ctype in ("conflict", "ambiguity", "question"),
        "fanout_scope": P.FANOUT_LOCAL if idempotent_link else P.FANOUT_BOUNDED,
        "requires_user_supplied_fact": ctype == "ambiguity" and not evidence_complete,
        "evidence_gap_high_value": bool(not evidence_complete
                                        and _s(row.get("raw_excerpt")).strip()),
        "project_boundary_change": False,
        "target_not_unique": target_ambiguous,
        "model_must_guess_fields": not bool(proposed),
        "missing_required_fields": [],
        "deterministic_envelope": bool(proposed) and bool(operation),
        "evidence_complete": evidence_complete,
        # --- auto-flow qualifiers ---
        "idempotent_unique_target_link": idempotent_link and target_exists,
        "user_approved_successor": False,
        "deterministic_parser": _s(row.get("analysis_model")).startswith("deterministic"),
        "mechanical_execution": False,
        "touches_source_identity": "source" in ctype.casefold(),
        "deterministic_identity": None,
        "identity_key_kind": "",
    }
    return signals


def route_candidate(row: dict[str, Any], conn: Optional[sqlite3.Connection] = None,
                    *, closed_resolution: bool = False,
                    presentation: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Return the full ReviewRoutingDecision for one candidate row."""
    ctx = derive_signals(row, conn, closed_resolution=closed_resolution,
                         presentation=presentation)
    decision = P.route(ctx)
    target = target_router.resolve_target(ctx)

    out = {
        "candidate_id": ctx["candidate_id"],
        "candidate_type": ctx["candidate_type"],
        "route": decision["route"],
        "reason_codes": decision["reason_codes"],
        "system_remediation": decision.get("system_remediation", False),
        "target_type": target["target_type"],
        "declared_target": target["declared_target"],
        "target_mismatch": target["target_mismatch"],
        "target_why": target["why"],
        "risk_class": decision["risk_class"],
        "matched_rule": decision["matched_rule"],
        "requires_human_semantic_judgment": ctx["requires_human_semantic_judgment"],
        "deterministic_identity": ctx["deterministic_identity"],
        "evidence_complete": ctx["evidence_complete"],
        "target_unique": ctx["target_unique"],
        "semantic_conflict": ctx["semantic_conflict"],
        "business_authority_change": ctx["business_authority_change"],
        "destructive_change": ctx["destructive_change"],
        "fanout_scope": ctx["fanout_scope"],
        "presentation_renderable": ctx["presentation_renderable"],
        "recommended_review_decision": decision["recommended_review_decision"],
        "presentation": None,
    }
    if out["route"] == P.USER_REVIEW_REQUIRED:
        out["presentation"] = review_presentation.build_presentation(ctx, out)
    return out


def route_items(rows: list[dict[str, Any]], conn: Optional[sqlite3.Connection] = None,
                closed_ids: Optional[set[str]] = None,
                presentations: Optional[dict[str, dict]] = None) -> list[dict[str, Any]]:
    closed_ids = closed_ids or set()
    presentations = presentations or {}
    return [route_candidate(r, conn, closed_resolution=r.get("id") in closed_ids,
                            presentation=presentations.get(r.get("id")))
            for r in rows]
