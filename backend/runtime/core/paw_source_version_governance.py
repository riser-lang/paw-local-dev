#!/usr/bin/env python3
"""Source Version Governance — logical document families, authority resolution,
decision log, current-evidence filtering, and resynthesis impact tracking.

Raw SourceVersion history is append-only.  Governed writes go through the
existing ReviewDecision -> ApprovedChange -> Committer path; version rulings
travel as ``project.update`` payloads with a ``version_ruling`` block.
"""

from __future__ import annotations

import os


import hashlib
import json
import re
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

GOVERNANCE_SCHEMA = "paw-source-version-governance/1"
DEFAULT_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))
RESYNTHESIS_STORE = Path("/var/lib/paw-mcp-gateway/paw_source_version_resynthesis.json")

# PAW v1.2 phase 3 (technical debt D): ``project.description`` is a stable
# project identity statement owned by the Project Registry, not a rolling
# synthesis buffer.  Derived state (Outcome / Handbook / Explainer) is exposed
# through its own read surfaces instead.
PROJECT_DESCRIPTION_IDENTITY_OWNER = "project_registry"
# Upper bound applied only when *seeding* a description for a project that has
# none.  An existing description is never rewritten, so this limit can never
# truncate a human-authored identity statement.
PROJECT_DESCRIPTION_SEED_LIMIT = 2000

STATUS_CURRENT = "current"
STATUS_SUPERSEDED = "superseded"
STATUS_ARCHIVED = "archived"
STATUS_UNRESOLVED = "unresolved"
GOVERNANCE_STATUSES = frozenset({STATUS_CURRENT, STATUS_SUPERSEDED, STATUS_ARCHIVED, STATUS_UNRESOLVED})

AUTHORITY_GOVERNED_DECISION = 1
AUTHORITY_EXPLICIT_SUPERSEDES = 2
AUTHORITY_EFFECTIVE_DECLARATION = 3
AUTHORITY_MTIME_WEAK = 4

_SOURCE_VERSION_CONTRACT = re.compile(r"^(.+?)@(\d+)$")

_FILENAME_AUTHORITY_RE = re.compile(r"(最终版|final|latest|v\d+|版本)", re.I)
_EXPLICIT_RULING_RE = re.compile(
    r"(?:从\s*(?P<from_date>\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?)\s*(?:起|开始)?)?"
    r"(?:使用|采用|以)\s*(?:v|版本)?(?P<current>\d+)\s*(?:为准|生效|作为当前版本)?"
    r"(?:[；;，,。\s]*(?:v|版本)?(?P<superseded>\d+)\s*(?:及)?(?:更早|之前|以前)?(?:版本)?(?:为)?(?:历史|归档| superseded)?)?",
    re.I,
)
_REPLACES_RE = re.compile(
    r"(?P<current>v?\d+)\s*(?:替换|取代|supersedes?|replaces?)\s*(?P<prior>v?\d+)",
    re.I,
)
_EFFECTIVE_DATE_RE = re.compile(
    r"(?:effective|生效|自)\s*(?:from|自)?\s*(?P<date>\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?)",
    re.I,
)

_COMMITTER_HOOKED = False
_ORIGINAL_PROJECT_UPDATE = None


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def logical_source_identity(source_version_id: Any, object_id: Any = None) -> str:
    """Resolve stable Source family (mirrors paw_project_outcome)."""
    object_text = _text(object_id)
    if object_text:
        return "object:" + object_text
    sid = _text(source_version_id)
    match = _SOURCE_VERSION_CONTRACT.fullmatch(sid)
    if match:
        return "object:" + match.group(1)
    return "version:" + sid if sid else ""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


def _parse_version_num(source_version_id: str) -> int | None:
    sid = _text(source_version_id)
    if "@" in sid:
        try:
            return int(sid.rsplit("@", 1)[-1])
        except ValueError:
            return None
    match = re.search(r"v(\d+)", sid, re.I)
    if match:
        return int(match.group(1))
    return None


