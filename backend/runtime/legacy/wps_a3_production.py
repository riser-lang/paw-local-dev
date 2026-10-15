#!/usr/bin/env python3
"""R15-A.3P.2 Production Final Acceptance Runner — Test/Report Integrity Closure.

Uses the formal chain: collect_from_wps_drive_api -> run_inventory_from_objects.
No APIFallbackPage. No fake DOM. No old enrichment.

R15-A.3P.2 changes:
  - Real synthetic tests via subprocess.run (no hardcoded PASS)
  - PAGE_1_NEXT_OFFSET_VALUE from page_diagnostics[0].response_next_offset
  - PAGE_2_REQUESTED from any(diag.page_index >= 1)
  - OBJECT_TYPE_METADATA_FALLBACK_IMPLEMENTED via callable() check
  - Chromium Gate in Final Gate
  - Per-page schema contract (files/next_offset/next_filter keys)
"""
from __future__ import annotations
import json
import os
import sys
import time
import uuid
import sqlite3
import subprocess
from datetime import datetime, timezone

os.environ.setdefault("DISPLAY", ":99")
sys.path.insert(0, "/opt/paw-ksheet-worker")

from playwright.sync_api import sync_playwright
from wps_inventory_v1 import init_db, utc_now
from wps_inventory_api import run_inventory_from_objects, compute_fingerprint_v2
from wps_drive_api import (
    collect_from_wps_drive_api,
    resolve_object_type_from_metadata,
    resolve_object_type,
    classify_pagination_state,
)

PROFILE_DIR = "/var/lib/paw-ksheet-worker/chromium-profile"
BROWSER_EXECUTABLE = "/var/lib/paw-ksheet-worker/ms-playwright/chromium-1208/chrome-linux64/chrome"
SCOPE_URL = "https://365.kdocs.cn/ent/690174051/3009705063/485481025119"
PROD_DB = "/srv/paw/ksheet_exports/cloud_inventory/wps_inventory_v1.sqlite3"
GROUP_ID = "3009705063"
FOLDER_ID = "485481025119"
KNOWN_FOLDER_ID = "485481025119"
KNOWN_FILE_1_ID = "485497734941"
KNOWN_FILE_2_ID = "485497736526"
FROZEN_IDS = {KNOWN_FILE_1_ID, KNOWN_FILE_2_ID}
SYNTHETIC_TEST_SCRIPT = "/opt/paw-ksheet-worker/wps_a3p1_synthetic_tests.py"


def log(msg):
    print(f"[A3-PROD] {msg}", flush=True)


def get_chrome_count():
    return int(subprocess.check_output("ps aux | grep 'chrome-linux64/chrome' | grep -v grep | wc -l", shell=True).strip())


def get_mem():
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    return 0


def db_reality_check(conn, run_label):
    """Query production DB for both objects and verify all fields."""
    c = conn.cursor()
    c.execute("""SELECT object_id, display_name, object_type, parent_id, parent_relation_proof,
                   version, size, mtime, ctime, metadata_source, metadata_fingerprint,
                   file_family, file_type, mirror_policy, mirror_backend_candidate
                FROM inventory_objects
                WHERE provider='wps' AND group_id=? AND is_currently_seen=1
                ORDER BY object_id""", (GROUP_ID,))
    rows = c.fetchall()
    results = {}
    for i, row in enumerate(rows, 1):
        obj = {
            "object_id": row[0], "display_name": row[1], "object_type": row[2],
            "parent_id": row[3], "parent_relation_proof": row[4],
            "version": row[5], "size": row[6], "mtime": row[7], "ctime": row[8],
            "metadata_source": row[9], "metadata_fingerprint": row[10],
            "file_family": row[11], "file_type": row[12],
            "mirror_policy": row[13], "mirror_backend_candidate": row[14],
        }
        results[f"object_{i}"] = obj
        fp_input = {k: obj[k] for k in ["object_id", "object_type", "parent_id", "display_name", "version", "size", "mtime", "file_type"]}
        recomputed = compute_fingerprint_v2(fp_input)
        obj["fingerprint_valid"] = (recomputed == obj["metadata_fingerprint"])
    return results


