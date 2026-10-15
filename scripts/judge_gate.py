"""Self-Acceptance Gate for PAW Claude Judge Package (judge_gate.py).

Validates:
1. Code reference existence (file exists under 01_Latest_Source_Code, line number <= line count)
2. Code snippet consistency (```python ref=path:N-M verbatim matches source file lines)
3. Log snippet consistency (```log ref=path verbatim matches continuous lines in log file)
4. Hash consistency (every 64-char hex string in formal docs matches MANIFEST.sha256)
5. Cert thumbprint (every 40-char hex string matches actual signer thumbprint B2D6EB9F...)
6. Banned names (encrypt_api_key, win32crypt, etc. forbidden except in CHANGES_SINCE_JUDGE.md old column)
7. Banned phrases (准予, 满分, 100% 保真, 彻底根除 forbidden in formal docs)
8. Empty assertion check (scripts/verify_a22_a33.py has no unconditional is_passed = True, A28 has assert)
9. Source consistency (files in 01_Latest_Source_Code have same hash as ROOT)
10. Hygiene (0 __pycache__, 0 .pyc)
11. Log freshness (mtime of referenced logs >= latest source file mtime)
12. Product code zero diff against baseline 2cf6738
13. Desktop installer hash integrity
14. RTM test function existence (every function `X` in RTM exists as def X in referenced script)
"""
import os
import re
import sys
import hashlib
import subprocess
from pathlib import Path
from typing import List, Tuple, Dict, Set

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT_DIR = Path(__file__).resolve().parent.parent

BANNED_NAMES = [
    "encrypt_api_key",
    "DPAPIControlledException",
    "win32crypt",
    "backend/rag",
    "offline_search",
    "IsPortInUse",
    "NextButtonClick",
    "InitializeWizard",
    "MigrateFromPreviousVersion",
    "ws2_32",
    "WSAStartup",
]

BANNED_PHRASES = [
    "准予",
    "满分",
    "100% 保真",
    "彻底根除",
]

EXPECTED_THUMBPRINT = "B2D6EB9F292A758A7C45F993B838680BDCC26068"

def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().upper()

def get_formal_docs(pkg_dir: Path) -> List[Path]:
    docs = []
    for p in pkg_dir.rglob("*.md"):
        rel = p.relative_to(pkg_dir)
        parts = rel.parts
        if len(parts) == 1:
            docs.append(p)
        elif len(parts) > 1 and parts[0] in (
            "02_Runtime_Verification_Logs",
            "03_Decompilation_And_Binary_Fidelity",
            "04_Requirement_Traceability_Matrix",
        ):
            docs.append(p)
    return docs

def load_manifest(manifest_path: Path) -> Dict[str, str]:
    mapping = {}
    if not manifest_path.is_file():
        return mapping
    for line in manifest_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            h, fname = parts[0].upper(), parts[1].strip()
            mapping[h] = Path(fname).name
            mapping[Path(fname).name] = h
    return mapping

