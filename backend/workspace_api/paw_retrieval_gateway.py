"""PAW v1.3D — Retrieval Gateway.

The Gateway is the ONE typed read entry for standard business queries:

    typed request (paw.query-contract.v13d/1)
        -> EXISTING PAW capability
        -> typed response (paw.retrieval-gateway.v13d/1)

What the Gateway is NOT
-----------------------
* NOT a database. It never opens a write connection.
* NOT a fact cache, NOT Memory, NOT a second search engine.
* NOT an owner of Current. It never decides which revision is current and
  never runs ``closest revision`` heuristics.
* NOT a place where a knowledge locator is invented.

Ownership (unchanged, by delegation)
------------------------------------
* Current                -> ``paw_temporal_authority.TemporalAuthorityResolver``
* Knowledge recall       -> ``workspace_api.retrieval_contract``
                            (``paw.retrieval-contract.v13/1``)
* Formal metric          -> ``paw_temporal_authority.query_formal_metric``
* Evidence               -> ``paw_retrieval_core.get_evidence``
* Business changes       -> ``paw_retrieval_core.get_recent_changes``
* Project serving chain  -> ``workspace_api.paw_projection_v13c``

This module adds composition and a UNIFIED RESPONSE CONTRACT, nothing else.
``DIRECT_SQL_CURRENT_GUESS = 0``, ``NEW_RETRIEVAL_IMPLEMENTATION = 0``,
``MODEL_CALLS = 0``, read-only.

Response contract
-----------------
Every response carries, at minimum::

    result_type, result_state, authority_state, canonical, temporal_state,
    source_refs, evidence_refs, project_refs,
    valid_from, valid_to, known_from, known_to,
    unresolved_reason, provisional_reason, input_watermark

States are never flattened into "nothing found": ``NOT_FOUND`` /
``UNRESOLVED`` / ``TEMPORAL_HISTORY_INCOMPLETE`` / ``EVIDENCE_NOT_AVAILABLE`` /
``RETRIEVAL_STALE`` / ``NON_CANONICAL_ONLY`` / ``AMBIGUOUS_QUERY`` /
``INVALID_ARGS`` are preserved verbatim.

Provider neutrality
-------------------
No vendor SDK, no vendor response object, no vendor chat identity, no
vendor-side durable state, no vendor model name, no vendor-specific tool-call
payload. The Gateway communicates only in PAW types and PAW result states, and
its durable state is the Formal State in ``workspace.db``, addressed by entity
ids — never an agent-side artefact.
"""

from __future__ import annotations

from pathlib import Path


import os
import sys
from typing import Any, Callable, Mapping

# ---------------------------------------------------------------------------
# Path contracts (same explicit discipline as workspace_api.retrieval_contract)
# ---------------------------------------------------------------------------
_WS_ROOT = str(Path(__file__).resolve().parents[1 if "backend" in str(Path(__file__).parent.name) else 2])
if _WS_ROOT not in sys.path:
    sys.path.append(_WS_ROOT)

_RUNTIME_ROOT = (
    os.environ.get("PAW_RUNTIME_MODULE_ROOT") or str(Path(__file__).resolve().parents[1 if Path(__file__).parent.name == "backend" else 2] / "runtime")
)
if _RUNTIME_ROOT not in sys.path:
    sys.path.append(_RUNTIME_ROOT)

import paw_retrieval_core as rc  # noqa: E402
import paw_temporal_authority as ta  # noqa: E402

from . import paw_query_contract as qc  # noqa: E402
from . import repository as repo  # noqa: E402
from .retrieval_contract import knowledge_search  # noqa: E402
from .paw_projection_v13c import (  # noqa: E402
    build_knowledge_detail_projection,
    build_project_operating_projection,
)

SCHEMA = "paw.retrieval-gateway.v13d/1"

