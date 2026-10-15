#!/usr/bin/env python3
"""R15-A.3H — WPS Drive API Backend for Autonomous Metadata Inventory.

Uses page.evaluate(fetch(...)) to call WPS Drive API from the browser's
authenticated session. No cookies/tokens are extracted to Python.

Security:
  - Only extracts whitelist metadata fields
  - Never logs cookies, tokens, authorization, headers, or full response bodies
  - Read-only API calls only
"""
from __future__ import annotations
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/opt/paw-ksheet-worker")

METADATA_WHITELIST = [
    "fileid", "id",
    "parentid",
    "fname", "name",
    "type",
    "ext",
    "fver", "version",
    "fsize", "size",
    "mtime", "ctime",
    "groupid",
]

MAX_PAGES = 20


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def classify_pagination_state(next_offset, next_filter, raw_count):
    """Classify pagination state based on WPS Drive API response fields.
    
    Live evidence (R15-A.3P.1 diagnostics) proves:
      - next_offset == -1 → END_OF_PAGINATION (no more pages)
      - next_offset >= 0 → MAY_CONTINUE (but still needs contract validation)
      - Other negative values → INVALID → FAIL CLOSED
    
    Returns: 'END', 'CONTINUE', or 'INVALID'
    """
    if next_offset is None:
        return "END"
    if not isinstance(next_offset, (int, float)):
        return "INVALID"
    if next_offset == -1:
        return "END"
    if next_offset >= 0:
        if raw_count == 0:
            return "END"
        return "CONTINUE"
    return "INVALID"


def fetch_folder_listing(page, group_id: str, parent_id: str, offset: int = 0, count: int = 100, next_filter: str = None):
    """Call WPS Drive API via page.evaluate(fetch(...)) to list folder contents.
    
    Returns dict with:
      - files: list of sanitized metadata dicts
      - next_offset: value or None
      - next_filter: value or None
      - http_status: int
      - raw_count: int
      - files_key_present: bool (true if 'files' key exists in response)
      - next_offset_key_present: bool
      - next_filter_key_present: bool
    """
    api_url = f"/3rd/drive/api/v5/groups/{group_id}/files"
    import urllib.parse
    param_dict = {
        "parentid": parent_id,
        "include": "0",
        "with_link": "0",
        "review_pic_thumbnail": "0",
        "with_sharefolder_type": "0",
        "offset": str(offset),
        "count": str(count),
    }
    if next_filter:
        param_dict["next_filter"] = next_filter
    params = "?" + urllib.parse.urlencode(param_dict)
    
    js_code = f"""
    async () => {{
        const controller = new AbortController();
        const deadline = setTimeout(() => controller.abort(), 30000);
        try {{
            const resp = await fetch("{api_url}{params}", {{
                method: "GET",
                signal: controller.signal,
                credentials: "include",
                headers: {{ "Accept": "application/json" }}
            }});
            const status = resp.status;
            if (!resp.ok) {{
                return {{ http_status: status, error: "HTTP_ERROR", files: [], next_offset: null, next_filter: null, files_key_present: false, next_offset_key_present: false, next_filter_key_present: false, raw_count: 0 }};
            }}
            const data = await resp.json();
            
            // Use hasOwnProperty for real key presence check
            const filesKeyPresent = Object.prototype.hasOwnProperty.call(data, "files");
            const nextOffsetKeyPresent = Object.prototype.hasOwnProperty.call(data, "next_offset");
            const nextFilterKeyPresent = Object.prototype.hasOwnProperty.call(data, "next_filter");
            
            // Only use 'files' key (no fallback to list/items)
            const files = filesKeyPresent ? (data.files || []) : [];
            const nextOffset = nextOffsetKeyPresent ? data.next_offset : null;
            const nextFilter = nextFilterKeyPresent ? data.next_filter : null;
            
            // Extract only whitelist fields from each file entry
            const whitelist = {METADATA_WHITELIST};
            const sanitized = files.map(f => {{
                const s = {{}};
                for (const k of whitelist) {{
                    if (f[k] !== undefined && f[k] !== null) {{
                        s[k] = f[k];
                    }}
                }}
                return s;
            }});
            
            return {{
                http_status: status,
                files: sanitized,
                next_offset: nextOffset,
                next_filter: nextFilter,
                files_key_present: filesKeyPresent,
                next_offset_key_present: nextOffsetKeyPresent,
                next_filter_key_present: nextFilterKeyPresent,
                raw_count: files.length
            }};
        }} catch (e) {{
            return {{ http_status: 0, error: e.message, files: [], next_offset: null, next_filter: null, files_key_present: false, next_offset_key_present: false, next_filter_key_present: false, raw_count: 0 }};
        }} finally {{
            clearTimeout(deadline);
        }}
    }}
    """
    
    result = page.evaluate(js_code)
    return result


