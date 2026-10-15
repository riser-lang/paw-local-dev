#!/usr/bin/env python3
"""R15-B1.3 DEV-only Smart Document identity assessment.

This module is an assessment/join boundary, not a production mirror path.
It accepts the already-proven v6 provider tuple, but refuses to promote a v5
tree object unless an authoritative per-object namespace and type signal is
present.  ``.otl`` and ``ftype=file`` are never enough by themselves.
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
UI_FILTER_SETTINGS_PATH = "/kfc/miniprovider/v1/navigation/filter/settings"
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
    """Sanitize a request without reading or retaining headers or values."""
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


def classify_v6_provider_item(item: Mapping[str, Any], provider_path: str = V6_SEARCH_PATH) -> str:
    """Use the proven v6 tuple only; all other routes fail closed."""
    if provider_path != V6_SEARCH_PATH:
        return "UNKNOWN"
    if (
        item.get("group_source") == "yundoc"
        and item.get("ftype") == "file"
        and isinstance(item.get("fname"), str)
        and item["fname"]
        and item["fname"].casefold().endswith(".otl")
    ):
        return "AIRPAGE"
    return "UNKNOWN"


def enrich_v5_object(
    tree_item: Mapping[str, Any],
    metadata: Mapping[str, Any] | None,
    metadata_path: str = V5_METADATA_PATH,
) -> dict[str, Any]:
    """Join one v5 object with metadata, promoting only authoritative evidence."""
    object_id = _stable_id(tree_item)
    link_id = tree_item.get("link_id")
    excluded = is_excluded_inventory_only(tree_item)
    result: dict[str, Any] = {
        "object_id": object_id,
        "link_id": link_id,
        "classification": "INVENTORY_ONLY" if excluded else "UNKNOWN",
        "reason": "EXCLUSION_RULE" if excluded else "MISSING_OR_UNPROVEN_METADATA",
        "metadata_path": metadata_path,
    }
    if not object_id:
        result.update(classification="UNKNOWN", reason="STABLE_ID_MISSING")
        return result
    if excluded or metadata_path != V5_METADATA_PATH or not isinstance(metadata, Mapping):
        return result

    fileinfo = metadata.get("fileinfo")
    if not isinstance(fileinfo, Mapping):
        result["reason"] = "FILEINFO_MISSING"
        return result
    metadata_id = str(fileinfo.get("fileid") or fileinfo.get("id") or "")
    if metadata_id != object_id:
        result["reason"] = "STABLE_ID_MISMATCH"
        return result
    metadata_link = fileinfo.get("link_id")
    if link_id and metadata_link and str(link_id) != str(metadata_link):
        result["reason"] = "LINK_ID_MISMATCH"
        return result

    # The live v5 metadata has neither group_source nor an equivalent field.
    if fileinfo.get("group_source") != "yundoc" or fileinfo.get("ftype") != "file":
        result["reason"] = "AUTHORITATIVE_NAMESPACE_OR_TYPE_MISSING"
        return result
    result.update(classification="AIRPAGE", reason="PROVIDER_NATIVE_METADATA")
    return result


def _id_set_sha256(ids: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(sorted(str(value) for value in ids)).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class JoinSummary:
    status: str
    input_count: int
    output_count: int
    airpage_ids: tuple[str, ...]
    duplicate_id_count: int
    missing_enrichment_count: int
    loss_count: int
    excluded_inventory_only_count: int
    id_set_sha256: str


def join_v5_tree(
    tree_items: Iterable[Mapping[str, Any]],
    enrichment_by_id: Mapping[str, Mapping[str, Any] | None],
) -> JoinSummary:
    """Perform a bounded, idempotent, loss-reconciling DEV enrichment join."""
    items = list(tree_items)
    ids = [_stable_id(item) for item in items]
    duplicate_count = len(ids) - len(set(ids))
    if any(not value for value in ids) or duplicate_count:
        return JoinSummary("BLOCKED", len(items), 0, (), duplicate_count, 0, len(items), 0, _id_set_sha256(()))

    airpage_ids: list[str] = []
    missing = 0
    excluded = 0
    for item in items:
        object_id = _stable_id(item)
        if is_excluded_inventory_only(item):
            excluded += 1
            continue
        if object_id not in enrichment_by_id:
            missing += 1
            continue
        enriched = enrich_v5_object(item, enrichment_by_id[object_id])
        if enriched["classification"] == "AIRPAGE":
            airpage_ids.append(object_id)

    return JoinSummary(
        "PASS" if missing == 0 else "BLOCKED",
        len(items),
        len(items),
        tuple(sorted(airpage_ids)),
        0,
        missing,
        0,
        excluded,
        _id_set_sha256(airpage_ids),
    )


def compare_two_joins(first: JoinSummary, second: JoinSummary) -> dict[str, str]:
    equal = first.airpage_ids == second.airpage_ids and first.id_set_sha256 == second.id_set_sha256
    return {
        "two_run_airpage_id_set_equal": "YES" if equal else "NO",
        "run_1_status": first.status,
        "run_2_status": second.status,
    }


def default_partial_assessment() -> dict[str, Any]:
    return {
        "status": "PARTIAL",
        "identity_method": "NONE",
        "provider_native_airpage_identity": "UNPROVEN",
        "full_scope_classification": "UNPROVEN",
        "two_run_airpage_id_set_equal": "NOT_RUN",
        "airpage_object_count": "UNKNOWN",
        "promotion_allowed": "NO",
        "production_changed": "NO",
    }


def main() -> int:
    print(json.dumps(default_partial_assessment(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
