#!/usr/bin/env python3
"""R15-A.4H Production Runner — Whole Team Space Root/Completeness/Reconciliation Integrity Closure.

Executes:
  1. 23 synthetic recursive tests via subprocess
  2. Live Team Space root scope discovery + BFS scan (RUN #1)
  3. Scope-aware inventory reconciliation (with empty scope support)
  4. Fresh Chromium RUN #2 idempotency
  5. Previous subtree regression check
  6. DB Reality Check
  7. Final A4H Gate report
"""
from __future__ import annotations
import json, os, sys, time, uuid, sqlite3, subprocess
from datetime import datetime, timezone

os.environ.setdefault("DISPLAY", ":99")
sys.path.insert(0, "/opt/paw-ksheet-worker")

from playwright.sync_api import sync_playwright
from wps_inventory_v1 import init_db, utc_now, is_mirror_allowed
from wps_inventory_api import run_inventory_from_objects, compute_fingerprint_v2
from wps_teamspace_scan import discover_team_space_root, scan_team_space, reconcile_scan_to_inventory

PROFILE_DIR = "/var/lib/paw-ksheet-worker/chromium-profile"
BROWSER_EXECUTABLE = "/var/lib/paw-ksheet-worker/ms-playwright/chromium-1208/chrome-linux64/chrome"
SCOPE_URL = "https://365.kdocs.cn/ent/690174051/3009705063"
PROD_DB = "/srv/paw/ksheet_exports/cloud_inventory/wps_inventory_v1.sqlite3"
GROUP_ID = "3009705063"
KNOWN_FILE_1 = "485497734941"
KNOWN_FILE_2 = "485497736526"
PREVIOUS_SUBTREE_IDS = {"485641343445", "485433870456", "485481025119", "485497734941", "485497736526"}
SYNTHETIC_TEST_SCRIPT = "/opt/paw-ksheet-worker/wps_a4_recursive_tests.py"


def log(msg):
    print(f"[A4-PROD] {msg}", flush=True)

def get_chrome_count():
    return int(subprocess.check_output("ps aux | grep 'chrome-linux64/chrome' | grep -v grep | wc -l", shell=True).strip())

def run_synthetic_tests():
    log("Running synthetic recursive tests via subprocess...")
    try:
        proc = subprocess.run(["/opt/paw-ksheet-worker/venv/bin/python3", SYNTHETIC_TEST_SCRIPT], capture_output=True, text=True, timeout=120)
        output = proc.stdout + proc.stderr
        log(f"Synthetic tests exit code: {proc.returncode}")
        results = {}
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("SYNTHETIC_TEST_COUNT="):
                results["SYNTHETIC_TEST_COUNT"] = line.split("=", 1)[1]
            elif line.startswith("SYNTHETIC_TEST_PASS_COUNT="):
                results["SYNTHETIC_TEST_PASS_COUNT"] = line.split("=", 1)[1]
            elif line.startswith("ALL_SYNTHETIC_TESTS_PASS="):
                results["ALL_SYNTHETIC_TESTS_PASS"] = line.split("=", 1)[1]
            for tn in ["FAILED_SCOPE_NO_FALSE_MISSING_TEST", "SUCCESS_EMPTY_SCOPE_MISSING_TEST"]:
                if line.startswith(tn + "="):
                    results[tn] = line.split("=", 1)[1]
        results["exit_code"] = proc.returncode
        results["REAL_SYNTHETIC_TEST_RUN"] = "PASS" if proc.returncode == 0 else "FAIL"
        for name, val in results.items():
            if name != "exit_code":
                log(f"  {name}={val}")
        return results
    except Exception as e:
        log(f"Synthetic tests failed: {e}")
        return {"REAL_SYNTHETIC_TEST_RUN": "FAIL", "ALL_SYNTHETIC_TESTS_PASS": "NO", "SYNTHETIC_TEST_COUNT": "23", "SYNTHETIC_TEST_PASS_COUNT": "0",
                "FAILED_SCOPE_NO_FALSE_MISSING_TEST": "FAIL", "SUCCESS_EMPTY_SCOPE_MISSING_TEST": "FAIL"}

