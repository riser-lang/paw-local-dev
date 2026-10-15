"""Cloud Source Mirror — server-side read cache for trusted local sources.

Phase 3 (CLOUD_SOURCE_MIRROR_V1). The office PC pushes parsed, hashed entries
for its registered trusted local sources; the server keeps the last known good
AI-readable text so Agnes can read project material even when the office PC /
WPS are offline.

Purity rules (per product spec):
- MIRROR IS CACHE, NOT CANONICAL: never writes Knowledge/Project/Todo/Capture,
  never changes Formal Source identity, never touches workspace.db.
- Incremental: entries are skipped when the content hash is unchanged.
- Last-known-good: a parse failure never overwrites existing content.
- Storage: independent directory (default /var/lib/personal-workspace/source-mirror)
  with manifest.json + objects/<sha256>.txt. No DB schema changes.

Security:
- canonical_location must match `root:relative-path` (no drive letters, no
  traversal) — the storage key space is validated before any write.
- object file names are derived from sha256 hex only (safe, content-addressed).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DEFAULT_MIRROR_ROOT = "/var/lib/personal-workspace/source-mirror"

MANIFEST_NAME = "manifest.json"
OBJECT_DIR = "objects"
FAILED_DIR = "failed"

_LOCATION_RE = re.compile(r"^[a-z0-9_]+:[^:]+$")
MAX_ENTRIES_PER_BATCH = 50
MAX_TEXT_BYTES = 2 * 1024 * 1024  # 2 MB parsed text per entry

# Only sources with a trusted local origin may be mirrored (M02).
ALLOWED_ORIGINS = {"formal_business", "office_sync", "canonical"}


class MirrorError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _strip_hash_prefix(value: str) -> str:
    value = (value or "").strip()
    if value.startswith("sha256:"):
        value = value[len("sha256:"):]
    return value.lower()


def _valid_location(canonical_location: Any) -> str:
    text = str(canonical_location or "").strip().replace("\\", "/")
    if not _LOCATION_RE.match(text):
        raise MirrorError("invalid_location", f"invalid canonical_location: {canonical_location!r}")
    root, _, rel = text.partition(":")
    # single-char roots are drive letters (C:/x) — always reject; the relative
    # part must never be absolute, contain a drive prefix, or traverse.
    if len(root) < 2 or not rel or rel.startswith("/") or ":" in rel or ".." in text:
        raise MirrorError("invalid_location", "canonical_location must not contain traversal or drive prefixes")
    return text


def ensure_root(mirror_root: str = DEFAULT_MIRROR_ROOT) -> Path:
    root = Path(mirror_root)
    (root / OBJECT_DIR).mkdir(parents=True, exist_ok=True)
    (root / FAILED_DIR).mkdir(parents=True, exist_ok=True)
    return root


def _load_manifest(mirror_root: str) -> dict:
    root = ensure_root(mirror_root)
    path = root / MANIFEST_NAME
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("sources"), dict):
                return data
        except (OSError, ValueError):
            pass
    return {"version": 1, "updated_at": _now(), "sources": {}}


def _save_manifest(mirror_root: str, manifest: dict) -> None:
    root = ensure_root(mirror_root)
    path = root / MANIFEST_NAME
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    temp.replace(path)


def ingest_batch(mirror_root: str, entries: list[dict]) -> dict:
    """Store parsed entries incrementally (hash-based, last-good preserved).

    entry fields:
      canonical_location (required, root:relative)
      content_hash       (required, sha256:... or bare hex)
      size, mtime_ns     (source metadata)
      source_origin      (formal_business | office_sync | ...)
      source_kind        (docx|xlsx|pdf|pptx|txt|md|...)
      source_root        (local root id)
      relative_path      (local relative path)
      text               (parsed AI-readable text; required unless parse_error)
      parse_error        (optional; when set the entry is NOT written if a last
                          good object already exists)
      parser             (optional parser/version label)
    """
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES_PER_BATCH:
        raise MirrorError("invalid_batch", f"entries must be a list of at most {MAX_ENTRIES_PER_BATCH}")
    root = ensure_root(mirror_root)
    manifest = _load_manifest(mirror_root)
    sources = manifest["sources"]
    accepted = 0
    skipped_unchanged = 0
    skipped_failed = 0
    updated = 0
    errors: list[dict] = []

    for raw in entries:
        if not isinstance(raw, dict):
            errors.append({"error": "entry_not_object"})
            continue
        try:
            location = _valid_location(raw.get("canonical_location"))
            content_hash = _strip_hash_prefix(raw.get("content_hash") or "")
            if len(content_hash) != 64 or not re.fullmatch(r"[0-9a-f]{64}", content_hash):
                raise MirrorError("invalid_hash", "content_hash must be sha256 hex")
            origin = str(raw.get("source_origin") or "unknown")
            if origin not in ALLOWED_ORIGINS:
                errors.append({"canonical_location": location, "error": "origin_not_allowed", "message": f"source_origin '{origin}' is not a trusted mirror origin"})
                continue
            existing = sources.get(location) or {}
            parse_error = bool(raw.get("parse_error"))
            if not parse_error:
                text = raw.get("text")
                if not isinstance(text, str) or not text.strip():
                    raise MirrorError("empty_text", "parsed text is required when parse_error is not set")
                if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
                    raise MirrorError("text_too_large", "parsed text exceeds size limit")
            # incremental: unchanged hash → skip reparse entirely
            if existing.get("content_hash") == content_hash and not parse_error:
                skipped_unchanged += 1
                continue
            object_path = root / OBJECT_DIR / f"{content_hash}.txt"
            if parse_error:
                # last-known-good: keep the previous object if any
                if existing.get("content_hash") and (root / OBJECT_DIR / f"{existing['content_hash']}.txt").is_file():
                    skipped_failed += 1
                    sources[location] = {**existing, "last_error": str(raw.get("parse_error"))[:300], "last_error_at": _now()}
                    continue
                (root / FAILED_DIR / f"{content_hash}.json").write_text(
                    json.dumps({"location": location, "error": str(raw.get("parse_error"))[:300], "at": _now()}, ensure_ascii=False),
                    encoding="utf-8",
                )
                skipped_failed += 1
                continue
            object_path.write_text(text, encoding="utf-8")
            updated += 1
            sources[location] = {
                "content_hash": content_hash,
                "size": raw.get("size"),
                "mtime_ns": raw.get("mtime_ns"),
                "source_origin": raw.get("source_origin"),
                "source_kind": raw.get("source_kind"),
                "source_root": raw.get("source_root"),
                "relative_path": raw.get("relative_path"),
                "parser": raw.get("parser"),
                "mirrored_at": _now(),
            }
            accepted += 1
        except MirrorError as exc:
            errors.append({"canonical_location": raw.get("canonical_location"), "error": exc.code, "message": exc.message})
    manifest["updated_at"] = _now()
    _save_manifest(mirror_root, manifest)
    return {
        "accepted": accepted,
        "updated": updated,
        "skipped_unchanged": skipped_unchanged,
        "skipped_failed_last_good": skipped_failed,
        "errors": errors,
    }


def get_entry(mirror_root: str, canonical_location: str) -> Optional[dict]:
    """Return the last known good entry (with parsed text) or None.

    Works with no dependency on the office PC or WPS — this is the offline-read
    property (M06).
    """
    location = _valid_location(canonical_location)
    manifest = _load_manifest(mirror_root)
    entry = manifest["sources"].get(location)
    if not entry:
        return None
    content_hash = entry.get("content_hash") or ""
    object_path = Path(mirror_root) / OBJECT_DIR / f"{content_hash}.txt"
    if not object_path.is_file():
        return None
    try:
        text = object_path.read_text(encoding="utf-8")
    except OSError:
        return None
    return {**entry, "canonical_location": location, "text": text}


def list_entries(mirror_root: str) -> list[dict]:
    manifest = _load_manifest(mirror_root)
    return [
        {**entry, "canonical_location": location}
        for location, entry in sorted(manifest["sources"].items())
    ]


def entry_freshness(mirror_root: str, canonical_location: str, max_age_hours: float = 24.0) -> dict:
    """Freshness exposure (M09): age vs mirrored_at + source metadata."""
    location = _valid_location(canonical_location)
    manifest = _load_manifest(mirror_root)
    entry = manifest["sources"].get(location)
    if not entry:
        return {"canonical_location": location, "available": False}
    mirrored_at = entry.get("mirrored_at") or ""
    fresh = False
    age_hours = None
    try:
        if mirrored_at:
            mirrored = datetime.fromisoformat(mirrored_at.replace("Z", "+00:00"))
            age_hours = max(0.0, (datetime.now(timezone.utc) - mirrored).total_seconds() / 3600.0)
            fresh = age_hours <= max_age_hours
    except (ValueError, TypeError):
        pass
    return {
        "canonical_location": location,
        "available": True,
        "fresh": fresh,
        "age_hours": age_hours,
        "mirrored_at": mirrored_at,
        "content_hash": entry.get("content_hash"),
        "source_origin": entry.get("source_origin"),
    }


def summary(mirror_root: str) -> dict:
    manifest = _load_manifest(mirror_root)
    sources = manifest["sources"]
    by_origin: dict[str, int] = {}
    for entry in sources.values():
        origin = str(entry.get("source_origin") or "unknown")
        by_origin[origin] = by_origin.get(origin, 0) + 1
    return {
        "mirror_root": str(Path(mirror_root)),
        "total_sources": len(sources),
        "by_origin": by_origin,
        "updated_at": manifest.get("updated_at"),
    }
