#!/usr/bin/env python3
"""Read-only Analysis Plane indexes and packet interfaces for PAW R13E.

The scan consumes canonical Reader Evidence and already-written semantic
results.  It never invokes a model and never writes Formal State.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from semantic_supervisor_v2 import (
    BASE, CORE_BATCHES, CORE_PROJECT_NAMES, CanonicalReaderIndex, PersistentQueue,
    ResourceGuard, desired_queues, read_json, result_is_success, safe_name,
    atomic_json, utc_now,
)


V2_ROOT = BASE / "state" / "semantic_v2"
MODEL_DIRS = {"flash": "agy", "qwen": "qwen", "visual": "gemini31pro"}
SECTIONS = ("facts", "inferences", "project_signals", "timeline", "metrics", "decisions_or_plans", "conflicts", "risks", "ambiguities")


def _index(index: CanonicalReaderIndex | None = None) -> CanonicalReaderIndex:
    return index or CanonicalReaderIndex()


def _records(index: CanonicalReaderIndex) -> list[Any]:
    return index.records()


def _semantic_rows(index: CanonicalReaderIndex) -> Iterable[dict[str, Any]]:
    for record in _records(index):
        for worker, model_dir in MODEL_DIRS.items():
            path = index.semantic_path(model_dir, record)
            value = read_json(path)
            if not result_is_success(path, record.source_version_id):
                continue
            yield {"worker": worker, "model": value.get("model"), "path": str(path), "record": record, "result": value}


def _evidence_item(row: dict[str, Any], section: str, item: Any) -> dict[str, Any]:
    record = row["record"]
    item = item if isinstance(item, dict) else {"content": str(item), "confidence": "low", "evidence_locators": []}
    return {
        "content": str(item.get("content", "")), "confidence": item.get("confidence", "low"),
        "evidence_locators": list(item.get("evidence_locators") or []), "section": section,
        "model": row["model"], "worker": row["worker"], "result_path": row["path"],
        "source_id": record.source_id, "source_version_id": record.source_version_id,
        "raw_sha256": record.raw_sha256, "reader_result_path": record.reader_result_path,
        "project": record.paw_project_name, "batch": record.batch,
    }


def _all_items(index: CanonicalReaderIndex, sections: tuple[str, ...] = SECTIONS) -> dict[str, list[dict[str, Any]]]:
    output = {section: [] for section in sections}
    for row in _semantic_rows(index):
        analysis = row["result"].get("analysis") or {}
        for section in sections:
            for item in analysis.get(section, []) if isinstance(analysis.get(section), list) else []:
                output[section].append(_evidence_item(row, section, item))
    for section in output:
        output[section].sort(key=lambda x: (x["source_version_id"], x["model"] or "", x["evidence_locators"], x["content"]))
    return output


def _queue_metrics(worker: str, index: CanonicalReaderIndex, state_root: Path = V2_ROOT) -> dict[str, Any]:
    q = PersistentQueue(worker, state_root)
    counts = q.counts()
    successes: list[float] = []
    for item in q.snapshot().get("items", {}).values():
        stamp = item.get("last_success_at")
        if stamp:
            try:
                value = datetime.fromisoformat(stamp).timestamp()
                if value >= time.time() - 3600:
                    successes.append(value)
            except (TypeError, ValueError):
                pass
    throughput = len(successes)
    return {"queue_depth": counts["depth"], "pending": counts["pending"], "running": counts["running"], "succeeded": counts["succeeded"], "failed": counts["failed"], "paused": counts["paused"], "throughput_per_hour": throughput, "eta_seconds": None if not throughput else round(counts["depth"] / throughput * 3600, 1)}


def get_semantic_progress(index: CanonicalReaderIndex | None = None) -> dict[str, Any]:
    index = _index(index); records = _records(index)
    result = {worker: {"success": sum(index.semantic_success(model, record) for record in records), "expected": len(records)} for worker, model in MODEL_DIRS.items()}
    flash_coverage = result["flash"]["success"]
    queues = {worker: _queue_metrics(worker, index) for worker in MODEL_DIRS}
    return {"schema": "paw-semantic-progress-v2/1", "generated_at": utc_now(), "canonical_source_total": len(records), "coverage": {"covered": flash_coverage, "expected": len(records), "fraction": f"{flash_coverage}/{len(records)}"}, "workers": {worker: {**result[worker], **queues[worker]} for worker in MODEL_DIRS}, "resource": ResourceGuard(index.base).status(), "formal_state_write": "PROHIBITED"}


def get_semantic_batch_summary(index: CanonicalReaderIndex | None = None) -> dict[str, Any]:
    index = _index(index); output: dict[str, Any] = {}
    for batch in sorted({record.batch for record in _records(index)}):
        records = [r for r in _records(index) if r.batch == batch]
        output[batch] = {"expected": len(records), "flash_success": sum(index.semantic_success("agy", r) for r in records), "qwen_success": sum(index.semantic_success("qwen", r) for r in records), "visual_success": sum(index.semantic_success("gemini31pro", r) for r in records), "core": batch in CORE_BATCHES, "source_versions": [r.source_version_id for r in records]}
    return {"schema": "paw-semantic-batch-summary-v2/1", "generated_at": utc_now(), "batches": output}


def get_semantic_delta(since: str | None = None, index: CanonicalReaderIndex | None = None) -> dict[str, Any]:
    index = _index(index); threshold = 0.0
    if since:
        try: threshold = datetime.fromisoformat(since).timestamp()
        except ValueError: threshold = 0.0
    rows = []
    for row in _semantic_rows(index):
        path = Path(row["path"])
        if path.stat().st_mtime <= threshold:
            continue
        rows.append(row)
    # Delta is materialized from result files, not semantic_state aggregates.
    output = {section: [] for section in SECTIONS}
    for row in rows:
        analysis = row["result"].get("analysis") or {}
        for section in SECTIONS:
            output[section].extend(_evidence_item(row, section, item) for item in analysis.get(section, []) if isinstance(analysis.get(section), list))
    for section in output:
        output[section].sort(key=lambda x: (x["source_version_id"], x["model"] or "", x["content"]))
    return {"schema": "paw-semantic-delta-v2/1", "generated_at": utc_now(), "since": since, "result_count": len(rows), **output}


def get_project_analysis_packet(project_name: str, index: CanonicalReaderIndex | None = None) -> dict[str, Any]:
    index = _index(index); items = _all_items(index)
    # Only CORE sources are project evidence.  Non-core sources remain
    # relationship/reference signals in the cross-project section.
    return {"schema": "paw-project-analysis-packet-v2/1", "project": project_name, "core_project": project_name in CORE_PROJECT_NAMES, "sections": {section: [item for item in values if item["project"] == project_name and item["batch"] in CORE_BATCHES] for section, values in items.items()}, "cross_project_reference_signals": {section: [item for item in values if item["batch"] not in CORE_BATCHES and project_name in item["content"]] for section, values in items.items() if section in {"project_signals", "related_projects"}}, "formal_state_write": "PROHIBITED"}


def get_conflict_queue(index: CanonicalReaderIndex | None = None) -> list[dict[str, Any]]:
    index = _index(index); items = _all_items(index, ("conflicts", "ambiguities"))
    output = items["conflicts"] + items["ambiguities"]
    output.sort(key=lambda x: (x["source_version_id"], x["section"], x["content"]))
    return output


def get_metric_changes(index: CanonicalReaderIndex | None = None) -> list[dict[str, Any]]:
    index = _index(index); metrics = _all_items(index, ("metrics",))["metrics"]
    metrics.sort(key=lambda x: (x["source_version_id"], x["content"], x["evidence_locators"]))
    return metrics


def get_deep_read_queue(index: CanonicalReaderIndex | None = None, state_root: Path = V2_ROOT) -> list[dict[str, Any]]:
    index = _index(index); desired = desired_queues(index).get("qwen", []); q = PersistentQueue("qwen", state_root)
    states = q.snapshot().get("items", {})
    return [{"source_version_id": record.source_version_id, "batch": record.batch, "project": record.paw_project_name, "title": record.title, "status": states.get(record.key(), {}).get("status", "pending"), "source": record.as_meta()} for record in desired]


def get_analysis_evidence(source_version_id: str | None = None, locator: str | None = None, index: CanonicalReaderIndex | None = None) -> list[dict[str, Any]]:
    index = _index(index); items = _all_items(index)
    output = [item for values in items.values() for item in values if (source_version_id is None or item["source_version_id"] == source_version_id) and (locator is None or locator in item["evidence_locators"])]
    output.sort(key=lambda x: (x["source_version_id"], x["section"], x["evidence_locators"], x["content"]))
    return output


def _packet(index: CanonicalReaderIndex, state_root: Path = V2_ROOT) -> dict[str, Any]:
    items = _all_items(index)
    related: list[dict[str, Any]] = []
    for row in _semantic_rows(index):
        record = row["record"]; analysis = row["result"].get("analysis") or {}
        for relation in analysis.get("related_projects", []) if isinstance(analysis.get("related_projects"), list) else []:
            if isinstance(relation, dict):
                related.append({**relation, "model": row["model"], "source_version_id": record.source_version_id, "raw_sha256": record.raw_sha256, "reader_result_path": record.reader_result_path, "batch": record.batch})
    related.sort(key=lambda x: (x.get("source_version_id", ""), x.get("project", ""), x.get("relationship", "")))
    deep = get_deep_read_queue(index, state_root)
    progress = get_semantic_progress(index)
    return {
        "packet_version": "2026-09-03-v2", "generated_at": utc_now(), "purpose": "read-only Analysis Plane full-state packet; deterministic index scan, no model reread",
        "core_projects": list(CORE_PROJECT_NAMES), "new_facts": items["facts"], "new_metrics": items["metrics"], "timeline_updates": items["timeline"],
        "conflicts": items["conflicts"], "resolved_conflicts": [], "project_updates": items["project_signals"], "cross_project_links": related,
        "deep_read_requests": deep, "knowledge_candidates": items["facts"] + items["project_signals"], "ambiguities": items["ambiguities"],
        "evidence_refs": get_analysis_evidence(index=index), "coverage": progress, "health": {"resource": progress["resource"], "worker_heartbeats": {worker: read_json(V2_ROOT / f"heartbeat_{worker}.json", {}) for worker in MODEL_DIRS}, "reader_registry_source_total": len(index.records()), "formal_state_write": "PROHIBITED"},
    }


def get_patrol_full_packet(index: CanonicalReaderIndex | None = None) -> dict[str, Any]:
    path = (index.base if index else BASE) / "state" / "patrol_analysis_packet.json"
    value = read_json(path, {})
    return value if isinstance(value, dict) else {}


def get_latest_synthesis_packet(index: CanonicalReaderIndex | None = None) -> dict[str, Any]:
    root = (index.base if index else BASE) / "state" / "semantic_v2"
    candidates = sorted(root.glob("*synthesis*packet*.json"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return read_json(candidates[0], {}) if candidates else {}


def refresh_patrol_packet(index: CanonicalReaderIndex | None = None, *, force: bool = False, output_path: Path | None = None, state_root: Path = V2_ROOT) -> dict[str, Any]:
    index = _index(index); output_path = output_path or (index.base / "state" / "patrol_analysis_packet.json")
    if not force and output_path.is_file() and time.time() - output_path.stat().st_mtime < 1800:
        return read_json(output_path, {})
    packet = _packet(index, Path(state_root))
    # These two small indexes are useful for future MCP registration and are
    # derived in the same scan as the packet.
    state_root = Path(state_root)
    atomic_json(state_root / "analysis_index.json", {"generated_at": packet["generated_at"], "packet_path": str(output_path), "evidence_count": len(packet["evidence_refs"]), "source_total": len(index.records())})
    atomic_json(state_root / "metric_index.json", {"schema": "paw-metric-index-v2/1", "generated_at": packet["generated_at"], "metrics": get_metric_changes(index)})
    if output_path.resolve() == (index.base / "state" / "patrol_analysis_packet.json").resolve():
        # atomic_json permits the canonical patrol path and keeps this update
        # separate from all scheduler state.
        atomic_json(output_path, packet)
    else:
        atomic_json(output_path, packet)
    return packet


def patrol_refresh_due(index: CanonicalReaderIndex | None = None) -> bool:
    path = (index.base if index else BASE) / "state" / "patrol_analysis_packet.json"
    return not path.is_file() or time.time() - path.stat().st_mtime >= 1800


if __name__ == "__main__":
    print(json.dumps(get_semantic_progress(), ensure_ascii=False, indent=2))
