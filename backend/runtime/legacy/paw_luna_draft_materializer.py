#!/usr/bin/python3
"""Deterministic Luna Draft materializer — projects existing semantic artifacts into Draft store."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gpt_brain_common import safe_copy, text, utc_now
from paw_gpt_review_bundle import ReviewBundleStore, default_brain_path, get_bundle_preanalysis
from paw_luna_draft_contract import (
    AUTHORITY_DRAFT,
    merge_draft_status,
    map_bundle_state_to_draft_status,
    projection_status_for_coverage,
    stable_draft_id,
)
from paw_luna_draft_semantic import LunaSemanticReader, extract_projection_fields
from paw_luna_draft_store import migrate, upsert_draft

DEFAULT_BRAIN = default_brain_path()


def _load_brain(path: str | os.PathLike[str]) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _project_index(brain: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for item in (brain.get("project_consolidations") or {}).values():
        if not isinstance(item, Mapping):
            continue
        for svid in item.get("source_version_ids") or []:
            out.setdefault(str(svid), []).append({
                "project_id": item.get("project_id"),
                "project_name": item.get("project_name"),
                "project_tier": item.get("project_tier"),
            })
    for item in (brain.get("knowledge_topics") or {}).values():
        if not isinstance(item, Mapping):
            continue
        for svid in item.get("source_version_ids") or []:
            out.setdefault(str(svid), []).append({
                "project_id": item.get("project_or_domain"),
                "project_name": item.get("project_or_domain_name") or item.get("canonical_topic"),
            })
    return out


def _canonical_linkage(conn_path: str, source_version_id: str) -> tuple[str | None, str | None]:
    import sqlite3
    path = Path(conn_path)
    if not path.exists():
        return None, None
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT applied_entity_type, applied_entity_id FROM candidate "
        "WHERE applied_entity_type IS NOT NULL AND applied_entity_id IS NOT NULL "
        "AND source_refs LIKE ? ORDER BY reviewed_at DESC LIMIT 1",
        (f"%{source_version_id}%",),
    ).fetchone()
    if row:
        conn.close()
        return str(row["applied_entity_type"]), str(row["applied_entity_id"])
    row = conn.execute(
        "SELECT cr.id FROM commit_record cr "
        "JOIN approved_change ac ON ac.change_id = cr.change_id "
        "WHERE ac.payload LIKE ? ORDER BY cr.committed_at DESC LIMIT 1",
        (f"%{source_version_id}%",),
    ).fetchone()
    conn.close()
    if row:
        return "commit_record", str(row["id"])
    return None, None


def _wiki_source_path(source_id: str, db_path: str) -> str | None:
    import sqlite3
    if not source_id:
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT source_path, title FROM wiki_page WHERE id=? OR canonical_source_id=? LIMIT 1",
        (source_id, source_id),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return text(row["source_path"]) or text(row["title"]) or None


def materialize_source(
    source_version_id: str,
    *,
    brain: Mapping[str, Any],
    bundle_store: ReviewBundleStore,
    reader: LunaSemanticReader,
    db_path: str,
    queue_entry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    bootstrap = brain.get("bootstrap") or {}
    source_states = bootstrap.get("source_states") or {}
    source_state = source_states.get(source_version_id) or {}
    coverage_state = text(source_state.get("state"))
    semantic_read = reader.read_semantic(source_version_id, queue_entry)
    has_semantic = bool(semantic_read.get("available"))
    extracted = extract_projection_fields(
        semantic_read.get("semantic_result") if has_semantic else None,
        source_state,
    )
    bundle_data = bundle_store.load()
    bundle_id = (bundle_data.get("source_index") or {}).get(source_version_id)
    bundle = (bundle_data.get("bundles") or {}).get(bundle_id) if bundle_id else None
    canonical_type, canonical_id = _canonical_linkage(db_path, source_version_id)
    review_status = map_bundle_state_to_draft_status(
        bundle,
        canonical_entity_type=canonical_type,
        canonical_entity_id=canonical_id,
    )
    coverage_status = projection_status_for_coverage(coverage_state, has_semantic)
    status = merge_draft_status(review_status, coverage_status)
    projects = _project_index(brain).get(source_version_id) or []
    primary_project = projects[0] if projects else {}
    source_id = extracted.get("source_id") or text((queue_entry or {}).get("source_id"))
    source_path = _wiki_source_path(source_id, db_path)
    luna_pre = get_bundle_preanalysis(bundle or {}, source_state) if bundle else {}
    provenance = {
        "schema": "paw-luna-draft-provenance/1",
        "source_version_id": source_version_id,
        "coverage_state": coverage_state,
        "semantic_available": has_semantic,
        "semantic_path": semantic_read.get("path"),
        "semantic_artifacts": [
            {"worker": a.get("worker"), "model": a.get("model"), "path": a.get("path"), "inherited": a.get("inherited")}
            for a in semantic_read.get("semantic_artifacts") or []
        ],
        "processor_provenance": safe_copy(source_state.get("processor_provenance") or {}),
        "luna_preanalysis": safe_copy(luna_pre),
        "review_bundle_state": text((bundle or {}).get("state")),
        "materialized_at": utc_now(),
    }
    record = {
        "draft_id": stable_draft_id(source_version_id),
        "source_id": source_id,
        "source_version_id": source_version_id,
        "source_title": extracted.get("source_title") or text((queue_entry or {}).get("title")),
        "source_path": source_path,
        "project_id": primary_project.get("project_id"),
        "project_name": primary_project.get("project_name"),
        "project_tier": primary_project.get("project_tier"),
        "summary": extracted.get("summary"),
        "facts": extracted.get("facts") or [],
        "decisions": extracted.get("decisions") or [],
        "metrics": extracted.get("metrics") or [],
        "timeline": extracted.get("timeline") or [],
        "risks": extracted.get("risks") or [],
        "ambiguities": extracted.get("ambiguities") or [],
        "related_projects": projects,
        "evidence_refs": extracted.get("evidence_refs") or [],
        "provenance": provenance,
        "preanalysis_processor": extracted.get("preanalysis_processor"),
        "preanalysis_model": extracted.get("preanalysis_model"),
        "status": status,
        "authority_level": AUTHORITY_DRAFT,
        "is_canonical": False,
        "review_bundle_id": bundle_id,
        "canonical_entity_type": canonical_type,
        "canonical_entity_id": canonical_id,
        "coverage_state": coverage_state,
        "semantic_hash": extracted.get("semantic_hash"),
        "ingestion_error": None if has_semantic or coverage_state in {"NO_KNOWLEDGE_IMPACT", "PROJECT_CONTEXT_ONLY", "FAILED"} else "semantic_result_missing",
    }
    return upsert_draft(record, db_path)


def materialize_all(
    *,
    brain_path: str | os.PathLike[str] | None = None,
    db_path: str | None = None,
    source_version_ids: list[str] | None = None,
) -> dict[str, Any]:
    brain_path = Path(brain_path or os.environ.get("PAW_GPT_BRAIN_STATE_PATH", DEFAULT_BRAIN))
    db_path = str(db_path or os.environ.get("PAW_WORKSPACE_DB", os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))))
    migrate(db_path)
    brain = _load_brain(brain_path)
    bootstrap = brain.get("bootstrap") or {}
    frozen = bootstrap.get("frozen_queue") or {}
    queue_total = int(frozen.get("total_sources") or len(bootstrap.get("source_order") or []))
    order = list(source_version_ids or bootstrap.get("source_order") or [])
    reader = LunaSemanticReader()
    queue_map = {item["source_version_id"]: item for item in reader.queue_sources()}
    bundle_store = ReviewBundleStore()
    stats = {
        "queue_total": queue_total,
        "requested": len(order),
        "materialized": 0,
        "updated": 0,
        "skipped_no_semantic": 0,
        "failed": 0,
        "by_status": {},
        "by_project": {},
        "errors": [],
    }
    for svid in order:
        try:
            result = materialize_source(
                svid,
                brain=brain,
                bundle_store=bundle_store,
                reader=reader,
                db_path=db_path,
                queue_entry=queue_map.get(svid),
            )
            action = result.get("action")
            if action == "inserted":
                stats["materialized"] += 1
            else:
                stats["updated"] += 1
            if result.get("ingestion_error"):
                stats["skipped_no_semantic"] += 1
            status = text(result.get("status")) or "UNKNOWN"
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1
            project = text(result.get("project_id")) or "(unassigned)"
            stats["by_project"][project] = stats["by_project"].get(project, 0) + 1
        except Exception as exc:
            stats["failed"] += 1
            stats["errors"].append({"source_version_id": svid, "error": str(exc)})
            try:
                upsert_draft({
                    "source_version_id": svid,
                    "source_title": text((queue_map.get(svid) or {}).get("title")),
                    "summary": f"Draft ingestion error: {exc}",
                    "status": projection_status_for_coverage(
                        text((bootstrap.get("source_states") or {}).get(svid, {}).get("state")),
                        False,
                    ),
                    "coverage_state": text((bootstrap.get("source_states") or {}).get(svid, {}).get("state")),
                    "ingestion_error": str(exc),
                }, db_path)
            except Exception:
                pass
    stats["draft_source_coverage"] = stats["materialized"] + stats["updated"]
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize Luna Draft projections from existing semantic artifacts.")
    parser.add_argument("--brain", default=str(DEFAULT_BRAIN))
    parser.add_argument("--db", default=os.environ.get("PAW_WORKSPACE_DB", os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))))
    parser.add_argument("--source-version-id", action="append", dest="source_version_ids")
    parser.add_argument("--migrate-only", action="store_true")
    args = parser.parse_args(argv)
    if args.migrate_only:
        print(json.dumps(migrate(args.db), ensure_ascii=False, indent=2))
        return 0
    result = materialize_all(brain_path=args.brain, db_path=args.db, source_version_ids=args.source_version_ids)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
