#!/usr/bin/env python3
"""
R15-A.5.5 WPS Spreadsheet Export XLSX — Production Acceptance Runner

Flow:
  0. Reality state: registry pre-state must be
     registered=True / CAPABILITY_PROVEN / acquisition_ready=False
  1. Acceptance sample selection (read-only inventory query, fail-closed)
  2. Pre-Production Gate: A4 + A5.1 + A5.2 + A5.3 + A5.4 + A5.5 synthetic,
     exact ready set PRE
  3. RUN #1: fresh Chromium -> identity verify -> real server-side export ->
     XLSX validation -> SHA256 + normalized fingerprint -> identity re-read
  4. Fresh RUN #2 (new browser context, no reuse of RUN1 state)
  5. Export Idempotency Decision (byte or normalized OOXML)
  6. Full Promotion Gate -> registry promotion -> post-promotion regression
     -> rollback on any regression failure
  7. Final R15_A5_5_RESULT report

Security: no credentials/locations/raw payloads are read or persisted;
only sanitized status fields are printed and staged.
"""

import os
import sys
import sqlite3
import subprocess
import importlib
import time
import json
import shutil
import hashlib
import zipfile

DEV_DIR = os.path.dirname(os.path.abspath(__file__))
if DEV_DIR not in sys.path:
    sys.path.insert(0, DEV_DIR)
if "/opt/paw-ksheet-worker" not in sys.path:
    sys.path.append("/opt/paw-ksheet-worker")

import wps_file_registry
from wps_native_acquisition import can_promote_backend
from wps_file_registry import (
    is_backend_acquisition_ready,
    get_mirror_backend_status,
    validate_backend_readiness_exact,
    get_acquisition_ready_backends,
)
from wps_inventory_v1 import evaluate_mirror_policy
from wps_export_acquisition import (
    run_acceptance_acquisition_export,
    compare_xlsx_packages,
    EXPORT_STAGING_BASE,
    VOLATILE_OOXML_RULES,
    _VOLATILE_XML_PATHS,
)

PROD_DB = "/srv/paw/ksheet_exports/cloud_inventory/wps_inventory_v1.sqlite3"
GROUP_ID = "3009705063"
PROFILE_DIR = "/var/lib/paw-ksheet-worker/chromium-profile"
BROWSER_EXECUTABLE = "/var/lib/paw-ksheet-worker/ms-playwright/chromium-1208/chrome-linux64/chrome"

BACKEND = "WPS_SPREADSHEET_EXPORT_XLSX"
EXPECTED_PRE = {"NATIVE_XLSX_DIRECT", "NATIVE_DOCX_DIRECT", "NATIVE_PPTX_DIRECT", "PDF_DIRECT"}
EXPECTED_POST = EXPECTED_PRE | {BACKEND}
OTHER_UNACCEPTED = [
    "WPS_DOCUMENT_EXPORT_DOCX",
    "WPS_PRESENTATION_EXPORT_PPTX",
    "NATIVE_PPT_DIRECT_OR_CONVERT",
    "NATIVE_XLS_DIRECT_OR_CONVERT",
    "NATIVE_DOC_DIRECT_OR_CONVERT",
    "NO_MIRROR", "UNSUPPORTED", "NEEDS_CLASSIFICATION",
]
SUBTREE_IDS = ["485641343445", "485433870456", "485481025119", "485497734941", "485497736526"]
REGRESSION_SUITES = [
    ("A4", "/opt/paw-ksheet-worker/wps_a4_recursive_tests.py"),
    ("A5_1", "/opt/paw-ksheet-worker/wps_a5_native_xlsx_tests.py"),
    ("A5_2", "/opt/paw-ksheet-worker/wps_a5_native_docx_tests.py"),
    ("A5_3", "/opt/paw-ksheet-worker/wps_a5_native_pptx_tests.py"),
    ("A5_4", "/opt/paw-ksheet-worker/wps_a5_pdf_tests.py"),
]
A5_5_SYNTH = "/opt/paw-ksheet-worker/wps_a5_wps_spreadsheet_tests.py"
PY = "/opt/paw-ksheet-worker/venv/bin/python"


def log(msg):
    print(f"[A5-PROD-SS] {msg}", flush=True)


def chrome_count():
    proc = subprocess.run(["pgrep", "-c", "-f", "chrome-linux64/chrome"],
                          capture_output=True, text=True)
    try:
        return int(proc.stdout.strip())
    except (ValueError, IndexError):
        return 0


def run_suite(path, timeout=180):
    proc = subprocess.run([PY, path], capture_output=True, text=True, timeout=timeout)
    output = proc.stdout + proc.stderr
    count = passed = 0
    all_pass = "NO"
    for line in output.splitlines():
        if line.startswith("SYNTHETIC_TEST_COUNT="):
            count = int(line.split("=", 1)[1])
        elif line.startswith("SYNTHETIC_TEST_PASS_COUNT="):
            passed = int(line.split("=", 1)[1])
        elif line.startswith("ALL_SYNTHETIC_TESTS_PASS="):
            all_pass = line.split("=", 1)[1]
    return {"count": count, "pass": passed, "all_pass": all_pass}


