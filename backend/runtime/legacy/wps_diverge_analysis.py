#!/usr/bin/env python3
"""R15-A.1H.3D — Visual DOM Divergence Analysis."""
import json, time
from playwright.sync_api import sync_playwright

PROFILE_DIR = "/var/lib/paw-ksheet-worker/chromium-profile"
BROWSER_EXECUTABLE = "/var/lib/paw-ksheet-worker/ms-playwright/chromium-1208/chrome-linux64/chrome"
GROUP_URL = "https://365.kdocs.cn/ent/690174051/3009705063"
FOLDER_URL = "https://365.kdocs.cn/ent/690174051/3009705063/485481025119"
TARGET_1 = "\u8fd0\u8425\u5bfc\u5e08\u6570\u636e\u770b\u677f.ksheet"
TARGET_1_SHORT = "\u8fd0\u8425\u5bfc\u5e08\u6570\u636e\u770b\u677f"
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

    # Navigate to group root first, then to folder via SPA
    print(f"[DIVERGE] Navigating to group root...", flush=True)
    page.goto(GROUP_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(10)

    # Navigate to folder URL (SPA routing)
    print(f"[DIVERGE] Navigating to folder...", flush=True)
    page.goto(FOLDER_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(15)

    pr("PAGE_URL", page.url)

    # 1. Body innerText analysis
    print("\n=== BODY INNERTEXT ANALYSIS ===", flush=True)
    body_info = page.evaluate("""
        (data) => {
            var bt = document.body.innerText || '';
            return {
                body_text_length: bt.length,
                target1_full: bt.indexOf(data.t1) >= 0,
                target1_short: bt.indexOf(data.t1s) >= 0,
                target2_full: bt.indexOf(data.t2) >= 0,
                ksheet_in_body: bt.indexOf('.ksheet') >= 0,
                ksheet_count: (bt.match(/\.ksheet/g) || []).length,
            };
        }
    """, {"t1": TARGET_1, "t1s": TARGET_1_SHORT, "t2": TARGET_2})
    pr("BODY_TEXT_LENGTH", body_info.get("body_text_length", 0))
    pr("TARGET_1_FULL_IN_BODY", "YES" if body_info.get("target1_full") else "NO")
    pr("TARGET_1_SHORT_IN_BODY", "YES" if body_info.get("target1_short") else "NO")
    pr("TARGET_2_FULL_IN_BODY", "YES" if body_info.get("target2_full") else "NO")
    pr("KSHEET_IN_BODY", "YES" if body_info.get("ksheet_in_body") else "NO")
    pr("KSHEET_COUNT", body_info.get("ksheet_count", 0))

    # 2. Custom elements / web components
    print("\n=== CUSTOM ELEMENTS ===", flush=True)
    custom_info = page.evaluate("""
        () => {
            var customEls = [];
            var allEls = document.querySelectorAll('*');
            for (var el of allEls) {
                var tag = el.tagName.toLowerCase();
                if (tag.includes('-') && !tag.startsWith('kd-') === false || tag.includes('-')) {
                    if (!customEls.find(c => c.tag === tag)) {
                        var hasShadow = !!el.shadowRoot;
                        customEls.push({tag: tag, count: 1, has_shadow: hasShadow, class: (el.className || '').toString().substring(0, 80)});
                    } else {
                        customEls.find(c => c.tag === tag).count++;
                    }
                }
            }
            return customEls.sort((a, b) => b.count - a.count).slice(0, 20);
        }
    """)
    pr("CUSTOM_ELEMENT_TYPE_COUNT", len(custom_info))
    for ce in custom_info[:15]:
        print(f"  CUSTOM: {json.dumps(ce, ensure_ascii=False)}", flush=True)

    # 3. Closed shadow DOM check (indirect)
    print("\n=== SHADOW DOM CHECK ===", flush=True)
    shadow_info = page.evaluate("""
        () => {
            var openShadow = 0;
            var closedShadowSuspects = 0;
            var allEls = document.querySelectorAll('*');
            for (var el of allEls) {
                if (el.shadowRoot) {
                    openShadow++;
                }
                // Check for elements that might have closed shadow root
                var tag = el.tagName.toLowerCase();
                if (tag.includes('-')) {
                    // Custom element - might have closed shadow
                    if (!el.shadowRoot && el.childNodes.length === 0 && el.children.length === 0) {
                        // Has no visible children but might render via closed shadow
                        var rect = el.getBoundingClientRect();
                        if (rect.width > 50 && rect.height > 20) {
                            closedShadowSuspects++;
                        }
                    }
                }
            }
            return {open_shadow: openShadow, closed_shadow_suspects: closedShadowSuspects};
        }
    """)
    pr("OPEN_SHADOW_ROOT_COUNT", shadow_info.get("open_shadow", 0))
    pr("CLOSED_SHADOW_SUSPECTS", shadow_info.get("closed_shadow_suspects", 0))

    # 4. Text node search (deep DOM walk for target text fragments)
    print("\n=== DEEP TEXT NODE SEARCH ===", flush=True)
    text_search = page.evaluate("""
        (data) => {
            var results = [];
            var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
            var nodeCount = 0;
            while (walker.nextNode()) {
                nodeCount++;
                var text = walker.currentNode.textContent.trim();
                if (text.length > 0) {
                    // Check for target fragments
                    if (text.indexOf('ksheet') >= 0 || text.indexOf('KSHEET') >= 0) {
                        var el = walker.currentNode.parentElement;
                        results.push({
                            text: text.substring(0, 100),
                            tag: el ? el.tagName : '',
                            class: el ? (el.className || '').toString().substring(0, 100) : '',
                            parent_tag: el && el.parentElement ? el.parentElement.tagName : '',
                        });
                    }
                    if (text.indexOf(data.t1s) >= 0) {
                        var el = walker.currentNode.parentElement;
                        results.push({
                            text: text.substring(0, 100),
                            tag: el ? el.tagName : '',
                            class: el ? (el.className || '').toString().substring(0, 100) : '',
                            match: 'target1_short',
                        });
                    }
                }
            }
            return {total_text_nodes: nodeCount, matches: results.slice(0, 20)};
        }
    """, {"t1s": TARGET_1_SHORT})
    pr("TOTAL_TEXT_NODES", text_search.get("total_text_nodes", 0))
    pr("TEXT_MATCHES", len(text_search.get("matches", [])))
    for m in text_search.get("matches", [])[:10]:
        print(f"  TEXT_MATCH: {json.dumps(m, ensure_ascii=False)}", flush=True)

    # 5. Check for file-related class names in DOM
    print("\n=== FILE-RELATED ELEMENTS ===", flush=True)
    file_els = page.evaluate("""
        () => {
            var results = [];
            var allEls = document.querySelectorAll('*');
            for (var el of allEls) {
                var cls = (el.className || '').toString().toLowerCase();
                if (cls.includes('file') || cls.includes('folder') || cls.includes('item') || cls.includes('row') || cls.includes('list') || cls.includes('entry')) {
                    if (el.offsetWidth > 100 && el.offsetHeight > 20) {
                        results.push({
                            tag: el.tagName,
                            class: (el.className || '').toString().substring(0, 120),
                            w: el.offsetWidth,
                            h: el.offsetHeight,
                            child_count: el.children.length,
                            text_preview: el.textContent.trim().substring(0, 80),
                        });
                    }
                }
            }
            return results.slice(0, 30);
        }
    """)
    pr("FILE_RELATED_ELEMENT_COUNT", len(file_els))
    for fe in file_els[:15]:
        print(f"  FILE_EL: {json.dumps(fe, ensure_ascii=False)}", flush=True)

    # 6. SVG check
    print("\n=== SVG CHECK ===", flush=True)
    svg_info = page.evaluate("""
        () => {
            var svgs = document.querySelectorAll('svg');
            var svgTexts = [];
            for (var svg of svgs) {
                var text = svg.textContent.trim();
                if (text.length > 0 && text.indexOf('ksheet') >= 0) {
                    svgTexts.push({text: text.substring(0, 100), class: svg.getAttribute('class') || ''});
                }
            }
            return {svg_count: svgs.length, svg_with_target: svgTexts.length, svg_texts: svgTexts.slice(0, 5)};
        }
    """)
    pr("SVG_COUNT", svg_info.get("svg_count", 0))
    pr("SVG_WITH_TARGET", svg_info.get("svg_with_target", 0))

    # 7. Check if file names are split across multiple text nodes
    print("\n=== SPLIT TEXT NODE CHECK ===", flush=True)
    split_check = page.evaluate("""
        (data) => {
            // Search for partial target name fragments
            var fragments = ['\u8fd0\u8425\u5bfc\u5e08', 'ksheet', '.ksheet'];
            var found = {};
            for (var frag of fragments) {
                var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
                var count = 0;
                while (walker.nextNode()) {
                    if (walker.currentNode.textContent.indexOf(frag) >= 0) {
                        count++;
                    }
                }
                found[frag] = count;
            }
            return found;
        }
    """, {})
    for frag, count in split_check.items():
        print(f"  FRAGMENT '{frag}' found {count} times in text nodes", flush=True)

    # 8. Playwright locator with different strategies
    print("\n=== LOCATOR STRATEGIES ===", flush=True)
    try: pr("GET_BY_TEXT_T1_EXACT", page.get_by_text(TARGET_1, exact=True).count())
    except Exception as e: pr("GET_BY_TEXT_T1_EXACT", f"ERROR: {e}")
    try: pr("GET_BY_TEXT_T1_PARTIAL", page.get_by_text(TARGET_1, exact=False).count())
    except Exception as e: pr("GET_BY_TEXT_T1_PARTIAL", f"ERROR: {e}")
    try: pr("GET_BY_TEXT_T1_SHORT", page.get_by_text(TARGET_1_SHORT, exact=False).count())
    except Exception as e: pr("GET_BY_TEXT_T1_SHORT", f"ERROR: {e}")
    try: pr("GET_BY_ROLE_LINK", page.get_by_role("link").count())
    except Exception as e: pr("GET_BY_ROLE_LINK", f"ERROR: {e}")

    # Final summary
    print("\n=== FINAL SUMMARY ===", flush=True)
    visual_dom_divergence = (body_info.get("target1_full") == False and body_info.get("ksheet_count", 0) == 0)
    pr("VISUAL_DOM_DIVERGENCE", "YES" if visual_dom_divergence else "NO")
    pr("CANVAS_COUNT", 0)
    pr("SVG_COUNT", svg_info.get("svg_count", 0))
    pr("OPEN_SHADOW_ROOT_COUNT", shadow_info.get("open_shadow", 0))
    pr("CLOSED_SHADOW_SUSPECTS", shadow_info.get("closed_shadow_suspects", 0))
    pr("CUSTOM_ELEMENT_TYPE_COUNT", len(custom_info))
    pr("DOM_NODE_COUNT", len(page.evaluate("() => document.querySelectorAll('*').length")))

    ctx.close()
    print("[DIVERGE] Done.", flush=True)
