#!/usr/bin/env python3
"""R15-B1 local mirror state and immutable artifact primitives.

The store is intentionally caller-selected so tests use a temporary SQLite
database and mirror root.  No production path is opened by importing this
module.
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


MIRROR_STATUSES = frozenset({
    "DISCOVERED", "EXCLUDED", "NEEDS_CLASSIFICATION", "BLOCKED_BACKEND",
    "ELIGIBLE", "UNCHANGED", "ACQUIRING", "ACQUIRED", "VALIDATED",
    "COMMITTED_LOCAL", "FAILED", "FAILED_RETRYABLE", "EXPLICIT_HOLD", "EXCLUDED_INVENTORY_ONLY",
    "BLOCKED_WITH_EXACT_REASON", "EVICTED_ANALYZED", "REHYDRATE_REQUIRED",
})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _component(value: Any) -> str:
    raw = str(value or "")
    if re.fullmatch(r"[A-Za-z0-9._-]+", raw):
        return raw
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MirrorState:
    object_id: str
    file_type: str = ""
    backend: str = ""
    source_version: str = ""
    source_mtime: str = ""
    source_size: int = 0
    display_name: str = ""
    parent_id: str = ""
    artifact_path: str = ""
    artifact_size: int = 0
    sha256: str = ""
    status: str = "DISCOVERED"
    last_attempt_at: str = ""
    last_success_at: str = ""
    error_class: str = ""
    # R15-B1.5 routing/audit fields.  ``status`` remains the operational
    # lifecycle for compatibility; this field is the terminal routing state.
    semantic_classification: str = "UNKNOWN"
    acquisition_route: str = ""
    routing_evidence: str = ""
    link_id: str = ""
    terminal_routing_state: str = ""
    # Single-file isolation: bounded cross-run retry accounting.
    attempt_count: int = 0
    run_id: str = ""
    next_retry_at: str = ""


class MirrorStateStore:
    """SQLite state store with one current row per stable object_id."""

    def __init__(self, db_path: str | os.PathLike[str]):
        self.db_path = str(db_path)
        parent = os.path.dirname(os.path.abspath(self.db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS mirror_state (
                object_id TEXT PRIMARY KEY,
                file_type TEXT NOT NULL DEFAULT '',
                backend TEXT NOT NULL DEFAULT '',
                source_version TEXT NOT NULL DEFAULT '',
                source_mtime TEXT NOT NULL DEFAULT '',
                source_size INTEGER NOT NULL DEFAULT 0,
                display_name TEXT NOT NULL DEFAULT '',
                parent_id TEXT NOT NULL DEFAULT '',
                artifact_path TEXT NOT NULL DEFAULT '',
                artifact_size INTEGER NOT NULL DEFAULT 0,
                sha256 TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                last_attempt_at TEXT NOT NULL DEFAULT '',
                last_success_at TEXT NOT NULL DEFAULT '',
                error_class TEXT NOT NULL DEFAULT '',
                semantic_classification TEXT NOT NULL DEFAULT 'UNKNOWN',
                acquisition_route TEXT NOT NULL DEFAULT '',
                routing_evidence TEXT NOT NULL DEFAULT '',
                link_id TEXT NOT NULL DEFAULT '',
                terminal_routing_state TEXT NOT NULL DEFAULT '',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                run_id TEXT NOT NULL DEFAULT '',
                next_retry_at TEXT NOT NULL DEFAULT ''
            )""")
            for column, definition in (
                ("semantic_classification", "TEXT NOT NULL DEFAULT 'UNKNOWN'"),
                ("acquisition_route", "TEXT NOT NULL DEFAULT ''"),
                ("routing_evidence", "TEXT NOT NULL DEFAULT ''"),
                ("link_id", "TEXT NOT NULL DEFAULT ''"),
                ("terminal_routing_state", "TEXT NOT NULL DEFAULT ''"),
                ("attempt_count", "INTEGER NOT NULL DEFAULT 0"),
                ("run_id", "TEXT NOT NULL DEFAULT ''"),
                ("next_retry_at", "TEXT NOT NULL DEFAULT ''"),
            ):
                try:
                    conn.execute(f"ALTER TABLE mirror_state ADD COLUMN {column} {definition}")
                except sqlite3.OperationalError:
                    pass
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mirror_status ON mirror_state(status)")

    def get(self, object_id: str) -> MirrorState | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM mirror_state WHERE object_id=?", (str(object_id),)).fetchone()
        return MirrorState(**dict(row)) if row else None

    def upsert(self, state: MirrorState | Mapping[str, Any]) -> MirrorState:
        values = asdict(state) if isinstance(state, MirrorState) else dict(state)
        values.setdefault("object_id", "")
        values.setdefault("status", "DISCOVERED")
        values.setdefault("terminal_routing_state", "")
        if not values["terminal_routing_state"]:
            values["terminal_routing_state"] = {
                "EXCLUDED": "EXCLUDED_INVENTORY_ONLY",
                "EXCLUDED_INVENTORY_ONLY": "EXCLUDED_INVENTORY_ONLY",
                "BLOCKED_BACKEND": "BLOCKED_WITH_EXACT_REASON",
                "BLOCKED_WITH_EXACT_REASON": "BLOCKED_WITH_EXACT_REASON",
                "FAILED": "BLOCKED_WITH_EXACT_REASON",
                "FAILED_RETRYABLE": "BLOCKED_WITH_EXACT_REASON",
                "EXPLICIT_HOLD": "BLOCKED_WITH_EXACT_REASON",
                "COMMITTED_LOCAL": "COMMITTED_LOCAL",
                "UNCHANGED": "COMMITTED_LOCAL",
                "EVICTED_ANALYZED": "ANALYZED_HASH_RECEIPT",
                "REHYDRATE_REQUIRED": "ANALYZED_HASH_RECEIPT",
            }.get(values["status"], "")
        if values["status"] not in MIRROR_STATUSES:
            raise ValueError("unknown mirror status")
        fields = [field.name for field in MirrorState.__dataclass_fields__.values()]
        values = {field: values.get(field, "" if field not in {"source_size", "artifact_size", "attempt_count"} else 0) for field in fields}
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO mirror_state (" + ",".join(fields) + ") VALUES (" + ",".join("?" for _ in fields) + ") "
                "ON CONFLICT(object_id) DO UPDATE SET " + ",".join(f"{field}=excluded.{field}" for field in fields if field != "object_id"),
                tuple(values[field] for field in fields),
            )
        return MirrorState(**values)

    def update_status(self, object_id: str, status: str, **updates: Any) -> MirrorState:
        current = self.get(object_id) or MirrorState(object_id=str(object_id))
        values = asdict(current)
        values.update(updates)
        values["object_id"] = str(object_id)
        values["status"] = status
        return self.upsert(values)

    def all(self) -> list[MirrorState]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM mirror_state ORDER BY object_id").fetchall()
        return [MirrorState(**dict(row)) for row in rows]


