#!/usr/bin/env python3
import json, time, re
from playwright.sync_api import sync_playwright

PROFILE_DIR = "/var/lib/paw-ksheet-worker/chromium-profile"
BROWSER_EXECUTABLE = "/var/lib/paw-ksheet-worker/ms-playwright/chromium-1208/chrome-linux64/chrome"
FOLDER_URL = "https://365.kdocs.cn/ent/690174051/3009705063/485481025119"

TARGET_1 = "\u8fd0\u8425\u5bfc\u5e08\u6570\u636e\u770b\u677f.ksheet"
TARGET_2 = "\u8fd0\u8425\u5bfc\u5e08\u6240\u5e26\u5f92\u5f1f\u79bb\u804c\u60c5\u51b5\u8868.ksheet"
TARGET_1_SHORT = "\u8fd0\u8425\u5bfc\u5e08\u6570\u636e\u770b\u677f"
TARGET_2_SHORT = "\u8fd0\u8425\u5bfc\u5e08\u6240\u5e26\u5f92\u5f1f\u79bb\u804c\u60c5\u51b5\u8868"
FOLDER_ID = "485481025119"
WHITELIST = ["id", "fileid", "parentid", "fname", "name", "type", "ext", "fver", "fsize", "mtime", "ctime", "groupid", "parent_name", "group_name"]


