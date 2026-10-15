#!/usr/bin/env python3
"""
R15-A.1H.2 — WPS Folder Navigation Network Capture

Headful Playwright with network monitoring.
User navigates folders via noVNC while we capture API requests.

Security:
  - Only logs: method, path, query keys, response keys, item count, item field names
  - Never logs: cookies, tokens, authorization, headers, body values
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

# Set DISPLAY before Playwright import
os.environ.setdefault("DISPLAY", ":99")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROFILE_DIR = Path("/var/lib/paw-ksheet-worker/chromium-profile")
BROWSER_EXECUTABLE = "/var/lib/paw-ksheet-worker/ms-playwright/chromium-1208/chrome-linux64/chrome"
WPS_DRIVE_URL = "https://drive.wps.cn/"

CAPTURE_LOG = Path("/srv/paw/ksheet_exports/cloud_inventory/folder_capture.log")
CAPTURE_JSON = Path("/srv/paw/ksheet_exports/cloud_inventory/folder_capture.json")

# Sensitive field patterns
SENSITIVE_KEYS = [
    "token", "trial_token", "cookie", "authorization", "csrf",
    "session", "password", "credential", "secret",
    "access_token", "refresh_token",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def is_sensitive(key: str) -> bool:
    k = key.lower()
    return any(s in k for s in SENSITIVE_KEYS)

def safe_url(url: str) -> str:
    """Redact sensitive query param values, keep keys."""
    if "?" in url:
        base, qs = url.split("?", 1)
        params = qs.split("&")
        safe_params = []
        for p in params:
            if "=" in p:
                k, _ = p.split("=", 1)
                if is_sensitive(k):
                    safe_params.append(f"{k}=[REDACTED]")
                else:
                    safe_params.append(f"{k}=[value]")
            else:
                safe_params.append(p)
        return f"{base}?{'&'.join(safe_params)}"
    return url

def get_response_info(body_text: str) -> dict:
    """Extract safe response metadata."""
    info = {}
    try:
        data = json.loads(body_text)
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

def log_line(msg: str):
    """Print and flush immediately (unbuffered)."""
    print(msg, flush=True)
    # Also append to log file
    try:
        CAPTURE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(CAPTURE_LOG, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Main capture logic
# ---------------------------------------------------------------------------

def main():
    from playwright.sync_api import sync_playwright

    captured: list[dict] = []
    baseline_patterns: set[tuple[str, str]] = set()  # (method, url_path_without_query)

    log_line(f"[CAPTURE] Starting at {utc_now()}")
    log_line(f"[CAPTURE] Profile: {PROFILE_DIR}")
    log_line(f"[CAPTURE] Browser: {BROWSER_EXECUTABLE}")
    log_line(f"[CAPTURE] DISPLAY: {os.environ.get('DISPLAY', ':99')}")

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
        # Use existing page if available (persistent context may have one), else create new
        if ctx.pages:
            page = ctx.pages[0]
        else:
            page = ctx.new_page()

        # STEP 4 — Register network listeners on context (captures ALL pages)
        seen_requests = set()
        def handle_request(request):
            url = request.url
            # Skip static resources
            if any(url.endswith(ext) for ext in [".js", ".css", ".png", ".jpg", ".svg", ".ico", ".woff", ".woff2"]):
                return
            if "wpscdn" in url or "cache" in url:
                return
            method = request.method
            if "/api/" in url or "/drive" in url or "/kdocs" in url:
                # Deduplicate: ctx + page listeners may fire for same request
                req_key = (method, url)
                if req_key in seen_requests:
                    return
                seen_requests.add(req_key)
                # Extract URL path without query params for baseline comparison
                url_path = url.split("?", 1)[0] if "?" in url else url
                # Build baseline key: url_path + critical query params that distinguish folder/file context
                baseline_key = url_path
                if "?" in url:
                    query = url.split("?", 1)[1]
                    params = urllib.parse.parse_qs(query)
                    critical_params = {k: v[0] for k, v in params.items()
                                       if k.lower() in ("parent_id", "group_id", "folder_id", "file_id", "obj_id", "id")}
                    if critical_params:
                        baseline_key = url_path + "?" + urllib.parse.urlencode(sorted(critical_params.items()))
                entry = {
                    "timestamp": utc_now(),
                    "method": method,
                    "url": safe_url(url),
                    "url_path": url_path,
                    "baseline_key": baseline_key,
                    "post_data_keys": [],
                }
                if method == "POST":
                    try:
                        post_data = request.post_data
                        if post_data:
                            try:
                                body = json.loads(post_data)
                                if isinstance(body, dict):
                                    entry["post_data_keys"] = [k for k in body.keys() if not is_sensitive(k)]
                            except Exception:
                                pass
                    except Exception:
                        pass
                captured.append(entry)

        def handle_response(response):
            url = response.url
            if any(url.endswith(ext) for ext in [".js", ".css", ".png", ".jpg", ".svg", ".ico", ".woff", ".woff2"]):
                return
            if "wpscdn" in url or "cache" in url:
                return
            if "/api/" in url or "/drive" in url or "/kdocs" in url:
                # Find matching request entry
                for entry in reversed(captured):
                    if safe_url(url) == entry["url"] and "response_status" not in entry:
                        entry["response_status"] = response.status
                        try:
                            body = response.text()
                            entry.update(get_response_info(body))
                        except Exception:
                            pass
                        break

        # Use CDP (Chrome DevTools Protocol) to capture ALL network requests including user manual navigation
        cdp_session = ctx.new_cdp_session(page)
        cdp_session.send("Network.enable")
        cdp_request_map = {}
        def on_cdp_request(params):
            req = params.get("request", {})
            url = req.get("url", "")
            if any(url.endswith(ext) for ext in [".js", ".css", ".png", ".jpg", ".svg", ".ico", ".woff", ".woff2"]):
                return
            if "wpscdn" in url or "cache" in url:
                return
            method = req.get("method", "GET")
            if "/api/" in url or "/drive" in url or "/kdocs" in url:
                req_id = params.get("requestId", "")
                cdp_request_map[req_id] = (method, url)
                url_path = url.split("?", 1)[0] if "?" in url else url
                baseline_key = url_path
                if "?" in url:
                    query = url.split("?", 1)[1]
                    params_dict = urllib.parse.parse_qs(query)
                    critical_params = {k: v[0] for k, v in params_dict.items()
                                       if k.lower() in ("parent_id", "group_id", "folder_id", "file_id", "obj_id", "id")}
                    if critical_params:
                        baseline_key = url_path + "?" + urllib.parse.urlencode(sorted(critical_params.items()))
                entry = {
                    "timestamp": utc_now(),
                    "method": method,
                    "url": safe_url(url),
                    "url_path": url_path,
                    "baseline_key": baseline_key,
                    "post_data_keys": [],
                }
                post_data = req.get("postData", "")
                if method == "POST" and post_data:
                    try:
                        body = json.loads(post_data)
                        if isinstance(body, dict):
                            entry["post_data_keys"] = [k for k in body.keys() if not is_sensitive(k)]
                    except Exception:
                        pass
                captured.append(entry)
        def on_cdp_response(params):
            req_id = params.get("requestId", "")
            response = params.get("response", {})
            url = response.get("url", "")
            if any(url.endswith(ext) for ext in [".js", ".css", ".png", ".jpg", ".svg", ".ico", ".woff", ".woff2"]):
                return
            if "wpscdn" in url or "cache" in url:
                return
            if "/api/" in url or "/drive" in url or "/kdocs" in url:
                for entry in reversed(captured):
                    if safe_url(url) == entry["url"] and "response_status" not in entry:
                        entry["response_status"] = response.get("status", 0)
                        try:
                            body = cdp_session.send("Network.getResponseBody", {"requestId": req_id})
                            if body and body.get("body"):
                                entry.update(get_response_info(body["body"]))
                        except Exception:
                            pass
                        break
        cdp_session.on("Network.requestWillBeSent", on_cdp_request)
        cdp_session.on("Network.responseReceived", on_cdp_response)
        # Also create CDP sessions for new pages opened by user
        def _on_new_cdp_page(new_page):
            try:
                new_cdp = ctx.new_cdp_session(new_page)
                new_cdp.send("Network.enable")
                new_cdp.on("Network.requestWillBeSent", on_cdp_request)
                new_cdp.on("Network.responseReceived", on_cdp_response)
                log_line(f"[CAPTURE] CDP session created for new page: {new_page.url[:100]}")
            except Exception as e:
                log_line(f"[CAPTURE] CDP session error for new page: {e}")
        ctx.on("page", _on_new_cdp_page)

        # Navigate to WPS Drive
        log_line("[CAPTURE] Navigating to https://drive.wps.cn/ ...")
        try:
            page.goto(WPS_DRIVE_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log_line(f"[CAPTURE] Page.goto warning: {e}")
        page.wait_for_timeout(10000)

        log_line(f"[CAPTURE] URL: {page.url}")
        log_line(f"[CAPTURE] TITLE: {page.title()}")

        baseline_count = len(captured)
        log_line(f"[CAPTURE] BASELINE_REQUESTS: {baseline_count}")
        baseline_patterns = {(e["method"], e["baseline_key"]) for e in captured}

        # Print baseline requests (safe metadata only)
        for i, entry in enumerate(captured):
            log_line(f"  BASE[{i}] {entry['method']} {entry['url'][:150]}")

        # STEP 5 — Wait for user navigation
        log_line("")
        log_line("=== CAPTURE_STATE=WAITING_FOR_USER_NAVIGATION ===")
        log_line(f"NOVNC_PORT=6080")
        log_line(f"DISPLAY=:99")
        log_line(f"BROWSER_PROFILE={PROFILE_DIR}")
        log_line("NETWORK_CAPTURE_ACTIVE=YES")
        log_line("")
        log_line("Please navigate via noVNC to:")
        log_line("  1. 钟楚计划-兵种培训")
        log_line("  2. 钟楚计划-运营导师")
        log_line("  3. Wait for file list to load")
        log_line("  4. Then say: 已进入运营导师目录，继续")
        log_line("")
        log_line("Monitoring for 600 seconds...")
        log_line("")

        # Monitor for 600 seconds, printing new requests in real-time
        start = time.time()
        last_printed = baseline_count

        while time.time() - start < 600:
            page.wait_for_timeout(5000)
            new_count = len(captured)
            if new_count != last_printed or (int(time.time() - start) % 30 == 0):
                log_line(f"[DEBUG] elapsed={int(time.time()-start)}s captured={new_count} baseline={baseline_count} last_url={captured[-1]['url'][:100] if captured else 'none'}")
            if new_count > last_printed:
                new_entries = captured[last_printed:]
                for entry in new_entries:
                    if (entry["method"], entry["baseline_key"]) not in baseline_patterns:
                        log_line(f"\n--- NEW REQUEST ---")
                        log_line(f"  METHOD={entry['method']}")
                        log_line(f"  URL={entry['url'][:200]}")
                        if entry.get("post_data_keys"):
                            log_line(f"  POST_BODY_KEYS={entry['post_data_keys']}")
                        if "response_status" in entry:
                            log_line(f"  STATUS={entry['response_status']}")
                        if entry.get("response_keys"):
                            log_line(f"  RESPONSE_KEYS={entry['response_keys']}")
                        if "item_count" in entry:
                            log_line(f"  ITEM_COUNT={entry['item_count']}")
                        if entry.get("list_key"):
                            log_line(f"  LIST_KEY={entry['list_key']}")
                        if entry.get("item_field_names"):
                            log_line(f"  ITEM_FIELD_NAMES={entry['item_field_names']}")
                last_printed = new_count

        # Final summary
        new_requests = [e for e in captured if (e["method"], e["baseline_key"]) not in baseline_patterns]
        log_line(f"\n=== ALL NEW REQUESTS: {len(new_requests)} ===")
        for i, entry in enumerate(new_requests):
            log_line(f"NEW[{i}]:")
            log_line(f"  METHOD={entry['method']}")
            log_line(f"  URL={entry['url'][:200]}")
            if entry.get("post_data_keys"):
                log_line(f"  POST_BODY_KEYS={entry['post_data_keys']}")
            if "response_status" in entry:
                log_line(f"  STATUS={entry['response_status']}")
            if entry.get("response_keys"):
                log_line(f"  RESPONSE_KEYS={entry['response_keys']}")
            if "item_count" in entry:
                log_line(f"  ITEM_COUNT={entry['item_count']}")
            if entry.get("list_key"):
                log_line(f"  LIST_KEY={entry['list_key']}")
            if entry.get("item_field_names"):
                log_line(f"  ITEM_FIELD_NAMES={entry['item_field_names']}")

        # Save captured data as JSON
        try:
            CAPTURE_JSON.parent.mkdir(parents=True, exist_ok=True)
            with open(CAPTURE_JSON, "w", encoding="utf-8") as f:
                json.dump({
                    "capture_time": utc_now(),
                    "baseline_count": baseline_count,
                    "new_request_count": len(new_requests),
                    "new_requests": new_requests,
                }, f, ensure_ascii=False, indent=2)
            log_line(f"\n[CAPTURE] JSON saved to: {CAPTURE_JSON}")
        except Exception as e:
            log_line(f"[CAPTURE] JSON save error: {e}")

        # Get final page URL
        try:
            final_url = page.url
            log_line(f"\nFINAL_PAGE_URL: {final_url}")
        except Exception:
            pass

        ctx.close()
        log_line("[CAPTURE] Browser closed.")

if __name__ == "__main__":
    main()