def fetch_all_pages(page, group_id: str, parent_id: str, count: int = 100):
    """Fetch all pages of folder listing with bounded pagination.
    
    Implements R15-A.3P.1 pagination contract:
      - classify_pagination_state determines END/CONTINUE/INVALID
      - next_offset == -1 → END (proven by live diagnostics)
      - Each page records sanitized diagnostics (no raw response/headers/cookies)
      - Any page error → fail-closed (discard all partial objects)
    
    Returns dict with:
      - files: list of all sanitized metadata dicts (deduplicated)
      - http_status: int (from first page)
      - pages_fetched: int
      - total_count: int
      - error: str or None
      - pagination_complete: bool
      - page_diagnostics: list of per-page sanitized diagnostic dicts
      - files_key_present: bool (from first page)
      - next_offset_key_present: bool (from first page)
      - next_filter_key_present: bool (from first page)
    """
    all_files = []
    seen_ids = set()
    offset = 0
    current_next_filter = None
    pages_fetched = 0
    http_status = None
    error = None
    pagination_complete = False
    files_key_present = False
    next_offset_key_present = False
    next_filter_key_present = False
    visited_cursors = set()
    page_diagnostics = []
    
    while pages_fetched < MAX_PAGES:
        result = fetch_folder_listing(page, group_id, parent_id, offset, count, current_next_filter)
        
        page_http_status = result.get("http_status", 0)
        page_error = result.get("error")
        page_files_key_present = result.get("files_key_present", False)
        page_next_offset_key_present = result.get("next_offset_key_present", False)
        page_next_filter_key_present = result.get("next_filter_key_present", False)
        page_raw_count = result.get("raw_count", 0)
        
        if http_status is None:
            http_status = page_http_status
        
        # Record schema presence from first page
        if pages_fetched == 0:
            files_key_present = page_files_key_present
            next_offset_key_present = page_next_offset_key_present
            next_filter_key_present = page_next_filter_key_present
        
        # Determine page_status for diagnostics
        page_status = "OK"
        if page_error:
            page_status = "HTTP_ERROR"
            error = page_error if isinstance(page_error, str) else "HTTP_ERROR"
            page_diagnostics.append({
                "page_index": pages_fetched,
                "request_offset": offset,
                "request_next_filter_present": current_next_filter is not None,
                "request_next_filter_length": len(current_next_filter) if current_next_filter else 0,
                "http_status": page_http_status,
                "files_key_present": page_files_key_present,
                "next_offset_key_present": page_next_offset_key_present,
                "next_filter_key_present": page_next_filter_key_present,
                "raw_count": page_raw_count,
                "unique_count_added": 0,
                "response_next_offset": None,
                "response_next_filter_present": False,
                "response_next_filter_length": 0,
                "pagination_state": "N/A",
                "page_status": page_status,
            })
            break
        
        if page_http_status != 200:
            page_status = "HTTP_ERROR"
            error = f"HTTP_{page_http_status}"
            page_diagnostics.append({
                "page_index": pages_fetched,
                "request_offset": offset,
                "request_next_filter_present": current_next_filter is not None,
                "request_next_filter_length": len(current_next_filter) if current_next_filter else 0,
                "http_status": page_http_status,
                "files_key_present": page_files_key_present,
                "next_offset_key_present": page_next_offset_key_present,
                "next_filter_key_present": page_next_filter_key_present,
                "raw_count": page_raw_count,
                "unique_count_added": 0,
                "response_next_offset": None,
                "response_next_filter_present": False,
                "response_next_filter_length": 0,
                "pagination_state": "N/A",
                "page_status": page_status,
            })
            break
        
        if not page_files_key_present or not page_next_offset_key_present or not page_next_filter_key_present:
            page_status = "SCHEMA_ERROR"
            error = "SCHEMA_ERROR"
            page_diagnostics.append({
                "page_index": pages_fetched,
                "request_offset": offset,
                "request_next_filter_present": current_next_filter is not None,
                "request_next_filter_length": len(current_next_filter) if current_next_filter else 0,
                "http_status": page_http_status,
                "files_key_present": page_files_key_present,
                "next_offset_key_present": page_next_offset_key_present,
                "next_filter_key_present": page_next_filter_key_present,
                "raw_count": page_raw_count,
                "unique_count_added": 0,
                "response_next_offset": None,
                "response_next_filter_present": False,
                "response_next_filter_length": 0,
                "pagination_state": "N/A",
                "page_status": page_status,
            })
            break
        
        files = result.get("files", [])
        unique_added = 0
        
        # Dedup by object_id (fileid or id) + NORMALIZATION_ERROR check
        for f in files:
            fid = f.get("fileid") or f.get("id")
            if not fid:
                page_status = "NORMALIZATION_ERROR"
                error = "NORMALIZATION_ERROR"
                break
            if str(fid) not in seen_ids:
                seen_ids.add(str(fid))
                all_files.append(f)
                unique_added += 1
        
        if page_status == "NORMALIZATION_ERROR":
            page_diagnostics.append({
                "page_index": pages_fetched,
                "request_offset": offset,
                "request_next_filter_present": current_next_filter is not None,
                "request_next_filter_length": len(current_next_filter) if current_next_filter else 0,
                "http_status": page_http_status,
                "files_key_present": page_files_key_present,
                "next_offset_key_present": page_next_offset_key_present,
                "next_filter_key_present": page_next_filter_key_present,
                "raw_count": page_raw_count,
                "unique_count_added": unique_added,
                "response_next_offset": None,
                "response_next_filter_present": False,
                "response_next_filter_length": 0,
                "pagination_state": "N/A",
                "page_status": page_status,
            })
            break
        
        pages_fetched += 1
        
        next_offset = result.get("next_offset")
        next_filter_val = result.get("next_filter")
        
        # R15-A.3P.1: Use classify_pagination_state for termination
        page_state = classify_pagination_state(next_offset, next_filter_val, page_raw_count)
        
        # Record page diagnostics with response fields and pagination_state
        page_diagnostics.append({
            "page_index": pages_fetched - 1,
            "request_offset": offset,
            "request_next_filter_present": current_next_filter is not None,
            "request_next_filter_length": len(current_next_filter) if current_next_filter else 0,
            "http_status": page_http_status,
            "files_key_present": page_files_key_present,
            "next_offset_key_present": page_next_offset_key_present,
            "next_filter_key_present": page_next_filter_key_present,
            "raw_count": page_raw_count,
            "unique_count_added": unique_added,
            "response_next_offset": next_offset,
            "response_next_filter_present": next_filter_val is not None,
            "response_next_filter_length": len(next_filter_val) if next_filter_val else 0,
            "pagination_state": page_state,
            "page_status": page_status,
        })
        
        if page_state == "END":
            pagination_complete = True
            break
        
        if page_state == "INVALID":
            error = "PAGINATION_ERROR"
            page_diagnostics[-1]["page_status"] = "PAGINATION_ERROR"
            break
        
        # page_state == "CONTINUE"
        # Loop detection using (offset, next_filter) tuple as cursor
        cursor = (next_offset, next_filter_val)
        if cursor in visited_cursors:
            error = "PAGINATION_LOOP_DETECTED"
            page_diagnostics[-1]["page_status"] = "PAGINATION_ERROR"
            break
        visited_cursors.add(cursor)
        
        offset = next_offset
        current_next_filter = next_filter_val
    
    # If we exited the loop without error and without pagination_complete, mark as incomplete
    if not error and not pagination_complete and pages_fetched >= MAX_PAGES:
        error = "MAX_PAGES_EXCEEDED"
    
    # Fail-closed: if any error occurred, discard all partial objects
    if error:
        all_files = []
        pagination_complete = False
    
    return {
        "files": all_files,
        "http_status": http_status,
        "pages_fetched": pages_fetched,
        "total_count": len(all_files),
        "error": error,
        "pagination_complete": pagination_complete,
        "page_diagnostics": page_diagnostics,
        "files_key_present": files_key_present,
        "next_offset_key_present": next_offset_key_present,
        "next_filter_key_present": next_filter_key_present,
    }


