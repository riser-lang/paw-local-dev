#!/usr/bin/env python3
"""Apply GPT Patrol authored Project Explainer/Handbook revisions.

Python never generates business summaries. Unaffected sections of an existing
semantic handbook are preserved. Deterministic field concatenation is refused.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping

from gpt_brain_common import utc_now
from paw_patrol_contract import FORMAL_STATE_BYPASS, canonical_json, fingerprint, _list, _mapping, _text

HANDBOOK_SCHEMA_VERSION = "paw-project-handbook/1"
AUTHORITY = "analysis_plane_semantic_artifact"


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def handbook_is_semantic_artifact(sections: Mapping[str, Any] | None) -> bool:
    if not isinstance(sections, Mapping):
        return False
    if "why_and_background" in sections and "historical_evolution" in sections:
        return True
    explainer = sections.get("project_explainer")
    if isinstance(explainer, Mapping) and explainer.get("authority") in {AUTHORITY, "gpt_patrol"}:
        return True
    rules = sections.get("current_effective_rules")
    return (
        isinstance(rules, list)
        and bool(rules)
        and isinstance(rules[0], Mapping)
        and "state" in rules[0]
        and "text" in rules[0]
    )


def merge_authored_sections(
    current: Mapping[str, Any] | None,
    authored: Mapping[str, Any] | None,
    *,
    changed_sections: list[str] | None = None,
) -> dict[str, Any]:
    current_sections = dict(_mapping(current))
    authored_sections = dict(_mapping(authored))
    if not authored_sections:
        return current_sections
    requested = [_text(x) for x in (changed_sections or authored_sections.keys()) if _text(x)]
    merged = dict(current_sections)
    for name in requested:
        if name in authored_sections:
            merged[name] = authored_sections[name]
    return merged


def apply_authored_project_revision(
    *,
    db_path: str,
    project_id: str,
    explainer_delta: Mapping[str, Any] | None = None,
    handbook_delta: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    patrol_run_id: str | None = None,
    input_watermark: str | None = None,
) -> dict[str, Any]:
    """Merge GPT Patrol authored sections into the analysis-plane handbook.

    Formal State (canonical Project row business fields) is not written here.
    """
    project_id = _text(project_id)
    if not project_id:
        return {"status": "FAILED_CLOSED", "reason": "project_id_required", "formal_state_bypass": 0}
    authored_by = _text((_mapping(provenance).get("authoritative_owner") or _mapping(handbook_delta).get("authored_by") or _mapping(explainer_delta).get("authored_by")))
    if _mapping(provenance).get("python_generated_summary") is True:
        return {"status": "FAILED_CLOSED", "reason": "python_generated_summary_forbidden", "formal_state_bypass": 0}
    if authored_by and authored_by.lower() in {"deterministic-project-synthesizer-v1", "project-handbook-governor"}:
        return {"status": "FAILED_CLOSED", "reason": "deterministic_summary_forbidden", "formal_state_bypass": 0}

    explainer_payload = _mapping(explainer_delta)
    if "payload" in explainer_payload:
        explainer_payload = _mapping(explainer_payload.get("payload")) or explainer_payload
    handbook_payload = _mapping(handbook_delta)
    if "payload" in handbook_payload:
        handbook_payload = _mapping(handbook_payload.get("payload")) or handbook_payload
    authored_sections = _mapping(handbook_payload.get("sections") or handbook_payload.get("project_handbook"))
    changed = [_text(x) for x in _list(handbook_payload.get("changed_sections") or explainer_payload.get("changed_sections")) if _text(x)]
    authored_explainer = _mapping(explainer_payload.get("explainer") or explainer_payload.get("project_explainer") or (explainer_payload if explainer_payload.get("next_step") or explainer_payload.get("one_sentence") else {}))
    if authored_explainer:
        authored_explainer = {
            **authored_explainer,
            "authority": authored_explainer.get("authority") or AUTHORITY,
        }
        authored_sections = {**authored_sections, "project_explainer": authored_explainer}
        if "project_explainer" not in changed:
            changed.append("project_explainer")
    if not authored_sections:
        return {"status": "NO_CHANGE", "reason": "no_authored_sections", "formal_state_bypass": 0, "project_id": project_id}

    conn = sqlite3.connect(db_path, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS project_handbook (
                project_id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                sections_json TEXT NOT NULL DEFAULT '{}',
                section_metadata_json TEXT NOT NULL DEFAULT '{}',
                generated_at TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                input_watermark TEXT NOT NULL,
                evidence_fingerprint TEXT NOT NULL DEFAULT '',
                quality_gate TEXT NOT NULL,
                evidence_sufficient INTEGER NOT NULL DEFAULT 0,
                baseline_input_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        prior = conn.execute("SELECT * FROM project_handbook WHERE project_id=?", (project_id,)).fetchone()
        current_sections = _json(prior["sections_json"], {}) if prior is not None else {}
        merged = merge_authored_sections(current_sections, authored_sections, changed_sections=changed or None)
        for name, value in current_sections.items():
            if name not in (changed or authored_sections.keys()):
                merged[name] = value
        now = utc_now()
        revision = int(prior["revision"]) + 1 if prior is not None else 1
        watermark = _text(input_watermark) or fingerprint({
            "project_id": project_id,
            "patrol_run_id": patrol_run_id,
            "changed": changed,
            "sections": {name: merged.get(name) for name in (changed or merged.keys())},
        })
        if prior is not None and prior["input_watermark"] == watermark:
            return {
                "status": "IDEMPOTENT",
                "idempotent": True,
                "revision": prior["revision"],
                "project_id": project_id,
                "formal_state_bypass": FORMAL_STATE_BYPASS,
            }
        meta = {
            "authority": AUTHORITY,
            "authored_by": authored_by or "gpt_patrol",
            "patrol_run_id": patrol_run_id,
            "changed_sections": changed,
            "python_generated_summary": False,
            "preserved_unaffected_sections": True,
            "source_refs": _list(handbook_payload.get("source_refs") or explainer_payload.get("source_refs") or _mapping(provenance).get("source_refs")),
        }
        baseline = fingerprint({"watermark": watermark, "sections": merged})
        payload = (
            project_id, HANDBOOK_SCHEMA_VERSION, canonical_json(merged), canonical_json(meta),
            now, revision, watermark, _text(_mapping(provenance).get("evidence_fingerprint")),
            "PASS", 1, baseline, now if prior is None else prior["created_at"], now,
        )
        conn.execute(
            """INSERT INTO project_handbook(
                project_id, schema_version, sections_json, section_metadata_json, generated_at,
                revision, input_watermark, evidence_fingerprint, quality_gate, evidence_sufficient,
                baseline_input_hash, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(project_id) DO UPDATE SET
                sections_json=excluded.sections_json,
                section_metadata_json=excluded.section_metadata_json,
                generated_at=excluded.generated_at,
                revision=excluded.revision,
                input_watermark=excluded.input_watermark,
                evidence_fingerprint=excluded.evidence_fingerprint,
                quality_gate=excluded.quality_gate,
                evidence_sufficient=excluded.evidence_sufficient,
                baseline_input_hash=excluded.baseline_input_hash,
                updated_at=excluded.updated_at
            """,
            payload,
        )
        conn.commit()
        readback = conn.execute("SELECT revision, input_watermark, sections_json FROM project_handbook WHERE project_id=?", (project_id,)).fetchone()
        return {
            "status": "COMMITTED",
            "project_id": project_id,
            "revision": readback["revision"] if readback else revision,
            "input_watermark": readback["input_watermark"] if readback else watermark,
            "changed_sections": changed,
            "preserved_unaffected_sections": True,
            "canonical_readback": bool(readback),
            "python_generated_summary": False,
            "formal_state_bypass": FORMAL_STATE_BYPASS,
        }
    except Exception as exc:
        conn.rollback()
        return {"status": "FAILED_CLOSED", "reason": str(exc)[:500], "formal_state_bypass": 0}
    finally:
        conn.close()
