#!/usr/bin/env python3
"""ksheet_process_worker.py — Process-isolated single-object KSheet export worker.

Executed as a standalone subprocess by the corpus orchestrator.
Acquires exactly ONE KSheet object in its own process tree with its own Playwright/Chromium context.
Outputs sanitized result JSON to a target file and stdout.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
import subprocess
import sys
import time

DEV_DIR = os.path.dirname(os.path.abspath(__file__))
if DEV_DIR not in sys.path:
    sys.path.insert(0, DEV_DIR)
if "/opt/paw-ksheet-worker" not in sys.path:
    sys.path.append("/opt/paw-ksheet-worker")

PROFILE_DIR = "/var/lib/paw-ksheet-worker/chromium-profile"
BROWSER_EXECUTABLE = "/var/lib/paw-ksheet-worker/ms-playwright/chromium-1208/chrome-linux64/chrome"


def _clean_stale_singleton_locks(profile_dir: str = PROFILE_DIR) -> None:
    """Remove dead Singleton symlinks in Chromium profile to avoid wedged browser startup."""
    for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock_path = os.path.join(profile_dir, lock_name)
        try:
            if os.path.islink(lock_path) or os.path.exists(lock_path):
                target = os.readlink(lock_path) if os.path.islink(lock_path) else ""
                is_alive = False
                if "-" in target:
                    try:
                        pid = int(target.split("-")[-1])
                        os.kill(pid, 0)
                        is_alive = True
                    except (ValueError, OSError):
                        is_alive = False
                if not is_alive:
                    try:
                        os.unlink(lock_path)
                    except OSError:
                        pass
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Process-isolated KSheet Export Worker")
    parser.add_argument("--item-json", required=True, help="JSON-encoded normalized item dictionary")
    parser.add_argument("--staging-dir", required=True, help="Staging directory for exported artifact")
    parser.add_argument("--result-json", required=True, help="File path to write sanitized result JSON")
    args = parser.parse_args()

    item = json.loads(args.item_json)
    staging_dir = args.staging_dir
    result_path = args.result_json

    os.makedirs(staging_dir, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(result_path)), exist_ok=True)

    _clean_stale_singleton_locks(PROFILE_DIR)

    from playwright.sync_api import sync_playwright
    from wps_export_acquisition import (
        run_production_acquisition_export,
        ExportAcquisitionResult,
    )

    result: ExportAcquisitionResult | None = None
    t0 = time.time()

    try:
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                PROFILE_DIR,
                headless=True,
                accept_downloads=True,
                executable_path=BROWSER_EXECUTABLE,
                args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--display=:99"],
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://365.kdocs.cn/latest", wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(3000)

                result = run_production_acquisition_export(page, item, staging_dir)
            finally:
                try:
                    for p in list(context.pages):
                        p.close()
                except Exception:
                    pass
                try:
                    context.close()
                except Exception:
                    pass
    except Exception as exc:
        result = ExportAcquisitionResult(
            status="FAILED",
            object_id=str(item.get("object_id", "")),
            error_class=f"WORKER_EXCEPTION:{type(exc).__name__}:{str(exc)[:100]}",
            started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)),
            finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    if result is None:
        result = ExportAcquisitionResult(
            status="FAILED",
            object_id=str(item.get("object_id", "")),
            error_class="NO_RESULT_PRODUCED",
        )

    res_dict = asdict(result)
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(res_dict, f, indent=2, ensure_ascii=False)

    print(json.dumps({"status": result.status, "error_class": result.error_class, "object_id": result.object_id, "size": result.artifact_size}))


if __name__ == "__main__":
    main()
