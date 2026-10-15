#!/usr/bin/python3
"""PROJECT-first outcome and relation layer.

This module is intentionally deterministic.  It consumes the already frozen
Luna semantic state, Brain findings, canonical Project/Knowledge rows and
existing governed links.  It never opens or re-reads the 710 raw Sources and
it never writes canonical state directly.  Callers hand the returned payloads
to the existing policy committer.
"""

from __future__ import annotations

import os


import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from current_context_policy import (
        is_current_business_project,
        is_current_confirmed_knowledge,
    )
except ImportError:  # pragma: no cover - alternate import root
    from workspace_api.current_context_policy import (
        is_current_business_project,
        is_current_confirmed_knowledge,
    )


def canonical_knowledge_refs(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """P0-3: only current + confirmed-latest Knowledge may be emitted as
    canonical Knowledge refs.  Provisional / needs_review / deprecated rows are
    excluded here; they stay intact in knowledge_page - nothing is deleted.
    """
    refs: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if not is_current_confirmed_knowledge(row):
            continue
        refs.append({
            "knowledge_id": row.get("id"),
            "title": row.get("title"),
            "status": row.get("status"),
        })
    return refs


def _read_knowledge_rows(conn: sqlite3.Connection, pid: str) -> list[dict[str, Any]]:
    """Project-linked Knowledge rows with their authority columns.

    ``confirmed_revision_no`` and the ``knowledge_revision`` table belong to the
    Knowledge Confirmation Gate, which an additive migration
    (``_ensure_knowledge_confirmation_columns``) applies to existing databases.
    A database that only replays ``SCHEMA_SQL`` - or a read-only fixture - does
    not carry them, so the governed read must degrade rather than raise.

    Degrading is FAIL CLOSED: with no ``confirmed_revision_no`` the row can
    never satisfy CURRENT_CONFIRMED, so it stays out of Current Project Context.
    """
    governed = """SELECT kp.id, kp.title, kp.knowledge_type, kp.status, kp.updated_at,
                  kp.confirmed_revision_no,
                  (SELECT MAX(revision_no) FROM knowledge_revision kr
                    WHERE kr.knowledge_id = kp.id) AS latest_revision_no
           FROM knowledge_page kp JOIN knowledge_project_link kpl ON kpl.knowledge_id=kp.id
           WHERE kpl.project_id=? ORDER BY kp.title"""
    base = """SELECT kp.id, kp.title, kp.knowledge_type, kp.status, kp.updated_at
           FROM knowledge_page kp JOIN knowledge_project_link kpl ON kpl.knowledge_id=kp.id
           WHERE kpl.project_id=? ORDER BY kp.title"""
    try:
        rows = conn.execute(governed, (pid,)).fetchall()
    except sqlite3.OperationalError:
        rows = conn.execute(base, (pid,)).fetchall()
    return [dict(r) for r in rows]


try:
    from paw_source_version_governance import (
        clear_resynthesis_mark,
        ensure_schema as ensure_governance_schema,
        filter_current_source_versions,
        identify_stale_outcome_refs,
        list_impacted_resynthesis_projects,
        read_project_version_context,
        seed_unresolved_family,
    )
except ImportError:
    ensure_governance_schema = None  # type: ignore[assignment,misc]
    filter_current_source_versions = None  # type: ignore[assignment,misc]
    identify_stale_outcome_refs = None  # type: ignore[assignment,misc]
    read_project_version_context = None  # type: ignore[assignment,misc]
    seed_unresolved_family = None  # type: ignore[assignment,misc]
    list_impacted_resynthesis_projects = None  # type: ignore[assignment,misc]
    clear_resynthesis_mark = None  # type: ignore[assignment,misc]

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))
DEFAULT_BRAIN = "/var/lib/paw-mcp-gateway/gpt_brain_bootstrap.json"
DEFAULT_BUILDER = "/var/lib/paw-mcp-gateway/gpt_brain_project_builder.json"
DEFAULT_BUNDLES = "/var/lib/paw-mcp-gateway/paw_gpt_review_bundles.json"

