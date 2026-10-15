"""Durable Cursor visual supplement runner (analysis plane only)."""
from __future__ import annotations

from collections import Counter
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cursor_visual_admission as admission
from cursor_visual_policy import screen_queue, screen_source

DEFAULT_QUEUE = admission.DEFAULT_READER_ROOT / "state" / "cursor_semantic_v1" / "queue.json"
DEFAULT_STORE = ROOT / "_cursor_visual_store"
CHECKPOINT_NAME = "checkpoint.json"
SCREENING_NAME = "screening_ledger.json"

REVIEW_STATUSES = {"REVIEW_REQUIRED_TEXT", "REVIEW_REQUIRED_TABLE"}
GROUNDED_STATUSES = {"REVIEWED_GROUNDED", "VISUAL_SUPPLEMENTED"}


def _blank_checkpoint() -> dict[str, Any]:
    return {
        "schema": "paw-cursor-visual-supplement-checkpoint-v1/1",
        "model": admission.DEFAULT_MODEL,
        "model_id": admission.DEFAULT_MODEL_ID,
        "started_at": None,
        "updated_at": None,
        "status": "INIT",
        "canary": {},
        "last_source": None,
        "last_image": None,
        "last_error": None,
        "counts": {},
        "processed": {},
        "pending": [],
        "formal_state_bypass": 0,
        "gpt_brain_mutated_by_cursor": "NO",
        "ui_freeze_touched": "NO",
    }


def checkpoint_path(store: Path | None = None) -> Path:
    return Path(store or DEFAULT_STORE) / CHECKPOINT_NAME


def load_checkpoint(store: Path | None = None) -> dict[str, Any]:
    path = checkpoint_path(store)
    if not path.is_file():
        return _blank_checkpoint()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _blank_checkpoint()
    if not isinstance(raw, dict):
        return _blank_checkpoint()
    base = _blank_checkpoint()
    base.update(raw)
    return base


def save_checkpoint(state: Mapping[str, Any], store: Path | None = None) -> Path:
    path = checkpoint_path(store)
    payload = dict(state)
    payload["updated_at"] = admission.utc_now()
    if not payload.get("started_at"):
        payload["started_at"] = payload["updated_at"]
    admission.atomic_write_json(path, payload)
    return path


def load_queue_sources(queue_path: Path | None = None) -> list[dict[str, Any]]:
    path = Path(queue_path or DEFAULT_QUEUE)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [dict(s) for s in (payload.get("sources") or []) if isinstance(s, dict)]


def queue_by_svid(queue_path: Path | None = None) -> dict[str, dict[str, Any]]:
    return {str(s.get("source_version_id")): s for s in load_queue_sources(queue_path)}


def run_screening(
    *,
    store: Path | None = None,
    queue_path: Path | None = None,
) -> dict[str, Any]:
    store_path = Path(store or DEFAULT_STORE)
    rows = screen_queue(Path(queue_path or DEFAULT_QUEUE))
    counts = Counter(r.get("status") for r in rows)
    review = [r for r in rows if r.get("status") in REVIEW_STATUSES]
    ledger = {
        "schema": "paw-cursor-visual-screening-v1/1",
        "screened_at": admission.utc_now(),
        "total": len(rows),
        "counts": dict(counts),
        "review_required": [
            {
                "source_version_id": r["source_version_id"],
                "title": r.get("title"),
                "batch": r.get("batch"),
                "status": r.get("status"),
                "reason": r.get("reason"),
                "asset_count": len(r.get("assets") or []),
                "assets": r.get("assets") or [],
            }
            for r in review
        ],
        "rows": rows,
    }
    admission.atomic_write_json(store_path / SCREENING_NAME, ledger)
    state = load_checkpoint(store_path)
    state["status"] = "SCREENED"
    state["counts"] = {
        "total_screened": len(rows),
        **{f"status_{k}": v for k, v in sorted(counts.items())},
        "review_required": len(review),
    }
    state["pending"] = [r["source_version_id"] for r in review]
    save_checkpoint(state, store_path)
    return ledger


