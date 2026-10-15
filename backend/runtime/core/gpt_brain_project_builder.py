"""Project Builder: aggregate existing human-defined projects; never create Formal Projects.

Brain-layer snapshots are analysis/proposal/context.  Missing projects become
PROJECT_PROPOSAL / USER_DECISION_REQUIRED.  Formal Project rows are read-only.
"""

from __future__ import annotations

from pathlib import Path


import os
import sqlite3
from typing import Any, Mapping, Protocol

from gpt_brain_common import (
    LAYER_SCHEMA_VERSION,
    LockedJsonStore,
    hash_payload,
    redact_mapping,
    safe_copy,
    text,
    unique,
    utc_now,
    validate_evidence,
)

try:  # pragma: no cover - import root differs between the two packages
    from current_context_policy import is_current_business_project
except ImportError:  # pragma: no cover - alternate import root
    from workspace_api.current_context_policy import is_current_business_project


# Current Project factual mutation keys.  A value for any of these is a claim
# about the Project, so it requires real Evidence.  Suggestion-only payloads
# (next_action.gpt_suggestion, proposal / suggestion, linked_source_versions,
# linked_todos, linked_knowledge_topics) are deliberately NOT in this set, and
# a suggestion must never be promoted into an evidence fact.
FACTUAL_MUTATION_KEYS = (
    "definition",
    "timeline",
    "stages",
    "decisions",
    "resolved_issues",
    "unresolved_issues",
    "linked_metric_assets",
    "roles",
    "stakeholders",
    "risks",
    "conflicts",
    "ambiguities",
    "current_state",
    "effective_period",
    "version_relations",
)


def _formal_project_is_current_business(formal: Mapping[str, Any] | None) -> bool:
    """Canonical Current-State admission for a Formal Project record.

    The catalog shape keys the name as ``project_name`` while the canonical
    rule reads ``name``; only the field names are adapted here - the rule
    itself is never restated.
    """
    if not isinstance(formal, Mapping):
        return False
    return bool(is_current_business_project({
        "name": formal.get("project_name") or formal.get("name"),
        "status": formal.get("status"),
    }))


def _factual_keys_without_evidence(
    update: Mapping[str, Any], evidence: list[dict[str, Any]]
) -> list[str]:
    """Factual mutation keys present in the payload that lack valid Evidence."""
    if evidence:
        return []
    context = update.get("context") if isinstance(update.get("context"), Mapping) else {}
    missing: list[str] = []
    for key in FACTUAL_MUTATION_KEYS:
        if update.get(key) or context.get(key):
            missing.append(key)
    return missing


class FormalProjectCatalog(Protocol):
    def get(self, project_id: str) -> dict[str, Any] | None: ...
    def find_by_name(self, name: str) -> dict[str, Any] | None: ...
    def list_projects(self) -> list[dict[str, Any]]: ...


class MemoryProjectCatalog:
    """Test / canary catalog.  Never writes Formal State."""

    def __init__(self, projects: list[Mapping[str, Any]] | None = None):
        self._by_id: dict[str, dict[str, Any]] = {}
        self._by_name: dict[str, dict[str, Any]] = {}
        for raw in projects or ():
            record = _catalog_record(raw)
            self._by_id[record["project_id"]] = record
            self._by_name[record["project_name"]] = record

    def get(self, project_id: str) -> dict[str, Any] | None:
        return self._by_id.get(text(project_id))

    def find_by_name(self, name: str) -> dict[str, Any] | None:
        return self._by_name.get(text(name))

    def list_projects(self) -> list[dict[str, Any]]:
        return [safe_copy(v) for v in self._by_id.values()]


# Frozen fixture for 运营导师体系.  Identity matches Formal Project id from
# the read-only catalog; the fixture does not write that row.
MENTOR_SYSTEM_FIXTURE = {
    "project_id": "e81643f1-c06b-462f-8b12-9b6daea980f5",
    "project_name": "运营导师体系",
    "status": "active",
    "stage": "FY27 上线准备与导师续聘",
    "definition": "人类定义的运营导师体系正式项目",
}


def mentor_system_catalog() -> MemoryProjectCatalog:
    return MemoryProjectCatalog([MENTOR_SYSTEM_FIXTURE])


DEFAULT_WORKSPACE_DB = os.environ.get(
    "PAW_WORKSPACE_DB",
    os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db")),
)


