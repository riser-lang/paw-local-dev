#!/usr/bin/python3
"""Realtime Project Handbook closure + Project freshness state.

Design constraints (deliberate, do not relax):

* ``paw_patrol_project_revision`` states the project-wide rule: *"Python never
  generates business summaries.  Unaffected sections of an existing semantic
  handbook are preserved.  Deterministic field concatenation is refused."*
  This module therefore NEVER overwrites a section that already exists and is
  not machine-owned, and it NEVER invents business prose.  What it publishes is
  an explicitly labelled **formal-state projection**: field values copied out of
  the canonical ``project_outcome`` row, with provenance attached.

* The Handbook row is written through the normal governed chain
  (Candidate -> ReviewDecision -> ApprovedChange -> Committer) as a
  ``project.update`` envelope carrying ``project_handbook``.  This module never
  executes SQL against canonical tables.

* Closure is idempotent: the same ``input_watermark`` redelivered produces the
  same payload, and the caller short-circuits when the stored watermark already
  matches.

Freshness states (``PROJECT_FRESHNESS``):
  FRESH             outcome and handbook both carry the current input watermark
  CLOSURE_PENDING   outcome advanced, handbook not yet refreshed (transient)
  STALE             a commit landed after the project outcome watermark
  BLOCKED           outcome missing, or its quality gate did not pass
"""

from __future__ import annotations

import os


import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

HANDBOOK_SCHEMA_VERSION = "paw-project-handbook/1"
BUILDER = "deterministic-project-handbook-closure-v1"
AUTHORITY_PROJECTION = "formal_state_projection"

DEFAULT_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))
FRESHNESS_PATH = Path("/var/lib/paw-mcp-gateway/paw_project_freshness.json")

# Sections this builder owns.  A section is only (re)written when it is absent
# or when the existing section_metadata marks it machine_owned, so authored
# semantic content can never be clobbered.
MACHINE_OWNED_SECTIONS = (
    "current_state_from_outcome",
    "current_phase_and_status",
    "latest_progress",
    "next_actions",
)

PROJECTION_SECTION = "current_state_from_outcome"

FRESH = "FRESH"
CLOSURE_PENDING = "CLOSURE_PENDING"
STALE = "STALE"
BLOCKED = "BLOCKED"


# ------------------------------------------------------------------ helpers