def extract_object_id(file_entry: dict) -> str | None:
    return str(file_entry.get("fileid") or file_entry.get("id") or "")


def extract_display_name(file_entry: dict) -> str | None:
    return file_entry.get("fname") or file_entry.get("name")


def extract_parent_id(file_entry: dict) -> str | None:
    pid = file_entry.get("parentid")
    return str(pid) if pid is not None else None


def extract_version(file_entry: dict) -> str | None:
    v = file_entry.get("fver") or file_entry.get("version")
    return str(v) if v is not None else None


def extract_size(file_entry: dict) -> str | None:
    s = file_entry.get("fsize") or file_entry.get("size")
    return str(s) if s is not None else None


def extract_mtime(file_entry: dict) -> str | None:
    m = file_entry.get("mtime")
    return str(m) if m is not None else None


def extract_object_type(file_entry: dict) -> str:
    """Extract object type from listing entry only.
    
    R15-A.3H.1: Do NOT guess numeric type mapping.
    Only identify string types containing 'folder'/'dir'/'file'.
    Return 'UNKNOWN' for unproven numeric types (including None).
    
    This function only processes listing-observable type info.
    For metadata-based type resolution, use resolve_object_type_from_metadata().
    """
    t = file_entry.get("type")
    if isinstance(t, str):
        if "folder" in t.lower() or "dir" in t.lower():
            return "folder"
        if "file" in t.lower():
            return "file"
    # Numeric type mapping not proven yet — return UNKNOWN
    return "UNKNOWN"