def is_cold_evicted_receipt(previous: MirrorState | Mapping[str, Any] | None) -> bool:
    """True when live state is a durable analyzed-hash receipt without raw bytes.

    REHYDRATE_REQUIRED is intentionally excluded: drift must not skip acquisition.
    FAILED is also excluded so a retryable error cannot freeze as cold.
    """
    if previous is None:
        return False
    value = asdict(previous) if isinstance(previous, MirrorState) else dict(previous)
    if str(value.get("status") or "") in {"FAILED", "FAILED_RETRYABLE", "EXPLICIT_HOLD", "REHYDRATE_REQUIRED"}:
        return False
    if not str(value.get("sha256") or ""):
        return False
    status = str(value.get("status") or "")
    terminal = str(value.get("terminal_routing_state") or "")
    return status == "EVICTED_ANALYZED" or (
        terminal == "ANALYZED_HASH_RECEIPT" and status != "REHYDRATE_REQUIRED"
    )


def decide_incremental_identity(observed: Mapping[str, Any], previous: MirrorState | None) -> str:
    """Return the B1 change type using stable object_id plus version/mtime/size."""
    if previous is None:
        return "NEW"
    # A failed attempt is retryable even when the source metadata did not
    # change.  The previous committed artifact, if any, remains current.
    if previous.status in {"FAILED", "FAILED_RETRYABLE"}:
        return "NEW"
    # Cold PPTX retention is a legitimate terminal state: SHA/receipt remain,
    # the raw path is intentionally absent, and unchanged identity must not
    # be classified NEW merely because bytes were evicted.
    if not previous.artifact_path and not is_cold_evicted_receipt(previous):
        return "NEW"
    same_content_identity = (
        str(observed.get("version", observed.get("source_version", "")) or "") == previous.source_version
        and str(observed.get("mtime", "") or "") == previous.source_mtime
        and int(observed.get("size", 0) or 0) == int(previous.source_size or 0)
    )
    if not same_content_identity:
        return "CONTENT_CHANGED"
    name_changed = str(observed.get("display_name", "") or "") != previous.display_name
    parent_changed = str(observed.get("parent_id", "") or "") != previous.parent_id
    if parent_changed:
        return "MOVED"
    if name_changed:
        return "RENAMED"
    return "UNCHANGED"


