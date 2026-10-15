"""Fail-closed Reader Fidelity Gate."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from .contract import ReaderResult, artifact_sha256

FAMILIES = ("PPTX", "PDF", "DOCX", "XLSX", "AIRPAGE", "LEGACY")
VISUAL_FAMILIES = {"PPTX", "PDF", "DOCX", "XLSX"}


def _family(result: ReaderResult) -> str:
    return result.reader_type.upper()


def _is_hash(value: Any) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{64}", str(value or "")))


def _hash_matches(path: Path, expected: Any) -> bool:
    try:
        return path.is_file() and _is_hash(expected) and artifact_sha256(path) == str(expected)
    except OSError:
        return False


def _lineage_ok(result: ReaderResult) -> bool:
    if not (result.raw_source_available and result.source_version_linked and
            _is_hash(result.raw_artifact_sha256) and result.source_object_id and
            result.source_version and result.content_fingerprint):
        return False
    for item in result.derived_artifacts:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        path = Path(str(item["path"]))
        if (not _hash_matches(path, item.get("sha256")) or
                item.get("parent_raw_artifact_sha256") != result.raw_artifact_sha256 or
                item.get("source_version") != result.source_version):
            return False
    return True


def _visual_ok(result: ReaderResult) -> bool:
    if _family(result) not in VISUAL_FAMILIES:
        return True
    if not result.fidelity_flags.get("visual_evidence_available", result.fidelity_flags.get("visual_rendered", False)):
        return False
    if not result.visual_pages:
        return False
    return all(_hash_matches(Path(str(item.get("path", ""))), item.get("sha256")) for item in result.visual_pages)


def _structure_ok(result: ReaderResult) -> bool:
    flags = result.fidelity_flags
    required = {
        "PPTX": ("structure_parsed", "relationships_preserved", "image_evidence_accounted"),
        "PDF": ("text_layout_parsed", "page_envelopes", "annotations_preserved"),
        "DOCX": ("structure_parsed", "tables_parsed", "images_accounted", "drawings_accounted"),
        "XLSX": ("raw_data_fidelity", "formula_fidelity", "styles_accounted", "charts_accounted", "images_accounted"),
        "AIRPAGE": ("semantic_normalized", "block_table_link_hierarchy_preserved", "embedded_refs_preserved", "completeness_evidence", "stability_evidence"),
        "LEGACY": ("raw_canonical", "validated_derived_parse_route"),
    }.get(_family(result), ())
    return bool(required) and all(bool(flags.get(key)) for key in required)


def _contract(result: ReaderResult) -> tuple[dict[str, Any], list[str]]:
    family = _family(result)
    raw_available = bool(result.raw_source_available and _is_hash(result.raw_artifact_sha256))
    source_linked = bool(result.source_version_linked and result.source_object_id and result.source_version)
    structure = _structure_ok(result)
    visual_applicable = family in VISUAL_FAMILIES
    visual = _visual_ok(result) if visual_applicable else True
    hash_lineage = _lineage_ok(result)
    failure_explicit = bool(result.fidelity_flags.get("failure_explicit"))
    checks = {
        "RAW_SOURCE_AVAILABLE": "PASS" if raw_available else "FAIL",
        "SOURCE_VERSION_LINKED": "PASS" if source_linked else "FAIL",
        "STRUCTURE_PARSED": "PASS" if structure else "FAIL",
        "VISUAL_EVIDENCE_AVAILABLE": ("PASS" if visual else "FAIL") if visual_applicable else "NOT_APPLICABLE",
        "HASH_LINEAGE": "PASS" if hash_lineage else "FAIL",
        "FAILURE_EXPLICIT": "PASS" if failure_explicit else "FAIL",
        "visual_evidence_applicable": visual_applicable,
    }
    blockers: list[str] = []
    if not raw_available: blockers.append("RAW_SOURCE_AVAILABLE failed")
    if not source_linked: blockers.append("SOURCE_VERSION_LINKED failed")
    if not structure:
        required = {
            "PPTX": ("structure_parsed", "relationships_preserved", "image_evidence_accounted"),
            "PDF": ("text_layout_parsed", "page_envelopes", "annotations_preserved"),
            "DOCX": ("structure_parsed", "tables_parsed", "images_accounted", "drawings_accounted"),
            "XLSX": ("raw_data_fidelity", "formula_fidelity", "styles_accounted", "charts_accounted", "images_accounted"),
            "AIRPAGE": ("semantic_normalized", "block_table_link_hierarchy_preserved", "embedded_refs_preserved", "completeness_evidence", "stability_evidence"),
            "LEGACY": ("raw_canonical", "validated_derived_parse_route"),
        }.get(family, ())
        missing = [key for key in required if not bool(result.fidelity_flags.get(key))]
        detail = f": {','.join(missing)}" if missing else ""
        blockers.append(f"{family} structure not parsed or required structure evidence missing{detail}")
    if visual_applicable and not visual:
        blockers.append(f"{family} visual evidence unavailable")
        if family == "DOCX": blockers.append("DOCX visual pagination unavailable")
    if not hash_lineage: blockers.append("HASH_LINEAGE failed")
    if not failure_explicit: blockers.append("FAILURE_EXPLICIT missing")
    return checks, blockers


def _status(result: ReaderResult) -> tuple[str, list[str], dict[str, Any]]:
    checks, blockers = _contract(result)
    return ("FAIL" if blockers else "PASS"), blockers, checks


def _required_source_key(item: Mapping[str, Any]) -> tuple[str, str]:
    """Resolve a required source identity without confusing version aliases.

    ``source_version_id`` is the canonical identity.  The shorter version
    fields are legacy aliases and are only consulted when the canonical field
    is absent, never when it conflicts with one.
    """
    object_id = next(
        (str(item.get(name)) for name in ("source_object_id", "source_id", "object_id", "stable_object_id")
         if item.get(name) not in (None, "")),
        "",
    )
    canonical = item.get("source_version_id")
    if canonical not in (None, ""):
        version = str(canonical)
    else:
        version = next(
            (str(item.get(name)) for name in ("source_version", "version", "version_marker")
             if item.get(name) not in (None, "")),
            "",
        )
    return object_id, version


def evaluate_gate(results: Iterable[ReaderResult], *, required_source_versions: Iterable[dict[str, Any]] | None = None, full_corpus: bool = False) -> dict[str, Any]:
    """Evaluate supplied evidence only. Any unmet contract is FAIL."""
    results = list(results)
    counts: dict[str, Counter[str]] = {f: Counter() for f in FAMILIES}
    blockers: list[str] = []
    statuses: list[str] = []
    evidence_checks: list[dict[str, Any]] = []
    if not results:
        blockers.append("no_reader_results")
    for result in results:
        family = _family(result)
        if family not in counts:
            blockers.append(f"unknown_family:{family}")
            continue
        status, reasons, checks = _status(result)
        statuses.append(status)
        counts[family]["total"] += 1
        counts[family]["pass" if status == "PASS" else "blocked"] += 1
        evidence_checks.append({
            "source_object_id": result.source_object_id, "source_version": result.source_version,
            "reader_type": family, "checks": checks, "status": status,
            "failure_reasons": reasons, "reader_version": result.reader_version,
            "parser_version": result.parser_version,
        })
        blockers.extend(f"{result.source_object_id}@{result.source_version}:{x}" for x in reasons)
    seen = {(r.source_object_id, r.source_version) for r in results}
    for item in list(required_source_versions or []):
        key = _required_source_key(item)
        if key not in seen: blockers.append(f"missing_required_source_version:{key[0]}@{key[1]}")
    overall = "FAIL" if blockers or any(x != "PASS" for x in statuses) else "PASS"
    values = [x["checks"] for x in evidence_checks]
    aggregate = lambda name: "PASS" if values and all(x.get(name) == "PASS" for x in values) else ("NOT_APPLICABLE" if not values else "FAIL")
    visual_results = [x for x in evidence_checks if x["reader_type"] in VISUAL_FAMILIES]
    visual_aggregate = "PASS" if visual_results and all(x["checks"]["VISUAL_EVIDENCE_AVAILABLE"] == "PASS" for x in visual_results) else ("NOT_APPLICABLE" if not visual_results else "FAIL")
    pptx = [r for r in results if _family(r) == "PPTX"]
    charts = [r for r in results if _family(r) == "PPTX" and r.fidelity_flags.get("chart_count", 0)] + [r for r in results if _family(r) == "XLSX" and r.fidelity_flags.get("chart_count", 0)]
    return {
        "READER_FIDELITY_GATE": overall, "GATE_MODE": "FAIL_CLOSED", "TOTAL_SOURCE_VERSIONS": len(results),
        **{f"{f}_TOTAL": counts[f]["total"] for f in FAMILIES}, **{f"{f}_PASS": counts[f]["pass"] for f in FAMILIES},
        **{f"{f}_BLOCKED": counts[f]["blocked"] for f in FAMILIES},
        "RAW_SOURCE_AVAILABLE": aggregate("RAW_SOURCE_AVAILABLE"), "SOURCE_VERSION_LINKED": aggregate("SOURCE_VERSION_LINKED"),
        "STRUCTURE_PARSED": aggregate("STRUCTURE_PARSED"), "VISUAL_EVIDENCE_AVAILABLE": visual_aggregate,
        "HASH_LINEAGE": aggregate("HASH_LINEAGE"), "FAILURE_EXPLICIT": aggregate("FAILURE_EXPLICIT"),
        "VISUAL_RENDER_REQUIRED": sum(len(r.visual_pages) for r in results if _family(r) in VISUAL_FAMILIES),
        "VISUAL_RENDER_VALIDATED": sum(len(r.visual_pages) for r in results),
        "RELATIONSHIP_PRESERVATION": "PASS" if pptx and all(r.fidelity_flags.get("relationships_preserved") for r in pptx) else ("PASS" if not pptx else "FAIL"),
        "CHART_DATA_PRESERVATION": "PASS" if all(r.fidelity_flags.get("chart_data_preserved", r.fidelity_flags.get("charts_accounted", False)) and r.fidelity_flags.get("chart_visual_evidence", False) for r in charts) else ("PASS" if not charts else "FAIL"),
        "SPREADSHEET_DATA_FIDELITY": "PASS" if all(r.fidelity_flags.get("raw_data_fidelity") and r.fidelity_flags.get("formula_fidelity") for r in results if _family(r) == "XLSX") else ("PASS" if not any(_family(r) == "XLSX" for r in results) else "FAIL"),
        "EVIDENCE_CHECKS": evidence_checks, "BLOCKERS": blockers,
        "NEXT_STEP": "resolve blockers and rerun Reader Fidelity Gate" if full_corpus else "after Full Mirror PASS, materialize all included SourceVersions and run Reader Fidelity Gate",
    }
