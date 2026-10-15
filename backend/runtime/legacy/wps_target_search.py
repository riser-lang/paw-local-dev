#!/usr/bin/env python3
"""Search for target document names in folder page HTML and JS state."""
import json, time
from playwright.sync_api import sync_playwright

PROFILE_DIR = "/var/lib/paw-ksheet-worker/chromium-profile"
BROWSER_EXECUTABLE = "/var/lib/paw-ksheet-worker/ms-playwright/chromium-1208/chrome-linux64/chrome"
FOLDER_URL = "https://365.kdocs.cn/ent/690174051/3009705063/485433870456"

TARGET = "\u8fd0\u8425\u5bfc\u5e08\u6570\u636e\u770b\u677f.ksheet"
TARGET2 = "\u8fd0\u8425\u5bfc\u5e08\u6240\u5e26\u5f92\u5f1f\u79bb\u804c\u60c5\u51b5\u8868.ksheet"

with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        executable_path=BROWSER_EXECUTABLE,
        user_data_dir=PROFILE_DIR,
        headless=False,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
              "--no-first-run", "--no-default-browser-check"],
        viewport={"width": 1440, "height": 900},
        locale="zh-CN",
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    # Capture all requests during navigation
    all_reqs = []
    ctx.on("request", lambda r: all_reqs.append({
        "m": r.method,
        "u": r.url,
        "rt": r.resource_type,
    }))

    print(f"Navigating to {FOLDER_URL} ...", flush=True)
    page.goto(FOLDER_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(15)

    html = page.content()
    print(f"HTML_LEN={len(html)}", flush=True)

    # Search in HTML
    for t in [TARGET, TARGET2]:
        idx = html.find(t)
        print(f"TARGET_IN_HTML={t} -> {idx}", flush=True)
        if idx >= 0:
            ctx_str = html[max(0, idx-300):idx+300].replace("\n", " ")[:500]
            print(f"HTML_CONTEXT={ctx_str}", flush=True)

    # Search in __INITIAL_STATE__
    state = page.evaluate("() => window.__INITIAL_STATE__")
    state_str = json.dumps(state, ensure_ascii=False)
    print(f"STATE_LEN={len(state_str)}", flush=True)

    for t in [TARGET, TARGET2]:
        idx = state_str.find(t)
        print(f"TARGET_IN_STATE={t} -> {idx}", flush=True)
        if idx >= 0:
            ctx_str = state_str[max(0, idx-300):idx+300][:500]
            print(f"STATE_CONTEXT={ctx_str}", flush=True)

    # File extLabelsMap
    file_obj = state.get("file", {})
    print(f"FILE_KEYS={list(file_obj.keys())}", flush=True)
    print(f"EXT_LABELS={json.dumps(file_obj.get('extLabelsMap', {}), ensure_ascii=False)}", flush=True)

    # Search all window objects
    result = page.evaluate("""
        () => {
            var target = "\u8fd0\u8425\u5bfc\u5e08\u6570\u636e\u770b\u677f";
            var found = [];
            for (var k in window) {
                try {
                    var s = JSON.stringify(window[k]);
                    if (s && s.indexOf(target) >= 0) {
                        found.push(k + ":" + s.length);
                    }
                } catch(e) {}
            }
            return found.join("|");
        }
    """)
    print(f"WINDOW_MATCHES={result}", flush=True)

    # Print new API requests (xhr/fetch only)
    api_reqs = [r for r in all_reqs if r["rt"] in ("xhr", "fetch") and any(h in r["u"] for h in ["365.kdocs.cn", "drive.kdocs.cn", "plus.kdocs.cn"])]
    print(f"\nAPI_REQUEST_COUNT={len(api_reqs)}", flush=True)
    seen = set()
    for r in api_reqs:
        key = r["m"] + " " + r["u"].split("?")[0]
        if key not in seen:
            seen.add(key)
            print(f"  {r['m']} {r['rt']} {r['u'][:200]}", flush=True)

    ctx.close()
    print("Done.", flush=True)