def governance_schema_present(conn: sqlite3.Connection) -> bool:
    """True when governance tables exist in this database."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_version_governance' LIMIT 1",
    ).fetchone()
    return row is not None


def empty_version_context(project_id: str) -> dict[str, Any]:
    """Compatibility read model when governance schema is absent."""
    return {
        "schema_version": GOVERNANCE_SCHEMA,
        "project_id": project_id,
        "governance_schema": "absent",
        "current_source_versions": [],
        "all_linked_source_versions": [],
        "superseded_source_versions": [],
        "archived_source_versions": [],
        "unresolved_conflicts": [],
        "version_decisions": [],
        "filter_meta": {"mode": "no_governance_schema", "excluded": [], "unresolved": [], "conflicts": []},
        "authority": {
            "route": "OK",
            "logical_documents": {},
            "conflicts": [],
            "unresolved": [],
            "governance_schema": "absent",
        },
        "stale_derivations": {
            "needs_resynthesis": False,
            "stale_refs": [],
            "active_refs": [],
            "governance_schema": "absent",
        },
        "resynthesis": [],
        "description_projection": {},
        "formal_state_bypass": 0,
    }


def ensure_schema(db_path: str = DEFAULT_DB) -> dict[str, Any]:
    """Idempotent migration for governance tables."""
    conn = sqlite3.connect(db_path, timeout=20)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS logical_document (
            logical_document_id TEXT PRIMARY KEY,
            object_id TEXT,
            display_name TEXT,
            family_status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_logical_document_object ON logical_document(object_id);

        CREATE TABLE IF NOT EXISTS source_version_governance (
            source_version_id TEXT PRIMARY KEY,
            logical_document_id TEXT NOT NULL REFERENCES logical_document(logical_document_id),
            governance_status TEXT NOT NULL,
            supersedes_source_version_id TEXT,
            effective_from TEXT,
            effective_to TEXT,
            authority_rank INTEGER NOT NULL DEFAULT 99,
            authority_reason TEXT NOT NULL DEFAULT '',
            confidence TEXT NOT NULL DEFAULT 'HIGH',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_svg_logical ON source_version_governance(logical_document_id);
        CREATE INDEX IF NOT EXISTS idx_svg_status ON source_version_governance(governance_status);

        CREATE TABLE IF NOT EXISTS source_version_decision_log (
            decision_id TEXT PRIMARY KEY,
            logical_document_id TEXT NOT NULL,
            ruling_json TEXT NOT NULL,
            approval_ref TEXT,
            change_id TEXT,
            project_ids_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_svdl_logical ON source_version_decision_log(logical_document_id);
        CREATE INDEX IF NOT EXISTS idx_svdl_created ON source_version_decision_log(created_at);
        """)
        conn.commit()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {
            "status": "READY",
            "logical_document": "logical_document" in tables,
            "source_version_governance": "source_version_governance" in tables,
            "source_version_decision_log": "source_version_decision_log" in tables,
            "formal_state_bypass": 0,
        }
    finally:
        conn.close()


def _ensure_logical_document(conn: sqlite3.Connection, source_version_id: str, *, display_name: str = "") -> str:
    family = logical_source_identity(source_version_id)
    if not family:
        raise ValueError("cannot resolve logical document family")
    object_id = family.split(":", 1)[-1] if family.startswith("object:") else family
    now = _now()
    conn.execute(
        """INSERT INTO logical_document(logical_document_id, object_id, display_name, family_status, created_at, updated_at)
           VALUES (?, ?, ?, 'active', ?, ?)
           ON CONFLICT(logical_document_id) DO UPDATE SET
             display_name=CASE WHEN excluded.display_name != '' THEN excluded.display_name ELSE logical_document.display_name END,
             updated_at=excluded.updated_at""",
        (family, object_id, display_name or object_id, now, now),
    )
    return family