def pr(label, value):
    print(f"{label}={value}", flush=True)


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

    print(f"[HTML_SEARCH] Navigating to {FOLDER_URL} ...", flush=True)
    page.goto(FOLDER_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(20)

    pr("PAGE_URL", page.url)
    pr("PAGE_TITLE", page.title())

    html = page.content()
    pr("HTML_LENGTH", len(html))

    print("\n=== TARGET SEARCH IN HTML ===", flush=True)
    for target, label in [(TARGET_1, "TARGET_1"), (TARGET_2, "TARGET_2")]:
        idx = html.find(target)
        pr(f"{label}_IN_HTML", "YES" if idx >= 0 else "NO")
        pr(f"{label}_HTML_INDEX", idx)
        if idx >= 0:
            start = max(0, idx - 500)
            end = min(len(html), idx + 500)
            context = html[start:end].replace("\n", " ")[:800]
            print(f"  {label}_HTML_CONTEXT: {context}", flush=True)

    for target, label in [(TARGET_1_SHORT, "TARGET_1_SHORT"), (TARGET_2_SHORT, "TARGET_2_SHORT")]:
        idx = html.find(target)
        pr(f"{label}_IN_HTML", "YES" if idx >= 0 else "NO")
        if idx >= 0:
            start = max(0, idx - 300)
            end = min(len(html), idx + 300)
            context = html[start:end].replace("\n", " ")[:500]
            print(f"  {label}_HTML_CONTEXT: {context}", flush=True)

    ksheet_count = html.count(".ksheet")
    pr("KSHEET_IN_HTML", "YES" if ksheet_count > 0 else "NO")
    pr("KSHEET_COUNT", ksheet_count)
    if ksheet_count > 0:
        idx = 0
        for i in range(min(ksheet_count, 10)):
            idx = html.find(".ksheet", idx)
            if idx < 0:
                break
            start = max(0, idx - 100)
            end = min(len(html), idx + 100)
            context = html[start:end].replace("\n", " ")[:200]
            print(f"  KSHEET[{i}]: {context}", flush=True)
            idx += 1

    print("\n=== SCRIPT TAG SEARCH ===", flush=True)
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
    pr("SCRIPT_TAG_COUNT", len(scripts))
    for i, script in enumerate(scripts):
        for target, label in [(TARGET_1, "T1"), (TARGET_2, "T2")]:
            if target in script:
                print(f"  {label} FOUND IN SCRIPT[{i}] (len={len(script)})", flush=True)
                idx = script.find(target)
                start = max(0, idx - 300)
                end = min(len(script), idx + 300)
                context = script[start:end].replace("\n", " ")[:500]
                print(f"  {label}_SCRIPT_CONTEXT: {context}", flush=True)

    print("\n=== RECURSIVE STATE SEARCH ===", flush=True)
    state = page.evaluate("() => window.__INITIAL_STATE__")

    def recursive_search(obj, target_name, path="", parent=None, grandparent=None, results=None, depth=0):
        if results is None:
            results = []
        if depth > 12 or len(results) >= 10:
            return results
        if isinstance(obj, dict):
            for key in ["fname", "name", "title"]:
                if key in obj and isinstance(obj[key], str) and target_name in obj[key]:
                    safe = {}
                    for src_label, src_obj in [("self", obj), ("parent", parent), ("grandparent", grandparent)]:
                        if src_obj and isinstance(src_obj, dict):
                            for wk in WHITELIST:
                                if wk in src_obj:
                                    safe[f"{src_label}.{wk}"] = src_obj[wk]
                    results.append({"path": path, "fields": safe})
                    break
            for k, v in obj.items():
                recursive_search(v, target_name, f"{path}.{k}", obj, parent, results, depth + 1)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                recursive_search(item, target_name, f"{path}[{i}]", obj, parent, results, depth + 1)
        return results

    for target, label in [(TARGET_1, "TARGET_1"), (TARGET_2, "TARGET_2"),
                          (TARGET_1_SHORT, "TARGET_1_SHORT"), (TARGET_2_SHORT, "TARGET_2_SHORT")]:
        matches = recursive_search(state, target)
        pr(f"{label}_STATE_MATCHES", len(matches))
        for i, m in enumerate(matches[:3]):
            print(f"  {label}_STATE[{i}]: path={m['path']} fields={json.dumps(m['fields'], ensure_ascii=False)}", flush=True)

    print("\n=== FILEID EXTRACTION ===", flush=True)
    def get_field(matches, field_names):
        for m in matches:
            for fn in field_names:
                for prefix in ["self", "parent", "grandparent"]:
                    key = f"{prefix}.{fn}"
                    if key in m["fields"]:
                        return m["fields"][key], f"{prefix}.{fn}"
        return "NOT_FOUND", "NOT_FOUND"

    t1_matches = recursive_search(state, TARGET_1)
    t2_matches = recursive_search(state, TARGET_2)
    t1s_matches = recursive_search(state, TARGET_1_SHORT)
    t2s_matches = recursive_search(state, TARGET_2_SHORT)

    t1_fileid, t1_fileid_src = get_field(t1_matches + t1s_matches, ["fileid", "id"])
    t1_parentid, _ = get_field(t1_matches + t1s_matches, ["parentid"])
    t2_fileid, t2_fileid_src = get_field(t2_matches + t2s_matches, ["fileid", "id"])
    t2_parentid, _ = get_field(t2_matches + t2s_matches, ["parentid"])

    pr("TARGET_1_FILEID", t1_fileid)
    pr("TARGET_1_FILEID_SOURCE", t1_fileid_src)
    pr("TARGET_1_PARENTID", t1_parentid)
    pr("TARGET_2_FILEID", t2_fileid)
    pr("TARGET_2_FILEID_SOURCE", t2_fileid_src)
    pr("TARGET_2_PARENTID", t2_parentid)

    print("\n=== OTHER WINDOW OBJECTS ===", flush=True)
    other_state = page.evaluate("""
        () => {
            var results = {};
            var targets = ['__KDOCS_STATE__', '__KDOCS_STATE_SERVICE_CONFIG__', '__POWER_CONFIG__'];
            for (var t of targets) {
                try {
                    var obj = window[t];
                    if (obj) {
                        var s = JSON.stringify(obj);
                        results[t] = {length: s.length, has_ksheet: s.indexOf('ksheet') >= 0};
                    }
                } catch(e) {}
            }
            return results;
        }
    """)
    for k, v in other_state.items():
        print(f"  {k}: length={v.get('length', 0)} has_ksheet={v.get('has_ksheet', False)}", flush=True)

    print("\n=== FINAL SUMMARY ===", flush=True)
    t1_in_html = TARGET_1 in html
    t2_in_html = TARGET_2 in html
    pr("TARGET_1_IN_FULL_HTML", "YES" if t1_in_html else "NO")
    pr("TARGET_2_IN_FULL_HTML", "YES" if t2_in_html else "NO")
    pr("TARGET_1_FILEID", t1_fileid)
    pr("TARGET_2_FILEID", t2_fileid)
    pr("FOLDER_485481025119_CONFIRMED", "YES" if str(t1_parentid) == FOLDER_ID else "NO")

    ctx.close()
    print("[HTML_SEARCH] Done.", flush=True)
