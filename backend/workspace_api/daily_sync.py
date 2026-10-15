from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from . import repository as repo
from .time_utils import business_datetime


BASE = "http://127.0.0.1:8787"
DEVICE_ID = "office-pc-01"
TERMINAL = {"succeeded", "waiting_adapter", "waiting_workbuddy", "waiting_quota", "failed", "cancelled"}

LEGACY_WPS_ERRORS = {
    "WORKBUDDY_UNAVAILABLE": "WPS_ADAPTER_UNAVAILABLE",
    "WORKBUDDY_AUTH_UNAVAILABLE": "WPS_AUTH_UNAVAILABLE",
    "MCP_DISCOVERY_FAILED": "WPS_ADAPTER_UNAVAILABLE",
    "MCP_HANDSHAKE_FAILED": "WPS_ADAPTER_UNAVAILABLE",
    "MCP_TOOL_NOT_AVAILABLE": "WPS_ADAPTER_UNAVAILABLE",
}


class DailySyncError(RuntimeError):
    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.code = code


def sync_capture(capture_id: str) -> dict[str, Any] | None:
    capture = repo.capture_for_daily_sync(capture_id)
    if capture is None:
        return None
    if capture["sync_status"] == "synced" and capture.get("wps_document_id"):
        return {"synced": True, "already_synced": True, "created": False, "appended": False}

    # Canonical Daily already exists (created when the Capture was created).
    # WPS replication is OPTIONAL: a WPS failure must NOT fail the canonical
    # Capture -> Daily main flow. We attempt the WPS replica and, on any
    # WPS/bridge failure, record the replica as unavailable and still return a
    # successful canonical result.
    title = f"{capture['date']} Daily"
    captured = business_datetime(capture["captured_at"])
    entry = f"### {captured:%H:%M}\n\n{capture['raw_text']}"
    document_id = capture.get("wps_document_id")
    created = False
    appended = False
    wps_replica = "unavailable"

    try:
        if not document_id:
            root = _run_job("wps.list", {"scope": "personal", "limit": 50})
            daily_folder = _unique(
                root.get("items") or [],
                lambda item: item.get("type") == "folder"
                and str(item.get("title") or "").casefold() == "daily",
                "Daily folder",
            )
            drive_id = daily_folder.get("drive_id")
            folder_id = daily_folder.get("document_id")
            if not drive_id or not folder_id:
                raise DailySyncError("Daily folder lacks drive/document identifier")
            listed = _run_job(
                "wps.list",
                {"scope": "personal", "drive_id": drive_id, "parent_id": folder_id, "limit": 50},
            )
            matches = [
                item for item in listed.get("items") or []
                if str(item.get("title") or "").casefold() == f"{title}.otl".casefold()
            ]
            if len(matches) > 1:
                raise DailySyncError("Multiple WPS Daily documents match today's title")
            if matches:
                document_id = matches[0].get("document_id")
            else:
                result = _run_job(
                    "wps.create",
                    {
                        "title": title,
                        "content": f"# {title}\n\n## 原始活动流\n\n{entry}",
                        "idempotency_key": f"daily-create-{capture_id}",
                        "drive_id": drive_id,
                        "parent_id": folder_id,
                        "extension": "otl",
                    },
                )
                document_id = _find_identifier(result)
                created = True
        if not document_id:
            raise DailySyncError("WPS Daily write returned no document identifier")

        current = _run_job("wps.read", {"document_id": document_id, "max_chars": 200_000})
        if entry not in str(current.get("content") or ""):
            _run_job(
                "wps.append",
                {
                    "document_id": document_id,
                    "content": "\n\n" + entry,
                    "idempotency_key": f"daily-append-{capture_id}",
                },
            )
            appended = True
            current = _run_job("wps.read", {"document_id": document_id, "max_chars": 200_000})
        if entry not in str(current.get("content") or ""):
            raise DailySyncError("WPS Daily read-back did not contain the exact raw Capture")

        repo.mark_capture_synced(capture_id, document_id)
        wps_replica = "synced"
    except DailySyncError as exc:
        # WPS is optional. The canonical Daily already exists; do not fail the
        # main action because the WPS replica is temporarily unavailable.
        repo.mark_capture_wps_unavailable(capture_id)
        wps_replica = "unavailable"

    return {
        "synced": True,
        "canonical": True,
        "already_synced": False,
        "created": created,
        "appended": appended,
        "wps_replica": wps_replica,
    }


_bridge_store = None


def set_bridge_store(store) -> None:
    global _bridge_store
    _bridge_store = store


def _run_job(operation: str, params: dict) -> dict:
    if _bridge_store is None:
        raise DailySyncError("Bridge store is unavailable")
    try:
        record = _bridge_store.submit_job(
            target_device_id=DEVICE_ID,
            operation=operation,
            params=params,
        )
    except ValueError as exc:
        raise DailySyncError(f"{operation} job submission failed") from exc
    deadline = time.time() + 90
    while time.time() < deadline:
        view = _bridge_store.job_view(record.job_id)
        if view is None:
            raise DailySyncError(f"{operation} job disappeared")
        if view.get("state") in TERMINAL:
            break
        time.sleep(0.5)
    else:
        raise DailySyncError(f"{operation} timed out")
    if view.get("state") != "succeeded":
        error = ((view.get("result") or {}).get("error") or {}).get("code", "UNKNOWN")
        normalized = LEGACY_WPS_ERRORS.get(error, error)
        raise DailySyncError(f"{operation} failed: {normalized}", code=normalized)
    return (view.get("result") or {}).get("data") or {}


def _unique(items: list[dict], predicate, label: str) -> dict:
    matches = [item for item in items if predicate(item)]
    if len(matches) != 1:
        raise DailySyncError(f"Expected one {label}, found {len(matches)}")
    return matches[0]


def _find_identifier(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("document_id", "file_id", "id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
        for child in value.values():
            candidate = _find_identifier(child)
            if candidate:
                return candidate
    elif isinstance(value, list):
        for child in value:
            candidate = _find_identifier(child)
            if candidate:
                return candidate
    return None