class SqliteReadOnlyProjectCatalog:
    """Read-only Formal Project lookup.  Opens the DB in URI mode=ro."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, project_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id, name, description, status, stage, next_action_summary FROM project WHERE id = ?",
                (text(project_id),),
            ).fetchone()
            return _row_to_project(row) if row else None
        finally:
            conn.close()

    def find_by_name(self, name: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id, name, description, status, stage, next_action_summary FROM project WHERE name = ?",
                (text(name),),
            ).fetchone()
            return _row_to_project(row) if row else None
        finally:
            conn.close()

    def list_projects(self) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, name, description, status, stage, next_action_summary FROM project"
            ).fetchall()
            return [_row_to_project(row) for row in rows]
        finally:
            conn.close()

    def list_todo_refs(self, project_id: str) -> list[dict[str, Any]]:
        """Reference-only Todo listing.  Does not mutate Todo rows."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, title, status, priority FROM todo WHERE project_id = ?",
                (text(project_id),),
            ).fetchall()
            return [
                {"todo_id": row["id"], "title": row["title"], "status": row["status"], "priority": row["priority"]}
                for row in rows
            ]
        finally:
            conn.close()


def default_project_catalog(db_path: str | None = None) -> SqliteReadOnlyProjectCatalog:
    """Read-only Formal Project catalog from the live workspace DB."""
    return SqliteReadOnlyProjectCatalog(db_path or DEFAULT_WORKSPACE_DB)


def _row_to_project(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "project_id": row["id"],
        "project_name": row["name"],
        "definition": row["description"],
        "status": row["status"],
        "stage": row["stage"],
        "next_action_summary": row["next_action_summary"],
        "formal": True,
    }


def _catalog_record(raw: Mapping[str, Any]) -> dict[str, Any]:
    project_id = text(raw.get("project_id") or raw.get("id"))
    project_name = text(raw.get("project_name") or raw.get("name"))
    if not project_id or not project_name:
        raise ValueError("catalog project requires project_id and project_name")
    return {
        "project_id": project_id,
        "project_name": project_name,
        "definition": raw.get("definition") or raw.get("description"),
        "status": raw.get("status"),
        "stage": raw.get("stage"),
        "formal": True,
    }


def _empty_snapshot(project_id: str, project_name: str, definition: Any) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "project_name": project_name,
        "definition": definition,
        "timeline": [],
        "stages": [],
        "effective_period": None,
        "decisions": [],
        "resolved_issues": [],
        "unresolved_issues": [],
        "linked_source_versions": [],
        "linked_knowledge_topics": [],
        "linked_metric_assets": [],
        "linked_todos": [],
        "roles": [],
        "stakeholders": [],
        "risks": [],
        "conflicts": [],
        "ambiguities": [],
        "current_state": None,
        "next_action": {
            "evidence_fact": None,
            "gpt_suggestion": None,
            "suggestion_source": "NONE",
        },
        "version_relations": [],
        "evidence": [],
        "updated_at": utc_now(),
        "created_at": utc_now(),
        "formal_project_mutated": False,
    }


def _merge_unique_maps(existing: list[Any], incoming: Any, key: str) -> list[Any]:
    rows = [dict(x) for x in existing if isinstance(x, Mapping)]
    seen = {text(x.get(key)) for x in rows if text(x.get(key))}
    additions = incoming if isinstance(incoming, list) else ([] if incoming in (None, "") else [incoming])
    for raw in additions:
        if isinstance(raw, Mapping):
            item = dict(raw)
            ident = text(item.get(key))
            if ident and ident in seen:
                # Merge source_version_ids if both sides carry them.
                for row in rows:
                    if text(row.get(key)) == ident:
                        if item.get("source_version_ids") or row.get("source_version_ids"):
                            row["source_version_ids"] = unique([
                                *row.get("source_version_ids", []),
                                *item.get("source_version_ids", []),
                            ])
                        if item.get("evidence"):
                            row.setdefault("evidence", [])
                            row["evidence"] = [*row["evidence"], *item["evidence"]]
                        break
                continue
            if ident:
                seen.add(ident)
            rows.append(item)
        else:
            value = text(raw)
            if value and value not in seen:
                seen.add(value)
                rows.append({key: value})
    return rows