def run_gate(pkg_dir: Path) -> Tuple[bool, List[str]]:
    failures = []
    pkg_dir = pkg_dir.resolve()
    print(f"=== Running Judge Gate on: {pkg_dir} ===")

    src_dir = pkg_dir / "01_Latest_Source_Code"
    logs_dir = pkg_dir / "02_Runtime_Verification_Logs"
    manifest_file = pkg_dir / "MANIFEST.sha256"
    formal_docs = get_formal_docs(pkg_dir)
    manifest = load_manifest(manifest_file)

    # 10. Hygiene Check
    pycache_dirs = list(pkg_dir.rglob("__pycache__"))
    pyc_files = list(pkg_dir.rglob("*.pyc"))
    if pycache_dirs:
        failures.append(f"[HYGIENE] Found {len(pycache_dirs)} __pycache__ directories: {pycache_dirs[:3]}")
    if pyc_files:
        failures.append(f"[HYGIENE] Found {len(pyc_files)} .pyc files: {pyc_files[:3]}")

    # 8. Empty Assertion Check in verify_a22_a33.py
    v_script = src_dir / "scripts" / "verify_a22_a33.py"
    if not v_script.is_file():
        v_script = ROOT_DIR / "scripts" / "verify_a22_a33.py"

    if v_script.is_file():
        lines = v_script.read_text(encoding="utf-8", errors="ignore").splitlines()
        for i, l in enumerate(lines):
            # Check unconditional is_passed = True
            if re.match(r"^\s*is_passed\s*=\s*True\s*$", l):
                failures.append(f"[ASSERTION] verify_a22_a33.py:{i+1} has unconditional 'is_passed = True': {l.strip()}")
        # Check A28 has assert
        a28_code = False
        a28_assert = False
        a28b_code = False
        a28b_assert = False
        for l in lines:
            if "def test_a28_" in l:
                a28_code = True
            elif a28_code and l.startswith("def "):
                a28_code = False
            elif a28_code and "assert " in l:
                a28_assert = True

            if "def test_a28b_" in l:
                a28b_code = True
            elif a28b_code and l.startswith("def "):
                a28b_code = False
            elif a28b_code and "assert " in l:
                a28b_assert = True

        if not a28_assert:
            failures.append("[ASSERTION] test_a28_prompt_injection_defense body lacks an assert statement")
        if not a28b_assert:
            failures.append("[ASSERTION] test_a28b_compromised_model_injection missing or body lacks an assert statement")
    else:
        failures.append(f"[ASSERTION] Cannot find verify_a22_a33.py for inspection: {v_script}")

    # 9. Source Consistency Check (if src_dir exists and ROOT available)
    if src_dir.is_dir():
        for f in src_dir.rglob("*"):
            if f.is_file():
                rel = f.relative_to(src_dir)
                root_f = ROOT_DIR / rel
                if root_f.is_file():
                    h_pkg = sha256_file(f)
                    h_root = sha256_file(root_f)
                    if h_pkg != h_root:
                        failures.append(f"[SRC_CONSISTENCY] Hash mismatch for {rel}: pkg={h_pkg[:8]} root={h_root[:8]}")

    # 12. Product Code Unchanged Check (since baseline 2cf6738)
    try:
        res = subprocess.run(
            ["git", "diff", "2cf6738", "--", "backend", "frontend", "installer", "run.py", "paw_desktop.spec"],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            check=False
        )
        if res.returncode != 0:
            failures.append(f"[PRODUCT_CODE_DIFF] git diff returned non-zero code {res.returncode}: {res.stderr.strip()[:100]}")
        elif res.stdout.strip():
            failures.append(f"[PRODUCT_CODE_DIFF] Product code has been modified since baseline 2cf6738:\n{res.stdout.strip()[:300]}")
    except Exception as e:
        failures.append(f"[PRODUCT_CODE_DIFF] Unable to run git diff: {e}")

    # 13. Desktop Installer Unchanged Check
    EXPECTED_INSTALLER_HASH = "45D2CFEF237EDF3C9F03A82561F872937DDC25980CC3ADB09B8EC351546F5B10"
    desktop_installer = Path(os.path.expanduser("~")) / "Desktop" / "PAW_Personal_Work_OS_Setup_v2.3.2.exe"
    if not desktop_installer.is_file():
        desktop_installer = Path(r"C:\Users\sunao\Desktop\PAW_Personal_Work_OS_Setup_v2.3.2.exe")

    if not desktop_installer.is_file():
        failures.append(f"[INSTALLER_HASH] Desktop installer not found at {desktop_installer}")
    else:
        actual_installer_hash = sha256_file(desktop_installer)
        if actual_installer_hash != EXPECTED_INSTALLER_HASH:
            failures.append(f"[INSTALLER_HASH] Desktop installer hash mismatch: expected {EXPECTED_INSTALLER_HASH}, got {actual_installer_hash}")

    # Latest source mtime for freshness check (excluding meta packaging/gate tooling)
    latest_src_mtime = 0
    if src_dir.is_dir():
        for f in src_dir.rglob("*.py"):
            if f.name in ("build_judge_package.py", "judge_gate.py"):
                continue
            latest_src_mtime = max(latest_src_mtime, f.stat().st_mtime)

    referenced_logs: Set[str] = set()

    # Process all formal docs
    for doc in formal_docs:
        rel_doc = doc.relative_to(pkg_dir)
        is_changes_doc = (doc.name == "CHANGES_SINCE_JUDGE.md")
        is_rtm_doc = (doc.name == "REQUIREMENT_TRACEABILITY_MATRIX.md")
        content = doc.read_text(encoding="utf-8", errors="ignore")
        doc_lines = content.splitlines()

        # Check 7: Banned phrases
        for phrase in BANNED_PHRASES:
            for i, line in enumerate(doc_lines):
                if phrase in line:
                    failures.append(f"[BANNED_PHRASE] {rel_doc}:{i+1} contains forbidden phrase '{phrase}': {line.strip()[:60]}")

        # Check 6: Banned names
        for name in BANNED_NAMES:
            for i, line in enumerate(doc_lines):
                if name in line:
                    if is_changes_doc:
                        # In CHANGES_SINCE_JUDGE.md, allow in table rows documenting old errors
                        if line.strip().startswith("|"):
                            continue
                    failures.append(f"[BANNED_NAME] {rel_doc}:{i+1} contains forbidden name '{name}': {line.strip()[:60]}")

        # Check 5: Cert thumbprint (40-char hex string)
        for i, line in enumerate(doc_lines):
            # Match 40-char hex not part of a 64-char string
            for m in re.finditer(r"(?<![a-fA-F0-9])([a-fA-F0-9]{40})(?![a-fA-F0-9])", line):
                found_tp = m.group(1).upper()
                if found_tp != EXPECTED_THUMBPRINT:
                    failures.append(f"[THUMBPRINT] {rel_doc}:{i+1} contains unexpected 40-char thumbprint: {found_tp}")

        # Check 4: 64-char hex hash consistency
        if manifest:
            for i, line in enumerate(doc_lines):
                if is_changes_doc and ("旧值" in line or "旧" in line):
                    continue
                for m in re.finditer(r"(?<![a-fA-F0-9])([a-fA-F0-9]{64})(?![a-fA-F0-9])", line):
                    found_h = m.group(1).upper()
                    if found_h not in manifest:
                        failures.append(f"[HASH] {rel_doc}:{i+1} hash {found_h[:12]}... not in MANIFEST.sha256")
                    else:
                        expected_fn = manifest[found_h]
                        # Check if expected filename is mentioned on the same line
                        if expected_fn.lower() not in line.lower():
                            failures.append(f"[HASH_NAME] {rel_doc}:{i+1} hash belongs to {expected_fn}, but filename not on same line: {line.strip()[:60]}")

        # Check 1: Code references like `path/to/file.ext:N` or `path:N-M`
        for i, line in enumerate(doc_lines):
            # Find backtick references with line numbers
            for m in re.finditer(r"`([a-zA-Z0-9_\-\.\/\\]+\.(?:py|iss|spec|js|json|css|html|txt)):(\d+)(?:-(\d+))?`", line):
                raw_path = m.group(1).replace("\\", "/")
                start_l = int(m.group(2))
                end_l = int(m.group(3)) if m.group(3) else start_l

                if is_rtm_doc:
                    failures.append(f"[RTM_BARE_REF] {rel_doc}:{i+1} bare backtick code reference forbidden in RTM: {m.group(0)}")

                # Resolve file in src_dir
                target_f = src_dir / raw_path
                if not target_f.is_file():
                    # Try strip leading dirs like 01_Latest_Source_Code/
                    clean_path = re.sub(r"^01_Latest_Source_Code/", "", raw_path)
                    target_f = src_dir / clean_path

                if not target_f.is_file():
                    failures.append(f"[REF_EXISTENCE] {rel_doc}:{i+1} references non-existent file: {raw_path}")
                else:
                    file_line_count = len(target_f.read_text(encoding="utf-8", errors="ignore").splitlines())
                    if start_l > file_line_count or end_l > file_line_count:
                        failures.append(f"[REF_LINE] {rel_doc}:{i+1} line range {start_l}-{end_l} exceeds {raw_path} total lines ({file_line_count})")

        # RTM section check: each ### Req section must contain at least one ref code block with anchor or '未找到实现'
        if is_rtm_doc:
            req_sections = re.findall(r"(### Req [^\n]+)(.*?)(?=\n### Req |\Z)", content, re.DOTALL)
            if not req_sections:
                failures.append(f"[RTM_SECTION] {rel_doc}: No '### Req' sections found in RTM!")
            for req_title, req_body in req_sections:
                has_anchor_ref = bool(re.search(r"```\w+\s+ref=[^\s:]+:\d+-\d+\s+anchor=\S+", req_body))
                has_no_impl = "未找到实现" in req_body
                if not (has_anchor_ref or has_no_impl):
                    failures.append(f"[RTM_SECTION] {rel_doc}: section '{req_title.strip()}' must contain at least one ref code block with anchor or '未找到实现'")

            # RTM test function check: every function `X` mentioned in RTM must exist as def X in referenced script
            for i, line in enumerate(doc_lines):
                func_matches = list(re.finditer(r"函数\s+`([^`]+)`", line))
                if not func_matches:
                    continue
                script_m = re.search(r"`([a-zA-Z0-9_\-\.\/\\]+\.py)`", line)
                if not script_m:
                    failures.append(f"[RTM_TEST_FUNC] {rel_doc}:{i+1} mentions '函数 `...`' but no script is referenced: {line.strip()[:60]}")
                    continue
                raw_script_path = script_m.group(1).replace("\\", "/")
                clean_script_path = re.sub(r"^01_Latest_Source_Code/", "", raw_script_path)
                target_script = src_dir / clean_script_path
                if not target_script.is_file():
                    target_script = ROOT_DIR / clean_script_path

                if not target_script.is_file():
                    failures.append(f"[RTM_TEST_FUNC] {rel_doc}:{i+1} referenced script not found: {raw_script_path}")
                    continue

                script_content = target_script.read_text(encoding="utf-8", errors="ignore")
                for fm in func_matches:
                    fn = fm.group(1)
                    if not re.search(rf"\bdef\s+{re.escape(fn)}\b", script_content):
                        failures.append(f"[RTM_TEST_FUNC] {rel_doc}:{i+1} function '{fn}' not found as 'def {fn}' in {raw_script_path}")

        # Check 2 & 3: Code and Log snippet verbatim consistency
        # Find ```lang ref=path:N-M [anchor=...]
        pattern_code = re.compile(r"```(\w+)\s+ref=([a-zA-Z0-9_\-\.\/\\]+):(\d+)-(\d+)(?:\s+anchor=(\S+))?\n(.*?)```", re.DOTALL)
        for m in pattern_code.finditer(content):
            lang = m.group(1)
            ref_path = m.group(2).replace("\\", "/")
            start_l = int(m.group(3))
            end_l = int(m.group(4))
            anchor = m.group(5)
            snippet = m.group(6)

            # Snippet length check: exceeds 25 lines fails
            line_span = end_l - start_l + 1
            if line_span > 25:
                failures.append(f"[SNIPPET_LENGTH] {rel_doc} snippet {ref_path}:{start_l}-{end_l} exceeds 25 lines limit ({line_span} lines)")

            # Anchor check if specified
            if anchor:
                if anchor not in snippet:
                    failures.append(f"[SNIPPET_ANCHOR] {rel_doc} snippet {ref_path}:{start_l}-{end_l} anchor '{anchor}' not found in code snippet!")

            clean_path = re.sub(r"^01_Latest_Source_Code/", "", ref_path)
            target_f = src_dir / clean_path
            if not target_f.is_file():
                target_f = ROOT_DIR / clean_path

            if not target_f.is_file():
                failures.append(f"[SNIPPET_FILE] {rel_doc} references missing code file: {clean_path}")
                continue

            file_lines = target_f.read_text(encoding="utf-8", errors="ignore").splitlines()
            expected_slice = [l.rstrip() for l in file_lines[start_l - 1:end_l]]
            actual_slice = [l.rstrip() for l in snippet.splitlines()]
            # Strip leading/trailing empty lines from actual_slice
            while actual_slice and not actual_slice[0].strip(): actual_slice.pop(0)
            while actual_slice and not actual_slice[-1].strip(): actual_slice.pop()

            if expected_slice != actual_slice:
                failures.append(f"[CODE_SNIPPET] {rel_doc} snippet {ref_path}:{start_l}-{end_l} does not verbatim match source!\n"
                                f"  Expected ({len(expected_slice)} lines): {expected_slice[:2]}...\n"
                                f"  Actual   ({len(actual_slice)} lines): {actual_slice[:2]}...")

        # Find ```log ref=path
        pattern_log = re.compile(r"```log\s+ref=([a-zA-Z0-9_\-\.\/\\]+)\n(.*?)```", re.DOTALL)
        for m in pattern_log.finditer(content):
            ref_log = m.group(1).replace("\\", "/")
            snippet = m.group(2)
            clean_log_path = re.sub(r"^02_Runtime_Verification_Logs/", "", ref_log)
            target_log = logs_dir / clean_log_path
            if not target_log.is_file():
                target_log = ROOT_DIR / "verification_logs" / clean_log_path

            if not target_log.is_file():
                failures.append(f"[LOG_FILE] {rel_doc} references missing log file: {clean_log_path}")
                continue

            referenced_logs.add(str(target_log))
            log_content = target_log.read_text(encoding="utf-8", errors="ignore")
            # Verify continuous lines in log file
            clean_snippet = "\n".join(l.rstrip() for l in snippet.strip().splitlines())
            clean_log_content = "\n".join(l.rstrip() for l in log_content.splitlines())
            if clean_snippet not in clean_log_content:
                failures.append(f"[LOG_SNIPPET] {rel_doc} snippet for {clean_log_path} does not appear verbatim in log file!")

    # 11. Log Freshness Check
    for log_str in referenced_logs:
        lp = Path(log_str)
        if lp.is_file() and latest_src_mtime > 0:
            if lp.stat().st_mtime < (latest_src_mtime - 1.0):  # 1s tolerance
                failures.append(f"[LOG_STALE] Log {lp.name} mtime ({lp.stat().st_mtime}) is older than latest source mtime ({latest_src_mtime})")

    passed = len(failures) == 0
    return passed, failures

def main():
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT_DIR
    passed, failures = run_gate(target)
    out_log = ROOT_DIR / "gate_report.log"
    with open(out_log, "w", encoding="utf-8") as f:
        f.write(f"=== Judge Gate Verification Report ===\n")
        f.write(f"Target: {target}\n")
        f.write(f"Result: {'PASS' if passed else 'FAIL'}\n")
        f.write(f"Total failures: {len(failures)}\n\n")
        if failures:
            f.write("Failure details:\n")
            for fl in failures:
                f.write(f"  * {fl}\n")
        else:
            f.write("All gate verification checks passed successfully!\n")

    print(f"\nGate Report written to {out_log}")
    if passed:
        print("[GATE PASS] All checks passed successfully.")
        sys.exit(0)
    else:
        print(f"[GATE FAIL] {len(failures)} failures detected:")
        for fl in failures:
            print(f"  * {fl}")
        sys.exit(1)

if __name__ == "__main__":
    main()
