"""Full Mirror -> Reader Artifact Ledger materializer.

This module accepts an already committed artifact manifest.  It never opens
the Mirror checkpoint, acquires a source, calls a model, or writes Formal
State.  Its only writes are the caller-owned output directory, checkpoint,
ledger, and derived reader evidence.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable, Mapping

from .contract import ReaderInput, ReaderResult, artifact_sha256, canonical_json
from .dispatch import read_artifact, reader_contract_current
from .gate import evaluate_gate


MATERIALIZER_VERSION = "paw-reader-materializer/5"
LEDGER_VERSION = "reader-artifact-ledger/1"


class MaterializerError(ValueError):
    """Input or checkpoint cannot be safely used for materialization."""


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _is_hash(value: Any) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{64}", str(value or "")))


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        Path(name).replace(path)
    except Exception:
        Path(name).unlink(missing_ok=True)
        raise


def _json_file(path: str | Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MaterializerError(f"cannot read JSON input {path}: {exc}") from exc


def _records(data: Any, keys: tuple[str, ...], label: str) -> list[dict[str, Any]]:
    if isinstance(data, list):
        value = data
    elif isinstance(data, Mapping):
        value = next((data[k] for k in keys if isinstance(data.get(k), list)), None)
        if value is None:
            value = [data]
    else:
        raise MaterializerError(f"{label} must be an object or array")
    if not all(isinstance(x, Mapping) for x in value):
        raise MaterializerError(f"{label} records must be JSON objects")
    return [dict(x) for x in value]


def load_source_versions(path: str | Path) -> list[dict[str, Any]]:
    return _records(_json_file(path), ("source_versions", "versions", "items"), "SourceVersion")


def load_artifact_manifest(path: str | Path) -> list[dict[str, Any]]:
    return _records(_json_file(path), ("artifacts", "artifact_manifest", "items"), "artifact manifest")


def _first(row: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value is not None and str(value) != "":
            return str(value)
    return ""


def _source_identity(row: Mapping[str, Any]) -> dict[str, str]:
    stable = _first(row, "stable_object_id", "source_object_id", "object_id", "source_id")
    source_id = _first(row, "source_id", "stable_object_id", "source_object_id", "object_id")
    version_id = _first(row, "source_version_id", "source_version", "version_id", "version", "version_marker")
    marker = _first(row, "version_marker", "source_version", "version")
    content_hash = _first(row, "content_sha256", "raw_artifact_sha256", "sha256")
    return {"source_id": source_id, "source_version_id": version_id, "stable_object_id": stable, "version_marker": marker, "content_sha256": content_hash}


def _artifact_identity(row: Mapping[str, Any]) -> dict[str, str]:
    source = _source_identity(row)
    source["artifact_path"] = _first(row, "artifact_path", "path", "raw_path")
    source["display_name"] = _first(row, "display_name", "filename", "name")
    source["family"] = _first(row, "family", "reader_type", "format").upper()
    source["manifest_sha256"] = _first(row, "sha256", "artifact_sha256", "raw_artifact_sha256")
    source["committed"] = str(row.get("committed", row.get("status", "COMMITTED"))).upper() in {"TRUE", "1", "YES", "COMMITTED", "COMMITTED_LOCAL", "PERSISTED"}
    return source


def _key(identity: Mapping[str, Any]) -> str:
    return f"{identity.get('stable_object_id', '')}@{identity.get('source_version_id', '')}@{identity.get('content_sha256', '')}"


def _component(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(value))
    return cleaned[:180] or "unknown"


def _family_for(path: str, declared: str) -> str:
    if declared:
        return declared.upper()
    return Path(path).suffix.lower().lstrip(".").upper()


def _evidence_hashes(result: ReaderResult) -> dict[str, str]:
    return {
        "structured_semantics_sha256": _sha256_json(result.structured_semantics),
        "text_blocks_sha256": _sha256_json(result.text_blocks),
        "tables_sha256": _sha256_json(result.tables),
        "relationships_sha256": _sha256_json(result.relationships),
        "embedded_objects_sha256": _sha256_json(result.embedded_objects),
        "visual_pages_sha256": _sha256_json(result.visual_pages),
        "content_fingerprint": result.content_fingerprint,
    }


def _ledger_entry(identity: Mapping[str, Any], *, status: str, result: ReaderResult | None = None, result_path: Path | None = None, error: str = "", reason_code: str = "") -> dict[str, Any]:
    entry: dict[str, Any] = {
        "ledger_version": LEDGER_VERSION,
        "materializer_version": MATERIALIZER_VERSION,
        "entry_key": _key(identity),
        "status": status,
        "source_lineage": {
            "source_id": identity.get("source_id", ""),
            "source_version_id": identity.get("source_version_id", ""),
            "stable_object_id": identity.get("stable_object_id", ""),
            "version_marker": identity.get("version_marker", ""),
        },
        "artifact": {
            "path": identity.get("artifact_path", ""),
            "sha256": identity.get("content_sha256", ""),
            "display_name": identity.get("display_name", ""),
            "family": identity.get("family", ""),
        },
        "reader_version": result.reader_version if result else "",
        "parser_version": result.parser_version if result else "",
        "result_path": str(result_path) if result_path else "",
        "error": error,
        "reason_code": reason_code,
    }
    if result is not None:
        gate = evaluate_gate([result])
        entry["model_independent_evidence"] = {
            "model_independent": True,
            "model_invocations": 0,
            "knowledge_bootstrap_started": False,
            "evidence_schema_version": result.evidence_schema_version,
            "raw_artifact_sha256_actual": result.raw_artifact_sha256,
            "evidence_hashes": _evidence_hashes(result),
            "fidelity_flags": result.fidelity_flags,
            "warnings": result.warnings,
            "gate_checks": gate.get("EVIDENCE_CHECKS", [{}])[0].get("checks", {}) if gate.get("EVIDENCE_CHECKS") else {},
            "failure_reasons": gate.get("EVIDENCE_CHECKS", [{}])[0].get("failure_reasons", []) if gate.get("EVIDENCE_CHECKS") else [],
        }
    return entry


def _checkpoint_payload(manifest_hash: str, source_hash: str, entries: Iterable[dict[str, Any]]) -> dict[str, Any]:
    return {
        "checkpoint_version": "reader-materializer-checkpoint/3",
        "materializer_version": MATERIALIZER_VERSION,
        "manifest_sha256": manifest_hash,
        "source_versions_sha256": source_hash,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "entries": {str(x["entry_key"]): {"status": x["status"], "result_path": x.get("result_path", ""), "reader_version": x.get("reader_version", ""), "parser_version": x.get("parser_version", ""), "artifact_sha256": x.get("artifact", {}).get("sha256", ""), "error": x.get("error", ""), "reason_code": x.get("reason_code", "")} for x in entries},
    }


def _reusable_existing_result(prior: Mapping[str, Any], identity: Mapping[str, Any]) -> bool:
    """Allow a safe resume across materializer/reader releases.

    A release bump must not force a full-corpus visual rebuild.  Existing
    completed evidence is reusable only when its file and controller-owned
    identity still match.  Gate evaluation remains authoritative and will
    reject any stale/incomplete evidence.
    """
    if prior.get("status") != "COMPLETED":
        return False
    result_path = Path(str(prior.get("result_path", "")))
    if not result_path.is_file():
        return False
    try:
        data = _json_file(result_path)
    except (OSError, ValueError, TypeError):
        return False
    flags = data.get("fidelity_flags", {})
    current_contract = reader_contract_current(
        str(identity.get("family", "")),
        str(prior.get("reader_version", "")),
        str(prior.get("parser_version", "")),
    )
    if not current_contract and os.environ.get("PAW_ALLOW_LEGACY_READER_RESUME") != "1":
        return False
    checks = data.get("gate_checks", {})
    if isinstance(checks, Mapping) and checks:
        if checks.get("STRUCTURE_PARSED") != "PASS":
            return False
        if checks.get("VISUAL_EVIDENCE_AVAILABLE") == "FAIL":
            return False
    else:
        family = str(data.get("reader_type") or identity.get("family") or "").upper()
        required = {
            "PPTX": ("structure_parsed", "relationships_preserved", "image_evidence_accounted"),
            "PDF": ("text_layout_parsed", "page_envelopes", "annotations_preserved"),
            "DOCX": ("structure_parsed", "tables_parsed", "images_accounted", "drawings_accounted"),
            "XLSX": ("raw_data_fidelity", "formula_fidelity", "styles_accounted", "charts_accounted", "images_accounted"),
            "AIRPAGE": ("semantic_normalized", "block_table_link_hierarchy_preserved", "embedded_refs_preserved", "completeness_evidence", "stability_evidence"),
            "LEGACY": ("raw_canonical", "validated_derived_parse_route"),
        }.get(family, ())
        if not required or not all(bool(flags.get(name)) for name in required):
            return False
        if family in {"PPTX", "PDF", "DOCX", "XLSX"} and not bool(flags.get("visual_evidence_available")):
            return False
    return (
        data.get("source_object_id") == identity.get("stable_object_id")
        and data.get("source_version") == identity.get("source_version_id")
        and data.get("raw_artifact_sha256") == identity.get("content_sha256")
        and bool(data.get("content_fingerprint"))
        and isinstance(flags, Mapping)
        and bool(flags.get("failure_explicit"))
    )


def materialize(
    source_versions: list[dict[str, Any]],
    artifact_manifest: list[dict[str, Any]],
    output_dir: str | Path,
    *,
    resume: bool = True,
    checkpoint_path: str | Path | None = None,
) -> dict[str, Any]:
    """Materialize all supplied artifacts and return the JSON ledger.

    The input lists are metadata supplied by a completed Full Mirror run.  A
    missing source/artifact/hash is recorded as BLOCKED; it is never silently
    omitted from the ledger.
    """
    out = Path(output_dir)
    checkpoint = Path(checkpoint_path) if checkpoint_path else out / "reader_materializer_checkpoint.json"
    source_hash = _sha256_json(source_versions)
    manifest_hash = _sha256_json(artifact_manifest)
    old: dict[str, Any] = {}
    if resume and checkpoint.exists():
        old = _json_file(checkpoint)
        previous_materializer = old.get("materializer_version")
        supported_legacy_resume = previous_materializer == "paw-reader-materializer/4"
        if ((previous_materializer != MATERIALIZER_VERSION and not supported_legacy_resume) or
                old.get("manifest_sha256") != manifest_hash or old.get("source_versions_sha256") != source_hash):
            raise MaterializerError("checkpoint input fingerprint mismatch; use a new output/checkpoint path")
    old_ledger_entries: dict[str, Any] = {}
    ledger_path = Path(output_dir) / "reader_artifact_ledger.json"
    if resume and ledger_path.exists():
        prior_ledger = _json_file(ledger_path)
        if isinstance(prior_ledger, Mapping) and isinstance(prior_ledger.get("entries"), list):
            old_ledger_entries = {str(x.get("entry_key")): x for x in prior_ledger["entries"] if isinstance(x, Mapping) and x.get("entry_key")}

    sources = [_source_identity(x) for x in source_versions]
    source_by_key = {(x["source_id"], x["source_version_id"]): x for x in sources if x["source_id"] and x["source_version_id"] and x["stable_object_id"] and _is_hash(x["content_sha256"])}
    if len(source_by_key) != len([x for x in sources if x["source_id"] and x["source_version_id"]]):
        raise MaterializerError("duplicate SourceVersion identity")
    artifacts = [_artifact_identity(x) for x in artifact_manifest]
    entries: dict[str, dict[str, Any]] = {}
    old_entries = old.get("entries", {}) if isinstance(old.get("entries", {}), Mapping) else {}
    matched_source_keys: set[tuple[str, str]] = set()
    for source in sources:
        if (not source["source_id"] or not source["source_version_id"] or
                not source["stable_object_id"] or not _is_hash(source["content_sha256"] )):
            identity = {**source, "artifact_path": "", "display_name": "", "family": "", "committed": False}
            entries[_key(identity)] = _ledger_entry(identity, status="BLOCKED", reason_code="SOURCE_VERSION_IDENTITY_OR_HASH_MISSING", error="SourceVersion requires source_id, source_version_id, stable_object_id, and content_sha256")
    for artifact in artifacts:
        source_key = (artifact["source_id"], artifact["source_version_id"])
        source = source_by_key.get(source_key)
        identity = dict(artifact)
        if source:
            matched_source_keys.add(source_key)
            identity["source_id"] = source["source_id"]
            identity["stable_object_id"] = source["stable_object_id"]
            identity["version_marker"] = source.get("version_marker", "")
            if not identity["content_sha256"]: identity["content_sha256"] = source["content_sha256"]
            if source["content_sha256"] and artifact["content_sha256"] and source["content_sha256"] != artifact["content_sha256"]:
                entries[_key(identity)] = _ledger_entry(identity, status="BLOCKED", reason_code="SOURCE_VERSION_HASH_MISMATCH", error="SourceVersion content_sha256 differs from artifact manifest sha256")
                continue
        key = _key(identity)
        prior = old_entries.get(key, {})
        if (resume and prior.get("status") == "COMPLETED" and
                prior.get("artifact_sha256") == identity.get("content_sha256") and
                _reusable_existing_result(prior, identity) and
                Path(str(prior.get("result_path", ""))).is_file()):
            entries[key] = dict(old_ledger_entries.get(key) or {"entry_key": key, "status": "COMPLETED", "source_lineage": {"source_id": identity.get("source_id", ""), "source_version_id": identity.get("source_version_id", ""), "stable_object_id": identity.get("stable_object_id", ""), "version_marker": identity.get("version_marker", "")}, "artifact": {"path": identity.get("artifact_path", ""), "sha256": identity.get("content_sha256", ""), "display_name": identity.get("display_name", ""), "family": identity.get("family", "")}, "reader_version": prior.get("reader_version", ""), "parser_version": prior.get("parser_version", ""), "result_path": prior.get("result_path", ""), "error": "", "reason_code": "RESUMED_FROM_CHECKPOINT", "model_independent_evidence": {"resumed": True, "model_independent": True, "model_invocations": 0}})
            entries[key]["reason_code"] = "RESUMED_FROM_CHECKPOINT"
            continue
        if source is None:
            entries[key] = _ledger_entry(identity, status="BLOCKED", reason_code="SOURCE_VERSION_NOT_IN_INPUT", error="artifact has no matching SourceVersion")
            continue
        path = Path(identity["artifact_path"])
        expected_hash = identity["manifest_sha256"]
        if not identity["committed"]:
            entries[key] = _ledger_entry(identity, status="BLOCKED", reason_code="RAW_SOURCE_NOT_COMMITTED", error="artifact is not marked committed")
            continue
        if not identity["artifact_path"] or not path.is_file():
            entries[key] = _ledger_entry(identity, status="BLOCKED", reason_code="RAW_SOURCE_UNAVAILABLE", error="artifact path is missing or unavailable")
            continue
        if not _is_hash(expected_hash):
            entries[key] = _ledger_entry(identity, status="BLOCKED", reason_code="ARTIFACT_HASH_MISSING", error="manifest must provide a SHA-256")
            continue
        actual_hash = artifact_sha256(path)
        if identity.get("content_sha256") and actual_hash != identity["content_sha256"]:
            entries[key] = _ledger_entry(identity, status="BLOCKED", reason_code="SOURCE_VERSION_HASH_MISMATCH", error=f"SourceVersion hash {identity['content_sha256']} != actual hash {actual_hash}")
            continue
        if actual_hash != expected_hash:
            entries[key] = _ledger_entry(identity, status="BLOCKED", reason_code="ARTIFACT_HASH_MISMATCH", error=f"manifest hash {expected_hash} != actual hash {actual_hash}")
            continue
        family = _family_for(identity["artifact_path"], identity["family"])
        if not family:
            entries[key] = _ledger_entry(identity, status="BLOCKED", reason_code="READER_FAMILY_UNKNOWN", error="artifact family cannot be inferred")
            continue
        identity["family"] = family
        reader_path = out / "artifacts" / _component(identity["stable_object_id"]) / _component(identity["source_version_id"]) / _component(expected_hash[:16])
        try:
            result = read_artifact(ReaderInput(path=str(path), source_object_id=identity["stable_object_id"], source_version=identity["source_version_id"], display_name=identity["display_name"], family=family, committed=True, raw_artifact_sha256=expected_hash), reader_path)
            result.source_version_linked = result.source_version == identity["source_version_id"] and result.source_object_id == identity["stable_object_id"]
            result.write_json(reader_path / "reader_result.json")
            entries[key] = _ledger_entry(identity, status="COMPLETED", result=result, result_path=reader_path / "reader_result.json")
            if result.raw_artifact_sha256 != expected_hash:
                entries[key]["status"] = "FAILED"
                entries[key]["reason_code"] = "READER_RAW_HASH_CONFLICT"
                entries[key]["error"] = "reader raw hash differs from manifest"
        except Exception as exc:
            entries[key] = _ledger_entry(identity, status="FAILED", reason_code="READER_EXCEPTION", error=str(exc))
        _atomic_json(checkpoint, _checkpoint_payload(manifest_hash, source_hash, entries.values()))

    for source in sources:
        source_key = (source["source_id"], source["source_version_id"])
        if source_key in matched_source_keys:
            continue
        identity = {**source, "artifact_path": "", "display_name": "", "family": "", "committed": False}
        if _key(identity) not in entries:
            entries[_key(identity)] = _ledger_entry(identity, status="BLOCKED", reason_code="ARTIFACT_MANIFEST_MISSING", error="SourceVersion has no artifact manifest entry")

    ledger = {
        "ledger_version": LEDGER_VERSION,
        "materializer_version": MATERIALIZER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": {"source_versions_sha256": source_hash, "artifact_manifest_sha256": manifest_hash, "source_version_count": len(sources), "artifact_manifest_count": len(artifacts)},
        "scope": "supplied SourceVersion + artifact manifest only",
        "entries": sorted(entries.values(), key=lambda x: x["entry_key"]),
        "summary": {"total": len(entries), "completed": sum(x["status"] == "COMPLETED" for x in entries.values()), "failed": sum(x["status"] == "FAILED" for x in entries.values()), "blocked": sum(x["status"] == "BLOCKED" for x in entries.values())},
    }
    _atomic_json(checkpoint, _checkpoint_payload(manifest_hash, source_hash, entries.values()))
    _atomic_json(out / "reader_artifact_ledger.json", ledger)
    return ledger


def _main() -> None:
    parser = argparse.ArgumentParser(description="Materialize reader evidence from a committed SourceVersion manifest")
    parser.add_argument("--source-version", required=True, type=Path, help="JSON SourceVersion object or array")
    parser.add_argument("--artifact-manifest", required=True, type=Path, help="JSON artifact manifest object or array")
    parser.add_argument("--output", required=True, type=Path, help="caller-owned Reader output directory")
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    ledger = materialize(load_source_versions(args.source_version), load_artifact_manifest(args.artifact_manifest), args.output, resume=not args.no_resume, checkpoint_path=args.checkpoint)
    print(json.dumps(ledger["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