def fetch_object_metadata(page, group_id: str, object_id: str):
    """Fetch metadata for a single object via WPS Drive API metadata endpoint.
    
    Uses GET /3rd/drive/api/v5/files/{id}/metadata
    Returns dict with sanitized keys (no raw response/headers/cookies).
    """
    api_url = f"/3rd/drive/api/v5/files/{object_id}/metadata"
    
    js_code = f"""
    async () => {{
        const controller = new AbortController();
        const deadline = setTimeout(() => controller.abort(), 30000);
        try {{
            const resp = await fetch("{api_url}", {{
                method: "GET",
                signal: controller.signal,
                credentials: "include",
                headers: {{ "Accept": "application/json" }}
            }});
            const status = resp.status;
            if (!resp.ok) {{
                return {{ http_status: status, error: "HTTP_ERROR", fileinfo_present: false, folderinfo_present: false, keys: [] }};
            }}
            const data = await resp.json();
            const keys = Object.keys(data);
            const fileinfoPresent = Object.prototype.hasOwnProperty.call(data, "fileinfo");
            const folderinfoPresent = Object.prototype.hasOwnProperty.call(data, "folderinfo");
            return {{
                http_status: status,
                error: null,
                fileinfo_present: fileinfoPresent,
                folderinfo_present: folderinfoPresent,
                keys: keys
            }};
        }} catch (e) {{
            return {{ http_status: 0, error: e.message, fileinfo_present: false, folderinfo_present: false, keys: [] }};
        }} finally {{
            clearTimeout(deadline);
        }}
    }}
    """
    
    result = page.evaluate(js_code)
    return result


