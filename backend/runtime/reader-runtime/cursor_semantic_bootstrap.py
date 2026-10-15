#!/usr/bin/env python3
"""Resumable Cursor semantic bootstrap supervisor.

This is deliberately a separate analysis-only control plane.  It freezes the
canonical Reader queue once, submits one fresh Cursor task per 20-source
window, and materializes one atomic result per SourceVersion.  It does not
write PAW Formal State, Candidates, ReviewDecision, or ApprovedChange.

The Cursor operator remains the only process boundary for Cursor execution.
This module never invokes a shell and never cancels a task it does not own.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from cursor_semantic_reuse import ReusableArtifactIndex

STATE_ROOT = BASE / "state" / "cursor_semantic_v1"
SEMANTIC_ROOT = BASE / "semantic"
QUEUE_PATH = STATE_ROOT / "queue.json"
CHECKPOINT_PATH = STATE_ROOT / "checkpoint.json"
SUMMARY_PATH = STATE_ROOT / "summary.txt"
LOCK_PATH = STATE_ROOT / "supervisor.lock"
RESULTS_ROOT = STATE_ROOT / "results"
FAILURES_ROOT = STATE_ROOT / "failures"
BATCHES_ROOT = STATE_ROOT / "batches"

MODEL = "cursor-grok-4.6-xhigh"
DEFAULT_BATCH_SIZE = 20
MIN_BATCH_SIZE = 10
MAX_BATCH_SIZE = 30
# The operator rejects instructions over 50,000 characters.  Keep a small
# margin for future operator-side framing and make the effective batch scope
# the last-resort protection when compact metadata is still too large.
MAX_INSTRUCTION_LENGTH = 50_000
PROMPT_BUDGET = 48_000
DEFAULT_BATCH_TIMEOUT = 45 * 60
MAX_OVERALL_TIMEOUT = 7 * 60 * 60
SCHEMA = "paw-cursor-semantic-bootstrap-v1/1"
QUEUE_SCHEMA = "paw-cursor-semantic-queue-v1/1"
CHECKPOINT_SCHEMA = "paw-cursor-semantic-checkpoint-v1/1"
RESULT_SCHEMA = "paw-cursor-source-semantic-v1/1"
_SAFE = re.compile(r"[^A-Za-z0-9_.@-]+")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_name(value: str) -> str:
    return _SAFE.sub("_", str(value))[:190] or "unknown"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def read_json(path: Path, fallback: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return fallback


def atomic_json(path: Path, value: Any) -> None:
    """Write JSON with fsync + replace; refuse anything outside this plane."""
    resolved = path.resolve()
    allowed = STATE_ROOT.resolve() in resolved.parents or resolved == STATE_ROOT.resolve()
    if not allowed or "formal" in str(resolved).casefold():
        raise ValueError(f"cursor bootstrap writer refuses path: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def atomic_text(path: Path, value: str) -> None:
    resolved = path.resolve()
    if STATE_ROOT.resolve() not in resolved.parents:
        raise ValueError(f"cursor bootstrap writer refuses path: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(value)
        if not value.endswith("\n"):
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


@contextlib.contextmanager
def supervisor_lock(root: Path = STATE_ROOT):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "supervisor.lock").open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _record_meta(record: Any, reader: dict[str, Any]) -> dict[str, Any]:
    flags = reader.get("fidelity_flags") if isinstance(reader.get("fidelity_flags"), dict) else {}
    structured = reader.get("structured_semantics") if isinstance(reader.get("structured_semantics"), dict) else {}
    reader_type = str(getattr(record, "family", "") or reader.get("reader_type") or "")
    visual_reasons: list[str] = []
    if str(flags.get("visual_status", "")).upper() in {"REQUIRED", "PENDING", "IMAGE_ONLY"}:
        visual_reasons.append("reader_fidelity_visual_status")
    if str(flags.get("visual_evidence", "")).upper() in {"REQUIRED", "IMAGE_ONLY", "FAIL"}:
        visual_reasons.append("reader_fidelity_visual_evidence")
    if int(flags.get("chart_count") or 0) > 0 or int(flags.get("connector_count") or 0) > 0:
        visual_reasons.append("reader_fidelity_chart_or_connector")
    if reader_type.upper() in {"PPTX", "PDF"} and not reader.get("text_blocks") and not structured.get("pages"):
        visual_reasons.append("no_reliable_text_evidence")
    # The Reader gate is authoritative.  The flag is a review obligation, not
    # an instruction to invent visual text.
    return {
        "batch": getattr(record, "batch", ""),
        "ordinal": getattr(record, "ordinal", 0),
        "source_version_id": getattr(record, "source_version_id", ""),
        "source_id": getattr(record, "source_id", ""),
        "stable_object_id": getattr(record, "stable_object_id", ""),
        "version_marker": getattr(record, "version_marker", ""),
        "title": getattr(record, "title", ""),
        "family": reader_type,
        "project_tier": getattr(record, "project_tier", ""),
        "paw_project_name": getattr(record, "paw_project_name", ""),
        "paw_project_id": getattr(record, "paw_project_id", None),
        "raw_sha256": getattr(record, "raw_sha256", ""),
        "reader_result_path": getattr(record, "reader_result_path", ""),
        "reader_fidelity_visual_reasons": visual_reasons,
        "reader_fidelity_gate": reader.get("reader_fidelity_gate") or reader.get("READER_FIDELITY_GATE") or "PASS",
        "reader_fidelity_flags": flags,
    }


def canonical_records(base: Path = BASE) -> list[Any]:
    """Read only the current canonical Reader registry through existing v2 code."""
    sys.path.insert(0, str(base))
    from semantic_supervisor_v2 import CanonicalReaderIndex
    return CanonicalReaderIndex(base).records()


def _queue_payload(records: Iterable[Any], index: Any, registry_path: Path) -> dict[str, Any]:
    items = [_record_meta(record, index.reader(record)) for record in records]
    body = {
        "schema": QUEUE_SCHEMA,
        "queue_version": "cursor-semantic-v1-" + sha256_json(items)[:16],
        "frozen_at": now(),
        "canonical_registry_path": str(registry_path),
        "canonical_registry_sha256": sha256_bytes(registry_path.read_bytes()),
        "source_order": "canonical Reader registry ORDER, then completed ledger ordinal",
        "total_sources": len(items),
        "sources": items,
    }
    body["queue_hash"] = sha256_json(body)
    return body


def freeze_queue(*, base: Path = BASE, output_path: Path = QUEUE_PATH, force: bool = False) -> dict[str, Any]:
    """Freeze the canonical queue; existing frozen scope is immutable by default."""
    output_path = Path(output_path)
    if output_path.exists() and not force:
        existing = read_json(output_path, {})
        if not isinstance(existing, dict) or existing.get("schema") != QUEUE_SCHEMA or not existing.get("queue_hash"):
            raise RuntimeError("existing queue is invalid; explicit force is required")
        return existing
    sys.path.insert(0, str(base))
    from semantic_supervisor_v2 import CanonicalReaderIndex
    registry_path = base / "state" / "canonical_reader_batches.json"
    index = CanonicalReaderIndex(base)
    records = index.records()
    payload = _queue_payload(records, index, registry_path)
    atomic_json(output_path, payload)
    return payload


def load_queue(path: Path = QUEUE_PATH) -> dict[str, Any]:
    queue = read_json(path, None)
    if not isinstance(queue, dict) or queue.get("schema") != QUEUE_SCHEMA:
        raise RuntimeError(f"frozen queue missing or invalid: {path}")
    expected = queue.get("queue_hash")
    body = dict(queue); body.pop("queue_hash", None)
    if expected != sha256_json(body):
        raise RuntimeError("frozen queue hash mismatch")
    sources = queue.get("sources")
    if not isinstance(sources, list) or int(queue.get("total_sources") or -1) != len(sources):
        raise RuntimeError("frozen queue source count mismatch")
    ids = [str(item.get("source_version_id")) for item in sources if isinstance(item, dict)]
    if len(ids) != len(set(ids)) or any(not item for item in ids):
        raise RuntimeError("frozen queue has duplicate/empty SourceVersion IDs")
    return queue


def result_path(source: dict[str, Any], root: Path = STATE_ROOT) -> Path:
    return Path(root) / "results" / safe_name(source.get("batch", "unknown")) / f"{safe_name(source['source_version_id'])}.json"


def failure_path(source: dict[str, Any], root: Path = STATE_ROOT) -> Path:
    return Path(root) / "failures" / safe_name(source.get("batch", "unknown")) / f"{safe_name(source['source_version_id'])}.json"


def result_complete(path: Path, source_version_id: str) -> bool:
    value = read_json(path, {})
    return isinstance(value, dict) and value.get("schema") == RESULT_SCHEMA and value.get("source_version_id") == source_version_id and value.get("status") == "SUCCESS"


def cursor_result_ids(queue: dict[str, Any], root: Path = STATE_ROOT) -> set[str]:
    return {
        source["source_version_id"]
        for source in queue["sources"]
        if result_complete(result_path(source, root), source["source_version_id"])
    }


def selection_report(
    queue: dict[str, Any],
    *,
    root: Path = STATE_ROOT,
    semantic_root: Path = SEMANTIC_ROOT,
    limit_sources: int | None = None,
) -> dict[str, Any]:
    """Return the pending Cursor queue after immutable artifact reuse.

    The frozen queue remains the source of ordering and scope.  Only the
    derived pending list is reordered: failed/retryable work first, then
    review/evidence work, then sources with no semantic coverage.
    """
    index = ReusableArtifactIndex(semantic_root)
    queue_ids = {source["source_version_id"] for source in queue["sources"]}
    inherited_ids = index.semantic_sources() & queue_ids
    cursor_ids = cursor_result_ids(queue, root)
    takeover_dir = Path(root).parent / "cloudshell_glm53_takeover_v1" / "results"
    if takeover_dir.is_dir():
        takeover_idx = ReusableArtifactIndex(takeover_dir)
        inherited_ids |= takeover_idx.semantic_sources() & queue_ids
    # A Cursor artifact may have been produced for a source that already had
    # reusable semantic coverage. Keep artifact provenance counts truthful,
    # but count it as new analysis only when it expands source coverage.
    cursor_new_ids = cursor_ids - inherited_ids
    pending_rows: list[tuple[int, int, dict[str, Any], dict[str, Any]]] = []
    failed_ids: set[str] = set()
    review_ids: set[str] = set()
    no_coverage_ids: set[str] = set()
    for source in queue["sources"]:
        svid = source["source_version_id"]
        if svid in inherited_ids or svid in cursor_ids:
            continue
        metadata = index.metadata(svid)
        cursor_failed = failure_path(source, root).is_file()
        failed = cursor_failed or svid in index.failed_sources()
        review = bool(metadata["review_signals"])
        if failed:
            priority = 0
            failed_ids.add(svid)
        elif review:
            priority = 1
            review_ids.add(svid)
        else:
            priority = 2
            no_coverage_ids.add(svid)
        pending_rows.append((priority, int(source.get("ordinal") or 0), source, metadata))
    pending_rows.sort(key=lambda row: (row[0], row[1], row[2]["source_version_id"]))
    pending_all = [row[2] for row in pending_rows]
    selected_rows = pending_rows[:limit_sources] if limit_sources else pending_rows

    provenance_counts = index.provenance_counts()
    provenance_counts[MODEL] = {
        "artifact_count": len(cursor_ids),
        "source_count": len(cursor_ids),
        "semantic_sources": len(cursor_ids),
        "visual_sources": 0,
        "failed_artifacts": sum(failure_path(source, root).is_file() for source in queue["sources"]),
    }
    return {
        "artifact_root": str(semantic_root),
        "inherited_semantic_sources": len(inherited_ids),
        "inherited_semantic_source_ids": sorted(inherited_ids),
        "cursor_new_analysis_sources": len(cursor_new_ids),
        "cursor_new_analysis_source_ids": sorted(cursor_new_ids),
        "pending_sources_after_reuse": len(pending_all),
        "pending_source_ids": [source["source_version_id"] for source in pending_all],
        "selected_pending_source_ids": [source["source_version_id"] for source in [row[2] for row in selected_rows]],
        "failed_retryable_sources": len(failed_ids),
        "needs_review_sources": len(review_ids),
        "no_semantic_coverage_sources": len(no_coverage_ids),
        "visual_coverage_sources": len(index.visual_sources() & queue_ids),
        "provenance_counts_by_model": provenance_counts,
        "pending_sources": pending_all,
        "selected_sources": [row[2] for row in selected_rows],
        "source_metadata": {row[2]["source_version_id"]: row[3] for row in pending_rows},
    }


def initial_checkpoint(queue: dict[str, Any], *, batch_size: int, started_at: str | None = None) -> dict[str, Any]:
    ts = started_at or now()
    return {
        "schema": CHECKPOINT_SCHEMA,
        "queue_version": queue["queue_version"], "queue_hash": queue["queue_hash"],
        "total_sources": queue["total_sources"], "completed_sources": 0, "failed_sources": 0,
        "visual_pending_sources": 0, "skipped_existing": 0,
        "inherited_semantic_sources": 0, "cursor_new_analysis_sources": 0,
        "pending_sources_after_reuse": queue["total_sources"],
        "provenance_counts_by_model": {},
        "current_batch": None, "current_source": None, "batches_completed": 0, "batches_failed": 0,
        "heartbeat_at": ts, "last_checkpoint_at": ts, "started_at": ts, "updated_at": ts,
        "effective_model": MODEL, "status": "READY", "resume_cursor": 0, "last_error": None,
        "current_job_id": None, "current_batch_id": None, "last_batch_usage": None,
        "governance": {"formal_state_write": "PROHIBITED", "candidate_creation": "PROHIBITED", "cross_source_synthesis": "PROHIBITED"},
    }


def _coverage_fields(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "inherited_semantic_sources": report["inherited_semantic_sources"],
        "cursor_new_analysis_sources": report["cursor_new_analysis_sources"],
        "pending_sources_after_reuse": report["pending_sources_after_reuse"],
        "provenance_counts_by_model": report["provenance_counts_by_model"],
    }


def _counts(queue: dict[str, Any], root: Path) -> tuple[int, int, int, int, int, dict[str, Any]]:
    report = selection_report(queue, root=root)
    semantic_ids = set(report["inherited_semantic_source_ids"]) | set(report["cursor_new_analysis_source_ids"])
    completed = len(semantic_ids)
    failed = report["failed_retryable_sources"]
    visual = 0
    for source in queue["sources"]:
        if source["source_version_id"] in report["cursor_new_analysis_source_ids"]:
            value = read_json(result_path(source, root), {})
            if value.get("visual_pending") or (value.get("visual_status") or {}).get("status") == "VISUAL_PENDING":
                visual += 1
    skipped = report["inherited_semantic_sources"]
    cursor = next((i for i, s in enumerate(queue["sources"]) if s["source_version_id"] not in semantic_ids), len(queue["sources"]))
    return completed, failed, visual, skipped, cursor, report


def update_checkpoint(checkpoint: dict[str, Any], queue: dict[str, Any], *, root: Path = STATE_ROOT, **updates: Any) -> dict[str, Any]:
    completed, failed, visual, skipped, cursor, report = _counts(queue, root)
    checkpoint.update({"queue_version": queue["queue_version"], "queue_hash": queue["queue_hash"], "total_sources": queue["total_sources"], "completed_sources": completed, "failed_sources": failed, "visual_pending_sources": visual, "skipped_existing": skipped, "resume_cursor": cursor, **_coverage_fields(report), "heartbeat_at": now(), "last_checkpoint_at": now(), "updated_at": now(), **updates})
    atomic_json(root / "checkpoint.json", checkpoint)
    write_summary(checkpoint, root=root)
    return checkpoint


def write_summary(checkpoint: dict[str, Any], *, root: Path = STATE_ROOT) -> None:
    elapsed = "unknown"
    try:
        started = datetime.fromisoformat(checkpoint["started_at"]); elapsed = str(datetime.now(timezone.utc) - started.astimezone(timezone.utc)).split(".")[0]
    except (KeyError, ValueError, TypeError):
        pass
    lines = [
        "PAW Cursor semantic bootstrap supervisor",
        f"status={checkpoint.get('status')} model={checkpoint.get('effective_model')}",
        f"progress={checkpoint.get('completed_sources', 0)}/{checkpoint.get('total_sources', 0)} failed={checkpoint.get('failed_sources', 0)} visual_pending={checkpoint.get('visual_pending_sources', 0)} skipped_existing={checkpoint.get('skipped_existing', 0)}",
        f"inherited_semantic_sources={checkpoint.get('inherited_semantic_sources', 0)} cursor_new_analysis_sources={checkpoint.get('cursor_new_analysis_sources', 0)} pending_sources_after_reuse={checkpoint.get('pending_sources_after_reuse', 0)}",
        f"provenance_counts_by_model={json.dumps(checkpoint.get('provenance_counts_by_model', {}), ensure_ascii=False, sort_keys=True)}",
        f"current_batch={checkpoint.get('current_batch')} current_source={checkpoint.get('current_source')} resume_cursor={checkpoint.get('resume_cursor')}",
        f"batches_completed={checkpoint.get('batches_completed', 0)} batches_failed={checkpoint.get('batches_failed', 0)} elapsed={elapsed}",
        f"heartbeat_at={checkpoint.get('heartbeat_at')} last_checkpoint_at={checkpoint.get('last_checkpoint_at')} updated_at={checkpoint.get('updated_at')}",
        f"last_error={checkpoint.get('last_error')}",
        f"queue_hash={checkpoint.get('queue_hash')}",
    ]
    atomic_text(root / "summary.txt", "\n".join(lines))


def status(*, root: Path = STATE_ROOT) -> dict[str, Any]:
    queue = load_queue(Path(root) / "queue.json")
    checkpoint = read_json(Path(root) / "checkpoint.json", None)
    if not isinstance(checkpoint, dict):
        checkpoint = initial_checkpoint(queue, batch_size=DEFAULT_BATCH_SIZE)
    report = selection_report(queue, root=root)
    coverage = _coverage_fields(report)
    priority_fields = {k: report[k] for k in ("failed_retryable_sources", "needs_review_sources", "no_semantic_coverage_sources", "visual_coverage_sources")}
    return {
        "schema": SCHEMA,
        "queue": {"path": str(Path(root) / "queue.json"), "version": queue["queue_version"], "hash": queue["queue_hash"], "total_sources": queue["total_sources"]},
        "checkpoint": {**checkpoint, **coverage},
        **coverage, **priority_fields,
        "reuse": {k: v for k, v in report.items() if k not in {"pending_sources", "selected_sources", "source_metadata"}},
        "summary_path": str(Path(root) / "summary.txt"), "results_root": str(Path(root) / "results"), "failures_root": str(Path(root) / "failures"),
    }


def _json_values_in_text(text: str) -> list[Any]:
    """Decode complete JSON values embedded in prose or a clipped log.

    Cursor's JSON envelope can contain prose followed by a JSON object, and a
    bounded log may clip the final closing braces of the outer object.  The
    JSON decoder can still recover each complete source record from that
    clipped text, so scan every object/array start instead of requiring one
    complete top-level value.
    """
    text = str(text or "")
    values: list[Any] = []
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        values.append(value)
    return values


def _parse_json_values(value: Any) -> list[Any]:
    if not isinstance(value, str):
        return [value]
    text = value.strip()
    values: list[Any] = []
    try:
        values.append(json.loads(text))
    except (ValueError, TypeError):
        pass
    values.extend(_json_values_in_text(text))
    return values


def extract_records(response: Any, expected_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """Extract explicit source-level records from all Cursor result shapes.

    The operator has used ``response``, ``output``, ``final``, and ``result``
    at different envelope levels.  This walker follows those fields (and log
    text), accepts a normal ``records`` array, and also collects complete
    source-record objects when the surrounding array was clipped.  It never
    derives an ID from position, title, or filename.
    """
    wanted = {str(item) for item in expected_ids} if expected_ids is not None else None
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    visited: set[int] = set()
    result_keys = {"records", "source_records", "semantic_records", "results"}
    nested_keys = {"result", "response", "output", "text", "final", "message", "content", "log_tail", "log"}

    def add(record: Any) -> None:
        if not isinstance(record, dict):
            return
        svid = record.get("source_version_id") or record.get("source_version")
        if not svid:
            return
        svid = str(svid)
        if wanted is not None and svid not in wanted:
            return
        if svid in seen:
            return
        seen.add(svid)
        records.append(record)

    def walk(value: Any, depth: int = 0) -> None:
        if depth > 12:
            return
        if isinstance(value, str):
            for parsed in _parse_json_values(value):
                if parsed is not value:
                    walk(parsed, depth + 1)
            return
        if isinstance(value, dict):
            marker = id(value)
            if marker in visited:
                return
            visited.add(marker)
            for key in result_keys:
                candidate = value.get(key)
                if isinstance(candidate, list):
                    for item in candidate:
                        add(item)
                        walk(item, depth + 1)
            # A source object may itself be the only complete object left in a
            # clipped records array.
            add(value)
            for key, child in value.items():
                if key.casefold() in nested_keys or key in result_keys:
                    walk(child, depth + 1)
            return
        if isinstance(value, list):
            marker = id(value)
            if marker in visited:
                return
            visited.add(marker)
            for item in value:
                add(item)
                walk(item, depth + 1)

    walk(response)
    return records


def normalize_record(raw: dict[str, Any], source: dict[str, Any], batch_id: str, inherited_provenance: dict[str, Any] | None = None) -> dict[str, Any]:
    svid = str(raw.get("source_version_id") or raw.get("source_version") or "")
    if svid != source["source_version_id"]:
        raise ValueError("source_version_id mismatch")
    visual = raw.get("visual_status")
    if isinstance(visual, str): visual = {"status": visual}
    if not isinstance(visual, dict): visual = {"status": "CLEAR"}
    visual_pending = bool(raw.get("visual_pending") or str(visual.get("status", "")).upper() == "VISUAL_PENDING")
    if source.get("reader_fidelity_visual_reasons") and (not raw.get("source_evidence_pointers") or raw.get("visual_pending") is True):
        visual_pending = True
        visual.setdefault("why", "Reader Fidelity indicates visual evidence obligation; inaccessible visual text was not fabricated")
    visual["status"] = "VISUAL_PENDING" if visual_pending else str(visual.get("status") or "CLEAR")
    evidence = raw.get("source_evidence_pointers") or raw.get("evidence_pointers") or raw.get("evidence_locators") or []
    if not isinstance(evidence, list): evidence = [evidence]
    result = {
        "schema": RESULT_SCHEMA, "status": "SUCCESS", "source_version_id": svid,
        "title": raw.get("title") or source.get("title"), "source_identity": raw.get("source_identity") or {k: source.get(k) for k in ("source_id", "stable_object_id", "version_marker", "title", "family")},
        "project_domain_signals": raw.get("project_domain_signals") or raw.get("project_signals") or [], "document_role": raw.get("document_role") or "",
        "key_facts": raw.get("key_facts") or raw.get("facts") or [], "metrics_data_signals": raw.get("metrics_data_signals") or raw.get("metrics") or [],
        "timeline_decision_signals": raw.get("timeline_decision_signals") or raw.get("timeline") or raw.get("decisions_or_plans") or [],
        "version_supersession_signals": raw.get("version_supersession_signals") or raw.get("version_signals") or [],
        "conflicts_ambiguities": raw.get("conflicts_ambiguities") or raw.get("conflicts") or raw.get("ambiguities") or [],
        "evidence_quality": raw.get("evidence_quality") or {"reader_fidelity": source.get("reader_fidelity_gate"), "issues": raw.get("issues") or []},
        "issues": raw.get("issues") or [], "confidence": raw.get("confidence") or "medium", "visual_status": visual, "visual_pending": visual_pending,
        "source_evidence_pointers": evidence, "reader_fidelity": {"gate": source.get("reader_fidelity_gate"), "flags": source.get("reader_fidelity_flags"), "visual_reasons": source.get("reader_fidelity_visual_reasons")},
        "batch_id": batch_id, "model": MODEL, "analyzed_at": now(),
        "analysis_layer": "cursor_review_enrichment",
        "provenance": {
            "layer": "cursor_review_enrichment",
            "original_artifacts_preserved": True,
            "inherited": inherited_provenance or {"semantic_coverage": False, "visual_coverage": False, "provenance": []},
        },
        "governance": {"artifact_type": "analysis", "formal_state_write": "PROHIBITED", "cross_source_synthesis": "PROHIBITED"},
    }
    # Preserve extra source-level model fields without allowing governance
    # fields to be smuggled into a PAW write path.
    for key, value in raw.items():
        if key not in result and key not in {"candidate", "review_decision", "approved_change", "formal_state"}:
            result[key] = value
    return result


def persist_failure(source: dict[str, Any], *, batch_id: str, error: str, job_id: str | None = None, root: Path = STATE_ROOT) -> None:
    atomic_json(failure_path(source, root), {"schema": "paw-cursor-source-failure-v1/1", "source_version_id": source["source_version_id"], "title": source.get("title"), "batch_id": batch_id, "model": MODEL, "error": str(error)[:2000], "job_id": job_id, "failed_at": now(), "governance": {"formal_state_write": "PROHIBITED"}})


def _compact_source_metadata(source: dict[str, Any], *, minimal: bool = False) -> dict[str, Any]:
    """Keep prompt metadata bounded without changing the frozen queue."""
    keys = (
        "source_version_id", "batch", "ordinal", "source_id", "stable_object_id",
        "version_marker", "title", "family", "project_tier", "paw_project_name",
        "paw_project_id", "raw_sha256", "reader_result_path", "reader_fidelity_gate",
        "reader_fidelity_visual_reasons", "reader_fidelity_flags",
    )
    if minimal:
        keys = (
            "source_version_id", "batch", "ordinal", "source_id", "stable_object_id",
            "version_marker", "title", "family", "raw_sha256", "reader_result_path",
            "reader_fidelity_gate", "reader_fidelity_visual_reasons",
        )
    return {key: source.get(key) for key in keys if key in source}


def _compact_provenance(metadata: Any, *, minimal: bool = False) -> dict[str, Any]:
    """Render provenance-only coverage; source artifacts remain immutable."""
    if not isinstance(metadata, dict):
        return {}
    result = {key: metadata.get(key) for key in ("semantic_coverage", "visual_coverage") if key in metadata}
    signals = metadata.get("review_signals")
    if isinstance(signals, list):
        result["review_signals"] = [str(signal)[:160] for signal in signals[:8]]
    elif signals is not None:
        result["review_signals"] = [str(signals)[:160]]
    provenance = metadata.get("provenance")
    if isinstance(provenance, list):
        result["provenance"] = [
            {
                key: item.get(key)
                for key in ("worker", "model", "semantic_coverage", "visual_coverage", "grounded_semantic_fields")
                if isinstance(item, dict) and key in item
            }
            for item in provenance if isinstance(item, dict)
        ]
    if minimal:
        result.pop("review_signals", None)
        result["provenance"] = [
            {key: item.get(key) for key in ("worker", "model", "semantic_coverage", "visual_coverage") if key in item}
            for item in result.get("provenance", []) if isinstance(item, dict)
        ]
    return result


def _prompt_payload(batch_id: str, sources: list[dict[str, Any]], provenance_by_source: dict[str, Any] | None, *, minimal: bool = False) -> str:
    ids = [s["source_version_id"] for s in sources]
    metadata = json.dumps([_compact_source_metadata(s, minimal=minimal) for s in sources], ensure_ascii=False, separators=(",", ":"))
    inherited = json.dumps({svid: _compact_provenance((provenance_by_source or {}).get(svid, {}), minimal=minimal) for svid in ids}, ensure_ascii=False, separators=(",", ":"))
    return f"""PAW FULL-READER CURSOR SEMANTIC BOOTSTRAP — READ-ONLY BATCH

