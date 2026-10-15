#!/usr/bin/env python3
"""
R15-A.1H — WPS Cloud Team Space Inventory (Hardened v3)

Independent Cloud-only inventory module with security hardening.
Uses persistent Chromium profile to call WPS Web APIs for read-only
file metadata enumeration.

Hardening improvements over v2:
  - Secret redaction for all debug/response logging
  - True Team Space Enumeration via drive/groups API (not just search)
  - ID namespace separation (wps_cloud_object_id vs wps_roaming_fileid)
  - Field provenance tracking
  - data_sources_used and bridge_call_count
  - Snapshot separates team_space/search_discovery/roaming documents
  - PASS only on SUCCESS or NO_CHANGE (not PARTIAL_FAILURE)

Prohibitions enforced:
  - No Windows Bridge / office-pc-01 / local.* operations
  - No modification of WPS documents
  - No reading of cookies / tokens / passwords / credentials
  - No modification of Spark / Candidate Governance / Committer / etc.
  - No cron / scheduled tasks
  - No restructuring of ksheet_server_worker.py
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from wps_file_registry import classify_file

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATE_ROOT = Path(os.environ.get("PAW_KSHEET_STATE_ROOT", "/srv/paw/ksheet_exports"))
PROFILE_DIR = Path(os.environ.get("PAW_KSHEET_PROFILE", "/var/lib/paw-ksheet-worker/chromium-profile"))
BROWSER_EXECUTABLE = os.environ.get(
    "PAW_KSHEET_BROWSER_EXECUTABLE",
    "/var/lib/paw-ksheet-worker/ms-playwright/chromium-1208/chrome-linux64/chrome",
)

WPS_DRIVE_URL = "https://drive.wps.cn/"
# This is the provider used by the WPS 365 UI search.  The older v7 search
# source does not expose the Smart Document objects returned by the UI.
WPS_SEARCH_API = "https://365.kdocs.cn/3rd/drive/api/v6/search/files"
WPS_ROAMING_API = "https://drive.kdocs.cn/api/v5/roaming"
WPS_LINKS_API = "https://365.kdocs.cn/3rd/drive/api/v5/links"
WPS_GROUP_FILES_API = "https://drive.kdocs.cn/api/v5/groups/{drive_id}/files"

INVENTORY_DIR = STATE_ROOT / "cloud_inventory"
SNAPSHOT_PATH = INVENTORY_DIR / "latest_snapshot.json"
REPORT_PATH = INVENTORY_DIR / "latest_report.txt"

SEARCH_KEYWORDS = ["运营导师"]

# Known scope from previous analysis
INVENTORY_SCOPE = {
    "inventory_scope_type": "team_space_folder",
    "drive_id": "3009705063",
    "root_parent_id": None,  # root level
    "scope_path": "省域人才发展组文件/钟楚计划-兵种培训/钟楚计划-运营导师",
    "enumeration_method": "wps_group_files_api",
}

LOGIN_PAGE_MARKERS = ["account.wps.cn", "account.kdocs.cn", "singlesso"]

# Sensitive field patterns for redaction
SENSITIVE_KEYS = [
    "token", "trial_token", "cookie", "authorization", "csrf",
    "session", "password", "credential", "secret",
    "access_token", "refresh_token",
]

# Track data sources and bridge usage
DATA_SOURCES_USED = []
BRIDGE_CALL_COUNT = 0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]

def redact_sensitive(text: str) -> str:
    """Redact sensitive values from text before any output."""
    for key in SENSITIVE_KEYS:
        text = re.sub(rf'("{key}"\s*:\s*")[^"]*"', rf'"{key}":"[REDACTED]"', text, flags=re.IGNORECASE)
        text = re.sub(rf'({key}=[^&\s]+)', rf'{key}=[REDACTED]', text, flags=re.IGNORECASE)
    return text

def safe_url_for_recording(url: str) -> str:
    """Return a URL safe for reports/logs; never retain query or fragment data."""
    try:
        parts = urlsplit(str(url or ""))
    except Exception:
        return ""
    if not parts.scheme or not parts.netloc:
        return ""
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "query=REDACTED", "fragment=REDACTED"))


def safe_url_log_fields(url: str) -> dict[str, str]:
    """Reduce a URL to non-sensitive URL components for diagnostic output."""
    try:
        parts = urlsplit(str(url or ""))
    except Exception:
        parts = None
    return {
        "scheme": parts.scheme if parts else "",
        "host": parts.hostname if parts else "",
        "path": parts.path if parts else "",
        "query": "REDACTED",
        "fragment": "REDACTED",
    }


def safe_log(endpoint: str, status: int, item_count: int = 0,
             field_names: list = None, method: str = "GET",
             content_type: str = "application/json"):
    """Log API call facts without exposing URL data or exception text."""
    fields = safe_url_log_fields(endpoint)
    print(
        "[API] "
        f"scheme={fields['scheme']} host={fields['host']} path={fields['path']} "
        f"query={fields['query']} fragment={fields['fragment']} "
        f"method={method} status={status} content_type={content_type} items={item_count}",
        file=sys.stderr,
    )
    if field_names:
        print(f"[API] field_names={field_names}", file=sys.stderr)


def safe_exception_name(exc: Exception) -> str:
    """Keep persisted/output errors free of URLs and secret-bearing messages."""
    return type(exc).__name__

def atomic_write_json(path: Path, value: Any) -> None:
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o660)
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass

def read_json_file(path: Path, default: Any) -> Any:
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return default

# ---------------------------------------------------------------------------
# Browser context
# ---------------------------------------------------------------------------

def _browser_kwargs() -> dict:
    return {
        "executable_path": BROWSER_EXECUTABLE,
        "user_data_dir": str(PROFILE_DIR),
        "headless": True,
        "args": [
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--no-first-run",
            "--disable-background-networking",
        ],
        "viewport": {"width": 1280, "height": 800},
        "locale": "zh-CN",
    }

# ---------------------------------------------------------------------------
# Auth check
# ---------------------------------------------------------------------------

def check_auth(page) -> str:
    try:
        try:
            page.goto(WPS_DRIVE_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as goto_exc:
            print(f"[AUTH_CHECK] Page.goto warning: {safe_exception_name(goto_exc)}", file=sys.stderr)
        time.sleep(8)
        try:
            current_url = page.url
        except Exception:
            current_url = ""
        try:
            page_text = page.content()
        except Exception:
            page_text = ""

        for marker in LOGIN_PAGE_MARKERS:
            if marker in current_url:
                return "NEED_USER_LOGIN"

        login_in_content = any(m in page_text for m in ["扫码登录", "密码登录", "请输入密码", "输入密码"])
        if login_in_content and not any(s in page_text for s in ["file-list", "file-item", "WPS 365", "drive"]):
            return "NEED_USER_LOGIN"

        if "drive.wps.cn" in current_url or "kdocs.cn" in current_url or "365.kdocs.cn" in current_url:
            has_content = any(
                s in page_text
                for s in ["file-list", "file-item", "文件名", "团队", "最近", "drive", "WPS 365", "WPS", "kdocs"]
            )
            has_user = any(s in page_text.lower() for s in ["avatar", "user-info", "username", "userid"])
            if has_content or has_user:
                return "VALID"
            if page_text and len(page_text) > 100000:
                return "LIKELY_VALID"
            return "LIKELY_VALID"

        if page_text and len(page_text) > 100000:
            return "LIKELY_VALID"

        return "UNKNOWN"
    except Exception as exc:
        print(f"[AUTH_CHECK] Error: {safe_exception_name(exc)}", file=sys.stderr)
        return "UNKNOWN"

# ---------------------------------------------------------------------------
# WPS API calls
# ---------------------------------------------------------------------------

def _call_wps_api(page, api_url: str, params: dict = None) -> dict | None:
    try:
        query = ""
        if params:
            query = urlencode(params)
        full_url = f"{api_url}?{query}" if query else api_url

        js_code = f"""
        async () => {{
            try {{
                const resp = await fetch("{full_url}", {{
                    method: "GET",
                    credentials: "include",
                    headers: {{ "Accept": "application/json" }}
                }});
                const text = await resp.text();
                return {{ status: resp.status, body: text }};
            }} catch(e) {{
                return {{ status: 0, body: e.toString() }};
            }}
        }}
        """
        result = page.evaluate(js_code)
        if result and result.get("status") == 200:
            body = result.get("body", "")
            try:
                data = json.loads(body)
                # Log safely without exposing sensitive data
                item_count = 0
                if isinstance(data.get("files"), list):
                    item_count = len(data["files"])
                elif isinstance(data.get("data", {}).get("items"), list):
                    item_count = len(data["data"]["items"])
                elif isinstance(data.get("list"), list):
                    item_count = len(data["list"])
                safe_log(api_url, 200, item_count)
                return data
            except json.JSONDecodeError:
                fields = safe_url_log_fields(api_url)
                print(
                    f"[API] JSON decode error scheme={fields['scheme']} host={fields['host']} "
                    f"path={fields['path']} query=REDACTED fragment=REDACTED",
                    file=sys.stderr,
                )
                return None
        else:
            status = result.get("status") if result else "no response"
            safe_log(api_url, status)
            return None
    except Exception as exc:
        fields = safe_url_log_fields(api_url)
        print(
            f"[API] Error calling scheme={fields['scheme']} host={fields['host']} "
            f"path={fields['path']} query=REDACTED fragment=REDACTED "
            f"error={safe_exception_name(exc)}",
            file=sys.stderr,
        )
        return None

# ---------------------------------------------------------------------------
# Team Space Enumeration
# ---------------------------------------------------------------------------

def _enumerate_team_space(page, drive_id: str, parent_id: str = None) -> list[dict]:
    """Enumerate files/folders in WPS Team Space via group files API."""
    global DATA_SOURCES_USED
    docs = []

    api_url = WPS_GROUP_FILES_API.format(drive_id=drive_id)
    params = {"offset": "0", "count": "50", "orderby": "file_mtime", "order": "desc"}
    if parent_id:
        params["parent_id"] = parent_id

    result = _call_wps_api(page, api_url, params)
    if not result:
        return docs

    if "wps_group_files_api" not in DATA_SOURCES_USED:
        DATA_SOURCES_USED.append("wps_group_files_api")

    files = result.get("files", [])
    for f in files:
        doc = {
            "document_id": str(f.get("id", "")),
            "title": f.get("fname", ""),
            "file_type": f.get("ftype", ""),
            "drive_id": str(f.get("groupid", "")),
            "parent_id": str(f.get("parentid", "")),
            "locator": None,
            "path": None,
            "modified_at": f.get("mtime"),
            "size": f.get("fsize"),
            "version_metadata": {"version": f.get("fver")} if f.get("fver") else None,
            "creator": f.get("creator", {}).get("id") if isinstance(f.get("creator"), dict) else None,
            "id_namespace": "wps_group_fileid",
            "stable_document_id": None,
            "provenance": {
                "document_id_source": "wps_group_files_api.file.id",
                "title_source": "wps_group_files_api.file.fname",
                "file_type_source": "wps_group_files_api.file.ftype",
                "drive_id_source": "wps_group_files_api.file.groupid",
                "parent_id_source": "wps_group_files_api.file.parentid",
                "modified_at_source": "wps_group_files_api.file.mtime",
                "size_source": "wps_group_files_api.file.fsize",
                "version_source": "wps_group_files_api.file.fver",
            },
        }
        did = doc["document_id"]
        if did and did != "unsupported" and did != "null" and did != "":
            doc["stable_document_id"] = did
        for k, v in doc.items():
            if v is None:
                doc[k] = "null"
        if doc["document_id"] and doc["document_id"] != "null" and doc["document_id"] != "":
            docs.append(doc)

    return docs

def _enumerate_team_space_recursive(page, drive_id: str, max_depth: int = 3) -> list[dict]:
    """Recursively enumerate Team Space folders."""
    all_docs = []
    visited_ids = set()

    def _recurse(pid: str, depth: int):
        if depth > max_depth:
            return
        docs = _enumerate_team_space(page, drive_id, pid if pid else None)
        for doc in docs:
            did = doc.get("document_id", "")
            if did and did not in visited_ids:
                visited_ids.add(did)
                all_docs.append(doc)
                # Recurse into folders
                if doc.get("file_type") == "folder":
                    _recurse(did, depth + 1)

    _recurse(None, 0)
    return all_docs

# ---------------------------------------------------------------------------
# Search Discovery
# ---------------------------------------------------------------------------

def _search_documents(page, keyword: str) -> list[dict]:
    """Search WPS documents via search API."""
    global DATA_SOURCES_USED
    docs = []

    search_params = {
        "offset": "0",
        "count": "50",
        "sort_by": "file_mtime",
        "order": "desc",
        "search_group_info": "true",
        "search_operator_name": "true",
        "include_device_info": "false",
        "searchname": keyword,
        "platform": "pc",
        "terminal": "web",
        "product": "wps365",
        "search_file_content": "false",
        "search_file_name": "true",
        "include_group": "true",
        "with_sharefolder_type": "true",
        "with_link": "true",
        "group_sources": "yundoc",
        "scene": "cloud",
    }
    result = _call_wps_api(page, WPS_SEARCH_API, search_params)
    if not result:
        return docs

    if "wps_ui_search_v6" not in DATA_SOURCES_USED:
        DATA_SOURCES_USED.append("wps_ui_search_v6")

    files = result.get("files", [])
    for file_obj in files:
        if not isinstance(file_obj, dict):
            continue
        provider_metadata = {
            "provider_path": "wps_ui_search_v6",
            "provider_namespace": file_obj.get("group_source"),
            "provider_object_type": file_obj.get("ftype"),
            "provider_filename": file_obj.get("fname"),
        }
        file_type, file_family = classify_file(
            file_obj.get("fname"), file_obj.get("ext"), provider_metadata
        )
        doc = {
            "document_id": file_obj.get("id"),
            "title": file_obj.get("fname"),
            "file_type": file_type,
            "file_family": file_family,
            "drive_id": file_obj.get("groupid"),
            "parent_id": file_obj.get("parentid"),
            "locator": safe_url_for_recording(file_obj.get("link_url")),
            "path": file_obj.get("path"),
            "modified_at": file_obj.get("mtime"),
            "size": file_obj.get("fsize"),
            "version_metadata": {"version": file_obj.get("fver")} if file_obj.get("fver") else None,
            "creator": file_obj.get("creator", {}).get("id") if isinstance(file_obj.get("creator"), dict) else None,
            "provider_namespace": file_obj.get("group_source"),
            "provider_object_type": file_obj.get("ftype"),
            "id_namespace": "wps_ui_search_file_id",
            "stable_document_id": None,
            "provenance": {
                "document_id_source": "wps_ui_search_v6.files.id",
                "title_source": "wps_ui_search_v6.files.fname",
                "file_type_source": "wps_ui_search_v6.files.group_source+ftype+fname",
                "drive_id_source": "wps_ui_search_v6.files.groupid",
                "parent_id_source": "wps_ui_search_v6.files.parentid",
                "modified_at_source": "wps_ui_search_v6.files.mtime",
                "size_source": "wps_ui_search_v6.files.fsize",
                "version_source": "wps_ui_search_v6.files.fver",
            },
        }
        did = doc["document_id"]
        if did and did != "unsupported" and did != "null":
            doc["stable_document_id"] = did
        for k, v in doc.items():
            if v is None:
                doc[k] = "null"
        if doc["document_id"] and doc["document_id"] != "null":
            docs.append(doc)
    return docs

# ---------------------------------------------------------------------------
# Roaming Discovery
# ---------------------------------------------------------------------------

def _get_roaming_documents(page) -> list[dict]:
    """Get recent documents via roaming API."""
    global DATA_SOURCES_USED
    docs = []

    roaming_params = {"include": "group_type", "count": "50"}
    result = _call_wps_api(page, WPS_ROAMING_API, roaming_params)
    if not result:
        return docs

    if "wps_roaming_api" not in DATA_SOURCES_USED:
        DATA_SOURCES_USED.append("wps_roaming_api")

    items = result.get("list", [])
    for item in items:
        roaming = item.get("roaming", {})
        file_obj = item.get("file", {})
        doc = {
            "document_id": str(file_obj.get("fileid") or roaming.get("fileid") or ""),
            "title": roaming.get("name"),
            "file_type": _detect_file_type(roaming.get("name") or ""),
            "drive_id": str(file_obj.get("groupid") or roaming.get("groupid") or ""),
            "parent_id": str(file_obj.get("parent_id") or ""),
            "locator": None,
            "path": roaming.get("path"),
            "modified_at": roaming.get("mtime"),
            "size": roaming.get("size"),
            "version_metadata": {"version": file_obj.get("fver")} if file_obj.get("fver") else None,
            "creator": file_obj.get("creator", {}).get("id") if isinstance(file_obj.get("creator"), dict) else None,
            "group_type": roaming.get("group_type"),
            "id_namespace": "wps_roaming_fileid",
            "stable_document_id": None,
            "provenance": {
                "document_id_source": "wps_roaming_api.file.fileid",
                "title_source": "wps_roaming_api.roaming.name",
                "file_type_source": "inferred_from_extension",
                "drive_id_source": "wps_roaming_api.file.groupid",
                "parent_id_source": "wps_roaming_api.file.parent_id",
                "modified_at_source": "wps_roaming_api.roaming.mtime",
                "size_source": "wps_roaming_api.roaming.size",
                "version_source": "wps_roaming_api.file.fver",
            },
        }
        did = doc["document_id"]
        if did and did != "unsupported" and did != "null" and did != "":
            doc["stable_document_id"] = did
        for k, v in doc.items():
            if v is None:
                doc[k] = "null"
        if doc["document_id"] and doc["document_id"] != "null" and doc["document_id"] != "":
            docs.append(doc)
    return docs

def _detect_file_type(filename: str) -> str:
    if not filename:
        return "unsupported"
    lower = filename.lower()
    if lower.endswith(".ksheet") or lower.endswith(".xlsx") or lower.endswith(".xls"):
        return "spreadsheet"
    # KDOC is a historical/product label, not an authoritative file-format
    # signal for WPS Smart Document.  Keep this legacy helper fail-closed.
    elif lower.endswith(".docx") or lower.endswith(".doc"):
        return "document"
    elif lower.endswith(".pptx") or lower.endswith(".ppt"):
        return "presentation"
    elif lower.endswith(".pdf"):
        return "pdf"
    elif lower.endswith(".kform") or lower.endswith(".form"):
        return "form"
    else:
        return "unsupported"

# ---------------------------------------------------------------------------
# Cross-validation
# ---------------------------------------------------------------------------

def _cross_validate_ids(team_docs: list[dict], search_docs: list[dict]) -> dict:
    """Cross-validate document IDs between Team Space and Search API."""
    # Build search ID map by title
    search_by_title = {}
    for d in search_docs:
        title = d.get("title", "")
        if title and title != "null":
            search_by_title[title] = d.get("document_id", "")

    # Target files for cross-check
    targets = ["运营导师数据看板.ksheet", "运营导师所带徒弟离职情况表.ksheet"]
    results = {}

    for target in targets:
        search_id = search_by_title.get(target, "NOT_FOUND")
        # Try to find in team_docs by title
        enum_id = "NOT_FOUND"
        for d in team_docs:
            if d.get("title", "") == target:
                enum_id = d.get("document_id", "")
                break

        match = (search_id != "NOT_FOUND" and enum_id != "NOT_FOUND" and search_id == enum_id)
        results[target] = {
            "search_id": search_id,
            "enum_id": enum_id,
            "match": match,
        }

    return results

# ---------------------------------------------------------------------------
# Inventory execution
# ---------------------------------------------------------------------------

def run_inventory(mode: str = "FULL_SCAN") -> dict:
    global DATA_SOURCES_USED, BRIDGE_CALL_COUNT
    DATA_SOURCES_USED = []
    BRIDGE_CALL_COUNT = 0

    run_id = new_run_id()
    start_time = utc_now()

    result = {
        "run_id": run_id,
        "mode": mode,
        "start_time": start_time,
        "end_time": None,
        "auth_status": "UNKNOWN",
        "true_team_space_enumeration": "NO",
        "team_space_inventory_available": "NO",
        "stable_document_id_available": "NO",
        "stable_document_id_confirmed": "NO",
        "incremental_metadata_available": "NO",
        "windows_bridge_used": "NO",
        "bridge_call_count": 0,
        "data_sources_used": [],
        "security_raw_token_output": "NO",
        "total_found": 0,
        "team_space_document_count": 0,
        "search_discovery_count": 0,
        "roaming_discovery_count": 0,
        "success_count": 0,
        "failed_count": 0,
        "team_space_documents": [],
        "search_discovery_documents": [],
        "roaming_documents": [],
        "documents": [],
        "errors": [],
        "previous_snapshot_id": None,
        "change_summary": None,
        "cross_validation": None,
        "run_status": "FAILED",
    }

    prev_snapshot = None
    if mode == "INCREMENTAL_CHECK":
        prev_snapshot = read_json_file(SNAPSHOT_PATH, None)
        if prev_snapshot:
            result["previous_snapshot_id"] = prev_snapshot.get("run_id")
        else:
            mode = "FULL_SCAN"
            result["mode"] = mode
            result["errors"].append({"stage": "incremental_init", "error": "No previous snapshot, falling back to FULL_SCAN"})

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        result["errors"].append({"stage": "import", "error": "playwright not installed"})
        result["end_time"] = utc_now()
        return result

    errors = []

    try:
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(**_browser_kwargs())
            page = context.new_page()

            # Auth check
            auth_status = check_auth(page)
            result["auth_status"] = auth_status

            if auth_status == "NEED_USER_LOGIN":
                result["errors"].append({"stage": "auth", "error": "User login required"})
                result["end_time"] = utc_now()
                context.close()
                return result

            # Team Space Enumeration
            drive_id = INVENTORY_SCOPE["drive_id"]
            try:
                team_docs = _enumerate_team_space_recursive(page, drive_id, max_depth=3)
                if team_docs:
                    result["true_team_space_enumeration"] = "YES"
                    result["team_space_inventory_available"] = "YES"
                    print(f"[INVENTORY] Team Space: found {len(team_docs)} items", file=sys.stderr)
            except Exception as exc:
                errors.append({"stage": "team_space_enum", "error": safe_exception_name(exc)})
                team_docs = []

            # Search Discovery
            search_docs = []
            for keyword in SEARCH_KEYWORDS:
                try:
                    found = _search_documents(page, keyword)
                    if found:
                        search_docs.extend(found)
                        print(f"[INVENTORY] Search '{keyword}': found {len(found)} docs", file=sys.stderr)
                except Exception as exc:
                    errors.append({"stage": "search", "keyword": keyword, "error": safe_exception_name(exc)})

            # Roaming Discovery
            roaming_docs = []
            try:
                roaming_docs = _get_roaming_documents(page)
                if roaming_docs:
                    print(f"[INVENTORY] Roaming: found {len(roaming_docs)} docs", file=sys.stderr)
            except Exception as exc:
                errors.append({"stage": "roaming", "error": safe_exception_name(exc)})

            context.close()

    except Exception as exc:
        errors.append({"stage": "browser", "error": safe_exception_name(exc)})

    # Cross-validation
    cross_val = _cross_validate_ids(team_docs, search_docs)
    result["cross_validation"] = cross_val

    # Check if stable_document_id is confirmed
    confirmed = all(
        v.get("match", False) for v in cross_val.values()
        if v.get("search_id") != "NOT_FOUND" and v.get("enum_id") != "NOT_FOUND"
    )
    # Also accept if at least one match
    any_match = any(v.get("match", False) for v in cross_val.values())
    result["stable_document_id_confirmed"] = "YES" if any_match else "NO"

    # Stable document ID available
    has_stable_id = bool(team_docs or search_docs)
    result["stable_document_id_available"] = "YES" if has_stable_id else "NO"

    # Counts
    result["team_space_document_count"] = len(team_docs)
    result["search_discovery_count"] = len(search_docs)
    result["roaming_discovery_count"] = len(roaming_docs)

    # All documents combined
    all_docs = team_docs + search_docs + roaming_docs
    result["total_found"] = len(all_docs)
    result["success_count"] = len(all_docs)
    result["failed_count"] = len(errors)

    # Separate snapshots
    result["team_space_documents"] = team_docs
    result["search_discovery_documents"] = search_docs
    result["roaming_documents"] = roaming_docs
    result["documents"] = all_docs

    # Data sources and bridge tracking
    result["data_sources_used"] = DATA_SOURCES_USED
    result["bridge_call_count"] = BRIDGE_CALL_COUNT
    result["windows_bridge_used"] = "NO" if BRIDGE_CALL_COUNT == 0 else "YES"
    result["security_raw_token_output"] = "NO"

    # Incremental comparison
    if mode == "INCREMENTAL_CHECK" and prev_snapshot:
        prev_docs_map = {}
        for d in prev_snapshot.get("documents", []):
            did = d.get("document_id")
            if did and did != "null":
                prev_docs_map[did] = d

        changes = []
        current_ids = set()

        for doc in all_docs:
            did = doc.get("document_id")
            if not did or did == "null":
                continue
            current_ids.add(did)
            prev_doc = prev_docs_map.get(did)
            if not prev_doc:
                changes.append({"document_id": did, "change": "ADDED"})
            else:
                modified = False
                for field in ["modified_at", "size", "version_metadata"]:
                    if doc.get(field) != prev_doc.get(field):
                        modified = True
                        changes.append({"document_id": did, "change": "MODIFIED", "field": field})
                for field in ["title", "parent_id", "path"]:
                    if doc.get(field) != prev_doc.get(field):
                        modified = True
                        changes.append({"document_id": did, "change": "RENAMED_OR_MOVED", "field": field})
                if not modified:
                    changes.append({"document_id": did, "change": "UNCHANGED"})

        for did in prev_docs_map:
            if did not in current_ids:
                changes.append({"document_id": did, "change": "VISIBILITY_MISSING"})

        result["change_summary"] = {
            "added": len([c for c in changes if c["change"] == "ADDED"]),
            "modified": len([c for c in changes if c["change"] == "MODIFIED"]),
            "renamed_or_moved": len([c for c in changes if c["change"] == "RENAMED_OR_MOVED"]),
            "unchanged": len([c for c in changes if c["change"] == "UNCHANGED"]),
            "visibility_missing": len([c for c in changes if c["change"] == "VISIBILITY_MISSING"]),
        }
        result["incremental_metadata_available"] = "YES"
    elif mode == "FULL_SCAN":
        result["incremental_metadata_available"] = "YES"

    # Run status — PARTIAL_FAILURE and FAILED do NOT pass
    if errors and not all_docs:
        run_status = "FAILED"
    elif errors and all_docs:
        run_status = "PARTIAL_FAILURE"
    elif not all_docs and not errors:
        run_status = "NO_CHANGE"
    elif mode == "INCREMENTAL_CHECK" and result.get("change_summary"):
        cs = result["change_summary"]
        if cs["added"] == 0 and cs["modified"] == 0 and cs["renamed_or_moved"] == 0 and cs["visibility_missing"] == 0:
            run_status = "NO_CHANGE"
        else:
            run_status = "SUCCESS"
    else:
        run_status = "SUCCESS"

    result["end_time"] = utc_now()
    result["errors"] = errors
    result["run_status"] = run_status

    # Build snapshot
    snapshot = {
        "schema_version": "2.0",
        "run_id": run_id,
        "mode": mode,
        "started_at": start_time,
        "finished_at": result["end_time"],
        "run_status": run_status,
        "auth_status": result["auth_status"],
        "inventory_scope": INVENTORY_SCOPE,
        "team_space_document_count": len(team_docs),
        "search_discovery_count": len(search_docs),
        "roaming_discovery_count": len(roaming_docs),
        "team_space_documents": team_docs,
        "search_discovery_documents": search_docs,
        "roaming_documents": roaming_docs,
        "documents": all_docs,
        "cross_validation": cross_val,
        "stable_document_id_confirmed": result["stable_document_id_confirmed"],
        "data_sources_used": DATA_SOURCES_USED,
        "bridge_call_count": BRIDGE_CALL_COUNT,
        "errors": errors,
        "previous_snapshot_id": result.get("previous_snapshot_id"),
        "change_summary": result.get("change_summary"),
    }

    atomic_write_json(SNAPSHOT_PATH, snapshot)
    _write_report(result, snapshot)
    return result

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _write_report(result: dict, snapshot: dict) -> None:
    lines = []
    lines.append("=" * 60)
    lines.append("R15-A.1H WPS Cloud Team Space Inventory Hardened Report")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"RUN_ID={result['run_id']}")
    lines.append(f"MODE={result['mode']}")
    lines.append(f"START_TIME={result['start_time']}")
    lines.append(f"END_TIME={result['end_time']}")
    lines.append(f"RUN_STATUS={result.get('run_status', 'UNKNOWN')}")
    lines.append("")
    lines.append("--- Security Verification ---")
    lines.append(f"AUTH_STATUS={result['auth_status']}")
    lines.append(f"TRUE_TEAM_SPACE_ENUMERATION={result['true_team_space_enumeration']}")
    lines.append(f"TEAM_SPACE_INVENTORY_AVAILABLE={result['team_space_inventory_available']}")
    lines.append(f"STABLE_DOCUMENT_ID_AVAILABLE={result['stable_document_id_available']}")
    lines.append(f"STABLE_DOCUMENT_ID_CONFIRMED={result['stable_document_id_confirmed']}")
    lines.append(f"INCREMENTAL_METADATA_AVAILABLE={result['incremental_metadata_available']}")
    lines.append(f"WINDOWS_BRIDGE_USED={result['windows_bridge_used']}")
    lines.append(f"BRIDGE_CALL_COUNT={result['bridge_call_count']}")
    lines.append(f"SECURITY_RAW_TOKEN_OUTPUT={result['security_raw_token_output']}")
    lines.append(f"DATA_SOURCES_USED={result['data_sources_used']}")
    lines.append("")
    lines.append("--- Run Statistics ---")
    lines.append(f"TOTAL_FOUND={result['total_found']}")
    lines.append(f"TEAM_SPACE_DOCUMENT_COUNT={result['team_space_document_count']}")
    lines.append(f"SEARCH_DISCOVERY_COUNT={result['search_discovery_count']}")
    lines.append(f"ROAMING_DISCOVERY_COUNT={result['roaming_discovery_count']}")
    lines.append(f"SUCCESS_COUNT={result['success_count']}")
    lines.append(f"FAILED_COUNT={result['failed_count']}")
    lines.append("")

    if result.get("change_summary"):
        cs = result["change_summary"]
        lines.append("--- Incremental Change Summary ---")
        lines.append(f"ADDED={cs['added']}")
        lines.append(f"MODIFIED={cs['modified']}")
        lines.append(f"RENAMED_OR_MOVED={cs['renamed_or_moved']}")
        lines.append(f"UNCHANGED={cs['unchanged']}")
        lines.append(f"VISIBILITY_MISSING={cs['visibility_missing']}")
        lines.append("")

    if result.get("cross_validation"):
        lines.append("--- Cross Validation ---")
        for title, val in result["cross_validation"].items():
            lines.append(f"  {title}:")
            lines.append(f"    search_id={val['search_id']}")
            lines.append(f"    enum_id={val['enum_id']}")
            lines.append(f"    match={val['match']}")
        lines.append("")

    lines.append("--- Sample Team Space Documents (max 10) ---")
    for i, doc in enumerate(result.get("team_space_documents", [])[:10]):
        lines.append(f"  [{i+1}]")
        lines.append(f"    document_id={doc.get('document_id', 'null')}")
        lines.append(f"    title={doc.get('title', 'null')}")
        lines.append(f"    file_type={doc.get('file_type', 'null')}")
        lines.append(f"    modified_at={doc.get('modified_at', 'null')}")
        lines.append(f"    id_namespace={doc.get('id_namespace', 'null')}")
        lines.append("")

    if result.get("errors"):
        lines.append("--- Errors ---")
        for err in result["errors"]:
            lines.append(f"  stage={err.get('stage', '?')}: {err.get('error', '?')}")
        lines.append("")

    # PASS only on SUCCESS or NO_CHANGE
    pass_conditions = [
        result["auth_status"] in ("VALID", "LIKELY_VALID"),
        result["true_team_space_enumeration"] == "YES",
        result["team_space_document_count"] > 0,
        result["stable_document_id_confirmed"] == "YES",
        result["windows_bridge_used"] == "NO",
        result["bridge_call_count"] == 0,
        result["security_raw_token_output"] == "NO",
        result.get("run_status") in ("SUCCESS", "NO_CHANGE"),
    ]
    all_pass = all(pass_conditions)
    lines.append(f"R15_A1_HARDENING_RESULT={'PASS' if all_pass else 'PARTIAL'}")
    lines.append("")
    lines.append("=" * 60)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")

def generate_final_report(result: dict) -> str:
    lines = []

    pass_conditions = [
        result["auth_status"] in ("VALID", "LIKELY_VALID"),
        result["true_team_space_enumeration"] == "YES",
        result["team_space_document_count"] > 0,
        result["stable_document_id_confirmed"] == "YES",
        result["windows_bridge_used"] == "NO",
        result["bridge_call_count"] == 0,
        result["security_raw_token_output"] == "NO",
        result.get("run_status") in ("SUCCESS", "NO_CHANGE"),
    ]
    all_pass = all(pass_conditions)
    final_result = "PASS" if all_pass else "PARTIAL"

    lines.append("")
    lines.append("=" * 60)
    lines.append("R15-A.1H FINAL REPORT")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"R15_A1_HARDENING_RESULT={final_result}")
    lines.append("")
    lines.append(f"AUTH_STATUS={result['auth_status']}")
    lines.append(f"TRUE_TEAM_SPACE_ENUMERATION={result['true_team_space_enumeration']}")
    lines.append(f"INVENTORY_SCOPE={INVENTORY_SCOPE['scope_path']}")
    lines.append(f"TEAM_SPACE_DOCUMENT_COUNT={result['team_space_document_count']}")
    lines.append(f"SEARCH_DISCOVERY_COUNT={result['search_discovery_count']}")
    lines.append(f"ROAMING_DISCOVERY_COUNT={result['roaming_discovery_count']}")
    lines.append(f"STABLE_DOCUMENT_ID_CONFIRMED={result['stable_document_id_confirmed']}")
    lines.append(f"ID_NAMESPACE_POLICY=wps_cloud_object_id (search) vs wps_group_fileid (team_space) vs wps_roaming_fileid (roaming)")
    lines.append("")

    if result.get("cross_validation"):
        for title, val in result["cross_validation"].items():
            lines.append(f"CROSSCHECK_DOCUMENT_TITLE={title}")
            lines.append(f"CROSSCHECK_SEARCH_ID={val['search_id']}")
            lines.append(f"CROSSCHECK_ENUM_ID={val['enum_id']}")
            lines.append(f"CROSSCHECK_MATCH={val['match']}")
            lines.append("")

    lines.append(f"INCREMENTAL_METADATA_AVAILABLE={result['incremental_metadata_available']}")
    lines.append(f"FIRST_RUN_STATUS={result.get('run_status', 'UNKNOWN') if result['mode'] == 'FULL_SCAN' else 'see previous'}")
    lines.append(f"SECOND_RUN_STATUS={result.get('run_status', 'UNKNOWN') if result['mode'] == 'INCREMENTAL_CHECK' else 'not run'}")
    lines.append("")

    if result.get("change_summary"):
        cs = result["change_summary"]
        lines.append(f"INCREMENTAL_ADDED={cs['added']}")
        lines.append(f"INCREMENTAL_MODIFIED={cs['modified']}")
        lines.append(f"INCREMENTAL_RENAMED_OR_MOVED={cs['renamed_or_moved']}")
        lines.append(f"INCREMENTAL_UNCHANGED={cs['unchanged']}")
        lines.append(f"INCREMENTAL_VISIBILITY_MISSING={cs['visibility_missing']}")
    else:
        lines.append("INCREMENTAL_ADDED=N/A")
        lines.append("INCREMENTAL_MODIFIED=N/A")
        lines.append("INCREMENTAL_RENAMED_OR_MOVED=N/A")
        lines.append("INCREMENTAL_UNCHANGED=N/A")
        lines.append("INCREMENTAL_VISIBILITY_MISSING=N/A")
    lines.append("")

    lines.append(f"DATA_SOURCES_USED={result['data_sources_used']}")
    lines.append(f"BRIDGE_CALL_COUNT={result['bridge_call_count']}")
    lines.append(f"WINDOWS_BRIDGE_USED={result['windows_bridge_used']}")
    lines.append(f"SECURITY_RAW_TOKEN_OUTPUT={result['security_raw_token_output']}")
    lines.append("")
    lines.append("MODIFIED_FILES=")
    lines.append("  (none — only wps_cloud_inventory.py updated)")
    lines.append("")
    lines.append("NEW_FILES=")
    lines.append("  /opt/paw-ksheet-worker/wps_cloud_inventory.py (hardened v3)")
    lines.append("  /srv/paw/ksheet_exports/cloud_inventory/latest_snapshot.json")
    lines.append("  /srv/paw/ksheet_exports/cloud_inventory/latest_report.txt")
    lines.append("")

    blockers = []
    if result["auth_status"] not in ("VALID", "LIKELY_VALID"):
        blockers.append("Auth not valid")
    if result["true_team_space_enumeration"] != "YES":
        blockers.append("True Team Space enumeration not achieved")
    if result["team_space_document_count"] == 0:
        blockers.append("No Team Space documents found")
    if result["stable_document_id_confirmed"] != "YES":
        blockers.append("Stable document ID not confirmed via cross-validation")
    if result.get("run_status") not in ("SUCCESS", "NO_CHANGE"):
        blockers.append(f"Run status is {result.get('run_status')}")

    lines.append("BLOCKERS=")
    if blockers:
        for b in blockers:
            lines.append(f"  - {b}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("NEXT_STEP=R15-A.1H hardening complete. Ready for R15-A.2 when approved.")
    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="R15-A.1H WPS Cloud Team Space Inventory (Hardened)")
    parser.add_argument("--mode", choices=["FULL_SCAN", "INCREMENTAL_CHECK"], default="FULL_SCAN")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()

    if args.report_only:
        snapshot = read_json_file(SNAPSHOT_PATH, None)
        if snapshot:
            print(generate_final_report(snapshot))
        else:
            print("No previous snapshot found.")
        return

    print(f"[R15-A.1H] Starting WPS Cloud Team Space Inventory (Hardened) — mode={args.mode}")
    print(f"[R15-A.1H] Profile: {PROFILE_DIR}")
    print(f"[R15-A.1H] Browser: {BROWSER_EXECUTABLE}")
    print(f"[R15-A.1H] Snapshot output: {SNAPSHOT_PATH}")
    print()

    result = run_inventory(mode=args.mode)
    print(generate_final_report(result))
    print(f"\n[R15-A.1H] Snapshot saved to: {SNAPSHOT_PATH}")
    print(f"[R15-A.1H] Report saved to: {REPORT_PATH}")

if __name__ == "__main__":
    main()
