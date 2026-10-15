#!/usr/bin/env python3
"""PAW PPTX analyze-then-evict retention manager.

This module is deliberately independent of Playwright and of the semantic
supervisor.  It reads the WPS inventory and the already canonicalized Reader
and semantic evidence, and only removes a PPTX after an auditable SQLite
receipt has been durably committed.
Deleting local source bytes additionally requires an independent, verified
cold copy of this exact SourceVersion. An analysis/hash receipt alone is not
source availability or a promise that WPS can restore an old version.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Iterable, Mapping


# Paths come from the explicit PAW Runtime Path Contract;
# no development-tree fallback.
_WORKER_ROOT = (
    os.environ.get("PAW_RUNTIME_MODULE_ROOT")
)
if not _WORKER_ROOT:
    raise RuntimeError(
        "PAW_RUNTIME_MODULE_ROOT is required "
        "(PAW Runtime Path Contract, /etc/paw-runtime-paths.env)"
    )
WORKER_DIR = Path(_WORKER_ROOT)
_READER_DIR_val = os.environ.get("PAW_READER_RUNTIME_ROOT")
if not _READER_DIR_val:
    raise RuntimeError(
        "PAW_READER_RUNTIME_ROOT is required "
        "(PAW Runtime Path Contract, /etc/paw-runtime-paths.env)"
    )
READER_DIR = Path(_READER_DIR_val)
DEFAULT_STORE = Path(os.environ["PAW_CDP_STORE_ROOT"])
DEFAULT_INVENTORY = Path(os.environ.get("PAW_PPTX_INVENTORY_PATH",
    str(Path(os.environ.get("PAW_WPS_0600_MIRROR_ROOT", "/var/lib/paw-wps-mirror")) / "inventory-last-good.json")))
DEFAULT_REGISTRY = READER_DIR / "state/canonical_reader_batches.json"
DEFAULT_LEDGER = DEFAULT_STORE / "pptx_retention.sqlite3"
DEFAULT_REPORT = DEFAULT_STORE / "pptx_retention_report.json"
DEFAULT_REPORT_MD = DEFAULT_STORE / "pptx_retention_report.md"
PRODUCTION_MIRROR_ROOT = Path(os.environ.get("PAW_WPS_0600_MIRROR_ROOT", "/var/lib/paw-wps-mirror"))
DEFAULT_LIVE_STATE_DB = PRODUCTION_MIRROR_ROOT / "state.sqlite3"
_READER_FIDELITY_ROOT_val = os.environ.get("PAW_READER_FIDELITY_ROOT")
if not _READER_FIDELITY_ROOT_val:
    raise RuntimeError(
        "PAW_READER_FIDELITY_ROOT is required "
        "(PAW Runtime Path Contract, /etc/paw-runtime-paths.env)"
    )
READER_FIDELITY_ROOT = Path(_READER_FIDELITY_ROOT_val)

LOCAL_RAW = "LOCAL_RAW"
EVICTED_ANALYZED = "EVICTED_ANALYZED"
REHYDRATE_REQUIRED = "REHYDRATE_REQUIRED"
ANALYZED_HASH_RECEIPT = "ANALYZED_HASH_RECEIPT"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def file_sha256(path: str | os.PathLike[str]) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def resolve_evidence_path(path_value: str | os.PathLike[str] | None, *roots: str | os.PathLike[str]) -> Path:
    """Resolve a Reader ledger path that may be stored relative to the Reader tree."""
    path = Path(_as_text(path_value))
    if str(path) and path.is_file():
        return path
    if path.is_absolute():
        return path
    for root in roots:
        if not root:
            continue
        candidate = Path(root) / path
        if candidate.is_file():
            return candidate
    return path


def _source_version(item: Mapping[str, Any]) -> str:
    value = _as_text(item.get("source_version"))
    if value and "@" in value:
        return value
    version = value or _as_text(item.get("version"))
    object_id = _as_text(item.get("object_id"))
    return f"{object_id}@{version}" if object_id and version else version


def _source_size(item: Mapping[str, Any]) -> int | None:
    value = item.get("size")
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def source_identity(item: Mapping[str, Any]) -> tuple[str, str, str, int | None, str]:
    return (
        _as_text(item.get("object_id")),
        _source_version(item),
        _as_text(item.get("mtime")),
        _source_size(item),
        _as_text(item.get("metadata_fingerprint")),
    )


def identity_matches(item: Mapping[str, Any], receipt: Mapping[str, Any]) -> bool:
    """Strictly compare inventory identity with a durable receipt.

    A missing field is not treated as equal.  The only allowed fallback is
    the legacy identity tuple when *both* sides do not have a fingerprint.
    """
    oid, ver, mtime, size, fp = source_identity(item)
    if not oid or not ver or not mtime or size is None:
        return False
    if _as_text(receipt.get("object_id")) != oid:
        return False
    if _as_text(receipt.get("source_version")) != ver:
        return False
    if _as_text(receipt.get("source_mtime")) != mtime:
        return False
    try:
        if int(receipt.get("source_size")) != size:
            return False
    except (TypeError, ValueError):
        return False
    receipt_fp = _as_text(receipt.get("metadata_fingerprint"))
    if fp or receipt_fp:
        return bool(fp and receipt_fp and fp == receipt_fp)
    return True


class PptxRetentionLedger:
    """SQLite ledger with a current receipt and append-only audit events."""

    def __init__(self, path: str | os.PathLike[str] = DEFAULT_LEDGER):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS source_versions (
              object_id TEXT NOT NULL,
              source_version TEXT NOT NULL,
              source_mtime TEXT NOT NULL DEFAULT '',
              source_size INTEGER,
              metadata_fingerprint TEXT NOT NULL DEFAULT '',
              display_name TEXT NOT NULL DEFAULT '',
              canonical_inventory_path TEXT NOT NULL DEFAULT '',
              original_artifact_path TEXT NOT NULL DEFAULT '',
              original_size INTEGER NOT NULL DEFAULT 0,
              sha256 TEXT NOT NULL DEFAULT '',
              reader_result_path TEXT NOT NULL DEFAULT '',
              reader_result_sha256 TEXT NOT NULL DEFAULT '',
              canonical_reader_gate_evidence TEXT NOT NULL DEFAULT '{}',
              semantic_success_evidence TEXT NOT NULL DEFAULT '{}',
              visual_requirement TEXT NOT NULL DEFAULT 'pending',
              visual_state TEXT NOT NULL DEFAULT 'pending',
              retention_status TEXT NOT NULL DEFAULT 'LOCAL_RAW',
              evicted_at TEXT NOT NULL DEFAULT '',
              rehydrate_count INTEGER NOT NULL DEFAULT 0,
              alias_of_source_version TEXT NOT NULL DEFAULT '',
              source_content_drift TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (object_id, source_version)
            );
            CREATE TABLE IF NOT EXISTS audit_events (
              event_id INTEGER PRIMARY KEY AUTOINCREMENT,
              object_id TEXT NOT NULL,
              source_version TEXT NOT NULL,
              event_type TEXT NOT NULL,
              event_at TEXT NOT NULL,
              payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_source_sha ON source_versions(sha256);
            CREATE INDEX IF NOT EXISTS idx_event_source ON audit_events(object_id, source_version);
            """)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(source_versions)")}
            if "source_recovery_evidence" not in columns:
                conn.execute("ALTER TABLE source_versions ADD COLUMN source_recovery_evidence TEXT NOT NULL DEFAULT '{}'")

    def _durable(self, conn: sqlite3.Connection) -> None:
        conn.commit()
        # SQLite FULL synchronous commits the database/WAL.  fsync the main
        # file as an additional receipt durability barrier where present.
        try:
            fd = os.open(self.path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass

    def get(self, object_id: str, source_version: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM source_versions WHERE object_id=? AND source_version=?",
                (str(object_id), str(source_version)),
            ).fetchone()
        return dict(row) if row else None

    def all(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM source_versions ORDER BY object_id, source_version")]

    def append_event(self, object_id: str, source_version: str, event_type: str, payload: Mapping[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_events(object_id,source_version,event_type,event_at,payload) VALUES(?,?,?,?,?)",
                (str(object_id), str(source_version), event_type, now_iso(), canonical_json(dict(payload))),
            )
            self._durable(conn)

    def events(self, object_id: str, source_version: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM audit_events WHERE object_id=? AND source_version=? ORDER BY event_id",
                (str(object_id), str(source_version)),
            )]

    def upsert(self, values: Mapping[str, Any], event_type: str = "OBSERVED") -> dict[str, Any]:
        v = dict(values)
        v["object_id"] = _as_text(v.get("object_id"))
        v["source_version"] = _as_text(v.get("source_version"))
        if not v["object_id"] or not v["source_version"]:
            raise ValueError("object_id and source_version are required")
        old = self.get(v["object_id"], v["source_version"])
        merged = dict(old or {})
        merged.update(v)
        ts = now_iso()
        merged.setdefault("created_at", old.get("created_at", ts) if old else ts)
        merged["updated_at"] = ts
        defaults = {
            "source_mtime":"", "source_size":None, "metadata_fingerprint":"", "display_name":"",
            "canonical_inventory_path":"", "original_artifact_path":"", "original_size":0,
            "sha256":"", "reader_result_path":"", "reader_result_sha256":"",
            "canonical_reader_gate_evidence":"{}", "semantic_success_evidence":"{}",
            "visual_requirement":"pending", "visual_state":"pending", "retention_status":LOCAL_RAW,
            "evicted_at":"", "rehydrate_count":0, "alias_of_source_version":"",
            "source_content_drift":"",
            "source_recovery_evidence":"{}",
        }
        for k, default in defaults.items():
            merged.setdefault(k, default)
        cols = [
            "object_id", "source_version", "source_mtime", "source_size", "metadata_fingerprint",
            "display_name", "canonical_inventory_path", "original_artifact_path", "original_size",
            "sha256", "reader_result_path", "reader_result_sha256", "canonical_reader_gate_evidence",
            "semantic_success_evidence", "visual_requirement", "visual_state", "retention_status",
            "evicted_at", "rehydrate_count", "alias_of_source_version", "source_content_drift",
            "created_at", "updated_at",
            "source_recovery_evidence",
        ]
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO source_versions (" + ",".join(cols) + ") VALUES (" + ",".join("?" for _ in cols) + ") "
                "ON CONFLICT(object_id,source_version) DO UPDATE SET " +
                ",".join(f"{c}=excluded.{c}" for c in cols if c not in {"object_id", "source_version", "created_at"}),
                tuple(merged.get(c) for c in cols),
            )
            conn.execute(
                "INSERT INTO audit_events(object_id,source_version,event_type,event_at,payload) VALUES(?,?,?,?,?)",
                (v["object_id"], v["source_version"], event_type, ts, canonical_json(v)),
            )
            self._durable(conn)
        return self.get(v["object_id"], v["source_version"]) or merged

    def commit_eviction_receipt(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        """Durably write the complete pre-unlink receipt."""
        v = dict(receipt)
        v["retention_status"] = "EVICTION_RECEIPT_COMMITTED"
        v["evicted_at"] = v.get("evicted_at") or now_iso()
        return self.upsert(v, event_type="EVICTION_RECEIPT_COMMITTED")

    def mark_evicted(self, object_id: str, source_version: str) -> dict[str, Any]:
        row = self.get(object_id, source_version)
        if not row:
            raise KeyError((object_id, source_version))
        row["retention_status"] = EVICTED_ANALYZED
        row["updated_at"] = now_iso()
        return self.upsert(row, event_type="RAW_UNLINKED_VERIFIED")

    def mark_drift(self, object_id: str, source_version: str, observed_sha256: str) -> dict[str, Any] | None:
        row = self.get(object_id, source_version)
        if not row:
            return None
        row["retention_status"] = REHYDRATE_REQUIRED
        row["source_content_drift"] = observed_sha256
        row["rehydrate_count"] = int(row.get("rehydrate_count") or 0) + 1
        # Keep original sha256 and source metadata immutable.
        return self.upsert(row, event_type="SOURCE_CONTENT_DRIFT")

    def find_by_hash(self, sha256: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM source_versions WHERE sha256=? ORDER BY object_id,source_version", (sha256,))]


@dataclass
class Evaluation:
    item: dict[str, Any]
    receipt: dict[str, Any]
    status: str
    reason: str
    raw_bytes: int = 0
    semantic_model: str = ""
    visual_path: str = ""
    busy: bool = False

    @property
    def eligible(self) -> bool:
        return self.status == "ELIGIBLE"


def load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def load_inventory(path: Path, require_timestamp: bool = False) -> list[dict[str, Any]]:
    # Absence, malformed JSON and schema changes must not become an empty
    # successful sweep. WPS publishes this snapshot only after a real full scan.
    data = json.loads(path.read_text(encoding="utf-8"))
    scanned_at = data.get('scanned_at') if isinstance(data,dict) else None
    if require_timestamp or scanned_at:
        if not isinstance(scanned_at,str):raise ValueError('INVENTORY_OBSERVATION_TIME_MISSING')
        stamp=datetime.fromisoformat(scanned_at)
        if stamp.tzinfo is None:raise ValueError('INVENTORY_OBSERVATION_TIME_INVALID')
        age=(datetime.now(timezone.utc)-stamp).total_seconds()
        if age < -300 or age > 48*3600:raise ValueError('INVENTORY_STALE_OR_FUTURE')
    if isinstance(data, dict):
        data = data.get("documents",data.get("objects", data.get("all_objects")))
    if not isinstance(data,list) or not data or any(not isinstance(x,dict) or not x.get('object_id') for x in data):
        raise ValueError('INVENTORY_EMPTY_OR_INVALID')
    return [dict(x) for x in data]


def pptx_items(inventory: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for item in inventory:
        name = _as_text(item.get("display_name") or item.get("filename"))
        if item.get("object_type", "file") == "file" and name.casefold().endswith(".pptx"):
            out.append(dict(item))
    return out


def load_checkpoint(path: Path) -> dict[str, Any]:
    data = load_json(path, {})
    return data if isinstance(data, dict) else {}


def _walk_for_flag(value: Any, wanted: tuple[str, ...]) -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    def walk(v: Any, path: str) -> None:
        if isinstance(v, dict):
            for k, child in v.items():
                lk = str(k).casefold()
                if any(token in lk for token in wanted):
                    found.append((path + "/" + str(k), child))
                walk(child, path + "/" + str(k))
        elif isinstance(v, list):
            for i, child in enumerate(v):
                walk(child, path + "/" + str(i))
    walk(value, "")
    return found


class EvidenceIndex:
    def __init__(self, reader_dir: Path = READER_DIR, registry_path: Path = DEFAULT_REGISTRY):
        self.reader_dir = reader_dir
        self.registry_path = registry_path
        self.reader: dict[str, dict[str, Any]] = {}
        self.semantic: dict[str, dict[str, dict[str, Any]]] = {"qwen": {}, "agy": {}, "gemini31pro": {}}
        self.semantic_by_hash: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
        self._load_reader()
        self._load_semantic()

    def _load_reader(self) -> None:
        registry = load_json(self.registry_path, {})
        batches = registry.get("batches", {}) if isinstance(registry, dict) else {}
        if not isinstance(batches, dict):
            return
        for batch, meta in batches.items():
            if not isinstance(meta, dict) or meta.get("canonical") is not True or meta.get("gate") != "PASS":
                continue
            ledger = load_json(Path(_as_text(meta.get("ledger_path"))), {})
            gate = load_json(Path(_as_text(meta.get("gate_path"))), {})
            if not isinstance(ledger, dict) or not isinstance(gate, dict) or gate.get("READER_FIDELITY_GATE") != "PASS":
                continue
            for entry in ledger.get("entries", []):
                if not isinstance(entry, dict) or _as_text(entry.get("artifact", {}).get("family")).upper() != "PPTX":
                    continue
                lineage = entry.get("source_lineage", {})
                sv = _as_text(lineage.get("source_version_id"))
                if sv:
                    self.reader[sv] = {"batch": batch, "entry": entry, "gate": gate}

    def _load_semantic(self) -> None:
        for kind in self.semantic:
            root = self.reader_dir / "semantic" / kind
            if not root.exists():
                continue
            for path in root.rglob("*.json"):
                data = load_json(path, {})
                if not isinstance(data, dict):
                    continue
                lineage = data.get("authoritative_lineage", {})
                sv = _as_text(lineage.get("source_version_id") or data.get("source_version_id"))
                sha = _as_text(lineage.get("raw_sha256") or data.get("raw_artifact_sha256"))
                if not sv:
                    continue
                data = dict(data)
                data["_path"] = str(path)
                self.semantic[kind][sv] = data
                if data.get("status") == "SUCCESS" and sha:
                    self.semantic_by_hash.setdefault(sha, []).append((kind, sv, data))

    def reader_evidence(self, source_version: str, raw_sha: str) -> tuple[dict[str, Any] | None, str]:
        found = self.reader.get(source_version)
        if not found:
            return None, "READER_REGISTRY_MISSING"
        entry = found["entry"]
        checks = entry.get("model_independent_evidence", {}).get("gate_checks", {})
        required = ("RAW_SOURCE_AVAILABLE", "SOURCE_VERSION_LINKED", "STRUCTURE_PARSED", "VISUAL_EVIDENCE_AVAILABLE", "HASH_LINEAGE", "FAILURE_EXPLICIT")
        if entry.get("status") != "COMPLETED" or any(checks.get(k) != "PASS" for k in required):
            return None, "READER_GATE_NOT_PASS"
        artifact = entry.get("artifact", {})
        entry_sha = _as_text(artifact.get("sha256") or entry.get("model_independent_evidence", {}).get("raw_artifact_sha256_actual"))
        if not entry_sha or entry_sha != raw_sha:
            return None, "READER_RAW_SHA_MISMATCH"
        path = resolve_evidence_path(_as_text(entry.get("result_path")), self.reader_dir)
        if not path.is_file():
            return None, "READER_RESULT_MISSING"
        result = load_json(path, None)
        if not isinstance(result, dict):
            return None, "READER_RESULT_INVALID"
        result_sv = _as_text(result.get("source_version") or result.get("source_version_id"))
        result_sha = _as_text(result.get("raw_artifact_sha256") or result.get("raw_source_sha256"))
        if result_sv != source_version or result_sha != raw_sha:
            return None, "READER_RESULT_LINEAGE_MISMATCH"
        result_hash = file_sha256(path)
        visual_flags = entry.get("model_independent_evidence", {}).get("fidelity_flags", {})
        visual_required = False
        direct_flags = _walk_for_flag(result, ("needs_visual_review", "visual_review_required"))
        visual_required = any(v is True for _, v in direct_flags)
        for key in ("connector_count", "chart_count"):
            try:
                visual_required = visual_required or int(visual_flags.get(key, 0) or 0) > 0
            except (TypeError, ValueError):
                return None, "READER_VISUAL_FLAG_INVALID"
        evidence = {
            "batch": found["batch"],
            "ledger_path": _as_text(found.get("gate", {}).get("LEDGER_PATH")) or _as_text(self.registry_path),
            "gate": "PASS",
            "entry_key": entry.get("entry_key", ""),
            "checks": checks,
            "reader_result_path": str(path),
            "reader_result_sha256": result_hash,
            "reader_result_raw_sha256": result_sha,
            "visual_required": visual_required,
            "fidelity_flags": visual_flags,
        }
        return evidence, "OK"

    def semantic_success(self, source_version: str, raw_sha: str) -> tuple[dict[str, Any] | None, str]:
        for kind in ("qwen", "agy"):
            data = self.semantic[kind].get(source_version)
            if data and data.get("status") == "SUCCESS":
                lineage = data.get("authoritative_lineage", {})
                if _as_text(lineage.get("raw_sha256")) == raw_sha and Path(data["_path"]).is_file():
                    return {"model": kind, "path": data["_path"], "sha256": file_sha256(data["_path"]), "lineage": lineage}, "OK"
        # Hash reuse is only valid for an exact byte-identical prior analysis.
        for kind, old_sv, data in self.semantic_by_hash.get(raw_sha, []):
            if kind not in ("qwen", "agy"):
                continue
            return {"model": kind, "path": data["_path"], "sha256": file_sha256(data["_path"]), "lineage": data.get("authoritative_lineage", {}), "alias_of_source_version": old_sv}, "CONTENT_HASH_REUSE"
        return None, "SEMANTIC_SUCCESS_MISSING"

    def visual_success(self, source_version: str, raw_sha: str) -> tuple[dict[str, Any] | None, str]:
        data = self.semantic["gemini31pro"].get(source_version)
        if not data or data.get("status") != "SUCCESS":
            # Visual reuse is not inferred from a text/flash result.  An
            # exact hash reuse is allowed only when the visual result itself
            # is an existing successful result.
            for _, old_sv, candidate in self.semantic_by_hash.get(raw_sha, []):
                if _ == "gemini31pro" and candidate.get("status") == "SUCCESS":
                    return {"path": candidate["_path"], "sha256": file_sha256(candidate["_path"]), "lineage": candidate.get("authoritative_lineage", {}), "alias_of_source_version": old_sv}, "CONTENT_HASH_REUSE"
            return None, "VISUAL_SUCCESS_MISSING"
        lineage = data.get("authoritative_lineage", {})
        if _as_text(lineage.get("raw_sha256")) != raw_sha or not Path(data["_path"]).is_file():
            return None, "VISUAL_RAW_SHA_MISMATCH"
        return {"path": data["_path"], "sha256": file_sha256(data["_path"]), "lineage": lineage}, "OK"


def _checkpoint_entry(checkpoint: Mapping[str, Any], object_id: str) -> dict[str, Any] | None:
    value = checkpoint.get("committed", {}).get(str(object_id)) if isinstance(checkpoint.get("committed", {}), dict) else None
    return dict(value) if isinstance(value, dict) else None


def acquisition_decision(item: Mapping[str, Any], checkpoint: Mapping[str, Any], ledger: PptxRetentionLedger) -> tuple[str, dict[str, Any] | None]:
    """Return UNCHANGED_EVICTED_ANALYZED only on a strict receipt match."""
    oid = _as_text(item.get("object_id")); sv = _source_version(item)
    receipt = ledger.get(oid, sv)
    if receipt and receipt.get("retention_status") == EVICTED_ANALYZED and identity_matches(item, receipt) and receipt.get("sha256"):
        return "UNCHANGED_EVICTED_ANALYZED", receipt
    if receipt and receipt.get("retention_status") == LOCAL_RAW and identity_matches(item, receipt) and receipt.get("sha256"):
        path = _as_text(receipt.get("original_artifact_path"))
        if path and os.path.isfile(path):
            return "UNCHANGED", {
                "version": sv.split("@", 1)[-1], "mtime": receipt.get("source_mtime", ""),
                "source_size": receipt.get("source_size", 0), "metadata_fingerprint": receipt.get("metadata_fingerprint", ""),
                "artifact_path": path, "artifact_size": receipt.get("original_size", 0), "sha256": receipt.get("sha256", ""),
            }
    cp = _checkpoint_entry(checkpoint, oid)
    if cp and cp.get("artifact_path") and os.path.isfile(cp.get("artifact_path", "")):
        # Legacy checkpoint compatibility is intentionally strict: no mtime,
        # size, or fingerprint means no local skip.
        cp_item = {
            "object_id": oid, "version": cp.get("version"), "mtime": cp.get("mtime"),
            "size": cp.get("source_size"), "metadata_fingerprint": cp.get("metadata_fingerprint"),
        }
        if identity_matches(item, cp_item):
            return "UNCHANGED", cp
    return "DOWNLOAD_REQUIRED", receipt


def _busy_source_ids() -> set[str]:
    busy: set[str] = set()
    proc = Path("/proc")
    if not proc.exists():
        return busy
    for p in proc.iterdir():
        if not p.name.isdigit():
            continue
        try:
            raw = (p / "cmdline").read_bytes().decode("utf-8", "ignore")
        except OSError:
            continue
        # IDs are only accepted when present in an analysis command's
        # authoritative lineage/attachment, avoiding arbitrary PID matches.
        if any(token in raw for token in ("semantic_supervisor.py", "qoderclicn", "unattended_guard.py")):
            import re
            busy.update(re.findall(r"(?<!\d)(\d{8,15})@\d+(?!\d)", raw))
            # qoderclicn command lines also have source_id=... in prompt.
            busy.update(re.findall(r"source_id=(\d{8,15})", raw))
    return busy


class PptxRetentionManager:
    def __init__(
        self,
        store_dir: Path = DEFAULT_STORE,
        reader_dir: Path = READER_DIR,
        registry_path: Path = DEFAULT_REGISTRY,
        live_state_db: Path | None = None,
        live_mirror_root: Path | None = None,
    ):
        self.store_dir = Path(store_dir)
        self.checkpoint_path = self.store_dir / "checkpoint.json"
        self.ledger = PptxRetentionLedger(self.store_dir / "pptx_retention.sqlite3")
        self.evidence = EvidenceIndex(Path(reader_dir), Path(registry_path))
        self.live_mirror_root = Path(live_mirror_root) if live_mirror_root else PRODUCTION_MIRROR_ROOT
        self.mirror_states: list[Any] = []
        self.mirror_state = None
        try:
            from wps_mirror_state import MirrorStateStore
            local_db = self.store_dir / "state.sqlite3"
            local_store = MirrorStateStore(local_db)
            self.mirror_states.append(local_store)
            self.mirror_state = local_store
            if live_state_db is not None:
                live_store = MirrorStateStore(live_state_db)
                if Path(live_store.db_path).resolve() != Path(local_store.db_path).resolve():
                    self.mirror_states.append(live_store)
            elif self.store_dir.resolve() == DEFAULT_STORE.resolve() and DEFAULT_LIVE_STATE_DB.is_file():
                if DEFAULT_LIVE_STATE_DB.resolve() != local_db.resolve():
                    self.mirror_states.append(MirrorStateStore(DEFAULT_LIVE_STATE_DB))
        except (ImportError, OSError):
            # The retention ledger remains authoritative for the manager;
            # state sync is a compatibility aid for older mirror consumers.
            self.mirror_state = None
            self.mirror_states = []

    def _sync_mirror_state(self, receipt: Mapping[str, Any], status: str, error_class: str = "") -> None:
        for store in self.mirror_states:
            self._sync_one_mirror_state(store, receipt, status, error_class)

    def _sync_one_mirror_state(self, store: Any, receipt: Mapping[str, Any], status: str, error_class: str = "") -> None:
        state_status = "COMMITTED_LOCAL" if status == LOCAL_RAW else status
        terminal = "ANALYZED_HASH_RECEIPT" if status in {EVICTED_ANALYZED, "REHYDRATE_REQUIRED"} else "COMMITTED_LOCAL"
        artifact_path = "" if status == EVICTED_ANALYZED else _as_text(receipt.get("original_artifact_path"))
        store.upsert({
            "object_id": _as_text(receipt.get("object_id")),
            "display_name": _as_text(receipt.get("display_name")),
            "file_type": "NATIVE_PPTX", "backend": "NATIVE_PPTX_DIRECT",
            "source_version": _as_text(receipt.get("source_version")).split("@", 1)[-1],
            "source_mtime": _as_text(receipt.get("source_mtime")),
            "source_size": int(receipt.get("source_size") or 0),
            "artifact_path": artifact_path,
            "artifact_size": int(receipt.get("original_size") or 0),
            "sha256": _as_text(receipt.get("sha256")),
            "status": state_status,
            "error_class": error_class,
            "terminal_routing_state": terminal,
            "last_success_at": now_iso() if status != "REHYDRATE_REQUIRED" else "",
        })

    def sync_evicted_receipts_to_live_state(self, inventory: Iterable[Mapping[str, Any]] | None = None) -> dict[str, Any]:
        """Align production incremental rows with durable EVICTED_ANALYZED receipts.

        Receipt identity remains authoritative. A drifted live version is left
        untouched and reported; it is never forced cold.
        """
        by_oid = {}
        for item in pptx_items(inventory or []):
            by_oid[_as_text(item.get("object_id"))] = item
        out = {"synced": 0, "skipped_identity": 0, "skipped_no_store": 0, "object_ids": []}
        if not self.mirror_states:
            out["skipped_no_store"] = 1
            return out
        for receipt in self.ledger.all():
            if receipt.get("retention_status") != EVICTED_ANALYZED or not receipt.get("sha256"):
                continue
            item = by_oid.get(_as_text(receipt.get("object_id")))
            if item is not None and not identity_matches(item, receipt):
                out["skipped_identity"] += 1
                continue
            self._sync_mirror_state(receipt, EVICTED_ANALYZED)
            out["synced"] += 1
            out["object_ids"].append(_as_text(receipt.get("object_id")))
        return out

    def verified_source_recovery(self, receipt: Mapping[str, Any], deleting_path: Path) -> tuple[bool, str]:
        """Recheck independent exact-version cold bytes, never trust a label.

        No automatic cold-copy creation or old-source re-download occurs.
        A remote provider without an implemented verifier fails closed.
        """
        proof = receipt.get("source_recovery_evidence", {})
        if isinstance(proof, str):
            try:
                proof = json.loads(proof)
            except (ValueError, TypeError):
                return False, "SOURCE_RECOVERY_PROOF_INVALID"
        if not isinstance(proof, dict) or proof.get("kind") != "COLD_BLOB_LOCAL":
            return False, "EXACT_VERSION_RECOVERY_NOT_PROVEN"
        if (proof.get("source_version") != receipt.get("source_version") or
                proof.get("sha256") != receipt.get("sha256") or not receipt.get("sha256")):
            return False, "SOURCE_RECOVERY_IDENTITY_MISMATCH"
        cold = Path(_as_text(proof.get("path")))
        if not cold.is_absolute() or cold.is_symlink() or not cold.is_file():
            return False, "COLD_SOURCE_NOT_AVAILABLE"
        try:
            if cold.resolve() == deleting_path.resolve() or os.path.samefile(cold, deleting_path):
                return False, "COLD_SOURCE_NOT_INDEPENDENT"
            if file_sha256(cold) != receipt["sha256"]:
                return False, "COLD_SOURCE_HASH_MISMATCH"
        except OSError:
            return False, "COLD_SOURCE_UNREADABLE"
        return True, "VERIFIED_EXACT_VERSION_COLD_SOURCE"

    def evaluate(self, item: Mapping[str, Any], checkpoint: Mapping[str, Any], busy_ids: set[str] | None = None) -> Evaluation:
        item = dict(item); oid = _as_text(item.get("object_id")); sv = _source_version(item)
        cp = _checkpoint_entry(checkpoint, oid) or {}
        existing = self.ledger.get(oid, sv)
        # An evicted source has no checkpoint path requirement.  The durable
        # receipt supplies the immutable hash/evidence for reporting; the
        # source is still considered cold, not local.
        if (not cp or not cp.get("sha256")) and existing and existing.get("retention_status") == EVICTED_ANALYZED and identity_matches(item, existing):
            cp = {
                "artifact_path": existing.get("original_artifact_path", ""),
                "artifact_size": existing.get("original_size", 0),
                "sha256": existing.get("sha256", ""),
            }
        raw_path = _as_text(cp.get("artifact_path"))
        raw_sha = _as_text(cp.get("sha256"))
        raw_size = int(cp.get("artifact_size") or 0)
        busy = oid in (busy_ids or set())
        reader, reader_reason = self.evidence.reader_evidence(sv, raw_sha)
        if reader:
            raw_size = int(cp.get("artifact_size") or item.get("size") or 0)
        semantic, semantic_reason = self.evidence.semantic_success(sv, raw_sha) if reader else (None, "SEMANTIC_NOT_EVALUATED")
        visual_required = bool(reader and reader.get("visual_required"))
        visual = None; visual_reason = "NOT_REQUIRED"
        if visual_required:
            visual, visual_reason = self.evidence.visual_success(sv, raw_sha)
        if busy:
            status, reason = "BLOCKED_BUSY", "BUSY_IN_FLIGHT"
        elif not cp or not raw_sha or not raw_path:
            status, reason = "BLOCKED_SEMANTIC", "CHECKPOINT_RAW_RECEIPT_MISSING"
        elif not reader:
            status, reason = "BLOCKED_SEMANTIC", reader_reason
        elif raw_path and os.path.exists(raw_path):
            # Verify actual local bytes before allowing unlink.  This is the
            # final local-source integrity check, not a WPS mutation.
            try:
                if file_sha256(raw_path) != raw_sha:
                    status, reason = "BLOCKED_SEMANTIC", "LOCAL_RAW_SHA_MISMATCH"
                elif not semantic:
                    status, reason = "BLOCKED_SEMANTIC", semantic_reason
                elif visual_required and not visual:
                    status, reason = "BLOCKED_VISUAL", visual_reason
                else:
                    status, reason = "ELIGIBLE", "ALL_FAIL_CLOSED_GATES_PASS"
            except OSError:
                status, reason = "BLOCKED_SEMANTIC", "LOCAL_RAW_UNREADABLE"
        else:
            # A missing raw is legal only with an already persisted receipt;
            # this path is mainly for reporting an existing eviction.
            if existing and existing.get("retention_status") == EVICTED_ANALYZED and identity_matches(item, existing):
                return Evaluation(item, existing, "EVICTED", "DURABLE_EVICTION_RECEIPT", 0, (semantic or {}).get("model", ""), (visual or {}).get("path", ""))
            status, reason = "BLOCKED_SEMANTIC", "RAW_MISSING_WITHOUT_RECEIPT"
        receipt = self._build_receipt(item, cp, reader, semantic, visual, visual_required, status)
        if status == "ELIGIBLE":
            recoverable, recovery_reason = self.verified_source_recovery(receipt, Path(raw_path))
            if not recoverable:
                status, reason = "BLOCKED_RECOVERY", recovery_reason
        # Observation is auditable, but never upgrades a gate failure.
        if receipt.get("object_id") and receipt.get("source_version"):
            self.ledger.upsert(receipt, event_type="SCAN_EVALUATION")
        return Evaluation(item, self.ledger.get(oid, sv) or receipt, status, reason, raw_size, (semantic or {}).get("model", ""), (visual or {}).get("path", ""), busy)

    def _build_receipt(self, item: Mapping[str, Any], cp: Mapping[str, Any], reader: Mapping[str, Any] | None, semantic: Mapping[str, Any] | None, visual: Mapping[str, Any] | None, visual_required: bool, status: str) -> dict[str, Any]:
        oid = _as_text(item.get("object_id")); sv = _source_version(item)
        old = self.ledger.get(oid, sv) or {}
        visual_state = "success" if visual else ("not-required" if not visual_required else "pending")
        retention = EVICTED_ANALYZED if status == "EVICTED" else (old.get("retention_status") if old.get("retention_status") == EVICTED_ANALYZED else LOCAL_RAW)
        def json_field(current: Mapping[str, Any] | None, previous: Any) -> str:
            if current is not None:
                return canonical_json(current)
            return previous if isinstance(previous, str) else canonical_json(previous or {})
        return {
            "object_id": oid, "source_version": sv, "source_mtime": _as_text(item.get("mtime")),
            "source_size": _source_size(item), "metadata_fingerprint": _as_text(item.get("metadata_fingerprint")),
            "display_name": _as_text(item.get("display_name") or item.get("filename")),
            "canonical_inventory_path": _as_text(item.get("inventory_path") or item.get("canonical_inventory_path") or item.get("path")),
            "original_artifact_path": _as_text(cp.get("artifact_path") or old.get("original_artifact_path")),
            "original_size": int(cp.get("artifact_size") or old.get("original_size") or _source_size(item) or 0),
            "sha256": _as_text(cp.get("sha256") or old.get("sha256")),
            "reader_result_path": _as_text(reader.get("reader_result_path")) if reader else old.get("reader_result_path", ""),
            "reader_result_sha256": _as_text(reader.get("reader_result_sha256")) if reader else old.get("reader_result_sha256", ""),
            "canonical_reader_gate_evidence": json_field(reader, old.get("canonical_reader_gate_evidence", {})),
            "semantic_success_evidence": json_field(semantic, old.get("semantic_success_evidence", {})),
            "visual_requirement": "required" if visual_required else "not-required",
            "visual_state": visual_state, "retention_status": retention,
            "evicted_at": old.get("evicted_at", ""), "rehydrate_count": int(old.get("rehydrate_count") or 0),
            "alias_of_source_version": _as_text((semantic or {}).get("alias_of_source_version") or old.get("alias_of_source_version")),
            "source_content_drift": old.get("source_content_drift", ""),
            "source_recovery_evidence": old.get("source_recovery_evidence", "{}"),
        }

    def scan(self, inventory: list[Mapping[str, Any]], checkpoint: Mapping[str, Any], busy_ids: set[str] | None = None) -> dict[str, Any]:
        evaluations = [self.evaluate(item, checkpoint, busy_ids) for item in pptx_items(inventory)]
        counts = {k: 0 for k in ("LOCAL_RAW", "ELIGIBLE", "BLOCKED_SEMANTIC", "BLOCKED_VISUAL", "EVICTED", "BLOCKED_BUSY", "BLOCKED_RECOVERY")}
        reasons: dict[str, int] = {}
        eligible_bytes = 0
        incremental_skips = 0
        content_hash_reuse = 0
        for ev in evaluations:
            decision, _ = acquisition_decision(ev.item, checkpoint, self.ledger)
            if decision in {"UNCHANGED", "UNCHANGED_EVICTED_ANALYZED"}:
                incremental_skips += 1
            if ev.receipt.get("alias_of_source_version"):
                content_hash_reuse += 1
            if ev.status == "ELIGIBLE": counts["ELIGIBLE"] += 1; eligible_bytes += ev.raw_bytes
            elif ev.status == "EVICTED": counts["EVICTED"] += 1
            elif ev.status == "BLOCKED_VISUAL": counts["BLOCKED_VISUAL"] += 1
            elif ev.status == "BLOCKED_BUSY": counts["BLOCKED_BUSY"] += 1
            elif ev.status == "BLOCKED_RECOVERY": counts["BLOCKED_RECOVERY"] += 1
            else: counts["BLOCKED_SEMANTIC"] += 1
            if ev.status not in {"ELIGIBLE", "EVICTED"}:
                reasons[ev.reason] = reasons.get(ev.reason, 0) + 1
        counts["LOCAL_RAW"] = sum(1 for ev in evaluations if os.path.isfile(_as_text(ev.receipt.get("original_artifact_path"))) and ev.status != "EVICTED")
        evicted_rows = [row for row in self.ledger.all() if row.get("retention_status") == EVICTED_ANALYZED]
        return {
            "generated_at": now_iso(), "pptx_total": len(evaluations), "counts": counts,
            "eligible_bytes": eligible_bytes, "evicted_bytes": sum(int(row.get("original_size") or 0) for row in evicted_rows),
            "incremental_skip_same_source_version": incremental_skips, "content_hash_reuse": content_hash_reuse,
            "reasons": dict(sorted(reasons.items())), "evaluations": evaluations,
        }

    def evict(self, evaluations: Iterable[Evaluation], eligible_only: bool = True) -> dict[str, Any]:
        out = {"evicted": 0, "freed_bytes": 0, "skipped": 0, "errors": []}
        for ev in evaluations:
            if eligible_only and not ev.eligible:
                out["skipped"] += 1; continue
            if not ev.eligible:
                out["skipped"] += 1; continue
            path = Path(_as_text(ev.receipt.get("original_artifact_path")))
            if not path.is_file() or path.suffix.casefold() != ".pptx":
                out["skipped"] += 1; out["errors"].append({"object_id": ev.receipt.get("object_id"), "reason": "RAW_PATH_NOT_PPTX_OR_MISSING"}); continue
            # Re-evaluate immediately before deletion to avoid deleting a
            # file that changed or became busy after scan.
            fresh = self.evaluate(ev.item, load_checkpoint(self.checkpoint_path), _busy_source_ids())
            if not fresh.eligible:
                out["skipped"] += 1; out["errors"].append({"object_id": ev.receipt.get("object_id"), "reason": "PRE_UNLINK_RECHECK_" + fresh.reason}); continue
            try:
                actual_size = path.stat().st_size
                receipt = dict(fresh.receipt)
                receipt["original_artifact_path"] = str(path)
                receipt["original_size"] = actual_size
                receipt["sha256"] = file_sha256(path)
                if receipt["sha256"] != fresh.receipt.get("sha256"):
                    raise RuntimeError("LOCAL_RAW_SHA_CHANGED_BEFORE_RECEIPT")
                recoverable, reason = self.verified_source_recovery(receipt, path)
                if not recoverable:
                    raise RuntimeError(reason)
                receipt["evicted_at"] = now_iso()
                # Durable complete receipt first.
                self.ledger.commit_eviction_receipt(receipt)
                durable = self.ledger.get(receipt["object_id"], receipt["source_version"])
                if not durable or durable.get("retention_status") != "EVICTION_RECEIPT_COMMITTED" or durable.get("sha256") != receipt["sha256"]:
                    raise RuntimeError("EVICTION_RECEIPT_DURABILITY_RECHECK_FAILED")
                path.unlink()
                if path.exists() or self.ledger.get(receipt["object_id"], receipt["source_version"]).get("retention_status") == LOCAL_RAW:
                    raise RuntimeError("POST_UNLINK_RECHECK_FAILED")
                self.ledger.mark_evicted(receipt["object_id"], receipt["source_version"])
                self._sync_mirror_state(self.ledger.get(receipt["object_id"], receipt["source_version"]) or receipt, EVICTED_ANALYZED)
                out["evicted"] += 1; out["freed_bytes"] += actual_size
            except Exception as exc:
                out["errors"].append({"object_id": ev.receipt.get("object_id"), "reason": f"{type(exc).__name__}:{exc}"})
        return out

    def rematerialize_missing_reader_results(self, evaluations: Iterable[Evaluation]) -> dict[str, Any]:
        """Repair READER_RESULT_MISSING from intact local raw PPTX. No semantic writes."""
        if str(READER_FIDELITY_ROOT) not in sys.path:
            sys.path.insert(0, str(READER_FIDELITY_ROOT))
        out = {"attempted": 0, "repaired": 0, "skipped": 0, "errors": []}
        try:
            from paw_reader_fidelity.contract import ReaderInput
            from paw_reader_fidelity.dispatch import read_artifact
        except ImportError as exc:
            return {"attempted": 0, "repaired": 0, "skipped": 0, "errors": [{"reason": f"READER_IMPORT:{exc}"}]}
        for ev in evaluations:
            if ev.reason != "READER_RESULT_MISSING":
                continue
            raw_path = Path(_as_text(ev.receipt.get("original_artifact_path")))
            if not raw_path.is_file() or raw_path.suffix.casefold() != ".pptx":
                out["skipped"] += 1
                continue
            sv = _source_version(ev.item)
            found = self.evidence.reader.get(sv)
            if not found:
                out["skipped"] += 1
                continue
            declared = Path(_as_text(found["entry"].get("result_path")))
            target = declared if declared.is_absolute() else (self.evidence.reader_dir / declared)
            if target.is_file():
                out["skipped"] += 1
                continue
            expected_sha = _as_text(ev.receipt.get("sha256"))
            try:
                actual = file_sha256(raw_path)
            except OSError as exc:
                out["errors"].append({"object_id": ev.item.get("object_id"), "reason": f"RAW_UNREADABLE:{exc}"})
                continue
            if expected_sha and actual != expected_sha:
                out["errors"].append({"object_id": ev.item.get("object_id"), "reason": "LOCAL_RAW_SHA_MISMATCH"})
                continue
            out["attempted"] += 1
            try:
                result = read_artifact(
                    ReaderInput(
                        path=str(raw_path),
                        source_object_id=_as_text(ev.item.get("object_id")),
                        source_version=sv,
                        display_name=_as_text(ev.item.get("display_name") or ev.item.get("filename")),
                        family="PPTX",
                        committed=True,
                        raw_artifact_sha256=actual,
                    ),
                    target.parent,
                )
                if not target.is_file():
                    raise RuntimeError("READER_RESULT_NOT_WRITTEN")
                result_sha = _as_text(getattr(result, "raw_artifact_sha256", "") or "")
                if result_sha and result_sha != actual:
                    raise RuntimeError("READER_RAW_HASH_CONFLICT")
                self.ledger.append_event(_as_text(ev.item.get("object_id")), sv, "READER_RESULT_REMATERIALIZED", {
                    "result_path": str(target), "raw_sha256": actual,
                })
                out["repaired"] += 1
            except Exception as exc:
                out["errors"].append({"object_id": ev.item.get("object_id"), "reason": f"{type(exc).__name__}:{exc}"})
        if out["repaired"]:
            self.evidence = EvidenceIndex(self.evidence.reader_dir, self.evidence.registry_path)
        return out

    def evict_duplicate_redownloads(self, inventory: Iterable[Mapping[str, Any]], live_root: Path | None = None) -> dict[str, Any]:
        """Remove same-version live PPTX copies that match an EVICTED_ANALYZED receipt."""
        live_root = Path(live_root) if live_root else self.live_mirror_root
        by_oid = {_as_text(item.get("object_id")): item for item in pptx_items(inventory)}
        out = {
            "found": 0, "removed": 0, "freed_bytes": 0, "drift": 0, "skipped": 0,
            "removed_objects": [], "drift_objects": [], "errors": [],
        }
        for receipt in self.ledger.all():
            if receipt.get("retention_status") != EVICTED_ANALYZED or not receipt.get("sha256"):
                continue
            oid = _as_text(receipt.get("object_id"))
            sv = _as_text(receipt.get("source_version"))
            ver = sv.split("@", 1)[-1] if "@" in sv else sv
            item = by_oid.get(oid)
            if item is not None and not identity_matches(item, receipt):
                out["skipped"] += 1
                continue
            obj_dir = live_root / "versions" / oid
            live_files = [p for p in obj_dir.rglob("*.pptx")] if obj_dir.is_dir() else []
            live_files = [p for p in live_files if p.is_file()]
            if not live_files:
                continue
            for path in live_files:
                out["found"] += 1
                try:
                    observed = file_sha256(path)
                except OSError as exc:
                    out["errors"].append({"object_id": oid, "path": str(path), "reason": f"UNREADABLE:{exc}"})
                    continue
                version_ok = path.parent.name == ver
                if observed != receipt.get("sha256") or not version_ok:
                    self.ledger.append_event(oid, sv, "DUPLICATE_REDOWNLOAD_DRIFT", {
                        "path": str(path), "observed_sha256": observed,
                        "receipt_sha256": receipt.get("sha256"), "version_dir": path.parent.name,
                        "expected_version": ver,
                    })
                    out["drift"] += 1
                    out["drift_objects"].append({
                        "object_id": oid, "source_version": sv, "path": str(path),
                        "observed_sha256": observed, "receipt_sha256": receipt.get("sha256"),
                        "version_dir_match": version_ok,
                    })
                    continue
                recoverable, recovery_reason = self.verified_source_recovery(receipt, path)
                if not recoverable:
                    out["skipped"] += 1
                    out["errors"].append({"object_id": oid, "path": str(path), "reason": recovery_reason})
                    continue
                try:
                    size = path.stat().st_size
                    path.unlink()
                    if path.exists():
                        raise RuntimeError("POST_UNLINK_RECHECK_FAILED")
                    current = live_root / "current" / oid / path.name
                    # Only remove an alias to the exact file just unlinked.
                    # Never unlink an independent or different current copy.
                    if current.is_symlink() and current.resolve() == path.resolve():
                        try:
                            current.unlink()
                        except OSError:
                            pass
                    self.ledger.append_event(oid, sv, "DUPLICATE_REDOWNLOAD_EVICTED", {
                        "path": str(path), "sha256": observed, "bytes": size,
                    })
                    self._sync_mirror_state(receipt, EVICTED_ANALYZED)
                    out["removed"] += 1
                    out["freed_bytes"] += size
                    out["removed_objects"].append({
                        "object_id": oid, "source_version": sv, "path": str(path),
                        "sha256": observed, "bytes": size,
                    })
                except Exception as exc:
                    out["errors"].append({"object_id": oid, "path": str(path), "reason": f"{type(exc).__name__}:{exc}"})
        return out

    def rehydrate(self, item: Mapping[str, Any], downloader: Callable[[Mapping[str, Any], Path], Path] | None = None) -> dict[str, Any]:
        oid = _as_text(item.get("object_id")); sv = _source_version(item); receipt = self.ledger.get(oid, sv)
        if not receipt or receipt.get("retention_status") not in {EVICTED_ANALYZED, REHYDRATE_REQUIRED}:
            return {"status": "FAIL_CLOSED", "reason": "EVICTED_RECEIPT_REQUIRED", "object_id": oid, "source_version": sv}
        target = Path(_as_text(receipt.get("original_artifact_path")))
        if target.suffix.casefold() != ".pptx" or not target.parent:
            return {"status": "FAIL_CLOSED", "reason": "EXPECTED_PPTX_PATH_INVALID"}
        if target.is_file():
            observed = file_sha256(target)
            if observed == receipt.get("sha256"):
                self.ledger.upsert({**receipt, "retention_status": LOCAL_RAW}, event_type="REHYDRATE_ALREADY_PRESENT")
                return {"status": "RESTORED", "path": str(target), "sha256": observed, "rehydrate_count": receipt.get("rehydrate_count", 0)}
            self.ledger.mark_drift(oid, sv, observed)
            return {"status": "SOURCE_CONTENT_DRIFT", "reason": "EXPECTED_PATH_HASH_MISMATCH", "observed_sha256": observed}
        if downloader is None:
            return {"status": "FAIL_CLOSED", "reason": "DOWNLOAD_CALLBACK_REQUIRED_FOR_LIBRARY_REHYDRATE"}
        with tempfile.TemporaryDirectory(prefix=f"paw-pptx-rehydrate-{oid}-", dir=str(self.store_dir)) as td:
            temp_target = Path(td) / target.name
            produced = Path(downloader(item, temp_target))
            if not produced.is_file():
                return {"status": "FAIL_CLOSED", "reason": "DOWNLOAD_ARTIFACT_MISSING"}
            observed = file_sha256(produced)
            if observed != receipt.get("sha256"):
                self.ledger.mark_drift(oid, sv, observed)
                self._sync_mirror_state(self.ledger.get(oid, sv) or receipt, REHYDRATE_REQUIRED, "SOURCE_CONTENT_DRIFT")
                return {"status": "SOURCE_CONTENT_DRIFT", "reason": "REMOTE_HASH_MISMATCH", "observed_sha256": observed, "expected_sha256": receipt.get("sha256")}
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(produced, target)
            if file_sha256(target) != receipt.get("sha256"):
                self.ledger.mark_drift(oid, sv, file_sha256(target))
                self._sync_mirror_state(self.ledger.get(oid, sv) or receipt, REHYDRATE_REQUIRED, "SOURCE_CONTENT_DRIFT")
                return {"status": "SOURCE_CONTENT_DRIFT", "reason": "POST_RESTORE_HASH_MISMATCH"}
            self.ledger.upsert({**receipt, "retention_status": LOCAL_RAW, "rehydrate_count": int(receipt.get("rehydrate_count") or 0) + 1}, event_type="REHYDRATE_HASH_VERIFIED")
            self._sync_mirror_state(self.ledger.get(oid, sv) or receipt, LOCAL_RAW)
            return {"status": "RESTORED", "path": str(target), "sha256": observed}


def _render_report(summary: Mapping[str, Any]) -> str:
    c = summary.get("counts", {})
    lines = ["# PAW PPTX retention report", "", f"Generated: {summary.get('generated_at','')}", "", "| Status | Count |", "|---|---:|"]
    for k in ("LOCAL_RAW", "ELIGIBLE", "BLOCKED_SEMANTIC", "BLOCKED_VISUAL", "BLOCKED_BUSY", "BLOCKED_RECOVERY", "EVICTED"):
        lines.append(f"| {k} | {c.get(k, 0)} |")
    lines += ["", f"Eligible bytes: {summary.get('eligible_bytes', 0)}", f"Evicted bytes: {summary.get('evicted_bytes', 0)}", "", "## Blocking reasons", ""]
    for reason, count in summary.get("reasons", {}).items(): lines.append(f"- `{reason}`: {count}")
    return "\n".join(lines) + "\n"


def _default_downloader(item: Mapping[str, Any], target: Path) -> Path:
    run_dir = target.parent / "worker"
    run_dir.mkdir(parents=True, exist_ok=True)
    result = run_dir / "worker_result.json"
    cmd = [sys.executable, str(WORKER_DIR / "cdp_process_worker.py"), "--item-json", json.dumps(dict(item), ensure_ascii=False), "--staging-dir", str(run_dir), "--result-json", str(result)]
    completed = subprocess.run(cmd, text=True, capture_output=True, timeout=240, check=False)
    data = load_json(result, {})
    if completed.returncode != 0 or data.get("status") != "SUCCESS":
        raise RuntimeError(data.get("error_class") or f"WORKER_EXIT_{completed.returncode}")
    produced = Path(_as_text(data.get("artifact_path")))
    return produced


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PAW PPTX analyze-after-analysis retention manager")
    parser.add_argument("command", choices=("scan", "evict", "rehydrate", "report", "sweep"))
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_STORE / "checkpoint.json")
    parser.add_argument("--store-dir", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--reader-dir", type=Path, default=READER_DIR)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--markdown-path", type=Path, default=DEFAULT_REPORT_MD)
    parser.add_argument("--eligible-only", action="store_true", help="evict only when all fail-closed gates pass")
    parser.add_argument("--object-id")
    parser.add_argument("--source-version")
    parser.add_argument("--live-state-db", type=Path, default=None)
    parser.add_argument("--live-mirror-root", type=Path, default=None)
    args = parser.parse_args(argv)
    # Validate before creating/updating a retention ledger or syncing receipts.
    inventory = load_inventory(args.inventory, require_timestamp=True)
    manager = PptxRetentionManager(
        args.store_dir, args.reader_dir, args.registry,
        live_state_db=args.live_state_db, live_mirror_root=args.live_mirror_root,
    )
    checkpoint = load_checkpoint(args.checkpoint)
    if args.command in {"scan", "evict", "report", "sweep"}:
        summary = manager.scan(inventory, checkpoint, _busy_source_ids())
        if args.command in {"evict", "sweep"}:
            if args.command == "sweep":
                # Retention observes already-produced Reader evidence; it is
                # not an ingestion job or a historical-source rebuild trigger.
                summary["reader_repair"] = {"enabled":False,"attempted":0,"repaired":0,
                    "reason":"READER_REPAIR_REQUIRES_SEPARATE_EXPLICIT_SCOPE"}
            result = manager.evict(summary["evaluations"], eligible_only=True if args.command == "sweep" else args.eligible_only)
            duplicates = manager.evict_duplicate_redownloads(inventory)
            live_sync = manager.sync_evicted_receipts_to_live_state(inventory)
            summary["eviction"] = result
            summary["duplicate_redownload"] = duplicates
            summary["live_state_sync"] = live_sync
            summary["post_eviction"] = manager.scan(inventory, load_checkpoint(args.checkpoint), _busy_source_ids())["counts"]
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(json.dumps(_jsonable(summary), ensure_ascii=False, indent=2), encoding="utf-8")
        args.markdown_path.write_text(_render_report(_jsonable(summary)), encoding="utf-8")
        print(json.dumps(_summary_for_stdout(summary), ensure_ascii=False, indent=2))
        return 0
    if not args.object_id or not args.source_version:
        parser.error("rehydrate requires --object-id and --source-version")
    item = next((x for x in pptx_items(inventory) if _as_text(x.get("object_id")) == args.object_id and _source_version(x) == args.source_version), None)
    if not item:
        print(json.dumps({"status": "FAIL_CLOSED", "reason": "INVENTORY_SOURCE_VERSION_NOT_FOUND"}, ensure_ascii=False))
        return 2
    try:
        result = manager.rehydrate(item, _default_downloader)
    except Exception as exc:
        result = {"status": "FAIL_CLOSED", "reason": f"REHYDRATE_EXCEPTION:{type(exc).__name__}:{exc}"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "RESTORED" else 2


def _jsonable(summary: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(summary)
    evs = []
    for ev in out.get("evaluations", []):
        if isinstance(ev, Evaluation):
            evs.append({"object_id": ev.item.get("object_id"), "source_version": _source_version(ev.item), "display_name": ev.item.get("display_name"), "status": ev.status, "reason": ev.reason, "raw_bytes": ev.raw_bytes, "semantic_model": ev.semantic_model, "visual_path": ev.visual_path, "busy": ev.busy})
        else: evs.append(ev)
    out["evaluations"] = evs
    return out


def _summary_for_stdout(summary: Mapping[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in summary.items() if k != "evaluations"}
    if "eviction" in summary: out["eviction"] = summary["eviction"]
    return out


if __name__ == "__main__":
    raise SystemExit(main())
