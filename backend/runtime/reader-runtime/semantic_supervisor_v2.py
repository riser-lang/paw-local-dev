#!/usr/bin/env python3
"""PAW semantic scheduling v2.

This module deliberately lives beside ``semantic_supervisor.py``.  It is a
file-backed, source-version keyed scheduler; importing it does not start any
worker and it never reads semantic_state.json to decide what is runnable.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import semantic_supervisor as legacy


BASE = Path(__file__).resolve().parent
REGISTRY_PATH = BASE / "state" / "canonical_reader_batches.json"
V2_STATE_ROOT = BASE / "state" / "semantic_v2"
PATROL_PACKET_PATH = BASE / "state" / "patrol_analysis_packet.json"
QWEN_SOFT_CREDIT_CAP = 2800.0
LOW_DISK_WATERMARK_BYTES = 4 * 1024**3
DEFAULT_MEMORY_RESERVE_BYTES = 512 * 1024**2
DEFAULT_LARGE_READER_BYTES = 4 * 1024**2
DEFAULT_LARGE_UNIT_COUNT = 2000
ORDER = (
    "00_core_mentor", "01_core_intern", "02_core_bootcamp",
    "03_core_branch_training", "04_core_talent_review",
    "10_associated_sales_competition", "20_cross_project_shared",
    "30_shared_reference", "90_reference_other",
)
CORE_BATCHES = set(ORDER[:6])
CORE_PROJECT_NAMES = (
    "运营导师体系", "运营实习生", "星火计划新兵营", "兵种培训",
    "干部管理人才盘点", "销售顾问技能大赛",
)
VISUAL_KEYWORDS = ("方案", "汇报", "复盘", "手册", "介绍", "制度", "竞聘", "规划", "盘点", "培训", "课程", "大赛", "总结", "报告")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_name(value: str) -> str:
    return "".join(c if c.isalnum() or c in "._-@#" else "_" for c in str(value))[:190] or "unknown"


def atomic_json(path: Path, value: Any) -> None:
    """Atomic JSON writer restricted to Analysis Plane/state destinations."""
    resolved = path.resolve()
    # Test/deployment roots may be relocated, so accept the same relative
    # Analysis Plane destinations under another base while rejecting arbitrary
    # infrastructure paths.
    is_v2_state = resolved.parent.name == "semantic_v2" or "semantic_v2" in resolved.parts and "state" in resolved.parts
    is_patrol = resolved.name == "patrol_analysis_packet.json" and resolved.parent.name == "state"
    if not is_v2_state and not is_patrol:
        raise ValueError(f"v2 writer refuses non-Analysis-Plane path: {path}")
    if "formal" in str(resolved).lower():
        raise ValueError("Formal State path is prohibited")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def read_json(path: Path, fallback: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return fallback


def result_is_success(path: Path, source_version_id: str | None = None) -> bool:
    value = read_json(path)
    if not isinstance(value, dict) or value.get("status") != "SUCCESS" or not isinstance(value.get("analysis"), dict):
        return False
    if source_version_id is not None:
        lineage = value.get("authoritative_lineage") or {}
        if lineage.get("source_version_id") not in (None, source_version_id):
            return False
    return True


@dataclass(frozen=True)
class SourceRecord:
    batch: str
    ordinal: int
    source_id: str
    source_version_id: str
    stable_object_id: str
    version_marker: str
    title: str
    inventory_path: str
    family: str
    project_tier: str
    paw_project_name: str
    paw_project_id: str | None
    raw_sha256: str
    reader_result_path: str

    def key(self) -> str:
        return self.source_version_id

    def as_meta(self) -> dict[str, Any]:
        return asdict(self)


class CanonicalReaderIndex:
    """Canonical Reader registry + ledger + artifact manifests only."""

    def __init__(self, base: Path = BASE, registry_path: Path | None = None, semantic_root: Path | None = None):
        self.base = Path(base)
        self.registry_path = registry_path or self.base / "state" / "canonical_reader_batches.json"
        self.semantic_root = semantic_root or self.base / "semantic"

    def registry(self) -> dict[str, Any]:
        value = read_json(self.registry_path, {})
        return value if isinstance(value, dict) else {}

    def records(self) -> list[SourceRecord]:
        registry = self.registry()
        records: list[SourceRecord] = []
        for batch in ORDER:
            spec = (registry.get("batches") or {}).get(batch) or {}
            if spec.get("canonical") is not True or spec.get("gate") != "PASS":
                continue
            ledger = read_json(Path(str(spec.get("ledger_path", ""))), {})
            manifest = read_json(self.base / "manifests" / f"{batch}_artifact_manifest.json", {})
            by_version = {str(row.get("source_version_id")): row for row in (manifest.get("artifacts") or []) if isinstance(row, dict)}
            entries = [e for e in (ledger.get("entries") or []) if e.get("status") == "COMPLETED" and e.get("result_path")]
            entries.sort(key=lambda e: str((e.get("source_lineage") or {}).get("source_version_id", "")))
            for ordinal, entry in enumerate(entries, 1):
                lineage = entry.get("source_lineage") or {}
                svid = str(lineage.get("source_version_id") or "")
                row = by_version.get(svid, {})
                artifact = entry.get("artifact") or {}
                result_path = Path(str(entry.get("result_path")))
                if not result_path.is_absolute():
                    result_path = self.base / result_path
                records.append(SourceRecord(
                    batch=batch, ordinal=ordinal,
                    source_id=str(lineage.get("source_id") or row.get("source_id") or ""),
                    source_version_id=svid,
                    stable_object_id=str(lineage.get("stable_object_id") or row.get("stable_object_id") or ""),
                    version_marker=str(lineage.get("version_marker") or row.get("version_marker") or ""),
                    title=str(row.get("title") or row.get("display_name") or artifact.get("display_name") or ""),
                    inventory_path=str(row.get("inventory_path") or ""),
                    family=str(row.get("family") or artifact.get("family") or ""),
                    project_tier=str(row.get("project_tier") or ("CORE" if batch in CORE_BATCHES else "REFERENCE")),
                    paw_project_name=str(row.get("paw_project_name") or ""),
                    paw_project_id=row.get("paw_project_id"),
                    raw_sha256=str(row.get("sha256") or artifact.get("sha256") or ""),
                    reader_result_path=str(result_path),
                ))
        return records

    def semantic_path(self, model_dir: str, record: SourceRecord) -> Path:
        return self.semantic_root / model_dir / record.batch / f"{safe_name(record.source_version_id)}.json"

    def semantic_success(self, model_dir: str, record: SourceRecord) -> bool:
        return result_is_success(self.semantic_path(model_dir, record), record.source_version_id)

    def reader(self, record: SourceRecord) -> dict[str, Any]:
        value = read_json(Path(record.reader_result_path), {})
        return value if isinstance(value, dict) else {}


def reader_visual_required(record: SourceRecord, reader: dict[str, Any], flash_result: dict[str, Any] | None = None) -> tuple[bool, str]:
    if record.project_tier != "CORE" or record.family.upper() not in {"PPTX", "PDF"}:
        return False, ""
    flags = reader.get("fidelity_flags") if isinstance(reader.get("fidelity_flags"), dict) else {}
    if int(flags.get("chart_count") or 0) > 0:
        return True, "reader_chart_count"
    if int(flags.get("connector_count") or 0) > 0:
        return True, "reader_connector_count"
    if any(word in record.title for word in VISUAL_KEYWORDS):
        return True, "important_visual_title"
    analysis = (flash_result or {}).get("analysis") or {}
    if analysis.get("needs_visual_review") is True:
        return True, "flash_requested_visual_review"
    return False, ""


def qwen_requested(record: SourceRecord, reader: dict[str, Any], flash_result: dict[str, Any] | None = None) -> tuple[bool, str]:
    if record.project_tier == "CORE":
        return True, "core"
    flags = reader.get("fidelity_flags") if isinstance(reader.get("fidelity_flags"), dict) else {}
    if any(int(flags.get(k) or 0) > 0 for k in ("formula_count", "chart_count", "slide_count", "table_count")):
        return True, "data_heavy_or_structured"
    if any(token in record.title for token in ("冲突", "对比", "复盘", "数据", "指标", "台账", "方案", "规划")):
        return True, "high_value_or_conflict_signal"
    analysis = (flash_result or {}).get("analysis") or {}
    if analysis.get("conflicts") or analysis.get("ambiguities"):
        return True, "flash_conflict_or_ambiguity"
    return False, ""


class PersistentQueue:
    """One JSON queue per worker, with leases and atomic claim/ack."""

    def __init__(self, worker: str, root: Path = V2_STATE_ROOT):
        if worker not in {"flash", "qwen", "visual"}:
            raise ValueError(worker)
        self.worker = worker
        self.root = Path(root)
        self.path = self.root / f"queue_{worker}.json"
        self.lock_path = self.root / f"queue_{worker}.lock"

    @contextlib.contextmanager
    def lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _load(self) -> dict[str, Any]:
        value = read_json(self.path, None)
        if not isinstance(value, dict) or not isinstance(value.get("items"), dict):
            return {"schema": "paw-semantic-queue-v2/1", "worker": self.worker, "updated_at": utc_now(), "items": {}}
        return value

    def snapshot(self) -> dict[str, Any]:
        with self.lock():
            return self._load()

    def reconcile(self, desired: Iterable[SourceRecord], success: Callable[[SourceRecord], bool]) -> dict[str, Any]:
        desired_map = {record.key(): record for record in desired}
        with self.lock():
            state = self._load()
            items = state.setdefault("items", {})
            now_epoch = time.time()
            for key, record in desired_map.items():
                item = items.get(key) or {}
                if item.get("status") == "running" and float(item.get("lease_until_epoch") or 0) < now_epoch:
                    item["status"] = "pending"; item["lease_until_epoch"] = None
                if success(record):
                    item.update(status="succeeded", source_version_id=key, updated_at=utc_now(), lease_until_epoch=None)
                elif item.get("status") not in {"running", "paused"}:
                    item.update(status="pending", source_version_id=key, updated_at=item.get("updated_at") or utc_now(), attempt=int(item.get("attempt") or 0))
                item.update(source=record.as_meta())
                items[key] = item
            # A changed canonical registry removes no historical record, but
            # non-desired items are explicitly tombstoned for auditability.
            for key, item in items.items():
                if key not in desired_map and item.get("status") in {"pending", "running", "paused"}:
                    item.update(status="not_required", updated_at=utc_now(), lease_until_epoch=None)
            state["updated_at"] = utc_now()
            atomic_json(self.path, state)
            return state

    def recover_stale(self) -> int:
        changed = 0
        with self.lock():
            state = self._load()
            for item in state.get("items", {}).values():
                if item.get("status") == "running" and float(item.get("lease_until_epoch") or 0) < time.time():
                    item.update(status="pending", lease_until_epoch=None, updated_at=utc_now()); changed += 1
            if changed:
                state["updated_at"] = utc_now(); atomic_json(self.path, state)
        return changed

    def claim(self, lease_seconds: int = 3600) -> dict[str, Any] | None:
        self.recover_stale()
        with self.lock():
            state = self._load()
            candidates = [(key, value) for key, value in state.get("items", {}).items() if value.get("status") == "pending"]
            candidates.sort(key=lambda pair: (int((pair[1].get("source") or {}).get("ordinal") or 0), pair[0]))
            if not candidates:
                return None
            key, item = candidates[0]
            item.update(status="running", attempt=int(item.get("attempt") or 0) + 1, claimed_at=utc_now(), updated_at=utc_now(), lease_until_epoch=time.time() + lease_seconds)
            state["updated_at"] = utc_now(); atomic_json(self.path, state)
            return {"queue_key": key, **item}

    def finish(self, key: str, status: str, **fields: Any) -> None:
        if status not in {"succeeded", "pending", "failed", "paused"}:
            raise ValueError(status)
        with self.lock():
            state = self._load(); item = state.setdefault("items", {}).setdefault(key, {})
            item.update(status=status, updated_at=utc_now(), lease_until_epoch=None, **fields)
            state["updated_at"] = utc_now(); atomic_json(self.path, state)

    def counts(self) -> dict[str, int]:
        items = self.snapshot().get("items", {})
        result = {name: 0 for name in ("pending", "running", "succeeded", "failed", "paused", "not_required")}
        for item in items.values():
            result[item.get("status", "pending")] = result.get(item.get("status", "pending"), 0) + 1
        result["depth"] = result["pending"] + result["running"]
        return result


class QwenCreditLedger:
    """Model-specific soft cap; it has no state or control path for Flash."""

    def __init__(self, root: Path = V2_STATE_ROOT, cap: float = QWEN_SOFT_CREDIT_CAP):
        self.root, self.path, self.cap = Path(root), Path(root) / "qwen_credit_ledger.json", cap
        self.lock_path = Path(root) / "qwen_credit_ledger.lock"

    def used(self) -> float:
        value = read_json(self.path, {})
        return float(value.get("credits_consumed", 0.0)) if isinstance(value, dict) else 0.0

    def can_spend(self) -> bool:
        return self.used() < self.cap

    def bootstrap_from_results(self, index: CanonicalReaderIndex) -> float:
        """Seed once from actual SUCCESS files so v2 cannot overspend history."""
        if self.path.is_file():
            return self.used()
        total = 0.0
        for record in index.records():
            path = index.semantic_path("qwen", record)
            value = read_json(path, {})
            if result_is_success(path, record.source_version_id):
                try:
                    total += float(value.get("credits") or 0.0)
                except (TypeError, ValueError):
                    pass
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_json(self.path, {"model": "Qwen3.8-Max", "credits_consumed": round(total, 6), "soft_credit_cap": self.cap, "updated_at": utc_now(), "paused": total >= self.cap, "bootstrapped_from": "actual semantic/qwen SUCCESS files"})
        return round(total, 6)

    def add(self, credits: float) -> float:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                current = read_json(self.path, {})
                total = round(float((current or {}).get("credits_consumed", 0.0)) + float(credits or 0.0), 6)
                atomic_json(self.path, {"model": "Qwen3.8-Max", "credits_consumed": total, "soft_credit_cap": self.cap, "updated_at": utc_now(), "paused": total >= self.cap})
                return total
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class ResourceGuard:
    """Conservative guards for a 2GB host and a 4GiB disk low watermark."""

    def __init__(self, base: Path = BASE, low_disk_bytes: int = LOW_DISK_WATERMARK_BYTES, memory_reserve_bytes: int = DEFAULT_MEMORY_RESERVE_BYTES):
        self.base = Path(base)
        self.low_disk_bytes = low_disk_bytes
        self.memory_reserve_bytes = memory_reserve_bytes
        self.active: dict[str, int] = {"qwen": 0, "flash": 0, "visual": 0}
        self.lock = threading.Lock()

    def disk_free_bytes(self) -> int:
        return int(shutil.disk_usage(self.base).free)

    def memory_available_bytes(self) -> int:
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError):
            pass
        return 0

    def disk_safe(self) -> bool:
        return self.disk_free_bytes() >= self.low_disk_bytes

    def memory_safe(self) -> bool:
        return self.memory_available_bytes() >= self.memory_reserve_bytes

    def can_start(self, worker: str, *, qwen_active: bool = False, resource_safe: bool | None = None) -> bool:
        if worker not in self.active:
            raise ValueError(worker)
        if not self.disk_safe():
            return False
        if resource_safe is None:
            resource_safe = self.memory_safe()
        if not resource_safe:
            return False
        with self.lock:
            if worker == "visual":
                return self.active[worker] == 0 and not qwen_active and self.active["qwen"] == 0
            return self.active[worker] == 0

    @contextlib.contextmanager
    def slot(self, worker: str):
        with self.lock:
            if self.active[worker]:
                raise RuntimeError(f"{worker} slot already active")
            if worker == "visual" and self.active["qwen"]:
                raise RuntimeError("visual cannot overlap Qwen")
            if worker == "qwen" and self.active["visual"]:
                raise RuntimeError("Qwen cannot overlap visual")
            self.active[worker] += 1
        try:
            yield
        finally:
            with self.lock:
                self.active[worker] = max(0, self.active[worker] - 1)

    def status(self) -> dict[str, Any]:
        with self.lock:
            active = dict(self.active)
        return {"disk_free_bytes": self.disk_free_bytes(), "disk_low_watermark_bytes": self.low_disk_bytes, "memory_available_bytes": self.memory_available_bytes(), "memory_reserve_bytes": self.memory_reserve_bytes, "active": active, "disk_safe": self.disk_safe(), "memory_safe": self.memory_safe()}


def canonical_progress(index: CanonicalReaderIndex, state_root: Path = V2_STATE_ROOT) -> dict[str, Any]:
    """Persistable progress derived from the registry/results, never aggregates."""
    records = index.records()
    workers = {}
    for worker, model in (("flash", "agy"), ("qwen", "qwen"), ("visual", "gemini31pro")):
        queue = PersistentQueue(worker, state_root)
        counts = queue.counts()
        workers[worker] = {"model": {"flash": "gemini-3.7-flash-high", "qwen": "Qwen3.8-Max", "visual": "gemini-3.1-pro-high"}[worker], "success": sum(index.semantic_success(model, record) for record in records), "expected": len(records), "queue": counts}
    progress = {"schema": "paw-canonical-semantic-progress-v2/1", "updated_at": utc_now(), "source_of_truth": [str(index.registry_path), "actual semantic result files", "persistent v2 queue leases"], "canonical_source_total": len(records), "coverage": {"covered": workers["flash"]["success"], "expected": len(records), "fraction": f"{workers['flash']['success']}/{len(records)}"}, "workers": workers, "formal_state_write": "PROHIBITED"}
    atomic_json(Path(state_root) / "progress.json", progress)
    return progress


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


@dataclass(frozen=True)
class ReaderShard:
    shard_id: str
    source_version_id: str
    raw_sha256: str
    unit_type: str
    unit_start: int
    unit_end: int
    payload: dict[str, Any]
    locator_map: dict[str, str]


def _chunked(values: list[Any], size: int) -> Iterable[tuple[int, list[Any]]]:
    for start in range(0, len(values), size):
        yield start, values[start:start + size]


def build_reader_shards(record: SourceRecord, reader: dict[str, Any], *, max_bytes: int = DEFAULT_LARGE_READER_BYTES, max_units: int = DEFAULT_LARGE_UNIT_COUNT) -> list[ReaderShard]:
    """Make deterministic, bounded shards and retain a local->original locator map."""
    sections = [(name, reader.get(name)) for name in ("sheets", "slides", "pages", "tables", "structured_semantics", "text_blocks")]
    large = _json_size(reader) > max_bytes or sum(len(v) for _, v in sections if isinstance(v, list)) > max_units
    if not large:
        return [ReaderShard("source", record.source_version_id, record.raw_sha256, "source", 0, 1, dict(reader), {})]
    shards: list[ReaderShard] = []
    # Lists are split by their natural evidence unit.  Structured semantics is
    # also split by named section so a giant source cannot monopolise Qwen.
    for section, value in sections:
        if isinstance(value, list) and value:
            size = max(1, min(max_units, max(1, max_units // 4)))
            for n, (start, chunk) in enumerate(_chunked(value, size), 1):
                payload = {"_paw_shard": {"source_version_id": record.source_version_id, "raw_sha256": record.raw_sha256, "unit_type": section, "unit_start": start, "unit_end": start + len(chunk)}, section: chunk}
                mapping = {f"{section}[{i}]": f"{section}[{start + i}]" for i in range(len(chunk))}
                shards.append(ReaderShard(f"{section}-{n:04d}", record.source_version_id, record.raw_sha256, section, start, start + len(chunk), payload, mapping))
        elif section == "structured_semantics" and isinstance(value, dict) and value:
            for n, name in enumerate(sorted(value), 1):
                payload = {"_paw_shard": {"source_version_id": record.source_version_id, "raw_sha256": record.raw_sha256, "unit_type": "structured_section", "unit_start": n - 1, "unit_end": n}, "structured_semantics": {name: value[name]}}
                shards.append(ReaderShard(f"structured-{n:04d}", record.source_version_id, record.raw_sha256, "structured_section", n - 1, n, payload, {f"structured_semantics.{name}": f"structured_semantics.{name}"}))
    if not shards:
        shards.append(ReaderShard("source", record.source_version_id, record.raw_sha256, "source", 0, 1, dict(reader), {}))
    return shards


def map_locator(locator: str, locator_map: dict[str, str]) -> str:
    return locator_map.get(locator, locator)


def merge_shard_analyses(record: SourceRecord, shard_results: list[tuple[ReaderShard, dict[str, Any]]], reader_path: str) -> dict[str, Any]:
    """Concatenate every shard in stable order; never deduplicate evidence."""
    sections = ("facts", "inferences", "project_signals", "timeline", "metrics", "decisions_or_plans", "conflicts", "risks", "ambiguities")
    merged: dict[str, Any] = {key: [] for key in sections}
    merged["related_projects"] = []
    summaries: list[str] = []
    shard_manifest: list[dict[str, Any]] = []
    for shard, result in sorted(shard_results, key=lambda pair: pair[0].shard_id):
        analysis = result.get("analysis") or {}
        summaries.append(str(analysis.get("summary") or ""))
        for section in sections:
            for item in analysis.get(section, []) if isinstance(analysis.get(section), list) else []:
                cloned = dict(item) if isinstance(item, dict) else {"content": str(item), "confidence": "low", "evidence_locators": []}
                cloned["evidence_locators"] = [map_locator(str(loc), shard.locator_map) for loc in cloned.get("evidence_locators", [])]
                merged[section].append(cloned)
        for item in analysis.get("related_projects", []) if isinstance(analysis.get("related_projects"), list) else []:
            cloned = dict(item) if isinstance(item, dict) else {"project": str(item), "relationship": "", "confidence": "low"}
            cloned["evidence_locators"] = [map_locator(str(loc), shard.locator_map) for loc in cloned.get("evidence_locators", [])]
            merged["related_projects"].append(cloned)
        shard_manifest.append({"shard_id": shard.shard_id, "source_version_id": record.source_version_id, "raw_sha256": record.raw_sha256, "unit_type": shard.unit_type, "unit_start": shard.unit_start, "unit_end": shard.unit_end, "reader_result_path": reader_path, "evidence_locators": sorted(set(shard.locator_map.values()))})
    merged.update(summary="\n".join(x for x in summaries if x), needs_visual_review=any(bool((r.get("analysis") or {}).get("needs_visual_review")) for _, r in shard_results), visual_review_reason="; ".join(str((r.get("analysis") or {}).get("visual_review_reason") or "") for _, r in shard_results if (r.get("analysis") or {}).get("visual_review_reason")))
    return {"status": "SUCCESS", "result_success": True, "model": "Qwen3.8-Max", "completed_at": utc_now(), "authoritative_lineage": {"source_id": record.source_id, "source_version_id": record.source_version_id, "stable_object_id": record.stable_object_id, "version_marker": record.version_marker, "raw_sha256": record.raw_sha256, "reader_result_path": reader_path}, "routing": {"batch": record.batch, "project_tier": record.project_tier, "paw_project_name": record.paw_project_name, "paw_project_id": record.paw_project_id, "inventory_path": record.inventory_path, "title": record.title, "family": record.family}, "credits": sum(float(r.get("credits") or 0) for _, r in shard_results), "sharded": True, "shard_count": len(shard_results), "shards": shard_manifest, "analysis": merged}


class Worker:
    """One independent worker.  ``run_once`` is intentionally testable."""

    def __init__(self, worker: str, index: CanonicalReaderIndex, queue: PersistentQueue, guard: ResourceGuard, handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None, state_root: Path = V2_STATE_ROOT):
        self.worker, self.index, self.queue, self.guard = worker, index, queue, guard
        self.handler = handler or self._default_handler
        self.state_root = Path(state_root)
        self.credit_ledger = QwenCreditLedger(self.state_root)

    def _default_handler(self, meta: dict[str, Any]) -> dict[str, Any]:
        if self.worker == "flash":
            return legacy.run_agy(meta)
        if self.worker == "visual":
            return legacy.run_agy(meta, visual=True)
        return legacy.run_qwen(meta)

    def _record(self, item: dict[str, Any]) -> SourceRecord:
        source = item.get("source") or {}
        return SourceRecord(**{key: source[key] for key in SourceRecord.__dataclass_fields__})

    def _heartbeat(self, phase: str, *, last_success_at: str | None = None) -> None:
        queue_counts = self.queue.counts()
        successes = []
        for item in self.queue.snapshot().get("items", {}).values():
            stamp = item.get("last_success_at")
            if stamp:
                try:
                    successes.append(datetime.fromisoformat(stamp).timestamp())
                except (TypeError, ValueError):
                    pass
        now_epoch = time.time()
        recent = [stamp for stamp in successes if stamp >= now_epoch - 3600]
        throughput = len(recent)
        eta = None if throughput <= 0 else round(queue_counts["depth"] / throughput * 3600, 1)
        atomic_json(self.state_root / f"heartbeat_{self.worker}.json", {
            "worker": self.worker, "pid": os.getpid(), "at": utc_now(), "phase": phase,
            "queue_depth": queue_counts["depth"], "last_success_at": last_success_at or (max(successes) and datetime.fromtimestamp(max(successes), timezone.utc).isoformat() if successes else None),
            "throughput_per_hour": throughput, "eta_seconds": eta,
        })
        canonical_progress(self.index, self.state_root)

    def run_once(self, *, qwen_active: bool = False) -> dict[str, Any] | None:
        self._heartbeat("IDLE")
        if self.worker == "qwen" and not self.credit_ledger.can_spend():
            self._heartbeat("SOFT_CAP_PAUSED")
            return {"status": "SOFT_CAP_PAUSED", "worker": "qwen", "soft_credit_cap": self.credit_ledger.cap}
        if not self.guard.can_start(self.worker, qwen_active=qwen_active):
            self._heartbeat("GUARD_BLOCKED")
            return {"status": "GUARD_BLOCKED", "worker": self.worker}
        item = self.queue.claim()
        if not item:
            self._heartbeat("IDLE")
            return None
        record = self._record(item)
        key = record.key()
        with self.guard.slot(self.worker):
            try:
                if self.worker == "qwen":
                    result = self._run_qwen_sharded(record)
                else:
                    result = self.handler(record.as_meta())
                status = "succeeded" if result.get("status") in {"SUCCESS", "SKIPPED_EXISTS"} else "pending"
                success_at = utc_now() if status == "succeeded" else None
                if self.worker == "qwen" and status == "succeeded":
                    self.credit_ledger.add(float(result.get("credits") or 0.0))
                self.queue.finish(key, status, last_result=result, last_success_at=success_at)
                self._heartbeat("IDLE", last_success_at=success_at)
                return result
            except Exception as exc:
                self.queue.finish(key, "pending", last_error=repr(exc))
                self._heartbeat("IDLE")
                return {"status": "FAILED", "kind": "CONTROLLER_EXCEPTION", "detail": repr(exc)}

    def _run_qwen_sharded(self, record: SourceRecord) -> dict[str, Any]:
        final = self.index.semantic_path("qwen", record)
        if result_is_success(final, record.source_version_id):
            return {"status": "SKIPPED_EXISTS", "path": str(final), "credits": 0.0}
        reader = self.index.reader(record)
        shards = build_reader_shards(record, reader)
        if len(shards) == 1 and shards[0].shard_id == "source":
            return self.handler(record.as_meta())
        shard_root = self.state_root / "shards" / safe_name(record.source_version_id)
        shard_results: list[tuple[ReaderShard, dict[str, Any]]] = []
        for shard in shards:
            shard_path = shard_root / f"{safe_name(shard.shard_id)}.reader_result.json"
            atomic_json(shard_path, shard.payload)
            meta = record.as_meta(); meta.update(source_version_id=f"{record.source_version_id}#shard-{shard.shard_id}", reader_result_path=str(shard_path))
            result = self.handler(meta)
            if result.get("status") not in {"SUCCESS", "SKIPPED_EXISTS"}:
                return result
            shard_path_out = Path(str(result.get("path", "")))
            payload = read_json(shard_path_out, {}) if shard_path_out.is_file() else result
            shard_results.append((shard, payload if isinstance(payload, dict) else result))
        merged = merge_shard_analyses(record, shard_results, record.reader_result_path)
        if not legacy.write_result(final, merged):
            return {"status": "SKIPPED_EXISTS", "path": str(final), "credits": 0.0}
        return {"status": "SUCCESS", "path": str(final), "credits": merged["credits"], "shard_count": len(shards)}

    def run_forever(self, stop_event: threading.Event, *, poll_seconds: float = 2.0) -> None:
        while not stop_event.is_set():
            result = self.run_once(qwen_active=self.guard.active["qwen"] > 0)
            # A guard block is expected during pressure/quota windows; avoid
            # a busy loop while leaving the other workers unaffected.
            if result is None or result.get("status") in {"GUARD_BLOCKED", "SOFT_CAP_PAUSED"}:
                stop_event.wait(poll_seconds)


def desired_queues(index: CanonicalReaderIndex) -> dict[str, list[SourceRecord]]:
    records = index.records()
    flash, qwen, visual = [], [], []
    for record in records:
        reader = index.reader(record)
        flash_result = read_json(index.semantic_path("agy", record), {})
        if not index.semantic_success("agy", record):
            flash.append(record)
        requested, _ = qwen_requested(record, reader, flash_result)
        if requested and not index.semantic_success("qwen", record):
            qwen.append(record)
        selected, _ = reader_visual_required(record, reader, flash_result)
        if selected and not index.semantic_success("gemini31pro", record):
            visual.append(record)
    return {"flash": flash, "qwen": qwen, "visual": visual}


def dry_run(index: CanonicalReaderIndex, state_root: Path = V2_STATE_ROOT) -> dict[str, Any]:
    desired = desired_queues(index)
    counts: dict[str, Any] = {"canonical_sources": len(index.records()), "workers": {}}
    for worker, records in desired.items():
        q = PersistentQueue(worker, state_root)
        existing = q.snapshot().get("items", {})
        pending = sum(1 for record in records if existing.get(record.key(), {}).get("status") not in {"succeeded"} )
        success = sum(1 for record in index.records() if index.semantic_success({"flash":"agy","qwen":"qwen","visual":"gemini31pro"}[worker], record))
        counts["workers"][worker] = {"desired": len(records), "existing_success": success, "pending_after_reconcile": pending, "queue_file": str(q.path)}
    qwen_credits = 0.0; qwen_calls = 0
    for record in index.records():
        value = read_json(index.semantic_path("qwen", record), {})
        if index.semantic_success("qwen", record):
            qwen_calls += 1
            try: qwen_credits += float(value.get("credits") or 0.0)
            except (TypeError, ValueError): pass
    assumptions = {"flash": 5.0, "qwen": 3.4, "visual": 1.0}
    counts["throughput_assumptions_per_hour"] = assumptions
    counts["estimated_hours_at_assumption"] = {worker: round(info["pending_after_reconcile"] / assumptions[worker], 1) if assumptions[worker] else None for worker, info in counts["workers"].items()}
    counts["qwen_credit_guard"] = {"actual_success_credits": round(qwen_credits, 6), "soft_cap": QWEN_SOFT_CREDIT_CAP, "actual_success_calls": qwen_calls, "average_credits_per_success": round(qwen_credits / qwen_calls, 3) if qwen_calls else None, "estimated_additional_calls_before_cap": max(0, int((QWEN_SOFT_CREDIT_CAP - qwen_credits) / (qwen_credits / qwen_calls))) if qwen_calls and qwen_credits < QWEN_SOFT_CREDIT_CAP else 0}
    return counts


def reconcile_all(index: CanonicalReaderIndex, state_root: Path = V2_STATE_ROOT) -> dict[str, Any]:
    desired = desired_queues(index)
    model_dirs = {"flash": "agy", "qwen": "qwen", "visual": "gemini31pro"}
    output = {}
    QwenCreditLedger(state_root).bootstrap_from_results(index)
    for worker, records in desired.items():
        q = PersistentQueue(worker, state_root)
        output[worker] = q.reconcile(records, lambda record, model=model_dirs[worker]: index.semantic_success(model, record))
    canonical_progress(index, state_root)
    return output


def run_workers(index: CanonicalReaderIndex, *, state_root: Path = V2_STATE_ROOT, stop_event: threading.Event | None = None, poll_seconds: float = 2.0) -> None:
    """Run all queues; not called by import or dry-run CLI."""
    if legacy_supervisor_active():
        raise RuntimeError("refusing v2 activation while legacy semantic_supervisor.py is running")
    reconcile_all(index, state_root)
    guard = ResourceGuard(index.base)
    queues = {worker: PersistentQueue(worker, state_root) for worker in ("flash", "qwen", "visual")}
    workers = {worker: Worker(worker, index, queues[worker], guard, state_root=state_root) for worker in queues}
    stop_event = stop_event or threading.Event()
    threads = [threading.Thread(target=workers[worker].run_forever, args=(stop_event,), kwargs={"poll_seconds": poll_seconds}, name=f"paw-v2-{worker}", daemon=True) for worker in ("flash", "qwen", "visual")]
    for thread in threads:
        thread.start()
    while not stop_event.is_set():
        # The patrol refresh is an index scan, not a model task.  Importing
        # here avoids a module cycle and keeps it out of the worker hot path.
        try:
            from analysis_plane import refresh_patrol_packet, patrol_refresh_due
            if patrol_refresh_due(index):
                refresh_patrol_packet(index, force=True, state_root=state_root)
        except (OSError, ValueError, TypeError, ImportError):
            pass
        if stop_event.wait(poll_seconds):
            break
    for thread in threads:
        thread.join(timeout=max(1.0, poll_seconds * 2))


def legacy_supervisor_active() -> bool:
    """Read-only activation fence; v2 cannot race the rollback supervisor."""
    lock = BASE / "state" / "semantic_supervisor.lock"
    try:
        pid = int(lock.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PAW semantic scheduling v2; default operation is a non-starting dry run")
    parser.add_argument("--dry-run", action="store_true", help="print queue depths and exit (the default)")
    parser.add_argument("--reconcile", action="store_true", help="write v2 queue state; does not start workers")
    parser.add_argument("--run", action="store_true", help="start v2 workers; requires the legacy supervisor to be stopped")
    args = parser.parse_args(argv)
    index = CanonicalReaderIndex()
    if args.run:
        run_workers(index)
        return 0
    report = dry_run(index)
    if args.reconcile:
        reconcile_all(index)
        report["reconciled"] = True
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
