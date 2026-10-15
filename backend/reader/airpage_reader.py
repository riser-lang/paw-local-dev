from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Mapping

from .contract import ReaderInput, ReaderResult, new_result


AIRPAGE_READER_VERSION = "airpage-normalized-json/0.2.0"
AIRPAGE_PARSER_VERSION = "python-json+airpage-normalizer/2"


def _items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else ([] if value is None else [value])


def _block_text(value: Any) -> str:
    """Flatten AirPage text, including text held inside table rows."""
    if not isinstance(value, Mapping):
        return str(value or "")
    parts: list[str] = []
    for key in ("text", "content"):
        if isinstance(value.get(key), str):
            parts.append(value[key])
    rows = value.get("rows")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, list):
                for cell in row:
                    if isinstance(cell, Mapping):
                        parts.append(_block_text(cell))
                    elif cell is not None:
                        parts.append(str(cell))
    # The mirror's text_char_count is a byte-independent character total of
    # source strings; separators would make the evidence count drift.
    return "".join(x for x in parts if x)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _same_path(left: Any, right: Path) -> bool:
    try:
        return Path(str(left)).resolve() == right.resolve()
    except (OSError, ValueError):
        return False


def _mirror_evidence(inp: Any, data: Mapping[str, Any], blocks: list[Any], tables: list[Any], embedded: list[Any], text_blocks: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Verify the committed AirPage sidecar instead of inventing evidence.

    The semantic JSON is intentionally the manifest artifact, while the
    capture run's two-pass and count evidence lives in its adjacent
    ``metadata.json``.  A sidecar only counts when its hashes, identities,
    counts, raw capture and derived Markdown all agree with the artifact.
    """
    inline_completeness = data.get("completeness", data.get("completeness_evidence"))
    inline_stability = data.get("stability", data.get("stability_evidence"))
    if inline_completeness and inline_stability:
        return ({"verified": True, "source": "semantic-mirror-explicit-evidence", "checks": {"explicit_completeness": True}, "value": inline_completeness},
                {"verified": True, "source": "semantic-mirror-explicit-evidence", "checks": {"explicit_stability": True}, "value": inline_stability}, [])

    semantic_path = Path(inp.path)
    metadata_path = semantic_path.with_name("metadata.json")
    raw_path = semantic_path.with_name("raw.json")
    markdown_path = semantic_path.with_name("content.md")
    warnings: list[str] = []
    checks: dict[str, bool] = {
        "metadata_present": metadata_path.is_file(),
        "raw_capture_present": raw_path.is_file(),
        "markdown_present": markdown_path.is_file(),
    }
    metadata: Mapping[str, Any] = {}
    raw: Mapping[str, Any] = {}
    if checks["metadata_present"]:
        try:
            loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata = loaded if isinstance(loaded, Mapping) else {}
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"AirPage metadata sidecar is unreadable: {exc}")
    if checks["raw_capture_present"]:
        try:
            loaded = json.loads(raw_path.read_text(encoding="utf-8"))
            raw = loaded if isinstance(loaded, Mapping) else {}
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"AirPage raw capture is unreadable: {exc}")

    checks.update({
        "semantic_hash_matches": bool(metadata.get("semantic_sha256") and metadata.get("semantic_sha256") == _sha256(semantic_path)),
        "metadata_points_to_semantic": _same_path(metadata.get("artifacts", {}).get("semantic_json", ""), semantic_path) if isinstance(metadata.get("artifacts"), Mapping) else False,
        "source_object_matches": str(metadata.get("object_id", "")) == str(inp.source_object_id),
        "source_version_matches": str(metadata.get("source_version", "")) in {str(inp.source_version), str(inp.source_version).rsplit("@", 1)[-1]},
        "raw_identity_matches": bool(raw) and str(raw.get("srcDocID") or raw.get("id") or "") == str(inp.source_object_id),
        "raw_version_matches": bool(raw) and str(raw.get("version", "")) == str(metadata.get("doc_version", "")),
        "block_count_matches": metadata.get("block_count") == data.get("block_count") == len(blocks) == len(text_blocks),
        "table_count_matches": metadata.get("table_count") == data.get("table_count") == len(tables),
        "attachment_count_matches": metadata.get("attachment_count") == data.get("attachment_count") == len(embedded),
        "text_char_count_matches": metadata.get("text_char_count") == data.get("text_char_count") == sum(len(str(x.get("text", ""))) for x in text_blocks),
        "two_pass_stability_pass": metadata.get("two_pass_stability") == "PASS",
    })
    if checks["markdown_present"]:
        expected = metadata.get("markdown_sha256")
        checks["markdown_hash_matches"] = bool(expected) and expected == _sha256(markdown_path)
    else:
        checks["markdown_hash_matches"] = False
    if not all(checks.values()):
        warnings.append("AirPage sidecar evidence is missing or inconsistent; completeness/stability remain fail-closed")
    completeness_checks = {k: v for k, v in checks.items() if k != "two_pass_stability_pass"}
    completeness = {
        "verified": bool(all(completeness_checks.values())),
        "source": "committed-mirror-metadata+semantic-consistency",
        "checks": completeness_checks,
        "metadata_sha256": _sha256(metadata_path) if metadata_path.is_file() else "",
    }
    stability = {
        "verified": bool(checks["two_pass_stability_pass"] and checks["semantic_hash_matches"] and checks["metadata_points_to_semantic"]),
        "source": "committed-mirror-two-pass-metadata",
        "checks": {k: checks[k] for k in ("two_pass_stability_pass", "semantic_hash_matches", "metadata_points_to_semantic")},
        "declared": metadata.get("two_pass_stability", ""),
    }
    return completeness, stability, warnings


def read_airpage(inp: ReaderInput, output_dir: str | Path = "") -> ReaderResult:
    result = new_result(inp, "AIRPAGE")
    result.reader_version = AIRPAGE_READER_VERSION
    result.parser_version = AIRPAGE_PARSER_VERSION
    try:
        data = json.loads(Path(inp.path).read_text(encoding="utf-8"))
        if not isinstance(data, Mapping):
            raise ValueError("semantic JSON root must be an object")
        blocks = _items(data.get("blocks", data.get("content", [])))
        tables = _items(data.get("tables", []))
        if not tables:
            tables = [x for x in blocks if isinstance(x, Mapping) and str(x.get("kind", "")).lower() == "table"]
        links = _items(data.get("links", data.get("hyperlinks", [])))
        embedded = _items(data.get("embedded_refs", data.get("embedded_objects", data.get("embeds", data.get("attachments", [])))))
        result.text_blocks = [{"block_id": x.get("id", x.get("block_id", str(i))), "order": i, "type": x.get("type", x.get("kind", "text")), "text": _block_text(x), "parent_id": x.get("parent_id", x.get("parent")), "properties": x.get("properties", {})} if isinstance(x, Mapping) else {"block_id": str(i), "order": i, "type": "text", "text": str(x)} for i, x in enumerate(blocks)]
        result.tables = [dict(x, table_id=x.get("id", x.get("table_id", str(i)))) if isinstance(x, Mapping) else {"table_id": str(i), "raw": x} for i, x in enumerate(tables)]
        result.relationships = [dict(x, kind=x.get("kind", "link")) if isinstance(x, Mapping) else {"kind": "link", "target": str(x)} for x in links]
        result.embedded_objects = [dict(x, kind=x.get("kind", "embedded-ref")) if isinstance(x, Mapping) else {"kind": "embedded-ref", "ref": str(x)} for x in embedded]
        hierarchy = data.get("hierarchy", data.get("tree", []))
        completeness, stability, evidence_warnings = _mirror_evidence(inp, data, blocks, tables, embedded, result.text_blocks)
        result.warnings.extend(evidence_warnings)
        result.structured_semantics = {"schema": data.get("schema", data.get("schema_version", data.get("version", "unknown"))), "metadata": data.get("metadata", {}), "hierarchy": hierarchy, "block_count": len(result.text_blocks), "table_count": len(result.tables), "link_count": len(result.relationships), "embedded_ref_count": len(result.embedded_objects), "normalized": bool(data.get("normalized", True)), "completeness": completeness, "stability": stability}
        result.fidelity_flags = {"semantic_normalized": result.structured_semantics["normalized"], "block_table_link_hierarchy_preserved": True, "embedded_refs_preserved": True, "completeness_evidence": completeness["verified"], "stability_evidence": stability["verified"], "failure_explicit": True}
        if not result.fidelity_flags["completeness_evidence"] or not result.fidelity_flags["stability_evidence"]:
            result.warnings.append("AirPage semantic mirror lacks verifiable completeness/stability evidence")
    except Exception as exc:
        result.warnings.append(f"AirPage semantic JSON parse failed closed: {exc}")
        result.fidelity_flags = {"semantic_normalized": False, "completeness_evidence": False, "stability_evidence": False, "failure_explicit": True}
    return result.finalize()
