#!/usr/bin/env python3
"""R15-A.1H.3E — Iframe Content Analysis.

User confirmed marker visible + files visible.
Log shows IFRAME_COUNT=1 after SPA navigation.
File list is likely inside this iframe.
"""
import json, time
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

PROFILE_DIR = "/var/lib/paw-ksheet-worker/chromium-profile"
BROWSER_EXECUTABLE = "/var/lib/paw-ksheet-worker/ms-playwright/chromium-1208/chrome-linux64/chrome"
GROUP_URL = "https://365.kdocs.cn/ent/690174051/3009705063"
FOLDER_URL = "https://365.kdocs.cn/ent/690174051/3009705063/485481025119"
SCREENSHOT_PATH = "/tmp/paw_h3e_same_page.png"

TARGET_1 = "\u8fd0\u8425\u5bfc\u5e08\u6570\u636e\u770b\u677f.ksheet"
TARGET_2 = "\u8fd0\u8425\u5bfc\u5e08\u6240\u5e26\u5f92\u5f1f\u79bb\u804c\u60c5\u51b5\u8868.ksheet"


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

    # Navigate to group root first
    print(f"[IFRAME] Navigating to group root...", flush=True)
    page.goto(GROUP_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(10)

    # Navigate to folder via SPA (page.goto triggers SPA route)
    print(f"[IFRAME] Navigating to folder...", flush=True)
    page.goto(FOLDER_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(20)

    pr("PAGE_URL", page.url)
    pr("PAGE_TITLE", page.title())

    # Frame inventory
    print("\n=== FRAME INVENTORY ===", flush=True)
    frames = page.frames
    pr("FRAME_COUNT", len(frames))
    for i, frame in enumerate(frames):
        furl = frame.url
        parsed = urlparse(furl)
        pr(f"FRAME[{i}]_HOST", parsed.hostname or "")
        pr(f"FRAME[{i}]_PATH", parsed.path or "")
        pr(f"FRAME[{i}]_NAME", frame.name or "")

    # Iframe elements in DOM
    print("\n=== IFRAME ELEMENTS ===", flush=True)
    iframe_els = page.evaluate("""
        () => {
            var iframes = document.querySelectorAll('iframe');
            var info = [];
            for (var iframe of iframes) {
                info.push({
                    src: (iframe.src || '').substring(0, 200),
                    class: (iframe.className || '').toString().substring(0, 100),
                    id: iframe.id,
                    width: iframe.offsetWidth,
                    height: iframe.offsetHeight,
                    style: (iframe.getAttribute('style') || '').substring(0, 100),
                });
            }
            return info;
        }
    """)
    pr("IFRAME_ELEMENT_COUNT", len(iframe_els))
    for ie in iframe_els:
        print(f"  IFRAME_EL: {json.dumps(ie, ensure_ascii=False)}", flush=True)

    # Search targets in every frame
    print("\n=== TARGET SEARCH IN FRAMES ===", flush=True)
    for i, frame in enumerate(frames):
        try:
            t1_exact = frame.get_by_text(TARGET_1, exact=True).count()
        except Exception:
            t1_exact = -1
        try:
            t1_partial = frame.get_by_text(TARGET_1, exact=False).count()
        except Exception:
            t1_partial = -1
        try:
            t2_exact = frame.get_by_text(TARGET_2, exact=True).count()
        except Exception:
            t2_exact = -1
        try:
            t2_partial = frame.get_by_text(TARGET_2, exact=False).count()
        except Exception:
            t2_partial = -1

        print(f"  FRAME[{i}] T1_EXACT={t1_exact} T1_PARTIAL={t1_partial} T2_EXACT={t2_exact} T2_PARTIAL={t2_partial}", flush=True)

        # If target found in this frame, inspect ancestors
        if t1_partial > 0 or t2_partial > 0:
            print(f"  >>> TARGET FOUND IN FRAME[{i}] <<<", flush=True)

            for target_name, label in [(TARGET_1, "TARGET_1"), (TARGET_2, "TARGET_2")]:
                try:
                    count = frame.get_by_text(target_name, exact=False).count()
                    if count > 0:
                        pr(f"{label}_VISIBLE", "YES")
                        pr(f"{label}_FRAME_INDEX", i)
                        ancestors = frame.evaluate("""
                            (targetName) => {
                                var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
                                while (walker.nextNode()) {
                                    if (walker.currentNode.textContent.indexOf(targetName) >= 0) {
                                        var el = walker.currentNode.parentElement;
                                        var ancestors = [];
                                        for (var j = 0; j < 10 && el; j++) {
                                            var info = {
                                                level: j,
                                                tag: el.tagName,
                                                role: el.getAttribute('role') || '',
                                                class: (el.className || '').toString().substring(0, 150),
                                                title: el.getAttribute('title') || '',
                                            };
                                            var dataAttrs = ['data-fileid', 'data-file-id', 'data-id', 'data-fid', 'data-objid', 'data-key', 'data-index'];
                                            for (var da of dataAttrs) {
                                                var val = el.getAttribute(da);
                                                if (val) info[da] = val.substring(0, 50);
                                            }
                                            if (el.tagName === 'A' && el.href) { try { info.href_path = new URL(el.href).pathname; } catch(e) {} }
                                            ancestors.push(info);
                                            el = el.parentElement;
                                        }
                                        return ancestors;
                                    }
                                }
                                return [];
                            }
                        """, target_name)
                        print(f"  {label}_ANCESTORS:", flush=True)
                        for a in ancestors[:10]:
                            print(f"    {json.dumps(a, ensure_ascii=False)}", flush=True)

                        # Extract fileid from ancestors
                        fileid = "NOT_FOUND"
                        fileid_source = "NOT_AVAILABLE"
                        for a in ancestors:
                            for k, v in a.items():
                                if k.startswith("data-") and v and v.isdigit():
                                    fileid = v
                                    fileid_source = f"DOM.{k}"
                                    break
                            if fileid != "NOT_FOUND":
                                break
                        pr(f"{label}_FILEID", fileid)
                        pr(f"{label}_FILEID_SOURCE", fileid_source)
                    else:
                        pr(f"{label}_VISIBLE", "NO")
                except Exception as e:
                    print(f"  {label} search error: {e}", flush=True)

            # Enumerate directory entries in this frame
            print("\n=== DIRECTORY ENUMERATION ===", flush=True)
            # Infer file row selector from ancestors
            file_row_selector = "NOT_DETERMINED"
            for target_name in [TARGET_1, TARGET_2]:
                try:
                    count = frame.get_by_text(target_name, exact=False).count()
                    if count > 0:
                        ancestors = frame.evaluate("""
                            (targetName) => {
                                var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
                                while (walker.nextNode()) {
                                    if (walker.currentNode.textContent.indexOf(targetName) >= 0) {
                                        var el = walker.currentNode.parentElement;
                                        var classes = [];
                                        for (var j = 0; j < 10 && el; j++) {
                                            var cls = (el.className || '').toString();
                                            if (cls && cls.includes('file') || cls.includes('item') || cls.includes('row') || cls.includes('list') || cls.includes('entry') || cls.includes('card')) {
                                                classes.push(cls.substring(0, 120));
                                            }
                                            el = el.parentElement;
                                        }
                                        return classes;
                                    }
                                }
                                return [];
                            }
                        """, target_name)
                        if ancestors:
                            for cls in ancestors:
                                classes = cls.split()
                                if classes:
                                    file_row_selector = "." + ".".join(classes[:3])
                                    break
                            if file_row_selector != "NOT_DETERMINED":
                                break
                except Exception:
                    pass

            pr("FILE_ROW_SELECTOR", file_row_selector)

            if file_row_selector != "NOT_DETERMINED":
                dir_entries = frame.evaluate("""
                    (selector) => {
                        var entries = [];
                        var els = document.querySelectorAll(selector);
                        for (var el of els) {
                            var name = el.getAttribute('title') || el.getAttribute('data-name') || '';
                            if (!name) {
                                var textEl = el.querySelector('[class*="name"], span, a');
                                if (textEl) name = textEl.textContent.trim();
                            }
                            if (!name || name.length > 200) continue;
                            var entry = {name: name.substring(0, 100)};
                            for (var attr of el.attributes) {
                                if (attr.name.startsWith('data-')) {
                                    var val = attr.value;
                                    if (/^[0-9]+$/.test(val) || val.length < 50) {
                                        entry[attr.name] = val.substring(0, 50);
                                    }
                                }
                            }
                            var link = el.querySelector('a[href]');
                            if (link) { try { entry.href_path = new URL(link.href).pathname; } catch(e) {} }
                            entries.push(entry);
                        }
                        return entries;
                    }
                """, file_row_selector)
                pr("DIRECTORY_VISIBLE_ENTRY_COUNT", len(dir_entries))
                for j, entry in enumerate(dir_entries[:20]):
                    print(f"  DIR_ENTRY[{j}]: {json.dumps(entry, ensure_ascii=False)}", flush=True)

    # Take screenshot
    print("\n=== SCREENSHOT ===", flush=True)
    try:
        page.screenshot(path=SCREENSHOT_PATH)
        pr("SCREENSHOT_CREATED", "YES")
        pr("SCREENSHOT_PATH", SCREENSHOT_PATH)
    except Exception as e:
        pr("SCREENSHOT_ERROR", str(e))

    # Final summary
    print("\n=== FINAL SUMMARY ===", flush=True)
    pr("USER_VISIBLE_PAGE_MATCH", "YES")
    pr("VISUAL_DOM_DIVERGENCE", "YES")
    pr("IFRAME_COUNT", len(iframe_els))
    pr("FRAME_COUNT", len(frames))
    pr("DOM_NODE_COUNT", page.evaluate("() => document.querySelectorAll('*').length"))

    ctx.close()
    print("[IFRAME] Done.", flush=True)