def persist_source_review(
    *,
    source: Mapping[str, Any],
    visual_inputs: list[Mapping[str, Any]],
    grounded_visual_facts: list[Mapping[str, Any]],
    table_observations: list[Mapping[str, Any]] | None = None,
    visual_observations: list[Mapping[str, Any]] | None = None,
    ambiguities: list[Mapping[str, Any]] | None = None,
    already_grounded_skipped: list[Mapping[str, Any]] | None = None,
    page_dispositions: list[Mapping[str, Any]] | None = None,
    source_status: str,
    store: Path | None = None,
    reader_root: Path | None = None,
    merge_existing: bool = False,
) -> dict[str, Any]:
    store_path = Path(store or DEFAULT_STORE)
    state = load_checkpoint(store_path)
    dest = admission.canonical_artifact_path(
        str(source["source_version_id"]),
        str(source.get("batch") or "_unbatched"),
        reader_root=reader_root,
    )
    merged_inputs = list(visual_inputs)
    merged_facts = list(grounded_visual_facts)
    merged_tables = list(table_observations or [])
    merged_obs = list(visual_observations or [])
    merged_amb = list(ambiguities or [])
    merged_skip = list(already_grounded_skipped or [])
    merged_disp = list(page_dispositions or [])
    if merge_existing and dest.is_file():
        try:
            old = json.loads(dest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = {}
        if isinstance(old, dict) and str(old.get("status") or "").upper() == "SUCCESS":
            merged_inputs = admission.merge_visual_inputs(
                old.get("visual_inputs") or [], visual_inputs
            )
            analysis = admission.merge_analysis_payloads(
                old.get("analysis") or {},
                {
                    "grounded_visual_facts": grounded_visual_facts,
                    "table_observations": table_observations or [],
                    "visual_observations": visual_observations or [],
                    "ambiguities": ambiguities or [],
                    "already_grounded_skipped": already_grounded_skipped or [],
                    "page_dispositions": page_dispositions or [],
                },
            )
            merged_facts = analysis["grounded_visual_facts"]
            merged_tables = analysis["table_observations"]
            merged_obs = analysis["visual_observations"]
            merged_amb = analysis["ambiguities"]
            merged_skip = analysis["already_grounded_skipped"]
            merged_disp = analysis["page_dispositions"]
    shas = [str(v.get("image_sha256") or v.get("sha256") or "") for v in merged_inputs]
    if source_status in GROUNDED_STATUSES and admission.should_skip_repeat(dest, shas):
        record = {
            "status": source_status,
            "path": str(dest),
            "skipped_repeat": True,
            "updated_at": admission.utc_now(),
        }
        state.setdefault("processed", {})[str(source["source_version_id"])] = record
        state["last_source"] = str(source["source_version_id"])
        save_checkpoint(state, store_path)
        return record

    payload = admission.build_canonical_artifact(
        source=source,
        visual_inputs=merged_inputs,
        grounded_visual_facts=merged_facts,
        table_observations=merged_tables,
        visual_observations=merged_obs,
        ambiguities=merged_amb,
        already_grounded_skipped=merged_skip,
        page_dispositions=merged_disp,
        source_status=source_status,
    )
    path = admission.persist_canonical_artifact(payload, reader_root=reader_root)
    # Mirror into the workspace store for durable recovery without touching
    # existing semantic worker identities.
    mirror = store_path / "artifacts" / str(source.get("batch") or "_unbatched") / f"{source['source_version_id']}.json"
    admission.atomic_write_json(mirror, payload)
    record = {
        "status": source_status,
        "path": str(path),
        "mirror": str(mirror),
        "image_sha256s": [i["image_sha256"] for i in payload["visual_inputs"]],
        "image_paths": [i["image_path"] for i in payload["visual_inputs"]],
        "fact_count": len(payload["analysis"]["grounded_visual_facts"]),
        "skipped_repeat": False,
        "updated_at": payload["completed_at"],
    }
    state.setdefault("processed", {})[str(source["source_version_id"])] = record
    state["last_source"] = str(source["source_version_id"])
    state["last_image"] = payload["visual_input"]["image_path"]
    state["last_error"] = None
    pending = [s for s in (state.get("pending") or []) if s != source["source_version_id"]]
    state["pending"] = pending
    save_checkpoint(state, store_path)
    return record


def persist_skip(
    source: Mapping[str, Any],
    status: str,
    reason: str,
    *,
    store: Path | None = None,
) -> dict[str, Any]:
    store_path = Path(store or DEFAULT_STORE)
    state = load_checkpoint(store_path)
    record = {
        "status": status,
        "reason": reason,
        "path": None,
        "updated_at": admission.utc_now(),
    }
    state.setdefault("processed", {})[str(source["source_version_id"])] = record
    state["last_source"] = str(source["source_version_id"])
    pending = [s for s in (state.get("pending") or []) if s != source["source_version_id"]]
    state["pending"] = pending
    save_checkpoint(state, store_path)
    return record


def verify_brain_visible(source_version_id: str, artifact_path: str) -> dict[str, Any]:
    idx = admission.index_visual_coverage(source_version_id)
    semantic = admission.brain_read_semantic(source_version_id)
    visual_arts = semantic.get("visual_artifacts") or []
    cursor_hits = [
        a for a in visual_arts
        if str(a.get("worker")) == admission.WORKER
        or str(a.get("path")) == artifact_path
    ]
    return {
        "reusable_artifact_index": idx,
        "brain_available": bool(semantic.get("available")),
        "brain_visual_artifact_count": len(visual_arts),
        "brain_cursor_visual_count": len(cursor_hits),
        "brain_cursor_visual": cursor_hits,
        "identity_ok": any(
            str((a.get("payload") or {}).get("authoritative_lineage", {}).get("source_version_id"))
            == source_version_id
            for a in cursor_hits
        ),
    }
