#!/usr/bin/env python3
"""SSR Analysis — Extract file list data from group page HTML."""
import os, re, json, time
from datetime import datetime, timezone

PROFILE_DIR = "/var/lib/paw-ksheet-worker/chromium-profile"
BROWSER_EXECUTABLE = "/var/lib/paw-ksheet-worker/ms-playwright/chromium-1208/chrome-linux64/chrome"
GROUP_URL = "https://365.kdocs.cn/ent/690174051/3009705063"
OUTPUT_DIR = "/srv/paw/ksheet_exports/cloud_inventory"

TARGET_DOCS = [
    "\u8fd0\u8425\u5bfc\u5e08\u6570\u636e\u770b\u677f.ksheet",
    "\u8fd0\u8425\u5bfc\u5e08\u6240\u5e26\u5f92\u5f1f\u79bb\u804c\u60c5\u51b5\u8868.ksheet",
]

SSR_PATTERNS = [
    r"__INITIAL_STATE__",
    r"__NUXT__",
    r"__PRELOADED_STATE__",
    r"window\.__data__",
    r"window\.__INITIAL",
    r"window\.__APOLLO",
    r"window\.pageData",
    r"window\.g_config",
    r"__NEXT_DATA__",
    r"window\._SSR",
]


def main():
    from playwright.sync_api import sync_playwright

    print(f"[SSR] Starting at {datetime.now(timezone.utc).isoformat()}", flush=True)
    print(f"[SSR] URL: {GROUP_URL}", flush=True)

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

        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        print(f"[SSR] Navigating to {GROUP_URL} ...", flush=True)
        page.goto(GROUP_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(15)

        url = page.url
        title = page.title()
        print(f"[SSR] URL: {url}", flush=True)
        print(f"[SSR] TITLE: {title}", flush=True)

        html = page.content()
        print(f"[SSR] HTML length: {len(html)}", flush=True)

        # Save HTML to file
        html_path = os.path.join(OUTPUT_DIR, "group_page.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[SSR] HTML saved to {html_path}", flush=True)

        # Search for SSR patterns
        for pattern in SSR_PATTERNS:
            matches = re.findall(pattern, html)
            if matches:
                print(f"[SSR] PATTERN FOUND: {pattern} ({len(matches)} matches)", flush=True)
                # Find context around first match
                idx = html.find(matches[0])
                if idx >= 0:
                    start = max(0, idx - 50)
                    end = min(len(html), idx + 500)
                    snippet = html[start:end].replace("\n", " ")[:400]
                    print(f"[SSR] CONTEXT: {snippet}", flush=True)

        # Search for target document names
        for target in TARGET_DOCS:
            if target in html:
                print(f"[SSR] TARGET FOUND IN HTML: {target}", flush=True)
                idx = html.find(target)
                start = max(0, idx - 200)
                end = min(len(html), idx + 200)
                snippet = html[start:end].replace("\n", " ")[:400]
                print(f"[SSR] TARGET CONTEXT: {snippet}", flush=True)
            else:
                print(f"[SSR] TARGET NOT IN HTML: {target}", flush=True)

        # Search for file list related keywords
        for kw in ["file_list", "fileList", "files", "folder", "children", "entries", "items"]:
            count = html.count(kw)
            if count > 0:
                print(f"[SSR] KEYWORD '{kw}' found {count} times in HTML", flush=True)

        # Try to extract JSON from script tags
        scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
        print(f"[SSR] Script tags: {len(scripts)}", flush=True)
        for i, script in enumerate(scripts):
            for target in TARGET_DOCS:
                if target in script:
                    print(f"[SSR] TARGET FOUND IN SCRIPT[{i}]: {target}", flush=True)
                    # Try to extract JSON object around the target
                    idx = script.find(target)
                    start = max(0, idx - 300)
                    end = min(len(script), idx + 300)
                    snippet = script[start:end].replace("\n", " ")[:500]
                    print(f"[SSR] SCRIPT CONTEXT: {snippet}", flush=True)

            # Check for window.xxx = {...} patterns
            for pattern in SSR_PATTERNS:
                if pattern.replace("\\", "") in script:
                    print(f"[SSR] PATTERN IN SCRIPT[{i}]: {pattern}", flush=True)

        # Also try page.evaluate to get common SSR state objects
        try:
            state = page.evaluate("() => { try { return JSON.stringify(Object.keys(window.__INITIAL_STATE__ || {})); } catch(e) { return 'N/A'; } }")
            print(f"[SSR] window.__INITIAL_STATE__ keys: {state}", flush=True)
        except Exception:
            pass

        try:
            state = page.evaluate("() => { try { return JSON.stringify(Object.keys(window.__NUXT__ || {})); } catch(e) { return 'N/A'; } }")
            print(f"[SSR] window.__NUXT__ keys: {state}", flush=True)
        except Exception:
            pass

        try:
            state = page.evaluate("() => { try { return typeof window.__data__; } catch(e) { return 'N/A'; } }")
            print(f"[SSR] window.__data__ type: {state}", flush=True)
        except Exception:
            pass

        # Try to find file list data in page context
        try:
            result = page.evaluate("""() => {
                try {
                    var keys = [];
                    for (var k in window) {
                        if (k.startsWith('__') || k.includes('STATE') || k.includes('DATA') || k.includes('data') || k.includes('initial')) {
                            try {
                                var v = window[k];
                                if (v && typeof v === 'object') {
                                    var s = JSON.stringify(v);
                                    if (s && s.length > 100) {
                                        keys.push(k + ':' + s.length);
                                    }
                                }
                            } catch(e) {}
                        }
                    }
                    return keys.join(',');
                } catch(e) { return 'ERROR:' + e; }
            }""")
            print(f"[SSR] Window state objects: {result}", flush=True)
        except Exception as e:
            print(f"[SSR] Window state eval error: {e}", flush=True)

        ctx.close()
        print("[SSR] Done.", flush=True)


if __name__ == "__main__":
    main()
