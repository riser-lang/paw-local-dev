#!/usr/bin/env python3
"""R15-A.1H.2B — Clean Single-Browser Observability Capture.

Purpose:
  1. Prove whether noVNC user operates within Playwright Context's Page.
  2. Detect if Context receives request/response events during manual navigation.
  3. If events received, determine if previous NEW_REQUEST=0 was filter/baseline error.

Security:
  - No query values, headers, cookies, tokens, authorization, body content.
  - Only method, host, path, query key names, body field names (POST).
"""

import os
import sys
import time
import json
from urllib.parse import urlparse
from collections import deque
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PROFILE_DIR = "/var/lib/paw-ksheet-worker/chromium-profile"
BROWSER_EXECUTABLE = "/var/lib/paw-ksheet-worker/ms-playwright/chromium-1208/chrome-linux64/chrome"
WPS_DRIVE_URL = "https://drive.wps.cn/"
CAPTURE_LOG = "/tmp/folder_capture_observe.log"
JSON_OUTPUT = "/srv/paw/ksheet_exports/cloud_inventory/folder_capture_observe.json"
MONITOR_SECONDS = 600
TICK_INTERVAL = 5
MAX_RECENT = 50
MAX_RECENT_DISPLAY = 10

# Sensitive key patterns — never log values for these
SENSITIVE_PATTERNS = [
    "token", "auth", "cookie", "session", "password", "credential",
    "secret", "access_token", "refresh_token", "csrf", "trial_token",
]


