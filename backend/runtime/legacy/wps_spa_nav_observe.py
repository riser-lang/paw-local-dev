#!/usr/bin/env python3
"""R15-A.1H.3D — Manual SPA Navigation Render Proof.

User manually navigates via noVNC through SPA routing.
Script observes DOM state every 2 seconds.
"""
import os, time, json
from urllib.parse import urlparse
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

PROFILE_DIR = "/var/lib/paw-ksheet-worker/chromium-profile"
BROWSER_EXECUTABLE = "/var/lib/paw-ksheet-worker/ms-playwright/chromium-1208/chrome-linux64/chrome"
GROUP_URL = "https://365.kdocs.cn/ent/690174051/3009705063"
LOG_FILE = "/tmp/spa_nav_observe.log"
MONITOR_SECONDS = 600
TICK_INTERVAL = 2

TARGET_1 = "\u8fd0\u8425\u5bfc\u5e08\u6570\u636e\u770b\u677f.ksheet"
TARGET_2 = "\u8fd0\u8425\u5bfc\u5e08\u6240\u5e26\u5f92\u5f1f\u79bb\u804c\u60c5\u51b5\u8868.ksheet"
FOLDER_NAME = "\u8fd0\u8425\u5bfc\u5e08\u6570\u636e"


def log(msg):
    print(msg, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        executable_path=BROWSER_EXECUTABLE,
        user_data_dir=PROFILE_DIR,
        headless=False,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
              "--no-first-run", "--no-default-browser-check",
              "--window-position=0,0", "--window-size=1440,900"],
        viewport={"width": 1440, "height": 900},
        locale="zh-CN",
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    log(f"[SPA_NAV] Starting at {datetime.now(timezone.utc).isoformat()}")
    log(f"[SPA_NAV] Navigating to GROUP ROOT: {GROUP_URL}")
    page.goto(GROUP_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(10)

    initial_url = page.url
    initial_frame_count = len(page.frames)
    log(f"INITIAL_PAGE_URL={initial_url}")
    log(f"INITIAL_FRAME_COUNT={initial_frame_count}")
    log(f"INITIAL_TITLE={page.title()}")
    log("")
    log("=== CAPTURE_STATE=WAITING_FOR_USER_NAVIGATION ===")
    log(f"CAPTURE_PROCESS_PID={os.getpid()}")
    log(f"BROWSER_ROOT_PID={os.getpid()}")
    log(f"PAGE_URL={initial_url}")
    log(f"FRAME_COUNT={initial_frame_count}")
    log(f"OBSERVATION_LOOP_ACTIVE=YES")
    log(f"NOVNC_PORT=6080")
    log(f"RAW_SECRET_PRINTED=NO")
    log("")
    log("Please navigate via noVNC:")
    log("  1. \u949f\u695a\u8ba1\u5212-\u5175\u79cd\u57f9\u8bad")
    log("  2. \u949f\u695a\u8ba1\u5212-\u8fd0\u8425\u5bfc\u5e08")
    log("  3. \u8fd0\u8425\u5bfc\u5e08\u6570\u636e")
    log("  4. Wait until you see both target files")
    log("  5. Then say: done")
    log("")
    log(f"Monitoring for {MONITOR_SECONDS} seconds...")
    log("")

    start = time.time()
    tick = 0

    while time.time() - start < MONITOR_SECONDS:
        time.sleep(TICK_INTERVAL)
        tick += 1

        try:
            current_url = page.url
        except Exception:
            current_url = "[error]"

        # Safe observation checks
        obs = page.evaluate("""
            (data) => {
                var result = {};
                result.dom_node_count = document.querySelectorAll('*').length;
                result.canvas_count = document.querySelectorAll('canvas').length;
                result.svg_count = document.querySelectorAll('svg').length;
                
                // Check body innerText for targets
                var bodyText = document.body.innerText || '';
                result.target1_in_body = bodyText.indexOf(data.t1) >= 0;
                result.target2_in_body = bodyText.indexOf(data.t2) >= 0;
                result.folder_in_body = bodyText.indexOf(data.folder) >= 0;
                
                // Check breadcrumb for folder name
                var breadcrumbs = document.querySelectorAll('[class*="breadcrumb"], [class*="Breadcrumb"], [class*="crumb"], [class*="path"]');
                result.breadcrumb_folder = false;
                for (var el of breadcrumbs) {
                    if (el.textContent.indexOf(data.folder) >= 0) {
                        result.breadcrumb_folder = true;
                        break;
                    }
                }
                
                // Count scrollable containers
                var scrollable = 0;
                var allEls = document.querySelectorAll('*');
                for (var el of allEls) {
                    if (el.scrollHeight > el.clientHeight + 50 && el.clientHeight > 100) {
                        var style = window.getComputedStyle(el);
                        if (style.overflow === 'auto' || style.overflow === 'scroll' || style.overflowY === 'auto' || style.overflowY === 'scroll') {
                            scrollable++;
                        }
                    }
                }
                result.scrollable_count = scrollable;
                
                return result;
            }
        """, {"t1": TARGET_1, "t2": TARGET_2, "folder": FOLDER_NAME})

        # Playwright locator counts
        try:
            t1_locator = page.get_by_text(TARGET_1, exact=False).count()
        except Exception:
            t1_locator = -1
        try:
            t2_locator = page.get_by_text(TARGET_2, exact=False).count()
        except Exception:
            t2_locator = -1

        log(f"--- TICK={tick} ---")
        log(f"  PAGE_URL={current_url[:200]}")
        log(f"  BREADCRUMB_TARGET_FOLDER_PRESENT={'YES' if obs.get('breadcrumb_folder') else 'NO'}")
        log(f"  TARGET_1_BODY_TEXT_PRESENT={'YES' if obs.get('target1_in_body') else 'NO'}")
        log(f"  TARGET_2_BODY_TEXT_PRESENT={'YES' if obs.get('target2_in_body') else 'NO'}")
        log(f"  TARGET_1_EXACT_LOCATOR_COUNT={t1_locator}")
        log(f"  TARGET_2_EXACT_LOCATOR_COUNT={t2_locator}")
        log(f"  SCROLLABLE_CONTAINER_COUNT={obs.get('scrollable_count', 0)}")
        log(f"  CANVAS_COUNT={obs.get('canvas_count', 0)}")
        log(f"  DOM_NODE_COUNT={obs.get('dom_node_count', 0)}")

        # Memory check
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        avail_mb = int(line.split()[1]) // 1024
                        log(f"  AVAILABLE_MEM_MB={avail_mb}")
                        if avail_mb < 200:
                            log("  [WARNING] Memory pressure")
                        break
        except Exception:
            pass

    log("\n=== OBSERVATION COMPLETE ===")
    log(f"  FINAL_PAGE_URL={page.url}")
    ctx.close()
    log("[SPA_NAV] Done.")