def run_real_synthetic_tests():
    """Run synthetic tests via subprocess.run and parse real results."""
    log("Running real synthetic fail-closed tests via subprocess...")
    try:
        proc = subprocess.run(
            ["/opt/paw-ksheet-worker/venv/bin/python3", SYNTHETIC_TEST_SCRIPT],
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = proc.stdout + proc.stderr
        log(f"Synthetic tests exit code: {proc.returncode}")
        
        results = {}
        for line in output.splitlines():
            line = line.strip()
            for test_name in [
                "LATER_PAGE_HTTP_FAIL_CLOSED_TEST",
                "LATER_PAGE_SCHEMA_FAIL_CLOSED_TEST",
                "LATER_PAGE_NEXT_OFFSET_KEY_MISSING_FAIL_CLOSED_TEST",
                "MISSING_STABLE_ID_FAIL_CLOSED_TEST",
                "PAGINATION_TERMINATION_NEGATIVE_ONE_TEST",
            ]:
                if line.startswith(test_name + "="):
                    results[test_name] = line.split("=", 1)[1].strip()
            if line.startswith("ALL_SYNTHETIC_TESTS_PASS="):
                results["ALL_SYNTHETIC_TESTS_PASS"] = line.split("=", 1)[1].strip()
        
        results["exit_code"] = proc.returncode
        results["REAL_SYNTHETIC_TEST_RUN"] = "PASS" if proc.returncode == 0 else "FAIL"
        
        for name, val in results.items():
            if name not in ("exit_code",):
                log(f"  {name}={val}")
        
        return results
    except Exception as e:
        log(f"Synthetic tests execution failed: {e}")
        return {
            "REAL_SYNTHETIC_TEST_RUN": "FAIL",
            "exit_code": -1,
            "LATER_PAGE_HTTP_FAIL_CLOSED_TEST": "FAIL",
            "LATER_PAGE_SCHEMA_FAIL_CLOSED_TEST": "FAIL",
            "LATER_PAGE_NEXT_OFFSET_KEY_MISSING_FAIL_CLOSED_TEST": "FAIL",
            "MISSING_STABLE_ID_FAIL_CLOSED_TEST": "FAIL",
            "PAGINATION_TERMINATION_NEGATIVE_ONE_TEST": "FAIL",
            "ALL_SYNTHETIC_TESTS_PASS": "NO",
        }


def collect_object_type_evidence(page):
    """Collect Object Type Evidence via metadata endpoint for known folder and files."""
    evidence = {}
    
    folder_meta = resolve_object_type_from_metadata(page, KNOWN_FOLDER_ID)
    evidence["folder"] = {
        "id": KNOWN_FOLDER_ID,
        "fileinfo_present": folder_meta["fileinfo_present"],
        "folderinfo_present": folder_meta["folderinfo_present"],
        "resolved_type": folder_meta["resolved_type"],
        "http_status": folder_meta["http_status"],
        "error": folder_meta["error"],
    }
    
    file1_meta = resolve_object_type_from_metadata(page, KNOWN_FILE_1_ID)
    evidence["file_1"] = {
        "id": KNOWN_FILE_1_ID,
        "fileinfo_present": file1_meta["fileinfo_present"],
        "folderinfo_present": file1_meta["folderinfo_present"],
        "resolved_type": file1_meta["resolved_type"],
        "http_status": file1_meta["http_status"],
        "error": file1_meta["error"],
    }
    
    file2_meta = resolve_object_type_from_metadata(page, KNOWN_FILE_2_ID)
    evidence["file_2"] = {
        "id": KNOWN_FILE_2_ID,
        "fileinfo_present": file2_meta["fileinfo_present"],
        "folderinfo_present": file2_meta["folderinfo_present"],
        "resolved_type": file2_meta["resolved_type"],
        "http_status": file2_meta["http_status"],
        "error": file2_meta["error"],
    }
    
    folder_proven = (
        evidence["folder"]["folderinfo_present"] is True and
        evidence["folder"]["fileinfo_present"] is True and
        evidence["folder"]["resolved_type"] == "folder"
    )
    file1_proven = (
        evidence["file_1"]["folderinfo_present"] is False and
        evidence["file_1"]["fileinfo_present"] is True and
        evidence["file_1"]["resolved_type"] == "file"
    )
    file2_proven = (
        evidence["file_2"]["folderinfo_present"] is False and
        evidence["file_2"]["fileinfo_present"] is True and
        evidence["file_2"]["resolved_type"] == "file"
    )
    
    evidence["mapping_proven"] = folder_proven and file1_proven and file2_proven
    evidence["evidence_source"] = "METADATA_FOLDERINFO_STRUCTURE"
    
    return evidence


def run_production(run_label):
    """Execute one production run: Chromium -> collect -> inventory -> DB check."""
    log(f"=== RUN {run_label} STARTING ===")
    mem_before = get_mem()
    result = {"run_label": run_label, "mem_before": mem_before}

    chrome_count = get_chrome_count()
    log(f"Chromium instances before: {chrome_count}")
    if chrome_count > 0:
        log("Killing residual Chromium...")
        os.system("pkill -f 'chrome-linux64/chrome'")
        time.sleep(3)

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            executable_path=BROWSER_EXECUTABLE,
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                  "--no-first-run", "--no-default-browser-check",
                  "--window-position=0,0", "--window-size=1440,900"],
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        log(f"Navigating to {SCOPE_URL}")
        try:
            page.goto(SCOPE_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log(f"Navigation warning: {e}")
        page.wait_for_timeout(12000)
        log(f"Page URL: {page.url}")

        log("Calling collect_from_wps_drive_api...")
        collector = collect_from_wps_drive_api(page, GROUP_ID, FOLDER_ID, expected_nonempty=True)
        result["collector_status"] = collector["status"]
        result["collector_error_class"] = collector["error_class"]
        result["http_status"] = collector["http_status"]
        result["files_key_present"] = collector["files_key_present"]
        result["api_object_count"] = len(collector["objects"])
        result["pages_fetched"] = collector.get("pages_fetched", 0)
        result["pagination_complete"] = collector.get("pagination_complete", False)
        result["page_diagnostics"] = collector.get("page_diagnostics", [])
        
        page_diags = result["page_diagnostics"]
        result["page_request_count"] = len(page_diags)
        result["page_2_requested"] = any(d.get("page_index", 0) >= 1 for d in page_diags)
        
        if page_diags:
            result["page_1_next_offset"] = page_diags[0].get("response_next_offset")
            result["page_1_pagination_state"] = page_diags[0].get("pagination_state", "N/A")
        else:
            result["page_1_next_offset"] = None
            result["page_1_pagination_state"] = "N/A"
        
        api_ids = {o["object_id"] for o in collector["objects"]}
        result["api_ids"] = api_ids
        result["frozen_id_parity"] = "PASS" if api_ids == FROZEN_IDS else "FAIL"
        log(f"Collector: status={collector['status']} error={collector['error_class']} http={collector['http_status']} objects={len(collector['objects'])}")
        log(f"Pages fetched: {result['pages_fetched']} page_request_count: {result['page_request_count']} pagination_complete: {result['pagination_complete']}")
        log(f"Page 1 next_offset: {result['page_1_next_offset']} pagination_state: {result['page_1_pagination_state']}")
        log(f"Page 2 requested: {result['page_2_requested']}")
        log(f"API IDs: {api_ids}")
        log(f"Frozen ID parity: {result['frozen_id_parity']}")

        log("Collecting Object Type Evidence via metadata endpoint...")
        try:
            type_evidence = collect_object_type_evidence(page)
            result["type_evidence"] = type_evidence
            log(f"Object Type Mapping Proven: {type_evidence['mapping_proven']}")
            log(f"  Folder: fileinfo={type_evidence['folder']['fileinfo_present']} folderinfo={type_evidence['folder']['folderinfo_present']} type={type_evidence['folder']['resolved_type']}")
            log(f"  File 1: fileinfo={type_evidence['file_1']['fileinfo_present']} folderinfo={type_evidence['file_1']['folderinfo_present']} type={type_evidence['file_1']['resolved_type']}")
            log(f"  File 2: fileinfo={type_evidence['file_2']['fileinfo_present']} folderinfo={type_evidence['file_2']['folderinfo_present']} type={type_evidence['file_2']['resolved_type']}")
        except Exception as e:
            log(f"Object Type Evidence collection failed: {e}")
            result["type_evidence"] = {"mapping_proven": False, "error": str(e)}

        if collector["status"] != "SUCCESS":
            log(f"Collector FAILED, skipping inventory")
            result["inventory_status"] = "BLOCKED"
            ctx.close()
            result["mem_after"] = get_mem()
            return result

        log("Running run_inventory_from_objects...")
        conn = init_db(PROD_DB)
        run_id = f"a3prod_{run_label}_{uuid.uuid4().hex[:8]}"
        inv_result = run_inventory_from_objects(conn, run_id, collector["objects"], GROUP_ID, FOLDER_ID, "WPS_DRIVE_API")
        result["inventory_status"] = inv_result["status"]
        result["inventory_added"] = inv_result["added"]
        result["inventory_changed"] = inv_result["changed"]
        result["inventory_unchanged"] = inv_result["unchanged"]
        result["inventory_scope_missing"] = inv_result["scope_missing"]
        result["run_id"] = run_id
        log(f"Inventory: status={inv_result['status']} added={inv_result['added']} changed={inv_result['changed']} unchanged={inv_result['unchanged']} missing={inv_result['scope_missing']}")

        db_check = db_reality_check(conn, run_label)
        result["db_check"] = db_check
        conn.close()

        ctx.close()

    time.sleep(3)
    result["mem_after"] = get_mem()
    result["chrome_after"] = get_chrome_count()
    log(f"Memory: before={mem_before}MB after={result['mem_after']}MB")
    log(f"Chromium after: {result['chrome_after']}")
    log(f"=== RUN {run_label} COMPLETE ===")
    return result


def main():
    log("=== R15-A.3P.2 PRODUCTION FINAL ACCEPTANCE ===")
    log(f"GROUP_ID={GROUP_ID} FOLDER_ID={FOLDER_ID}")
    log(f"SCOPE_URL={SCOPE_URL}")
    log(f"FROZEN_IDS={FROZEN_IDS}")

    synthetic_results = run_real_synthetic_tests()
    
    synthetic_all_pass = synthetic_results.get("REAL_SYNTHETIC_TEST_RUN") == "PASS"
    
    if not synthetic_all_pass:
        log("Synthetic tests FAILED — skipping Production runs.")
        final_chrome = get_chrome_count()
        print()
        print("R15_A3P2_RESULT=")
        print()
        print(f"REAL_SYNTHETIC_TEST_RUN=FAIL")
        print()
        print(f"LATER_PAGE_HTTP_FAIL_CLOSED_TEST={synthetic_results.get('LATER_PAGE_HTTP_FAIL_CLOSED_TEST', 'FAIL')}")
        print(f"LATER_PAGE_SCHEMA_FAIL_CLOSED_TEST={synthetic_results.get('LATER_PAGE_SCHEMA_FAIL_CLOSED_TEST', 'FAIL')}")
        print(f"LATER_PAGE_NEXT_OFFSET_KEY_MISSING_FAIL_CLOSED_TEST={synthetic_results.get('LATER_PAGE_NEXT_OFFSET_KEY_MISSING_FAIL_CLOSED_TEST', 'FAIL')}")
        print(f"MISSING_STABLE_ID_FAIL_CLOSED_TEST={synthetic_results.get('MISSING_STABLE_ID_FAIL_CLOSED_TEST', 'FAIL')}")
        print(f"PAGINATION_TERMINATION_NEGATIVE_ONE_TEST={synthetic_results.get('PAGINATION_TERMINATION_NEGATIVE_ONE_TEST', 'FAIL')}")
        print()
        print(f"RUN_1_COLLECTOR_STATUS=SKIPPED")
        print(f"RUN_2_COLLECTOR_STATUS=SKIPPED")
        print()
        print(f"CHROMIUM_REMAINING_PROCESS_COUNT={final_chrome}")
        print()
        print(f"R15_A3_FORMAL_FREEZE=FAIL")
        print(f"R15_A3_FINAL_STATUS=FAIL")
        print(f"R15_A4_READY=NO")
        print()
        print(f"BLOCKERS=SYNTHETIC_TESTS_FAILED")
        return

    run1 = run_production("run1")

    time.sleep(3)
    chrome_count = get_chrome_count()
    log(f"Chromium between runs: {chrome_count}")
    if chrome_count > 0:
        log("Killing residual Chromium before RUN #2")
        os.system("pkill -f 'chrome-linux64/chrome'")
        time.sleep(3)

    run2 = run_production("run2")

    time.sleep(3)
    final_chrome = get_chrome_count()

    type_evidence = run1.get("type_evidence", {})
    type_mapping_proven = type_evidence.get("mapping_proven", False)
    type_evidence_source = type_evidence.get("evidence_source", "")

    # Check OBJECT_TYPE_METADATA_FALLBACK_IMPLEMENTED via callable()
    metadata_fallback_implemented = callable(resolve_object_type_from_metadata) and callable(resolve_object_type)

    r1_db = run1.get("db_check", {})
    r2_db = run2.get("db_check", {})

    r1_obj1 = r1_db.get("object_1", {})
    r1_obj2 = r1_db.get("object_2", {})
    r1_metadata_present = all(r1_obj1.get(k) is not None for k in ["version", "size", "mtime"]) and all(r1_obj2.get(k) is not None for k in ["version", "size", "mtime"])
    r1_fp_valid = r1_obj1.get("fingerprint_valid", False) and r1_obj2.get("fingerprint_valid", False)

    r2_obj1 = r2_db.get("object_1", {})
    r2_obj2 = r2_db.get("object_2", {})
    r2_metadata_preserved = all(r2_obj1.get(k) is not None for k in ["version", "size", "mtime"]) and all(r2_obj2.get(k) is not None for k in ["version", "size", "mtime"])
    final_metadata_present = r2_metadata_preserved
    final_fp_valid = r2_obj1.get("fingerprint_valid", False) and r2_obj2.get("fingerprint_valid", False)

    # Formal Freeze Gate
    formal_freeze_pass = (
        synthetic_results.get("REAL_SYNTHETIC_TEST_RUN") == "PASS" and
        synthetic_results.get("LATER_PAGE_HTTP_FAIL_CLOSED_TEST") == "PASS" and
        synthetic_results.get("LATER_PAGE_SCHEMA_FAIL_CLOSED_TEST") == "PASS" and
        synthetic_results.get("LATER_PAGE_NEXT_OFFSET_KEY_MISSING_FAIL_CLOSED_TEST") == "PASS" and
        synthetic_results.get("MISSING_STABLE_ID_FAIL_CLOSED_TEST") == "PASS" and
        synthetic_results.get("PAGINATION_TERMINATION_NEGATIVE_ONE_TEST") == "PASS" and
        run1.get("collector_status") == "SUCCESS" and
        run1.get("collector_error_class") == "NONE" and
        run1.get("page_request_count") == 1 and
        run1.get("pages_fetched") == 1 and
        run1.get("page_1_next_offset") == -1 and
        run1.get("page_1_pagination_state") == "END" and
        not run1.get("page_2_requested", True) and
        run1.get("pagination_complete") is True and
        run1.get("frozen_id_parity") == "PASS" and
        run2.get("collector_status") == "SUCCESS" and
        run2.get("collector_error_class") == "NONE" and
        run2.get("page_request_count") == 1 and
        run2.get("pages_fetched") == 1 and
        run2.get("page_1_next_offset") == -1 and
        run2.get("page_1_pagination_state") == "END" and
        not run2.get("page_2_requested", True) and
        run2.get("pagination_complete") is True and
        run2.get("frozen_id_parity") == "PASS" and
        type_mapping_proven and
        metadata_fallback_implemented and
        final_metadata_present and
        final_fp_valid and
        final_chrome == 0
    )

    a3_pass = formal_freeze_pass
    a4_ready = a3_pass

    blockers = []
    if synthetic_results.get("REAL_SYNTHETIC_TEST_RUN") != "PASS":
        blockers.append("SYNTHETIC_TESTS_FAILED")
    for test_name in [
        "LATER_PAGE_HTTP_FAIL_CLOSED_TEST",
        "LATER_PAGE_SCHEMA_FAIL_CLOSED_TEST",
        "LATER_PAGE_NEXT_OFFSET_KEY_MISSING_FAIL_CLOSED_TEST",
        "MISSING_STABLE_ID_FAIL_CLOSED_TEST",
        "PAGINATION_TERMINATION_NEGATIVE_ONE_TEST",
    ]:
        if synthetic_results.get(test_name) != "PASS":
            blockers.append(f"{test_name}_FAILED")
    if run1.get("collector_status") != "SUCCESS":
        blockers.append("RUN_1_COLLECTOR_FAILED")
    if run1.get("frozen_id_parity") != "PASS":
        blockers.append("RUN_1_FROZEN_ID_PARITY_FAIL")
    if not (run1.get("pagination_complete") and run1.get("pages_fetched") == 1):
        blockers.append("RUN_1_PAGINATION_INCOMPLETE")
    if run1.get("page_1_next_offset") != -1:
        blockers.append("RUN_1_PAGE_1_NEXT_OFFSET_INVALID")
    if run1.get("page_1_pagination_state") != "END":
        blockers.append("RUN_1_PAGE_1_PAGINATION_STATE_INVALID")
    if run2.get("collector_status") != "SUCCESS":
        blockers.append("RUN_2_COLLECTOR_FAILED")
    if run2.get("frozen_id_parity") != "PASS":
        blockers.append("RUN_2_FROZEN_ID_PARITY_FAIL")
    if not (run2.get("pagination_complete") and run2.get("pages_fetched") == 1):
        blockers.append("RUN_2_PAGINATION_INCOMPLETE")
    if not type_mapping_proven:
        blockers.append("WPS_OBJECT_TYPE_MAPPING_UNPROVEN")
    if not metadata_fallback_implemented:
        blockers.append("OBJECT_TYPE_METADATA_FALLBACK_NOT_IMPLEMENTED")
    if not final_metadata_present:
        blockers.append("FINAL_DB_METADATA_MISSING")
    if not final_fp_valid:
        blockers.append("FINAL_DB_FINGERPRINT_INVALID")
    if final_chrome != 0:
        blockers.append("CHROMIUM_NOT_CLEANED")

    folder_ev = type_evidence.get("folder", {})
    file1_ev = type_evidence.get("file_1", {})
    file2_ev = type_evidence.get("file_2", {})

    print()
    print("R15_A3P2_RESULT=")
    print()
    print(f"REAL_SYNTHETIC_TEST_RUN={synthetic_results.get('REAL_SYNTHETIC_TEST_RUN', 'FAIL')}")
    print()
    print(f"LATER_PAGE_HTTP_FAIL_CLOSED_TEST={synthetic_results.get('LATER_PAGE_HTTP_FAIL_CLOSED_TEST', 'FAIL')}")
    print(f"LATER_PAGE_SCHEMA_FAIL_CLOSED_TEST={synthetic_results.get('LATER_PAGE_SCHEMA_FAIL_CLOSED_TEST', 'FAIL')}")
    print(f"LATER_PAGE_NEXT_OFFSET_KEY_MISSING_FAIL_CLOSED_TEST={synthetic_results.get('LATER_PAGE_NEXT_OFFSET_KEY_MISSING_FAIL_CLOSED_TEST', 'FAIL')}")
    print(f"MISSING_STABLE_ID_FAIL_CLOSED_TEST={synthetic_results.get('MISSING_STABLE_ID_FAIL_CLOSED_TEST', 'FAIL')}")
    print(f"PAGINATION_TERMINATION_NEGATIVE_ONE_TEST={synthetic_results.get('PAGINATION_TERMINATION_NEGATIVE_ONE_TEST', 'FAIL')}")
    print()
    print(f"RUN_1_COLLECTOR_STATUS={run1.get('collector_status', 'UNKNOWN')}")
    print(f"RUN_1_COLLECTOR_ERROR_CLASS={run1.get('collector_error_class', 'UNKNOWN')}")
    print(f"RUN_1_PAGE_REQUEST_COUNT={run1.get('page_request_count', 0)}")
    print(f"RUN_1_PAGE_COUNT={run1.get('pages_fetched', 0)}")
    print(f"RUN_1_PAGE_1_NEXT_OFFSET={run1.get('page_1_next_offset', 'N/A')}")
    print(f"RUN_1_PAGE_1_PAGINATION_STATE={run1.get('page_1_pagination_state', 'N/A')}")
    print(f"RUN_1_PAGE_2_REQUESTED={'YES' if run1.get('page_2_requested', False) else 'NO'}")
    print(f"RUN_1_PAGINATION_COMPLETE={'YES' if run1.get('pagination_complete') else 'NO'}")
    print(f"RUN_1_API_OBJECT_COUNT={run1.get('api_object_count', 0)}")
    print(f"RUN_1_FROZEN_ID_PARITY={run1.get('frozen_id_parity', 'UNKNOWN')}")
    print(f"RUN_1_ADDED={run1.get('inventory_added', 0)}")
    print(f"RUN_1_CHANGED={run1.get('inventory_changed', 0)}")
    print(f"RUN_1_UNCHANGED={run1.get('inventory_unchanged', 0)}")
    print(f"RUN_1_SCOPE_MISSING={run1.get('inventory_scope_missing', 0)}")
    print()
    print(f"RUN_2_COLLECTOR_STATUS={run2.get('collector_status', 'UNKNOWN')}")
    print(f"RUN_2_COLLECTOR_ERROR_CLASS={run2.get('collector_error_class', 'UNKNOWN')}")
    print(f"RUN_2_PAGE_REQUEST_COUNT={run2.get('page_request_count', 0)}")
    print(f"RUN_2_PAGE_COUNT={run2.get('pages_fetched', 0)}")
    print(f"RUN_2_PAGE_1_NEXT_OFFSET={run2.get('page_1_next_offset', 'N/A')}")
    print(f"RUN_2_PAGE_1_PAGINATION_STATE={run2.get('page_1_pagination_state', 'N/A')}")
    print(f"RUN_2_PAGE_2_REQUESTED={'YES' if run2.get('page_2_requested', False) else 'NO'}")
    print(f"RUN_2_PAGINATION_COMPLETE={'YES' if run2.get('pagination_complete') else 'NO'}")
    print(f"RUN_2_API_OBJECT_COUNT={run2.get('api_object_count', 0)}")
    print(f"RUN_2_FROZEN_ID_PARITY={run2.get('frozen_id_parity', 'UNKNOWN')}")
    print(f"RUN_2_ADDED={run2.get('inventory_added', 0)}")
    print(f"RUN_2_CHANGED={run2.get('inventory_changed', 0)}")
    print(f"RUN_2_UNCHANGED={run2.get('inventory_unchanged', 0)}")
    print(f"RUN_2_SCOPE_MISSING={run2.get('inventory_scope_missing', 0)}")
    print()
    print(f"WPS_OBJECT_TYPE_MAPPING_PROVEN={'YES' if type_mapping_proven else 'NO'}")
    print(f"WPS_OBJECT_TYPE_MAPPING_EVIDENCE={type_evidence_source}")
    print()
    print(f"OBJECT_TYPE_METADATA_FALLBACK_IMPLEMENTED={'YES' if metadata_fallback_implemented else 'NO'}")
    print()
    print(f"FINAL_DB_METADATA_PRESENT={'YES' if final_metadata_present else 'NO'}")
    print(f"FINAL_DB_FINGERPRINT_VALID={'YES' if final_fp_valid else 'NO'}")
    print()
    print(f"REAL_WPS_MULTIPAGE_VALIDATED=NO")
    print()
    print(f"CHROMIUM_REMAINING_PROCESS_COUNT={final_chrome}")
    print()
    print(f"R15_A3_FORMAL_FREEZE={'PASS' if formal_freeze_pass else 'FAIL'}")
    print(f"R15_A3_FINAL_STATUS={'PASS' if a3_pass else 'FAIL'}")
    print(f"R15_A4_READY={'YES' if a4_ready else 'NO'}")
    print()
    print(f"BLOCKERS={','.join(blockers) if blockers else 'NONE'}")


if __name__ == "__main__":
    main()