OUTCOME_SCHEMA = "paw-project-outcome/1"
RELATION_SCHEMA = "paw-project-relation/1"
MACHINE_FIELDS = frozenset({
    "current_status", "phase", "executive_summary", "objectives", "scope",
    "latest_progress", "completed_milestones", "open_items", "key_decisions",
    "current_effective_rules", "risks", "blockers", "ambiguities", "key_metrics",
    "canonical_knowledge_refs", "source_evidence_refs", "related_projects",
    "unresolved_items", "synthesized_at", "input_watermark", "synthesis_revision",
    "evidence_fingerprint", "evidence_sufficient", "quality_gate",
})


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _unique(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _text(value)
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical(value: Any) -> str:
    return _json(value)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


_SOURCE_VERSION_CONTRACT = re.compile(r"^(.+?)@(\d+)$")


def logical_source_identity(source_version_id: Any, object_id: Any = None) -> str:
    """Resolve the stable Source family without guessing unknown formats."""
    object_text = _text(object_id)
    if object_text:
        return "object:" + object_text
    sid = _text(source_version_id)
    match = _SOURCE_VERSION_CONTRACT.fullmatch(sid)
    if match:
        return "object:" + match.group(1)
    # Unknown historical ids remain their own family.
    return "version:" + sid if sid else ""


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(db_path: str = DEFAULT_DB) -> dict[str, Any]:
    """Install only the outcome/relation schema; no canonical content writes."""
    conn = sqlite3.connect(db_path, timeout=20)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS project_outcome (
            project_id TEXT PRIMARY KEY REFERENCES project(id),
            schema_version TEXT NOT NULL,
            current_status TEXT,
            phase TEXT,
            executive_summary TEXT NOT NULL,
            objectives_json TEXT NOT NULL DEFAULT '[]',
            scope_json TEXT NOT NULL DEFAULT '{}',
            latest_progress_json TEXT NOT NULL DEFAULT '[]',
            completed_milestones_json TEXT NOT NULL DEFAULT '[]',
            open_items_json TEXT NOT NULL DEFAULT '[]',
            key_decisions_json TEXT NOT NULL DEFAULT '[]',
            current_effective_rules_json TEXT NOT NULL DEFAULT '[]',
            risks_json TEXT NOT NULL DEFAULT '[]',
            blockers_json TEXT NOT NULL DEFAULT '[]',
            ambiguities_json TEXT NOT NULL DEFAULT '[]',
            key_metrics_json TEXT NOT NULL DEFAULT '[]',
            canonical_knowledge_refs_json TEXT NOT NULL DEFAULT '[]',
            source_evidence_refs_json TEXT NOT NULL DEFAULT '[]',
            related_projects_json TEXT NOT NULL DEFAULT '[]',
            unresolved_items_json TEXT NOT NULL DEFAULT '{}',
            synthesized_at TEXT NOT NULL,
            input_watermark TEXT NOT NULL,
            synthesis_revision INTEGER NOT NULL DEFAULT 1,
            evidence_fingerprint TEXT NOT NULL,
            evidence_sufficient INTEGER NOT NULL DEFAULT 0,
            quality_gate TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_project_outcome_watermark ON project_outcome(input_watermark);
        CREATE TABLE IF NOT EXISTS project_relation (
            relation_id TEXT PRIMARY KEY,
            schema_version TEXT NOT NULL,
            from_project_id TEXT NOT NULL REFERENCES project(id),
            to_project_id TEXT NOT NULL REFERENCES project(id),
            relation_type TEXT NOT NULL,
            summary TEXT NOT NULL,
            evidence_refs_json TEXT NOT NULL DEFAULT '[]',
            confidence TEXT NOT NULL,
            evidence_sufficiency TEXT NOT NULL,
            temporal_state TEXT NOT NULL DEFAULT 'current',
            revision INTEGER NOT NULL DEFAULT 1,
            input_watermark TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_project_relation_from ON project_relation(from_project_id);
        CREATE INDEX IF NOT EXISTS idx_project_relation_to ON project_relation(to_project_id);
        """)
        conn.commit()
        if ensure_governance_schema is not None:
            ensure_governance_schema(db_path)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {"status": "READY", "project_outcome": "project_outcome" in tables, "project_relation": "project_relation" in tables, "formal_state_bypass": 0}
    finally:
        conn.close()


def _read_json(path: str, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _ro(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _project_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM project ORDER BY name, id").fetchall()]


def _project_eligibility_select(conn: sqlite3.Connection) -> str:
    """Column list for Project eligibility reads.

    ``status`` exists in every governed schema; the guard only keeps a legacy
    or partially migrated database readable.  A schema without ``status``
    cannot express "archived" at all, so the name-based exclusions still apply
    and the canonical rule fails every such row closed.
    """
    columns = {str(r[1]) for r in conn.execute("PRAGMA table_info(project)").fetchall()}
    return "id, name" + (", status" if "status" in columns else "")


def _current_business_project_ids(conn: sqlite3.Connection) -> set[str]:
    """Project ids admitted to default Current-State impact resolution.

    The rule is NOT re-defined here: it delegates to the single canonical
    ``is_current_business_project`` policy.  Archived Projects, the personal
    life Project and WPS evidence containers therefore never enter an impact
    set, a resynthesis set or a relation scope.
    """
    admitted: set[str] = set()
    select = _project_eligibility_select(conn)
    for row in conn.execute(f"SELECT {select} FROM project").fetchall():
        record = dict(row)
        if is_current_business_project(record):
            admitted.add(_text(record.get("id")))
    return admitted


def _source_evidence(brain: Mapping[str, Any], source_ids: Iterable[str], evidence_ids: Iterable[str]) -> list[dict[str, str]]:
    nodes = brain.get("evidence_nodes") or {}
    out: list[dict[str, str]] = []
    source_ids = _unique(source_ids)
    for eid in _unique(evidence_ids):
        node = nodes.get(eid) if isinstance(nodes, Mapping) else None
        if isinstance(node, Mapping):
            sid = _text(node.get("source_version_id")) or (source_ids[0] if source_ids else "")
            locator = _text(node.get("evidence_locator") or node.get("locator") or eid)
        else:
            sid, locator = (source_ids[0] if source_ids else ""), eid
        if sid and locator:
            out.append({"source_version_id": sid, "evidence_locator": locator, "evidence_id": eid})
    if not out and source_ids:
        out.append({"source_version_id": source_ids[0], "evidence_locator": "semantic_results", "evidence_id": "semantic_results"})
    return out[:80]


def _project_inputs(
    project: Mapping[str, Any], brain: Mapping[str, Any], builder: Mapping[str, Any], bundles: Mapping[str, Any], conn: sqlite3.Connection,
) -> dict[str, Any]:
    pid, name = _text(project.get("id")), _text(project.get("name"))
    consolidations = [x for x in (brain.get("project_consolidations") or {}).values() if isinstance(x, Mapping) and _text(x.get("project_id")) == pid]
    topics = [x for x in (brain.get("knowledge_topics") or {}).values() if isinstance(x, Mapping) and _text(x.get("project_or_domain")) in {pid, name}]
    metrics = []
    consolidation_sources = _unique(s for x in consolidations for s in (x.get("source_version_ids") or []))
    for x in (brain.get("metric_proposals") or {}).values():
        if not isinstance(x, Mapping):
            continue
        targets = {_text(x.get(k)) for k in ("project_id", "project", "project_or_domain", "paw_project_id", "paw_project_name")}
        source_match = bool(set(x.get("source_version_ids") or []) & set(consolidation_sources))
        if pid in targets or name in targets or source_match:
            metrics.append(x)
    ctx = ((builder.get("project_contexts") or {}).get(pid) or {})
    knowledge_rows = _read_knowledge_rows(conn, pid)
    try:
        linked_sources = [dict(r) for r in conn.execute(
            """SELECT wp.id, wp.title, wp.source_path, wp.source_origin, wp.canonical_state
               FROM wiki_page wp JOIN knowledge_source_link ksl ON ksl.source_page_id=wp.id
               JOIN knowledge_project_link kpl ON kpl.knowledge_id=ksl.knowledge_id
               WHERE kpl.project_id=? ORDER BY wp.title LIMIT 100""", (pid,)).fetchall()]
    except sqlite3.OperationalError:
        # The source-link tables are optional in small readback fixtures; the
        # semantic Project Builder inputs above remain authoritative.
        linked_sources = []
    bundle_items = [x for x in bundles.values() if isinstance(x, Mapping) and pid in _bundle_project_ids(x)]
    builder_source_ids = _unique(ctx.get("linked_source_versions") or [])
    builder_topics = []
    for raw in ctx.get("linked_knowledge_topics") or []:
        if isinstance(raw, Mapping):
            topic = _text(raw.get("topic") or raw.get("canonical_topic"))
            ids = _unique(raw.get("source_version_ids") or [])
        else:
            topic, ids = _text(raw), []
        if topic:
            builder_topics.append({"canonical_topic": topic, "source_version_ids": ids})
            builder_source_ids.extend(ids)
    context_evidence = [dict(x) for x in (ctx.get("evidence") or []) if isinstance(x, Mapping)]
    source_ids = _unique([
        *consolidation_sources,
        *(s for x in topics for s in (x.get("source_version_ids") or [])),
        *(s for x in metrics for s in (x.get("source_version_ids") or [])),
        *builder_source_ids,
    ])
    version_filter_meta: dict[str, Any] = {"mode": "current"}
    if filter_current_source_versions is not None and source_ids:
        source_ids, version_filter_meta = filter_current_source_versions(source_ids, conn)
    return {
        "consolidations": consolidations, "topics": topics, "metrics": metrics,
        "context": ctx, "builder_topics": builder_topics,
        "context_evidence": context_evidence, "knowledge_rows": knowledge_rows,
        "linked_sources": linked_sources, "bundles": bundle_items,
        "source_ids": source_ids,
        "version_filter_meta": version_filter_meta,
    }


def _bundle_project_ids(bundle: Mapping[str, Any]) -> list[str]:
    ids: list[str] = []
    for key in ("project_consolidations", "project_proposals", "knowledge_proposals", "metric_proposals"):
        for x in bundle.get(key) or []:
            if isinstance(x, Mapping):
                for k in ("project_id", "project_or_domain"):
                    if _text(x.get(k)):
                        ids.append(_text(x.get(k)))
    return _unique(ids)


def _quality_summary(name: str, project: Mapping[str, Any], inp: Mapping[str, Any], evidence: list[dict[str, Any]]) -> tuple[str, str, bool]:
    topics = _unique(x.get("canonical_topic") for x in inp["topics"])
    progress = [x for x in (inp["context"].get("timeline") or inp["context"].get("stages") or []) if isinstance(x, Mapping)]
    # PAW v1.2 phase 3 (technical debt D): ``project`` is deliberately NOT read
    # here any more.  The previous round's ``latest_progress_summary`` is not
    # evidence, and feeding it back in made this function non-idempotent: it
    # previously appended "保留既有项目记录：" + old[:160] to the new summary,
    # and because that same string is also the source of
    # ``project_outcome.executive_summary`` and of the Project Registry
    # description, the description grew on every closure round.  The append
    # never influenced ``sufficient`` (it only padded the text), so dropping it
    # removes the inflation without weakening the evidence gate: a project
    # without consolidations/topics/timeline still reports
    # PASS_EVIDENCE_INSUFFICIENT below.
    pieces = []
    if inp["consolidations"]:
        pieces.append(f"已完成 {len(inp['consolidations'])} 条项目语义归并，覆盖 {len(inp['source_ids'])} 个 SourceVersion。")
    if topics:
        pieces.append("当前已归纳主题：" + "、".join(topics[:6]) + "。")
    if progress:
        pieces.append("已有时间线/阶段事实 " + str(len(progress)) + " 条。")
    if not pieces:
        return (f"当前没有足够的已记录语义证据形成项目综合；已建立基线并标记为证据不足，等待后续 Source/Knowledge 证据。", "PASS_EVIDENCE_INSUFFICIENT", False)
    return (f"{name}：" + "".join(pieces), "PASS", True)


def synthesize_project(project: Mapping[str, Any], brain: Mapping[str, Any], builder: Mapping[str, Any], bundles: Mapping[str, Any], conn: sqlite3.Connection, *, now: str | None = None) -> dict[str, Any]:
    now = now or _now()
    pid, name = _text(project.get("id")), _text(project.get("name"))
    inp = _project_inputs(project, brain, builder, bundles, conn)
    source_ids = inp["source_ids"]
    evidence_ids = [eid for x in inp["consolidations"] for eid in (x.get("evidence_ids") or [])]
    evidence = _source_evidence(brain, source_ids, evidence_ids)
    # Reuse exact Luna/Project Builder evidence locators.  This is bounded
    # semantic-state reuse; no frozen Source is enumerated or reopened.
    seen_evidence = {(x.get("source_version_id"), x.get("evidence_locator")) for x in evidence}
    for raw in inp["context_evidence"]:
        sid = _text(raw.get("source_version_id"))
        locator = _text(raw.get("evidence_locator") or raw.get("locator"))
        if not sid or not locator or (sid, locator) in seen_evidence:
            continue
        item = {"source_version_id": sid, "evidence_locator": locator}
        if _text(raw.get("evidence_id")):
            item["evidence_id"] = _text(raw.get("evidence_id"))
        evidence.append(item)
        seen_evidence.add((sid, locator))
    summary, quality, sufficient = _quality_summary(name, project, inp, evidence)
    ctx = inp["context"]
    timeline = list(ctx.get("timeline") or ctx.get("stages") or [])[:100]
    decisions = list(ctx.get("decisions") or [])[:100]
    risks = list(ctx.get("risks") or [])[:100]
    ambiguities = list(ctx.get("ambiguities") or [])[:100]
    open_items = list(ctx.get("unresolved_issues") or [])[:100]
    unresolved = {"terra": [], "opus": [], "user": [], "insufficient": []}
    for bundle in inp["bundles"]:
        state = _text(bundle.get("state")).upper()
        route = _text((bundle.get("risk_route") or {}).get("route")).upper()
        if route == "TERRA_REVIEW_REQUIRED" or state in {"GPT_REVIEW_REQUIRED", "REVIEW_PENDING"}:
            unresolved["terra"].append(bundle.get("id"))
        elif route == "OPUS_REVIEW_REQUIRED" or "OPUS" in state:
            unresolved["opus"].append(bundle.get("id"))
        elif route == "USER_DECISION_REQUIRED" or "USER" in state:
            unresolved["user"].append(bundle.get("id"))
        elif route == "INSUFFICIENT_EVIDENCE" or state == "INSUFFICIENT_EVIDENCE":
            unresolved["insufficient"].append(bundle.get("id"))
    knowledge_refs = canonical_knowledge_refs(inp["knowledge_rows"])
    knowledge_refs.extend({"canonical_topic": _text(x.get("canonical_topic")), "proposal_id": x.get("id"), "source_version_ids": list(x.get("source_version_ids") or [])} for x in inp["topics"][:80])
    knowledge_refs.extend({"canonical_topic": _text(x.get("canonical_topic")), "source_version_ids": list(x.get("source_version_ids") or []), "semantic_layer": "project_builder"} for x in inp["builder_topics"][:80])
    deduped_knowledge = []
    seen_knowledge = set()
    for ref in knowledge_refs:
        key = (ref.get("knowledge_id"), ref.get("canonical_topic"), tuple(ref.get("source_version_ids") or []))
        if key not in seen_knowledge:
            seen_knowledge.add(key)
            deduped_knowledge.append(ref)
    knowledge_refs = deduped_knowledge[:160]
    metrics = []
    for x in inp["metrics"][:120]:
        metrics.append({"metric": _text(x.get("metric") or x.get("name")), "value": x.get("value"), "period": x.get("period") or x.get("effective_period"), "unit": x.get("unit"), "definition": x.get("definition") or x.get("description"), "evidence_refs": _source_evidence(brain, x.get("source_version_ids") or source_ids, x.get("evidence_ids") or [])[:12]})
    outcome = {
        "schema_version": OUTCOME_SCHEMA, "project_id": pid,
        "current_status": _text(project.get("status")) or "unknown", "phase": _text(project.get("stage")),
        "executive_summary": summary, "objectives": ctx.get("objectives") or [], "scope": ctx.get("definition") or {},
        "latest_progress": [{"summary": summary, "source_version_ids": source_ids[:40], "evidence_refs": evidence[:20]}],
        "completed_milestones": timeline, "open_items": open_items, "key_decisions": decisions,
        "current_effective_rules": list(ctx.get("effective_rules") or []), "risks": risks,
        "blockers": list(ctx.get("blockers") or []), "ambiguities": ambiguities, "key_metrics": metrics,
        "canonical_knowledge_refs": knowledge_refs, "source_evidence_refs": evidence,
        "related_projects": [], "unresolved_items": {k: _unique(v) for k, v in unresolved.items()},
        "version_governance_meta": inp.get("version_filter_meta") or {},
        "synthesized_at": now, "input_watermark": _hash({"project_id": pid, "sources": source_ids, "topics": [x.get("id") for x in inp["topics"]], "metrics": [x.get("id") for x in inp["metrics"]], "knowledge_links": [x.get("id") for x in inp["knowledge_rows"]], "ctx": inp["context"]}),
        "synthesis_revision": 1, "evidence_fingerprint": _hash(evidence), "evidence_sufficient": sufficient,
        "quality_gate": quality,
    }
    return outcome


def outcome_payload(outcome: Mapping[str, Any], *, db_path: str = DEFAULT_DB) -> dict[str, Any]:
    """Flatten the semantic outcome for the canonical committer executor."""
    summary = _text(outcome.get("executive_summary"))
    if not summary:
        raise ValueError("project outcome cannot be written without an executive summary")
    payload: dict[str, Any] = {
        "id": outcome["project_id"],
        "latest_progress_summary": summary,
        "project_outcome": dict(outcome),
    }
    try:
        from paw_source_version_governance import project_description_projection
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            description, prov = project_description_projection(conn, outcome["project_id"], outcome)
            if description is not None:
                payload["description"] = description
                payload["description_provenance"] = prov
        finally:
            conn.close()
    except Exception:
        pass
    return payload


def quality_gate(outcome: Mapping[str, Any], project_name: str) -> tuple[bool, str]:
    summary = _text(outcome.get("executive_summary"))
    if not summary or summary == project_name or summary.strip("：: ") == project_name:
        return False, "empty_or_title_only_summary"
    if "当前没有足够" not in summary and not (outcome.get("source_evidence_refs") or outcome.get("canonical_knowledge_refs")):
        return False, "summary_has_no_evidence"
    return True, "passed"


def build_relations(projects: list[Mapping[str, Any]], outcomes: Mapping[str, Mapping[str, Any]], *, now: str | None = None) -> list[dict[str, Any]]:
    now = now or _now()
    rows: list[dict[str, Any]] = []
    for i, left in enumerate(projects):
        for right in projects[i + 1:]:
            a, b = outcomes.get(_text(left.get("id")), {}), outcomes.get(_text(right.get("id")), {})
            if not a or not b:
                continue
            sources_a = {x.get("source_version_id") for x in a.get("source_evidence_refs") or []}
            sources_b = {x.get("source_version_id") for x in b.get("source_evidence_refs") or []}
            knowledge_a = {x.get("knowledge_id") for x in a.get("canonical_knowledge_refs") or [] if x.get("knowledge_id")}
            knowledge_b = {x.get("knowledge_id") for x in b.get("canonical_knowledge_refs") or [] if x.get("knowledge_id")}
            common_sources = sorted(x for x in sources_a & sources_b if x)
            common_knowledge = sorted(x for x in knowledge_a & knowledge_b if x)
            if not common_sources and not common_knowledge:
                continue
            refs: list[dict[str, Any]] = []
            for source_id in common_sources[:20]:
                # Carry real semantic evidence locators from both sides.  A
                # synthetic relation marker is not sufficient evidence for a
                # governed cross-Project write.
                for outcome in (a, b):
                    for ref in outcome.get("source_evidence_refs") or []:
                        if not isinstance(ref, Mapping) or ref.get("source_version_id") != source_id:
                            continue
                        candidate = {
                            "source_version_id": source_id,
                            "evidence_locator": _text(ref.get("evidence_locator")),
                        }
                        if _text(ref.get("evidence_id")):
                            candidate["evidence_id"] = _text(ref.get("evidence_id"))
                        if candidate["evidence_locator"] and candidate not in refs:
                            refs.append(candidate)
            if not refs and common_knowledge:
                # Knowledge-only relations still need an actual semantic
                # pointer, never a fabricated Source row.
                for outcome in (a, b):
                    for ref in outcome.get("source_evidence_refs") or []:
                        if isinstance(ref, Mapping) and ref.get("source_version_id") and ref.get("evidence_locator") and dict(ref) not in refs:
                            refs.append(dict(ref))
                        if len(refs) >= 20:
                            break
                    if len(refs) >= 20:
                        break
            if not refs:
                continue
            relation_type = "shared_knowledge" if common_knowledge else "shared_evidence"
            summary = f"{left.get('name')} 与 {right.get('name')} 共享 {len(common_knowledge) or len(common_sources)} 个已记录证据/知识输入；需在后续冲突巡航中保持口径一致。"
            # Stable relation identity is pair + governed type. Evidence and
            # version changes update the relation payload/lifecycle instead
            # of creating an unrelated relation key.
            key = _hash({"from": left.get("id"), "to": right.get("id"), "type": relation_type})
            rows.append({"relation_id": "project-relation:" + key[:24], "schema_version": RELATION_SCHEMA, "from_project_id": left.get("id"), "to_project_id": right.get("id"), "relation_type": relation_type, "summary": summary, "evidence_refs": refs, "confidence": "HIGH" if common_sources else "MEDIUM", "evidence_sufficiency": "SUFFICIENT", "temporal_state": "current", "revision": 1, "input_watermark": key, "idempotency_key": key, "created_at": now, "updated_at": now})
    return rows


def read_outcome(db_path: str, project_id: str) -> dict[str, Any] | None:
    conn = _ro(db_path)
    try:
        row = conn.execute("SELECT * FROM project_outcome WHERE project_id=?", (project_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        for key in ("objectives_json", "scope_json", "latest_progress_json", "completed_milestones_json", "open_items_json", "key_decisions_json", "current_effective_rules_json", "risks_json", "blockers_json", "ambiguities_json", "key_metrics_json", "canonical_knowledge_refs_json", "source_evidence_refs_json", "related_projects_json", "unresolved_items_json"):
            result[key.removesuffix("_json")] = json.loads(result.pop(key) or ("{}" if key == "scope_json" or key == "unresolved_items_json" else "[]"))
        return result
    finally:
        conn.close()


def identify_impacted_projects(
    db_path: str,
    change: Mapping[str, Any],
    semantic: Mapping[str, Any] | None = None,
) -> list[str]:
    """Resolve existing Projects affected by a Source/Knowledge change.

    Resolution uses only persisted IDs, links and the materialized Project
    Builder semantic index.  It never creates a Project and never reads raw
    Source content.  Ambiguous names are intentionally ignored unless they
    match exactly one existing Project.
    """
    semantic = semantic or {}
    conn = _ro(db_path)
    try:
        rows = [dict(r) for r in conn.execute(f"SELECT {_project_eligibility_select(conn)} FROM project").fetchall()]
        # P0: only current business Projects may be impacted.  Filtering here -
        # rather than only at the end - means relation neighbours and semantic
        # candidates re-enter through ``add`` and are gated too, so an archived
        # / personal-life / evidence-container Project can never be dragged
        # back in by graph expansion.  Nothing is deleted or modified.
        eligible = {str(r["id"]) for r in rows if is_current_business_project(r)}
        by_id = {str(r["id"]): str(r["id"]) for r in rows if str(r["id"]) in eligible}
        by_name = defaultdict(list)
        for row in rows:
            if str(row["id"]) in eligible:
                by_name[_text(row.get("name"))].append(str(row["id"]))
        impacted: list[str] = []
        ownership_ids: list[tuple[str, str]] = []

        def add(value: Any) -> None:
            value = _text(value)
            if value in by_id:
                impacted.append(value)
            elif value in by_name and len(by_name[value]) == 1:
                impacted.append(by_name[value][0])

        result = change.get("luna_result") if isinstance(change.get("luna_result"), Mapping) else change
        containers = [change, result]
        source_ids = set()
        knowledge_ids = set()
        for container in containers:
            if not isinstance(container, Mapping):
                continue
            for key in ("project_id", "existing_project_id", "project_name", "project_or_domain"):
                add(container.get(key))
            for key in ("project_ids", "existing_project_ids"):
                for value in container.get(key) or []:
                    add(value)
            source_ids.update(_unique(container.get("source_version_ids") or []))
            source_ids.update(_text(x.get("source_version_id")) for x in (container.get("evidence_refs") or []) if isinstance(x, Mapping))
            for key in ("knowledge_id", "current_knowledge_ref"):
                if _text(container.get(key)):
                    knowledge_ids.add(_text(container.get(key)))
            for key in ("knowledge_proposals", "project_consolidations", "metric_proposals"):
                for raw in container.get(key) or []:
                    if not isinstance(raw, Mapping):
                        continue
                    add(raw.get("project_id") or raw.get("project_or_domain") or raw.get("project_name"))
                    source_ids.update(_unique(raw.get("source_version_ids") or []))
                    knowledge_ids.update(_unique([raw.get("knowledge_id"), raw.get("current_knowledge_ref")]))
            for raw in container.get("write_plan") or []:
                if not isinstance(raw, Mapping):
                    continue
                payload = raw.get("payload") if isinstance(raw.get("payload"), Mapping) else {}
                target = _text(raw.get("target_type")).lower()
                if target == "project":
                    add(payload.get("id"))
                if target == "knowledge":
                    knowledge_ids.add(_text(payload.get("id")))
                if target == "knowledge_project_link":
                    add(payload.get("project_id"))
                    knowledge_ids.add(_text(payload.get("knowledge_id")))
                # Structured ownership targets (todo / decision / metric /
                # capture-derived rows) are resolved through their persisted
                # project_id below; text matching stays a fallback only.
                if target in ("todo", "decision", "data", "metric"):
                    for key in ("id", "todo_id", "decision_id", "metric_id"):
                        if _text(payload.get(key)):
                            ownership_ids.append((target, _text(payload.get(key))))
                    capture_id = _text(payload.get("capture_id")) or _text(payload.get("source_capture_id"))
                    if capture_id:
                        ownership_ids.append(("capture", capture_id))
        source_ids.discard("")
        knowledge_ids.discard("")

        for pid, ctx in (semantic.get("project_contexts") or {}).items():
            if not isinstance(ctx, Mapping):
                continue
            linked = set(_unique(ctx.get("linked_source_versions") or []))
            linked.update(_text(x.get("source_version_id")) for x in (ctx.get("evidence") or []) if isinstance(x, Mapping))
            if source_ids & linked:
                add(pid)

        if knowledge_ids:
            placeholders = ",".join("?" for _ in knowledge_ids)
            for row in conn.execute(
                f"SELECT project_id FROM knowledge_project_link WHERE knowledge_id IN ({placeholders})",
                tuple(sorted(knowledge_ids)),
            ).fetchall():
                add(row[0])
        # Structured ownership lookups: todo / decision / metric rows persist
        # their owning project_id, which is authoritative.  Capture targets
        # resolve through decisions that reference them.  Text matching is
        # never the primary signal.
        ownership_tables = {"todo": "todo", "decision": "decision", "data": "metric_definition", "metric": "metric_definition"}
        for target, row_id in ownership_ids:
            if not row_id:
                continue
            if target == "capture":
                try:
                    for row in conn.execute("SELECT project_id FROM decision WHERE source_capture_id = ?", (row_id,)).fetchall():
                        add(row[0])
                except sqlite3.OperationalError:
                    continue
                continue
            table = ownership_tables.get(target)
            if not table:
                continue
            try:
                for row in conn.execute(f"SELECT project_id FROM {table} WHERE id = ?", (row_id,)).fetchall():
                    add(row[0])
            except sqlite3.OperationalError:
                continue
        # Existing relation graph: a change to one Project must also reach
        # its current relation neighbours.
        if impacted:
            snapshot = list(dict.fromkeys(impacted))
            placeholders = ",".join("?" for _ in snapshot)
            try:
                for row in conn.execute(
                    f"SELECT from_project_id, to_project_id FROM project_relation WHERE (from_project_id IN ({placeholders}) OR to_project_id IN ({placeholders})) AND temporal_state = 'current'",
                    tuple(snapshot) * 2,
                ).fetchall():
                    add(row[0])
                    add(row[1])
            except sqlite3.OperationalError:
                pass
        if list_impacted_resynthesis_projects is not None:
            for item in list_impacted_resynthesis_projects():
                add(item.get("project_id"))
        return list(dict.fromkeys(impacted))
    finally:
        conn.close()


def merge_incremental_outcome(
    current: Mapping[str, Any],
    project: Mapping[str, Any],
    change: Mapping[str, Any],
    *,
    now: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Merge a new semantic/evidence envelope into one existing Outcome."""
    now = now or _now()
    result = change.get("luna_result") if isinstance(change.get("luna_result"), Mapping) else change
    refs = [dict(x) for x in (change.get("evidence_refs") or result.get("evidence_refs") or []) if isinstance(x, Mapping)]
    incoming_object_id = _text(change.get("object_id") or result.get("object_id"))
    incoming_fingerprint = _text(change.get("content_fingerprint") or result.get("content_fingerprint"))
    refs = [x for x in refs if _text(x.get("source_version_id")) and _text(x.get("evidence_locator"))]
    for ref in refs:
        object_id = _text(ref.get("object_id") or incoming_object_id)
        if object_id:
            ref["object_id"] = object_id
        if incoming_fingerprint and not _text(ref.get("content_fingerprint")):
            ref["content_fingerprint"] = incoming_fingerprint
    prior = [dict(x) for x in (current.get("source_evidence_refs") or []) if isinstance(x, Mapping)]
    keys = {(x.get("source_version_id"), x.get("evidence_locator")) for x in prior}
    additions = [x for x in refs if (x.get("source_version_id"), x.get("evidence_locator")) not in keys]
    if incoming_fingerprint and any(
        _text(x.get("content_fingerprint")) == incoming_fingerprint
        and _text(x.get("source_version_id")) == _text(refs[0].get("source_version_id") if refs else "")
        for x in prior
    ):
        return dict(current), False
    if not additions:
        return dict(current), False
    project_name = _text(project.get("name"))
    summary = _text(result.get("summary") or result.get("latest_progress_summary") or result.get("update_summary"))
    if not summary:
        topic = _text(result.get("canonical_topic"))
        summary = f"已接收 {topic} 的增量语义证据，已纳入项目结果层复核。" if topic else "已接收新的增量语义证据，已纳入项目结果层复核。"
    if summary == project_name:
        summary = f"{project_name}：已接收新的增量语义证据，已纳入项目结果层复核。"
    updated = dict(current)
    # CONTENT_CHANGED supersession follows the stable logical Source family,
    # not the versioned locator. Unknown id formats are never coalesced.
    change_type = _text(change.get("change_type") or result.get("change_type")).upper()
    changed_families = {
        logical_source_identity(x.get("source_version_id"), x.get("object_id"))
        for x in additions
    } if change_type == "CONTENT_CHANGED" else set()
    kept_refs: list[dict[str, Any]] = []
    superseded_refs: list[dict[str, Any]] = []
    for ref in prior:
        if logical_source_identity(ref.get("source_version_id"), ref.get("object_id")) in changed_families:
            marked = dict(ref)
            marked["superseded"] = True
            marked["current"] = False
            superseded_refs.append(marked)
        else:
            kept_refs.append(ref)
    additions = [{**x, "current": True} for x in additions]
    updated["source_evidence_refs"] = kept_refs + superseded_refs + additions
    prior_progress: list[Any] = []
    for entry in current.get("latest_progress") or []:
        entry_families = set()
        if isinstance(entry, Mapping):
            entry_families = {
                logical_source_identity(sid, entry.get("object_id"))
                for sid in _unique(entry.get("source_version_ids") or [])
            }
        if isinstance(entry, Mapping) and (entry_families & changed_families) and not entry.get("superseded"):
            marked = dict(entry)
            marked["superseded"] = True
            marked["current"] = False
            prior_progress.append(marked)
        else:
            prior_progress.append(entry)
    progress = {
        "summary": summary[:500],
        "source_version_ids": _unique(x.get("source_version_id") for x in additions),
        "evidence_refs": additions[:20],
        "current": True,
    }
    if incoming_object_id:
        progress["object_id"] = incoming_object_id
    updated["latest_progress"] = prior_progress + [progress]
    base_summary = _text(current.get("executive_summary")) or project_name
    marker = "增量证据补充："
    if marker in base_summary:
        base_summary = base_summary.split(marker)[0].rstrip()
    updated["executive_summary"] = f"{base_summary} {marker}{summary[:300]}"
    updated["synthesized_at"] = now
    updated["synthesis_revision"] = int(current.get("synthesis_revision") or 1) + 1
    updated["input_watermark"] = _hash({"prior": current.get("input_watermark"), "evidence": updated["source_evidence_refs"], "summary": summary})
    updated["evidence_fingerprint"] = _hash(updated["source_evidence_refs"])
    updated["evidence_sufficient"] = True
    gate_passed, gate_reason = quality_gate(updated, project_name)
    updated["quality_gate"] = "PASS" if gate_passed else "FAIL:" + gate_reason
    return updated, True


def relation_scope(
    db_path: str,
    impacted_ids: list[str],
    semantic: Mapping[str, Any] | None = None,
) -> list[str]:
    """Bounded neighbourhood for incremental cross-Project analysis.

    The scope is exactly the impacted projects plus their existing relation
    neighbours plus semantic candidate neighbours (projects sharing linked
    source versions in the Project Builder index).  It never enumerates the
    full Project graph and never reads raw Source content.
    """
    semantic = semantic or {}
    conn = _ro(db_path)
    try:
        try:
            eligible = _current_business_project_ids(conn)
        except sqlite3.OperationalError:
            eligible = set()
        # P0: the scope is bounded to current business Projects.  A relation
        # neighbour is admitted only when its own Project is eligible, even
        # when the relation row itself is temporal_state='current'.
        scope = [
            pid
            for pid in dict.fromkeys(_text(x) for x in impacted_ids if _text(x))
            if pid in eligible
        ]
        if scope:
            snapshot = list(scope)
            placeholders = ",".join("?" for _ in snapshot)
            try:
                relation_rows = conn.execute(
                    f"SELECT from_project_id, to_project_id FROM project_relation WHERE (from_project_id IN ({placeholders}) OR to_project_id IN ({placeholders})) AND temporal_state = 'current'",
                    tuple(snapshot) * 2,
                ).fetchall()
            except sqlite3.OperationalError:
                relation_rows = []
            for row in relation_rows:
                for value in (row[0], row[1]):
                    text_value = _text(value)
                    if text_value and text_value in eligible and text_value not in scope:
                        scope.append(text_value)
    finally:
        conn.close()
    linked_by_project: dict[str, set[str]] = {}
    for pid, ctx in (semantic.get("project_contexts") or {}).items():
        if not isinstance(ctx, Mapping):
            continue
        linked = set(_unique(ctx.get("linked_source_versions") or []))
        linked.update(_text(x.get("source_version_id")) for x in (ctx.get("evidence") or []) if isinstance(x, Mapping))
        linked_by_project[_text(pid)] = {x for x in linked if x}
    base_linked: set[str] = set()
    for pid in scope:
        base_linked.update(linked_by_project.get(pid, set()))
    if base_linked:
        for pid, linked in linked_by_project.items():
            if pid not in scope and pid in eligible and (linked & base_linked):
                scope.append(pid)
    return scope


def read_relations(db_path: str, project_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Read existing relations (optionally limited to a project neighbourhood)."""
    conn = _ro(db_path)
    try:
        if project_ids:
            placeholders = ",".join("?" for _ in project_ids)
            rows = conn.execute(
                f"SELECT * FROM project_relation WHERE from_project_id IN ({placeholders}) OR to_project_id IN ({placeholders})",
                tuple(project_ids) * 2,
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM project_relation").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["evidence_refs"] = json.loads(item.pop("evidence_refs_json") or "[]")
            result.append(item)
        return result
    finally:
        conn.close()


def relation_lifecycle(
    existing: list[Mapping[str, Any]],
    desired: list[Mapping[str, Any]],
    *,
    now: str | None = None,
) -> list[dict[str, Any]]:
    """Create / update / supersede lifecycle planning for relations.

    One project pair may carry several relation_type values at the same time
    (shared_kpi, dependency, rule_conflict, ...); types never overwrite each
    other.  Replayed relations keep their revision; current relations whose
    idempotency_key is no longer produced are superseded - history rows are
    kept, never deleted.
    """
    now = now or _now()
    by_key = {_text(x.get("idempotency_key")): x for x in existing if _text(x.get("idempotency_key"))}
    desired_keys = {_text(x.get("idempotency_key")) for x in desired if _text(x.get("idempotency_key"))}
    writes: list[dict[str, Any]] = []
    for relation in desired:
        row = dict(relation)
        prior = by_key.get(_text(relation.get("idempotency_key")))
        if prior is not None:
            comparable = ("relation_id", "from_project_id", "to_project_id", "relation_type", "summary", "evidence_refs", "confidence", "evidence_sufficiency", "temporal_state", "input_watermark")
            changed = any(_canonical(row.get(key)) != _canonical(prior.get(key)) for key in comparable)
            row["revision"] = int(prior.get("revision") or 1) + (1 if changed else 0)
            row["created_at"] = prior.get("created_at") or row.get("created_at")
        writes.append(row)
    for prior in existing:
        if _text(prior.get("temporal_state")) != "current":
            continue
        if _text(prior.get("idempotency_key")) in desired_keys:
            continue
        superseded = dict(prior)
        superseded["temporal_state"] = "superseded"
        superseded["revision"] = int(prior.get("revision") or 1) + 1
        superseded["updated_at"] = now
        superseded["summary"] = _text(prior.get("summary")) + " [superseded]"
        writes.append(superseded)
    return writes


def tombstone_relation(relation: Mapping[str, Any], *, now: str | None = None) -> dict[str, Any]:
    """Mark one relation tombstoned without deleting historical evidence."""
    row = dict(relation)
    row["temporal_state"] = "tombstoned"
    row["revision"] = int(relation.get("revision") or 1) + 1
    row["updated_at"] = now or _now()
    return row


__all__ = ["ensure_schema", "synthesize_project", "outcome_payload", "quality_gate", "build_relations", "read_outcome", "identify_impacted_projects", "logical_source_identity", "merge_incremental_outcome", "relation_scope", "read_relations", "relation_lifecycle", "tombstone_relation", "OUTCOME_SCHEMA", "RELATION_SCHEMA"]