DEFAULT_DB = os.environ.get(
    "PAW_WORKSPACE_DB", os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db")))

# Business change target types the Formal State commit chain can contain.
# Anything outside this set is NOT a business change and is dropped with a
# disclosure rather than returned as "recent activity". Production today
# contains only {data, knowledge, project, todo}.
BUSINESS_TARGET_TYPES = frozenset({"data", "knowledge", "project", "todo"})
NON_BUSINESS_TARGET_TYPES = frozenset({
    "analysis_run", "candidate", "capture", "review_decision",
    "patrol_run", "model_run", "debug_event", "service",
})

# Evidence rejection mapping. The retrieval core stays the authority on WHAT
# the evidence is; the Gateway only assigns the typed result state. A rejection
# is never remapped into a success.
_EVIDENCE_STATE_BY_REASON = {
    "invalid_source_version_id": qc.STATE_INVALID_ARGS,
    "locator_required": qc.STATE_INVALID_ARGS,
    "malformed_or_unknown_locator": qc.STATE_INVALID_ARGS,
    "unknown_root": qc.STATE_INVALID_ARGS,
    "index_out_of_range": qc.STATE_NOT_FOUND,
    "identity_mismatch": qc.STATE_UNRESOLVED,
}


# ---------------------------------------------------------------------------
# Unified response envelope
# ---------------------------------------------------------------------------
def _envelope(
    result_type: str,
    result_state: str,
    *,
    authority_state: str | None = None,
    canonical: bool = False,
    temporal_state: str | None = None,
    source_refs: Any = None,
    evidence_refs: Any = None,
    project_refs: Any = None,
    valid_from: str | None = None,
    valid_to: str | None = None,
    known_from: str | None = None,
    known_to: str | None = None,
    unresolved_reason: str | None = None,
    provisional_reason: str | None = None,
    input_watermark: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """The ONE unified response shape. Extra keys never replace core keys."""
    out: dict[str, Any] = {
        "schema": SCHEMA,
        "result_type": result_type,
        "result_state": result_state,
        "authority_state": authority_state,
        "canonical": bool(canonical),
        "temporal_state": temporal_state,
        "source_refs": list(source_refs or []),
        "evidence_refs": list(evidence_refs or []),
        "project_refs": list(project_refs or []),
        "valid_from": valid_from,
        "valid_to": valid_to,
        "known_from": known_from,
        "known_to": known_to,
        "unresolved_reason": unresolved_reason,
        "provisional_reason": provisional_reason,
        "input_watermark": input_watermark,
        "deterministic": True,
        "model_call": False,
    }
    out.update(extra)
    return out


def _dedup(values: Any) -> list[str]:
    seen: list[str] = []
    for v in values or []:
        s = str(v)
        if s and s not in seen:
            seen.append(s)
    return seen


# ---------------------------------------------------------------------------
# Project resolution (name -> id) via the EXISTING project read model
# ---------------------------------------------------------------------------
def _resolve_project_id(request: qc.QueryRequest) -> tuple[str | None, str | None]:
    """Return (project_id, failure_reason). Never guesses between candidates."""
    if request.project_id:
        return request.project_id, None
    name = (request.project_name or "").strip()
    if not name:
        return None, "project_identifier_required"
    try:
        rows = repo.list_projects(500)
    except Exception:
        return None, "project_read_model_unavailable"
    exact = [r for r in rows if (r.get("name") or "").strip() == name]
    if len(exact) == 1:
        return str(exact[0].get("id")), None
    if len(exact) > 1:
        return None, "multiple_projects_share_name"
    partial = [r for r in rows if name and name in (r.get("name") or "")]
    if len(partial) == 1:
        return str(partial[0].get("id")), None
    if len(partial) > 1:
        return None, "ambiguous_project_name"
    return None, "project_not_found"


def _project_authority_state(status: Any) -> str | None:
    """Derived project serving state.

    Projects are not an authority-resolvable entity type in production, so
    there is no TemporalAuthorityResolver answer to reuse. This mirrors the
    EXISTING mapping convention already used for non-authority entities in
    ``paw_retrieval_core._knowledge_doc``: current -> EFFECTIVE_CURRENT,
    deprecated/archived -> ARCHIVED, otherwise ``status.upper()``. It is a
    disclosure, not a Current claim, and is never used for knowledge / metric /
    source_version, whose Current always comes from the authority layer.
    """
    s = str(status).strip().lower() if status is not None else ""
    if not s:
        return None
    if s == "active":
        return "EFFECTIVE_CURRENT"
    if s in ("archived", "deprecated"):
        return "ARCHIVED"
    return s.upper()


# ---------------------------------------------------------------------------
# 1. PROJECT_CURRENT
# ---------------------------------------------------------------------------
def _h_project_current(request: qc.QueryRequest, path: str) -> dict[str, Any]:
    project_id, reason = _resolve_project_id(request)
    if project_id is None:
        state = (qc.STATE_AMBIGUOUS_QUERY
                 if reason in ("ambiguous_project_name",
                               "multiple_projects_share_name")
                 else qc.STATE_NOT_FOUND)
        return _envelope("project_current", state, unresolved_reason=reason)

    projection = build_project_operating_projection(project_id)
    if projection is None:
        return _envelope("project_current", qc.STATE_NOT_FOUND,
                         project_refs=[project_id],
                         unresolved_reason="project_serving_chain_empty")

    project = projection.get("project") or {}
    rules = projection.get("current_rules") or []
    history = projection.get("history") or {}

    # The serving chain already discloses per-rule evidence_state; it exposes
    # SourceVersion ids but no structural locators, so evidence_refs stays
    # empty rather than being filled with a state string.
    source_refs: list[str] = []
    for r in rules:
        source_refs.extend(r.get("source_version_ids") or [])

    temporal_state = (qc.STATE_TEMPORAL_HISTORY_INCOMPLETE
                      if history.get("valid_time_disclosure") else None)

    return _envelope(
        "project_current", qc.STATE_OK,
        authority_state=_project_authority_state(project.get("status")),
        canonical=True,
        temporal_state=temporal_state,
        source_refs=_dedup(source_refs),
        evidence_refs=[],
        project_refs=[project_id],
        known_from=project.get("updated_at"),
        unresolved_reason=None,
        provisional_reason=history.get("valid_time_disclosure"),
        project=project,
        current_rules=rules,
        current_rule_count=len(rules),
        formal_metrics=projection.get("formal_metrics") or [],
        recent_changes=projection.get("recent_changes") or [],
        open_items=projection.get("open_items") or [],
        recent_decisions=projection.get("recent_decisions") or [],
        related_projects=projection.get("related_projects") or [],
        history=history,
        serving_chain="paw.projection.v13c/1:build_project_operating_projection",
        raw_source_rebuild=False,
    )


# ---------------------------------------------------------------------------
# 2. KNOWLEDGE_CURRENT  (the ONE retrieval contract, unchanged)
# ---------------------------------------------------------------------------
def _knowledge_state(result: Mapping[str, Any]) -> tuple[str, str | None]:
    count = int(result.get("count") or 0)
    if count:
        admitted = int(result.get("admitted_total") or 0)
        return (qc.STATE_OK if admitted else qc.STATE_NON_CANONICAL_ONLY), None
    if int(result.get("stale_candidate_count") or 0):
        return qc.STATE_RETRIEVAL_STALE, "RETRIEVAL_STALE"
    if int(result.get("incomplete_candidate_count") or 0):
        return (qc.STATE_TEMPORAL_HISTORY_INCOMPLETE,
                qc.STATE_TEMPORAL_HISTORY_INCOMPLETE)
    return qc.STATE_NOT_FOUND, str(result.get("retrieval_query_mode") or "no_match")


def _h_knowledge_current(request: qc.QueryRequest, path: str) -> dict[str, Any]:
    result = knowledge_search(
        request.query or "",
        limit=request.limit,
        include_provisional=request.include_provisional,
        db_path=path,
    )
    state, reason = _knowledge_state(result)
    items = list(result.get("items") or [])
    canonical_items = [i for i in items if i.get("canonical")]

    authority_state = None
    if canonical_items:
        authority_state = (canonical_items[0].get("authority_state")
                           or "EFFECTIVE_CURRENT")
    elif items:
        authority_state = "UNRESOLVED"

    # The retrieval contract exposes authority/lineage/evidence STATE but not
    # the underlying refs. They come from the existing canonical safety gate
    # (the same one the Web project page uses) -- no second retrieval.
    source_refs: list[str] = []
    evidence_refs: list[str] = []
    for item in items:
        kid = item.get("id")
        if not kid:
            continue
        try:
            safety = rc.evaluate_knowledge_retrieval_safety(kid, db_path=path)
        except Exception:
            if os.environ.get("PAW_GATEWAY_STRICT") == "1":
                raise
            continue
        sv = list(safety.get("source_version_ids") or [])
        source_refs.extend(sv)
        evidence_refs.extend(safety.get("evidence_refs") or sv)
        item["source_version_ids"] = sv

    return _envelope(
        "knowledge_current", state,
        authority_state=authority_state,
        canonical=bool(canonical_items),
        temporal_state=(qc.STATE_TEMPORAL_HISTORY_INCOMPLETE
                        if int(result.get("incomplete_candidate_count") or 0)
                        else None),
        source_refs=_dedup(source_refs),
        evidence_refs=_dedup(evidence_refs),
        project_refs=[request.project_id] if request.project_id else [],
        unresolved_reason=reason if state != qc.STATE_OK else None,
        provisional_reason=(None if canonical_items
                            else "NOT_IN_CANONICAL_INDEX" if items else None),
        items=items,
        count=result.get("count", 0),
        matched_total=result.get("matched_total"),
        admitted_total=result.get("admitted_total"),
        excluded_provisional_count=result.get("excluded_provisional_count"),
        incomplete_candidate_count=result.get("incomplete_candidate_count", 0),
        untraceable_candidate_count=result.get("untraceable_candidate_count", 0),
        stale_candidate_count=result.get("stale_candidate_count", 0),
        retrieval_query_mode=result.get("retrieval_query_mode"),
        retrieval_contract=result.get("retrieval_contract"),
        authority_scope=result.get("authority_scope"),
        refs_source="paw_retrieval_core.evaluate_knowledge_retrieval_safety",
    )


# ---------------------------------------------------------------------------
# 3. TEMPORAL_QUERY  (as-of; fails closed without explicit valid time)
# ---------------------------------------------------------------------------
def _h_temporal_query(request: qc.QueryRequest, path: str) -> dict[str, Any]:
    result = rc.search_as_of(
        request.query or "",
        request.as_of or "",
        limit=request.limit,
        db_path=path,
    )
    items = result.get("items") or []
    if items:
        state = qc.STATE_OK
    elif int(result.get("incomplete_candidate_count") or 0):
        state = qc.STATE_TEMPORAL_HISTORY_INCOMPLETE
    else:
        state = qc.STATE_NOT_FOUND

    authority_state = items[0].get("authority_state") if items else None
    return _envelope(
        "temporal_query", state,
        authority_state=authority_state,
        canonical=any(i.get("canonical") for i in items),
        temporal_state=(f"VALID_AT_{request.as_of}"
                        if items else qc.STATE_TEMPORAL_HISTORY_INCOMPLETE),
        evidence_refs=_dedup(
            e for i in items for e in (i.get("evidence_refs") or [])),
        unresolved_reason=(None if items else
                           str(result.get("temporal_warning")
                               or "no_revision_effective_at_as_of")),
        provisional_reason=(None if items else
                            "NO_EXPLICIT_VALID_TIME_METADATA"),
        as_of=request.as_of,
        items=items,
        count=result.get("count", 0),
        incomplete_candidate_count=result.get(
            "incomplete_candidate_count", 0),
        temporal_warning=result.get("temporal_warning"),
        closest_revision_guess=False,
    )


# ---------------------------------------------------------------------------
# 4. ENTITY_AUTHORITY  (resolve_current passthrough)
# ---------------------------------------------------------------------------
_AUTHORITY_RESULT_STATE = {
    "EFFECTIVE_CURRENT": qc.STATE_OK,
    "HISTORICAL": qc.STATE_OK,
    "ARCHIVED": qc.STATE_OK,
    "SUPERSEDED": qc.STATE_OK,
    "FUTURE_EFFECTIVE": qc.STATE_OK,
    "UNRESOLVED": qc.STATE_UNRESOLVED,
    "TEMPORAL_HISTORY_INCOMPLETE": qc.STATE_TEMPORAL_HISTORY_INCOMPLETE,
}


def _h_entity_authority(request: qc.QueryRequest, path: str) -> dict[str, Any]:
    resolver = ta.TemporalAuthorityResolver(path)
    auth = resolver.resolve_current(
        entity_type=request.entity_type or "",
        entity_id=request.entity_id or "",
        as_of=request.as_of,
        known_as_of=request.known_as_of,
    )
    authority_state = auth.get("authority_state")
    state = _AUTHORITY_RESULT_STATE.get(authority_state, qc.STATE_UNRESOLVED)
    # When valid time is not recorded, the authority layer still reports which
    # revision is the confirmed pointer -- useful for audit, but it is NOT an
    # answer about the requested date.  Surfacing it as a top-level
    # ``resolved_ref`` invites an Agent to read "the revision at that date",
    # so it is withheld here and disclosed only inside the full authority
    # payload, with the reason stated explicitly.
    withhold_ref = authority_state in ("TEMPORAL_HISTORY_INCOMPLETE",
                                       "UNRESOLVED")
    return _envelope(
        "entity_authority", state,
        authority_state=authority_state,
        canonical=(authority_state == "EFFECTIVE_CURRENT"),
        temporal_state=authority_state,
        valid_from=auth.get("valid_from"),
        valid_to=auth.get("valid_to"),
        known_from=auth.get("known_from"),
        known_to=auth.get("known_to"),
        unresolved_reason=auth.get("unresolved_reason"),
        provisional_reason=(None if authority_state == "EFFECTIVE_CURRENT"
                            else authority_state),
        authority=auth,
        resolution_basis=auth.get("resolution_basis"),
        resolved_ref=None if withhold_ref else auth.get("resolved_ref"),
        resolved_ref_withheld=withhold_ref,
        resolved_ref_withheld_reason=(
            "valid_time_not_recorded_current_pointer_is_not_an_as_of_answer"
            if withhold_ref else None),
        supersedes=auth.get("supersedes"),
        superseded_by=auth.get("superseded_by"),
        decision_ref=auth.get("decision_ref"),
        evidence_fingerprint=auth.get("evidence_fingerprint"),
    )


# ---------------------------------------------------------------------------
# 5. TIMELINE  (known-time composition; valid time only when explicit)
# ---------------------------------------------------------------------------
def _h_timeline(request: qc.QueryRequest, path: str) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    project_refs: list[str] = []
    unresolved: str | None = None

    if request.entity_type and request.entity_id:
        etype = request.entity_type
        if etype == "knowledge":
            proj = build_knowledge_detail_projection(request.entity_id)
            if proj is None:
                return _envelope("timeline", qc.STATE_NOT_FOUND,
                                 unresolved_reason="knowledge_not_found")
            vt = proj.get("valid_time") or {}
            for rev in proj.get("revisions") or []:
                if not request.include_historical and not rev.get("is_current"):
                    continue
                entries.append({
                    "entry_type": "knowledge_revision",
                    "entity_type": "knowledge",
                    "entity_id": request.entity_id,
                    "label": f"revision {rev.get('revision_no')}",
                    "known_from": rev.get("created_at"),
                    "valid_from": vt.get("valid_from"),
                    "valid_to": vt.get("valid_to"),
                    "temporal_state": ("EXPLICIT_VALID_TIME" if vt
                                       else qc.STATE_TEMPORAL_HISTORY_INCOMPLETE),
                    "ref": f"knowledge_revision:{rev.get('revision_id')}",
                })
            if not vt:
                unresolved = qc.STATE_TEMPORAL_HISTORY_INCOMPLETE
        else:
            # metric / source_version have no revision-history reader that
            # records business valid time. Refuse rather than invent one.
            auth = ta.TemporalAuthorityResolver(path).resolve_current(
                entity_type=etype, entity_id=request.entity_id)
            return _envelope(
                "timeline", qc.STATE_TEMPORAL_HISTORY_INCOMPLETE,
                authority_state=auth.get("authority_state"),
                temporal_state=qc.STATE_TEMPORAL_HISTORY_INCOMPLETE,
                provisional_reason="NO_TIMELINE_READER",
                unresolved_reason="no_valid_time_history_for_entity_type",
                authority=auth, entries=[],
                closest_revision_guess=False)
    else:
        project_id, reason = _resolve_project_id(request)
        if project_id is None:
            state = (qc.STATE_AMBIGUOUS_QUERY
                     if reason in ("ambiguous_project_name",
                                   "multiple_projects_share_name")
                     else qc.STATE_NOT_FOUND)
            return _envelope("timeline", state, unresolved_reason=reason)
        project_refs.append(project_id)

        changes = rc.get_recent_changes(
            project_id=project_id, limit=request.limit, db_path=path)
        for c in (changes.get("items") or []):
            if c.get("entity_type") not in BUSINESS_TARGET_TYPES:
                continue
            entries.append({
                "entry_type": "business_change",
                "entity_type": c.get("entity_type"),
                "entity_id": c.get("entity_id"),
                "label": c.get("change_type"),
                "known_from": c.get("committed_at"),
                "valid_from": None,
                "valid_to": None,
                "temporal_state": qc.STATE_TEMPORAL_HISTORY_INCOMPLETE,
                "ref": c.get("change_ref"),
                "commit_ref": c.get("commit_ref"),
            })

        ctx = repo.project_context(project_id) or {}
        for t in (ctx.get("timeline") or [])[: request.limit]:
            entries.append({
                "entry_type": "project_event",
                "entity_type": "project",
                "entity_id": project_id,
                "label": (t.get("title") or t.get("summary") or t.get("type")
                          or "project event"),
                "known_from": (t.get("at") or t.get("created_at")
                               or t.get("timestamp")),
                "valid_from": None,
                "valid_to": None,
                "temporal_state": qc.STATE_TEMPORAL_HISTORY_INCOMPLETE,
                "ref": t.get("id"),
            })
        if not request.include_historical:
            entries = [e for e in entries if e["entry_type"] != "project_event"]
        unresolved = qc.STATE_TEMPORAL_HISTORY_INCOMPLETE

    entries.sort(key=lambda e: (e.get("known_from") or ""), reverse=True)
    entries = entries[: request.limit]
    if not entries:
        return _envelope("timeline", qc.STATE_NOT_FOUND,
                         unresolved_reason="no_timeline_records",
                         project_refs=project_refs)

    return _envelope(
        "timeline", qc.STATE_OK,
        authority_state=None,
        canonical=True,
        temporal_state=(qc.STATE_TEMPORAL_HISTORY_INCOMPLETE
                        if unresolved else None),
        project_refs=project_refs,
        unresolved_reason=None,
        provisional_reason=unresolved,
        entries=entries,
        count=len(entries),
        known_time_only=True,
        derived_view=True,
        closest_revision_guess=False,
    )


# ---------------------------------------------------------------------------
# 6. FORMAL_METRIC  (reuses the Formal Metric service; multidimensional kept)
# ---------------------------------------------------------------------------
def _metric_observation_view(obs: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "observation_id": obs.get("id"),
        "value": obs.get("value"),
        "observed_at": obs.get("observed_at"),
        "period": obs.get("period"),
        "dimensions": dict(obs.get("dimensions") or {}),
        "canonical": bool(obs.get("canonical")),
        "authority_state": obs.get("authority_state"),
        "source_version_ids": list(obs.get("source_version_ids") or []),
        "change_id": obs.get("change_id"),
        "commit_ref": obs.get("commit_ref"),
        "evidence_refs": list(obs.get("evidence_refs") or []),
    }


def _h_formal_metric(request: qc.QueryRequest, path: str) -> dict[str, Any]:
    raw = rc.query_formal_metrics(
        metric_id=request.metric_id,
        metric_name=(None if request.metric_id else request.metric_name),
        project_id=request.project_id,
        period=request.period,
        dimensions=(dict(request.dimensions) or None),
        db_path=path,
    )
    found = [r for r in (raw.get("results") or []) if r.get("found")]
    if not found:
        return _envelope(
            "formal_metric", qc.STATE_NOT_FOUND,
            project_refs=[request.project_id] if request.project_id else [],
            unresolved_reason=("metric_definition_not_found"
                               if (request.metric_id or request.metric_name)
                               else "metric_identifier_required"))

    metric_ids = {r.get("metric_id") for r in found}
    ambiguous = (len(metric_ids) > 1 and not request.metric_id
                 and not request.project_id)

    metrics = []
    canonical_total = 0
    observation_total = 0
    source_refs: list[str] = []
    for r in found:
        obs = [_metric_observation_view(o)
               for o in (r.get("formal_observations") or [])]
        canonical = int(r.get("canonical_observation_count") or 0)
        canonical_total += canonical
        observation_total += int(r.get("observation_count") or 0)
        for o in obs:
            source_refs.extend(o["source_version_ids"])
        definition = r.get("definition") or {}
        effective = list(r.get("effective_current_observations") or [])
        metrics.append({
            "metric_id": r.get("metric_id"),
            "name": definition.get("name"),
            "description": definition.get("description"),
            "unit": r.get("unit"),
            "definition_authority": r.get("definition_authority"),
            "effective_current_value": r.get("effective_current_value"),
            "latest_observed_is_effective_current":
                r.get("latest_observed_is_effective_current", False),
            "is_multidimensional": (r.get("effective_current_value") is None
                                    and bool(effective)),
            "effective_current_observations":
                [_metric_observation_view(o) for o in effective],
            "observations": obs,
            "observation_count": r.get("observation_count"),
            "canonical_observation_count": canonical,
            "matching_count": r.get("matching_count"),
            "source_lineage": r.get("source_lineage"),
            "evidence_locator": r.get("evidence_locator"),
            "filters": r.get("filters"),
        })

    primary = metrics[0]
    effective_primary = primary["effective_current_observations"]
    if ambiguous:
        state = qc.STATE_AMBIGUOUS_QUERY
    elif canonical_total > 0:
        state = qc.STATE_OK
    elif observation_total > 0:
        state = qc.STATE_NON_CANONICAL_ONLY
    else:
        state = qc.STATE_UNRESOLVED

    top_value = primary.get("effective_current_value")
    top_change = (effective_primary[0].get("change_id")
                  if effective_primary else None)
    top_commit = (effective_primary[0].get("commit_ref")
                  if effective_primary else None)

    return _envelope(
        "formal_metric", state,
        authority_state=("EFFECTIVE_CURRENT"
                         if canonical_total > 0 else "UNRESOLVED"),
        canonical=canonical_total > 0,
        temporal_state=(qc.STATE_TEMPORAL_HISTORY_INCOMPLETE
                        if canonical_total == 0 and observation_total
                        else None),
        source_refs=_dedup(source_refs),
        project_refs=[request.project_id] if request.project_id else [],
        unresolved_reason=("metric_name_matches_multiple_definitions"
                           if ambiguous else
                           "no_formal_observation" if observation_total == 0
                           else None),
        # Metric extras required by the response contract.
        metric_id=primary.get("metric_id"),
        metric_name=primary.get("name"),
        unit=primary.get("unit"),
        period=(effective_primary[0].get("period") if effective_primary
                else request.period),
        dimensions=(dict(effective_primary[0].get("dimensions") or {})
                    if effective_primary else dict(request.dimensions)),
        value=top_value,
        change_id=top_change,
        commit_ref=top_commit,
        multidimensional=bool(primary.get("is_multidimensional")),
        latest_observed_is_effective_current=False,
        metrics=metrics,
        metric_count=len(metrics),
        analysis_data_included=False,
        analysis_isolation="analysis data requires the ANALYSIS_DATA intent",
    )


# ---------------------------------------------------------------------------
# 7. EVIDENCE_QUERY  (real EvidenceRef only; never an invented locator)
# ---------------------------------------------------------------------------
def _h_evidence_query(request: qc.QueryRequest, path: str) -> dict[str, Any]:
    evidence = rc.get_evidence(
        request.source_version_id or "", request.locator or "", db_path=path)
    status = evidence.get("status")
    reason = evidence.get("reason")
    if status == "PASS":
        state = qc.STATE_OK
    elif status == qc.STATE_EVIDENCE_NOT_AVAILABLE:
        state = qc.STATE_EVIDENCE_NOT_AVAILABLE
    else:
        state = _EVIDENCE_STATE_BY_REASON.get(reason, qc.STATE_UNRESOLVED)

    return _envelope(
        "evidence_query", state,
        authority_state=None,
        canonical=False,
        temporal_state=None,
        source_refs=[request.source_version_id] if request.source_version_id
                     else [],
        unresolved_reason=(None if state == qc.STATE_OK else reason),
        provisional_reason=("evidence is source content only; it is never a "
                            "formal fact"),
        # Evidence extras required by the response contract.
        source_version_id=evidence.get("source_version_id"),
        locator=evidence.get("locator"),
        block_type=evidence.get("block_type"),
        block_index=evidence.get("block_index"),
        raw_artifact_available=evidence.get("raw_artifact_available"),
        identity_verification=evidence.get("identity_verification"),
        excerpt=evidence.get("excerpt"),
        fidelity=evidence.get("fidelity"),
        evidence_status=status,
        core_note=evidence.get("note"),
        locator_source="caller_supplied_evidenceref",
        locator_invented=False,
    )


# ---------------------------------------------------------------------------
# 8. RECENT_CHANGES  (business changes only)
# ---------------------------------------------------------------------------
def _h_recent_changes(request: qc.QueryRequest, path: str) -> dict[str, Any]:
    raw = rc.get_recent_changes(
        since=request.since,
        project_id=request.project_id,
        entity_types=(list(request.entity_types) or None),
        limit=request.limit,
        db_path=path,
    )
    items = raw.get("items") or []
    kept = [i for i in items if i.get("entity_type") in BUSINESS_TARGET_TYPES]
    excluded = len(items) - len(kept)
    state = qc.STATE_OK if kept else qc.STATE_NOT_FOUND
    return _envelope(
        "recent_changes", state,
        authority_state=None,
        canonical=True,
        temporal_state=qc.STATE_TEMPORAL_HISTORY_INCOMPLETE,
        source_refs=_dedup(
            r for i in kept for r in (i.get("source_refs") or [])),
        project_refs=[request.project_id] if request.project_id else [],
        unresolved_reason=None if kept else "no_business_change_in_window",
        provisional_reason=("business valid time is not recorded; committed_at "
                            "is known time"),
        items=kept,
        count=len(kept),
        ai_runs_included=bool(raw.get("ai_runs_included")),
        excluded_non_business_count=excluded,
        business_only=True,
        business_target_types=sorted(BUSINESS_TARGET_TYPES),
    )


# ---------------------------------------------------------------------------
# 9. ANALYSIS_DATA  (NON_CANONICAL, explicit opt-in only)
# ---------------------------------------------------------------------------
def _h_analysis_data(request: qc.QueryRequest, path: str) -> dict[str, Any]:
    from brain.api import brain_repository as brain_repo  # lazy, optional

    rows = brain_repo.list_ai_activity(limit=request.limit)
    return _envelope(
        "analysis_data", qc.STATE_NON_CANONICAL_ONLY,
        authority_state=None,
        canonical=False,
        temporal_state=None,
        project_refs=[request.project_id] if request.project_id else [],
        unresolved_reason=None,
        provisional_reason="NON_CANONICAL_ONLY",
        items=rows,
        count=len(rows),
        non_canonical=True,
        is_canonical=False,
        data_source="analysis_run",
        project_linkage_available=False,
        project_filter_applied=False,
        excluded_from_formal_queries=True,
    )


_HANDLERS: dict[str, Callable[[qc.QueryRequest, str], dict[str, Any]]] = {
    qc.INTENT_PROJECT_CURRENT: _h_project_current,
    qc.INTENT_KNOWLEDGE_CURRENT: _h_knowledge_current,
    qc.INTENT_TEMPORAL_QUERY: _h_temporal_query,
    qc.INTENT_ENTITY_AUTHORITY: _h_entity_authority,
    qc.INTENT_TIMELINE: _h_timeline,
    qc.INTENT_FORMAL_METRIC: _h_formal_metric,
    qc.INTENT_EVIDENCE_QUERY: _h_evidence_query,
    qc.INTENT_RECENT_CHANGES: _h_recent_changes,
    qc.INTENT_ANALYSIS_DATA: _h_analysis_data,
}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def execute(
    request: qc.QueryRequest | Mapping[str, Any],
    *,
    db_path: str | None = None,
    hops: int = 1,
) -> dict[str, Any]:
    """Execute one typed query. Fail closed on everything.

    ``hops`` is the number of Gateway calls made so far for this user
    question; it is bounded by ``paw_query_contract.MAX_TOOL_HOPS`` so an
    Agent can never loop.
    """
    if isinstance(request, Mapping):
        request, error = qc.validate_request(request)
        if request is None:
            return error  # already a typed INVALID_ARGS envelope
    if not isinstance(request, qc.QueryRequest):
        return qc.invalid_args("request_must_be_QueryRequest_or_mapping")

    if not isinstance(hops, int) or hops < 1:
        return qc.invalid_args("hops_must_be_positive_integer")
    if hops > qc.MAX_TOOL_HOPS:
        return _envelope(
            "bounded", qc.STATE_UNRESOLVED,
            unresolved_reason="max_tool_hops_exceeded",
            provisional_reason=None,
            hops=hops,
            max_tool_hops=qc.MAX_TOOL_HOPS,
            next_step="answer with what is already resolved, or narrow the "
                      "question",
        )

    handler = _HANDLERS.get(request.intent)
    if handler is None:
        return qc.invalid_args("unroutable_intent",
                               {"intent": request.intent})

    path = str(db_path or DEFAULT_DB)
    try:
        response = handler(request, path)
    except Exception as exc:  # noqa: BLE001 - fail closed, never crash a caller
        if os.environ.get("PAW_GATEWAY_STRICT") == "1":
            raise
        return _envelope(
            "error", qc.STATE_UNRESOLVED,
            unresolved_reason="capability_unavailable",
            provisional_reason=None,
            intent=request.intent,
            error_type=type(exc).__name__,
        )
    response["intent"] = request.intent
    response["hops"] = hops
    response["max_tool_hops"] = qc.MAX_TOOL_HOPS
    return response


__all__ = [
    "SCHEMA", "DEFAULT_DB", "BUSINESS_TARGET_TYPES",
    "NON_BUSINESS_TARGET_TYPES", "envelope", "execute",
]

# Public alias: the unified response envelope is part of the Gateway contract
# (the Planner composes multi-intent responses and must use the same shape).
envelope = _envelope
