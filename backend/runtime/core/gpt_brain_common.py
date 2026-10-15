"""Shared analysis-plane helpers for GPT Brain Runner / Project Builder / Data Layer.

This module never writes PAW Formal State, never mutates Cursor artifacts,
and never prints secrets.  Numeric identity is hashed; period parsing is
fail-closed (unknown calendars stay unlabeled rather than invented).
"""
from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


LAYER_SCHEMA_VERSION = 1
UNSPECIFIED_UNIT = "UNSPECIFIED"
VALUE_KIND_NUMERIC = "NUMERIC"
VALUE_KIND_STATIC_STORED = "STATIC_STORED"
VALUE_KIND_DERIVED = "DERIVED"
VALUE_KIND_FREE_TEXT = "FREE_TEXT"
VALUE_KIND_MIXED = "MIXED"

COVERAGE_FINAL_STATES = frozenset({
    "CONSOLIDATED",
    "NO_KNOWLEDGE_IMPACT",
    "DATA_EXTRACTED",
    "PROJECT_CONTEXT_ONLY",
    "REVIEW_REQUIRED",
    "INSUFFICIENT_EVIDENCE",
    "FAILED",
})
COVERAGE_RESULT_STATES = COVERAGE_FINAL_STATES | frozenset({"VISUAL_PENDING"})

# Keys / labels that must not appear unredacted in default public summaries.
_SENSITIVE_KEY_RE = re.compile(
    r"(姓名|员工\s*id|员工编号|薪酬|薪资|工资|salary|compensation|employee[_-]?id|national.?id|身份证)",
    re.IGNORECASE,
)
_EMPTY_DIM_VALUES = {
    "",
    "-",
    "--",
    "n/a",
    "na",
    "null",
    "none",
    "nil",
    "无",
    "空",
    "unspecified",
    "unknown",
}
_UNSPECIFIED_UNIT_LABELS = {
    "",
    "表内未标注",
    "未标注",
    "未注明",
    "无单位",
    "unknown",
    "unspecified",
    "n/a",
    "na",
    "none",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): canonical(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (list, tuple, set, frozenset)):
        vals = [canonical(v) for v in value]
        return sorted(vals, key=lambda v: json.dumps(v, ensure_ascii=False, sort_keys=True))
    return value