Batch ID: {batch_id}
Model contract: cursor-grok-4.6-xhigh
Fresh-session contract: this is an independent new session; do not use prior conversation memory.
Exact SourceVersion IDs in scope (do not add, drop, reorder, or reinterpret):
{json.dumps(ids, ensure_ascii=False)}

Read the listed Reader Fidelity evidence JSON files from the metadata below. Reader Fidelity is authoritative. Analyze each source independently; do not perform cross-source Project/Domain synthesis. Return ONLY one JSON object with a `records` array containing exactly one source-level record for each listed ID. Each record must include source_version_id, title/source_identity, project_domain_signals, document_role, key_facts, metrics_data_signals, timeline_decision_signals, version_supersession_signals, conflicts_ambiguities, evidence_quality/issues, confidence, visual_status, and source_evidence_pointers. Use exact evidence pointers into the Reader result. Never invent inaccessible visual text, numbers, dates, or names. If visual evidence is required or image-only and reliable text is unavailable, return partial grounded fields and visual_status={{"status":"VISUAL_PENDING","why":"..."}}, visual_pending=true. Do not edit files, create Candidates, make ReviewDecisions, write ApprovedChanges, or write PAW Formal State. Do not call external services or use raw corpus copies.

Prior-worker coverage metadata (provenance only; original artifacts are immutable and must not be replaced):
{inherited}

