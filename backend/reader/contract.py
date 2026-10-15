from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

READER_VERSION = "paw-reader-fidelity/0.4.0"
EVIDENCE_SCHEMA_VERSION = "reader-evidence/3"


def artifact_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def content_fingerprint(semantic: Mapping[str, Any], text_blocks: list[Any], tables: list[Any], relationships: list[Any]) -> str:
    return hashlib.sha256(canonical_json({
        "structured_semantics": semantic,
        "text_blocks": text_blocks,
        "tables": tables,
        "relationships": relationships,
    }).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReaderInput:
    """Read-only source identity supplied by the mirror adapter."""

    path: str
    source_object_id: str = "local-fixture"
    source_version: str = "fixture-1"
    display_name: str = ""
    family: str = ""
    committed: bool = True
    raw_artifact_sha256: str = ""


@dataclass
class ReaderResult:
    source_object_id: str
    source_version: str
    reader_type: str
    raw_artifact_sha256: str
    structured_semantics: dict[str, Any] = field(default_factory=dict)
    text_blocks: list[dict[str, Any]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    visual_pages: list[dict[str, Any]] = field(default_factory=list)
    relationships: list[dict[str, Any]] = field(default_factory=list)
    embedded_objects: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fidelity_flags: dict[str, Any] = field(default_factory=dict)
    derived_artifacts: list[dict[str, Any]] = field(default_factory=list)
    reader_version: str = READER_VERSION
    parser_version: str = ""
    evidence_schema_version: str = EVIDENCE_SCHEMA_VERSION
    raw_source_available: bool = False
    source_version_linked: bool = False
    content_fingerprint: str = ""

    def finalize(self) -> "ReaderResult":
        self.content_fingerprint = content_fingerprint(
            self.structured_semantics, self.text_blocks, self.tables, self.relationships
        )
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write_json(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_dict()
        payload["artifact_lineage"] = {"parent_raw_artifact_sha256": self.raw_artifact_sha256, "source_version": self.source_version, "kind": "reader-result"}
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def new_result(inp: ReaderInput, reader_type: str, *, raw_hash: str | None = None) -> ReaderResult:
    actual_hash = artifact_sha256(inp.path)
    result = ReaderResult(
        source_object_id=inp.source_object_id,
        source_version=inp.source_version,
        reader_type=reader_type,
        raw_artifact_sha256=actual_hash,
        raw_source_available=True,
        source_version_linked=bool(inp.source_object_id and inp.source_version),
    )
    if (raw_hash or inp.raw_artifact_sha256) and (raw_hash or inp.raw_artifact_sha256) != actual_hash:
        result.warnings.append("supplied raw artifact hash did not match bytes; actual hash is authoritative")
    return result


def lineage(raw_hash: str, source_version: str, path: str | Path, kind: str, **extra: Any) -> dict[str, Any]:
    """Create the common trace envelope for every derived artifact."""
    item = {
        "path": str(path), "kind": kind, "sha256": artifact_sha256(path),
        "parent_raw_artifact_sha256": raw_hash, "source_version": source_version,
    }
    item.update(extra)
    return item