def reconcile_inventory_coverage(inventory, states) -> dict[str, Any]:
    """Prove that every exhaustive-inventory file has exactly one terminal route.

    ``inventory`` may contain folders; only file records participate in the
    source-persistence equation. Duplicate stable IDs are reported and make
    reconciliation fail closed.  ``states`` may be ``MirrorState`` objects or
    mappings returned by a caller-owned state store.
    """
    files = [item for item in inventory if isinstance(item, Mapping) and item.get("object_type", "file") == "file"]
    ids = [str(item.get("object_id") or item.get("document_id") or "") for item in files]
    counts = {}
    for object_id in ids:
        counts[object_id] = counts.get(object_id, 0) + 1
    duplicate_count = sum(count - 1 for count in counts.values() if count > 1)
    state_by_id = {}
    for state in states:
        value = asdict(state) if isinstance(state, MirrorState) else dict(state)
        object_id = str(value.get("object_id") or "")
        if object_id:
            state_by_id[object_id] = value
    excluded = committed = blocked = 0
    missing_ids = {object_id for object_id in set(ids) if not object_id or object_id not in state_by_id}
    lost_count = len(missing_ids)
    unaccounted = duplicate_count + lost_count
    exact_exclusion_ids = set()
    for item in files:
        object_id = str(item.get("object_id") or item.get("document_id") or "")
        # Duplicates are intentionally counted only once below; the extra
        # references already make unaccounted_count non-zero.
        if object_id in exact_exclusion_ids:
            continue
        state = state_by_id.get(object_id)
        excluded_by_rule = (
            "FY26运营导师带教情况表" in str(item.get("filename") or "")
            or "FY26运营导师带教情况表" in str(item.get("display_name") or "")
        )
        terminal = str((state or {}).get("terminal_routing_state") or "")
        status = str((state or {}).get("status") or "")
        if excluded_by_rule or terminal == "EXCLUDED_INVENTORY_ONLY" or status == "EXCLUDED":
            excluded += 1
            exact_exclusion_ids.add(object_id)
        elif terminal == "COMMITTED_LOCAL" and (state or {}).get("artifact_path") and (state or {}).get("sha256"):
            try:
                artifact = Path(str(state["artifact_path"]))
                integrity_ok = artifact.is_file() and sha256_file(artifact) == str(state["sha256"])
            except (OSError, ValueError):
                integrity_ok = False
            if integrity_ok:
                committed += 1
            else:
                unaccounted += 1
        elif terminal == "ANALYZED_HASH_RECEIPT" and (state or {}).get("sha256"):
            # Cold retention is a legitimate persisted terminal state.  The
            # raw path is intentionally absent; the retention ledger is the
            # authority for the source-version receipt and rehydration path.
            committed += 1
        elif not state:
            continue
        elif terminal == "BLOCKED_WITH_EXACT_REASON" or status in {"BLOCKED_BACKEND", "FAILED", "FAILED_RETRYABLE", "EXPLICIT_HOLD", "NEEDS_CLASSIFICATION"}:
            blocked += 1
        else:
            unaccounted += 1
    inventory_file_count = len(files)
    equation_pass = inventory_file_count == excluded + committed + blocked and duplicate_count == 0 and unaccounted == 0
    return {
        "inventory_file_count": inventory_file_count,
        "excluded_count": excluded,
        "committed_count": committed,
        "blocked_count": blocked,
        "duplicate_count": duplicate_count,
        "lost_count": lost_count,
        "unaccounted_count": unaccounted,
        "equation_pass": equation_pass,
        "full_mirror_ready": equation_pass and blocked == 0,
    }


# Descriptive alias for callers that expose this as a report endpoint.
coverage_reconciliation_report = reconcile_inventory_coverage