def hash_payload(value: Any) -> str:
    encoded = json.dumps(canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def unique(values: Iterable[Any] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values or ():
        value = text(raw)
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def safe_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY_RE.search(key or ""))


def canonicalize_unit(unit: Any) -> str:
    raw = text(unit)
    if raw.casefold() in _UNSPECIFIED_UNIT_LABELS or raw == "":
        return UNSPECIFIED_UNIT
    return raw


def canonicalize_dimensions(dimensions: Any) -> dict[str, Any]:
    if dimensions is None:
        return {}
    if not isinstance(dimensions, Mapping):
        raise ValueError("dimensions must be a mapping")
    out: dict[str, Any] = {}
    for raw_key, raw_value in dimensions.items():
        key = text(raw_key)
        if not key:
            continue
        if isinstance(raw_value, Mapping):
            nested = canonicalize_dimensions(raw_value)
            if nested:
                out[key] = nested
            continue
        if raw_value is None:
            continue
        if isinstance(raw_value, str) and text(raw_value).casefold() in _EMPTY_DIM_VALUES:
            continue
        if raw_value in ("",):
            continue
        out[key] = raw_value
    return {k: out[k] for k in sorted(out)}


def redact_mapping(value: Any) -> Any:
    """Default public-read redaction.  Never drops evidence locators or SourceVersion ids."""
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, child in value.items():
            if is_sensitive_key(str(key)):
                out[str(key)] = "[REDACTED]"
            else:
                out[str(key)] = redact_mapping(child)
        return out
    if isinstance(value, list):
        return [redact_mapping(item) for item in value]
    return value


_MONTH_RE = re.compile(
    r"^(?:(?P<y>20\d{2})[-/\.年](?P<m>0?[1-9]|1[0-2])月?|(?P<y2>20\d{2})(?P<m2>0[1-9]|1[0-2]))$"
)
_QUARTER_RE = re.compile(
    r"^(?P<y>20\d{2})\s*[-/年]?\s*[Qq第]?[Qq季]?\s*(?P<q>[1-4])\s*季度?$"
)
_QUARTER_COMPACT_RE = re.compile(r"^(?P<y>20\d{2})[Qq](?P<q>[1-4])$")
_YEAR_RE = re.compile(r"^(?P<y>20\d{2})年?$")
_ISO_DATE_RE = re.compile(r"^(?P<y>20\d{2})-(?P<m>0[1-9]|1[0-2])-(?P<d>0[1-9]|[12]\d|3[01])$")
_FY_RE = re.compile(r"^[Ff][Yy]\s*(?P<yy>\d{2}|\d{4})$")


def parse_period(period: Any) -> dict[str, Any]:
    """Parse a period label into structured time.  Unknown calendars stay unlabeled.

    Does not invent a fiscal-year start month.  FY labels keep grain=FISCAL_YEAR
    with start/end null so range queries do not silently invent coverage.
    """
    label = text(period)
    record = {
        "label": label or None,
        "grain": "UNKNOWN",
        "start": None,
        "end": None,
        "parse_status": "UNSPECIFIED" if not label else "UNPARSED",
    }
    if not label:
        return record

    iso = _ISO_DATE_RE.match(label)
    if iso:
        y, m, d = int(iso.group("y")), int(iso.group("m")), int(iso.group("d"))
        try:
            day = date(y, m, d)
        except ValueError:
            record["parse_status"] = "INVALID"
            return record
        record.update(grain="DAY", start=day.isoformat(), end=day.isoformat(), parse_status="PARSED")
        return record

    month = _MONTH_RE.match(label)
    if month:
        y = int(month.group("y") or month.group("y2"))
        m = int(month.group("m") or month.group("m2"))
        last = calendar.monthrange(y, m)[1]
        record.update(
            grain="MONTH",
            start=date(y, m, 1).isoformat(),
            end=date(y, m, last).isoformat(),
            parse_status="PARSED",
        )
        return record

    quarter = _QUARTER_RE.match(label) or _QUARTER_COMPACT_RE.match(label)
    if quarter:
        y = int(quarter.group("y"))
        q = int(quarter.group("q"))
        start_m = (q - 1) * 3 + 1
        end_m = start_m + 2
        last = calendar.monthrange(y, end_m)[1]
        record.update(
            grain="QUARTER",
            start=date(y, start_m, 1).isoformat(),
            end=date(y, end_m, last).isoformat(),
            parse_status="PARSED",
        )
        return record

    year = _YEAR_RE.match(label)
    if year:
        y = int(year.group("y"))
        record.update(
            grain="YEAR",
            start=date(y, 1, 1).isoformat(),
            end=date(y, 12, 31).isoformat(),
            parse_status="PARSED",
        )
        return record

    if _FY_RE.match(label):
        record.update(grain="FISCAL_YEAR", parse_status="LABEL_ONLY")
        return record

    record["parse_status"] = "UNPARSED"
    return record


def periods_overlap(period: Mapping[str, Any], start: str | None, end: str | None) -> bool:
    """Inclusive overlap.  Periods without parsed bounds only match empty range filters."""
    if not start and not end:
        return True
    p_start = text(period.get("start")) or None
    p_end = text(period.get("end")) or None
    if not p_start or not p_end:
        return False
    if start and p_end < start:
        return False
    if end and p_start > end:
        return False
    return True


def validate_evidence(evidence: Iterable[Mapping[str, Any]] | None) -> tuple[bool, str, list[dict[str, str]]]:
    refs: list[dict[str, str]] = []
    for raw in evidence or ():
        if not isinstance(raw, Mapping):
            continue
        svid = text(raw.get("source_version_id"))
        locator = text(raw.get("evidence_locator"))
        if svid and locator:
            refs.append({"source_version_id": svid, "evidence_locator": locator})
    if refs:
        return True, "evidence_gate_passed", refs
    return False, "evidence_gate_failed: source_version_id+evidence_locator required", []


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


_LEASE_THREAD_LOCKS: dict[str, threading.Lock] = {}
_LEASE_THREAD_HELD: dict[str, int] = {}


def _lease_thread_lock(path: Path) -> threading.Lock:
    key = str(path.absolute())
    lock = _LEASE_THREAD_LOCKS.get(key)
    if lock is None:
        lock = threading.Lock()
        _LEASE_THREAD_LOCKS[key] = lock
    return lock


class FileLeaseLock:
    """Exclusive file lock with PID/lease metadata.  Single-flight for one Bootstrap.

    Combines a process-local threading lock with flock so same-process duplicate
    starts also fail (Linux flock is per-inode and does not conflict across
    descriptors of one process).
    """

    def __init__(self, path: str | os.PathLike[str], *, stale_seconds: int = 900):
        self.path = Path(path)
        self.stale_seconds = stale_seconds
        self._fh = None
        self.holder: dict[str, Any] | None = None
        self._thread_lock: threading.Lock | None = None

    def _open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+", encoding="utf-8")

    def acquire(self, *, run_id: str, actor: str) -> dict[str, Any]:
        if fcntl is None:
            raise RuntimeError("fcntl is required for single-flight")
        self._thread_lock = _lease_thread_lock(self.path)
        if not self._thread_lock.acquire(blocking=False):
            raise RuntimeError("bootstrap runner already active in this process")
        key = str(self.path)
        _LEASE_THREAD_HELD[key] = os.getpid()
        self._open()
        assert self._fh is not None
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            existing = ""
            try:
                self._fh.seek(0)
                existing = self._fh.read()
            finally:
                self._fh.close()
                self._fh = None
                self._thread_lock.release()
                _LEASE_THREAD_HELD.pop(key, None)
            raise RuntimeError(f"bootstrap runner already active: {existing[:300]}") from exc
        meta = {
            "pid": os.getpid(),
            "run_id": run_id,
            "actor": actor,
            "acquired_at": utc_now(),
            "heartbeat_at": utc_now(),
        }
        self._fh.seek(0)
        self._fh.truncate()
        json.dump(meta, self._fh, ensure_ascii=False)
        self._fh.write("\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())
        self.holder = meta
        return meta

    def heartbeat(self) -> None:
        if self._fh is None or self.holder is None:
            return
        self.holder["heartbeat_at"] = utc_now()
        self._fh.seek(0)
        self._fh.truncate()
        json.dump(self.holder, self._fh, ensure_ascii=False)
        self._fh.write("\n")
        self._fh.flush()

    def release(self) -> None:
        try:
            if self._fh is not None:
                if fcntl is not None:
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                self._fh.close()
        finally:
            self._fh = None
            self.holder = None
            if self._thread_lock is not None and self._thread_lock.locked():
                self._thread_lock.release()
            _LEASE_THREAD_HELD.pop(str(self.path), None)
            self._thread_lock = None

    def __enter__(self) -> "FileLeaseLock":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


class LockedJsonStore:
    """Process-safe JSON store used by Runner / Project Builder / Data Layer sidecars."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._thread = threading.RLock()
        self._lock_path = Path(str(self.path) + ".lock")

    def load(self) -> dict[str, Any]:
        with self._thread:
            return read_json(self.path)

    def save(self, value: Mapping[str, Any]) -> None:
        with self._thread:
            atomic_write_json(self.path, value)

    @contextmanager
    def mutate(self):
        with self._thread:
            lock_fh = None
            if fcntl is not None:
                self._lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_fh = open(self._lock_path, "a+", encoding="utf-8")
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            try:
                data = read_json(self.path)
                yield data
                atomic_write_json(self.path, data)
            finally:
                if lock_fh is not None:
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
                    lock_fh.close()


class FailClosed(RuntimeError):
    """Identity / evidence / governance failure that must stop advancement."""


__all__ = [
    "COVERAGE_FINAL_STATES",
    "COVERAGE_RESULT_STATES",
    "FailClosed",
    "FileLeaseLock",
    "LAYER_SCHEMA_VERSION",
    "LockedJsonStore",
    "UNSPECIFIED_UNIT",
    "VALUE_KIND_DERIVED",
    "VALUE_KIND_FREE_TEXT",
    "VALUE_KIND_MIXED",
    "VALUE_KIND_NUMERIC",
    "VALUE_KIND_STATIC_STORED",
    "atomic_write_json",
    "canonical",
    "canonicalize_dimensions",
    "canonicalize_unit",
    "hash_payload",
    "is_sensitive_key",
    "parse_period",
    "periods_overlap",
    "read_json",
    "redact_mapping",
    "safe_copy",
    "text",
    "unique",
    "utc_now",
    "validate_evidence",
]