Frozen source metadata:
{metadata}
"""


def prompt_for_batch(batch_id: str, sources: list[dict[str, Any]], provenance_by_source: dict[str, Any] | None = None) -> str:
    """Build a bounded prompt while retaining the exact ordered ID list."""
    prompt = _prompt_payload(batch_id, sources, provenance_by_source)
    if len(prompt) > PROMPT_BUDGET:
        prompt = _prompt_payload(batch_id, sources, provenance_by_source, minimal=True)
    if len(prompt) > MAX_INSTRUCTION_LENGTH:
        raise ValueError(f"prompt exceeds {MAX_INSTRUCTION_LENGTH} characters for {len(sources)} sources")
    return prompt


def fit_prompt_batch(batch_id_prefix: str, sources: list[dict[str, Any]], provenance_by_source: dict[str, Any] | None = None) -> tuple[str, list[dict[str, Any]], str]:
    """Return the largest ordered prefix that fits the operator limit.

    Reducing scope is only a fallback after compact metadata.  IDs are never
    shortened or regenerated, and the returned prompt records the same list
    returned by this function.
    """
    for count in range(len(sources), 0, -1):
        selected = sources[:count]
        batch_id = f"{batch_id_prefix}{count}"
        try:
            prompt = prompt_for_batch(batch_id, selected, provenance_by_source)
        except ValueError:
            continue
        if len(prompt) <= MAX_INSTRUCTION_LENGTH:
            return batch_id, selected, prompt
    raise ValueError("single-source Cursor prompt exceeds instruction limit")


def _read_text_artifact(path_value: Any) -> str:
    if not isinstance(path_value, str) or not path_value:
        return ""
    path = Path(path_value)
    try:
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    except OSError:
        return ""


def _records_from_job_payloads(job: dict[str, Any], result: Any, expected_ids: Iterable[str]) -> list[dict[str, Any]]:
    """Read operator fields first, then the durable job log/status artifacts."""
    payloads: list[Any] = [result]
    job_status = job.get("job_status") if isinstance(job.get("job_status"), dict) else {}
    payloads.extend([job_status.get("result"), job.get("result"), job_status.get("response")])
    result_metadata = job.get("result_metadata") if isinstance(job.get("result_metadata"), dict) else {}
    payloads.extend([result_metadata.get("result"), result_metadata.get("response"), result_metadata.get("output")])
    paths = [
        job_status.get("result_path"), result_metadata.get("result_path"),
        str(Path(job_status["run_dir"]) / "cursor.log") if job_status.get("run_dir") else None,
    ]
    payloads.extend(_read_text_artifact(path) for path in paths if path)
    return extract_records(payloads, expected_ids=expected_ids)


def _persist_recovered_records(
    queue: dict[str, Any], *, batch_id: str, job_id: str | None, job: dict[str, Any], result: Any,
    root: Path,
) -> tuple[list[str], list[str]]:
    source_by_id = {str(source["source_version_id"]): source for source in queue["sources"]}
    source_ids = batch_source_ids(batch_id, root)
    if not source_ids:
        return [], [f"{batch_id}: missing immutable source list"]
    if len(source_ids) != len(set(source_ids)) or any(svid not in source_by_id for svid in source_ids):
        return [], [f"{batch_id}: batch source list is outside the frozen queue"]
    records = _records_from_job_payloads(job, result, source_ids)
    by_id: dict[str, dict[str, Any]] = {}
    for raw in records:
        svid = str(raw.get("source_version_id") or raw.get("source_version") or "")
        if svid in source_by_id and svid in source_ids and svid not in by_id:
            by_id[svid] = raw
    recovered: list[str] = []
    missing: list[str] = []
    selection = selection_report(queue, root=root)
    for svid in source_ids:
        source = source_by_id[svid]
        target = result_path(source, root)
        if result_complete(target, svid):
            continue
        raw = by_id.get(svid)
        if raw is None:
            missing.append(svid)
            continue
        try:
            normalized = normalize_record(raw, source, batch_id, selection["source_metadata"].get(svid))
            # A prior successful artifact wins.  A corrupt/incomplete file is
            # replaceable through the same atomic, plane-confined writer.
            atomic_json(target, normalized)
            recovered.append(svid)
        except Exception as exc:
            missing.append(f"{svid}: {exc}")
    return recovered, missing


def recover_completed_batches(
    queue: dict[str, Any], *, root: Path = STATE_ROOT,
    result_reader: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Recover durable results from owned completed Cursor batch manifests."""
    recovered: set[str] = set()
    recovered_batches: list[str] = []
    incomplete_batches: list[str] = []
    errors: list[str] = []
    for manifest_path in sorted((Path(root) / "batches").glob("*/JOB.json")):
        job = read_json(manifest_path, None)
        if not isinstance(job, dict):
            continue
        batch_id = str(job.get("batch_id") or manifest_path.parent.name)
        job_status = job.get("job_status") if isinstance(job.get("job_status"), dict) else {}
        if str(job_status.get("status") or "").upper() != "COMPLETED":
            continue
        job_id = job.get("job_id") or job_status.get("job_id")
        result: Any = {}
        if isinstance(job_id, str) and result_reader is not None:
            try:
                result = result_reader(job_id)
            except Exception as exc:
                errors.append(f"{batch_id}: result reader: {exc}")
        elif isinstance(job_id, str):
            try:
                result = _operator_module().read_result(job_id, max_lines=200)
            except Exception as exc:
                errors.append(f"{batch_id}: result reader: {exc}")
        source_ids = batch_source_ids(batch_id, root)
        if not source_ids:
            errors.append(f"{batch_id}: missing immutable source list")
            continue
        before = set(cursor_result_ids(queue, root))
        got, missing = _persist_recovered_records(queue, batch_id=batch_id, job_id=job_id if isinstance(job_id, str) else None, job=job, result=result, root=root)
        recovered.update(got)
        if got and not missing:
            recovered_batches.append(batch_id)
        elif missing:
            incomplete_batches.append(batch_id)
            errors.extend(f"{batch_id}: {item}" for item in missing)
        # Keep this read-only diagnostic useful even when every record already
        # existed from an earlier duplicate batch.
        if not got and set(cursor_result_ids(queue, root)) == before and not missing:
            recovered_batches.append(batch_id)
    return {
        "recovered_sources": sorted(recovered),
        "recovered_source_count": len(recovered),
        "recovered_batches": sorted(set(recovered_batches)),
        "incomplete_batches": sorted(set(incomplete_batches)),
        "errors": errors,
    }