def _upsert_membership(
    conn: sqlite3.Connection,
    *,
    source_version_id: str,
    logical_document_id: str,
    governance_status: str,
    supersedes_source_version_id: str | None = None,
    effective_from: str | None = None,
    effective_to: str | None = None,
    authority_rank: int = AUTHORITY_GOVERNED_DECISION,
    authority_reason: str = "",
    confidence: str = "HIGH",
    provenance: Mapping[str, Any] | None = None,
) -> None:
    if governance_status not in GOVERNANCE_STATUSES:
        raise ValueError(f"invalid governance_status: {governance_status}")
    now = _now()
    conn.execute(
        """INSERT INTO source_version_governance(
               source_version_id, logical_document_id, governance_status,
               supersedes_source_version_id, effective_from, effective_to,
               authority_rank, authority_reason, confidence, provenance_json,
               created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(source_version_id) DO UPDATE SET
             logical_document_id=excluded.logical_document_id,
             governance_status=excluded.governance_status,
             supersedes_source_version_id=excluded.supersedes_source_version_id,
             effective_from=COALESCE(excluded.effective_from, source_version_governance.effective_from),
             effective_to=excluded.effective_to,
             authority_rank=MIN(source_version_governance.authority_rank, excluded.authority_rank),
             authority_reason=CASE WHEN excluded.authority_rank <= source_version_governance.authority_rank
                                   THEN excluded.authority_reason ELSE source_version_governance.authority_reason END,
             confidence=excluded.confidence,
             provenance_json=excluded.provenance_json,
             updated_at=excluded.updated_at""",
        (
            source_version_id,
            logical_document_id,
            governance_status,
            supersedes_source_version_id,
            effective_from,
            effective_to,
            authority_rank,
            authority_reason,
            confidence,
            _json(dict(provenance or {})),
            now,
            now,
        ),
    )


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _text(value)
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def parse_capture_version_directive(text: str, *, linked_source_version_ids: list[str] | None = None) -> dict[str, Any]:
    """Parse explicit user version directives. Ambiguous input => USER_DECISION_REQUIRED."""
    raw = _text(text)
    if not raw:
        return {"status": "USER_DECISION_REQUIRED", "reason": "empty directive"}

    linked = [_text(x) for x in (linked_source_version_ids or []) if _text(x)]
    if _FILENAME_AUTHORITY_RE.search(raw) and not _EXPLICIT_RULING_RE.search(raw) and not _REPLACES_RE.search(raw):
        return {
            "status": "USER_DECISION_REQUIRED",
            "reason": "filename_or_label_only_not_authoritative",
        }

    replaces = _REPLACES_RE.search(raw)
    explicit = _EXPLICIT_RULING_RE.search(raw)
    effective = _EFFECTIVE_DATE_RE.search(raw)

    if not replaces and not explicit:
        if re.search(r"(?:v|版本)\s*\d+", raw, re.I) and not re.search(r"(使用|采用|替换|取代|historical|历史|归档|superseded|current|当前)", raw, re.I):
            return {"status": "USER_DECISION_REQUIRED", "reason": "version_mention_without_explicit_ruling"}

    ruling: dict[str, Any] = {"directive_text": raw, "schema_version": GOVERNANCE_SCHEMA}
    if replaces:
        current_num = _text(replaces.group("current")).lstrip("vV")
        prior_num = _text(replaces.group("prior")).lstrip("vV")
        ruling["current_version_num"] = int(current_num)
        ruling["superseded_version_nums"] = [int(prior_num)]
        ruling["authority_reason"] = "explicit_replaces_supersedes"
    elif explicit:
        current_num = _text(explicit.group("current"))
        ruling["current_version_num"] = int(current_num)
        superseded_num = _text(explicit.group("superseded") or "")
        ruling["superseded_version_nums"] = [int(superseded_num)] if superseded_num else []
        if explicit.group("from_date"):
            ruling["effective_from"] = _text(explicit.group("from_date"))
        ruling["authority_reason"] = "explicit_governed_user_ruling"
    elif effective:
        ruling["effective_from"] = _text(effective.group("date"))
        ruling["authority_reason"] = "explicit_effective_date"
        return {"status": "USER_DECISION_REQUIRED", "reason": "effective_date_without_current_version"}

    if linked:
        by_num: dict[int, str] = {}
        for sid in linked:
            num = _parse_version_num(sid)
            if num is not None:
                by_num[num] = sid
        current_num = ruling.get("current_version_num")
        if current_num is not None and current_num not in by_num:
            return {"status": "USER_DECISION_REQUIRED", "reason": "current_version_not_in_linked_sources"}
        if current_num is not None:
            ruling["current_source_version_id"] = by_num[current_num]
            ruling["logical_document_id"] = logical_source_identity(by_num[current_num])
        superseded_ids = []
        for num in ruling.get("superseded_version_nums") or []:
            if num in by_num:
                superseded_ids.append(by_num[num])
            else:
                return {"status": "USER_DECISION_REQUIRED", "reason": f"superseded_version_{num}_not_in_linked_sources"}
        ruling["superseded_source_version_ids"] = superseded_ids
    else:
        return {"status": "USER_DECISION_REQUIRED", "reason": "missing_source_version_linkage"}

    if not ruling.get("current_source_version_id"):
        return {"status": "USER_DECISION_REQUIRED", "reason": "could_not_resolve_current_source_version"}

    ruling["superseded_source_version_ids"] = _unique(ruling.get("superseded_source_version_ids") or [])
    return {"status": "READY", "version_ruling": ruling}