def resolve_object_type_from_metadata(page, object_id: str):
    """Resolve object type from metadata endpoint.
    
    R15-A.3P.1 Object Type Evidence (proven by live diagnostics):
      - folderinfo present → folder
      - folderinfo absent AND fileinfo present → file
      - folderinfo absent AND fileinfo absent → UNKNOWN
      - metadata HTTP/schema failure → UNKNOWN + error
    
    Returns dict with:
      - resolved_type: 'folder', 'file', or 'UNKNOWN'
      - fileinfo_present: bool
      - folderinfo_present: bool
      - http_status: int
      - error: str or None
    """
    meta = fetch_object_metadata(page, "", object_id)
    http_status = meta.get("http_status", 0)
    error = meta.get("error")
    fileinfo_present = meta.get("fileinfo_present", False)
    folderinfo_present = meta.get("folderinfo_present", False)
    
    resolved_type = "UNKNOWN"
    
    if error or http_status != 200:
        resolved_type = "UNKNOWN"
    elif folderinfo_present:
        resolved_type = "folder"
    elif fileinfo_present:
        resolved_type = "file"
    else:
        resolved_type = "UNKNOWN"
    
    return {
        "resolved_type": resolved_type,
        "fileinfo_present": fileinfo_present,
        "folderinfo_present": folderinfo_present,
        "http_status": http_status,
        "error": error,
    }


def resolve_object_type(page, file_entry: dict):
    """A4 Resolver Foundation: resolve object type with listing + metadata fallback.
    
    Logic:
      A. If listing type matches proven mapping → use it
      B. Otherwise call metadata resolver
      C. metadata folder evidence → folder
      D. metadata file evidence → file
      E. Cannot prove → UNKNOWN
    
    Returns dict with:
      - resolved_type: 'folder', 'file', or 'UNKNOWN'
      - source: 'LISTING' or 'METADATA_FALLBACK' or 'NONE'
      - fileinfo_present: bool or None
      - folderinfo_present: bool or None
      - metadata_http_status: int or None
      - metadata_error: str or None
    """
    listing_type = extract_object_type(file_entry)
    
    # A. If listing type is proven (not UNKNOWN), use it
    if listing_type != "UNKNOWN":
        return {
            "resolved_type": listing_type,
            "source": "LISTING",
            "fileinfo_present": None,
            "folderinfo_present": None,
            "metadata_http_status": None,
            "metadata_error": None,
        }
    
    # B. Call metadata resolver
    obj_id = extract_object_id(file_entry)
    if not obj_id:
        return {
            "resolved_type": "UNKNOWN",
            "source": "NONE",
            "fileinfo_present": None,
            "folderinfo_present": None,
            "metadata_http_status": None,
            "metadata_error": "NO_OBJECT_ID",
        }
    
    meta_result = resolve_object_type_from_metadata(page, obj_id)
    
    return {
        "resolved_type": meta_result["resolved_type"],
        "source": "METADATA_FALLBACK" if meta_result["resolved_type"] != "UNKNOWN" else "NONE",
        "fileinfo_present": meta_result["fileinfo_present"],
        "folderinfo_present": meta_result["folderinfo_present"],
        "metadata_http_status": meta_result["http_status"],
        "metadata_error": meta_result["error"],
    }