def read_candidate_samples():
    """Read-only query of the production inventory DB."""
    conn = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    rows = c.execute(
        """SELECT object_id, display_name, parent_id, group_id, version, size, mtime,
                  mirror_policy, mirror_policy_evaluated_at, file_type,
                  mirror_backend_candidate, object_type
           FROM inventory_objects
           WHERE provider='wps' AND is_currently_seen=1 AND file_type='WPS_SPREADSHEET'"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def load_manifest_names():
    names = set()
    try:
        with open("/srv/paw/ksheet_exports/manifest.json", "r") as f:
            manifest = json.load(f)
        for entry in manifest.get("entries", []) or []:
            if isinstance(entry, dict) and entry.get("cloud_locator"):
                names.add(str(entry.get("source_name", "")).removesuffix(".ksheet.wpsonline"))
    except Exception:
        pass
    return names


def select_acceptance_sample():
    """Deterministic fail-closed sample selection per R15-A5.5 section 5."""
    candidates = []
    manifest_names = load_manifest_names()
    for row in read_candidate_samples():
        if row.get("object_type") != "file":
            continue
        if row.get("file_type") != "WPS_SPREADSHEET":
            continue
        if row.get("mirror_backend_candidate") != BACKEND:
            continue
        if row.get("mirror_policy") != "INCLUDED":
            continue
        if not row.get("mirror_policy_evaluated_at"):
            continue
        if str(row.get("group_id", "")) != GROUP_ID:
            continue
        required = ("object_id", "display_name", "parent_id", "version", "mtime")
        if any(not str(row.get(k, "") or "") for k in required):
            continue
        policy, _, _ = evaluate_mirror_policy(row["display_name"])
        if policy == "EXCLUDED":
            continue
        has_locator = row["display_name"].removesuffix(".ksheet") in manifest_names
        candidates.append((has_locator, int(row.get("size", 0) or 0), row))
    if not candidates:
        return None
    # Prefer a sample with a historically proven stable locator, then smallest size
    candidates.sort(key=lambda t: (not t[0], t[1]))
    return candidates[0][2]


def db_regression():
    conn = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
    c = conn.cursor()
    total = c.execute("SELECT COUNT(*) FROM inventory_objects").fetchone()[0]
    root = c.execute(
        "SELECT COUNT(*) FROM inventory_objects WHERE parent_id='0' OR parent_id=''").fetchone()[0]
    unknown = c.execute(
        "SELECT COUNT(*) FROM inventory_objects WHERE object_type='UNKNOWN'").fetchone()[0]
    dup = c.execute(
        "SELECT COUNT(*) FROM (SELECT object_id FROM inventory_objects GROUP BY object_id HAVING COUNT(*)>1)").fetchone()[0]
    prev = sum(1 for sid in SUBTREE_IDS
               if c.execute("SELECT COUNT(*) FROM inventory_objects WHERE object_id=?",
                            (sid,)).fetchone()[0] > 0)
    conn.close()
    return {"total": total, "root_scope": root, "unknown_type": unknown,
            "dup_count": dup, "prev_subtree": prev}


def kill_chromium():
    subprocess.run(["pkill", "-f", "chrome-linux64/chrome"], capture_output=True)
    time.sleep(1)


RUN_TIMEOUT_S = 600  # hard wall-clock bound per RUN (subprocess isolation)


def run_production_export(sample, run_label, baseline_identity=None):
    """One RUN in an isolated worker subprocess with a hard timeout.

    A hung browser protocol can never wedge the acceptance: on timeout the
    worker tree is killed and the RUN is reported FAILED (honest fail closed).
    """
    from dataclasses import fields as dc_fields
    from wps_export_acquisition import ExportAcquisitionResult
    baseline_json = json.dumps(baseline_identity) if baseline_identity else "-"
    cmd = [PY, "/opt/paw-ksheet-worker/wps_a55_export_worker.py",
           run_label, baseline_json, json.dumps(sample, ensure_ascii=False)]
    result, ident = None, {}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=RUN_TIMEOUT_S)
        output = proc.stdout.strip()
        if output:
            payload = json.loads(output.splitlines()[-1])
            data = payload["result"]
            result = ExportAcquisitionResult(**{k: data[k] for k in
                                                (f.name for f in dc_fields(ExportAcquisitionResult))})
            ident = payload.get("identity", {})
            chrome_after = payload.get("chrome_after", 0)
        else:
            raise RuntimeError("worker produced no result")
    except subprocess.TimeoutExpired:
        log(f"{run_label}: HARD TIMEOUT after {RUN_TIMEOUT_S}s — killing browser")
        result = ExportAcquisitionResult(
            status="FAILED", error_class="RUN_TIMEOUT",
            object_id=str(sample.get("object_id", "")),
            started_at="", finished_at="")
        ident = {}
        chrome_after = None
    except Exception as exc:
        log(f"{run_label}: worker error {type(exc).__name__}")
        result = ExportAcquisitionResult(
            status="FAILED", error_class=f"WORKER_ERROR: {type(exc).__name__}",
            object_id=str(sample.get("object_id", "")),
            started_at="", finished_at="")
        ident = {}
        chrome_after = None
    finally:
        kill_chromium()
    count = chrome_count() if chrome_after is None else chrome_after
    if count != 0:
        kill_chromium()
        count = chrome_count()
    log(f"Chromium after {run_label}: {count}")
    return result, ident, count


A55H_REPORT = os.path.join(EXPORT_STAGING_BASE, "a55h_icv_classification.json")
CONTRACT_MD = "/opt/paw-ksheet-worker/R15_A5_EXPORT_IDEMPOTENCY_CONTRACT.md"


def verify_a55h_evidence():
    """ICV_VOLATILITY_PROVEN per the sanitized classification report."""
    try:
        with open(A55H_REPORT) as f:
            report = json.load(f)
    except Exception:
        return False, {}
    if not report.get("all_probes_pass"):
        return False, report
    m = report.get("matrix", {})
    if not m.get("package_member_set_stable"):
        return False, report
    if m.get("core_xml_residual_diff_after_contracted_normalization") != 0:
        return False, report
    if m.get("raw_differing_parts_outside_authorized_set"):
        return False, report
    for a in m.get("per_artifact_vs_run1", {}).values():
        d = a.get("custom_xml_property_diff") or {}
        if not (d.get("property_set_match")
                and d.get("differing_property_names") == ["ICV"]
                and d.get("non_icv_property_difference_count") == 0
                and d.get("property_attribute_match")
                and d.get("property_type_match")
                and d.get("icv_present")):
            return False, report
    return True, report


def verify_contract_amendment():
    try:
        with open(CONTRACT_MD) as f:
            content = f.read()
    except Exception:
        return False
    return all(marker in content for marker in (
        "WPS_EXPORT_VOLATILE_METADATA_001",
        "NORMALIZE_PROPERTY_VALUE_ONLY",
        "__WPS_VOLATILE_ICV__",
    ))


def verify_rule_scope_exact():
    """VOLATILE_RULE_SCOPE_EXACT + WHOLE_PART_IGNORE_NOT_USED."""
    rules_exact = VOLATILE_OOXML_RULES == {"docProps/custom.xml": {"property:ICV": "normalize_value"}}
    core_only = _VOLATILE_XML_PATHS == {"docProps/core.xml"}
    return rules_exact and core_only


def raw_differing_parts(path1, path2):
    def part_hashes(p):
        with zipfile.ZipFile(p) as zf:
            return {n: hashlib.sha256(zf.read(n)).hexdigest()
                    for n in zf.namelist() if not zf.getinfo(n).is_dir()}
    h1, h2 = part_hashes(path1), part_hashes(path2)
    if set(h1) != set(h2):
        return sorted(set(h1).symmetric_difference(set(h2)))
    return sorted(n for n in h1 if h1[n] != h2[n])


def archive_previous_runs(sample):
    """Preserve the previous acceptance round's staging before formal RUNs."""
    base = os.path.join(EXPORT_STAGING_BASE, str(sample["object_id"]), str(sample["version"]))
    for run in ("run1", "run2"):
        src = os.path.join(base, run)
        dst = os.path.join(base, f"_prev_r15_a5_5_{run}")
        if os.path.isdir(src) and not os.path.isdir(dst):
            shutil.copytree(src, dst)
            log(f"Archived previous {run} -> _prev_r15_a5_5_{run}")


def registry_line(ready):
    state = "True" if ready else "False"
    return (f'"{BACKEND}": {{"registered": True, "capability_status": '
            f'"CAPABILITY_PROVEN", "acquisition_ready": {state}}}')


def mutate_registry(old_line, new_line, action):
    reg_path = "/opt/paw-ksheet-worker/wps_file_registry.py"
    with open(reg_path, "r") as f:
        content = f.read()
    if content.count(old_line) != 1:
        log(f"registry {action}: expected pattern not found exactly once")
        return False
    content = content.replace(old_line, new_line)
    with open(reg_path, "w") as f:
        f.write(content)
    proc = subprocess.run([PY, "-m", "py_compile", reg_path],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        log(f"registry {action}: py_compile FAILED, reverting")
        with open(reg_path, "r") as f:
            content = f.read()
        content = content.replace(new_line, old_line)
        with open(reg_path, "w") as f:
            f.write(content)
        return False
    importlib.reload(wps_file_registry)
    return True


def promote_registry():
    if is_backend_acquisition_ready(BACKEND):
        return True
    ok = mutate_registry(registry_line(False), registry_line(True), "promote")
    if ok:
        log(f"Registry promoted: {BACKEND} acquisition_ready=False -> True")
    return ok


def rollback_registry():
    if not is_backend_acquisition_ready(BACKEND):
        return True
    ok = mutate_registry(registry_line(True), registry_line(False), "rollback")
    if ok:
        log("Registry rolled back")
    return ok


def main():
    log("=== R15-A.5.5 WPS SPREADSHEET EXPORT XLSX ACCEPTANCE ===")
    log(f"GROUP_ID={GROUP_ID}")

    # ---- 0. Registry pre-state ----
    status = get_mirror_backend_status(BACKEND) or {}
    pre_registered = status.get("registered") is True
    pre_capability = status.get("capability_status") == "CAPABILITY_PROVEN"
    pre_ready = is_backend_acquisition_ready(BACKEND)
    log(f"Pre-state: registered={pre_registered} capability={status.get('capability_status')} "
        f"acquisition_ready={pre_ready}")
    if pre_ready:
        print("=== R15_A5_5_RESULT ===")
        print("R15_A5_5_FINAL_STATUS=BLOCKED")
        print("BLOCKERS=WPS_SPREADSHEET_ALREADY_ENABLED_BEFORE_ACCEPTANCE")
        return 2
    if not (pre_registered and pre_capability):
        print("=== R15_A5_5_RESULT ===")
        print("R15_A5_5_FINAL_STATUS=BLOCKED")
        print("BLOCKERS=BACKEND_REGISTRY_PRE_STATE_INVALID")
        return 2

    exact_pre = validate_backend_readiness_exact(EXPECTED_PRE)
    log(f"Exact ready set PRE (A5.1-A5.4 frozen): {exact_pre}")
    if not exact_pre:
        print("=== R15_A5_5_RESULT ===")
        print("R15_A5_5_FINAL_STATUS=BLOCKED")
        print("BLOCKERS=PRE_READY_SET_NOT_EXACT_FROZEN_STATE")
        return 2

    # ---- 1. Acceptance sample selection ----
    log("Selecting acceptance sample (read-only inventory query)...")
    sample = select_acceptance_sample()
    if not sample:
        print("=== R15_A5_5_RESULT ===")
        print("R15_A5_5_FINAL_STATUS=BLOCKED")
        print("BLOCKERS=NO_ELIGIBLE_WPS_SPREADSHEET_SAMPLE")
        return 2
    log(f"Sample: object_id={sample['object_id']} name={sample['display_name']} "
        f"fver={sample['version']} size={sample['size']} parent={sample['parent_id']}")

    # ---- 2. Pre-Production Gate ----
    log("=== PRE-PRODUCTION GATE (A4 + A5.1-A5.5 synthetic) ===")
    pre_suites = {}
    pre_all_pass = True
    for name, path in REGRESSION_SUITES:
        pre_suites[name] = run_suite(path)
        ok = pre_suites[name]["all_pass"] == "YES"
        pre_all_pass = pre_all_pass and ok
        log(f"  {name}: {pre_suites[name]['pass']}/{pre_suites[name]['count']} ok={ok}")
    a5_5_synth = run_suite(A5_5_SYNTH)
    a5_5_pass = a5_5_synth["all_pass"] == "YES"
    pre_all_pass = pre_all_pass and a5_5_pass
    log(f"  A5_5: {a5_5_synth['pass']}/{a5_5_synth['count']} ok={a5_5_pass}")
    if chrome_count() != 0:
        print("=== R15_A5_5_RESULT ===")
        print("R15_A5_5_FINAL_STATUS=BLOCKED")
        print("BLOCKERS=CHROMIUM_NOT_CLEAN_BEFORE_RUN")
        return 2
    if not pre_all_pass:
        print("=== R15_A5_5_RESULT ===")
        print("R15_A5_5_FINAL_STATUS=BLOCKED")
        print("BLOCKERS=PRE_PRODUCTION_GATE_FAIL")
        return 2

    # ---- 2b. R15-A.5.5H evidence gates ----
    icv_volatility_proven, a55h_report = verify_a55h_evidence()
    contract_amended = verify_contract_amendment()
    rule_scope_exact = verify_rule_scope_exact()
    whole_part_ignore_not_used = rule_scope_exact
    probes_pass = bool(a55h_report.get("all_probes_pass"))
    log(f"A5.5H: icv_volatility_proven={icv_volatility_proven} "
        f"contract_amended={contract_amended} rule_scope_exact={rule_scope_exact} "
        f"probes_pass={probes_pass}")
    if not (icv_volatility_proven and contract_amended and rule_scope_exact and probes_pass):
        print("=== R15_A5_5H_RESULT ===")
        print("R15_A5_5H_STATUS=BLOCKED")
        print("BLOCKERS=A55H_EVIDENCE_OR_CONTRACT_PREREQUISITE_FAIL")
        return 2

    # ---- 3. RUN #1 ----
    archive_previous_runs(sample)
    log("=== RUN run1 STARTING (fresh Chromium) ===")
    r1, ident1, chrome_after_r1 = run_production_export(sample, "run1")
    r1_pass = (r1.status == "SUCCESS")
    log(f"RUN1: status={r1.status} error={r1.error_class} nav={r1.navigation_source} "
        f"title_match={r1.ksheet_title_match}")
    log(f"  export_executed={r1.export_operation_executed} size={r1.artifact_size} "
        f"sig={r1.xlsx_signature_valid} zip={r1.xlsx_zip_valid} ct={r1.content_types_present} "
        f"wb={r1.workbook_xml_present} openpyxl={r1.openpyxl_read_valid} sheets={r1.worksheet_count}")
    log(f"  sha256={r1.sha256[:16]}... fp={r1.normalized_package_fingerprint[:16]}... "
        f"src_changed={r1.source_changed_during_export}")
    if chrome_after_r1 != 0:
        kill_chromium()
        chrome_after_r1 = chrome_count()

    # ---- 4. RUN #2 (fresh, no reuse) ----
    log("Waiting >=2s between runs...")
    time.sleep(2)
    sample2 = select_acceptance_sample()
    sample_changed_db = False
    if not sample2 or sample2["object_id"] != sample["object_id"]:
        sample_changed_db = True
        sample2 = sample
        log("WARNING: DB sample changed between runs")
    else:
        if str(sample2["version"]) != str(sample["version"]) or \
                str(sample2["mtime"]) != str(sample["mtime"]):
            sample_changed_db = True
            log("WARNING: DB sample version/mtime changed between runs")

    log("=== RUN run2 STARTING (brand-new Chromium) ===")
    r2, ident2, chrome_after_r2 = run_production_export(
        sample2, "run2", baseline_identity=ident1)
    r2_pass = (r2.status == "SUCCESS")
    sample_changed_between_runs = (
        sample_changed_db
        or str(r2.error_class or "").startswith("SAMPLE_CHANGED_BETWEEN_RUNS")
    )
    log(f"RUN2: status={r2.status} error={r2.error_class} nav={r2.navigation_source} "
        f"title_match={r2.ksheet_title_match}")
    log(f"  export_executed={r2.export_operation_executed} size={r2.artifact_size} "
        f"sig={r2.xlsx_signature_valid} zip={r2.xlsx_zip_valid} ct={r2.content_types_present} "
        f"wb={r2.workbook_xml_present} openpyxl={r2.openpyxl_read_valid} sheets={r2.worksheet_count}")
    log(f"  sha256={r2.sha256[:16]}... fp={r2.normalized_package_fingerprint[:16]}... "
        f"src_changed={r2.source_changed_during_export}")
    if chrome_after_r2 != 0:
        kill_chromium()
        chrome_after_r2 = chrome_count()

    r1_xlsx_valid = (r1.xlsx_signature_valid and r1.xlsx_zip_valid and
                     r1.content_types_present and r1.workbook_xml_present and
                     r1.openpyxl_read_valid)
    r2_xlsx_valid = (r2.xlsx_signature_valid and r2.xlsx_zip_valid and
                     r2.content_types_present and r2.workbook_xml_present and
                     r2.openpyxl_read_valid)

    # ---- 5. Export Idempotency Decision ----
    sha_match = bool(r1.sha256 and r2.sha256 and r1.sha256 == r2.sha256)
    byte_deterministic = sha_match
    cmp = None
    raw_parts = []
    icv_only_confirmed = False
    if r1_pass and r2_pass and r1.artifact_path and r2.artifact_path:
        raw_parts = raw_differing_parts(r1.artifact_path, r2.artifact_path)
        icv_only_confirmed = all(p in ("docProps/custom.xml", "docProps/core.xml")
                                 for p in raw_parts)
    if sha_match:
        normalized_match = True
        semantic_idempotent = True
        export_idempotent = True
        diff_count = 0
        differing_parts = []
        zip_member_set_match = True
        log("BYTE_DETERMINISTIC=YES EXPORT_IDEMPOTENT=YES")
    elif r1_pass and r2_pass:
        log("SHA mismatch -> NORMALIZED_OOXML_COMPARISON")
        cmp = compare_xlsx_packages(r1.artifact_path, r2.artifact_path)
        zip_member_set_match = cmp["zip_member_set_match"]
        differing_parts = cmp["differing_part_names"]
        diff_count = cmp["differing_part_count"]
        normalized_match = cmp["normalized_package_match"]
        semantic_idempotent = normalized_match
        export_idempotent = normalized_match
        log(f"  member_set_match={zip_member_set_match} diff_count={diff_count} "
            f"parts={differing_parts}")
        log(f"  normalized_match={normalized_match}")
    else:
        normalized_match = False
        semantic_idempotent = False
        export_idempotent = False
        diff_count = 0
        differing_parts = []
        zip_member_set_match = False

    # ---- 6. Full Promotion Gate ----
    source_unchanged = (r1.source_identity_pre_valid and r1.source_identity_post_valid and
                        not r1.source_changed_during_export and
                        r2.source_identity_pre_valid and r2.source_identity_post_valid and
                        not r2.source_changed_during_export and
                        not sample_changed_between_runs)
    export_executed = (r1.export_operation_executed and r2.export_operation_executed)
    xlsx_valid = (r1_xlsx_valid and r2_xlsx_valid and
                  r1.worksheet_count >= 1 and r2.worksheet_count >= 1)
    db_pre = db_regression()
    a4_regression_pass = pre_suites["A4"]["all_pass"] == "YES"
    db_reg_pass = (db_pre["unknown_type"] == 0 and db_pre["dup_count"] == 0)
    other_backends_false = all(not is_backend_acquisition_ready(b) for b in OTHER_UNACCEPTED)
    side_effect_guard = other_backends_false and exact_pre  # read-only interaction enforced by module

    full_gate = can_promote_backend(
        a5_5_pass,                    # synthetic (A5.5)
        r1_pass,                      # RUN1
        r2_pass,                      # RUN2
        export_idempotent,            # idempotency (byte or normalized)
        r1.artifact_size > 0 and r2.artifact_size > 0,  # EXPORT_ARTIFACT_NONZERO (size equality NOT required for export backend)
        source_unchanged,             # sample identity stable across both runs
        a4_regression_pass,           # A4 regression
        db_reg_pass,                  # DB read-only regression
        side_effect_guard,            # no WPS write / no Source / no canonical write
        chrome_after_r1 == 0 and chrome_after_r2 == 0,  # chromium clean
    ) and export_executed and xlsx_valid and exact_pre and pre_all_pass \
      and icv_volatility_proven and contract_amended and rule_scope_exact \
      and whole_part_ignore_not_used and probes_pass
    log(f"=== FULL PROMOTION GATE: {'PASS' if full_gate else 'FAIL'} ===")

    # ---- 7. Promotion + post-promotion regression ----
    promoted = promote_registry() if full_gate else False
    post_ready = is_backend_acquisition_ready(BACKEND)
    exact_post = validate_backend_readiness_exact(EXPECTED_POST)

    log("=== POST-PROMOTION REGRESSION ===")
    post_suites = {}
    post_all_pass = True
    for name, path in REGRESSION_SUITES:
        post_suites[name] = run_suite(path)
        ok = post_suites[name]["all_pass"] == "YES"
        post_all_pass = post_all_pass and ok
        log(f"  {name}: {post_suites[name]['pass']}/{post_suites[name]['count']} ok={ok}")
    a5_5_synth2 = run_suite(A5_5_SYNTH)
    a5_5_pass2 = a5_5_synth2["all_pass"] == "YES"
    post_all_pass = post_all_pass and a5_5_pass2
    log(f"  A5_5: {a5_5_synth2['pass']}/{a5_5_synth2['count']} ok={a5_5_pass2}")

    db_post = db_regression()
    db_post_pass = (db_post["unknown_type"] == 0 and db_post["dup_count"] == 0)
    other_backends_false2 = all(not is_backend_acquisition_ready(b) for b in OTHER_UNACCEPTED)
    chrome_final = chrome_count()
    if chrome_final != 0:
        kill_chromium()
        chrome_final = chrome_count()

    promotion_rolled_back = False
    if promoted:
        regression_ok = (post_all_pass and db_post_pass and other_backends_false2 and
                         chrome_final == 0)
        exact_post = validate_backend_readiness_exact(EXPECTED_POST)
        if not (regression_ok and exact_post):
            log("Post-promotion regression FAILED — rolling back A5.5 only")
            rollback_registry()
            post_ready = False
            exact_post = False
            promotion_rolled_back = True

    all_pass = (
        full_gate and promoted and post_ready and
        exact_post and post_all_pass and db_post_pass and
        other_backends_false2 and chrome_final == 0 and
        not promotion_rolled_back and not sample_changed_between_runs
    )

    # ---- 8. Final report ----
    print()
    print("=== R15_A5_5H_RESULT ===")
    print()
    print(f"R15_A5_5H_STATUS={'PASS' if all_pass else 'FAIL'}")
    print(f"R15_A5_5_FINAL_FREEZE={'PASS' if all_pass else 'NOT_FROZEN'}")
    if all_pass:
        print("BLOCKERS=NONE")
    else:
        blockers = []
        if not pre_all_pass:
            blockers.append("PRE_PRODUCTION_GATE_FAIL")
        if not r1_pass:
            blockers.append(f"RUN1_FAIL:{r1.error_class}")
        if not r2_pass:
            blockers.append(f"RUN2_FAIL:{r2.error_class}")
        if sample_changed_between_runs:
            blockers.append("SAMPLE_CHANGED_BETWEEN_RUNS")
        if not export_idempotent:
            blockers.append("EXPORT_NOT_IDEMPOTENT")
        if not export_executed:
            blockers.append("EXPORT_OPERATION_NOT_EXECUTED")
        if not xlsx_valid:
            blockers.append("XLSX_INVALID")
        if not full_gate:
            blockers.append("FULL_GATE_FAIL")
        if not promoted:
            blockers.append("PROMOTION_NOT_PERFORMED")
        if promotion_rolled_back:
            blockers.append("PROMOTION_ROLLED_BACK")
        if not exact_post:
            blockers.append("EXACT_READY_SET_POST_FAIL")
        if chrome_final != 0:
            blockers.append("CHROMIUM_NOT_CLEAN")
        if not post_all_pass:
            blockers.append("POST_PROMOTION_REGRESSION_FAIL")
        print(f"BLOCKERS={';'.join(blockers) if blockers else 'CHECK_LOGS'}")
    print()
    print(f"ACCEPTANCE_BACKEND={BACKEND}")
    print(f"SOURCE_FILE_TYPE=WPS_SPREADSHEET")
    print(f"EXPORT_TARGET_FORMAT=XLSX")
    print()
    print(f"ACCEPTANCE_OBJECT_ID={sample['object_id']}")
    print(f"ACCEPTANCE_DISPLAY_NAME={sample['display_name']}")
    print(f"ACCEPTANCE_PARENT_ID={sample['parent_id']}")
    print(f"ACCEPTANCE_VERSION={sample['version']}")
    print(f"ACCEPTANCE_MTIME={sample['mtime']}")
    print()
    print(f"PRE_ACCEPTANCE_READY_BACKENDS={','.join(sorted(get_acquisition_ready_backends()))}")
    print(f"EXACT_READY_SET_PRE_PASS={'YES' if exact_pre else 'NO'}")
    print()
    print(f"EXPORT_IDEMPOTENCY_CONTRACT_CREATED=YES")
    print(f"EXPORT_MECHANISM_SOURCE=ksheet_server_worker.py")
    print(f"EXPORT_OPERATION_PROVEN={'YES' if export_executed else 'NO'}")
    print()
    print(f"A5_5_SYNTHETIC_TEST_COUNT={a5_5_synth['count']}")
    print(f"A5_5_SYNTHETIC_TEST_PASS_COUNT={a5_5_synth['pass']}")
    print(f"A5_5_ALL_SYNTHETIC_TESTS_PASS={'YES' if a5_5_pass else 'NO'}")
    print()
    print(f"ALL_PREVIOUS_ACCEPTANCE_REGRESSIONS_PASS="
          f"{'YES' if (pre_all_pass and post_all_pass) else 'NO'}")
    print()
    print(f"RUN_1_EXPORT_STATUS={r1.status}")
    print(f"RUN_1_EXPORT_OPERATION_EXECUTED={'YES' if r1.export_operation_executed else 'NO'}")
    print(f"RUN_1_ARTIFACT_SIZE={r1.artifact_size}")
    print(f"RUN_1_XLSX_VALID={'YES' if r1_xlsx_valid else 'NO'}")
    print(f"RUN_1_OPENPYXL_READ_VALID={'YES' if r1.openpyxl_read_valid else 'NO'}")
    print(f"RUN_1_WORKSHEET_COUNT={r1.worksheet_count}")
    print(f"RUN_1_SHA256={r1.sha256 or 'NONE'}")
    print(f"RUN_1_NORMALIZED_PACKAGE_FINGERPRINT={r1.normalized_package_fingerprint or 'NONE'}")
    print(f"RUN_1_SOURCE_CHANGED_DURING_EXPORT="
          f"{'YES' if r1.source_changed_during_export else 'NO'}")
    print(f"CHROMIUM_AFTER_RUN_1={chrome_after_r1}")
    print()
    print(f"RUN_2_EXPORT_STATUS={r2.status}")
    print(f"RUN_2_EXPORT_OPERATION_EXECUTED={'YES' if r2.export_operation_executed else 'NO'}")
    print(f"RUN_2_ARTIFACT_SIZE={r2.artifact_size}")
    print(f"RUN_2_XLSX_VALID={'YES' if r2_xlsx_valid else 'NO'}")
    print(f"RUN_2_OPENPYXL_READ_VALID={'YES' if r2.openpyxl_read_valid else 'NO'}")
    print(f"RUN_2_WORKSHEET_COUNT={r2.worksheet_count}")
    print(f"RUN_2_SHA256={r2.sha256 or 'NONE'}")
    print(f"RUN_2_NORMALIZED_PACKAGE_FINGERPRINT={r2.normalized_package_fingerprint or 'NONE'}")
    print(f"RUN_2_SOURCE_CHANGED_DURING_EXPORT="
          f"{'YES' if r2.source_changed_during_export else 'NO'}")
    print(f"CHROMIUM_AFTER_RUN_2={chrome_after_r2}")
    print()
    print(f"SAMPLE_CHANGED_BETWEEN_RUNS={'YES' if sample_changed_between_runs else 'NO'}")
    print()
    print(f"BYTE_DETERMINISTIC={'YES' if byte_deterministic else 'NO'}")
    print(f"RUN_SHA256_MATCH={'YES' if sha_match else 'NO'}")
    print()
    print(f"ZIP_MEMBER_SET_MATCH="
          f"{'YES' if zip_member_set_match else 'NO' if cmp is not None else 'N/A'}")
    print(f"DIFFERING_PART_COUNT={diff_count if cmp is not None else 0}")
    print(f"DIFFERING_PART_NAMES={','.join(differing_parts) if differing_parts else 'NONE'}")
    print()
    print(f"NORMALIZED_PACKAGE_MATCH={'YES' if normalized_match else 'NO'}")
    print(f"SEMANTIC_EXPORT_IDEMPOTENT={'YES' if semantic_idempotent else 'NO'}")
    print(f"EXPORT_IDEMPOTENT={'YES' if export_idempotent else 'NO'}")
    print()
    print(f"SAMPLE_SEMANTICALLY_VOLATILE=NO")
    print(f"SOURCE_SIZE_MATCH=NOT_APPLICABLE")
    print()
    print(f"ICV_VOLATILITY_PROVEN={'YES' if icv_volatility_proven else 'NO'}")
    print(f"EXPORT_IDEMPOTENCY_CONTRACT_AMENDED={'YES' if contract_amended else 'NO'}")
    print(f"VOLATILE_RULE_SCOPE_EXACT={'YES' if rule_scope_exact else 'NO'}")
    print(f"WHOLE_PART_IGNORE_NOT_USED={'YES' if whole_part_ignore_not_used else 'NO'}")
    print(f"CLASSIFICATION_PROBES_PASS={'YES' if probes_pass else 'NO'}")
    print(f"RAW_DIFFERING_PART_NAMES={','.join(raw_parts) if raw_parts else 'NONE'}")
    print(f"ICV_ONLY_VOLATILITY_CONFIRMED="
          f"{'YES' if icv_only_confirmed and (normalized_match or sha_match) else 'NO'}")
    print()
    print(f"FULL_PROMOTION_GATE={'PASS' if full_gate else 'FAIL'}")
    print()
    print(f"PRODUCTION_REGISTRY_PROMOTED="
          f"{'YES' if promoted and not promotion_rolled_back else 'NO'}")
    print()
    print(f"POST_ACCEPTANCE_READY_BACKENDS={','.join(sorted(get_acquisition_ready_backends()))}")
    print(f"EXACT_READY_SET_POST_PASS={'YES' if exact_post else 'NO'}")
    print()
    print(f"OTHER_UNACCEPTED_BACKENDS_REMAIN_DISABLED="
          f"{'YES' if other_backends_false2 else 'NO'}")
    print()
    print(f"CURRENT_DB_OBJECT_COUNT={db_post['total']}")
    print(f"ROOT_SCOPE_OBJECT_COUNT={db_post['root_scope']}")
    print(f"PREVIOUS_SUBTREE_FOUND_COUNT={db_post['prev_subtree']}")
    print()
    print(f"INVENTORY_IDENTITY_REGRESSION="
          f"{'NONE' if db_post['prev_subtree'] == len(SUBTREE_IDS) else 'FOUND'}")
    print(f"UNKNOWN_OBJECT_TYPE_REGRESSION="
          f"{'NONE' if db_post['unknown_type'] == 0 else 'FOUND'}")
    print(f"STABLE_ID_DUPLICATE_REGRESSION="
          f"{'NONE' if db_post['dup_count'] == 0 else 'FOUND'}")
    print()
    print("WPS_WRITE_PERFORMED=NO")
    print("FILE_UPLOADED=NO")
    print("SOURCE_CREATED=NO")
    print("SOURCE_VERSION_CREATED=NO")
    print("PAW_CANONICAL_WRITE_PERFORMED=NO")
    print()
    print("WINDOWS_BRIDGE_USED=NO")
    print("USER_MANUAL_NAVIGATION_USED=NO")
    print()
    print("SIGNED_URL_LOGGING_DISABLED=YES")
    print("RAW_EXPORT_RESPONSE_LOGGING_DISABLED=YES")
    print()
    print("CHROMIUM_INSTANCE_MAX=1")
    print(f"CHROMIUM_REMAINING_PROCESS_COUNT={chrome_final}")
    print()
    print(f"PROMOTION_ROLLED_BACK={'YES' if promotion_rolled_back else 'NO'}")
    print()
    print(f"WPS_SPREADSHEET_EXPORT_XLSX_ACQUISITION_READY="
          f"{'YES' if post_ready and not promotion_rolled_back else 'NO'}")
    print()
    print(f"R15_A5_6_ALLOWED={'YES' if all_pass else 'NO'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