def batch_source_ids(batch_id: str, root: Path = STATE_ROOT) -> list[str]:
    """Read the immutable source list recorded for an owned batch prompt."""
    prompt_path = Path(root) / "batches" / safe_name(batch_id) / "PROMPT.md"
    try:
        lines = prompt_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    marker = "Exact SourceVersion IDs in scope"
    for index, line in enumerate(lines):
        if marker not in line or index + 1 >= len(lines):
            continue
        try:
            value = json.loads(lines[index + 1])
        except (ValueError, TypeError):
            return []
        return [str(item) for item in value] if isinstance(value, list) else []
    return []


def _operator_module():
    sys.path.insert(0, "/opt/paw-chatgpt-mcp-r13")
    import cursor_operator
    return cursor_operator


def active_jobs() -> list[dict[str, Any]]:
    operator = _operator_module()
    root = Path(getattr(operator, "RUN_ROOT", "/var/lib/paw-agent-runs/cursor"))
    jobs: list[dict[str, Any]] = []
    if not root.is_dir(): return jobs
    for path in root.iterdir():
        if not path.is_dir() or not re.fullmatch(r"[0-9a-f-]{36}", path.name): continue
        status = read_json(path / "STATUS.json", {})
        if isinstance(status, dict) and status.get("status") in {"QUEUED", "RUNNING"}:
            jobs.append(status)
    return jobs


