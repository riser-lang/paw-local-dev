#!/usr/bin/env python3
"""R15-A.3P.1 Read-only pagination diagnostics.
Checks first page response structure and determines if PAGE 2 should be requested.
"""
import os, sys, json
os.environ.setdefault("DISPLAY", ":99")
sys.path.insert(0, "/opt/paw-ksheet-worker")
from playwright.sync_api import sync_playwright
from wps_drive_api import fetch_folder_listing

PROFILE = "/var/lib/paw-ksheet-worker/chromium-profile"
BROWSER = "/var/lib/paw-ksheet-worker/ms-playwright/chromium-1208/chrome-linux64/chrome"
SCOPE_URL = "https://365.kdocs.cn/ent/690174051/3009705063/485481025119"
GROUP_ID = "3009705063"
FOLDER_ID = "485481025119"

def main():
    print("=== PAGINATION DIAGNOSTICS ===", flush=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            executable_path=BROWSER, user_data_dir=str(PROFILE),
            headless=False, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                  "--no-first-run", "--no-default-browser-check"],
            viewport={"width": 1440, "height": 900}, locale="zh-CN")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        print(f"Navigating to {SCOPE_URL}", flush=True)
        try:
            page.goto(SCOPE_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"Navigation warning: {e}", flush=True)
        page.wait_for_timeout(12000)
        print(f"Page URL: {page.url}", flush=True)
        
        # Call first page
        print("\n--- PAGE 1 ---", flush=True)
        result = fetch_folder_listing(page, GROUP_ID, FOLDER_ID, 0, 100, None)
        print(f"PAGE_1_HTTP_STATUS={result.get('http_status', 0)}", flush=True)
        print(f"PAGE_1_ERROR={result.get('error', 'None')}", flush=True)
        print(f"PAGE_1_RAW_COUNT={result.get('raw_count', 0)}", flush=True)
        print(f"PAGE_1_FILES_KEY_PRESENT={result.get('files_key_present', False)}", flush=True)
        print(f"PAGE_1_NEXT_OFFSET_KEY_PRESENT={result.get('next_offset_key_present', False)}", flush=True)
        print(f"PAGE_1_NEXT_FILTER_KEY_PRESENT={result.get('next_filter_key_present', False)}", flush=True)
        
        next_offset = result.get("next_offset")
        next_filter = result.get("next_filter")
        print(f"PAGE_1_NEXT_OFFSET_VALUE={next_offset}", flush=True)
        print(f"PAGE_1_NEXT_FILTER_PRESENT={next_filter is not None}", flush=True)
        if next_filter is not None:
            print(f"PAGE_1_NEXT_FILTER_LENGTH={len(str(next_filter))}", flush=True)
        
        # Determine if PAGE 2 should be requested
        should_request = False
        if next_offset is not None and next_offset != 0 and result.get('raw_count', 0) > 0:
            should_request = True
        print(f"SHOULD_REQUEST_NEXT_PAGE={should_request}", flush=True)
        
        if should_request:
            print("\n--- PAGE 2 ---", flush=True)
            result2 = fetch_folder_listing(page, GROUP_ID, FOLDER_ID, next_offset, 100, next_filter)
            print(f"PAGE_2_HTTP_STATUS={result2.get('http_status', 0)}", flush=True)
            print(f"PAGE_2_ERROR={result2.get('error', 'None')}", flush=True)
            print(f"PAGE_2_RAW_COUNT={result2.get('raw_count', 0)}", flush=True)
            print(f"PAGE_2_FILES_KEY_PRESENT={result2.get('files_key_present', False)}", flush=True)
            print(f"PAGE_2_NEXT_OFFSET={result2.get('next_offset')}", flush=True)
        else:
            print("\nNo PAGE 2 needed — pagination complete after PAGE 1", flush=True)
            print(f"CURRENT_SCOPE_PAGINATION_COMPLETE_AFTER_PAGE_1=YES", flush=True)
        
        # Object type evidence
        print("\n--- OBJECT TYPE EVIDENCE ---", flush=True)
        files = result.get("files", [])
        for f in files:
            fid = f.get("fileid") or f.get("id")
            ftype = f.get("type")
            fname = f.get("fname") or f.get("name")
            print(f"FILE id={fid} name={fname} type={ftype} type_type={type(ftype).__name__}", flush=True)
        
        # Check parent folder type (list parentid=485433870456)
        print("\n--- FOLDER TYPE EVIDENCE (parent listing) ---", flush=True)
        parent_result = fetch_folder_listing(page, GROUP_ID, "485433870456", 0, 100, None)
        print(f"PARENT_HTTP_STATUS={parent_result.get('http_status', 0)}", flush=True)
        print(f"PARENT_RAW_COUNT={parent_result.get('raw_count', 0)}", flush=True)
        parent_files = parent_result.get("files", [])
        for f in parent_files:
            fid = f.get("fileid") or f.get("id")
            ftype = f.get("type")
            fname = f.get("fname") or f.get("name")
            print(f"PARENT_ENTRY id={fid} name={fname} type={ftype} type_type={type(ftype).__name__}", flush=True)
            if str(fid) == FOLDER_ID:
                print(f"KNOWN_FOLDER_LIST_ENTRY_TYPE_FIELD=type", flush=True)
                print(f"KNOWN_FOLDER_LIST_ENTRY_TYPE_VALUE={ftype}", flush=True)
        
        # Metadata endpoint evidence
        print("\n--- METADATA ENDPOINT EVIDENCE ---", flush=True)
        for obj_id, label in [(FOLDER_ID, "FOLDER"), ("485497734941", "FILE_1"), ("485497736526", "FILE_2")]:
            try:
                meta_js = f"""
                async () => {{
                    try {{
                        const resp = await fetch("/3rd/drive/api/v5/files/{obj_id}/metadata", {{
                            method: "GET", credentials: "include",
                            headers: {{ "Accept": "application/json" }}
                        }});
                        const status = resp.status;
                        if (!resp.ok) return {{ http_status: status, error: "HTTP_ERROR" }};
                        const data = await resp.json();
                        const keys = Object.keys(data);
                        const hasFileinfo = Object.prototype.hasOwnProperty.call(data, "fileinfo");
                        const hasFolderinfo = Object.prototype.hasOwnProperty.call(data, "folderinfo");
                        return {{ http_status: status, keys: keys, hasFileinfo: hasFileinfo, hasFolderinfo: hasFolderinfo }};
                    }} catch (e) {{
                        return {{ http_status: 0, error: e.message }};
                    }}
                }}
                """
                meta_result = page.evaluate(meta_js)
                print(f"{label}_METADATA_HTTP_STATUS={meta_result.get('http_status', 0)}", flush=True)
                print(f"{label}_METADATA_KEYS={meta_result.get('keys', [])}", flush=True)
                print(f"{label}_METADATA_FILEINFO_PRESENT={meta_result.get('hasFileinfo', False)}", flush=True)
                print(f"{label}_METADATA_FOLDERINFO_PRESENT={meta_result.get('hasFolderinfo', False)}", flush=True)
            except Exception as e:
                print(f"{label}_METADATA_ERROR={e}", flush=True)
        
        ctx.close()
    print("\n=== DIAGNOSTICS COMPLETE ===", flush=True)

if __name__ == "__main__":
    main()