class ProjectBuilder:
    def __init__(self, store: LockedJsonStore, catalog: FormalProjectCatalog):
        self.store = store
        self.catalog = catalog

    def _blank(self) -> dict[str, Any]:
        return {
            "schema": "paw-gpt-brain-project-builder/1",
            "layer_schema_version": LAYER_SCHEMA_VERSION,
            "formal_state_write": False,
            "project_contexts": {},
            "project_proposals": {},
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }

    def _data(self) -> dict[str, Any]:
        data = self.store.load()
        if not data:
            data = self._blank()
        data.setdefault("project_contexts", {})
        data.setdefault("project_proposals", {})
        return data

    def _resolve_formal(self, project_id: str, project_name: str) -> dict[str, Any] | None:
        if project_id:
            found = self.catalog.get(project_id)
            if found:
                return found
        if project_name:
            found = self.catalog.find_by_name(project_name)
            if found:
                return found
        return None

    def apply_update(self, update: Mapping[str, Any], *, source_version_id: str) -> dict[str, Any]:
        project_id = text(update.get("project_id"))
        project_name = text(update.get("project_name"))
        operation = text(update.get("operation") or "UPDATE").upper()
        evidence_raw = update.get("evidence") if isinstance(update.get("evidence"), list) else []
        filled = []
        for ref in evidence_raw:
            if not isinstance(ref, Mapping):
                continue
            item = dict(ref)
            if not item.get("source_version_id"):
                item["source_version_id"] = source_version_id
            filled.append(item)
        if filled:
            ok, reason, _refs = validate_evidence(filled)
            if not ok:
                raise ValueError(reason)

        formal = self._resolve_formal(project_id, project_name)
        if formal is not None and not _formal_project_is_current_business(formal):
            # P0: an existing but archived / non-business Formal Project may
            # neither be updated as Current Project Context nor be treated as
            # absent - treating it as absent would mint a PROJECT_PROPOSAL for
            # a Project that already exists.  Fail closed and disclose.
            return {
                "status": "SKIPPED_INELIGIBLE_PROJECT",
                "kind": "PROJECT_CONTEXT",
                "reason": "FORMAL_PROJECT_NOT_CURRENT_BUSINESS",
                "project_id": formal.get("project_id"),
                "project_name": formal.get("project_name"),
                "formal_state_bypass": 0,
            }

        if operation == "CREATE" or not formal:
            return self._proposal(update, source_version_id=source_version_id, filled_evidence=filled)

        missing_evidence = _factual_keys_without_evidence(update, filled)
        if missing_evidence:
            # P1: a factual claim about the Current Project without Evidence
            # must never be merged into a draft snapshot.
            raise ValueError(
                "FACTUAL_EVIDENCE_REQUIRED: " + ",".join(missing_evidence)
                + " (source_version_id + evidence_locator required)"
            )

        pid = formal["project_id"]
        pname = formal["project_name"]
        with self.store.mutate() as data:
            if not data:
                data.update(self._blank())
            snapshot = data["project_contexts"].get(pid)
            if snapshot is None:
                snapshot = _empty_snapshot(pid, pname, formal.get("definition"))
                snapshot["definition"] = update.get("definition") or formal.get("definition")
                data["project_contexts"][pid] = snapshot
            self._merge_snapshot(snapshot, update, source_version_id, filled)
            snapshot["updated_at"] = utc_now()
            data["updated_at"] = utc_now()
            return {"status": "UPDATED", "kind": "PROJECT_CONTEXT", "record": safe_copy(snapshot)}

    def _proposal(
        self,
        update: Mapping[str, Any],
        *,
        source_version_id: str,
        filled_evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        project_name = text(update.get("project_name"))
        fingerprint = hash_payload({
            "project_name": project_name,
            "proposed_definition": text(update.get("definition") or update.get("proposed_change")),
        })
        with self.store.mutate() as data:
            if not data:
                data.update(self._blank())
            record = data["project_proposals"].get(fingerprint)
            if record is None:
                record = {
                    "id": "project-proposal:" + fingerprint[:24],
                    "fingerprint": fingerprint,
                    "operation": "PROJECT_PROPOSAL",
                    "user_decision_required": True,
                    "project_id": None,
                    "project_name": project_name or None,
                    "proposed_definition": update.get("definition") or update.get("proposed_change"),
                    "source_version_ids": [],
                    "evidence": [],
                    "context": {},
                    "created_at": utc_now(),
                    "formal_project_created": False,
                }
                data["project_proposals"][fingerprint] = record
            record["source_version_ids"] = unique([*record.get("source_version_ids", []), source_version_id])
            if filled_evidence:
                record["evidence"] = [*record.get("evidence", []), *filled_evidence]
            context = update.get("context")
            if isinstance(context, Mapping):
                record.setdefault("context", {}).update(safe_copy(context))
            record["updated_at"] = utc_now()
            data["updated_at"] = utc_now()
            return {
                "status": "USER_DECISION_REQUIRED",
                "kind": "PROJECT_PROPOSAL",
                "record": safe_copy(record),
            }

    def _merge_snapshot(
        self,
        snapshot: dict[str, Any],
        update: Mapping[str, Any],
        source_version_id: str,
        evidence: list[dict[str, Any]],
    ) -> None:
        snapshot["linked_source_versions"] = unique([
            *snapshot.get("linked_source_versions", []),
            source_version_id,
            *(update.get("linked_source_versions") or []),
        ])
        if evidence:
            snapshot["evidence"] = [*snapshot.get("evidence", []), *evidence]
        if update.get("definition"):
            snapshot["definition"] = update.get("definition")
        context = update.get("context") if isinstance(update.get("context"), Mapping) else {}

        snapshot["timeline"] = _merge_unique_maps(
            snapshot.get("timeline") or [],
            update.get("timeline") or context.get("timeline"),
            "event_id",
        )
        snapshot["stages"] = _merge_unique_maps(
            snapshot.get("stages") or [],
            update.get("stages") or context.get("stages"),
            "stage",
        )
        snapshot["decisions"] = _merge_unique_maps(
            snapshot.get("decisions") or [],
            update.get("decisions") or context.get("decisions"),
            "decision_id",
        )
        snapshot["resolved_issues"] = _merge_unique_maps(
            snapshot.get("resolved_issues") or [],
            update.get("resolved_issues") or context.get("resolved_issues"),
            "issue_id",
        )
        snapshot["unresolved_issues"] = _merge_unique_maps(
            snapshot.get("unresolved_issues") or [],
            update.get("unresolved_issues") or context.get("unresolved_issues"),
            "issue_id",
        )
        snapshot["linked_knowledge_topics"] = _merge_unique_maps(
            snapshot.get("linked_knowledge_topics") or [],
            update.get("linked_knowledge_topics") or context.get("linked_knowledge_topics"),
            "topic",
        )
        snapshot["linked_metric_assets"] = _merge_unique_maps(
            snapshot.get("linked_metric_assets") or [],
            update.get("linked_metric_assets") or context.get("linked_metric_assets"),
            "metric",
        )
        snapshot["linked_todos"] = _merge_unique_maps(
            snapshot.get("linked_todos") or [],
            update.get("linked_todos") or context.get("linked_todos"),
            "todo_id",
        )
        snapshot["roles"] = _merge_unique_maps(
            snapshot.get("roles") or [],
            update.get("roles") or context.get("roles"),
            "role",
        )
        snapshot["stakeholders"] = _merge_unique_maps(
            snapshot.get("stakeholders") or [],
            update.get("stakeholders") or context.get("stakeholders"),
            "name",
        )
        snapshot["risks"] = _merge_unique_maps(
            snapshot.get("risks") or [],
            update.get("risks") or context.get("risks"),
            "risk_id",
        )
        snapshot["conflicts"] = _merge_unique_maps(
            snapshot.get("conflicts") or [],
            update.get("conflicts") or context.get("conflicts"),
            "conflict_id",
        )
        snapshot["ambiguities"] = _merge_unique_maps(
            snapshot.get("ambiguities") or [],
            update.get("ambiguities") or context.get("ambiguities"),
            "ambiguity_id",
        )
        snapshot["version_relations"] = _merge_unique_maps(
            snapshot.get("version_relations") or [],
            update.get("version_relations") or context.get("version_relations"),
            "source_version_id",
        )
        if update.get("current_state") or context.get("current_state"):
            snapshot["current_state"] = update.get("current_state") or context.get("current_state")
        next_action = update.get("next_action") or context.get("next_action")
        if isinstance(next_action, Mapping):
            snapshot["next_action"] = {
                "evidence_fact": next_action.get("evidence_fact"),
                "gpt_suggestion": next_action.get("gpt_suggestion"),
                "suggestion_source": text(next_action.get("suggestion_source") or "GPT") if next_action.get("gpt_suggestion") else "NONE",
            }
        elif text(next_action):
            # Bare string is treated as GPT suggestion, not an evidence fact.
            snapshot["next_action"] = {
                "evidence_fact": None,
                "gpt_suggestion": text(next_action),
                "suggestion_source": "GPT",
            }
        if update.get("effective_period") or context.get("effective_period"):
            snapshot["effective_period"] = update.get("effective_period") or context.get("effective_period")

        # Dedup stakeholders without evidence: drop entries that have neither
        # evidence nor source_version_ids (roles/stakeholders require evidence).
        # P1-2: a role NAME is not Evidence.  Keeping `or r.get("role")` let
        # any GPT-invented role survive merely by having a name, contradicting
        # the comment above and the stakeholders rule below.
        snapshot["roles"] = [r for r in snapshot["roles"] if r.get("evidence") or r.get("source_version_ids")]
        snapshot["stakeholders"] = [
            s for s in snapshot["stakeholders"]
            if s.get("evidence") or s.get("source_version_ids")
        ]

    def get_context(self, project_id: str, *, redact: bool = True) -> dict[str, Any] | None:
        data = self._data()
        record = data["project_contexts"].get(text(project_id))
        if record is None:
            return None
        payload = safe_copy(record)
        payload["formal_project_mutated"] = False
        return redact_mapping(payload) if redact else payload

    def list_contexts(self, *, redact: bool = True) -> dict[str, Any]:
        data = self._data()
        items = [safe_copy(v) for v in data["project_contexts"].values()]
        payload = {"count": len(items), "project_contexts": items, "formal_state_write": False}
        return redact_mapping(payload) if redact else payload

    def list_proposals(self) -> dict[str, Any]:
        data = self._data()
        items = [safe_copy(v) for v in data["project_proposals"].values()]
        return {"count": len(items), "project_proposals": items, "formal_project_created": False}

    def _draft_counts(self, snapshot: Mapping[str, Any]) -> dict[str, int]:
        return {
            "timeline": len(snapshot.get("timeline") or []),
            "stages": len(snapshot.get("stages") or []),
            "decisions": len(snapshot.get("decisions") or []),
            "resolved_issues": len(snapshot.get("resolved_issues") or []),
            "unresolved_issues": len(snapshot.get("unresolved_issues") or []),
            "linked_source_versions": len(snapshot.get("linked_source_versions") or []),
            "linked_knowledge_topics": len(snapshot.get("linked_knowledge_topics") or []),
            "linked_metric_assets": len(snapshot.get("linked_metric_assets") or []),
            "linked_todos": len(snapshot.get("linked_todos") or []),
            "roles": len(snapshot.get("roles") or []),
            "stakeholders": len(snapshot.get("stakeholders") or []),
            "risks": len(snapshot.get("risks") or []),
            "conflicts": len(snapshot.get("conflicts") or []),
            "ambiguities": len(snapshot.get("ambiguities") or []),
            "version_relations": len(snapshot.get("version_relations") or []),
        }

    def _enrich_draft_context(
        self,
        snapshot: Mapping[str, Any],
        *,
        redact: bool = True,
    ) -> dict[str, Any]:
        project_id = text(snapshot.get("project_id"))
        formal = self.catalog.get(project_id) if project_id else None
        payload = safe_copy(snapshot)
        payload["authority_level"] = "draft"
        payload["is_canonical"] = False
        payload["draft_counts"] = self._draft_counts(snapshot)
        payload["formal_project_mutated"] = False
        if formal:
            payload["formal_project"] = {
                **safe_copy(formal),
                "authority_level": "canonical",
                "is_canonical": True,
            }
        else:
            payload["formal_project"] = None
        return redact_mapping(payload) if redact else payload

    def get_draft_context(
        self,
        project_id: str,
        *,
        redact: bool = True,
    ) -> dict[str, Any] | None:
        record = self.get_context(project_id, redact=False)
        if record is None:
            return None
        return self._enrich_draft_context(record, redact=redact)

    def search_contexts(
        self,
        *,
        query: str | None = None,
        project_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
        redact: bool = True,
    ) -> dict[str, Any]:
        data = self._data()
        items = [safe_copy(v) for v in data["project_contexts"].values()]
        if project_id:
            items = [item for item in items if text(item.get("project_id")) == text(project_id)]
        if query:
            q = text(query).casefold()
            items = [
                item for item in items
                if q in text(item.get("project_name")).casefold()
                or q in text(item.get("project_id")).casefold()
                or q in text(item.get("definition")).casefold()
            ]
        total = len(items)
        page = items[offset: offset + max(1, min(limit, 200))]
        enriched = [self._enrich_draft_context(item, redact=redact) for item in page]
        return {
            "count": len(page),
            "total": total,
            "offset": offset,
            "limit": limit,
            "project_contexts": enriched,
            "authority_level": "draft",
            "is_canonical": False,
            "formal_state_write": False,
        }

    def list_draft_contexts(self, *, redact: bool = True) -> dict[str, Any]:
        data = self._data()
        items = [self._enrich_draft_context(v, redact=redact) for v in data["project_contexts"].values()]
        return {
            "count": len(items),
            "project_contexts": items,
            "authority_level": "draft",
            "is_canonical": False,
            "formal_state_write": False,
        }

    def cleanup_formal_project_proposals(self) -> dict[str, int]:
        """Drop proposals that match a human-defined Formal Project by name."""
        removed = 0
        with self.store.mutate() as data:
            if not data:
                return {"removed": 0}
            keep: dict[str, Any] = {}
            for fingerprint, record in (data.get("project_proposals") or {}).items():
                name = text(record.get("project_name"))
                if name and self.catalog.find_by_name(name):
                    removed += 1
                    continue
                keep[fingerprint] = record
            data["project_proposals"] = keep
            if removed:
                data["updated_at"] = utc_now()
        return {"removed": removed}

    def hydrate_from_brain(
        self,
        *,
        project_consolidations: Mapping[str, Any],
        knowledge_topics: Mapping[str, Any] | None = None,
        metric_proposals: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build query context from already-recorded Brain objects.  No Formal writes."""
        applied = 0
        proposals = 0
        for raw in (project_consolidations or {}).values():
            if not isinstance(raw, Mapping):
                continue
            svids = raw.get("source_version_ids") or ["unknown"]
            knowledge_links = []
            for topic in (knowledge_topics or {}).values():
                if not isinstance(topic, Mapping):
                    continue
                if text(topic.get("project_or_domain")) in {text(raw.get("project_id")), text(raw.get("project_name"))}:
                    knowledge_links.append({
                        "topic": topic.get("canonical_topic"),
                        "source_version_ids": topic.get("source_version_ids") or [],
                    })
            metric_links = []
            for metric in (metric_proposals or {}).values():
                if not isinstance(metric, Mapping):
                    continue
                if text(metric.get("project")) in {text(raw.get("project_id")), text(raw.get("project_name"))}:
                    metric_links.append({
                        "metric": metric.get("metric"),
                        "period": metric.get("period"),
                        "source_version_ids": metric.get("source_version_ids") or [],
                    })
            update = {
                "project_id": raw.get("project_id"),
                "project_name": raw.get("project_name"),
                "operation": raw.get("operation") or "UPDATE",
                "context": raw.get("context") or {},
                "linked_source_versions": svids,
                "linked_knowledge_topics": knowledge_links,
                "linked_metric_assets": metric_links,
                "current_state": (raw.get("context") or {}).get("current_state"),
                "evidence": [
                    {"source_version_id": svid, "evidence_locator": eid}
                    for svid in svids
                    for eid in (raw.get("evidence_ids") or ["brain:project_consolidation"])
                ],
            }
            result = self.apply_update(update, source_version_id=svids[0])
            if result["kind"] == "PROJECT_PROPOSAL":
                proposals += 1
            else:
                applied += 1
        return {"contexts_updated": applied, "proposals": proposals}

    def rehydrate_from_brain(
        self,
        *,
        project_consolidations: Mapping[str, Any],
        knowledge_topics: Mapping[str, Any] | None = None,
        metric_proposals: Mapping[str, Any] | None = None,
        cleanup_formal_proposals: bool = True,
    ) -> dict[str, Any]:
        """Deterministic, idempotent rebuild from existing Brain consolidations."""
        before = self.snapshot()
        hydrated = self.hydrate_from_brain(
            project_consolidations=project_consolidations,
            knowledge_topics=knowledge_topics,
            metric_proposals=metric_proposals,
        )
        cleanup = self.cleanup_formal_project_proposals() if cleanup_formal_proposals else {"removed": 0}
        after = self.snapshot()
        return {
            "before": before,
            "after": after,
            "hydrated": hydrated,
            "cleanup": cleanup,
            "formal_state_write": False,
        }

    def snapshot(self) -> dict[str, Any]:
        data = self._data()
        return {
            "context_count": len(data.get("project_contexts") or {}),
            "proposal_count": len(data.get("project_proposals") or {}),
            "formal_state_write": False,
        }


__all__ = [
    "DEFAULT_WORKSPACE_DB",
    "MENTOR_SYSTEM_FIXTURE",
    "MemoryProjectCatalog",
    "ProjectBuilder",
    "SqliteReadOnlyProjectCatalog",
    "default_project_catalog",
    "mentor_system_catalog",
]