def is_ksheet_item(item: Mapping[str, Any]) -> bool:
    """Return True if an item is a .ksheet online spreadsheet object."""
    if not isinstance(item, Mapping):
        return False
    name = str(item.get("filename") or item.get("display_name") or item.get("fname") or "").casefold()
    ext = str(item.get("extension") or "").casefold()
    if not ext.startswith(".") and ext:
        ext = "." + ext
    ftype = str(item.get("file_type") or item.get("ftype") or "")
    route = str(item.get("acquisition_route") or "")
    backend = str(item.get("backend") or item.get("mirror_backend_candidate") or "")

    if name.endswith(".ksheet") or name.endswith(".ksheet.wpsonline") or ext == ".ksheet":
        return True
    if ftype == "WPS_SPREADSHEET" or route == "KSHEET_EXPORT_XLSX" or backend == "WPS_SPREADSHEET_EXPORT_XLSX":
        return True
    return False


def is_airpage_item(item: Mapping[str, Any]) -> bool:
    """Return True if an item is an AirPage / .otl document object."""
    if not isinstance(item, Mapping):
        return False
    name = str(item.get("filename") or item.get("display_name") or item.get("fname") or "").casefold()
    ext = str(item.get("extension") or "").casefold()
    if not ext.startswith(".") and ext:
        ext = "." + ext
    ftype = str(item.get("file_type") or item.get("ftype") or "")
    route = str(item.get("acquisition_route") or "")
    backend = str(item.get("backend") or item.get("mirror_backend_candidate") or "")

    if name.endswith(".otl") or ext == ".otl":
        return True
    if ftype == "WPS_DOCUMENT" or route == "AIRPAGE_EXPORT_DOCX" or backend == "WPS_DOCUMENT_EXPORT_DOCX":
        return True
    return False


def reconcile_ksheet_coverage(inventory: Iterable[Mapping[str, Any]], states: Iterable[MirrorState | Mapping[str, Any]]) -> dict[str, Any]:
    """KSheet-first reconciliation gate over an exhaustive inventory.

    Requirements:
      1. AirPage / .otl documents remain visible in inventory but are marked DEFERRED/NON_BLOCKING.
      2. All non-excluded .ksheet objects must be exactly one of COMMITTED/UNCHANGED or explicit FAILED_RETRYABLE.
      3. No silent drops; no BLOCKED_BACKEND / NEEDS_CLASSIFICATION for .ksheet.
      4. Reports KSheet discovered, excluded, committed, failed, and unaccounted counts.
    """
    files = [item for item in inventory if isinstance(item, Mapping) and item.get("object_type", "file") == "file"]
    
    ksheet_files = []
    airpage_deferred_count = 0
    non_ksheet_count = 0

    for item in files:
        if is_ksheet_item(item):
            ksheet_files.append(item)
        elif is_airpage_item(item):
            airpage_deferred_count += 1
        else:
            non_ksheet_count += 1

    ksheet_ids = [str(item.get("object_id") or item.get("document_id") or "") for item in ksheet_files]
    counts = {}
    for object_id in ksheet_ids:
        counts[object_id] = counts.get(object_id, 0) + 1
    duplicate_count = sum(count - 1 for count in counts.values() if count > 1)

    state_by_id = {}
    for state in states:
        value = asdict(state) if isinstance(state, MirrorState) else dict(state)
        object_id = str(value.get("object_id") or "")
        if object_id:
            state_by_id[object_id] = value

    missing_ids = {object_id for object_id in set(ksheet_ids) if not object_id or object_id not in state_by_id}
    lost_count = len(missing_ids)
    unaccounted_ksheet = duplicate_count + lost_count

    excluded = committed = failed = 0
    seen_ids = set()

    for item in ksheet_files:
        object_id = str(item.get("object_id") or item.get("document_id") or "")
        if object_id in seen_ids:
            continue
        seen_ids.add(object_id)

        state = state_by_id.get(object_id)
        name = str(item.get("filename") or item.get("display_name") or "")
        excluded_by_rule = "FY26运营导师带教情况表" in name

        terminal = str((state or {}).get("terminal_routing_state") or "")
        status = str((state or {}).get("status") or "")
        error_class = str((state or {}).get("error_class") or "")

        if excluded_by_rule or terminal == "EXCLUDED_INVENTORY_ONLY" or status == "EXCLUDED":
            excluded += 1
        elif terminal == "COMMITTED_LOCAL" or status in {"COMMITTED_LOCAL", "UNCHANGED"}:
            if (state or {}).get("artifact_path") and (state or {}).get("sha256"):
                try:
                    artifact = Path(str(state["artifact_path"]))
                    integrity_ok = artifact.is_file() and sha256_file(artifact) == str(state["sha256"])
                except (OSError, ValueError):
                    integrity_ok = False
                if integrity_ok:
                    committed += 1
                else:
                    unaccounted_ksheet += 1
            else:
                unaccounted_ksheet += 1
        elif not state:
            continue
        elif status in {"FAILED", "FAILED_RETRYABLE", "EXPLICIT_HOLD"} or terminal == "BLOCKED_WITH_EXACT_REASON":
            if error_class.startswith("BLOCKED_BACKEND") or status == "BLOCKED_BACKEND" or status == "NEEDS_CLASSIFICATION":
                unaccounted_ksheet += 1
            else:
                failed += 1
        else:
            unaccounted_ksheet += 1

    ksheet_discovered_count = len(ksheet_files)
    equation_pass = (
        ksheet_discovered_count == (excluded + committed + failed)
        and duplicate_count == 0
        and unaccounted_ksheet == 0
    )
    ksheet_readiness_pass = (
        equation_pass
        and failed == 0
        and unaccounted_ksheet == 0
        and ksheet_discovered_count > 0
    )

    return {
        "ksheet_discovered_count": ksheet_discovered_count,
        "ksheet_excluded_count": excluded,
        "ksheet_committed_count": committed,
        "ksheet_failed_count": failed,
        "duplicate_count": duplicate_count,
        "lost_count": lost_count,
        "unaccounted_ksheet_count": unaccounted_ksheet,
        "equation_pass": equation_pass,
        "ksheet_readiness_pass": ksheet_readiness_pass,
        "airpage_deferred_count": airpage_deferred_count,
        "non_ksheet_inventory_count": non_ksheet_count,
        "total_inventory_file_count": len(files),
    }


