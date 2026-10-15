#!/usr/bin/env python3
"""R15-B1.5 full-source persistence and dispatch contracts.

Semantic classification is authoritative only when evidence proves it;
acquisition routing is a separate, loss-preventing decision.  In particular,
``.otl`` is routed to the proven AirPage DOCX export adapter while provider-
native AirPage classification remains UNPROVEN.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from urllib.parse import urlsplit

from wps_file_registry import (
    MIRROR_BACKEND_STATUS_REGISTRY, classify_file, derive_acquisition_route,
    is_backend_acquisition_ready, is_registered_mirror_backend,
    semantic_classification,
    USER_DECLARED_EXCLUDED_UI_CATEGORIES,
    USER_DECLARED_INCLUDED_UI_CATEGORIES,
    is_user_declared_no_mirror_ui_category,
)
from wps_inventory_v1 import evaluate_mirror_policy

EXACT_READY_BACKENDS = frozenset({
    "NATIVE_XLSX_DIRECT", "NATIVE_DOCX_DIRECT", "NATIVE_PPTX_DIRECT",
    "PDF_DIRECT", "WPS_SPREADSHEET_EXPORT_XLSX", "WPS_DOCUMENT_EXPORT_DOCX",
})
FILE_TYPE_TO_BACKEND = {
    "NATIVE_XLSX": "NATIVE_XLSX_DIRECT", "NATIVE_DOCX": "NATIVE_DOCX_DIRECT",
    "NATIVE_PPTX": "NATIVE_PPTX_DIRECT", "PDF": "PDF_DIRECT",
    "WPS_SPREADSHEET": "WPS_SPREADSHEET_EXPORT_XLSX",
    "WPS_DOCUMENT": "WPS_DOCUMENT_EXPORT_DOCX",
}
BACKEND_FORMATS = {
    "NATIVE_XLSX_DIRECT": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "NATIVE_DOCX_DIRECT": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "NATIVE_PPTX_DIRECT": ("pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    "PDF_DIRECT": ("pdf", "application/pdf"),
    "WPS_SPREADSHEET_EXPORT_XLSX": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "WPS_DOCUMENT_EXPORT_DOCX": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "RAW_BINARY_DIRECT": ("bin", "application/octet-stream"),
}
# Deterministic matrix for the exhaustive v5 ordinary-tree surface.  The
# catch-all applies to legacy/native formats, standalone media, and OTHER
# extensions; it never means an implicit drop.
FULL_SOURCE_ACQUISITION_MATRIX = {
    ".otl": "AIRPAGE_EXPORT_DOCX",
    ".ksheet": "KSHEET_EXPORT_XLSX",
    ".xlsx": "NATIVE_DIRECT_BINARY",
    ".docx": "NATIVE_DIRECT_BINARY",
    ".pptx": "NATIVE_DIRECT_BINARY",
    ".pdf": "NATIVE_DIRECT_BINARY",
    ".xls": "RAW_BINARY_FALLBACK",
    ".doc": "RAW_BINARY_FALLBACK",
    ".ppt": "RAW_BINARY_FALLBACK",
    "MEDIA_IMAGE": "RAW_BINARY_FALLBACK",
    "MEDIA_VIDEO": "RAW_BINARY_FALLBACK",
    "MEDIA_AUDIO": "RAW_BINARY_FALLBACK",
    "OTHER": "RAW_BINARY_FALLBACK",
}
EXCLUSION_RULE_ID = "WPS_MIRROR_EXCLUSION_RULE_001"


@dataclass(frozen=True)
class DispatchResult:
    status: str
    file_type: str
    backend: str
    reason: str = ""
    acquisition_route: str = ""
    routing_evidence: str = ""
    semantic_classification: str = "UNKNOWN"


@dataclass(frozen=True)
class ProductionEligibility:
    eligible: bool
    status: str
    object_id: str
    file_type: str
    backend: str
    reason: str = ""
    exclusion_rule: str = ""
    semantic_classification: str = "UNKNOWN"
    acquisition_route: str = ""
    routing_evidence: str = ""


def dispatch_backend(file_type: str) -> DispatchResult:
    """Legacy semantic dispatch retained for A5 acceptance callers."""
    file_type = str(file_type or "")
    backend = FILE_TYPE_TO_BACKEND.get(file_type)
    if backend:
        return DispatchResult("DISPATCHED", file_type, backend)
    if file_type == "UNKNOWN" or not file_type:
        return DispatchResult("NEEDS_CLASSIFICATION", file_type or "UNKNOWN", "NEEDS_CLASSIFICATION", "UNKNOWN_FILE_TYPE")
    return DispatchResult("BLOCKED_BACKEND", file_type, "BLOCKED_BACKEND", "NO_APPROVED_BACKEND")


def dispatch_acquisition_route(record: Mapping[str, Any]) -> DispatchResult:
    """Dispatch one object by persistence route, not semantic type."""
    value = dict(record) if isinstance(record, Mapping) else record
    route = derive_acquisition_route(value)
    semantic, _family = semantic_classification(value if isinstance(value, dict) else {})
    if route["route"] == "INVENTORY_ONLY":
        return DispatchResult("EXCLUDED", semantic, route["backend"], route["evidence"], route["route"], route["evidence"], semantic)
    if route["route"] in {"BLOCKED", "DIRECTORY_METADATA"}:
        return DispatchResult("BLOCKED_BACKEND", semantic, route["backend"], route["evidence"], route["route"], route["evidence"], semantic)
    return DispatchResult("DISPATCHED", semantic, route["backend"], "", route["route"], route["evidence"], semantic)


def _iso_timestamp(value: Any) -> bool:
    if not value:
        return False
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return True


def _provider_metadata(record: Mapping[str, Any]) -> dict[str, Any]:
    nested = record.get("provider_metadata")
    if isinstance(nested, Mapping):
        return dict(nested)
    return {
        "provider_path": record.get("provider_path"),
        "provider_namespace": record.get("provider_namespace", record.get("group_source")),
        "provider_object_type": record.get("provider_object_type", record.get("ftype")),
        "provider_filename": record.get("provider_filename", record.get("filename", record.get("display_name"))),
    }


def trusted_file_type(record: Mapping[str, Any]) -> bool:
    """Validate semantic authority; this is not an acquisition prerequisite."""
    file_type = str(record.get("file_type") or "")
    if file_type not in FILE_TYPE_TO_BACKEND:
        return False
    detected, _family = classify_file(record.get("filename"), record.get("extension"), _provider_metadata(record))
    return detected == file_type


def _stable_locator(record: Mapping[str, Any]) -> str:
    locator = str(record.get("cloud_locator") or record.get("locator") or record.get("link_url") or "")
    if not locator and record.get("link_id"):
        locator = "https://365.kdocs.cn/l/" + str(record["link_id"])
    return locator


def source_identity_available(record: Mapping[str, Any], backend: str) -> bool:
    object_id = str(record.get("object_id") or "")
    version = str(record.get("version", record.get("source_version", "")) or "")
    if not object_id or not version:
        return False
    if backend in {"WPS_SPREADSHEET_EXPORT_XLSX", "WPS_DOCUMENT_EXPORT_DOCX", "RAW_BINARY_DIRECT"} and not str(record.get("parent_id") or ""):
        return False
    if backend == "WPS_DOCUMENT_EXPORT_DOCX":
        try:
            parts = urlsplit(_stable_locator(record))
        except ValueError:
            return False
        if not (parts.scheme == "https" and str(parts.hostname or "").casefold() in {"kdocs.cn", "www.kdocs.cn", "365.kdocs.cn"} and parts.path.startswith("/l/") and len(parts.path.split("/")) == 3 and bool(parts.path.rsplit("/", 1)[-1]) and not parts.query and not parts.fragment):
            return False
    return True


def _blocked(object_id, file_type, backend, reason, semantic, route, evidence, rule="", status="BLOCKED_BACKEND"):
    return ProductionEligibility(False, status, object_id, file_type, backend, reason, rule, semantic, route, evidence)


def production_eligibility(record: Mapping[str, Any]) -> ProductionEligibility:
    """Evaluate the fail-closed source persistence contract."""
    if not isinstance(record, Mapping):
        return _blocked("", "UNKNOWN", "BLOCKED_BACKEND", "INVALID_OBJECT_RECORD", "UNKNOWN", "", "")
    object_id = str(record.get("object_id") or "")
    policy, rule_id, _reason = evaluate_mirror_policy(record.get("display_name"), record.get("filename"))
    semantic, _family = semantic_classification(dict(record))
    dispatch = dispatch_acquisition_route(record)
    route, backend, evidence = dispatch.acquisition_route, dispatch.backend, dispatch.routing_evidence
    file_type = semantic
    if dispatch.status == "EXCLUDED":
        return _blocked(object_id, file_type, "NO_MIRROR", "EXCLUSION_RULE", semantic, route, evidence, rule_id or EXCLUSION_RULE_ID, "EXCLUDED")
    if record.get("object_type") != "file":
        return _blocked(object_id, file_type, "BLOCKED_BACKEND", "OBJECT_NOT_FILE", semantic, route, evidence)
    if dispatch.status != "DISPATCHED":
        return _blocked(object_id, file_type, "BLOCKED_BACKEND", dispatch.reason or "NO_ACQUISITION_ROUTE", semantic, route, evidence)
    if policy != "INCLUDED":
        return _blocked(object_id, file_type, "BLOCKED_BACKEND", "MIRROR_POLICY_NOT_INCLUDED", semantic, route, evidence)
    if not _iso_timestamp(record.get("mirror_policy_evaluated_at")):
        return _blocked(object_id, file_type, backend, "MIRROR_POLICY_NOT_EVALUATED", semantic, route, evidence)
    if not object_id:
        return _blocked(object_id, file_type, backend, "STABLE_OBJECT_ID_REQUIRED", semantic, route, evidence)
    candidate = record.get("mirror_backend_candidate")
    if candidate and candidate not in {backend, "NEEDS_CLASSIFICATION", "UNKNOWN", ""}:
        return _blocked(object_id, file_type, backend, "BACKEND_ROUTE_MISMATCH", semantic, route, evidence)
    if backend == "RAW_BINARY_DIRECT":
        if record.get("raw_download_capability_proven") is not True:
            return _blocked(object_id, file_type, backend, "RAW_BINARY_CAPABILITY_UNPROVEN", semantic, route, evidence)
    elif backend not in EXACT_READY_BACKENDS:
        return _blocked(object_id, file_type, backend, "BACKEND_NOT_IN_APPROVED_SET", semantic, route, evidence)
    status = MIRROR_BACKEND_STATUS_REGISTRY.get(backend)
    if not is_registered_mirror_backend(backend) or not status or status.get("registered") is not True:
        return _blocked(object_id, file_type, backend, "BACKEND_NOT_REGISTERED", semantic, route, evidence)
    if backend != "RAW_BINARY_DIRECT" and not is_backend_acquisition_ready(backend):
        return _blocked(object_id, file_type, backend, "BACKEND_NOT_READY", semantic, route, evidence)
    if not source_identity_available(record, backend):
        return _blocked(object_id, file_type, backend, "SOURCE_IDENTITY_UNAVAILABLE", semantic, route, evidence)
    return ProductionEligibility(True, "ELIGIBLE", object_id, file_type, backend, "", "", semantic, route, evidence)


def exact_ready_set_is_valid() -> bool:
    actual = frozenset(name for name, status in MIRROR_BACKEND_STATUS_REGISTRY.items() if status.get("acquisition_ready") is True)
    return actual == EXACT_READY_BACKENDS