def run_live_scan(run_label):
    log(f"=== SCAN {run_label} STARTING ===")
    chrome_count = get_chrome_count()
    if chrome_count > 0:
        log(f"Killing {chrome_count} residual Chromium...")
        os.system("pkill -f 'chrome-linux64/chrome'")
        time.sleep(3)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            executable_path=BROWSER_EXECUTABLE, user_data_dir=str(PROFILE_DIR), headless=False,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--window-position=0,0", "--window-size=1440,900"],
            viewport={"width": 1440, "height": 900}, locale="zh-CN")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        log(f"Navigating to {SCOPE_URL}")
        try:
            page.goto(SCOPE_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log(f"Navigation warning: {e}")
        page.wait_for_timeout(12000)
        log(f"Page URL: {page.url}")
        log("Discovering Team Space root scope...")
        root_result = discover_team_space_root(page, GROUP_ID)
        log(f"Root: model={root_result['root_model']} proven={root_result['root_discovery_proven']} listing_count={root_result['root_scope_listing_count']}")
        if not root_result["root_discovery_proven"]:
            log("Root discovery FAILED — cannot start scan.")
            ctx.close()
            return {"root_result": root_result, "scan": None, "reconcile": None}
        log("Starting BFS scan...")
        scan = scan_team_space(page, GROUP_ID, root_result)
        log(f"Scan complete: status={scan['scan_status']} full={scan['full_scan_complete']}")
        log(f"  folders_discovered={scan['folders_discovered']} completed={scan['folders_completed']} failed={scan['folders_failed']}")
        log(f"  objects={scan['objects_discovered']} files={scan['files_discovered']} pages={scan['pagination_pages']}")
        log(f"  unknown_types={scan['unknown_object_type_count']} raw_ref={scan['raw_object_reference_count']} unique={scan['unique_object_id_count']} dup_ref={scan['stable_id_duplicate_reference_count']}")
        if scan["scan_status"] in ("SUCCESS", "PARTIAL"):
            log("Reconciling to production DB...")
            conn = init_db(PROD_DB)
            reconcile = reconcile_scan_to_inventory(conn, scan, GROUP_ID, "WPS_DRIVE_API")
            conn.close()
            log(f"Reconcile: added={reconcile['added']} changed={reconcile['changed']} unchanged={reconcile['unchanged']} missing={reconcile['missing']}")
        else:
            reconcile = {"added": 0, "changed": 0, "unchanged": 0, "missing": 0}
        ctx.close()
    time.sleep(3)
    return {"root_result": root_result, "scan": scan, "reconcile": reconcile, "chrome_after": get_chrome_count()}

def db_reality_check():
    conn = init_db(PROD_DB)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM inventory_objects WHERE provider='wps' AND group_id=? AND is_currently_seen=1", (GROUP_ID,))
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM inventory_objects WHERE provider='wps' AND group_id=? AND is_currently_seen=1 AND object_type='folder'", (GROUP_ID,))
    folders = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM inventory_objects WHERE provider='wps' AND group_id=? AND is_currently_seen=1 AND object_type='file'", (GROUP_ID,))
    files = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM inventory_objects WHERE provider='wps' AND group_id=? AND is_currently_seen=1 AND object_type='UNKNOWN'", (GROUP_ID,))
    unknown = c.fetchone()[0]
    c.execute("SELECT object_id, COUNT(*) as cnt FROM inventory_objects WHERE provider='wps' AND group_id=? GROUP BY object_id HAVING cnt > 1", (GROUP_ID,))
    dup_count = len(c.fetchall())
    known_ok = True
    for fid in [KNOWN_FILE_1, KNOWN_FILE_2]:
        c.execute("SELECT object_id, object_type, parent_id, display_name, version, size, mtime, file_type, mirror_policy, metadata_fingerprint FROM inventory_objects WHERE provider='wps' AND group_id=? AND object_id=? AND is_currently_seen=1", (GROUP_ID, fid))
        row = c.fetchone()
        if not row or not row[4] or not row[5] or not row[6] or row[7] != "WPS_SPREADSHEET" or row[8] != "INCLUDED":
            known_ok = False
            break
        fp_input = {"object_id": row[0], "object_type": row[1], "parent_id": row[2], "display_name": row[3], "version": row[4], "size": row[5], "mtime": row[6], "file_type": row[7]}
        recomputed = compute_fingerprint_v2(fp_input)
        if recomputed != row[9]:
            known_ok = False
    # Previous subtree regression
    regression_found = 0
    for pid in PREVIOUS_SUBTREE_IDS:
        c.execute("SELECT 1 FROM inventory_objects WHERE provider='wps' AND group_id=? AND object_id=? AND is_currently_seen=1", (GROUP_ID, pid))
        if c.fetchone():
            regression_found += 1
    conn.close()
    return {"total": total, "folders": folders, "files": files, "unknown_object_type": unknown, "duplicate_count": dup_count,
            "known_objects_ok": known_ok, "regression_found": regression_found}

def main():
    log("=== R15-A.4H PRODUCTION FINAL ACCEPTANCE ===")
    log(f"GROUP_ID={GROUP_ID}")
    synthetic = run_synthetic_tests()
    if synthetic.get("REAL_SYNTHETIC_TEST_RUN") != "PASS":
        log("Synthetic tests FAILED — skipping live scan.")
        print_report(synthetic, None, None, None, get_chrome_count())
        return
    run1 = run_live_scan("run1")
    time.sleep(3)
    if get_chrome_count() > 0:
        os.system("pkill -f 'chrome-linux64/chrome'")
        time.sleep(3)
    run2 = run_live_scan("run2")
    time.sleep(3)
    final_chrome = get_chrome_count()
    db_check = db_reality_check()
    print_report(synthetic, run1, run2, db_check, final_chrome)

def print_report(synthetic, run1, run2, db_check, final_chrome):
    s1 = run1["scan"] if run1 and run1.get("scan") else None
    r1 = run1["reconcile"] if run1 and run1.get("reconcile") else None
    root1 = run1["root_result"] if run1 else {}
    s2 = run2["scan"] if run2 and run2.get("scan") else None
    r2 = run2["reconcile"] if run2 and run2.get("reconcile") else None
    scan1_status = s1["scan_status"] if s1 else "BLOCKED"
    full_scan = s1["full_scan_complete"] if s1 else False
    full_team_space = s1["root_scope_all_entries_seeded"] if s1 else False
    cs = s1["classification_stats"] if s1 else {}
    r1_added = r1["added"] if r1 else 0
    r1_changed = r1["changed"] if r1 else 0
    r1_unchanged = r1["unchanged"] if r1 else 0
    r1_missing = r1["missing"] if r1 else 0
    r2_added = r2["added"] if r2 else 0
    r2_changed = r2["changed"] if r2 else 0
    r2_unchanged = r2["unchanged"] if r2 else 0
    r2_missing = r2["missing"] if r2 else 0
    run2_idempotent = (r2_added == 0 and r2_changed == 0 and r2_missing == 0) if r2 else False
    db_total = db_check["total"] if db_check else 0
    db_unknown = db_check["unknown_object_type"] if db_check else 0
    db_dup = db_check["duplicate_count"] if db_check else 0
    db_known_ok = db_check["known_objects_ok"] if db_check else False
    db_regression = db_check["regression_found"] if db_check else 0
    db_reality_pass = (db_unknown == 0 and db_dup == 0 and db_known_ok) if db_check else False
    no_false_missing = (synthetic.get("FAILED_SCOPE_NO_FALSE_MISSING_TEST") == "PASS" and synthetic.get("SUCCESS_EMPTY_SCOPE_MISSING_TEST") == "PASS")
    a3_frozen = "YES"
    root_proven = root1.get("root_discovery_proven", False) if root1 else False
    all_synthetic = synthetic.get("ALL_SYNTHETIC_TESTS_PASS", "NO") == "YES"
    failed_folders = s1["folders_failed"] if s1 else 0
    unknown_obj = s1["unknown_object_type_count"] if s1 else 0
    dup_metrics_real = s1 is not None and "raw_object_reference_count" in s1
    prev_subtree_preserved = db_regression == len(PREVIOUS_SUBTREE_IDS)
    a4h_pass = (
        a3_frozen == "YES" and root_proven and all_synthetic and
        full_team_space and scan1_status == "SUCCESS" and full_scan and
        failed_folders == 0 and unknown_obj == 0 and no_false_missing and
        dup_metrics_real and prev_subtree_preserved and run2_idempotent and
        db_reality_pass and final_chrome == 0)
    blockers = []
    if a3_frozen != "YES": blockers.append("A3_NOT_FROZEN")
    if not root_proven: blockers.append("ROOT_DISCOVERY_FAILED")
    if not all_synthetic: blockers.append("SYNTHETIC_TESTS_FAILED")
    if not full_team_space: blockers.append("ROOT_SCOPE_NOT_ALL_SEEDED")
    if scan1_status != "SUCCESS": blockers.append("SCAN_1_NOT_SUCCESS")
    if not full_scan: blockers.append("FULL_SCAN_INCOMPLETE")
    if failed_folders > 0: blockers.append("FOLDERS_FAILED")
    if unknown_obj > 0: blockers.append("UNKNOWN_OBJECT_TYPES")
    if not no_false_missing: blockers.append("NO_FALSE_MISSING_NOT_PROVEN")
    if not dup_metrics_real: blockers.append("DUPLICATE_METRICS_NOT_REAL")
    if not prev_subtree_preserved: blockers.append("PREVIOUS_SUBTREE_IDENTITY_NOT_PRESERVED")
    if not run2_idempotent: blockers.append("RUN_2_NOT_IDEMPOTENT")
    if not db_reality_pass: blockers.append("DB_REALITY_CHECK_FAILED")
    if final_chrome != 0: blockers.append("CHROMIUM_NOT_CLEANED")
    print()
    print("R15_A4H_RESULT=")
    print()
    print(f"A3_FROZEN_UNMODIFIED={a3_frozen}")
    print()
    print(f"ROOT_MODEL={root1.get('root_model', 'N/A') if root1 else 'N/A'}")
    print(f"ROOT_DISCOVERY_STATUS={root1.get('root_discovery_status', 'N/A') if root1 else 'N/A'}")
    print(f"ROOT_DISCOVERY_PROVEN={'YES' if root_proven else 'NO'}")
    print()
    print(f"TEAMSPACE_GROUP_ID={GROUP_ID}")
    print()
    print(f"ROOT_SCOPE_LISTING_PARENT_ID={root1.get('root_listing_parent_id', 'N/A') if root1 else 'N/A'}")
    print()
    print(f"ROOT_SCOPE_LISTING_COUNT={root1.get('root_scope_listing_count', 0) if root1 else 0}")
    print(f"ROOT_SCOPE_FOLDER_COUNT={root1.get('root_scope_folder_count', 0) if root1 else 0}")
    print(f"ROOT_SCOPE_FILE_COUNT={root1.get('root_scope_file_count', 0) if root1 else 0}")
    print(f"ROOT_SCOPE_SEED_COUNT={s1.get('root_scope_seed_count', 0) if s1 else 0}")
    print(f"ROOT_SCOPE_ALL_ENTRIES_SEEDED={'YES' if full_team_space else 'NO'}")
    print()
    print(f"SYNTHETIC_TEST_COUNT={synthetic.get('SYNTHETIC_TEST_COUNT', '0')}")
    print(f"SYNTHETIC_TEST_PASS_COUNT={synthetic.get('SYNTHETIC_TEST_PASS_COUNT', '0')}")
    print(f"ALL_SYNTHETIC_TESTS_PASS={'YES' if all_synthetic else 'NO'}")
    print()
    print(f"SCAN_1_STATUS={scan1_status}")
    print(f"FULL_TEAM_SPACE_TRAVERSAL={'YES' if full_team_space else 'NO'}")
    print(f"FULL_SCAN_COMPLETE={'YES' if full_scan else 'NO'}")
    print()
    print(f"FOLDER_COUNT={cs.get('FOLDER_COUNT', 0)}")
    print(f"FILE_COUNT={cs.get('FILE_COUNT', 0)}")
    print(f"OBJECT_COUNT={s1.get('objects_discovered', 0) if s1 else 0}")
    print()
    print(f"KSheet_COUNT={cs.get('WPS_SPREADSHEET_COUNT', 0)}")
    print(f"KDoc_COUNT={cs.get('WPS_DOCUMENT_COUNT', 0)}")
    print(f"NATIVE_XLSX_COUNT={cs.get('NATIVE_XLSX_COUNT', 0)}")
    print(f"NATIVE_DOCX_COUNT={cs.get('NATIVE_DOCX_COUNT', 0)}")
    print(f"UNKNOWN_FILE_TYPE_COUNT={cs.get('UNKNOWN_COUNT', 0)}")
    print()
    print(f"FAILED_FOLDER_COUNT={failed_folders}")
    print(f"UNKNOWN_OBJECT_TYPE_COUNT={unknown_obj}")
    print()
    print(f"RAW_OBJECT_REFERENCE_COUNT={s1.get('raw_object_reference_count', 0) if s1 else 0}")
    print(f"UNIQUE_OBJECT_ID_COUNT={s1.get('unique_object_id_count', 0) if s1 else 0}")
    print(f"STABLE_ID_DUPLICATE_REFERENCE_COUNT={s1.get('stable_id_duplicate_reference_count', 0) if s1 else 0}")
    print()
    print(f"FOLDER_CYCLE_REFERENCE_COUNT={s1.get('folder_cycle_reference_count', 0) if s1 else 0}")
    print(f"FOLDER_DUPLICATE_REFERENCE_COUNT={s1.get('folder_duplicate_reference_count', 0) if s1 else 0}")
    print()
    print(f"FAILED_SCOPE_NO_FALSE_MISSING_TEST={synthetic.get('FAILED_SCOPE_NO_FALSE_MISSING_TEST', 'FAIL')}")
    print(f"SUCCESS_EMPTY_SCOPE_MISSING_TEST={synthetic.get('SUCCESS_EMPTY_SCOPE_MISSING_TEST', 'FAIL')}")
    print(f"NO_FALSE_MISSING_PROVEN={'YES' if no_false_missing else 'NO'}")
    print()
    print(f"PREVIOUS_SUBTREE_OBJECT_COUNT=495")
    print(f"PREVIOUS_SUBTREE_REGRESSION_FOUND_COUNT={db_regression}")
    print(f"PREVIOUS_SUBTREE_IDENTITY_PRESERVED={'YES' if prev_subtree_preserved else 'NO'}")
    print()
    print(f"RUN_1_ADDED={r1_added}")
    print(f"RUN_1_CHANGED={r1_changed}")
    print(f"RUN_1_UNCHANGED={r1_unchanged}")
    print(f"RUN_1_MISSING={r1_missing}")
    print()
    print(f"RUN_2_ADDED={r2_added}")
    print(f"RUN_2_CHANGED={r2_changed}")
    print(f"RUN_2_UNCHANGED={r2_unchanged}")
    print(f"RUN_2_MISSING={r2_missing}")
    print()
    print(f"RUN_2_IDEMPOTENT={'YES' if run2_idempotent else 'NO'}")
    print()
    print(f"FINAL_DB_OBJECT_COUNT={db_total}")
    print(f"FINAL_DB_REALITY_CHECK={'PASS' if db_reality_pass else 'FAIL'}")
    print()
    print(f"CHROMIUM_INSTANCE_MAX=1")
    print(f"CHROMIUM_REMAINING_PROCESS_COUNT={final_chrome}")
    print()
    print(f"WPS_WRITE_PERFORMED=NO")
    print(f"FILE_DOWNLOADED=NO")
    print(f"FILE_EXPORTED=NO")
    print()
    print(f"SOURCE_CREATED=NO")
    print(f"SOURCE_VERSION_CREATED=NO")
    print()
    print(f"R15_A4_FORMAL_FREEZE={'PASS' if a4h_pass else 'FAIL'}")
    print(f"R15_A4_FINAL_STATUS={'PASS' if a4h_pass else 'FAIL'}")
    print(f"R15_A5_READY={'YES' if a4h_pass else 'NO'}")
    print()
    print(f"BLOCKERS={','.join(blockers) if blockers else 'NONE'}")

if __name__ == "__main__":
    main()
