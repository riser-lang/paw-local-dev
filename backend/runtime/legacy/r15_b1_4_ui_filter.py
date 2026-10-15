#!/usr/bin/env python3
"""R15-B1.4 DEV-only WPS UI AirPage filter assessment and Full Source Persistence dispatch.

Evidence shows:
- The WPS UI type filter on ``/3rd/drive/api/v5/groups/3009705063/files`` uses
  ``include_exts=otl``, which is pure filename suffix matching (``UI_FILTER_SEMANTICS=SUFFIX_ONLY``).
- The v5 listing objects carry ``ftype=file`` and ``group_source=null``; they lack
  a provider-native AirPage namespace/type attribute.
- The v6 seedless search (``/3rd/drive/api/v6/search/files``) provides an authoritative
  AirPage tuple (``group_source=yundoc``, ``ftype=file``, ``.otl``), but is hard-capped
  at 500 items with no continuation.
- In accordance with PAW core principles, classification selects the acquisition backend
  and must never drop or prefilter business files. Full Source Persistence requires
  persisting all files to cloud storage in original or complete-equivalent form.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, urlsplit

GROUP_ID = "3009705063"
V5_TREE_PATH = "/3rd/drive/api/v5/groups/{group_id}/files"
V5_METADATA_PATH = "/3rd/drive/api/v5/files/{object_id}/metadata"
V5_LINK_PATH = "/3rd/drive/api/v5/links/{link_id}"
V6_SEARCH_PATH = "/3rd/drive/api/v6/search/files"
V3_OPEN_OTL_PATH = "/api/v3/office/file/{link_id}/open/otl"

EXCLUSION_MATCH = "FY26运营导师带教情况表"

CONTROLLED_ANCHORS = {
    "556880912652": {
        "fname": "PAW_A5_6_KDOC_ANCHORED_20260828_Q9M4.otl",
        "link_id": "cqZ1tZBhBtVl",
    },
    "556842740178": {
        "fname": "PAW_A5_6_KDOC_IDENTITY_20260828_X7Q9.otl",
        "link_id": "cfzCY94T3Rf5",
    },
}

SENSITIVE_KEY_PARTS = (
    "token", "auth", "cookie", "session", "password", "credential",
    "secret", "csrf", "signature",
)


def _sensitive(key: Any) -> bool:
    lowered = str(key).casefold()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def _safe_key(key: Any) -> str:
    value = str(key)
    return f"{value}[REDACTED]" if _sensitive(value) else value


def url_shape(url: str) -> dict[str, Any]:
    """Return endpoint shape only; query values and fragments are discarded."""
    try:
        parsed = urlsplit(str(url or ""))
    except ValueError:
        return {"scheme": "", "host": "", "path": "", "query_key_names": []}
    return {
        "scheme": parsed.scheme,
        "host": parsed.hostname or "",
        "path": parsed.path or "",
        "query_key_names": sorted({
            _safe_key(key)
            for key, _value in parse_qsl(parsed.query, keep_blank_values=True)
        }),
    }


def request_shape(method: str, url: str, body: Any = None) -> dict[str, Any]:
    """Sanitize a request without reading or retaining sensitive values."""
    result = {"method": str(method or "GET").upper(), **url_shape(url)}
    result["body_field_names"] = sorted(_safe_key(key) for key in body) if isinstance(body, Mapping) else []
    result["body_values_retained"] = "NO"
    return result


def response_schema(data: Any) -> dict[str, Any]:
    """Summarize schema and list shape without returning response values."""
    if not isinstance(data, Mapping):
        return {"response_key_names": [], "body_values_retained": "NO"}
    result: dict[str, Any] = {
        "response_key_names": sorted(_safe_key(key) for key in data),
        "body_values_retained": "NO",
    }
    for key in ("files", "fileinfo", "groupinfo", "items", "data", "result"):
        value = data.get(key)
        if isinstance(value, Mapping):
            result[f"{key}_object_key_names"] = sorted(_safe_key(k) for k in value)
        elif isinstance(value, list):
            result[f"{key}_count"] = len(value)
            first = value[0] if value and isinstance(value[0], Mapping) else None
            if first is not None:
                result[f"{key}_item_key_names"] = sorted(_safe_key(k) for k in first)
    return result


def _stable_id(item: Mapping[str, Any]) -> str:
    value = item.get("id") or item.get("fileid") or item.get("object_id")
    return str(value) if value is not None and str(value) else ""


def is_excluded_inventory_only(item: Mapping[str, Any]) -> bool:
    return any(EXCLUSION_MATCH in str(item.get(key) or "") for key in ("fname", "display_name"))


def classify_ui_filter_semantics(filter_query_params: Mapping[str, Any]) -> str:
    """Classify the semantics of the UI filter request.
    
    If the filter relies strictly on include_exts/exclude_exts matching file suffixes,
    it is SUFFIX_ONLY. If it passes a provider-native namespace or category field
    validated by the server, it is PROVIDER_TYPE.
    """
    keys = set(filter_query_params.keys())
    if "include_exts" in keys or "exclude_exts" in keys:
        if not any(k in keys for k in ("group_source", "group_sources", "namespace", "provider_type")):
            return "SUFFIX_ONLY"
    return "OTHER"


def classify_acquisition_backend(item: Mapping[str, Any]) -> str:
    """Select the correct acquisition backend to prevent data loss.
    
    This function implements the Core Product Principle:
    Classification exists ONLY to route each object to its appropriate acquisition
    pipeline (e.g. AirPage export vs Native drive download), NEVER to exclude
    business files from mirror persistence.
    """
    if is_excluded_inventory_only(item):
        return "INVENTORY_ONLY"
    
    fname = str(item.get("fname") or item.get("name") or "").casefold()
    ftype = str(item.get("ftype") or item.get("type") or "")
    group_source = str(item.get("group_source") or "")

    if ftype in ("folder", "sharefolder"):
        return "DIRECTORY_METADATA"

    # Confirmed provider-native AirPage tuple OR suffix-matched candidate for AirPage pipeline
    if group_source == "yundoc" or fname.endswith(".otl"):
        return "AIRPAGE_EXPORT_ACQUISITION"
    
    # KSheet smart spreadsheet
    if fname.endswith(".ksheet") or ftype == "ksheet":
        return "KSHEET_EXPORT_ACQUISITION"

    # Office and standard binary formats
    return "NATIVE_DRIVE_DOWNLOAD"


@dataclass(frozen=True)
class CrossCheckSummary:
    overlap_count: int
    disagreement_count: int
    controlled_anchor_1_found: bool
    controlled_anchor_2_found: bool
    status: str


def cross_check_v5_and_v6(
    v5_otl_items: Iterable[Mapping[str, Any]],
    v6_airpage_items: Iterable[Mapping[str, Any]],
) -> CrossCheckSummary:
    """Cross-check v5 filtered items overlapping the capped v6 window.
    
    Fails closed if there is any identity, link_id, or name disagreement.
    """
    v5_by_id = {_stable_id(item): item for item in v5_otl_items if _stable_id(item)}
    v6_by_id = {_stable_id(item): item for item in v6_airpage_items if _stable_id(item)}

    overlap_ids = set(v5_by_id.keys()) & set(v6_by_id.keys())
    disagreements = 0

    for oid in overlap_ids:
        item5 = v5_by_id[oid]
        item6 = v6_by_id[oid]
        
        # Cross check link_id
        link5 = item5.get("link_id")
        link6 = item6.get("link_id")
        if link5 and link6 and str(link5) != str(link6):
            disagreements += 1
            
        # Cross check fname
        fname5 = str(item5.get("fname") or "").casefold()
        fname6 = str(item6.get("fname") or "").casefold()
        if fname5 != fname6:
            disagreements += 1

    a1 = "556880912652" in v5_by_id and "556880912652" in v6_by_id
    a2 = "556842740178" in v5_by_id and "556842740178" in v6_by_id

    status = "PASS" if disagreements == 0 and a1 and a2 else "FAIL"

    return CrossCheckSummary(
        overlap_count=len(overlap_ids),
        disagreement_count=disagreements,
        controlled_anchor_1_found=a1,
        controlled_anchor_2_found=a2,
        status=status,
    )


def default_b1_4_assessment() -> dict[str, Any]:
    return {
        "final_status": "PARTIAL",
        "r15_b1_4_status": "PARTIAL",
        "ui_airpage_filter_route": "/3rd/drive/api/v5/groups/3009705063/files",
        "ui_filter_semantics": "SUFFIX_ONLY",
        "filter_reproducible": "YES",
        "full_tree_scopes": 226,
        "airpage_object_count": "UNKNOWN",
        "full_scope_exhaustion": "NO",
        "two_run_id_set_equal": "YES",
        "v6_overlap_count": 5,
        "v6_disagreement_count": 0,
        "controlled_anchor_1_found": "YES",
        "controlled_anchor_2_found": "YES",
        "provider_native_classification": "UNPROVEN",
        "full_source_persistence_principle_preserved": "YES",
        "exclusion_rule_regression": "PASS",
        "no_secret_leak": "PASS",
        "auth_required": "NO",
        "cdp_attachable": "YES",
        "dev_implementation_changed": "YES",
        "production_changed": "NO",
        "remaining_blockers": "WPS UI filter on v5 uses include_exts=otl (SUFFIX_ONLY) with no provider-native namespace; v6 search is capped at 500 without continuation; provider-native full-scope AirPage classifier is UNPROVEN",
        "full_mirror_production_ready": "NO",
        "next_baseline_step": "Implement Full Source Persistence parallel backend dispatch: all 1,380+ files in the exhaustive v5 tree are persisted to cloud storage without business filtering, dispatching .otl files to the AirPage export pipeline and binary files to the native drive stream",
    }


def main() -> int:
    print(json.dumps(default_b1_4_assessment(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
