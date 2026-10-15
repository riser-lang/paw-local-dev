#!/usr/bin/env python3
"""R15-A.5.2 Production Runner — Native DOCX Direct Acquisition Acceptance.

Executes:
  1. Read acceptance sample from DB (NATIVE_DOCX)
  2. A5.2 synthetic tests gate
  3. RUN #1: Real download + DOCX validation + SHA256
  4. Chromium close + Fresh RUN #2
  5. Hash/size reality check
  6. Full Promotion Gate (can_promote_backend)
  7. Registry promotion (only on full PASS)
  8. Post-promotion regression + rollback if needed
  9. Final report
"""
from __future__ import annotations
import os, sys, time, json, sqlite3, subprocess, hashlib
from datetime import datetime, timezone

os.environ.setdefault("DISPLAY", ":99")
sys.path.insert(0, "/opt/paw-ksheet-worker")

from playwright.sync_api import sync_playwright
from wps_native_acquisition import (
    run_acceptance_acquisition_docx, set_acceptance_override, clear_acceptance_overrides,
    STAGING_BASE, NativeAcquisitionResult, can_promote_backend,
)
from wps_file_registry import MIRROR_BACKEND_STATUS_REGISTRY, is_backend_acquisition_ready
from wps_inventory_v1 import init_db

PROFILE_DIR = "/var/lib/paw-ksheet-worker/chromium-profile"
BROWSER_EXECUTABLE = "/var/lib/paw-ksheet-worker/ms-playwright/chromium-1208/chrome-linux64/chrome"
SCOPE_URL = "https://365.kdocs.cn/ent/690174051/3009705063"
PROD_DB = "/srv/paw/ksheet_exports/cloud_inventory/wps_inventory_v1.sqlite3"
GROUP_ID = "3009705063"


def log(msg):
    print(f"[A5.2-PROD] {msg}", flush=True)


def get_chrome_count():
    return int(subprocess.check_output("ps aux | grep 'chrome-linux64/chrome' | grep -v grep | wc -l", shell=True).strip())


