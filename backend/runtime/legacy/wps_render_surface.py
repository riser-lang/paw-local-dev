#!/usr/bin/env python3
"""R15-A.1H.3C — File List Render Surface Discovery."""
import json, time
from playwright.sync_api import sync_playwright

PROFILE_DIR = "/var/lib/paw-ksheet-worker/chromium-profile"
BROWSER_EXECUTABLE = "/var/lib/paw-ksheet-worker/ms-playwright/chromium-1208/chrome-linux64/chrome"
FOLDER_URL = "https://365.kdocs.cn/ent/690174051/3009705063/485481025119"
FOLDER_ID = "485481025119"
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

    print(f"[SURFACE] Navigating to {FOLDER_URL} ...", flush=True)
    page.goto(FOLDER_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(15)

    url = page.url
    pr("PAGE_URL", url)

    # STEP 2 — Frame Inventory
    print("\n=== STEP 2: FRAME INVENTORY ===", flush=True)
    frames = page.frames
    pr("FRAME_COUNT", len(frames))
    for i, frame in enumerate(frames):
        frame_url = frame.url
        from urllib.parse import urlparse
        parsed = urlparse(frame_url)
        host = parsed.hostname or ""
        path = parsed.path or ""
        name = frame.name or ""
        print(f"  FRAME[{i}] name={name} host={host} path={path}", flush=True)

    # STEP 3 — Search Targets in Every Frame
    print("\n=== STEP 3: SEARCH TARGETS IN EVERY FRAME ===", flush=True)
    target_frame_idx = -1
    target_frame_host = ""
    target_frame_path = ""

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

        print(f"  FRAME[{i}] TARGET_1_EXACT={t1_exact} TARGET_1_PARTIAL={t1_partial} TARGET_2_EXACT={t2_exact} TARGET_2_PARTIAL={t2_partial}", flush=True)

        if t1_exact > 0 or t1_partial > 0 or t2_exact > 0 or t2_partial > 0:
            target_frame_idx = i
            parsed = urlparse(frame.url)
            target_frame_host = parsed.hostname or ""
            target_frame_path = parsed.path or ""

    pr("TARGET_RENDER_FRAME_FOUND", "YES" if target_frame_idx >= 0 else "NO")
    pr("TARGET_RENDER_FRAME_INDEX", target_frame_idx)
    pr("TARGET_RENDER_FRAME_HOST", target_frame_host)
    pr("TARGET_RENDER_FRAME_PATH", target_frame_path)

    # STEP 4 — Safe Ancestor Inspection (if found)
    print("\n=== STEP 4: SAFE ANCESTOR INSPECTION ===", flush=True)
    t1_visible = False
    t2_visible = False
    t1_ancestors = []
    t2_ancestors = []

    if target_frame_idx >= 0:
        frame = frames[target_frame_idx]
        for target_name, label in [(TARGET_1, "TARGET_1"), (TARGET_2, "TARGET_2")]:
            try:
                count = frame.get_by_text(target_name, exact=False).count()
                if count > 0:
                    if label == "TARGET_1":
                        t1_visible = True
                    else:
                        t2_visible = True
                    ancestors = frame.evaluate("""
                        (targetName) => {
                            var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
                            while (walker.nextNode()) {
                                if (walker.currentNode.textContent.indexOf(targetName) >= 0) {
                                    var el = walker.currentNode.parentElement;
                                    var ancestors = [];
                                    for (var i = 0; i < 10 && el; i++) {
                                        var info = {
                                            level: i,
                                            tag: el.tagName,
                                            role: el.getAttribute('role') || '',
                                            class: (el.className || '').toString().substring(0, 150),
                                            title: el.getAttribute('title') || '',
                                            aria_label: el.getAttribute('aria-label') || '',
                                        };
                                        var dataAttrs = ['data-fileid', 'data-file-id', 'data-id', 'data-fid', 'data-objid', 'data-key', 'data-index'];
                                        for (var da of dataAttrs) {
                                            var val = el.getAttribute(da);
                                            if (val) info[da] = val.substring(0, 50);
                                        }
                                        if (el.tagName === 'A' && el.href) {
                                            try { info.href_path = new URL(el.href).pathname; } catch(e) {}
                                        }
                                        ancestors.push(info);
                                        el = el.parentElement;
                                    }
                                    return ancestors;
                                }
                            }
                            return [];
                        }
                    """, target_name)
                    if label == "TARGET_1":
                        t1_ancestors = ancestors
                    else:
                        t2_ancestors = ancestors
                    print(f"  {label}_ANCESTORS:", flush=True)
                    for a in ancestors[:10]:
                        print(f"    {json.dumps(a, ensure_ascii=False)}", flush=True)
            except Exception as e:
                print(f"  {label} search error: {e}", flush=True)

    pr("TARGET_1_VISIBLE", "YES" if t1_visible else "NO")
    pr("TARGET_2_VISIBLE", "YES" if t2_visible else "NO")

    # STEP 5 — Shadow / Micro Frontend Check
    print("\n=== STEP 5: SHADOW / MICRO FRONTEND CHECK ===", flush=True)
    shadow_info = page.evaluate("""
        () => {
            var iframes = document.querySelectorAll('iframe');
            var customEls = document.querySelectorAll('*');
            var shadowRoots = [];
            var iframesInfo = [];
            for (var iframe of iframes) {
                iframesInfo.push({tag: 'iframe', src: (iframe.src || '').substring(0, 100), class: (iframe.className || '').toString().substring(0, 100), id: iframe.id});
            }
            // Check for open shadow roots
            var allEls = document.querySelectorAll('*');
            for (var el of allEls) {
                if (el.shadowRoot) {
                    shadowRoots.push({tag: el.tagName, class: (el.className || '').toString().substring(0, 100), id: el.id});
                }
            }
            return {iframe_count: iframes.length, iframes: iframesInfo.slice(0, 10), shadow_root_count: shadowRoots.length, shadow_roots: shadowRoots.slice(0, 10)};
        }
    """)
    pr("OPEN_SHADOW_ROOT_COUNT", shadow_info.get("shadow_root_count", 0))
    print(f"  IFRAME_COUNT={shadow_info.get('iframe_count', 0)}", flush=True)
    for iframe in shadow_info.get("iframes", [])[:5]:
        print(f"  IFRAME: {json.dumps(iframe, ensure_ascii=False)}", flush=True)
    for sr in shadow_info.get("shadow_roots", [])[:5]:
        print(f"  SHADOW_ROOT: {json.dumps(sr, ensure_ascii=False)}", flush=True)

    # STEP 6 — Identify File Viewport
    print("\n=== STEP 6: IDENTIFY FILE VIEWPORT ===", flush=True)
    viewport_info = page.evaluate("""
        () => {
            var scrollables = [];
            var allEls = document.querySelectorAll('*');
            for (var el of allEls) {
                if (el.scrollHeight > el.clientHeight + 50 && el.clientHeight > 100) {
                    var style = window.getComputedStyle(el);
                    if (style.overflow === 'auto' || style.overflow === 'scroll' || style.overflowY === 'auto' || style.overflowY === 'scroll') {
                        scrollables.push({
                            tag: el.tagName,
                            class: (el.className || '').toString().substring(0, 150),
                            scrollHeight: el.scrollHeight,
                            clientHeight: el.clientHeight,
                            scrollTop: el.scrollTop,
                        });
                    }
                }
            }
            return scrollables.sort((a, b) => b.scrollHeight - a.scrollHeight).slice(0, 10);
        }
    """)

    file_viewport_found = len(viewport_info) > 0
    pr("FILE_VIEWPORT_FOUND", "YES" if file_viewport_found else "NO")
    if viewport_info:
        vp = viewport_info[0]
        pr("FILE_VIEWPORT_TAG", vp.get("tag", ""))
        pr("FILE_VIEWPORT_CLASS", vp.get("class", ""))
        pr("FILE_VIEWPORT_SCROLL_HEIGHT", vp.get("scrollHeight", 0))
        pr("FILE_VIEWPORT_CLIENT_HEIGHT", vp.get("clientHeight", 0))
        print(f"  ALL VIEWPORTS:", flush=True)
        for v in viewport_info[:5]:
            print(f"    {json.dumps(v, ensure_ascii=False)}", flush=True)

    # STEP 7 — Controlled Virtual List Test
    print("\n=== STEP 7: CONTROLLED VIRTUAL LIST TEST ===", flush=True)
    virtual_scroll_used = False

    if file_viewport_found and viewport_info[0]["scrollHeight"] > viewport_info[0]["clientHeight"]:
        vp_class = viewport_info[0].get("class", "")
        # Try to find the scrollable element and scroll it
        for scroll_attempt in range(10):
            try:
                # Search targets in all frames before scroll
                found_t1 = False
                found_t2 = False
                for frame in frames:
                    try:
                        if frame.get_by_text(TARGET_1, exact=False).count() > 0:
                            found_t1 = True
                        if frame.get_by_text(TARGET_2, exact=False).count() > 0:
                            found_t2 = True
                    except Exception:
                        pass

                if found_t1 and found_t2:
                    t1_visible = True
                    t2_visible = True
                    break

                # Scroll the viewport
                page.evaluate("""
                    () => {
                        var allEls = document.querySelectorAll('*');
                        for (var el of allEls) {
                            var style = window.getComputedStyle(el);
                            if ((style.overflow === 'auto' || style.overflow === 'scroll' || style.overflowY === 'auto' || style.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 50 && el.clientHeight > 100) {
                                el.scrollTop += Math.floor(el.clientHeight * 0.7);
                                return;
                            }
                        }
                    }
                """)
                virtual_scroll_used = True
                time.sleep(0.8)
            except Exception:
                break

    pr("VIRTUAL_SCROLL_USED", "YES" if virtual_scroll_used else "NO")
    pr("TARGET_1_VISIBLE", "YES" if t1_visible else "NO")
    pr("TARGET_2_VISIBLE", "YES" if t2_visible else "NO")

    # Re-check ancestors after scroll if found
    if t1_visible and not t1_ancestors:
        for frame in frames:
            try:
                if frame.get_by_text(TARGET_1, exact=False).count() > 0:
                    t1_ancestors = frame.evaluate("""
                        (targetName) => {
                            var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
                            while (walker.nextNode()) {
                                if (walker.currentNode.textContent.indexOf(targetName) >= 0) {
                                    var el = walker.currentNode.parentElement;
                                    var ancestors = [];
                                    for (var i = 0; i < 10 && el; i++) {
                                        var info = {tag: el.tagName, class: (el.className || '').toString().substring(0, 150)};
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
                    """, TARGET_1)
                    break
            except Exception:
                pass

    if t2_visible and not t2_ancestors:
        for frame in frames:
            try:
                if frame.get_by_text(TARGET_2, exact=False).count() > 0:
                    t2_ancestors = frame.evaluate("""
                        (targetName) => {
                            var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
                            while (walker.nextNode()) {
                                if (walker.currentNode.textContent.indexOf(targetName) >= 0) {
                                    var el = walker.currentNode.parentElement;
                                    var ancestors = [];
                                    for (var i = 0; i < 10 && el; i++) {
                                        var info = {tag: el.tagName, class: (el.className || '').toString().substring(0, 150)};
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
                    """, TARGET_2)
                    break
            except Exception:
                pass

    # STEP 8 — Target 2 ID
    print("\n=== STEP 8: TARGET 2 ID ===", flush=True)
    t2_fileid = "NOT_FOUND"
    t2_fileid_source = "NOT_AVAILABLE_FROM_CURRENT_DOM_STATE"

    if t2_ancestors:
        for a in t2_ancestors:
            for k, v in a.items():
                if k.startswith("data-") and v and v.isdigit():
                    t2_fileid = v
                    t2_fileid_source = f"DOM.{k}"
                    break
            if t2_fileid != "NOT_FOUND":
                break

    pr("TARGET_2_FILEID", t2_fileid)
    pr("TARGET_2_FILEID_SOURCE", t2_fileid_source)

    # STEP 9 — Real Directory Enumeration
    print("\n=== STEP 9: REAL DIRECTORY ENUMERATION ===", flush=True)
    file_row_selector = "NOT_DETERMINED"
    dir_entries = []

    # Infer selector from ancestors
    for ancestors in [t1_ancestors, t2_ancestors]:
        if not ancestors:
            continue
        for a in ancestors:
            cls = a.get("class", "")
            if any(kw in cls.lower() for kw in ["file", "item", "row", "list", "entry", "card"]):
                classes = cls.split()
                if classes:
                    file_row_selector = "." + ".".join(classes[:3])
                    break
        if file_row_selector != "NOT_DETERMINED":
            break

    pr("FILE_ROW_SELECTOR", file_row_selector)

    if file_row_selector != "NOT_DETERMINED":
        for frame in frames:
            try:
                dir_entries = frame.evaluate("""
                    (selector) => {
                        var entries = [];
                        var els = document.querySelectorAll(selector);
                        for (var el of els) {
                            var name = el.getAttribute('title') || el.getAttribute('data-name') || el.getAttribute('data-fname') || '';
                            if (!name) {
                                var textEl = el.querySelector('[class*="name"], [class*="title"], span, a');
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
                if dir_entries:
                    break
            except Exception:
                pass

    pr("DIRECTORY_VISIBLE_ENTRY_COUNT", len(dir_entries))
    for i, entry in enumerate(dir_entries[:20]):
        print(f"  DIR_ENTRY[{i}]: {json.dumps(entry, ensure_ascii=False)}", flush=True)

    # Final output
    print("\n=== FINAL OUTPUT ===", flush=True)
    render_discovery = (target_frame_idx >= 0) or file_viewport_found or (file_row_selector != "NOT_DETERMINED")
    scoped = (render_discovery and t1_visible and t2_visible and t2_fileid != "NOT_FOUND" and file_row_selector != "NOT_DETERMINED")

    print(f"R15_A1_H3C_RESULT=", flush=True)
    print(f"PAGE_URL={url}", flush=True)
    print(f"FRAME_COUNT={len(frames)}", flush=True)
    print(f"TARGET_RENDER_FRAME_FOUND={'YES' if target_frame_idx >= 0 else 'NO'}", flush=True)
    print(f"TARGET_RENDER_FRAME_INDEX={target_frame_idx}", flush=True)
    print(f"TARGET_RENDER_FRAME_HOST={target_frame_host}", flush=True)
    print(f"TARGET_RENDER_FRAME_PATH={target_frame_path}", flush=True)
    print(f"OPEN_SHADOW_ROOT_COUNT={shadow_info.get('shadow_root_count', 0)}", flush=True)
    print(f"FILE_VIEWPORT_FOUND={'YES' if file_viewport_found else 'NO'}", flush=True)
    print(f"FILE_VIEWPORT_CLASS={viewport_info[0].get('class', '') if viewport_info else ''}", flush=True)
    print(f"VIRTUAL_SCROLL_USED={'YES' if virtual_scroll_used else 'NO'}", flush=True)
    print(f"TARGET_1_VISIBLE={'YES' if t1_visible else 'NO'}", flush=True)
    print(f"TARGET_1_FILEID=485497736526", flush=True)
    print(f"TARGET_2_VISIBLE={'YES' if t2_visible else 'NO'}", flush=True)
    print(f"TARGET_2_FILEID={t2_fileid}", flush=True)
    print(f"TARGET_2_FILEID_SOURCE={t2_fileid_source}", flush=True)
    print(f"FILE_ROW_SELECTOR={file_row_selector}", flush=True)
    print(f"DIRECTORY_VISIBLE_ENTRY_COUNT={len(dir_entries)}", flush=True)
    print(f"RENDER_SURFACE_DISCOVERY={'YES' if render_discovery else 'NO'}", flush=True)
    print(f"SCOPED_FOLDER_ENUMERATION={'YES' if scoped else 'PARTIAL'}", flush=True)
    print(f"WINDOWS_BRIDGE_USED=NO", flush=True)
    print(f"SEARCH_API_USED=NO", flush=True)
    print(f"ROAMING_API_USED=NO", flush=True)
    print(f"FULL_HTML_SAVED=NO", flush=True)
    print(f"FULL_STATE_SAVED=NO", flush=True)
    print(f"RAW_SECRET_PRINTED=NO", flush=True)
    print(f"CANONICAL_ID_STATUS=UNDECIDED", flush=True)
    print(f"R15_A2_ALLOWED=NO", flush=True)
    blockers = []
    if not render_discovery: blockers.append("RENDER_SURFACE_NOT_DISCOVERED")
    if not t1_visible: blockers.append("TARGET_1_NOT_VISIBLE")
    if not t2_visible: blockers.append("TARGET_2_NOT_VISIBLE")
    if t2_fileid == "NOT_FOUND": blockers.append("TARGET_2_FILEID_NOT_FOUND")
    if file_row_selector == "NOT_DETERMINED": blockers.append("FILE_ROW_SELECTOR_NOT_DETERMINED")
    print(f"BLOCKERS={','.join(blockers) if blockers else 'NONE'}", flush=True)

    ctx.close()
    print("[SURFACE] Done.", flush=True)
