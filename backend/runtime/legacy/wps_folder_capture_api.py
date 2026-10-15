#!/usr/bin/env python3
"""R15-A.1H.2C — Safe API-Only Persistent Capture.

Purpose:
  Capture Folder Listing / Child Enumeration API during user manual navigation.
  Uses persistent JSONL log (append-only, survives termination).

Security:
  - No full URL, query values, headers, cookies, tokens, body content.
  - Only structural metadata: method, host, path, query key names.
  - Safe ID fields (parent_id, folder_id, etc.) recorded as safe_query_fields.
  - Response: top_level_keys, list_keys, item_count, item_schema_keys only.
"""

import os
import sys
import time
import json
from urllib.parse import urlparse
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PROFILE_DIR = "/var/lib/paw-ksheet-worker/chromium-profile"
BROWSER_EXECUTABLE = "/var/lib/paw-ksheet-worker/ms-playwright/chromium-1208/chrome-linux64/chrome"
GROUP_URL = "https://365.kdocs.cn/ent/690174051/3009705063"
CAPTURE_LOG = "/tmp/folder_capture_api.log"
JSONL_DIR = "/srv/paw/ksheet_exports/cloud_inventory"
MONITOR_SECONDS = 600
TICK_INTERVAL = 5

# Safe ID fields — values allowed if numeric or WPS object identifier format
SAFE_ID_FIELDS = {
    "parent_id", "parentid", "folder_id", "folderid",
    "file_id", "fileid", "group_id", "groupid",
    "drive_id", "driveid", "offset", "count",
    "page", "page_size", "file_ids",
}

# Sensitive patterns — never log values
SENSITIVE_PATTERNS = [
    "token", "auth", "ticket", "session", "cookie", "secret",
    "password", "sign", "signature", "key", "csrf", "credential",
    "access_token", "refresh_token",
]

# Target document names
TARGET_DOCS = [
    "\u8fd0\u8425\u5bfc\u5e08\u6570\u636e\u770b\u677f.ksheet",
    "\u8fd0\u8425\u5bfc\u5e08\u6240\u5e26\u5f92\u5f1f\u79bb\u804c\u60c5\u51b5\u8868.ksheet",
]

# API candidate hosts
API_HOSTS = ["365.kdocs.cn", "drive.kdocs.cn", "plus.kdocs.cn"]

# API candidate path keywords
API_PATH_KEYWORDS = [
    "drive", "file", "files", "folder", "folders",
    "group", "groups", "list", "children",
    "link", "roaming",
]

# Static resource extensions to skip
STATIC_EXTS = [".js", ".css", ".png", ".jpg", ".svg", ".ico", ".woff", ".woff2", ".gif", ".mp4", ".webm"]

# List keys to detect in responses
LIST_KEYS = ["files", "items", "list", "data", "children", "entries", "objects", "share", "tagfiles", "link_list"]


def is_sensitive(key: str) -> bool:
    k = key.lower()
    return any(p in k for p in SENSITIVE_PATTERNS)


