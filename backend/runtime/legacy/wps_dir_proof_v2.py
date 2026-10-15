#!/usr/bin/env python3
"""R15-A.1H.3B — Targeted DOM + State Correlation Fix."""
import json, time
from playwright.sync_api import sync_playwright

PROFILE_DIR = "/var/lib/paw-ksheet-worker/chromium-profile"
BROWSER_EXECUTABLE = "/var/lib/paw-ksheet-worker/ms-playwright/chromium-1208/chrome-linux64/chrome"
FOLDER_URL = "https://365.kdocs.cn/ent/690174051/3009705063/485481025119"
FOLDER_ID = "485481025119"
FOLDER_NAME = "\u8fd0\u8425\u5bfc\u5e08\u6570\u636e"
TARGET_1 = "\u8fd0\u8425\u5bfc\u5e08\u6570\u636e\u770b\u677f.ksheet"
TARGET_2 = "\u8fd0\u8425\u5bfc\u5e08\u6240\u5e26\u5f92\u5f1f\u79bb\u804c\u60c5\u51b5\u8868.ksheet"
WHITELIST = ["id", "fileid", "parentid", "fname", "name", "type", "ext", "fver", "fsize", "mtime", "ctime", "groupid"]


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

    print(f"[PROOF_V2] Navigating to {FOLDER_URL} ...", flush=True)
    page.goto(FOLDER_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(15)

    url = page.url
    pr("PAGE_URL", url)

    # STEP 2 — Current Folder Name Proof
    print("\n=== STEP 2: CURRENT FOLDER NAME PROOF ===", flush=True)
    folder_name_result = page.evaluate("""
        (targetName) => {
            var sources = [];
            // Check breadcrumb
            var breadcrumbs = document.querySelectorAll('[class*="breadcrumb"], [class*="Breadcrumb"], [class*="crumb"], [class*="Crumb"], [class*="nav-path"], [class*="filepath"], [class*="path"]');
            for (var el of breadcrumbs) {
                var text = el.textContent.trim();
                if (text.indexOf(targetName) >= 0) {
                    sources.push({source: 'breadcrumb', text: text.substring(0, 200)});
                }
            }
            // Check headings
            var headings = document.querySelectorAll('h1, h2, h3, h4, [class*="title"], [class*="Title"], [class*="heading"], [class*="name"]');
            for (var el of headings) {
                var text = el.textContent.trim();
                if (text === targetName || text.indexOf(targetName) >= 0) {
                    if (text.length < 100) {
                        sources.push({source: 'heading', tag: el.tagName, text: text});
                    }
                }
            }
            // Check aria-label and title attributes
            var allEls = document.querySelectorAll('[aria-label], [title]');
            for (var el of allEls) {
                var al = el.getAttribute('aria-label') || '';
                var ti = el.getAttribute('title') || '';
                if (al === targetName || ti === targetName) {
                    sources.push({source: 'attr', tag: el.tagName, aria_label: al, title: ti, class: (el.className || '').toString().substring(0, 100)});
                }
            }
            return sources;
        }
    """, FOLDER_NAME)

    folder_name_visible = len(folder_name_result) > 0
    folder_name_source = folder_name_result[0]["source"] if folder_name_result else "NOT_FOUND"
    pr("CURRENT_FOLDER_NAME_VISIBLE", "YES" if folder_name_visible else "NO")
    pr("CURRENT_FOLDER_NAME_SOURCE", folder_name_source)
    pr("CURRENT_FOLDER_NAME", FOLDER_NAME if folder_name_visible else "NOT_PROVEN")
    if folder_name_result:
        print(f"  DETAIL: {json.dumps(folder_name_result[:3], ensure_ascii=False)}", flush=True)
    pr("CURRENT_FOLDER_NAME_PROVEN", "YES" if folder_name_visible else "NO")

    # STEP 3 — Exact Target Locators
    print("\n=== STEP 3: EXACT TARGET LOCATORS ===", flush=True)

    def find_target_in_dom(target_name, target_label):
        """Use Playwright to find exact text, then inspect ancestors."""
        try:
            # Try exact text match
            locator = page.get_by_text(target_name, exact=True)
            count = locator.count()
            pr(f"{target_label}_TEXT_COUNT", count)
            if count == 0:
                # Try partial text match
                locator = page.get_by_text(target_name, exact=False)
                count = locator.count()
                pr(f"{target_label}_TEXT_COUNT_PARTIAL", count)
            if count > 0:
                pr(f"{target_label}_VISIBLE", "YES")
                # Get ancestor info from first match
                ancestor_info = page.evaluate("""
                    (targetName) => {
                        var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
                        while (walker.nextNode()) {
                            if (walker.currentNode.textContent.trim() === targetName || walker.currentNode.textContent.indexOf(targetName) >= 0) {
                                var el = walker.currentNode.parentElement;
                                var ancestors = [];
                                for (var i = 0; i < 8 && el; i++) {
                                    var info = {
                                        level: i,
                                        tag: el.tagName,
                                        role: el.getAttribute('role') || '',
                                        class: (el.className || '').toString().substring(0, 150),
                                        title: el.getAttribute('title') || '',
                                        aria_label: el.getAttribute('aria-label') || '',
                                    };
                                    // Collect data-* attributes
                                    for (var attr of el.attributes) {
                                        if (attr.name.startsWith('data-')) {
                                            var val = attr.value;
                                            if (/^[0-9]+$/.test(val) || val.length < 50) {
                                                info[attr.name] = val.substring(0, 50);
                                            }
                                        }
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
                print(f"  {target_label}_ANCESTORS:", flush=True)
                for a in ancestor_info[:8]:
                    print(f"    {json.dumps(a, ensure_ascii=False)}", flush=True)
                return True, ancestor_info
            else:
                pr(f"{target_label}_VISIBLE", "NO")
                return False, []
        except Exception as e:
            pr(f"{target_label}_VISIBLE", f"ERROR: {e}")
            return False, []

    t1_visible, t1_ancestors = find_target_in_dom(TARGET_1, "TARGET_1")
    t2_visible, t2_ancestors = find_target_in_dom(TARGET_2, "TARGET_2")

    # STEP 4 — Infer Real File Row Selector
    print("\n=== STEP 4: INFER FILE ROW SELECTOR ===", flush=True)
    file_row_selector = "NOT_DETERMINED"
    dir_entries = []

    # Find common ancestor class from target ancestors
    for ancestors in [t1_ancestors, t2_ancestors]:
        if not ancestors:
            continue
        for a in ancestors:
            cls = a.get("class", "")
            tag = a.get("tag", "")
            role = a.get("role", "")
            # Look for file row patterns
            if any(kw in cls.lower() for kw in ["file", "item", "row", "list", "entry", "card"]):
                # Build selector from class
                classes = cls.split()
                if classes:
                    file_row_selector = "." + ".".join(classes[:3])
                    break
        if file_row_selector != "NOT_DETERMINED":
            break

    pr("FILE_ROW_SELECTOR", file_row_selector)

    if file_row_selector != "NOT_DETERMINED":
        dir_entries = page.evaluate("""
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
                    if (link) {
                        try { entry.href_path = new URL(link.href).pathname; } catch(e) {}
                    }
                    entries.push(entry);
                }
                return entries;
            }
        """, file_row_selector)

    pr("DIRECTORY_VISIBLE_ENTRY_COUNT", len(dir_entries))
    for i, entry in enumerate(dir_entries[:20]):
        print(f"  DIR_ENTRY[{i}]: {json.dumps(entry, ensure_ascii=False)}", flush=True)

    # STEP 5 — Recursive State Search
    print("\n=== STEP 5: RECURSIVE STATE SEARCH ===", flush=True)

    state = page.evaluate("() => window.__INITIAL_STATE__")

    def recursive_search(obj, target_name, path="", parent=None, grandparent=None, results=None, depth=0):
        if results is None:
            results = []
        if depth > 10 or len(results) >= 5:
            return results
        if isinstance(obj, dict):
            # Check if this object has fname/name/title matching target
            for key in ["fname", "name", "title"]:
                if key in obj and isinstance(obj[key], str) and obj[key] == target_name:
                    # Extract whitelist fields from current, parent, grandparent
                    safe = {}
                    for src_label, src_obj in [("self", obj), ("parent", parent), ("grandparent", grandparent)]:
                        if src_obj and isinstance(src_obj, dict):
                            for wk in WHITELIST:
                                if wk in src_obj:
                                    safe[f"{src_label}.{wk}"] = src_obj[wk]
                    results.append({"path": path, "fields": safe})
                    break
            # Recurse into children
            for k, v in obj.items():
                recursive_search(v, target_name, f"{path}.{k}", obj, parent, results, depth + 1)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                recursive_search(item, target_name, f"{path}[{i}]", obj, parent, results, depth + 1)
        return results

    t1_state = recursive_search(state, TARGET_1)
    t2_state = recursive_search(state, TARGET_2)

    pr("TARGET_1_STATE_MATCHES", len(t1_state))
    for i, m in enumerate(t1_state[:3]):
        print(f"  TARGET_1_STATE[{i}]: path={m['path']} fields={json.dumps(m['fields'], ensure_ascii=False)}", flush=True)

    pr("TARGET_2_STATE_MATCHES", len(t2_state))
    for i, m in enumerate(t2_state[:3]):
        print(f"  TARGET_2_STATE[{i}]: path={m['path']} fields={json.dumps(m['fields'], ensure_ascii=False)}", flush=True)

    # Extract IDs
    def get_field(matches, field_names):
        for m in matches:
            for fn in field_names:
                for prefix in ["self", "parent", "grandparent"]:
                    key = f"{prefix}.{fn}"
                    if key in m["fields"]:
                        return m["fields"][key], f"{prefix}.{fn}"
        return "NOT_FOUND", "NOT_FOUND"

    t1_fileid, t1_fileid_src = get_field(t1_state, ["fileid", "id"])
    t1_parentid, _ = get_field(t1_state, ["parentid"])
    t1_state_path = t1_state[0]["path"] if t1_state else "NOT_FOUND"

    t2_fileid, t2_fileid_src = get_field(t2_state, ["fileid", "id"])
    t2_parentid, _ = get_field(t2_state, ["parentid"])
    t2_state_path = t2_state[0]["path"] if t2_state else "NOT_FOUND"

    # If not found in state, try DOM ancestors
    if str(t1_fileid) == "NOT_FOUND" and t1_ancestors:
        for a in t1_ancestors:
            for k, v in a.items():
                if k.startswith("data-") and v and v.isdigit():
                    t1_fileid = v
                    t1_fileid_src = f"DOM.{k}"
                    break
            if str(t1_fileid) != "NOT_FOUND":
                break

    if str(t2_fileid) == "NOT_FOUND" and t2_ancestors:
        for a in t2_ancestors:
            for k, v in a.items():
                if k.startswith("data-") and v and v.isdigit():
                    t2_fileid = v
                    t2_fileid_src = f"DOM.{k}"
                    break
            if str(t2_fileid) != "NOT_FOUND":
                break

    if str(t2_fileid) == "NOT_FOUND":
        t2_fileid_src = "NOT_AVAILABLE_FROM_CURRENT_DOM_STATE"

    # STEP 6 — Target 2 handling
    print("\n=== STEP 6: TARGET 2 ===", flush=True)
    pr("TARGET_2_VISIBLE", "YES" if t2_visible else "NO")
    pr("TARGET_2_FILEID", t2_fileid)
    pr("TARGET_2_FILEID_SOURCE", t2_fileid_src)

    # STEP 7 — Directory Proof
    print("\n=== STEP 7: DIRECTORY PROOF ===", flush=True)
    url_match = FOLDER_ID in url
    name_proven = folder_name_visible
    t1_dom_visible = t1_visible
    t1_parentid_match = str(t1_parentid) == FOLDER_ID

    folder_confirmed = url_match and name_proven and t1_dom_visible and t1_parentid_match
    pr("FOLDER_485481025119_CONFIRMED", "YES" if folder_confirmed else "NO")

    # STEP 8 — Scoped Enumeration
    print("\n=== STEP 8: SCOPED ENUMERATION ===", flush=True)
    both_visible = t1_visible and t2_visible
    both_have_ids = (str(t1_fileid) != "NOT_FOUND") and (str(t2_fileid) != "NOT_FOUND")
    both_parentid_match = (str(t1_parentid) == FOLDER_ID) and (str(t2_parentid) == FOLDER_ID)
    selector_confirmed = file_row_selector != "NOT_DETERMINED"

    scoped = name_proven and both_visible and both_have_ids and both_parentid_match and selector_confirmed
    pr("SCOPED_FOLDER_ENUMERATION", "YES" if scoped else "PARTIAL")

    # Final output
    print("\n=== FINAL OUTPUT ===", flush=True)
    print(f"R15_A1_H3B_RESULT=", flush=True)
    print(f"PAGE_URL={url}", flush=True)
    print(f"CURRENT_FOLDER_ID={FOLDER_ID}", flush=True)
    print(f"CURRENT_FOLDER_NAME_PROVEN={'YES' if name_proven else 'NO'}", flush=True)
    print(f"CURRENT_FOLDER_NAME={FOLDER_NAME if name_proven else 'NOT_PROVEN'}", flush=True)
    print(f"CURRENT_FOLDER_NAME_SOURCE={folder_name_source}", flush=True)
    print(f"FILE_ROW_SELECTOR={file_row_selector}", flush=True)
    print(f"DIRECTORY_VISIBLE_ENTRY_COUNT={len(dir_entries)}", flush=True)
    print(f"TARGET_1_VISIBLE={'YES' if t1_visible else 'NO'}", flush=True)
    print(f"TARGET_1_FILEID={t1_fileid}", flush=True)
    print(f"TARGET_1_FILEID_SOURCE={t1_fileid_src}", flush=True)
    print(f"TARGET_1_PARENTID={t1_parentid}", flush=True)
    print(f"TARGET_1_STATE_PATH={t1_state_path}", flush=True)
    print(f"TARGET_2_VISIBLE={'YES' if t2_visible else 'NO'}", flush=True)
    print(f"TARGET_2_FILEID={t2_fileid}", flush=True)
    print(f"TARGET_2_FILEID_SOURCE={t2_fileid_src}", flush=True)
    print(f"TARGET_2_PARENTID={t2_parentid}", flush=True)
    print(f"TARGET_2_STATE_PATH={t2_state_path}", flush=True)
    print(f"FOLDER_485481025119_CONFIRMED={'YES' if folder_confirmed else 'NO'}", flush=True)
    print(f"SCOPED_FOLDER_ENUMERATION={'YES' if scoped else 'PARTIAL'}", flush=True)
    print(f"WINDOWS_BRIDGE_USED=NO", flush=True)
    print(f"SEARCH_API_USED_FOR_ENUMERATION=NO", flush=True)
    print(f"ROAMING_API_USED_FOR_ENUMERATION=NO", flush=True)
    print(f"FULL_HTML_SAVED=NO", flush=True)
    print(f"FULL_STATE_SAVED=NO", flush=True)
    print(f"RAW_SECRET_PRINTED=NO", flush=True)
    print(f"CANONICAL_ID_STATUS=UNDECIDED", flush=True)
    print(f"R15_A2_ALLOWED=NO", flush=True)
    blockers = []
    if not name_proven: blockers.append("FOLDER_NAME_NOT_PROVEN")
    if not t1_visible: blockers.append("TARGET_1_NOT_IN_DOM")
    if not t2_visible: blockers.append("TARGET_2_NOT_IN_DOM")
    if str(t1_fileid) == "NOT_FOUND": blockers.append("TARGET_1_FILEID_NOT_FOUND")
    if str(t2_fileid) == "NOT_FOUND": blockers.append("TARGET_2_FILEID_NOT_FOUND")
    if not both_parentid_match: blockers.append("PARENTID_MISMATCH")
    if not selector_confirmed: blockers.append("FILE_ROW_SELECTOR_NOT_DETERMINED")
    print(f"BLOCKERS={','.join(blockers) if blockers else 'NONE'}", flush=True)

    ctx.close()
    print("[PROOF_V2] Done.", flush=True)