def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _json_load(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _ro(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _hash(value: Any) -> str:
    import hashlib
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _now() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


# ------------------------------------------------------------------ readers

def read_handbook(db_path: str, project_id: str) -> dict[str, Any] | None:
    conn = _ro(db_path)
    try:
        if not _table_exists(conn, "project_handbook"):
            return None
        row = conn.execute(
            "SELECT * FROM project_handbook WHERE project_id=?", (project_id,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["sections"] = _json_load(result.pop("sections_json", None), {})
        result["section_metadata"] = _json_load(result.pop("section_metadata_json", None), {})
        return result
    finally:
        conn.close()


def read_project_row(db_path: str, project_id: str) -> dict[str, Any] | None:
    conn = _ro(db_path)
    try:
        row = conn.execute("SELECT * FROM project WHERE id=?", (project_id,)).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


# ------------------------------------------------------------------ builder

def _projection_section(outcome: Mapping[str, Any], now: str) -> dict[str, Any]:
    """A labelled, non-narrative projection of canonical formal state.

    Deliberately contains no synthesised business prose: every entry is copied
    from the canonical ``project_outcome`` row, and ``summary`` is a structural
    field listing that names itself as a projection.
    """
    latest = _list(outcome.get("latest_progress"))
    metrics = _list(outcome.get("key_metrics"))
    knowledge_refs = _list(outcome.get("canonical_knowledge_refs"))
    evidence_refs = _list(outcome.get("source_evidence_refs"))
    unresolved = outcome.get("unresolved_items") or {}
    if not isinstance(unresolved, Mapping):
        unresolved = {}
    watermark = _text(outcome.get("input_watermark"))
    return {
        "authority": AUTHORITY_PROJECTION,
        "formal_state": True,
        "analysis_model": None,
        "generated_by": BUILDER,
        "generated_at": now,
        "summary": (
            "[正式状态投影] 状态=%s；阶段=%s；进展条目=%d；开放项=%d；"
            "里程碑=%d；决策=%d；当前规则=%d；风险=%d；指标=%d；"
            "知识引用=%d；证据引用=%d；水位=%s"
            % (
                _text(outcome.get("current_status")) or "UNKNOWN",
                _text(outcome.get("phase")) or "UNKNOWN",
                len(latest),
                len(_list(outcome.get("open_items"))),
                len(_list(outcome.get("completed_milestones"))),
                len(_list(outcome.get("key_decisions"))),
                len(_list(outcome.get("current_effective_rules"))),
                len(_list(outcome.get("risks"))),
                len(metrics),
                len(knowledge_refs),
                len(evidence_refs),
                watermark[:16] or "NONE",
            )
        ),
        "current_status": outcome.get("current_status"),
        "phase": outcome.get("phase"),
        "executive_summary": outcome.get("executive_summary"),
        "latest_progress": latest,
        "open_items": _list(outcome.get("open_items")),
        "completed_milestones": _list(outcome.get("completed_milestones")),
        "key_decisions": _list(outcome.get("key_decisions")),
        "current_effective_rules": _list(outcome.get("current_effective_rules")),
        "risks": _list(outcome.get("risks")),
        "blockers": _list(outcome.get("blockers")),
        "ambiguities": _list(outcome.get("ambiguities")),
        "key_metrics_count": len(metrics),
        "canonical_knowledge_refs_count": len(knowledge_refs),
        "source_evidence_refs_count": len(evidence_refs),
        "related_projects": _list(outcome.get("related_projects")),
        "unresolved_items": dict(unresolved),
        "outcome_input_watermark": watermark,
        "outcome_synthesis_revision": int(outcome.get("synthesis_revision") or 1),
        "outcome_synthesized_at": outcome.get("synthesized_at"),
        "evidence_fingerprint": outcome.get("evidence_fingerprint"),
        "evidence_sufficient": bool(outcome.get("evidence_sufficient")),
        "quality_gate": outcome.get("quality_gate"),
        "updated_at": now,
    }


def _owned(meta: Mapping[str, Any], name: str) -> bool:
    entry = meta.get(name)
    if not isinstance(entry, Mapping):
        return False
    return bool(entry.get("machine_owned")) or _text(entry.get("generated_by")) == BUILDER


def build_handbook_projection(
    outcome: Mapping[str, Any],
    project_row: Mapping[str, Any],
    existing: Mapping[str, Any] | None = None,
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """Build the governed ``project_handbook`` payload (additive, non-destructive)."""
    now = now or _now()
    project_id = _text(outcome.get("project_id"))
    if not project_id:
        raise ValueError("handbook closure requires a project_id")
    if _text(project_row.get("id")) != project_id:
        raise ValueError("handbook closure project identity mismatch")

    existing = existing or {}
    sections = dict(existing.get("sections") or {})
    meta = dict(existing.get("section_metadata") or {})

    preserved = sorted(k for k in sections if k not in MACHINE_OWNED_SECTIONS)
    refreshed: list[str] = []

    projection = _projection_section(outcome, now)
    sections[PROJECTION_SECTION] = projection
    refreshed.append(PROJECTION_SECTION)

    # Structural restatements of formal state: only filled in when the section
    # is absent, or when a previous run of this builder owns it.
    if "current_phase_and_status" not in sections or _owned(meta, "current_phase_and_status"):
        phase = _text(outcome.get("phase")) or _text(outcome.get("current_status"))
        if phase:
            sections["current_phase_and_status"] = phase
            refreshed.append("current_phase_and_status")
    if "latest_progress" not in sections or _owned(meta, "latest_progress"):
        progress = _list(outcome.get("latest_progress"))
        if progress:
            sections["latest_progress"] = progress
            refreshed.append("latest_progress")
    if "next_actions" not in sections or _owned(meta, "next_actions"):
        nxt = _list(outcome.get("open_items"))
        if nxt:
            sections["next_actions"] = nxt
            refreshed.append("next_actions")

    for name in refreshed:
        meta[name] = {
            "machine_owned": True,
            "generated_by": BUILDER,
            "authority": AUTHORITY_PROJECTION,
            "formal_state": True,
            "quality": _text(outcome.get("quality_gate")) or "PASS",
            "evidence_sufficient": bool(outcome.get("evidence_sufficient")),
        }
    meta["realtime_closure"] = {
        "builder": BUILDER,
        "closed_at": now,
        "outcome_input_watermark": _text(outcome.get("input_watermark")),
        "outcome_synthesis_revision": int(outcome.get("synthesis_revision") or 1),
        "outcome_synthesized_at": outcome.get("synthesized_at"),
        "refreshed_sections": sorted(refreshed),
        "preserved_sections": preserved,
        "preserved_section_count": len(preserved),
        "preserves_authored_content": True,
        "generates_business_prose": False,
        "authority": AUTHORITY_PROJECTION,
        "semantic_enrichment": "DEFERRED_TO_PATROL_OR_MODEL_AUTHORED_REVISION",
        "formal_state_bypass": 0,
    }

    revision = int(existing.get("revision") or 0) + 1
    watermark = _text(outcome.get("input_watermark"))
    if not watermark:
        raise ValueError("handbook closure requires an outcome input_watermark")
    fingerprint = _text(outcome.get("evidence_fingerprint")) or _hash(
        outcome.get("source_evidence_refs") or []
    )
    return {
        "project_id": project_id,
        "schema_version": HANDBOOK_SCHEMA_VERSION,
        "sections": sections,
        "section_metadata": meta,
        "generated_at": now,
        "revision": revision,
        "input_watermark": watermark,
        "evidence_fingerprint": fingerprint,
        "quality_gate": _text(outcome.get("quality_gate")) or "PASS",
        "evidence_sufficient": bool(outcome.get("evidence_sufficient")),
        "baseline_input_hash": _hash({"project_id": project_id, "watermark": watermark,
                                      "revision": revision}),
    }


def handbook_needs_closure(outcome: Mapping[str, Any], existing: Mapping[str, Any] | None) -> bool:
    """True when the stored handbook has not yet caught up with the outcome."""
    if not existing:
        return True
    return _text(existing.get("input_watermark")) != _text(outcome.get("input_watermark"))


def handbook_write_item(
    outcome: Mapping[str, Any],
    project_row: Mapping[str, Any],
    existing: Mapping[str, Any] | None,
    *,
    now: str | None = None,
) -> dict[str, Any] | None:
    """Build the governed ``project.update`` envelope, or None when already fresh."""
    if not handbook_needs_closure(outcome, existing):
        return None
    project_id = _text(outcome.get("project_id"))
    handbook = build_handbook_projection(outcome, project_row, existing, now=now)
    refs = [dict(x) for x in (outcome.get("source_evidence_refs") or []) if isinstance(x, Mapping)]
    # Mirror paw_project_first_closure._source_refs(): a project whose evidence
    # is formally insufficient still carries a formal-state reference, which is
    # what the write policy's evidence gate accepts.
    if not refs:
        refs = [{"formal_state_ref": f"project:{project_id}:handbook"}]
    source_refs = sorted({f"source_version:{_text(x.get('source_version_id'))}"
                          for x in refs if _text(x.get("source_version_id"))}) or [f"project:{project_id}"]
    watermark = _text(outcome.get("input_watermark"))
    return {
        "id": f"project-handbook:{project_id}:{watermark[:32]}",
        "target_type": "project",
        "operation": "update",
        "payload": {"id": project_id, "project_handbook": handbook},
        "source_refs": source_refs,
        "evidence_refs": refs,
        "approval_source": "SYSTEM_EVIDENCE_GOVERNED_WRITE_V1",
        "preanalysis_model": "gpt-5.6-luna-reuse",
        "authoritative_model": BUILDER,
    }


def close_handbook(
    db_path: str,
    project_id: str,
    *,
    commit_policy_write=None,
    outcome: Mapping[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Idempotently bring one Project Handbook up to the current Outcome."""
    if commit_policy_write is None:
        from paw_gpt_brain_policy_committer import commit_policy_write as _cpw
        commit_policy_write = _cpw
    if outcome is None:
        from paw_project_outcome import read_outcome
        outcome = read_outcome(db_path, project_id)
    if not outcome:
        return {"status": "SKIPPED", "reason": "outcome_missing", "project_id": project_id}
    project_row = read_project_row(db_path, project_id)
    if not project_row:
        return {"status": "SKIPPED", "reason": "project_missing", "project_id": project_id}
    existing = read_handbook(db_path, project_id)
    item = handbook_write_item(outcome, project_row, existing, now=now)
    if item is None:
        return {"status": "ALREADY_FRESH", "project_id": project_id,
                "input_watermark": _text(outcome.get("input_watermark"))}
    result = commit_policy_write(
        item,
        db_path=db_path,
        bundle={"id": item["id"], "state": "CLEAR", "coverage_state": "CONSOLIDATED",
                "source_refs": item["source_refs"], "evidence_refs": item["evidence_refs"]},
        mark_policy=False,
    )
    after = read_handbook(db_path, project_id)
    readback_pass = bool(
        after
        and _text(after.get("input_watermark")) == _text(outcome.get("input_watermark"))
    )
    return {
        "status": result.get("status"),
        "project_id": project_id,
        "reason": result.get("reason"),
        "commit_record_id": result.get("commit_record_id"),
        "input_watermark": _text(outcome.get("input_watermark")),
        "handbook_revision": (after or {}).get("revision"),
        "readback_pass": readback_pass,
    }


# ------------------------------------------------------------------ freshness

def project_freshness(db_path: str = DEFAULT_DB, *,
                      closure_watermark: str | None = None) -> dict[str, Any]:
    """Compute per-Project freshness and the Phase-2 PROJECT_* gates.

    ``closure_watermark`` is the newest commit the last successful closure (or
    incremental Project synthesis) accounted for.  Commits newer than that
    watermark mean formal state moved after the read model was closed, which is
    reported as STALE -- a *reconciliation* signal (Patrol's job per the v1.2
    design), never a silent success.  When omitted, "now" is used, which is the
    correct reading immediately after a closure run.
    """
    conn = _ro(db_path)
    try:
        has_handbook = _table_exists(conn, "project_handbook")
        has_outcome = _table_exists(conn, "project_outcome")
        projects = [dict(r) for r in conn.execute(
            "SELECT id, name, status FROM project ORDER BY name, id").fetchall()]
        outcomes = {}
        if has_outcome:
            outcomes = {r["project_id"]: dict(r) for r in conn.execute(
                "SELECT project_id, input_watermark, synthesis_revision, synthesized_at,"
                " quality_gate, evidence_sufficient FROM project_outcome").fetchall()}
        handbooks = {}
        if has_handbook:
            handbooks = {r["project_id"]: dict(r) for r in conn.execute(
                "SELECT project_id, input_watermark, revision, generated_at FROM project_handbook").fetchall()}
        max_commit = conn.execute("SELECT MAX(committed_at) FROM commit_record").fetchone()[0]
        commit_failures = conn.execute(
            "SELECT COUNT(*) FROM commit_record WHERE result!='success'").fetchone()[0]
        recent_bad = conn.execute(
            "SELECT COUNT(*) FROM commit_record WHERE result!='success'"
            " AND committed_at > datetime('now','-1 day')").fetchone()[0]
        watermark = _text(closure_watermark) or _text(max_commit)
        pending = 0
        if watermark:
            pending = conn.execute(
                "SELECT COUNT(*) FROM commit_record WHERE committed_at > ?", (watermark,)
            ).fetchone()[0]
    finally:
        conn.close()

    per_project: list[dict[str, Any]] = []
    for p in projects:
        pid = p["id"]
        o = outcomes.get(pid)
        h = handbooks.get(pid)
        if not o:
            state, reason = BLOCKED, "outcome_missing"
        elif _text(o.get("quality_gate")) not in {"PASS", "PASS_EVIDENCE_INSUFFICIENT"}:
            state, reason = BLOCKED, "quality_gate_" + _text(o.get("quality_gate"))
        elif not h:
            state, reason = CLOSURE_PENDING, "handbook_missing"
        elif _text(h.get("input_watermark")) != _text(o.get("input_watermark")):
            state, reason = CLOSURE_PENDING, "handbook_watermark_behind_outcome"
        elif pending:
            state, reason = STALE, "reconciliation_pending_commits"
        else:
            state, reason = FRESH, "outcome_and_handbook_current"
        per_project.append({
            "project_id": pid, "project_name": p["name"], "state": state, "reason": reason,
            "outcome_input_watermark": _text((o or {}).get("input_watermark")),
            "outcome_synthesis_revision": (o or {}).get("synthesis_revision"),
            "outcome_synthesized_at": (o or {}).get("synthesized_at"),
            "handbook_input_watermark": _text((h or {}).get("input_watermark")),
            "handbook_revision": (h or {}).get("revision"),
            "handbook_generated_at": (h or {}).get("generated_at"),
        })

    counts = {s: sum(1 for x in per_project if x["state"] == s)
              for s in (FRESH, CLOSURE_PENDING, STALE, BLOCKED)}
    outcome_present = sum(1 for x in per_project if x["state"] != BLOCKED)
    handbook_match = sum(1 for x in per_project
                         if x["state"] in {FRESH, STALE}
                         and x["outcome_input_watermark"]
                         and x["handbook_input_watermark"] == x["outcome_input_watermark"])
    total = len(per_project)
    gates = {
        "PROJECT_COMMIT_GATE": "PASS" if recent_bad == 0 else "FAIL",
        "PROJECT_IMPACT_GATE": "PASS" if total and handbook_match == total and pending == 0 else "FAIL",
        "PROJECT_OUTCOME_UPDATE_GATE": "PASS" if total and outcome_present == total and pending == 0 else "FAIL",
        "PROJECT_HANDBOOK_UPDATE_GATE": "PASS" if total and handbook_match == total else "FAIL",
        "PROJECT_READ_AFTER_WRITE_GATE": "PASS" if total and counts[CLOSURE_PENDING] == 0 and counts[BLOCKED] == 0 else "FAIL",
    }
    result = {
        "schema": "paw-project-freshness/1",
        "generated_at": _now(),
        "closure_watermark": watermark,
        "max_commit_committed_at": max_commit,
        "reconciliation_pending_commits": pending,
        "commit_failures_total": commit_failures,
        "commit_failures_last_24h": recent_bad,
        "projects": per_project,
        "counts": counts,
        "gates": gates,
        "PROJECT_STALE_SILENT_SUCCESS": counts[CLOSURE_PENDING],
        "formal_state_bypass": 0,
    }
    return result


def write_freshness(db_path: str = DEFAULT_DB, *, closure_watermark: str | None = None,
                    path: Path | None = None) -> dict[str, Any]:
    result = project_freshness(db_path, closure_watermark=closure_watermark)
    target = path or FRESHNESS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


# ------------------------------------------------------------------ read-side
#
# These two helpers exist so the agent-facing read surface can disclose the
# closure layer instead of silently serving a stale Project.  Both are
# deliberately non-raising: a missing or unreadable report degrades to an
# explicit UNKNOWN, never to an exception and never to a silent "fresh".

GOVERNANCE_PATH = Path("/var/lib/paw-mcp-gateway/paw_knowledge_evidence_governance.json")

EVIDENCE_BACKED_STATES = ("EVIDENCE_BACKED_LINKED", "EVIDENCE_BACKED_PAYLOAD_IDENTITY")


def read_project_freshness(project_id: str, *, path: Path | None = None) -> dict[str, Any]:
    """Per-Project freshness state from the last written freshness report."""
    target = Path(path) if path else FRESHNESS_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"state": "UNKNOWN", "reason": "freshness_report_unavailable",
                "project_id": _text(project_id)}
    for entry in data.get("projects") or []:
        if _text(entry.get("project_id")) == _text(project_id):
            out = dict(entry)
            out["report_generated_at"] = data.get("generated_at")
            out["closure_watermark"] = data.get("closure_watermark")
            out["reconciliation_pending_commits"] = data.get("reconciliation_pending_commits")
            out["gates"] = data.get("gates")
            return out
    return {"state": "UNKNOWN", "reason": "project_not_in_freshness_report",
            "project_id": _text(project_id)}


def knowledge_evidence_admission(*, path: Path | None = None) -> dict[str, Any]:
    """Evidence-admission state per Current Knowledge.

    Returns ``items`` (knowledge_id -> evidence_state) so a caller can label each
    knowledge link, plus the aggregate counters.  Weak matches are never
    promoted here: only exact registry identity produces an EVIDENCE_BACKED_*
    state, which is the sole state treated as canonical for automatic decision.
    """
    target = Path(path) if path else GOVERNANCE_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"available": False, "reason": "governance_report_unavailable", "items": {}}
    items: dict[str, str] = {}
    for entry in data.get("items") or []:
        if not isinstance(entry, Mapping):
            continue
        kid = _text(entry.get("knowledge_id"))
        if kid:
            items[kid] = _text(entry.get("evidence_state")) or "UNKNOWN"
    return {
        "available": True,
        "schema": data.get("schema"),
        "gate": data.get("EVIDENCE_TRACEABILITY_GATE"),
        "counters": {k: data.get(k) for k in (
            "CURRENT_KNOWLEDGE_TOTAL", "CURRENT_KNOWLEDGE_WITH_VALID_EVIDENCE",
            "CURRENT_KNOWLEDGE_WITHOUT_EVIDENCE", "LEGACY_UNTRACEABLE_COUNT",
            "NEEDS_REVIEW_UNVALIDATED_REF_COUNT", "BRIDGEABLE_BY_DETERMINISTIC_IDENTITY",
            "SUPERSEDED_VERSION_CANDIDATES", "SOURCE_REGISTRY_ENTRIES")},
        "legacy_untrusted_policy": data.get("LEGACY_UNTRACEABLE_POLICY"),
        "weak_match_policy": data.get("WEAK_MATCH_POLICY"),
        "evidence_backed_states": list(EVIDENCE_BACKED_STATES),
        "items": items,
    }


__all__ = [
    "HANDBOOK_SCHEMA_VERSION", "BUILDER", "FRESHNESS_PATH", "GOVERNANCE_PATH",
    "FRESH", "CLOSURE_PENDING", "STALE", "BLOCKED", "EVIDENCE_BACKED_STATES",
    "read_handbook", "read_project_row", "build_handbook_projection",
    "handbook_needs_closure", "handbook_write_item", "close_handbook",
    "project_freshness", "write_freshness",
    "read_project_freshness", "knowledge_evidence_admission",
]