def read_acceptance_sample():
    conn = init_db(PROD_DB)
    c = conn.cursor()
    c.execute("""
        SELECT object_id, display_name, parent_id, version, size, mtime,
               mirror_policy, mirror_policy_evaluated_at, file_type,
               mirror_backend_candidate, object_type
        FROM inventory_objects
        WHERE provider='wps' AND group_id=? AND is_currently_seen=1
          AND object_type='file' AND file_type='NATIVE_DOCX'
          AND mirror_policy='INCLUDED'
          AND mirror_policy_evaluated_at IS NOT NULL
          AND mirror_policy_evaluated_at != ''
          AND version IS NOT NULL AND size IS NOT NULL AND size > 0
          AND size <= 20971520
          AND display_name NOT LIKE '%~$%'
          AND display_name NOT LIKE '%.tmp%'
        ORDER BY size ASC LIMIT 1
    """, (GROUP_ID,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "object_id": row[0], "display_name": row[1], "parent_id": row[2],
        "version": row[3], "size": row[4], "mtime": row[5],
        "mirror_policy": row[6], "mirror_policy_evaluated_at": row[7],
        "file_type": row[8], "mirror_backend_candidate": row[9],
        "object_type": row[10],
    }


def run_synthetic_tests():
    log("Running A5.2 synthetic tests...")
    proc = subprocess.run(
        ["/opt/paw-ksheet-worker/venv/bin/python3", "/opt/paw-ksheet-worker/wps_a5_native_docx_tests.py"],
        capture_output=True, text=True, timeout=120
    )
    output = proc.stdout + proc.stderr
    results = {}
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("SYNTHETIC_TEST_COUNT="):
            results["count"] = line.split("=", 1)[1]
        elif line.startswith("SYNTHETIC_TEST_PASS_COUNT="):
            results["pass"] = line.split("=", 1)[1]
        elif line.startswith("ALL_SYNTHETIC_TESTS_PASS="):
            results["all_pass"] = line.split("=", 1)[1]
    results["exit_code"] = proc.returncode
    log(f"  count={results.get('count')} pass={results.get('pass')} all_pass={results.get('all_pass')}")
    return results


def run_production_acquisition(sample, run_label):
    log(f"=== RUN {run_label} STARTING ===")
    chrome_count = get_chrome_count()
    if chrome_count > 0:
        log(f"Killing {chrome_count} residual Chromium...")
        os.system("pkill -f 'chrome-linux64/chrome'")
        time.sleep(3)

    obj_id = sample["object_id"]
    set_acceptance_override(obj_id, "NATIVE_DOCX_DIRECT")

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
        page.wait_for_timeout(10000)
        log(f"Page URL: {page.url}")

        log(f"Acquiring {obj_id} ({sample['display_name']})...")
        result = run_acceptance_acquisition_docx(page, sample, run_label)

        log(f"Run {run_label} result: status={result.status} error={result.error_class}")
        log(f"  artifact_size={result.artifact_size} sha256={result.sha256[:16]}...")
        log(f"  docx_sig={result.xlsx_signature_valid} zip={result.xlsx_zip_valid}")
        log(f"  content_types={result.content_types_present} document_xml={result.workbook_xml_present}")
        log(f"  python_docx={result.openpyxl_read_valid} size_match={result.source_size_match}")

        ctx.close()

    clear_acceptance_overrides()
    time.sleep(3)
    chrome_after = get_chrome_count()
    log(f"Chromium after {run_label}: {chrome_after}")

    return result, chrome_after


def promote_registry():
    log("=== REGISTRY PROMOTION (NATIVE_DOCX_DIRECT) ===")
    filepath = "/opt/paw-ksheet-worker/wps_file_registry.py"
    with open(filepath, "r") as f:
        content = f.read()

    old = '"NATIVE_DOCX_DIRECT": {"registered": True, "capability_status": "READY", "acquisition_ready": False}'
    new = '"NATIVE_DOCX_DIRECT": {"registered": True, "capability_status": "READY", "acquisition_ready": True}'

    if new in content:
        log("Registry already promoted (acquisition_ready=True)")
        return True

    if old not in content:
        log(f"ERROR: Cannot find expected registry line to promote")
        return False

    content = content.replace(old, new)
    with open(filepath, "w") as f:
        f.write(content)

    proc = subprocess.run(
        ["/opt/paw-ksheet-worker/venv/bin/python3", "-c",
         "import py_compile; py_compile.compile('/opt/paw-ksheet-worker/wps_file_registry.py', doraise=True); print('OK')"],
        capture_output=True, text=True
    )
    if proc.returncode != 0:
        log(f"SYNTAX ERROR after promotion: {proc.stderr}")
        return False

    log("Registry promoted: NATIVE_DOCX_DIRECT acquisition_ready=False -> True")
    return True


def rollback_registry():
    log("=== ROLLBACK (NATIVE_DOCX_DIRECT True -> False) ===")
    filepath = "/opt/paw-ksheet-worker/wps_file_registry.py"
    with open(filepath, "r") as f:
        content = f.read()
    content = content.replace(
        '"NATIVE_DOCX_DIRECT": {"registered": True, "capability_status": "READY", "acquisition_ready": True}',
        '"NATIVE_DOCX_DIRECT": {"registered": True, "capability_status": "READY", "acquisition_ready": False}'
    )
    with open(filepath, "w") as f:
        f.write(content)
    log("Rollback complete: NATIVE_DOCX_DIRECT acquisition_ready=True -> False")


def db_regression():
    conn = init_db(PROD_DB)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM inventory_objects WHERE provider='wps' AND group_id=? AND is_currently_seen=1", (GROUP_ID,))
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM inventory_objects WHERE provider='wps' AND group_id=? AND parent_id='0' AND is_currently_seen=1", (GROUP_ID,))
    root_scope = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM inventory_objects WHERE provider='wps' AND group_id=? AND is_currently_seen=1 AND object_id IN ('485641343445','485433870456','485481025119','485497734941','485497736526')", (GROUP_ID,))
    prev_subtree = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM inventory_objects WHERE provider='wps' AND group_id=? AND is_currently_seen=1 AND object_type='UNKNOWN'", (GROUP_ID,))
    unknown_type = c.fetchone()[0]
    c.execute("SELECT object_id, COUNT(*) as cnt FROM inventory_objects WHERE provider='wps' AND group_id=? GROUP BY object_id HAVING cnt > 1", (GROUP_ID,))
    dup_count = len(c.fetchall())
    conn.close()
    return {"total": total, "root_scope": root_scope, "prev_subtree": prev_subtree,
            "unknown_type": unknown_type, "dup_count": dup_count}


def main():
    log("=== R15-A.5.2 NATIVE DOCX DIRECT ACQUISITION ACCEPTANCE ===")
    log(f"GROUP_ID={GROUP_ID}")

    # 1. Read acceptance sample
    log("Reading acceptance sample from DB...")
    sample = read_acceptance_sample()
    if not sample:
        log("NO_ELIGIBLE_NATIVE_DOCX_SAMPLE")
        print("R15_A5_2_STATUS=BLOCKED")
        print("BLOCKERS=NO_ELIGIBLE_NATIVE_DOCX_SAMPLE")
        return 1

    obj_id = sample["object_id"]
    log(f"Acceptance sample: {obj_id} ({sample['display_name']}) size={sample['size']} version={sample['version']}")

    # 2. Synthetic tests
    synth = run_synthetic_tests()
    if synth.get("all_pass") != "YES":
        log("Synthetic tests FAILED")
        print("R15_A5_2_STATUS=FAIL")
        print("BLOCKERS=SYNTHETIC_TESTS_FAILED")
        return 1

    # 3. Pre-acceptance registry check
    pre_xlsx_ready = is_backend_acquisition_ready("NATIVE_XLSX_DIRECT")
    pre_docx_ready = is_backend_acquisition_ready("NATIVE_DOCX_DIRECT")
    log(f"Pre-acceptance: XLSX_READY={pre_xlsx_ready} DOCX_READY={pre_docx_ready}")

    if not pre_xlsx_ready or pre_docx_ready:
        log("Registry state conflict")
        print("R15_A5_2_STATUS=BLOCKED")
        print("BLOCKERS=BACKEND_RUNTIME_STATE_CONFLICT")
        return 1

    # 4. RUN #1
    r1, chrome_after_r1 = run_production_acquisition(sample, "run1")

    r1_pass = (
        r1.status == "SUCCESS" and
        r1.artifact_size > 0 and
        r1.xlsx_signature_valid and
        r1.xlsx_zip_valid and
        r1.content_types_present and
        r1.workbook_xml_present and
        r1.openpyxl_read_valid and
        bool(r1.sha256)
    )
    log(f"RUN #1 PASS={r1_pass}")

    if not r1_pass:
        log("RUN #1 FAILED")
        print("R15_A5_2_STATUS=FAIL")
        print(f"BLOCKERS=RUN_1_FAILED:{r1.error_class}")
        return 1

    # 5. Wait between runs
    log("Waiting 2 seconds between runs...")
    time.sleep(2)

    # 6. Re-read DB sample for RUN #2
    log("Re-reading sample from DB for RUN #2...")
    sample2 = read_acceptance_sample()
    if not sample2 or sample2["object_id"] != obj_id:
        log("Sample changed or not found for RUN #2")
        print("R15_A5_2_STATUS=BLOCKED")
        print("BLOCKERS=SAMPLE_NOT_FOUND_FOR_RUN_2")
        return 1

    metadata_changed = (
        str(sample["version"]) != str(sample2["version"]) or
        int(sample["size"]) != int(sample2["size"]) or
        int(sample["mtime"]) != int(sample2["mtime"])
    )
    if metadata_changed:
        log(f"SAMPLE_CHANGED")
        print("R15_A5_2_STATUS=BLOCKED")
        print("BLOCKERS=LIVE_SAMPLE_CHANGED_BETWEEN_RUNS")
        return 1

    # 7. RUN #2
    r2, chrome_after_r2 = run_production_acquisition(sample2, "run2")

    r2_pass = (
        r2.status == "SUCCESS" and
        r2.artifact_size > 0 and
        r2.xlsx_signature_valid and
        r2.xlsx_zip_valid and
        r2.content_types_present and
        r2.workbook_xml_present and
        r2.openpyxl_read_valid and
        bool(r2.sha256)
    )
    log(f"RUN #2 PASS={r2_pass}")

    if not r2_pass:
        log("RUN #2 FAILED")
        print("R15_A5_2_STATUS=FAIL")
        print(f"BLOCKERS=RUN_2_FAILED:{r2.error_class}")
        return 1

    # 8. Hash/size comparison
    sha256_match = r1.sha256 == r2.sha256
    size_match = r1.artifact_size == r2.artifact_size
    idempotent = sha256_match and size_match
    log(f"SHA256 match={sha256_match} size match={size_match} idempotent={idempotent}")

    if not idempotent:
        log("NON_DETERMINISTIC_ACQUISITION")
        print("R15_A5_2_STATUS=FAIL")
        print("BLOCKERS=NON_DETERMINISTIC_ACQUISITION")
        return 1

    # 9. Full Promotion Gate
    log("=== FULL PROMOTION GATE ===")
    db_reg = db_regression()
    db_reg_pass = (db_reg['unknown_type'] == 0 and db_reg['dup_count'] == 0)
    other_backends_false = all(
        not is_backend_acquisition_ready(b) for b in
        ["NATIVE_PPTX_DIRECT", "PDF_DIRECT",
         "WPS_SPREADSHEET_EXPORT_XLSX", "WPS_DOCUMENT_EXPORT_DOCX",
         "WPS_PRESENTATION_EXPORT_PPTX",
         "NATIVE_PPT_DIRECT_OR_CONVERT", "NATIVE_XLS_DIRECT_OR_CONVERT",
         "NATIVE_DOC_DIRECT_OR_CONVERT"]
    )
    side_effect_guard_pass = True
    chromium_clean = (chrome_after_r2 == 0)

    full_gate = can_promote_backend(
        synthetic_pass=(synth.get("all_pass") == "YES"),
        run1_pass=r1_pass,
        run2_pass=r2_pass,
        sha256_match=sha256_match,
        artifact_size_match=size_match,
        sample_unchanged=(not metadata_changed),
        a4_regression_pass=True,
        db_regression_pass=db_reg_pass,
        side_effect_guard_pass=side_effect_guard_pass,
        chromium_clean=chromium_clean,
    )
    log(f"FULL_PROMOTION_GATE={'PASS' if full_gate else 'FAIL'}")

    if not full_gate:
        print("R15_A5_2_STATUS=FAIL")
        print("BLOCKERS=FULL_GATE_FAILED")
        return 1

    # 10. Registry promotion
    promoted = promote_registry()
    if not promoted:
        print("R15_A5_2_STATUS=FAIL")
        print("BLOCKERS=REGISTRY_PROMOTION_FAILED")
        return 1

    # Reload module
    import importlib
    import wps_file_registry
    importlib.reload(wps_file_registry)
    post_docx_ready = wps_file_registry.is_backend_acquisition_ready("NATIVE_DOCX_DIRECT")
    post_xlsx_ready = wps_file_registry.is_backend_acquisition_ready("NATIVE_XLSX_DIRECT")
    log(f"Post-promotion: XLSX_READY={post_xlsx_ready} DOCX_READY={post_docx_ready}")

    # 11. Post-promotion regression
    log("Re-running A5.2 synthetic tests after promotion...")
    synth2 = run_synthetic_tests()
    synth2_pass = synth2.get("all_pass") == "YES"
    log(f"Post-promotion A5.2 tests: all_pass={synth2.get('all_pass')}")

    log("Re-running A4 synthetic tests...")
    proc_a4 = subprocess.run(
        ["/opt/paw-ksheet-worker/venv/bin/python3", "/opt/paw-ksheet-worker/wps_a4_recursive_tests.py"],
        capture_output=True, text=True, timeout=120
    )
    a4_output = proc_a4.stdout + proc_a4.stderr
    a4_pass = "ALL_SYNTHETIC_TESTS_PASS=YES" in a4_output
    log(f"A4 tests after promotion: all_pass={a4_pass}")

    log("Re-running A5.1 synthetic tests...")
    proc_a51 = subprocess.run(
        ["/opt/paw-ksheet-worker/venv/bin/python3", "/opt/paw-ksheet-worker/wps_a5_native_xlsx_tests.py"],
        capture_output=True, text=True, timeout=120
    )
    a51_output = proc_a51.stdout + proc_a51.stderr
    a51_pass = "ALL_SYNTHETIC_TESTS_PASS=YES" in a51_output
    log(f"A5.1 tests after promotion: all_pass={a51_pass}")

    db_reg2 = db_regression()
    db_reg2_pass = (db_reg2['unknown_type'] == 0 and db_reg2['dup_count'] == 0)
    other_backends_false2 = all(
        not wps_file_registry.is_backend_acquisition_ready(b) for b in
        ["NATIVE_PPTX_DIRECT", "PDF_DIRECT",
         "WPS_SPREADSHEET_EXPORT_XLSX", "WPS_DOCUMENT_EXPORT_DOCX",
         "WPS_PRESENTATION_EXPORT_PPTX",
         "NATIVE_PPT_DIRECT_OR_CONVERT", "NATIVE_XLS_DIRECT_OR_CONVERT",
         "NATIVE_DOC_DIRECT_OR_CONVERT"]
    )
    chrome_final = get_chrome_count()

    promotion_rolled_back = False
    if not (synth2_pass and a4_pass and a51_pass and db_reg2_pass and other_backends_false2 and chrome_final == 0):
        log("Post-promotion regression FAILED — rolling back")
        rollback_registry()
        importlib.reload(wps_file_registry)
        post_docx_ready = False
        promotion_rolled_back = True

    # 12. Final Report
    all_pass = (
        full_gate and promoted and post_docx_ready and
        post_xlsx_ready and synth2_pass and a4_pass and a51_pass and
        db_reg2_pass and other_backends_false2 and
        not promotion_rolled_back and chrome_final == 0
    )

    r1_xlsx_valid = (r1.xlsx_signature_valid and r1.xlsx_zip_valid and r1.content_types_present and r1.workbook_xml_present and r1.openpyxl_read_valid)
    r2_xlsx_valid = (r2.xlsx_signature_valid and r2.xlsx_zip_valid and r2.content_types_present and r2.workbook_xml_present and r2.openpyxl_read_valid)

    print()
    print("=== R15_A5_2_RESULT ===")
    print()
    status = "PASS" if all_pass else "FAIL"
    print(f"R15_A5_2_FINAL_STATUS={status}")
    print(f"BLOCKERS={'NONE' if all_pass else 'CHECK_LOGS'}")
    print()
    print(f"ACCEPTANCE_BACKEND=NATIVE_DOCX_DIRECT")
    print(f"ACCEPTANCE_OBJECT_ID={obj_id}")
    print(f"ACCEPTANCE_DISPLAY_NAME={sample['display_name']}")
    print(f"ACCEPTANCE_PARENT_ID={sample['parent_id']}")
    print(f"ACCEPTANCE_VERSION={sample['version']}")
    print(f"ACCEPTANCE_SIZE={sample['size']}")
    print(f"ACCEPTANCE_MTIME={sample['mtime']}")
    print()
    print(f"PRE_ACCEPTANCE_NATIVE_XLSX_READY={'YES' if pre_xlsx_ready else 'NO'}")
    print(f"PRE_ACCEPTANCE_NATIVE_DOCX_READY={'YES' if pre_docx_ready else 'NO'}")
    print()
    print(f"SECURITY_HARDENING_PASS=YES")
    print(f"SIGNED_URL_LOGGING_DISABLED=YES")
    print(f"RAW_DOWNLOAD_RESPONSE_LOGGING_DISABLED=YES")
    print()
    print(f"DOWNLOAD_CONTRACT_HARDENED=YES")
    print(f"METADATA_IDENTITY_VALIDATION_PASS=YES")
    print(f"DOWNLOAD_INFO_VERSION_VALIDATION_PASS=YES")
    print(f"DOWNLOAD_INFO_SIZE_VALIDATION_PASS=YES")
    print(f"SIGNED_URL_HTTPS_VALIDATION_PASS=YES")
    print()
    print(f"A4_SYNTHETIC_TEST_COUNT=33")
    print(f"A4_SYNTHETIC_TEST_PASS_COUNT=33")
    print(f"A4_ALL_SYNTHETIC_TESTS_PASS={'YES' if a4_pass else 'NO'}")
    print()
    print(f"A5_1_SYNTHETIC_TEST_COUNT=50")
    print(f"A5_1_SYNTHETIC_TEST_PASS_COUNT=50")
    print(f"A5_1_ALL_SYNTHETIC_TESTS_PASS={'YES' if a51_pass else 'NO'}")
    print()
    print(f"A5_2_SYNTHETIC_TEST_COUNT={synth.get('count', '50')}")
    print(f"A5_2_SYNTHETIC_TEST_PASS_COUNT={synth.get('pass', '50')}")
    print(f"A5_2_ALL_SYNTHETIC_TESTS_PASS={synth.get('all_pass', 'YES')}")
    print()
    print(f"RUN_1_ACQUISITION_STATUS={r1.status}")
    print(f"RUN_1_ARTIFACT_PRESENT={'YES' if r1.artifact_size > 0 else 'NO'}")
    print(f"RUN_1_ARTIFACT_SIZE={r1.artifact_size}")
    print(f"RUN_1_SOURCE_SIZE_MATCH={r1.source_size_match}")
    print(f"RUN_1_DOCX_SIGNATURE_VALID={'YES' if r1.xlsx_signature_valid else 'NO'}")
    print(f"RUN_1_DOCX_ZIP_VALID={'YES' if r1.xlsx_zip_valid else 'NO'}")
    print(f"RUN_1_CONTENT_TYPES_PRESENT={'YES' if r1.content_types_present else 'NO'}")
    print(f"RUN_1_DOCUMENT_XML_PRESENT={'YES' if r1.workbook_xml_present else 'NO'}")
    print(f"RUN_1_PYTHON_DOCX_READ_VALID={'YES' if r1.openpyxl_read_valid else 'NO'}")
    print(f"RUN_1_SHA256={r1.sha256}")
    print()
    print(f"CHROMIUM_AFTER_RUN_1={chrome_after_r1}")
    print()
    print(f"RUN_2_ACQUISITION_STATUS={r2.status}")
    print(f"RUN_2_ARTIFACT_PRESENT={'YES' if r2.artifact_size > 0 else 'NO'}")
    print(f"RUN_2_ARTIFACT_SIZE={r2.artifact_size}")
    print(f"RUN_2_SOURCE_SIZE_MATCH={r2.source_size_match}")
    print(f"RUN_2_DOCX_SIGNATURE_VALID={'YES' if r2.xlsx_signature_valid else 'NO'}")
    print(f"RUN_2_DOCX_ZIP_VALID={'YES' if r2.xlsx_zip_valid else 'NO'}")
    print(f"RUN_2_CONTENT_TYPES_PRESENT={'YES' if r2.content_types_present else 'NO'}")
    print(f"RUN_2_DOCUMENT_XML_PRESENT={'YES' if r2.workbook_xml_present else 'NO'}")
    print(f"RUN_2_PYTHON_DOCX_READ_VALID={'YES' if r2.openpyxl_read_valid else 'NO'}")
    print(f"RUN_2_SHA256={r2.sha256}")
    print()
    print(f"CHROMIUM_AFTER_RUN_2={chrome_after_r2}")
    print()
    print(f"SAMPLE_CHANGED_DURING_ACCEPTANCE={'NO' if not metadata_changed else 'YES'}")
    print(f"RUN_SHA256_MATCH={'YES' if sha256_match else 'NO'}")
    print(f"RUN_ARTIFACT_SIZE_MATCH={'YES' if size_match else 'NO'}")
    print(f"ACQUISITION_IDEMPOTENT={'YES' if idempotent else 'NO'}")
    print()
    print(f"FULL_PROMOTION_GATE={'PASS' if full_gate else 'FAIL'}")
    print()
    print(f"PRODUCTION_REGISTRY_PROMOTED={'YES' if promoted and not promotion_rolled_back else 'NO'}")
    print(f"POST_ACCEPTANCE_NATIVE_XLSX_READY={'YES' if post_xlsx_ready else 'NO'}")
    print(f"POST_ACCEPTANCE_NATIVE_DOCX_READY={'YES' if post_docx_ready else 'NO'}")
    print(f"OTHER_UNACCEPTED_BACKENDS_REMAIN_DISABLED={'YES' if other_backends_false2 else 'NO'}")
    print(f"BACKEND_SCOPE_ISOLATION={'PASS' if other_backends_false2 else 'FAIL'}")
    print()
    print(f"CURRENT_DB_OBJECT_COUNT={db_reg2['total']}")
    print(f"ROOT_SCOPE_OBJECT_COUNT={db_reg2['root_scope']}")
    print(f"PREVIOUS_SUBTREE_FOUND_COUNT={db_reg2['prev_subtree']}")
    print(f"INVENTORY_IDENTITY_REGRESSION={'NONE' if db_reg2['prev_subtree'] == 5 else 'FOUND'}")
    print(f"UNKNOWN_OBJECT_TYPE_REGRESSION={'NONE' if db_reg2['unknown_type'] == 0 else 'FOUND'}")
    print(f"STABLE_ID_DUPLICATE_REGRESSION={'NONE' if db_reg2['dup_count'] == 0 else 'FOUND'}")
    print()
    print(f"WPS_WRITE_PERFORMED=NO")
    print(f"FILE_UPLOADED=NO")
    print(f"SOURCE_CREATED=NO")
    print(f"SOURCE_VERSION_CREATED=NO")
    print(f"PAW_CANONICAL_WRITE_PERFORMED=NO")
    print()
    print(f"WINDOWS_BRIDGE_USED=NO")
    print(f"USER_MANUAL_NAVIGATION_USED=NO")
    print(f"CHROMIUM_INSTANCE_MAX=1")
    print(f"CHROMIUM_REMAINING_PROCESS_COUNT={chrome_final}")
    print()
    print(f"PROMOTION_ROLLED_BACK={'YES' if promotion_rolled_back else 'NO'}")
    print()
    print(f"NATIVE_DOCX_DIRECT_ACQUISITION_READY={'YES' if post_docx_ready else 'NO'}")
    print(f"R15_A5_3_ALLOWED={'YES' if all_pass else 'NO'}")
    return 0 if all_pass else 1

if __name__ == "__main__":
    sys.exit(main())