def build_version_ruling_envelope(ruling: Mapping[str, Any], *, project_id: str) -> dict[str, Any]:
    """Governed execution envelope for capture / submit_candidate."""
    vr = dict(ruling)
    return {
        "target_type": "project",
        "operation": "update",
        "payload": {
            "id": project_id,
            "version_ruling": vr,
            "governance_only": True,
        },
    }


def apply_version_ruling(
    conn: sqlite3.Connection,
    ruling: Mapping[str, Any],
    *,
    approval_ref: str | None = None,
    change_id: str | None = None,
    project_ids: list[str] | None = None,
) -> str:
    """Apply a governed version ruling inside an open transaction."""
    current_sid = _text(ruling.get("current_source_version_id"))
    if not current_sid:
        raise ValueError("version_ruling requires current_source_version_id")
    logical_id = _text(ruling.get("logical_document_id")) or _ensure_logical_document(conn, current_sid)
    _ensure_logical_document(conn, current_sid)
    provenance = {
        "approval_ref": approval_ref,
        "change_id": change_id,
        "authority_reason": _text(ruling.get("authority_reason") or "governed_user_decision"),
        "directive_text": ruling.get("directive_text"),
        "schema_version": GOVERNANCE_SCHEMA,
    }
    superseded_ids = _unique(list(ruling.get("superseded_source_version_ids") or []))
    effective_from = _text(ruling.get("effective_from")) or None

    for sid in superseded_ids:
        _ensure_logical_document(conn, sid)
        _upsert_membership(
            conn,
            source_version_id=sid,
            logical_document_id=logical_id,
            governance_status=STATUS_SUPERSEDED,
            supersedes_source_version_id=None,
            effective_to=effective_from,
            authority_rank=AUTHORITY_GOVERNED_DECISION,
            authority_reason=provenance["authority_reason"],
            provenance=provenance,
        )

    _upsert_membership(
        conn,
        source_version_id=current_sid,
        logical_document_id=logical_id,
        governance_status=STATUS_CURRENT,
        supersedes_source_version_id=superseded_ids[0] if len(superseded_ids) == 1 else None,
        effective_from=effective_from,
        authority_rank=AUTHORITY_GOVERNED_DECISION,
        authority_reason=provenance["authority_reason"],
        provenance=provenance,
    )

    decision_id = "svd-" + _hash({"logical_id": logical_id, "ruling": dict(ruling), "approval_ref": approval_ref})[:24]
    conn.execute(
        """INSERT INTO source_version_decision_log(
               decision_id, logical_document_id, ruling_json, approval_ref, change_id,
               project_ids_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(decision_id) DO UPDATE SET
             approval_ref=COALESCE(excluded.approval_ref, source_version_decision_log.approval_ref),
             change_id=COALESCE(excluded.change_id, source_version_decision_log.change_id)""",
        (
            decision_id,
            logical_id,
            _json(dict(ruling)),
            approval_ref,
            change_id,
            _json(list(project_ids or [])),
            _now(),
        ),
    )

    impacted = list(project_ids or [])
    if impacted:
        _mark_projects_for_resynthesis(impacted, reason="version_ruling", logical_document_id=logical_id)
    return logical_id