def run(*, queue_path: Path = QUEUE_PATH, root: Path = STATE_ROOT, batch_size: int = DEFAULT_BATCH_SIZE, batch_timeout: int = DEFAULT_BATCH_TIMEOUT, overall_timeout: int = MAX_OVERALL_TIMEOUT, limit_sources: int | None = None, submitter: Callable[..., dict[str, Any]] | None = None, poll_seconds: float = 5.0, stop_after_batches: int | None = None) -> dict[str, Any]:
    if not MIN_BATCH_SIZE <= batch_size <= MAX_BATCH_SIZE: raise ValueError(f"batch_size must be {MIN_BATCH_SIZE}-{MAX_BATCH_SIZE}")
    if batch_timeout < 30 or batch_timeout > 45 * 60: raise ValueError("batch_timeout must be 30..2700 seconds")
    queue = load_queue(queue_path)
    root.mkdir(parents=True, exist_ok=True)
    checkpoint = read_json(root / "checkpoint.json", None)
    if not isinstance(checkpoint, dict) or checkpoint.get("queue_hash") != queue["queue_hash"]:
        checkpoint = initial_checkpoint(queue, batch_size=batch_size)
    checkpoint["batch_size"] = batch_size; checkpoint["batch_timeout_seconds"] = batch_timeout
    if limit_sources and limit_sources > len(queue["sources"]): raise ValueError("limit_sources exceeds frozen queue")
    submitter = submitter or _operator_module().submit_task
    started_clock = time.monotonic()
    recovery = recover_completed_batches(queue, root=root)
    update_checkpoint(checkpoint, queue, root=root, status="RUNNING", last_error=None)
    batches_seen = 0
    while True:
        if time.monotonic() - started_clock >= overall_timeout:
            return update_checkpoint(checkpoint, queue, root=root, status="TIMEOUT", last_error="overall timeout")
        selection = selection_report(queue, root=root, limit_sources=limit_sources)
        pending = selection["selected_sources"]
        if not pending:
            return update_checkpoint(checkpoint, queue, root=root, status="COMPLETED", current_batch=None, current_source=None, current_job_id=None)
        batch_sources = pending[:batch_size]
        batch_id = ""
        prompt = ""
        job_id = checkpoint.get("current_job_id")
        # A recorded owned job may survive supervisor interruption. Reattach
        # to its status; a different active Cursor task is never canceled.
        if job_id:
            recorded_batch_id = checkpoint.get("current_batch_id") or checkpoint.get("current_batch")
            recorded_ids = batch_source_ids(str(recorded_batch_id), root) if recorded_batch_id else []
            requested_ids = {source["source_version_id"] for source in batch_sources}
            if not recorded_batch_id or set(recorded_ids) != requested_ids:
                previous_status = _operator_module().task_status(job_id)
                if previous_status.get("status") in {"QUEUED", "RUNNING"}:
                    return update_checkpoint(
                        checkpoint, queue, root=root,
                        status="WAITING_FOR_PREVIOUS_CURSOR_TASK",
                        last_error="owned Cursor task belongs to an obsolete pre-reuse batch; no task was canceled",
                        current_batch=str(recorded_batch_id) if recorded_batch_id else None,
                        current_source=recorded_ids[0] if recorded_ids else None,
                    )
                job_id = None
                checkpoint.update({"current_job_id": None, "current_batch_id": None})
            if job_id:
                job_status = _operator_module().task_status(job_id)
                if job_status.get("status") in {"QUEUED", "RUNNING"}:
                    expected = checkpoint.get("current_batch_id") or batch_id
                    batch_id = expected
                else:
                    job_id = None
        if not job_id:
            unrelated = [j for j in active_jobs() if j.get("job_id") != checkpoint.get("current_job_id")]
            if unrelated:
                return update_checkpoint(checkpoint, queue, root=root, status="WAITING_FOR_UNRELATED_CURSOR", last_error="Cursor concurrency=1 occupied by unrelated task", current_batch=batch_id)
            try:
                batch_id, batch_sources, prompt = fit_prompt_batch(
                    f"cursor-semantic-{uuid.uuid4().hex[:12]}-", batch_sources, selection["source_metadata"]
                )
            except ValueError as exc:
                for source in batch_sources:
                    persist_failure(source, batch_id=batch_id or "prompt-size", error=str(exc), root=root)
                return update_checkpoint(checkpoint, queue, root=root, status="FAILED", last_error=str(exc), current_batch=batch_id or None)
            atomic_text(root / "batches" / batch_id / "PROMPT.md", prompt)
            submitted = submitter(prompt, title=f"PAW full-reader semantic batch {batch_id}", model=MODEL, mode="exec", sandbox=False, timeout_seconds=batch_timeout)
            if submitted.get("error") == "BUSY":
                return update_checkpoint(checkpoint, queue, root=root, status="WAITING_FOR_UNRELATED_CURSOR", last_error="Cursor operator returned BUSY; no task was canceled", current_batch=batch_id)
            if submitted.get("error"):
                for source in batch_sources: persist_failure(source, batch_id=batch_id, error=f"submit: {submitted}", root=root)
                return update_checkpoint(checkpoint, queue, root=root, status="FAILED", batches_failed=int(checkpoint.get("batches_failed") or 0) + 1, last_error=str(submitted), current_batch=batch_id, current_job_id=None, current_batch_id=None)
            job_id = submitted.get("job_id")
            if not isinstance(job_id, str):
                return update_checkpoint(checkpoint, queue, root=root, status="FAILED", last_error="Cursor submit returned no job_id", current_batch=batch_id)
            # Fail closed if the operator did not honor the immutable control contract.
            if submitted.get("model") != MODEL or submitted.get("effective_sandbox") != "disabled" or submitted.get("continuation_mode") != "new":
                return update_checkpoint(checkpoint, queue, root=root, status="FAILED", last_error=f"Cursor control contract mismatch: {submitted}", current_batch=batch_id, current_job_id=job_id, current_batch_id=batch_id)
            checkpoint.update({"current_batch_id": batch_id, "current_job_id": job_id})
        update_checkpoint(checkpoint, queue, root=root, status="RUNNING", current_batch=batch_id, current_source=batch_sources[0]["source_version_id"], current_job_id=job_id, current_batch_id=batch_id)
        while True:
            if time.monotonic() - started_clock >= overall_timeout:
                return update_checkpoint(checkpoint, queue, root=root, status="TIMEOUT", last_error="overall timeout while polling batch")
            job_status = _operator_module().task_status(job_id)
            update_checkpoint(checkpoint, queue, root=root, current_batch=batch_id, current_source=batch_sources[0]["source_version_id"], current_job_id=job_id, current_batch_id=batch_id, last_error=job_status.get("error"))
            if job_status.get("status") not in {"QUEUED", "RUNNING"}: break
            time.sleep(poll_seconds)
        result = _operator_module().read_result(job_id, max_lines=200)
        batch_dir = root / "batches" / batch_id
        atomic_json(batch_dir / "JOB.json", {"batch_id": batch_id, "job_id": job_id, "job_status": job_status, "result_metadata": {k: result.get(k) for k in ("status", "model", "mode", "effective_sandbox", "usage", "duration_seconds", "session_id", "continuation_mode")}})
        records = _records_from_job_payloads(
            {"job_status": job_status, "job_id": job_id}, result,
            [source["source_version_id"] for source in batch_sources],
        ) if job_status.get("status") == "COMPLETED" else []
        by_id = {str(x.get("source_version_id") or x.get("source_version")): x for x in records if isinstance(x, dict)}
        for source in batch_sources:
            svid = source["source_version_id"]
            raw = by_id.get(svid)
            try:
                if raw is None: raise ValueError("missing source-level record in Cursor response")
                normalized = normalize_record(raw, source, batch_id, selection_report(queue, root=root)["source_metadata"].get(svid))
                target = result_path(source, root)
                if not target.exists(): atomic_json(target, normalized)
            except Exception as exc:
                persist_failure(source, batch_id=batch_id, error=str(exc), job_id=job_id, root=root)
        failed_batch = job_status.get("status") != "COMPLETED" or any(not result_complete(result_path(s, root), s["source_version_id"]) for s in batch_sources)
        batches_seen += 1
        checkpoint = update_checkpoint(checkpoint, queue, root=root, status="RUNNING", current_batch=None, current_source=None, current_job_id=None, current_batch_id=None, batches_completed=int(checkpoint.get("batches_completed") or 0) + (0 if failed_batch else 1), batches_failed=int(checkpoint.get("batches_failed") or 0) + (1 if failed_batch else 0), last_batch_usage=result.get("usage"), last_error=None if not failed_batch else f"batch {batch_id} failed or returned incomplete records")
        if stop_after_batches and batches_seen >= stop_after_batches:
            return update_checkpoint(checkpoint, queue, root=root, status="PAUSED_AFTER_BATCH")