class MirrorArtifactStore:
    """Staging, immutable version finalization, and atomic current pointers."""

    def __init__(self, mirror_root: str | os.PathLike[str]):
        self.root = Path(mirror_root).resolve()
        self.versions = self.root / "versions"
        self.current = self.root / "current"
        self.staging = self.root / ".staging"
        for path in (self.versions, self.current, self.staging):
            path.mkdir(parents=True, exist_ok=True)

    def staging_dir(self, run_id: str, object_id: str) -> Path:
        path = self.staging / _component(run_id) / _component(object_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def stage_bytes(self, run_id: str, object_id: str, filename: str, body: bytes) -> Path:
        directory = self.staging_dir(run_id, object_id)
        safe_name = _component(filename)
        target = directory / safe_name
        fd, temporary = tempfile.mkstemp(prefix=".part-", dir=directory)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return target

    def _assert_staged(self, path: Path) -> None:
        resolved = path.resolve()
        if not resolved.is_file() or self.staging not in resolved.parents:
            raise ValueError("ARTIFACT_MUST_BE_IN_STAGING")

    def finalize(self, staged_path: str | os.PathLike[str], object_id: str, source_version: str, artifact_name: str) -> tuple[Path, int, str]:
        staged = Path(staged_path)
        self._assert_staged(staged)
        object_dir = self.versions / _component(object_id) / _component(source_version)
        object_dir.mkdir(parents=True, exist_ok=True)
        historical = object_dir / _component(artifact_name)
        digest = sha256_file(staged)
        if historical.exists():
            if sha256_file(historical) != digest:
                raise FileExistsError("IMMUTABLE_VERSION_CONFLICT")
            staged.unlink()
        else:
            os.replace(staged, historical)
        self._update_current(historical, object_id, artifact_name)
        return historical, historical.stat().st_size, digest

    def _update_current(self, historical: Path, object_id: str, artifact_name: str) -> Path:
        current_dir = self.current / _component(object_id)
        current_dir.mkdir(parents=True, exist_ok=True)
        target = current_dir / _component(artifact_name)
        temporary = current_dir / ("." + target.name + ".new")
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()
        os.symlink(os.path.relpath(historical, current_dir), temporary)
        os.replace(temporary, target)
        return target
