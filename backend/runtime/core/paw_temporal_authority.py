#!/usr/bin/env python3
"""PAW v1.3A Temporal Authority Lite — Current Resolver (Patch 1).

Deterministic temporal authority layer over the existing Formal State.
Patch 1 aligns the resolver with the REAL production contracts:

- source_version_governance REAL columns: governance_status,
  supersedes_source_version_id, effective_from, effective_to,
  authority_rank, authority_reason, confidence, provenance_json.
- governance_status contract (paw_source_version_governance.py):
  {current, superseded, archived, unresolved} — mapped explicitly,
  unknown values fail closed to UNRESOLVED.
- knowledge_revision.compiled_at is a technical materialization time.
  It is NEVER used as business valid_from. Without explicit valid-time
  metadata, As-Of queries fail closed to TEMPORAL_HISTORY_INCOMPLETE.
- Metric authority is split: an ACTIVE definition only proves definition
  authority; observation authority requires canonical lineage (the
  notes JSON contract: dimensions / period / calculation_basis).
  latest_observed != effective_current.

No second fact store. No model calls. No bulk backfill.
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "paw-temporal-authority/2"

EFFECTIVE_CURRENT = "EFFECTIVE_CURRENT"
FUTURE_EFFECTIVE = "FUTURE_EFFECTIVE"
SUPERSEDED = "SUPERSEDED"
HISTORICAL = "HISTORICAL"
ARCHIVED = "ARCHIVED"
UNRESOLVED = "UNRESOLVED"
TEMPORAL_HISTORY_INCOMPLETE = "TEMPORAL_HISTORY_INCOMPLETE"

AUTHORITY_STATES = frozenset({
    EFFECTIVE_CURRENT, FUTURE_EFFECTIVE, SUPERSEDED, HISTORICAL,
    ARCHIVED, UNRESOLVED, TEMPORAL_HISTORY_INCOMPLETE,
})

ENTITY_KNOWLEDGE = "knowledge"
ENTITY_METRIC = "metric"
ENTITY_SOURCE_VERSION = "source_version"
SUPPORTED_ENTITY_TYPES = frozenset({ENTITY_KNOWLEDGE, ENTITY_METRIC, ENTITY_SOURCE_VERSION})

# ----------------------------------------------------------------------
# REAL production governance_status contract
# (paw_source_version_governance.GOVERNANCE_STATUSES, lowercase values)
# ----------------------------------------------------------------------
REAL_GOVERNANCE_STATUSES = frozenset({"current", "superseded", "archived", "unresolved"})

# Metric observation canonical-lineage contract fields (notes JSON).
METRIC_LINEAGE_FIELDS = ("dimensions", "period", "calculation_basis")


def _committed_metric_evidence(payload: Any, source_refs: list[Any]) -> list[dict[str, str]]:
    """Recover ONLY explicit refs inside this committed observation payload.

    Committer writes payload.notes verbatim to metric_observation.notes. Use
    the reviewed/committed payload, not mutable observation notes, sidecars,
    other changes, metric names or guessed cells. The existing QueryContract
    and Evidence Gateway still validate syntax and actual Reader identity.
    Reference presence is NOT evidence content/semantic acceptance.
    """
    try:
        reviewed = json.loads(payload) if isinstance(payload, str) else payload
        if not isinstance(reviewed, Mapping) or not reviewed.get("metric_definition_id"):
            return []
        notes = reviewed.get("notes")
        notes = json.loads(notes) if isinstance(notes, str) else notes
    except (ValueError, TypeError):
        return []
    if not isinstance(notes, Mapping):
        return []
    refs = notes.get("evidence_refs")
    if not isinstance(refs, list):
        return []
    # This is the existing incremental writer's explicit namespace, not a
    # fuzzy association. Preserve raw source_refs in the governance lineage.
    sources = list(dict.fromkeys(s.removeprefix("source_version:")
                                for s in source_refs if isinstance(s, str) and s))
    pairs = []
    for ref in refs:
        if isinstance(ref, Mapping):
            source, locator = ref.get("source_version_id"), ref.get("evidence_locator")
        elif isinstance(ref, str) and len(sources) == 1:
            source, locator = sources[0], ref
        else:
            continue
        if (not isinstance(source, str) or source not in sources
                or not isinstance(locator, str) or not locator.strip()):
            continue
        pair = {"source_version_id": source, "evidence_locator": locator}
        if pair not in pairs:
            pairs.append(pair)
    return pairs


def _observation_governance_lineage(
    db: sqlite3.Connection, observation_id: str,
) -> dict[str, Any] | None:
    """Closure B: derive metric observation authority from the REAL governed
    write chain: audit_event(target_type='data') -> committed ApprovedChange
    -> source_refs. Returns None when no unique committed lineage exists.

    Verified against production: all 2474 observations link uniquely to a
    committed change with non-empty source_refs (2026-09-20 audit).
    """
    links = db.execute(
        "SELECT change_id, commit_id FROM audit_event "
        "WHERE target_type='data' AND target_id = ?",
        (observation_id,),
    ).fetchall()
    if not links:
        return None
    committed_changes = {}
    for row in links:
        ac = db.execute(
            "SELECT change_id, status, source_refs, payload FROM approved_change "
            "WHERE change_id = ?", (row["change_id"],),
        ).fetchone()
        if ac is not None and ac["status"] == "committed":
            committed_changes[ac["change_id"]] = (ac, row["commit_id"])
    if len(committed_changes) != 1:
        return None  # zero or ambiguous: not provable
    change_id = next(iter(committed_changes))
    ac, commit_id = committed_changes[change_id]
    refs_raw = str(ac["source_refs"] or "").strip()
    source_refs = []
    if refs_raw:
        try:
            parsed = json.loads(refs_raw)
            source_refs = parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            source_refs = []
    if not source_refs:
        return None  # lineage requires non-empty SourceVersion refs
    return {
        "change_id": change_id,
        "commit_id": commit_id,
        "source_refs": source_refs,
        "source_version_ids": list(dict.fromkeys(s.removeprefix("source_version:")
                                  for s in source_refs if isinstance(s, str) and s)),
        "evidence_refs": _committed_metric_evidence(ac["payload"], source_refs),
    }


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _parse_ts(value: Any) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _connect(db_path: str | Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def map_governance_status(status: Any) -> str:
    """ONE explicit deterministic mapping: REAL governance_status -> DTO
    authority state. Unknown values NEVER default to current."""
    value = _text(status).lower()
    if value == "current":
        return EFFECTIVE_CURRENT
    if value == "superseded":
        return SUPERSEDED
    if value == "archived":
        return ARCHIVED
    if value == "unresolved":
        return UNRESOLVED
    return UNRESOLVED  # unknown value: fail closed


@dataclass(frozen=True)
class AuthorityResult:
    """Deterministic authority resolution result (the wire contract)."""

    entity_type: str
    entity_id: str
    authority_state: str
    resolved_ref: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    known_from: str | None = None
    known_to: str | None = None
    supersedes: str | None = None
    superseded_by: str | None = None
    decision_ref: str | None = None
    evidence_fingerprint: str | None = None
    resolution_basis: str = ""
    unresolved_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "authority_state": self.authority_state,
            "resolved_ref": self.resolved_ref,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "known_from": self.known_from,
            "known_to": self.known_to,
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
            "decision_ref": self.decision_ref,
            "evidence_fingerprint": self.evidence_fingerprint,
            "resolution_basis": self.resolution_basis,
            "unresolved_reason": self.unresolved_reason,
            "deterministic": True,
            "model_call": False,
        }


class TemporalAuthorityResolver:
    """Deterministic Current Resolver over the existing Formal State.

    Read-only. No model calls. No second fact store.
    """

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = str(db_path or os.environ.get(
            "PAW_WORKSPACE_DB", os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve_current(
        self,
        *,
        entity_type: str,
        entity_id: str,
        logical_family: str | None = None,
        as_of: str | None = None,
        known_as_of: str | None = None,
    ) -> dict[str, Any]:
        """Resolve the effective-current reference for one entity.

        as_of        -> valid-time query (business effectiveness date)
        known_as_of  -> known-time query (what the system knew at that date)
        """
        entity_type = _text(entity_type).lower()
        entity_id = _text(entity_id)
        if entity_type not in SUPPORTED_ENTITY_TYPES:
            return AuthorityResult(
                entity_type=entity_type, entity_id=entity_id,
                authority_state=UNRESOLVED,
                resolution_basis="unsupported_entity_type",
                unresolved_reason=f"unsupported_entity_type:{entity_type}",
            ).to_dict()
        if not entity_id:
            return AuthorityResult(
                entity_type=entity_type, entity_id=entity_id,
                authority_state=UNRESOLVED,
                resolution_basis="missing_entity_id",
                unresolved_reason="entity_id_required",
            ).to_dict()

        as_of_dt = _parse_ts(as_of)
        known_as_of_dt = _parse_ts(known_as_of)

        if entity_type == ENTITY_KNOWLEDGE:
            return self._resolve_knowledge(entity_id, as_of_dt, known_as_of_dt)
        if entity_type == ENTITY_METRIC:
            return self._resolve_metric(entity_id, as_of_dt, known_as_of_dt)
        return self._resolve_source_version(entity_id, as_of_dt, known_as_of_dt)

    # ------------------------------------------------------------------
    # Closure D: SourceVersion logical-family resolution.
    # "Which SourceVersion in logical document X was effective on date Y?"
    # ------------------------------------------------------------------

    def resolve_family(
        self,
        *,
        logical_document_id: str,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        """Resolve the effective member of one logical_document family.

        current query (as_of=None): the single provable current member.
        as_of query: the member whose explicit effective_from/effective_to
        interval contains the requested date. Multiple matches -> UNRESOLVED;
        no provable interval -> TEMPORAL_HISTORY_INCOMPLETE. Never max(version)
        / max(created_at) / mtime / filename.
        """
        family_id = _text(logical_document_id)
        if not family_id:
            return AuthorityResult(
                ENTITY_SOURCE_VERSION, "", UNRESOLVED,
                resolution_basis="missing_logical_document_id",
                unresolved_reason="logical_document_id_required",
            ).to_dict()
        as_of_dt = _parse_ts(as_of)

        with _connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            try:
                family = db.execute(
                    "SELECT source_version_id, governance_status, "
                    "supersedes_source_version_id, effective_from, effective_to, "
                    "authority_rank, created_at FROM source_version_governance "
                    "WHERE logical_document_id = ?",
                    (family_id,),
                ).fetchall()
            except sqlite3.OperationalError:
                family = []

        if not family:
            return AuthorityResult(
                ENTITY_SOURCE_VERSION, family_id, TEMPORAL_HISTORY_INCOMPLETE,
                resolution_basis="family_governance_absent",
                unresolved_reason="no_governance_record_no_backfill_allowed",
            ).to_dict()

        if as_of_dt is None:
            # Family current: single provable current member only.
            currents = [f for f in family
                        if map_governance_status(f["governance_status"]) == EFFECTIVE_CURRENT]
            if len(currents) == 0:
                return AuthorityResult(
                    ENTITY_SOURCE_VERSION, family_id, TEMPORAL_HISTORY_INCOMPLETE,
                    resolution_basis="family_no_current_member",
                    unresolved_reason="no_provable_current_member",
                ).to_dict()
            if len(currents) > 1:
                ranks = {int(f["authority_rank"]) for f in currents}
                best = min(ranks)
                best_rows = [f for f in currents if int(f["authority_rank"]) == best]
                if len(best_rows) > 1:
                    return AuthorityResult(
                        ENTITY_SOURCE_VERSION, family_id, UNRESOLVED,
                        resolution_basis="multiple_current_authorities",
                        unresolved_reason="multiple_current_authorities",
                    ).to_dict()
                currents = best_rows
            member = currents[0]
            return AuthorityResult(
                ENTITY_SOURCE_VERSION, family_id, EFFECTIVE_CURRENT,
                resolved_ref=_text(member["source_version_id"]),
                valid_from=_text(member["effective_from"]) or None,
                valid_to=_text(member["effective_to"]) or None,
                known_from=_text(member["created_at"]) or None,
                supersedes=_text(member["supersedes_source_version_id"]) or None,
                resolution_basis="family_single_provable_current",
            ).to_dict()

        # As-Of: explicit interval containment only.
        covering = []
        unprovable = 0
        for f in family:
            vf = _parse_ts(f["effective_from"])
            vt = _parse_ts(f["effective_to"]) if _text(f["effective_to"]) else None
            if vf is None:
                unprovable += 1
                continue
            if vf <= as_of_dt and (vt is None or vt > as_of_dt):
                covering.append(f)
        if len(covering) > 1:
            return AuthorityResult(
                ENTITY_SOURCE_VERSION, family_id, UNRESOLVED,
                resolution_basis="multiple_effective_intervals_at_as_of",
                unresolved_reason="multiple_current_authorities",
            ).to_dict()
        if len(covering) == 1:
            member = covering[0]
            # State relative to the family's current authority.
            member_state = map_governance_status(member["governance_status"])
            if member_state == EFFECTIVE_CURRENT:
                state = EFFECTIVE_CURRENT
            elif member_state == SUPERSEDED:
                state = HISTORICAL
            else:
                state = member_state
            return AuthorityResult(
                ENTITY_SOURCE_VERSION, family_id, state,
                resolved_ref=_text(member["source_version_id"]),
                valid_from=_text(member["effective_from"]) or None,
                valid_to=_text(member["effective_to"]) or None,
                known_from=_text(member["created_at"]) or None,
                supersedes=_text(member["supersedes_source_version_id"]) or None,
                resolution_basis="family_interval_contains_as_of",
            ).to_dict()
        # No covering member: either all future, or intervals unprovable.
        futures = [f for f in family if (_parse_ts(f["effective_from"]) or _now()) > as_of_dt]
        if futures and unprovable == 0:
            member = min(futures, key=lambda f: _parse_ts(f["effective_from"]) or _now())
            return AuthorityResult(
                ENTITY_SOURCE_VERSION, family_id, FUTURE_EFFECTIVE,
                resolved_ref=_text(member["source_version_id"]),
                valid_from=_text(member["effective_from"]) or None,
                known_from=_text(member["created_at"]) or None,
                resolution_basis="family_effective_from_after_as_of",
            ).to_dict()
        return AuthorityResult(
            ENTITY_SOURCE_VERSION, family_id, TEMPORAL_HISTORY_INCOMPLETE,
            resolution_basis="family_no_effective_member_at_as_of",
            unresolved_reason="no_effective_interval_at_as_of",
        ).to_dict()

    # ------------------------------------------------------------------
    # Knowledge: reuse knowledge_page + knowledge_revision.
    # confirmed_revision_no proves CURRENT authority only.
    # compiled_at is technical materialization time — NEVER valid_from.
    # Without explicit valid-time metadata, As-Of fails closed.
    # ------------------------------------------------------------------

    def _resolve_knowledge(
        self, knowledge_id: str, as_of_dt: datetime | None, known_as_of_dt: datetime | None,
    ) -> dict[str, Any]:
        with _connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            page = db.execute(
                "SELECT id, status, confirmed_revision_no, confirmed_at, confirmed_by "
                "FROM knowledge_page WHERE id = ?", (knowledge_id,),
            ).fetchone()
            if page is None:
                return AuthorityResult(
                    ENTITY_KNOWLEDGE, knowledge_id, UNRESOLVED,
                    resolution_basis="knowledge_page_not_found",
                    unresolved_reason="entity_not_found",
                ).to_dict()
            revisions = db.execute(
                "SELECT id, revision_no, compiled_at, created_at FROM knowledge_revision "
                "WHERE knowledge_id = ? ORDER BY revision_no", (knowledge_id,),
            ).fetchall()
            # Patch 1: explicit business valid-time metadata (optional table,
            # authority metadata only — never content). Absent => fail closed.
            metadata = self._load_knowledge_metadata(db, knowledge_id)

        confirmed_no = page["confirmed_revision_no"]
        if confirmed_no is None:
            return AuthorityResult(
                ENTITY_KNOWLEDGE, knowledge_id, TEMPORAL_HISTORY_INCOMPLETE,
                resolution_basis="no_confirmed_revision",
                unresolved_reason="confirmed_revision_no_missing",
            ).to_dict()

        by_no = {int(r["revision_no"]): r for r in revisions}
        confirmed = by_no.get(int(confirmed_no))
        if confirmed is None:
            return AuthorityResult(
                ENTITY_KNOWLEDGE, knowledge_id, TEMPORAL_HISTORY_INCOMPLETE,
                resolution_basis="confirmed_revision_row_missing",
                unresolved_reason="confirmed_revision_not_in_history",
            ).to_dict()

        if _text(page["status"]).lower() in {"deprecated", "archived"}:
            return AuthorityResult(
                ENTITY_KNOWLEDGE, knowledge_id, ARCHIVED,
                resolved_ref=f"knowledge_revision:{confirmed['id']}",
                known_from=_text(confirmed["created_at"]),
                decision_ref=f"knowledge_page:{knowledge_id}#status={_text(page['status'])}",
                resolution_basis="page_status_deprecated",
            ).to_dict()

        if _text(page["status"]).lower() != "current":
            return AuthorityResult(
                ENTITY_KNOWLEDGE, knowledge_id, UNRESOLVED,
                resolution_basis="knowledge_page_not_current",
                unresolved_reason="knowledge_status_does_not_prove_current",
            ).to_dict()

        # Known-time query: created_at IS the real known-time record.
        if known_as_of_dt is not None:
            known_from_dt = _parse_ts(confirmed["created_at"])
            if known_from_dt is not None and known_as_of_dt < known_from_dt:
                earlier = [r for r in revisions
                           if (_parse_ts(r["created_at"]) or _now()) <= known_as_of_dt]
                if earlier:
                    latest_known = max(earlier, key=lambda r: int(r["revision_no"]))
                    return AuthorityResult(
                        ENTITY_KNOWLEDGE, knowledge_id, HISTORICAL,
                        resolved_ref=f"knowledge_revision:{latest_known['id']}",
                        known_from=_text(latest_known["created_at"]),
                        superseded_by=f"knowledge_revision:{confirmed['id']}",
                        decision_ref=f"knowledge_page:{knowledge_id}#known_time_query",
                        resolution_basis="known_time_historical_revision",
                    ).to_dict()
                return AuthorityResult(
                    ENTITY_KNOWLEDGE, knowledge_id, TEMPORAL_HISTORY_INCOMPLETE,
                    resolution_basis="known_time_before_first_revision",
                    unresolved_reason="no_revision_known_at_known_as_of",
                ).to_dict()
            # known_as_of >= known_from: the confirmed revision was already
            # known. Current authority answerable; valid time not recorded.
            if as_of_dt is None:
                return self._knowledge_current_result(
                    knowledge_id, page, confirmed, revisions,
                    basis="confirmed_pointer_known_at_known_as_of",
                )

        # Valid-time (as_of) query: business valid time comes ONLY from the
        # explicit knowledge_revision_authority metadata. compiled_at is
        # NEVER used. Without metadata the query fails closed.
        if as_of_dt is not None:
            if not metadata:
                return AuthorityResult(
                    ENTITY_KNOWLEDGE, knowledge_id, TEMPORAL_HISTORY_INCOMPLETE,
                    resolved_ref=f"knowledge_revision:{confirmed['id']}",
                    known_from=_text(confirmed["created_at"]),
                    decision_ref=f"knowledge_page:{knowledge_id}#confirmed_revision_no={confirmed_no}",
                    resolution_basis="no_explicit_valid_time_metadata",
                    unresolved_reason="knowledge_valid_time_not_recorded",
                ).to_dict()
            covering = []
            future = []
            for m in metadata:
                vf = _parse_ts(m.get("valid_from"))
                vt = _parse_ts(m.get("valid_to")) if m.get("valid_to") else None
                if vf is None:
                    continue  # unparseable interval: not provable
                if vf <= as_of_dt and (vt is None or vt > as_of_dt):
                    covering.append(m)
                elif vf > as_of_dt:
                    future.append(m)
            if len(covering) > 1:
                return AuthorityResult(
                    ENTITY_KNOWLEDGE, knowledge_id, UNRESOLVED,
                    decision_ref=f"knowledge_page:{knowledge_id}#as_of_query",
                    resolution_basis="multiple_effective_revisions_at_as_of",
                    unresolved_reason="multiple_current_authorities",
                ).to_dict()
            if len(covering) == 1:
                m = covering[0]
                rev = by_no.get(int(m["revision_no"]))
                if rev is None:
                    return AuthorityResult(
                        ENTITY_KNOWLEDGE, knowledge_id, TEMPORAL_HISTORY_INCOMPLETE,
                        resolution_basis="metadata_references_missing_revision",
                        unresolved_reason="metadata_revision_not_in_history",
                    ).to_dict()
                if int(m["revision_no"]) == int(confirmed_no):
                    state, superseded_by = EFFECTIVE_CURRENT, None
                else:
                    state, superseded_by = HISTORICAL, f"knowledge_revision:{confirmed['id']}"
                return AuthorityResult(
                    ENTITY_KNOWLEDGE, knowledge_id, state,
                    resolved_ref=f"knowledge_revision:{rev['id']}",
                    valid_from=_text(m.get("valid_from")) or None,
                    valid_to=_text(m.get("valid_to")) or None,
                    known_from=_text(m.get("known_from")) or _text(rev["created_at"]),
                    superseded_by=superseded_by,
                    decision_ref=_text(m.get("decision_ref")) or f"knowledge_page:{knowledge_id}#as_of_query",
                    evidence_fingerprint=_text(m.get("evidence_fingerprint")) or None,
                    resolution_basis="explicit_valid_time_metadata",
                ).to_dict()
            if future:
                m = min(future, key=lambda x: _parse_ts(x.get("valid_from")) or _now())
                rev = by_no.get(int(m["revision_no"]))
                return AuthorityResult(
                    ENTITY_KNOWLEDGE, knowledge_id, FUTURE_EFFECTIVE,
                    resolved_ref=(f"knowledge_revision:{rev['id']}" if rev is not None else None),
                    valid_from=_text(m.get("valid_from")) or None,
                    known_from=_text(m.get("known_from")) or None,
                    decision_ref=_text(m.get("decision_ref")) or None,
                    resolution_basis="valid_from_after_as_of",
                ).to_dict()
            return AuthorityResult(
                ENTITY_KNOWLEDGE, knowledge_id, TEMPORAL_HISTORY_INCOMPLETE,
                resolution_basis="no_revision_effective_at_as_of",
                unresolved_reason="no_revision_effective_at_as_of",
            ).to_dict()

        # Plain current query: the explicit confirmed pointer is sufficient
        # for CURRENT authority; valid time from metadata when present.
        return self._knowledge_current_result(
            knowledge_id, page, confirmed, revisions, metadata,
        )

    @staticmethod
    def _load_knowledge_metadata(db: sqlite3.Connection, knowledge_id: str) -> list[dict[str, Any]]:
        """Load explicit valid-time metadata rows for one knowledge page.

        The knowledge_revision_authority table is Patch 1 minimal temporal
        metadata (authority metadata only, no content). If the table is
        absent or has no rows, returns [] and callers fail closed.
        """
        try:
            rows = db.execute(
                "SELECT knowledge_id, revision_no, valid_from, valid_to, known_from, "
                "known_to, supersedes_revision_no, decision_ref, evidence_fingerprint "
                "FROM knowledge_revision_authority WHERE knowledge_id = ? "
                "ORDER BY revision_no", (knowledge_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(r) for r in rows]

    @staticmethod
    def _knowledge_current_result(
        knowledge_id: str, page: sqlite3.Row, confirmed: sqlite3.Row,
        revisions: list[sqlite3.Row], metadata: list[dict[str, Any]] | None = None,
        *, basis: str = "confirmed_revision_pointer",
    ) -> dict[str, Any]:
        confirmed_no = int(page["confirmed_revision_no"])
        m = None
        for row in (metadata or []):
            if int(row["revision_no"]) == confirmed_no:
                m = row
                break
        valid_from = None
        if m is not None and _text(m.get("valid_to")):
            vt = _parse_ts(m.get("valid_to"))
            if vt is None:
                return AuthorityResult(ENTITY_KNOWLEDGE, knowledge_id, UNRESOLVED,
                    resolution_basis="invalid_valid_to", unresolved_reason="invalid_explicit_valid_time").to_dict()
            if vt <= _now():
                return AuthorityResult(ENTITY_KNOWLEDGE, knowledge_id, HISTORICAL,
                    resolved_ref=f"knowledge_revision:{confirmed['id']}",
                    valid_from=_text(m.get("valid_from")) or None,
                    valid_to=_text(m.get("valid_to")),
                    resolution_basis="confirmed_revision_valid_time_ended").to_dict()
        if m is not None and _text(m.get("valid_from")):
            vf = _parse_ts(m.get("valid_from"))
            if vf is None:
                return AuthorityResult(ENTITY_KNOWLEDGE, knowledge_id, UNRESOLVED,
                    resolution_basis="invalid_valid_from", unresolved_reason="invalid_explicit_valid_time").to_dict()
            if vf is not None and vf > _now():
                # Confirmed but not yet business-effective: future rule.
                return AuthorityResult(
                    ENTITY_KNOWLEDGE, knowledge_id, FUTURE_EFFECTIVE,
                    resolved_ref=f"knowledge_revision:{confirmed['id']}",
                    valid_from=_text(m.get("valid_from")),
                    known_from=_text(m.get("known_from")) or _text(confirmed["created_at"]),
                    decision_ref=_text(m.get("decision_ref")) or (
                        f"knowledge_page:{knowledge_id}#confirmed_revision_no={confirmed_no}"
                    ),
                    resolution_basis="confirmed_revision_not_yet_effective",
                ).to_dict()
            valid_from = _text(m.get("valid_from"))
        supersedes = None
        lower = [r for r in revisions if int(r["revision_no"]) < confirmed_no]
        if lower:
            prev = max(lower, key=lambda r: int(r["revision_no"]))
            supersedes = f"knowledge_revision:{prev['id']}"
        return AuthorityResult(
            ENTITY_KNOWLEDGE, knowledge_id, EFFECTIVE_CURRENT,
            resolved_ref=f"knowledge_revision:{confirmed['id']}",
            # valid_from is null without explicit metadata: compiled_at is
            # NEVER used as business valid_from.
            valid_from=valid_from,
            known_from=_text(confirmed["created_at"]),
            supersedes=supersedes,
            decision_ref=(
                f"knowledge_page:{knowledge_id}#confirmed_revision_no={confirmed_no}"
                f";confirmed_by={_text(page['confirmed_by']) or 'unknown'}"
            ),
            evidence_fingerprint=f"kr:{confirmed['id']}@rev{confirmed_no}",
            resolution_basis=basis,
        ).to_dict()

    # ------------------------------------------------------------------
    # Metric: definition authority vs observation authority.
    # An ACTIVE definition only proves the definition is active.
    # Observation authority requires canonical lineage in the notes JSON
    # (dimensions / period / calculation_basis). latest_observed != current.
    # ------------------------------------------------------------------

    def _resolve_metric(
        self, metric_id: str, as_of_dt: datetime | None, known_as_of_dt: datetime | None,
    ) -> dict[str, Any]:
        with _connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            definition = db.execute(
                "SELECT id, project_id, name, status, created_at, updated_at "
                "FROM metric_definition WHERE id = ?", (metric_id,),
            ).fetchone()
            if definition is None:
                return AuthorityResult(
                    ENTITY_METRIC, metric_id, UNRESOLVED,
                    resolution_basis="metric_definition_not_found",
                    unresolved_reason="entity_not_found",
                ).to_dict()
            observations = db.execute(
                "SELECT id, value, observed_at, notes, created_at FROM metric_observation "
                "WHERE metric_definition_id = ? ORDER BY observed_at",
                (metric_id,),
            ).fetchall()
            # Observation authority (Closure B): derived from the governed
            # write chain, evaluated inside the active connection.
            canonical_obs = None
            canonical_lineage = None
            for obs in observations:
                lineage = _observation_governance_lineage(db, obs["id"])
                if lineage is not None:
                    canonical_obs = obs
                    canonical_lineage = lineage
                    break

        status = _text(definition["status"]).lower()
        if status in {"deprecated", "archived"}:
            return AuthorityResult(
                ENTITY_METRIC, metric_id, ARCHIVED,
                resolved_ref=f"metric_definition:{definition['id']}",
                known_from=_text(definition["created_at"]),
                decision_ref=f"metric_definition:{metric_id}#status={status}",
                resolution_basis="metric_status_deprecated",
            ).to_dict()

        if as_of_dt is not None:
            created_dt = _parse_ts(definition["created_at"])
            if created_dt is not None and as_of_dt < created_dt:
                return AuthorityResult(
                    ENTITY_METRIC, metric_id, TEMPORAL_HISTORY_INCOMPLETE,
                    resolution_basis="metric_not_yet_defined_at_as_of",
                    unresolved_reason="definition_created_after_as_of",
                ).to_dict()
            # Metric observations carry no explicit business valid-time
            # intervals; observed_at is an observation timestamp, not an
            # authority interval. As-Of value resolution fails closed.
            return AuthorityResult(
                ENTITY_METRIC, metric_id, TEMPORAL_HISTORY_INCOMPLETE,
                resolved_ref=f"metric_definition:{definition['id']}",
                known_from=_text(definition["created_at"]),
                decision_ref=f"metric_definition:{metric_id}#as_of_query",
                resolution_basis="no_explicit_metric_valid_time",
                unresolved_reason="metric_valid_time_not_recorded",
            ).to_dict()

        # Observation authority was resolved inside the active connection
        # (canonical_obs / canonical_lineage) before it closed.

        if known_as_of_dt is not None:
            return AuthorityResult(ENTITY_METRIC, metric_id, TEMPORAL_HISTORY_INCOMPLETE,
                resolution_basis="metric_known_time_authority_history_not_recorded",
                unresolved_reason="metric_known_time_history_incomplete").to_dict()

        view = query_formal_metric(metric_id=metric_id, db_path=self.db_path)
        effective = view.get("effective_current_observations") or []
        proven = bool(effective and not view.get("unresolved_observation_count")
                      and status == "active")

        return AuthorityResult(
            ENTITY_METRIC, metric_id, EFFECTIVE_CURRENT if proven else UNRESOLVED,
            resolved_ref=f"metric_definition:{definition['id']}" if proven else None,
            known_from=_text(definition["created_at"]),
            decision_ref=f"metric_definition:{metric_id}#governed_semantic_cells" if proven else None,
            evidence_fingerprint=(
                "sourcerefs:" + ",".join(str(x) for x in canonical_lineage["source_refs"][:5])
                if canonical_lineage else f"md:{definition['id']}"
            ),
            resolution_basis=(
                "metric_definition_active_unique_governed_semantic_cells"
                if proven else "metric_observation_authority_unresolved"
            ),
            unresolved_reason=(
                None if proven else "no_unique_effective_governed_observations"
            ),
        ).to_dict()

    # ------------------------------------------------------------------
    # SourceVersion: REAL production schema adapter.
    # logical_document family resolution; explicit status mapping;
    # conflict -> UNRESOLVED; empty governance -> fail closed.
    # ------------------------------------------------------------------

    def _resolve_source_version(
        self, source_version_id: str, as_of_dt: datetime | None, known_as_of_dt: datetime | None,
    ) -> dict[str, Any]:
        with _connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            try:
                row = db.execute(
                    "SELECT source_version_id, logical_document_id, governance_status, "
                    "supersedes_source_version_id, effective_from, effective_to, "
                    "authority_rank, authority_reason, confidence, provenance_json, "
                    "created_at, updated_at "
                    "FROM source_version_governance WHERE source_version_id = ?",
                    (source_version_id,),
                ).fetchone()
            except sqlite3.OperationalError:
                row = None
            if row is None:
                return AuthorityResult(
                    ENTITY_SOURCE_VERSION, source_version_id, TEMPORAL_HISTORY_INCOMPLETE,
                    resolution_basis="source_version_governance_row_absent",
                    unresolved_reason="no_governance_record_no_backfill_allowed",
                ).to_dict()

            # Bounded family lookup: same logical_document only.
            family = db.execute(
                "SELECT source_version_id, governance_status, supersedes_source_version_id, "
                "effective_from, effective_to, authority_rank, created_at "
                "FROM source_version_governance WHERE logical_document_id = ?",
                (row["logical_document_id"],),
            ).fetchall()
            decisions = db.execute(
                "SELECT decision_id, created_at, approval_ref, change_id "
                "FROM source_version_decision_log WHERE logical_document_id = ? "
                "ORDER BY created_at",
                (row["logical_document_id"],),
            ).fetchall()

        # --- explicit deterministic status mapping ---
        raw_status = _text(row["governance_status"])
        if raw_status.lower() not in REAL_GOVERNANCE_STATUSES:
            return AuthorityResult(
                ENTITY_SOURCE_VERSION, source_version_id, UNRESOLVED,
                resolution_basis="unknown_governance_status",
                unresolved_reason=f"unknown_governance_status:{raw_status}",
            ).to_dict()
        state = map_governance_status(raw_status)

        # --- temporal fields from REAL columns ---
        valid_from = _text(row["effective_from"]) or None
        valid_to = _text(row["effective_to"]) or None
        # Known time: governance record creation (when PAW recorded the
        # authority state). effective_from is NEVER used as known_from.
        known_from = _text(row["created_at"]) or None
        decision_ref = None
        if decisions:
            latest = decisions[-1]
            decision_ref = f"source_version_decision_log:{latest['decision_id']}"
            if known_from is None:
                known_from = _text(latest["created_at"]) or None

        supersedes = _text(row["supersedes_source_version_id"]) or None
        superseded_by = None
        for f in family:
            if _text(f["supersedes_source_version_id"]) == source_version_id:
                superseded_by = _text(f["source_version_id"])
                break

        # --- evidence fingerprint: only from real provenance ---
        evidence_fingerprint = None
        try:
            prov = json.loads(_text(row["provenance_json"]) or "{}")
            if isinstance(prov, dict) and prov:
                evidence_fingerprint = "provenance:" + json.dumps(prov, sort_keys=True)[:120]
        except (ValueError, TypeError):
            evidence_fingerprint = None

        # --- future-effective check (real effective_from) ---
        if state == EFFECTIVE_CURRENT and valid_from:
            vf = _parse_ts(valid_from)
            if as_of_dt is not None and vf is not None:
                # As-Of query: judge by the requested date vs effective_from,
                # NOT by the wall clock.
                if as_of_dt < vf:
                    return AuthorityResult(
                        ENTITY_SOURCE_VERSION, source_version_id, FUTURE_EFFECTIVE,
                        resolved_ref=source_version_id,
                        valid_from=valid_from, valid_to=valid_to,
                        known_from=known_from,
                        supersedes=supersedes, superseded_by=superseded_by,
                        decision_ref=decision_ref,
                        evidence_fingerprint=evidence_fingerprint,
                        resolution_basis="effective_from_after_as_of",
                    ).to_dict()
                # as_of >= effective_from: the member is effective at as_of.
            elif vf is not None and vf > _now():
                # Plain current query: wall-clock judgement.
                state = FUTURE_EFFECTIVE

        # --- family conflict check: multiple provable currents ---
        if state == EFFECTIVE_CURRENT:
            currents = [f for f in family
                        if map_governance_status(f["governance_status"]) == EFFECTIVE_CURRENT]
            if len(currents) > 1:
                # Deterministic tie-break ONLY via proven authority_rank
                # (1 = governed decision, strongest; MIN keeps strongest).
                ranks = {int(f["authority_rank"]) for f in currents}
                best = min(ranks)
                best_rows = [f for f in currents if int(f["authority_rank"]) == best]
                if len(best_rows) > 1:
                    return AuthorityResult(
                        ENTITY_SOURCE_VERSION, source_version_id, UNRESOLVED,
                        valid_from=valid_from, valid_to=valid_to,
                        known_from=known_from,
                        decision_ref=decision_ref,
                        resolution_basis="multiple_current_authorities",
                        unresolved_reason="multiple_current_authorities",
                    ).to_dict()

        return AuthorityResult(
            ENTITY_SOURCE_VERSION, source_version_id, state,
            resolved_ref=source_version_id,
            valid_from=valid_from, valid_to=valid_to,
            known_from=known_from,
            supersedes=supersedes, superseded_by=superseded_by,
            decision_ref=decision_ref,
            evidence_fingerprint=evidence_fingerprint,
            resolution_basis=f"governance_status={raw_status}",
        ).to_dict()


def _metric_lineage(notes: Any) -> dict[str, Any] | None:
    """Parse the canonical metric lineage contract from observation notes.
    Returns None when the lineage contract is absent (not canonical)."""
    raw = _text(notes)
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(value, dict):
        return None
    if not all(field in value for field in METRIC_LINEAGE_FIELDS):
        return None
    return value


# ----------------------------------------------------------------------
# Formal metric query boundary (definition vs observation authority)
# ----------------------------------------------------------------------


def query_formal_metric(
    *,
    metric_id: str,
    db_path: str | Path | None = None,
    include_analysis_data: bool = False,
    period: str | None = None,
    dimensions: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Query one formal metric with explicit authority separation.

    Closure C semantics: a multi-dimensional metric has NO single scalar
    current value. effective_current_value is non-null ONLY when the
    filters (period / dimensions) identify exactly one semantic cell with
    exactly one authoritative observation. Otherwise the typed collection
    effective_current_observations is returned and the scalar is null.

    - definition_authority: ACTIVE status proves the definition is current.
    - observation authority: governed lineage (audit_event -> committed
      ApprovedChange -> source_refs), NOT structured notes semantics.
    Deterministic, read-only.
    """
    path = str(db_path or os.environ.get(
        "PAW_WORKSPACE_DB", os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))))
    with _connect(path) as db:
        db.row_factory = sqlite3.Row
        definition = db.execute(
            "SELECT id, project_id, name, description, unit, direction, "
            "target_value, status, created_at, updated_at "
            "FROM metric_definition WHERE id = ?", (metric_id,),
        ).fetchone()
        if definition is None:
            return {"schema": SCHEMA, "metric_id": metric_id, "found": False,
                    "formal_state_bypass": 0, "model_call": False}
        observations = db.execute(
            "SELECT id, value, observed_at, notes, created_at FROM metric_observation "
            "WHERE metric_definition_id = ? ORDER BY observed_at",
            (metric_id,),
        ).fetchall()

        definition_authority = (
            "DEFINITION_CURRENT" if _text(definition["status"]).lower() == "active"
            else f"DEFINITION_{_text(definition['status']).upper() or 'UNKNOWN'}"
        )

        # Build per-observation authority view with governed lineage.
        formal_observations = []
        canonical_count = 0
        for obs in observations:
            lineage = _observation_governance_lineage(db, obs["id"])
            canonical = lineage is not None
            if canonical:
                canonical_count += 1
            structured = _metric_lineage(obs["notes"]) or {}
            obs_period = ""
            period_obj = structured.get("period")
            if isinstance(period_obj, Mapping):
                obs_period = _text(period_obj.get("label"))
            else:
                obs_period = _text(period_obj)
            obs_dims = structured.get("dimensions") if isinstance(structured.get("dimensions"), Mapping) else {}
            # A committed observation proves lineage, not latest-wins authority.
            # Business valid time is used only when explicitly recorded in the
            # governed observation; observed_at is NEVER an effective interval.
            valid_from = structured.get("valid_from")
            valid_to = structured.get("valid_to")
            vf, vt = _parse_ts(valid_from), _parse_ts(valid_to)
            state = EFFECTIVE_CURRENT if canonical else TEMPORAL_HISTORY_INCOMPLETE
            reason = None
            if canonical and not structured:
                state, reason = TEMPORAL_HISTORY_INCOMPLETE, "metric_semantic_identity_missing"
            elif canonical and ((valid_from and vf is None) or (valid_to and vt is None)
                                or (vf and vt and vf >= vt)):
                state, reason = UNRESOLVED, "invalid_explicit_valid_time"
            elif canonical and vf and vf > _now():
                state = FUTURE_EFFECTIVE
            elif canonical and vt and vt <= _now():
                state = HISTORICAL
            elif canonical and definition_authority != "DEFINITION_CURRENT":
                state = ARCHIVED if _text(definition["status"]).lower() in {"archived", "deprecated"} else UNRESOLVED
            formal_observations.append({
                "id": obs["id"], "value": obs["value"],
                "observed_at": obs["observed_at"], "created_at": obs["created_at"],
                "period": obs_period,
                "dimensions": dict(obs_dims),
                "canonical": canonical,
                "authority_state": state,
                "unresolved_reason": reason,
                "valid_from": valid_from, "valid_to": valid_to,
                "_cell_key": (obs_period, json.dumps(obs_dims, sort_keys=True, ensure_ascii=False)),
                "source_version_ids": (lineage["source_version_ids"] if lineage else []),
                "change_id": (lineage["change_id"] if lineage else None),
                "commit_ref": (f"commit_record:{lineage['commit_id']}" if lineage and lineage.get("commit_id") else None),
                "evidence_refs": (lineage["evidence_refs"] if lineage else []),
            })

        # Multiple governed values in one semantic cell require an explicit
        # authority decision. Do not invent supersession from timestamps.
        currents_by_cell: dict[tuple, list[dict]] = {}
        for o in formal_observations:
            if o["authority_state"] == EFFECTIVE_CURRENT:
                currents_by_cell.setdefault(o["_cell_key"], []).append(o)
        for group in currents_by_cell.values():
            if len(group) > 1:
                for o in group:
                    o["authority_state"] = UNRESOLVED
                    o["unresolved_reason"] = "multiple_current_observations_for_semantic_cell"
        for o in formal_observations:
            o.pop("_cell_key", None)
        # Apply optional deterministic filters.
        matching = formal_observations
        if period is not None:
            matching = [o for o in matching if o["period"] == _text(period)]
        if dimensions is not None:
            wanted = {str(k): str(v) for k, v in dimensions.items()}
            matching = [
                o for o in matching
                if all(str(o["dimensions"].get(k)) == v for k, v in wanted.items())
            ]

        # Scalar semantics: only when filters isolate exactly one
        # authoritative observation.
        authoritative_matching = [o for o in matching if o["canonical"]
                                  and o["authority_state"] == EFFECTIVE_CURRENT]
        if len(authoritative_matching) == 1:
            effective_current_value = authoritative_matching[0]["value"]
            effective_current_observations = authoritative_matching
        else:
            effective_current_value = None
            effective_current_observations = authoritative_matching

    out = {
        "schema": SCHEMA,
        "metric_id": metric_id,
        "found": True,
        "definition": {k: definition[k] for k in definition.keys()},
        "definition_authority": definition_authority,
        "unit": definition["unit"],
        "filters": {"period": period, "dimensions": dict(dimensions) if dimensions else None},
        "matching_observations": matching,
        "matching_count": len(matching),
        # R6 fix: explicit filter-match status so a zero-hit filtered query
        # (e.g. a non-existent period) is unmistakable at the call site.
        "filter_match": (
            "NOT_FILTERED" if (period is None and dimensions is None)
            else ("MATCH" if matching else "FILTER_NO_MATCH")),
        "filter_no_match_reason": (
            None if (period is None and dimensions is None) or matching
            else "no observation matches the requested period/dimension filters"),
        "formal_observations": formal_observations,
        "observation_count": len(observations),
        "canonical_observation_count": canonical_count,
        "matching_canonical_observation_count": sum(bool(o["canonical"]) for o in matching),
        "effective_current_observation_count": len(authoritative_matching),
        "unresolved_observation_count": sum(o["authority_state"] == UNRESOLVED for o in matching),
        "effective_current_value": effective_current_value,
        "effective_current_observations": effective_current_observations,
        "latest_observed_is_effective_current": False,
        "source_lineage": ("READY" if canonical_count > 0 else "MISSING"),
        # A persisted reference is not proof of actual block readability or
        # full-source coverage. Old missing-locator rows remain PARTIAL.
        "evidence_reference_count": sum(len(o["evidence_refs"]) for o in matching),
        # R6 fix: evidence disclosure follows the FILTERED matching set,
        # not the full observation set.
        "evidence_locator": (
            ("PARTIAL" if any(o["canonical"] for o in matching) else "MISSING")
            if matching else "MISSING"),
        "analysis_data_included": bool(include_analysis_data),
        "formal_state_bypass": 0,
        "model_call": False,
    }
    if include_analysis_data:
        out["analysis_notes"] = [_text(obs["notes"]) for obs in observations]
    return out