def dry_run(*, queue_path: Path = QUEUE_PATH, base: Path = BASE) -> dict[str, Any]:
    queue = freeze_queue(base=base, output_path=queue_path)
    root = Path(queue_path).parent
    recovery = recover_completed_batches(queue, root=root)
    report = selection_report(queue, root=root, semantic_root=Path(base) / "semantic")
    checkpoint = read_json(root / "checkpoint.json", None)
    if not isinstance(checkpoint, dict) or checkpoint.get("queue_hash") != queue["queue_hash"]:
        checkpoint = initial_checkpoint(queue, batch_size=DEFAULT_BATCH_SIZE)
    checkpoint = update_checkpoint(checkpoint, queue, root=root, status="READY_FOR_RESUME", last_error=None)
    next_prompt_chars = None
    next_batch_size = 0
    if report["selected_sources"]:
        try:
            _, selected, prompt = fit_prompt_batch(
                f"cursor-semantic-dry-run-{uuid.uuid4().hex[:12]}-",
                report["selected_sources"][:DEFAULT_BATCH_SIZE], report["source_metadata"],
            )
            next_batch_size = len(selected)
            next_prompt_chars = len(prompt)
        except ValueError:
            next_prompt_size_error = True
        else:
            next_prompt_size_error = False
    else:
        next_prompt_size_error = False
    active = active_jobs()
    owned_job_id = checkpoint.get("current_job_id")
    unrelated_active = [job for job in active if job.get("job_id") != owned_job_id]
    return {
        "queue_version": queue["queue_version"], "queue_hash": queue["queue_hash"],
        "total_sources": queue["total_sources"],
        "coverage": f"{report['inherited_semantic_sources'] + report['cursor_new_analysis_sources']}/{queue['total_sources']}",
        "invoked_cursor": False,
        "batches_at_default_20": (report["pending_sources_after_reuse"] + 19) // 20,
        "recovered_sources": recovery["recovered_sources"],
        "recovered_source_count": recovery["recovered_source_count"],
        "recovered_batches": recovery["recovered_batches"],
        "incomplete_recovery_batches": recovery["incomplete_batches"],
        "recovery_errors": recovery["errors"],
        "pending_sources": report["pending_source_ids"],
        "pending_source_count": report["pending_sources_after_reuse"],
        "checkpoint_completed_sources": checkpoint["completed_sources"],
        "checkpoint_failed_sources": checkpoint["failed_sources"],
        "next_batch_size": next_batch_size,
        "next_prompt_chars": next_prompt_chars,
        "prompt_size_protected": not next_prompt_size_error,
        "ready_for_resume": not unrelated_active and not next_prompt_size_error,
        "readiness_reason": "unrelated Cursor job active" if unrelated_active else ("single-source prompt exceeds limit" if next_prompt_size_error else "READY"),
        **{k: v for k, v in report.items() if k not in {"pending_sources", "selected_sources", "source_metadata"}},
        "first_20_pending_candidates": report["pending_source_ids"][:20],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze-queue"); freeze.add_argument("--force", action="store_true")
    dry = sub.add_parser("dry-run")
    st = sub.add_parser("status")
    runp = sub.add_parser("run"); runp.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE); runp.add_argument("--batch-timeout", type=int, default=DEFAULT_BATCH_TIMEOUT); runp.add_argument("--overall-timeout", type=int, default=MAX_OVERALL_TIMEOUT); runp.add_argument("--limit-sources", type=int); runp.add_argument("--stop-after-batches", type=int)
    args = parser.parse_args(argv)
    if args.command == "freeze-queue": print(json.dumps(freeze_queue(force=args.force), ensure_ascii=False, indent=2)); return 0
    if args.command == "dry-run": print(json.dumps(dry_run(), ensure_ascii=False, indent=2)); return 0
    if args.command == "status": print(json.dumps(status(), ensure_ascii=False, indent=2)); return 0
    if args.command == "run": print(json.dumps(run(batch_size=args.batch_size, batch_timeout=args.batch_timeout, overall_timeout=args.overall_timeout, limit_sources=args.limit_sources, stop_after_batches=args.stop_after_batches), ensure_ascii=False, indent=2)); return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