def is_sensitive(key: str) -> bool:
    k = key.lower()
    return any(p in k for p in SENSITIVE_PATTERNS)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_line(msg: str):
    """Print and flush immediately, also append to log file."""
    print(msg, flush=True)
    try:
        with open(CAPTURE_LOG, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def safe_request_info(request) -> dict:
    """Extract safe metadata from a request object."""
    url = request.url
    parsed = urlparse(url)
    info = {
        "timestamp": utc_now(),
        "method": request.method,
        "host": parsed.hostname or "",
        "path": parsed.path or "",
        "query_key_names": [],
    }
    # Only query key names, never values
    if parsed.query:
        for pair in parsed.query.split("&"):
            if "=" in pair:
                k, _ = pair.split("=", 1)
                if not is_sensitive(k):
                    info["query_key_names"].append(k)
                else:
                    info["query_key_names"].append(f"{k}[REDACTED]")
            else:
                info["query_key_names"].append(pair)
    # POST body field names only
    if request.method == "POST":
        try:
            post_data = request.post_data
            if post_data:
                try:
                    body = json.loads(post_data)
                    if isinstance(body, dict):
                        info["body_field_names"] = [k for k in body.keys() if not is_sensitive(k)]
                except Exception:
                    pass
        except Exception:
            pass
    return info


def safe_response_info(response) -> dict:
    """Extract safe metadata from a response object."""
    url = response.url
    parsed = urlparse(url)
    info = {
        "status": response.status,
        "host": parsed.hostname or "",
        "path": parsed.path or "",
    }
    try:
        body = response.text()
        data = json.loads(body)
        if isinstance(data, dict):
            info["response_keys"] = list(data.keys())
            for key in ["files", "items", "list", "data", "share", "tagfiles", "link_list"]:
                if key in data:
                    val = data[key]
                    if isinstance(val, list):
                        info["item_count"] = len(val)
                        info["list_key"] = key
                        if val and isinstance(val[0], dict):
                            info["item_field_names"] = list(val[0].keys())
                        break
                    elif isinstance(val, dict) and "items" in val:
                        info["item_count"] = len(val["items"])
                        info["list_key"] = key
                        if val["items"] and isinstance(val["items"][0], dict):
                            info["item_field_names"] = list(val["items"][0].keys())
                        break
        elif isinstance(data, list):
            info["response_keys"] = ["[list]"]
            info["item_count"] = len(data)
            if data and isinstance(data[0], dict):
                info["item_field_names"] = list(data[0].keys())
    except Exception:
        pass
    return info


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    from playwright.sync_api import sync_playwright

    # Raw counters — NO filtering, NO baseline comparison
    request_event_count = 0
    response_event_count = 0
    frame_navigation_count = 0

    # Recent requests ring buffer (max 50)
    recent_requests = deque(maxlen=MAX_RECENT)
    # Map request URL to response info (for matching)
    request_response_map = {}

    log_line(f"[OBSERVE] Starting at {utc_now()}")
    log_line(f"[OBSERVE] Profile: {PROFILE_DIR}")
    log_line(f"[OBSERVE] Browser: {BROWSER_EXECUTABLE}")
    log_line(f"[OBSERVE] DISPLAY: {os.environ.get('DISPLAY', ':99')}")

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

        # STEP 4 — Register listeners BEFORE any navigation
        # Use context-level listeners to capture ALL pages

        def on_request(request):
            nonlocal request_event_count
            request_event_count += 1  # RAW COUNTER — no filter
            try:
                info = safe_request_info(request)
                recent_requests.append(info)
            except Exception:
                pass

        def on_response(response):
            nonlocal response_event_count
            response_event_count += 1  # RAW COUNTER — no filter
            try:
                url = response.url
                # Match to recent request for response metadata
                for req_info in reversed(recent_requests):
                    req_url = response.url
                    parsed = urlparse(req_url)
                    req_path = parsed.path
                    req_host = parsed.hostname
                    if req_info.get("path") == req_path and req_info.get("host") == req_host:
                        resp_info = safe_response_info(response)
                        req_info.update(resp_info)
                        break
            except Exception:
                pass

        def on_page(new_page):
            nonlocal frame_navigation_count
            log_line(f"[OBSERVE] NEW PAGE OPENED: {new_page.url}")
            new_page.on("framenavigated", lambda frame: on_frame_navigated(frame, new_page))

        def on_frame_navigated(frame, page_obj=None):
            nonlocal frame_navigation_count
            frame_navigation_count += 1
            try:
                log_line(f"[OBSERVE] FRAME_NAVIGATED: url={frame.url[:200]}")
            except Exception:
                pass

        # Register on context level
        ctx.on("request", on_request)
        ctx.on("response", on_response)
        ctx.on("page", on_page)

        # Also register on existing pages
        for p in ctx.pages:
            p.on("framenavigated", lambda frame, pg=p: on_frame_navigated(frame, pg))

        # STEP 8 — Use existing page if available
        if ctx.pages:
            page = ctx.pages[0]
            log_line(f"[OBSERVE] Using existing page: {page.url}")
        else:
            page = ctx.new_page()
            log_line("[OBSERVE] Created new page (no existing pages)")

        # Register framenavigated on the main page too
        page.on("framenavigated", lambda frame: on_frame_navigated(frame, page))

        # STEP 9 — Navigate to WPS Drive (listeners already active)
        log_line("[OBSERVE] Navigating to https://drive.wps.cn/ ...")
        try:
            page.goto(WPS_DRIVE_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log_line(f"[OBSERVE] Page.goto warning: {e}")
        time.sleep(10)

        # Record baseline
        baseline_page_count = len(ctx.pages)
        baseline_page_urls = []
        for p in ctx.pages:
            try:
                baseline_page_urls.append(p.url)
            except Exception:
                baseline_page_urls.append("[unknown]")
        baseline_request_count = request_event_count
        baseline_response_count = response_event_count
        baseline_frame_nav = frame_navigation_count

        log_line(f"[OBSERVE] URL: {page.url}")
        log_line(f"[OBSERVE] TITLE: {page.title()}")
        log_line(f"[OBSERVE] BASELINE_PAGE_COUNT: {baseline_page_count}")
        log_line(f"[OBSERVE] BASELINE_PAGE_URLS: {baseline_page_urls}")
        log_line(f"[OBSERVE] BASELINE_REQUEST_EVENT_COUNT: {baseline_request_count}")
        log_line(f"[OBSERVE] BASELINE_RESPONSE_EVENT_COUNT: {baseline_response_count}")
        log_line(f"[OBSERVE] BASELINE_FRAME_NAVIGATION_COUNT: {baseline_frame_nav}")

        # STEP 10 — Enter observation mode
        log_line("")
        log_line("=== CAPTURE_STATE=WAITING_FOR_USER_NAVIGATION ===")
        log_line(f"CAPTURE_PROCESS_PID={os.getpid()}")
        log_line(f"DISPLAY=:99")
        log_line(f"NOVNC_PORT=6080")
        log_line(f"BROWSER_PROFILE={PROFILE_DIR}")
        log_line(f"RUN_AS_USER=pawksheet")
        log_line(f"PAGE_COUNT={len(ctx.pages)}")
        log_line(f"PAGE_URLS={[p.url for p in ctx.pages]}")
        log_line(f"BASELINE_REQUEST_EVENT_COUNT={baseline_request_count}")
        log_line(f"BASELINE_RESPONSE_EVENT_COUNT={baseline_response_count}")
        log_line(f"NETWORK_CAPTURE_ACTIVE=YES")
        log_line(f"OBSERVATION_LOOP_ACTIVE=YES")
        log_line("")
        log_line("Please navigate via noVNC to:")
        log_line("  1. 省域人才发展组文件")
        log_line("  2. 钟楚计划-兵种培训")
        log_line("  3. 钟楚计划-运营导师")
        log_line("  4. Wait for file list to load")
        log_line("  5. Then say: 已进入运营导师目录，继续")
        log_line("")
        log_line(f"Monitoring for {MONITOR_SECONDS} seconds...")
        log_line("")

        # Observation loop
        start = time.time()
        tick_num = 0

        while time.time() - start < MONITOR_SECONDS:
            time.sleep(TICK_INTERVAL)
            tick_num += 1

            # Memory check
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

            # Page info
            page_count = len(ctx.pages)
            page_infos = []
            for i, p in enumerate(ctx.pages):
                try:
                    page_infos.append((i, p.url, p.title()))
                except Exception:
                    page_infos.append((i, "[error]", "[error]"))

            # Recent requests for display (last 10)
            recent_display = list(recent_requests)[-MAX_RECENT_DISPLAY:]

            log_line(f"--- OBSERVATION_TICK={tick_num} ---")
            log_line(f"  PAGE_COUNT={page_count}")
            for idx, url, title in page_infos:
                log_line(f"  PAGE_{idx}_URL={url[:200]}")
                log_line(f"  PAGE_{idx}_TITLE={title[:100]}")
            log_line(f"  REQUEST_EVENT_COUNT={request_event_count}")
            log_line(f"  RESPONSE_EVENT_COUNT={response_event_count}")
            log_line(f"  FRAME_NAVIGATION_COUNT={frame_navigation_count}")
            log_line(f"  RECENT_REQUEST_COUNT={len(recent_requests)}")
            log_line(f"  AVAILABLE_MEM_MB={avail_mb}")
            log_line(f"  RECENT_REQUESTS=")
            for req in recent_display:
                host = req.get("host", "")
                path = req.get("path", "")
                method = req.get("method", "")
                log_line(f"    {method} {host}{path}")

            # Memory pressure check
            if avail_mb >= 0 and avail_mb < 200:
                log_line(f"  [WARNING] Memory pressure: {avail_mb}MB available < 200MB threshold")
                log_line("  CAPTURE_ABORTED_MEMORY_PRESSURE=YES")
                break

        # Final summary
        log_line(f"\n=== OBSERVATION COMPLETE ===")
        log_line(f"  TOTAL_REQUEST_EVENTS={request_event_count}")
        log_line(f"  TOTAL_RESPONSE_EVENTS={response_event_count}")
        log_line(f"  TOTAL_FRAME_NAVIGATIONS={frame_navigation_count}")
        log_line(f"  FINAL_PAGE_COUNT={len(ctx.pages)}")
        for i, p in enumerate(ctx.pages):
            try:
                log_line(f"  FINAL_PAGE_{i}_URL={p.url}")
            except Exception:
                pass

        # Save JSON output
        try:
            os.makedirs(os.path.dirname(JSON_OUTPUT), exist_ok=True)
            output = {
                "baseline": {
                    "page_count": baseline_page_count,
                    "page_urls": baseline_page_urls,
                    "request_event_count": baseline_request_count,
                    "response_event_count": baseline_response_count,
                    "frame_navigation_count": baseline_frame_nav,
                },
                "final": {
                    "request_event_count": request_event_count,
                    "response_event_count": response_event_count,
                    "frame_navigation_count": frame_navigation_count,
                    "page_count": len(ctx.pages),
                },
                "recent_requests": list(recent_requests),
            }
            with open(JSON_OUTPUT, "w", encoding="utf-8") as f:
                json.dump(output, f, ensure_ascii=False, indent=2)
            log_line(f"[OBSERVE] JSON output saved to {JSON_OUTPUT}")
        except Exception as e:
            log_line(f"[OBSERVE] JSON save error: {e}")

        ctx.close()
        log_line("[OBSERVE] Browser context closed.")


if __name__ == "__main__":
    main()
