"""Read existing Luna/Cursor semantic artifacts without re-running models."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from gpt_brain_common import hash_payload, text

DEFAULT_CURSOR_ROOT = "/home/ubuntu/paw-dev/paw-full-reader-20260902"
INHERITED_WORKERS = ("agy", "qwen", "gemini31pro")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


class LunaSemanticReader:
    def __init__(self, root: str | os.PathLike[str] | None = None):
        self.root = Path(root or os.environ.get("PAW_CURSOR_FULL_READER_ROOT", DEFAULT_CURSOR_ROOT))
        self.state_root = self.root / "state" / "cursor_semantic_v1"
        self.queue_path = self.state_root / "queue.json"
        self.results_root = self.state_root / "results"
        self.semantic_root = self.root / "semantic"

    def queue_sources(self) -> list[dict[str, Any]]:
        queue = _read_json(self.queue_path) or {}
        return [dict(x) for x in queue.get("sources", []) if isinstance(x, Mapping) and text(x.get("source_version_id"))]

    def _cursor_result_path(self, source: Mapping[str, Any]) -> Path:
        return self.results_root / text(source.get("batch")) / f"{text(source.get('source_version_id'))}.json"

    def _inherited_paths(self, source_version_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for worker in INHERITED_WORKERS:
            worker_root = self.semantic_root / worker
            if not worker_root.is_dir():
                continue
            for batch_dir in sorted(p for p in worker_root.iterdir() if p.is_dir()):
                path = batch_dir / f"{source_version_id}.json"
                if not path.is_file():
                    continue
                payload = _read_json(path)
                if isinstance(payload, Mapping):
                    out.append({"worker": worker, "path": str(path), "payload": dict(payload)})
        return out

    def read_semantic(self, source_version_id: str, source_entry: Mapping[str, Any] | None = None) -> dict[str, Any]:
        source = dict(source_entry or {})
        if not source:
            for item in self.queue_sources():
                if item.get("source_version_id") == source_version_id:
                    source = item
                    break
        cursor_path = self._cursor_result_path(source) if source else None
        cursor_payload = _read_json(cursor_path) if cursor_path and cursor_path.is_file() else None
        inherited = self._inherited_paths(source_version_id)
        primary = cursor_payload if isinstance(cursor_payload, Mapping) else None
        if primary is None and inherited:
            primary = inherited[0]["payload"]
        available = primary is not None
        artifacts = []
        if isinstance(cursor_payload, Mapping):
            artifacts.append({
                "worker": "cursor",
                "model": cursor_payload.get("model") or "cursor",
                "path": str(cursor_path),
                "payload": dict(cursor_payload),
                "inherited": False,
            })
        for item in inherited:
            artifacts.append({**item, "inherited": True, "model": item["payload"].get("model") or item["worker"]})
        return {
            "source_version_id": source_version_id,
            "available": available,
            "source": source,
            "semantic_result": dict(primary) if isinstance(primary, Mapping) else None,
            "path": str(cursor_path) if cursor_path else None,
            "semantic_artifacts": artifacts,
        }


def extract_projection_fields(semantic: Mapping[str, Any] | None, source_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
    payload = semantic if isinstance(semantic, Mapping) else {}
    identity = payload.get("source_identity") if isinstance(payload.get("source_identity"), Mapping) else {}
    provenance = payload.get("provenance") if isinstance(payload.get("provenance"), Mapping) else {}
    key_facts = list(payload.get("key_facts") or [])
    metrics = list(payload.get("metrics_data_signals") or payload.get("metrics") or [])
    timeline = list(payload.get("timeline_decision_signals") or payload.get("timeline") or [])
    decisions = list(payload.get("decisions") or [])
    risks = list(payload.get("risks") or payload.get("issues") or [])
    ambiguities = list(payload.get("conflicts_ambiguities") or payload.get("ambiguities") or [])
    project_signals = list(payload.get("project_domain_signals") or payload.get("project_signals") or [])
    evidence_refs = list(payload.get("source_evidence_pointers") or payload.get("evidence") or [])
    title = text(payload.get("title") or identity.get("title"))
    summary_parts = []
    for fact in key_facts[:3]:
        if isinstance(fact, Mapping) and fact.get("content"):
            summary_parts.append(str(fact["content"]))
    if not summary_parts and source_state:
        summary_parts.append(text(source_state.get("reason")))
    summary = " ".join(summary_parts)[:2000] if summary_parts else title
    processor = source_state.get("processor_provenance") if isinstance(source_state, Mapping) else {}
    processor = processor if isinstance(processor, Mapping) else {}
    return {
        "source_id": text(identity.get("source_id") or payload.get("source_id")),
        "source_title": title,
        "summary": summary,
        "facts": key_facts,
        "decisions": decisions,
        "metrics": metrics,
        "timeline": timeline,
        "risks": risks,
        "ambiguities": ambiguities,
        "related_projects": project_signals,
        "evidence_refs": evidence_refs,
        "provenance": provenance,
        "preanalysis_model": text(payload.get("model") or "cursor"),
        "preanalysis_processor": text(processor.get("actor")) or "cursor-semantic",
        "semantic_hash": hash_payload(payload) if payload else None,
    }