def is_safe_id(key: str) -> bool:
    return key.lower() in SAFE_ID_FIELDS


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_line(msg: str):
    print(msg, flush=True)
    try:
        with open(CAPTURE_LOG, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def get_jsonl_path():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(JSONL_DIR, f"api_capture_events_{ts}.jsonl")


def append_jsonl(path, record):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        log_line(f"[API_CAPTURE] JSONL append error: {e}")


def extract_safe_query(url: str) -> dict:
    parsed = urlparse(url)
    result = {"query_key_names": [], "safe_query_fields": {}}
    if parsed.query:
        for pair in parsed.query.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                if is_sensitive(k):
                    result["query_key_names"].append(f"{k}[REDACTED]")
                else:
                    result["query_key_names"].append(k)
                    if is_safe_id(k):
                        if v.isdigit() or (v.isalnum() and len(v) > 8):
                            result["safe_query_fields"][k] = v
            else:
                result["query_key_names"].append(pair)
    return result


def extract_safe_post_body(request):
    result = {"body_field_names": [], "safe_body_fields": {}}
    try:
        post_data = request.post_data
        if post_data:
            try:
                body = json.loads(post_data)
                if isinstance(body, dict):
                    for k in body.keys():
                        if not is_sensitive(k):
                            result["body_field_names"].append(k)
                            if is_safe_id(k):
                                v = body[k]
                                if isinstance(v, (int, str)):
                                    result["safe_body_fields"][k] = str(v)
                                elif isinstance(v, list):
                                    result["safe_body_fields"][k] = f"[list:{len(v)}]"
            except Exception:
                pass
    except Exception:
        pass
    return result


def extract_safe_response_info(response):
    info = {
        "status": response.status,
        "content_type": "",
        "top_level_keys": [],
        "detected_list_keys": [],
        "item_count": None,
        "item_schema_keys": [],
        "target_found": False,
        "target_names": [],
    }
    try:
        headers = response.headers
        info["content_type"] = headers.get("content-type", "")[:100]
    except Exception:
        pass
    try:
        body = response.text()
        data = json.loads(body)
        if isinstance(data, dict):
            info["top_level_keys"] = list(data.keys())
            for key in LIST_KEYS:
                if key in data:
                    val = data[key]
                    if isinstance(val, list):
                        info["detected_list_keys"].append(key)
                        info["item_count"] = len(val)
                        if val and isinstance(val[0], dict):
                            info["item_schema_keys"] = list(val[0].keys())
                            for item in val[:20]:
                                if isinstance(item, dict):
                                    name = item.get("name", "") or item.get("title", "")
                                    if name and any(t in name for t in TARGET_DOCS):
                                        info["target_found"] = True
                                        info["target_names"].append(name)
                        break
                    elif isinstance(val, dict) and "items" in val:
                        info["detected_list_keys"].append(key)
                        info["item_count"] = len(val["items"])
                        if val["items"] and isinstance(val["items"][0], dict):
                            info["item_schema_keys"] = list(val["items"][0].keys())
                            for item in val["items"][:20]:
                                if isinstance(item, dict):
                                    name = item.get("name", "") or item.get("title", "")
                                    if name and any(t in name for t in TARGET_DOCS):
                                        info["target_found"] = True
                                        info["target_names"].append(name)
                        break
        elif isinstance(data, list):
            info["top_level_keys"] = ["[list]"]
            info["detected_list_keys"] = ["[root]"]
            info["item_count"] = len(data)
            if data and isinstance(data[0], dict):
                info["item_schema_keys"] = list(data[0].keys())
                for item in data[:20]:
                    if isinstance(item, dict):
                        name = item.get("name", "") or item.get("title", "")
                        if name and any(t in name for t in TARGET_DOCS):
                            info["target_found"] = True
                            info["target_names"].append(name)
    except Exception:
        pass
    return info


def is_api_candidate(request):
    url = request.url
    if any(url.endswith(ext) for ext in STATIC_EXTS):
        return False
    if "wpscdn" in url or "cache" in url:
        return False
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path or ""
    if not any(h in host for h in API_HOSTS):
        return False
    try:
        rt = request.resource_type
        if rt in ("xhr", "fetch", "document"):
            return True
    except Exception:
        pass
    if any(kw in path.lower() for kw in API_PATH_KEYWORDS):
        return True
    if "/api/" in path:
        return True
    return False


def main():
    from playwright.sync_api import sync_playwright

    raw_request_count = 0
    api_event_count = 0
    target_response_found = False
    target_response_info = {}

    jsonl_path = get_jsonl_path()

    log_line(f"[API_CAPTURE] Starting at {utc_now()}")
    log_line(f"[API_CAPTURE] Profile: {PROFILE_DIR}")
    log_line(f"[API_CAPTURE] Browser: {BROWSER_EXECUTABLE}")
    log_line(f"[API_CAPTURE] DISPLAY: {os.environ.get('DISPLAY', ':99')}")
    log_line(f"[API_CAPTURE] JSONL: {jsonl_path}")

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            executable_path=BROWSER_EXECUTABLE,
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-position=0,0",
                "--window-size=1440,900",
            ],
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
        )

        if ctx.pages:
            page = ctx.pages[0]
            log_line(f"[API_CAPTURE] Using existing page: {page.url}")
        else:
            page = ctx.new_page()
            log_line("[API_CAPTURE] Created new page")

        request_map = {}

        def on_request(request):
            nonlocal raw_request_count, api_event_count
            raw_request_count += 1
            if not is_api_candidate(request):
                return
            api_event_count += 1
            url = request.url
            parsed = urlparse(url)
            qinfo = extract_safe_query(url)
            binfo = extract_safe_post_body(request)
            record = {
                "timestamp": utc_now(),
                "event_type": "request",
                "resource_type": request.resource_type,
                "method": request.method,
                "host": parsed.hostname or "",
                "path": parsed.path or "",
                "query_key_names": qinfo["query_key_names"],
                "safe_query_fields": qinfo["safe_query_fields"],
                "body_field_names": binfo["body_field_names"],
                "safe_body_fields": binfo["safe_body_fields"],
            }
            append_jsonl(jsonl_path, record)
            req_key = f"{request.method}:{parsed.path}"
            if req_key not in request_map:
                request_map[req_key] = []
            request_map[req_key].append(record)

        def on_response(response):
            nonlocal target_response_found, target_response_info
            url = response.url
            parsed = urlparse(url)
            host = parsed.hostname or ""
            if not any(h in host for h in API_HOSTS):
                return
            if any(url.endswith(ext) for ext in STATIC_EXTS):
                return
            if "wpscdn" in url or "cache" in url:
                return
            rinfo = extract_safe_response_info(response)
            record = {
                "timestamp": utc_now(),
                "event_type": "response",
                "request_method": response.request.method,
                "resource_type": response.request.resource_type,
                "host": host,
                "path": parsed.path or "",
                "status": rinfo["status"],
                "content_type": rinfo["content_type"],
                "top_level_keys": rinfo["top_level_keys"],
                "detected_list_keys": rinfo["detected_list_keys"],
                "item_count": rinfo["item_count"],
                "item_schema_keys": rinfo["item_schema_keys"],
            }
            append_jsonl(jsonl_path, record)
            if rinfo["target_found"]:
                target_response_found = True
                req_key = f"{response.request.method}:{parsed.path}"
                matched_req = None
                if req_key in request_map and request_map[req_key]:
                    matched_req = request_map[req_key][-1]
                target_response_info = {
                    "TARGET_RESPONSE_FOUND": "YES",
                    "TARGET_RESPONSE_REQUEST_METHOD": response.request.method,
                    "TARGET_RESPONSE_REQUEST_HOST": host,
                    "TARGET_RESPONSE_REQUEST_PATH": parsed.path or "",
                    "TARGET_RESPONSE_SAFE_QUERY_FIELDS": matched_req.get("safe_query_fields", {}) if matched_req else {},
                    "TARGET_RESPONSE_BODY_FIELD_NAMES": matched_req.get("body_field_names", []) if matched_req else [],
                    "TARGET_RESPONSE_TOP_LEVEL_KEYS": rinfo["top_level_keys"],
                    "TARGET_RESPONSE_LIST_KEY": rinfo["detected_list_keys"],
                    "TARGET_RESPONSE_ITEM_COUNT": rinfo["item_count"],
                    "TARGET_RESPONSE_ITEM_SCHEMA_KEYS": rinfo["item_schema_keys"],
                    "TARGET_RESPONSE_TARGET_NAMES": rinfo["target_names"],
                }
                log_line(f"[API_CAPTURE] TARGET_RESPONSE_FOUND=YES")
                log_line(f"[API_CAPTURE] TARGET_METHOD={response.request.method}")
                log_line(f"[API_CAPTURE] TARGET_HOST={host}")
                log_line(f"[API_CAPTURE] TARGET_PATH={parsed.path}")
                log_line(f"[API_CAPTURE] TARGET_LIST_KEY={rinfo['detected_list_keys']}")
                log_line(f"[API_CAPTURE] TARGET_ITEM_COUNT={rinfo['item_count']}")
                log_line(f"[API_CAPTURE] TARGET_NAMES={rinfo['target_names']}")

        ctx.on("request", on_request)
        ctx.on("response", on_response)

        for p in ctx.pages:
            p.on("request", on_request)
            p.on("response", on_response)

        def _on_new_page(new_page):
            new_page.on("request", on_request)
            new_page.on("response", on_response)
        ctx.on("page", _on_new_page)

        log_line(f"[API_CAPTURE] Navigating to {GROUP_URL} ...")
        try:
            page.goto(GROUP_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log_line(f"[API_CAPTURE] Page.goto warning: {e}")
        time.sleep(10)

        log_line(f"[API_CAPTURE] URL: {page.url}")
        log_line(f"[API_CAPTURE] TITLE: {page.title()}")
        log_line(f"[API_CAPTURE] RAW_REQUEST_COUNT: {raw_request_count}")
        log_line(f"[API_CAPTURE] API_EVENT_COUNT: {api_event_count}")
        log_line("")
        log_line("MARKER=USER_NAVIGATION_START")
        log_line("")
        log_line("=== CAPTURE_STATE=WAITING_FOR_USER_NAVIGATION ===")
        log_line(f"CAPTURE_PROCESS_PID={os.getpid()}")
        log_line(f"DISPLAY=:99")
        log_line(f"NOVNC_PORT=6080")
        log_line(f"BROWSER_PROFILE={PROFILE_DIR}")
        log_line(f"RUN_AS_USER=pawksheet")
        log_line(f"PAGE_URL={page.url}")
        log_line(f"RAW_REQUEST_EVENT_COUNT={raw_request_count}")
        log_line(f"API_EVENT_COUNT={api_event_count}")
        log_line(f"API_LOG_FILE={jsonl_path}")
        log_line(f"NETWORK_CAPTURE_ACTIVE=YES")
        log_line(f"PERSISTENT_APPEND_LOG_ACTIVE=YES")
        log_line(f"CURRENT_MEMORY_AVAILABLE=")
        log_line("")
        log_line("Please navigate via noVNC to:")
        log_line("  1. folder 485641343445")
        log_line("  2. folder 485433870456")
        log_line("  3. Wait for file list to load")
        log_line("  4. Then say: done")
        log_line("")
        log_line(f"Monitoring for {MONITOR_SECONDS} seconds...")
        log_line("")

        start = time.time()
        tick_num = 0
        low_mem_count = 0

        while time.time() - start < MONITOR_SECONDS:
            time.sleep(TICK_INTERVAL)
            tick_num += 1

            try:
                with open("/proc/meminfo", "r") as f:
                    memlines = f.readlines()
                meminfo = {}
                for line in memlines:
                    parts = line.split()
                    if len(parts) >= 2:
                        meminfo[parts[0].rstrip(":")] = int(parts[1])
                avail_kb = meminfo.get("MemAvailable", 0)
                avail_mb = avail_kb // 1024
            except Exception:
                avail_mb = -1

            page_count = len(ctx.pages)
            page_url = ""
            try:
                page_url = page.url
            except Exception:
                page_url = "[error]"

            log_line(f"--- TICK={tick_num} ---")
            log_line(f"  PAGE_COUNT={page_count}")
            log_line(f"  PAGE_URL={page_url[:200]}")
            log_line(f"  RAW_REQUEST_COUNT={raw_request_count}")
            log_line(f"  API_EVENT_COUNT={api_event_count}")
            log_line(f"  AVAILABLE_MEM_MB={avail_mb}")
            if target_response_found:
                log_line(f"  TARGET_RESPONSE_FOUND=YES")

            if avail_mb >= 0 and avail_mb < 200:
                low_mem_count += 1
                log_line(f"  [WARNING] Memory pressure: {avail_mb}MB < 200MB ({low_mem_count}/2)")
                if low_mem_count >= 2:
                    log_line("  CAPTURE_ABORTED_MEMORY_PRESSURE=YES")
                    break
            else:
                low_mem_count = 0

        log_line(f"\n=== CAPTURE COMPLETE ===")
        log_line(f"  TOTAL_RAW_REQUESTS={raw_request_count}")
        log_line(f"  TOTAL_API_EVENTS={api_event_count}")
        log_line(f"  JSONL_FILE={jsonl_path}")
        if target_response_found:
            for k, v in target_response_info.items():
                log_line(f"  {k}={v}")
        else:
            log_line(f"  TARGET_RESPONSE_FOUND=NO")

        ctx.close()
        log_line("[API_CAPTURE] Browser context closed.")


if __name__ == "__main__":
    main()