def resolve_authority(
    conn: sqlite3.Connection,
    *,
    logical_document_id: str | None = None,
    source_version_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Deterministic authority view for one logical document family."""
    families: set[str] = set()
    if logical_document_id:
        families.add(logical_document_id)
    for sid in source_version_ids or []:
        families.add(logical_source_identity(sid))

    result: dict[str, Any] = {
        "logical_documents": {},
        "conflicts": [],
        "unresolved": [],
        "route": "OK",
    }
    if not governance_schema_present(conn):
        result["governance_schema"] = "absent"
        return result
    for family in sorted(families):
        if not family:
            continue
        rows = conn.execute(
            """SELECT * FROM source_version_governance
               WHERE logical_document_id=? ORDER BY authority_rank ASC, updated_at DESC""",
            (family,),
        ).fetchall()
        memberships = [dict(r) for r in rows]
        current = [m for m in memberships if m.get("governance_status") == STATUS_CURRENT]
        unresolved = [m for m in memberships if m.get("governance_status") == STATUS_UNRESOLVED]
        superseded = [m for m in memberships if m.get("governance_status") in {STATUS_SUPERSEDED, STATUS_ARCHIVED}]

        if len(current) > 1:
            result["conflicts"].append({"logical_document_id": family, "current": [m["source_version_id"] for m in current]})
            result["route"] = "CONFLICT"
        if unresolved:
            result["unresolved"].extend([m["source_version_id"] for m in unresolved])
            if result["route"] == "OK":
                result["route"] = "USER_DECISION_REQUIRED"

        decisions = conn.execute(
            """SELECT decision_id, ruling_json, approval_ref, project_ids_json, created_at
               FROM source_version_decision_log WHERE logical_document_id=? ORDER BY created_at DESC LIMIT 5""",
            (family,),
        ).fetchall()
        result["logical_documents"][family] = {
            "current": [m["source_version_id"] for m in current],
            "superseded": [m["source_version_id"] for m in superseded],
            "unresolved": [m["source_version_id"] for m in unresolved],
            "decisions": [
                {
                    "decision_id": d["decision_id"],
                    "ruling": json.loads(d["ruling_json"] or "{}"),
                    "approval_ref": d["approval_ref"],
                    "project_ids": json.loads(d["project_ids_json"] or "[]"),
                    "created_at": d["created_at"],
                }
                for d in decisions
            ],
        }
    return result


def filter_current_source_versions(
    source_version_ids: list[str],
    conn: sqlite3.Connection,
    *,
    include_historical: bool = False,
) -> tuple[list[str], dict[str, Any]]:
    """Return source versions effective for CURRENT synthesis."""
    if include_historical:
        return list(dict.fromkeys(_text(x) for x in source_version_ids if _text(x))), {"mode": "historical"}

    ids = [_text(x) for x in source_version_ids if _text(x)]
    if not ids:
        return [], {"mode": "current", "excluded": []}

    if not governance_schema_present(conn):
        return list(dict.fromkeys(ids)), {
            "mode": "current",
            "governance_schema": "absent",
            "excluded": [],
            "unresolved": [],
            "conflicts": [],
        }

    excluded: list[str] = []
    kept: list[str] = []
    meta: dict[str, Any] = {"mode": "current", "excluded": excluded, "unresolved": [], "conflicts": []}
    by_family: dict[str, list[str]] = defaultdict(list)
    status_by_id: dict[str, str | None] = {}
    for sid in ids:
        row = conn.execute(
            "SELECT governance_status FROM source_version_governance WHERE source_version_id=?",
            (sid,),
        ).fetchone()
        status = _text(row[0]) if row else None
        status_by_id[sid] = status
        by_family[logical_source_identity(sid)].append(sid)

    for family, members in by_family.items():
        if not family:
            kept.extend(members)
            continue
        current_members = [m for m in members if status_by_id.get(m) == STATUS_CURRENT]
        if len(current_members) > 1:
            meta["conflicts"].append({"logical_document_id": family, "current": current_members})
            meta["unresolved"].extend(members)
            excluded.extend(members)
            continue
        if len(members) > 1 and not current_members:
            meta["conflicts"].append({"logical_document_id": family, "competing": members})
            meta["unresolved"].extend(members)
            excluded.extend(members)
            continue
        for sid in members:
            status = status_by_id.get(sid)
            if status is None or status == STATUS_CURRENT:
                kept.append(sid)
            elif status == STATUS_UNRESOLVED:
                meta["unresolved"].append(sid)
            elif status in {STATUS_SUPERSEDED, STATUS_ARCHIVED}:
                excluded.append(sid)
            else:
                kept.append(sid)
    return list(dict.fromkeys(kept)), meta


def identify_stale_outcome_refs(outcome: Mapping[str, Any], conn: sqlite3.Connection) -> dict[str, Any]:
    """Mark derivations whose ONLY supporting evidence is superseded/archived."""
    refs = [dict(x) for x in (outcome.get("source_evidence_refs") or []) if isinstance(x, Mapping)]
    if not governance_schema_present(conn):
        return {
            "stale_refs": [],
            "active_refs": refs,
            "needs_resynthesis": False,
            "governance_schema": "absent",
        }
    stale: list[dict[str, Any]] = []
    active_support: list[dict[str, Any]] = []
    for ref in refs:
        sid = _text(ref.get("source_version_id"))
        if not sid:
            continue
        row = conn.execute(
            "SELECT governance_status FROM source_version_governance WHERE source_version_id=?",
            (sid,),
        ).fetchone()
        status = _text(row[0]) if row else STATUS_CURRENT
        if ref.get("superseded") is True or status in {STATUS_SUPERSEDED, STATUS_ARCHIVED}:
            stale.append(ref)
        elif status == STATUS_CURRENT or row is None:
            active_support.append(ref)
    only_stale = bool(stale) and not active_support
    return {
        "stale_refs": stale,
        "active_refs": active_support,
        "needs_resynthesis": only_stale,
    }


def _load_resynthesis_store() -> dict[str, Any]:
    try:
        return json.loads(RESYNTHESIS_STORE.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {"projects": {}}


def _save_resynthesis_store(data: dict[str, Any]) -> None:
    RESYNTHESIS_STORE.parent.mkdir(parents=True, exist_ok=True)
    RESYNTHESIS_STORE.write_text(_json(data), encoding="utf-8")


def _mark_projects_for_resynthesis(project_ids: list[str], *, reason: str, logical_document_id: str) -> None:
    data = _load_resynthesis_store()
    projects = data.setdefault("projects", {})
    now = _now()
    for pid in _unique(project_ids):
        entry = dict(projects.get(pid) or {})
        entry.update({
            "project_id": pid,
            "reason": reason,
            "logical_document_id": logical_document_id,
            "marked_at": now,
            "needs_resynthesis": True,
        })
        projects[pid] = entry
    _save_resynthesis_store(data)


def list_impacted_resynthesis_projects(*, project_id: str | None = None) -> list[dict[str, Any]]:
    data = _load_resynthesis_store()
    projects = data.get("projects") or {}
    if project_id:
        item = projects.get(project_id)
        return [item] if isinstance(item, dict) and item.get("needs_resynthesis") else []
    return [v for v in projects.values() if isinstance(v, dict) and v.get("needs_resynthesis")]


def clear_resynthesis_mark(project_id: str) -> None:
    data = _load_resynthesis_store()
    projects = data.get("projects") or {}
    if project_id in projects:
        entry = dict(projects[project_id])
        entry["needs_resynthesis"] = False
        entry["cleared_at"] = _now()
        projects[project_id] = entry
        data["projects"] = projects
        _save_resynthesis_store(data)


def project_description_projection(
    conn: sqlite3.Connection,
    project_id: str,
    outcome: Mapping[str, Any] | None = None,
    *,
    explainer: Mapping[str, Any] | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """Seed Project.description from accepted synthesis — once, never again.

    PAW v1.2 phase 3 (technical debt D): ``project.description`` is the
    *stable identity statement* of a Project Registry entry.  Project Outcome,
    Project Handbook and Project Explainer are **derived** state and must not
    be projected back into it.

    The previous behaviour overwrote the description with
    ``outcome.executive_summary`` on every closure round.  Combined with
    ``paw_project_outcome._quality_summary`` (which re-appended the previous
    ``latest_progress_summary`` into the next summary) this recursively
    inflated the description — e.g. 运营导师体系 reached 304 chars carrying
    "保留既有项目记录：" twice, and three projects lost their human-authored
    identity text entirely, replaced by the evidence-insufficient boilerplate.

    New contract:

    * a registry entry that already has a description keeps it — derived
      synthesis never overwrites or appends to it;
    * a registry entry with no description yet is seeded exactly once from the
      accepted explainer / outcome summary, so new projects still get a
      meaningful registry line;
    * ``None`` always means "no write", and the returned provenance records
      *why* nothing was written.

    Returns ``(description_or_None, provenance)``.
    """
    summary = ""
    source = ""
    if isinstance(explainer, Mapping):
        for key in ("one_sentence", "current_state", "executive_summary"):
            candidate = _text(explainer.get(key))
            if candidate:
                summary = candidate
                source = f"project_explainer.{key}"
                break
    if not summary and isinstance(outcome, Mapping):
        summary = _text(outcome.get("executive_summary"))
        source = "project_outcome.executive_summary"

    row = conn.execute("SELECT description FROM project WHERE id=?", (project_id,)).fetchone()
    current = _text(row[0]) if row else ""
    provenance: dict[str, Any] = {
        "source": source or PROJECT_DESCRIPTION_IDENTITY_OWNER,
        "identity_owner": PROJECT_DESCRIPTION_IDENTITY_OWNER,
        "synthesis_revision": outcome.get("synthesis_revision") if isinstance(outcome, Mapping) else None,
        "evidence_fingerprint": outcome.get("evidence_fingerprint") if isinstance(outcome, Mapping) else None,
        "projected_at": _now(),
        "idempotent_skip": False,
        "write_skipped_reason": "",
    }

    if current:
        # Identity statement already present: derived synthesis must not
        # overwrite it, and must never be concatenated onto it.
        provenance["idempotent_skip"] = current == summary
        provenance["write_skipped_reason"] = "project_description_is_stable_identity"
        return None, provenance

    if not summary:
        provenance["write_skipped_reason"] = "no_accepted_synthesis_available"
        return None, provenance

    provenance["write_skipped_reason"] = "seeded_initial_identity_description"
    return summary[:PROJECT_DESCRIPTION_SEED_LIMIT], provenance


def apply_project_description_projection(conn: sqlite3.Connection, payload: Mapping[str, Any]) -> str | None:
    """Apply description projection after a governed project outcome commit."""
    project_id = _text(payload.get("id"))
    if not project_id:
        return None
    outcome = payload.get("project_outcome") if isinstance(payload.get("project_outcome"), Mapping) else None
    explainer = payload.get("project_explainer") if isinstance(payload.get("project_explainer"), Mapping) else None
    description, prov = project_description_projection(conn, project_id, outcome, explainer=explainer)
    if description is None:
        return None
    conn.execute(
        "UPDATE project SET description=?, updated_at=? WHERE id=?",
        (description, _now(), project_id),
    )
    payload_prov = payload.get("description_provenance")
    if isinstance(payload_prov, dict):
        prov = {**prov, **payload_prov}
    conn.execute(
        """INSERT INTO audit_event(id, event_type, actor, target_type, target_id, change_id, commit_id, details, created_at)
           VALUES (?, 'project_description_projection', 'paw_source_version_governance', 'project', ?, NULL, NULL, ?, ?)""",
        (_uuid(), project_id, _json(prov), _now()),
    )
    return description


def read_project_version_context(db_path: str, project_id: str) -> dict[str, Any]:
    """Read-only version governance block for project-first context."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if not governance_schema_present(conn):
            ctx = empty_version_context(project_id)
            try:
                row = conn.execute("SELECT description FROM project WHERE id=?", (project_id,)).fetchone()
                if row:
                    ctx["description_projection"] = {"current_description": _text(row[0])}
            except sqlite3.OperationalError:
                pass
            return ctx
        outcome_row = conn.execute(
            "SELECT source_evidence_refs_json, executive_summary, synthesis_revision, evidence_fingerprint FROM project_outcome WHERE project_id=?",
            (project_id,),
        ).fetchone()
        source_ids: list[str] = []
        outcome: dict[str, Any] = {}
        if outcome_row:
            refs = json.loads(outcome_row["source_evidence_refs_json"] or "[]")
            source_ids = _unique([_text(x.get("source_version_id")) for x in refs if isinstance(x, dict)])
            outcome = {
                "executive_summary": outcome_row["executive_summary"],
                "synthesis_revision": outcome_row["synthesis_revision"],
                "evidence_fingerprint": outcome_row["evidence_fingerprint"],
                "source_evidence_refs": refs,
            }
        families = sorted({logical_source_identity(sid) for sid in source_ids if sid})
        authority = resolve_authority(
            conn,
            logical_document_id=families[0] if len(families) == 1 else None,
            source_version_ids=source_ids,
        )
        stale = identify_stale_outcome_refs(outcome, conn) if outcome else {"needs_resynthesis": False, "stale_refs": [], "active_refs": []}
        current_ids, filter_meta = filter_current_source_versions(source_ids, conn)
        superseded_ids: list[str] = []
        archived_ids: list[str] = []
        unresolved_ids: list[str] = list(filter_meta.get("unresolved") or [])
        version_decisions: list[dict[str, Any]] = []
        for family in authority.get("logical_documents", {}).values():
            superseded_ids.extend(family.get("superseded") or [])
            unresolved_ids.extend(family.get("unresolved") or [])
            version_decisions.extend(family.get("decisions") or [])
        for family in authority.get("logical_documents", {}).values():
            for sid in family.get("superseded") or []:
                if sid not in superseded_ids:
                    superseded_ids.append(sid)
        description_projection: dict[str, Any] = {}
        try:
            row = conn.execute("SELECT description FROM project WHERE id=?", (project_id,)).fetchone()
            projected, prov = project_description_projection(conn, project_id, outcome or None)
            description_projection = {
                "current_description": _text(row[0]) if row else "",
                "projected_description": projected,
                "provenance": prov,
            }
        except sqlite3.OperationalError:
            pass
        return {
            "schema_version": GOVERNANCE_SCHEMA,
            "project_id": project_id,
            "governance_schema": "present",
            "current_source_versions": current_ids,
            "all_linked_source_versions": source_ids,
            "superseded_source_versions": _unique(superseded_ids),
            "archived_source_versions": _unique(archived_ids),
            "unresolved_conflicts": _unique(unresolved_ids),
            "version_decisions": version_decisions,
            "filter_meta": filter_meta,
            "authority": authority,
            "stale_derivations": stale,
            "resynthesis": list_impacted_resynthesis_projects(project_id=project_id),
            "description_projection": description_projection,
            "formal_state_bypass": 0,
        }
    finally:
        conn.close()


def seed_unresolved_family(conn: sqlite3.Connection, source_version_ids: list[str]) -> None:
    """Register unknown members without inferring winners."""
    for sid in _unique(source_version_ids):
        family = _ensure_logical_document(conn, sid)
        existing = conn.execute(
            "SELECT governance_status FROM source_version_governance WHERE source_version_id=?",
            (sid,),
        ).fetchone()
        if existing:
            continue
        _upsert_membership(
            conn,
            source_version_id=sid,
            logical_document_id=family,
            governance_status=STATUS_UNRESOLVED,
            authority_rank=99,
            authority_reason="registered_without_ruling",
            confidence="LOW",
        )


def register_committer_integration() -> None:
    """Hook governance into the existing project.update committer executor."""
    global _COMMITTER_HOOKED, _ORIGINAL_PROJECT_UPDATE
    if _COMMITTER_HOOKED:
        return
    import committer as committer_mod

    _ORIGINAL_PROJECT_UPDATE = committer_mod._execute_project_update

    def _execute_project_update_with_governance(conn: sqlite3.Connection, payload: dict) -> str:
        ruling = payload.get("version_ruling")
        if isinstance(ruling, Mapping) and payload.get("governance_only"):
            project_id = _text(payload.get("id"))
            logical_id = apply_version_ruling(
                conn,
                ruling,
                project_ids=[project_id] if project_id else [],
            )
            return logical_id
        target_id = _ORIGINAL_PROJECT_UPDATE(conn, payload)
        if governance_schema_present(conn):
            try:
                apply_project_description_projection(conn, payload)
            except sqlite3.OperationalError:
                pass
            if isinstance(payload.get("project_outcome"), Mapping):
                stale = identify_stale_outcome_refs(payload["project_outcome"], conn)
                if stale.get("needs_resynthesis"):
                    _mark_projects_for_resynthesis([target_id], reason="stale_outcome_support", logical_document_id="")
        return target_id

    _execute_project_update_with_governance._paw_governance_wrapped = True  # type: ignore[attr-defined]
    committer_mod._execute_project_update = _execute_project_update_with_governance
    committer_mod.EXECUTORS[("project", "update")] = _execute_project_update_with_governance
    _COMMITTER_HOOKED = True


def install_committer_integration() -> None:
    """Explicit production entrypoint for canonical committer governance hooks."""
    register_committer_integration()


def is_committer_integration_active() -> bool:
    """Return True when the canonical committer project.update executor is wrapped."""
    if not _COMMITTER_HOOKED:
        return False
    import committer as committer_mod

    fn = committer_mod.EXECUTORS.get(("project", "update"))
    return bool(getattr(fn, "_paw_governance_wrapped", False))


def commit_approved_change(change_id: str, *, db_path: str | None = None) -> dict[str, Any]:
    """Commit through the canonical committer with governance integration installed."""
    install_committer_integration()
    import committer as committer_mod

    previous_db_path = getattr(committer_mod, "DB_PATH", None)
    if db_path is not None:
        committer_mod.DB_PATH = db_path
    try:
        return committer_mod.commit_approved_change(change_id)
    finally:
        if db_path is not None:
            committer_mod.DB_PATH = previous_db_path if previous_db_path is not None else db_path


__all__ = [
    "GOVERNANCE_SCHEMA",
    "apply_project_description_projection",
    "apply_version_ruling",
    "build_version_ruling_envelope",
    "clear_resynthesis_mark",
    "commit_approved_change",
    "empty_version_context",
    "ensure_schema",
    "filter_current_source_versions",
    "governance_schema_present",
    "identify_stale_outcome_refs",
    "install_committer_integration",
    "is_committer_integration_active",
    "list_impacted_resynthesis_projects",
    "parse_capture_version_directive",
    "project_description_projection",
    "read_project_version_context",
    "register_committer_integration",
    "resolve_authority",
    "seed_unresolved_family",
]