def api_entry_to_inventory_obj(file_entry: dict, group_id: str) -> dict:
    """Convert API file entry to inventory object dict."""
    obj_id = extract_object_id(file_entry)
    if not obj_id:
        return None
    
    return {
        "provider": "wps",
        "group_id": group_id,
        "object_id": obj_id,
        "object_type": extract_object_type(file_entry),
        "parent_id": extract_parent_id(file_entry) or "",
        "parent_relation_proof": "EXPLICIT_METADATA" if file_entry.get("parentid") is not None else "DIRECTORY_SCOPE",
        "display_name": extract_display_name(file_entry) or "",
        "filename": file_entry.get("fname") or file_entry.get("name"),
        "extension": str(file_entry.get("ext", "")) if file_entry.get("ext") is not None else None,
        "version": extract_version(file_entry),
        "size": extract_size(file_entry),
        "mtime": extract_mtime(file_entry),
        "ctime": str(file_entry.get("ctime")) if file_entry.get("ctime") is not None else None,
        "metadata_source": "WPS_DRIVE_API",
    }


def collect_from_wps_drive_api(page, group_id: str, parent_id: str, expected_nonempty: bool = True):
    """Unified API collector with proper error handling and result contract.
    
    R15-A.3P.1 Fail-Closed:
      - Any page error (HTTP/SCHEMA/PAGINATION/NORMALIZATION) → status=BLOCKED, objects=[]
      - Partial objects from earlier pages are discarded
      - pagination_complete=False on any error
    
    Returns dict with:
      - status: SUCCESS or BLOCKED
      - error_class: NONE, HTTP_ERROR, SCHEMA_ERROR, PAGINATION_ERROR, NORMALIZATION_ERROR, EMPTY_SCOPE_UNEXPECTED, AUTH_REQUIRED
      - objects: list of NormalizedInventoryObject dicts
      - http_status: int
      - files_key_present: bool
      - next_offset_key_present: bool
      - next_filter_key_present: bool
      - pages_fetched: int
      - pagination_complete: bool
      - page_diagnostics: list of per-page sanitized diagnostic dicts
    """
    result = fetch_all_pages(page, group_id, parent_id)
    http_status = result.get("http_status", 0)
    error = result.get("error")
    files_key_present = result.get("files_key_present", False)
    next_offset_key_present = result.get("next_offset_key_present", False)
    next_filter_key_present = result.get("next_filter_key_present", False)
    pages_fetched = result.get("pages_fetched", 0)
    pagination_complete = result.get("pagination_complete", False)
    page_diagnostics = result.get("page_diagnostics", [])
    raw_files = result.get("files", [])
    
    # Determine error class — fail-closed: any error from fetch_all_pages means BLOCKED
    error_class = "NONE"
    if error and error != "" and error != "null":
        if "NORMALIZATION" in error:
            error_class = "NORMALIZATION_ERROR"
        elif "PAGINATION_LOOP" in error or "PAGINATION_ERROR" in error or "MAX_PAGES" in error:
            error_class = "PAGINATION_ERROR"
        elif "SCHEMA" in error:
            error_class = "SCHEMA_ERROR"
        elif "HTTP" in error:
            error_class = "HTTP_ERROR"
        else:
            error_class = "HTTP_ERROR"
    elif http_status == 401 or http_status == 403:
        error_class = "AUTH_REQUIRED"
    elif http_status != 200:
        error_class = "HTTP_ERROR"
    elif not files_key_present:
        error_class = "SCHEMA_ERROR"
    elif expected_nonempty and len(raw_files) == 0:
        error_class = "EMPTY_SCOPE_UNEXPECTED"
    
    # R15-A.3P.1: Fail-closed — ANY error means discard ALL partial objects
    if error_class != "NONE":
        raw_files = []
        pagination_complete = False
    
    # Convert to NormalizedInventoryObject
    objects = []
    for f_entry in raw_files:
        obj = api_entry_to_inventory_obj(f_entry, group_id)
        if obj:
            objects.append(obj)
    
    status = "SUCCESS" if error_class == "NONE" else "BLOCKED"
    
    return {
        "status": status,
        "error_class": error_class,
        "objects": objects,
        "http_status": http_status,
        "files_key_present": files_key_present,
        "next_offset_key_present": next_offset_key_present,
        "next_filter_key_present": next_filter_key_present,
        "pages_fetched": pages_fetched,
        "pagination_complete": pagination_complete,
        "page_diagnostics": page_diagnostics,
    }
