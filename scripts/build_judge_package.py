# -*- coding: utf-8 -*-
"""Deterministic Package Builder for PAW Claude Judge Package (build_judge_package.py).

Assembles the full judge package, computes MANIFEST.sha256, runs judge_gate.py,
and compresses to .zip.
"""
import os
import sys
import shutil
import hashlib
import subprocess
from pathlib import Path

ROOT_DIR = Path(r"D:\PAW\paw-beta")
DESKTOP_DIR = Path(r"C:\Users\sunao\Desktop")
OLD_PKG_DIR = DESKTOP_DIR / "PAW_Claude_Judge_Package_v2.3.1"
SEVEN_ZIP = Path(r"D:\软件\Huorong\AppStore\bin\7z.exe")

def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().upper()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def copy_source_tree(src: Path, dst: Path):
    """Copy source code excluding artifacts, git, binaries, and temps."""
    top_exclude_dirs = {
        ".git", "data", "dist", "build", "logs",
        "fix_run", "test_env", "out_bad", "out_v1", "test_corrupt_out", "scratch"
    }
    exclude_exts = {".pyc", ".db", ".db-wal", ".db-shm", ".exe", ".zip", ".log"}
    
    for root, dirs, files in os.walk(src):
        rel_root = Path(root).relative_to(src)
        if rel_root == Path("."):
            dirs[:] = [d for d in dirs if d not in top_exclude_dirs and not d.startswith("out_") and not d.startswith("test_") and d != "__pycache__"]
        else:
            dirs[:] = [d for d in dirs if d != "__pycache__"]
        
        target_dir = dst / rel_root
        target_dir.mkdir(parents=True, exist_ok=True)
        
        for f in files:
            p = Path(root) / f
            if p.suffix.lower() in exclude_exts:
                continue
            if p.suffix.lower() == ".png" and "screenshots" not in str(rel_root).lower():
                continue
            target_f = target_dir / f
            shutil.copy2(p, target_f)

def build_package(version: str = "v2.3.2_r2"):
    pkg_name = f"PAW_Claude_Judge_Package_{version}"
    target_dir = DESKTOP_DIR / pkg_name
    target_zip = DESKTOP_DIR / f"{pkg_name}.zip"

    print(f"=== Building Judge Package: {pkg_name} ===")
    print(f"Target Directory: {target_dir}")
    print(f"Target Zip:       {target_zip}")

    if target_dir.exists():
        print(f"Cleaning existing directory: {target_dir}")
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    # 1. Root formal documents
    print("Copying root documents...")
    shutil.copy2(ROOT_DIR / "00_EXECUTIVE_AUDIT_REPORT_FOR_CLAUDE.md", target_dir / "00_EXECUTIVE_AUDIT_REPORT_FOR_CLAUDE.md")
    shutil.copy2(ROOT_DIR / "CHANGES_SINCE_JUDGE.md", target_dir / "CHANGES_SINCE_JUDGE.md")
    shutil.copy2(ROOT_DIR / "README.md", target_dir / "README.md")
    shutil.copy2(ROOT_DIR / "scripts" / "judge_gate.py", target_dir / "judge_gate.py")

    # Git log and diff
    print("Generating git log and diff...")
    git_log_out = subprocess.check_output(["git", "log", "--oneline", "-n", "20"], cwd=ROOT_DIR, encoding="utf-8", errors="replace")
    (target_dir / "git_log.txt").write_text(git_log_out, encoding="utf-8")
    git_diff_out = subprocess.check_output(["git", "diff", "2cf6738..HEAD"], cwd=ROOT_DIR, encoding="utf-8", errors="replace")
    (target_dir / "git_diff_baseline.diff").write_text(git_diff_out, encoding="utf-8")

    # 2. 01_Latest_Source_Code
    print("Populating 01_Latest_Source_Code...")
    src_dir = target_dir / "01_Latest_Source_Code"
    src_dir.mkdir(parents=True, exist_ok=True)
    copy_source_tree(ROOT_DIR, src_dir)

    # 3. 02_Runtime_Verification_Logs
    print("Populating 02_Runtime_Verification_Logs...")
    logs_dir = target_dir / "02_Runtime_Verification_Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    fresh_logs = [
        "a22_a33_verification.log",
        "audit_check_verification.log",
        "doctor_diagnostic.log",
        "gateway_smoke_tests.log",
        "signatures_verification.log",
        "bytecode_strings.log",
    ]
    for flog in fresh_logs:
        src_log = ROOT_DIR / "verification_logs" / flog
        if src_log.is_file():
            shutil.copy2(src_log, logs_dir / flog)

    # Observation & negative control logs
    fix_run_logs = [
        "a25_observation.log",
        "a25_negative_control.log",
        "a28_negative_control.log",
        "a28b_negative_control.log",
        "db_isolation_negative_control.log",
        "dev_db_guard.log",
        "gate_negative_control.log",
    ]
    for flog in fix_run_logs:
        src_log = ROOT_DIR / "fix_run" / flog
        if src_log.is_file():
            shutil.copy2(src_log, logs_dir / flog)

    # Screenshots
    src_screens = OLD_PKG_DIR / "02_Runtime_Verification_Logs" / "screenshots"
    dst_screens = logs_dir / "screenshots"
    if src_screens.is_dir():
        shutil.copytree(src_screens, dst_screens, dirs_exist_ok=True)

    # 4. 03_Decompilation_And_Binary_Fidelity
    print("Populating 03_Decompilation_And_Binary_Fidelity...")
    bin_dir = target_dir / "03_Decompilation_And_Binary_Fidelity"
    bin_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(ROOT_DIR / "docs" / "DECOMPILATION_FIDELITY_REPORT.md", bin_dir / "DECOMPILATION_FIDELITY_REPORT.md")
    shutil.copy2(ROOT_DIR / "docs" / "SIGNATURE_VERIFICATION_REPORT.md", bin_dir / "SIGNATURE_VERIFICATION_REPORT.md")

    # CompiledCode.bin
    src_compiled = ROOT_DIR / "scratch" / "test_innounp_v232" / "embedded" / "CompiledCode.bin"
    if not src_compiled.is_file():
        src_compiled = ROOT_DIR / "scratch" / "test_innounp" / "embedded" / "CompiledCode.bin"
    if not src_compiled.is_file():
        src_compiled = OLD_PKG_DIR / "03_Decompilation_And_Binary_Fidelity" / "CompiledCode.bin"
    shutil.copy2(src_compiled, bin_dir / "CompiledCode.bin")

    # sunao13_防伪公钥证书.cer
    src_cer = DESKTOP_DIR / "sunao13_防伪公钥证书.cer"
    shutil.copy2(src_cer, bin_dir / "sunao13_防伪公钥证书.cer")

    # 5. 04_Requirement_Traceability_Matrix
    print("Populating 04_Requirement_Traceability_Matrix...")
    rtm_dir = target_dir / "04_Requirement_Traceability_Matrix"
    rtm_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(ROOT_DIR / "docs" / "REQUIREMENT_TRACEABILITY_MATRIX.md", rtm_dir / "REQUIREMENT_TRACEABILITY_MATRIX.md")
    shutil.copy2(ROOT_DIR / "docs" / "交给委托人的手动测试清单.md", rtm_dir / "交给委托人的手动测试清单.md")
    if (ROOT_DIR / "fix_run" / "manual_checklist_strings.md").is_file():
        shutil.copy2(ROOT_DIR / "fix_run" / "manual_checklist_strings.md", rtm_dir / "manual_checklist_strings.md")

    # 6. 05_Historical_Unverified
    print("Populating 05_Historical_Unverified...")
    hist_dir = target_dir / "05_Historical_Unverified"
    hist_dir.mkdir(parents=True, exist_ok=True)

    hist_docs = [
        "PAW_本地版_第一次发布验收整改报告_R1.md",
        "PAW_本地版_自带联网搜索_验收报告_R2.md",
        "PAW_Personal_Work_OS_Architecture_And_User_Guide.md",
        "PAW_Personal_Work_OS_Final_Acceptance_Report_v2.3.md",
        "PAW_Windows_Release_Acceptance_Report.md",
    ]
    for hdoc in hist_docs:
        src_h = OLD_PKG_DIR / "04_Requirement_Traceability_Matrix" / hdoc
        if not src_h.is_file():
            src_h = ROOT_DIR / hdoc
        if src_h.is_file():
            shutil.copy2(src_h, hist_dir / hdoc)

    hist_logs = [
        "g1_final_package_audit.log",
        "g2_g3_desktop_lifecycle.log",
        "g4_h8_update_and_migration.log",
        "g5_screenshots.log",
        "g6_1000_file_scale.log",
        "h1_h3_coldstart_and_governance.log",
        "h2_g8_web_search_20_queries.log",
        "DEAD_CODE_AUDIT_ANALYSIS.md",
    ]
    for hlog in hist_logs:
        src_l = OLD_PKG_DIR / "02_Runtime_Verification_Logs" / hlog
        if src_l.is_file():
            shutil.copy2(src_l, hist_dir / hlog)

    hist_readme = """# 历史未复核材料归档说明 (Historical Unverified Materials)

本目录所含文档与测试日志均为历史版本（v2.3.1 之前）的遗留材料。
本轮整改遵循「诚实规则」，未对上述材料进行代码级复核与时间戳重测。
因此，本目录下的所有文件**仅作历史演进备查，严禁作为本轮验收依据**。
"""
    (hist_dir / "README.md").write_text(hist_readme, encoding="utf-8")

    # 7. Generate MANIFEST.sha256 (first pass before gate)
    print("Generating MANIFEST.sha256...")
    generate_manifest(target_dir, version)

    # 8. Run judge_gate.py
    print("Running judge_gate.py on package...")
    sys.path.insert(0, str(ROOT_DIR / "scripts"))
    import judge_gate
    passed, failures = judge_gate.run_gate(target_dir)

    gate_log_path = target_dir / "gate_report.log"
    with open(gate_log_path, "w", encoding="utf-8") as f:
        f.write("=== Judge Gate Verification Report ===\n")
        f.write(f"Target: {target_dir}\n")
        f.write(f"Result: {'PASS' if passed else 'FAIL'}\n")
        f.write(f"Total failures: {len(failures)}\n\n")
        if failures:
            f.write("Failure details:\n")
            for fl in failures:
                f.write(f"  * {fl}\n")
        else:
            f.write("All gate verification checks passed successfully!\n")

    # Update manifest to include gate_report.log
    generate_manifest(target_dir, version)

    if not passed:
        print(f"[ERROR] Judge Gate FAIL with {len(failures)} issues!")
        for fl in failures:
            print(f"  * {fl}")
        sys.exit(1)
    else:
        print("[SUCCESS] Judge Gate PASSED cleanly!")

    # 9. Create Zip
    print(f"Creating zip archive via 7-Zip: {target_zip}")
    if target_zip.exists():
        target_zip.unlink()

    cmd = [
        str(SEVEN_ZIP),
        "a",
        "-tzip",
        "-mx=9",
        str(target_zip),
        f"{target_dir}\\*"
    ]
    subprocess.check_call(cmd)
    print(f"Zip created successfully! Size: {target_zip.stat().st_size:,} bytes")
    print(f"Package build complete: {target_dir}")

def generate_manifest(pkg_dir: Path, version: str):
    manifest_lines = [
        "# PAW Personal Work OS Release Package Integrity Manifest (MANIFEST.sha256)",
        f"# Target Version: {version}",
        "# Generated automatically by build_judge_package.py",
        "",
        "# [1. External Standalone Installers & Root Certificates]",
    ]

    # External desktop files
    inst_name = "PAW_Personal_Work_OS_Setup_v2.3.1.exe" if "2.3.1" in version else "PAW_Personal_Work_OS_Setup_v2.3.2.exe"
    desktop_exe = DESKTOP_DIR / inst_name
    if desktop_exe.is_file():
        manifest_lines.append(f"{sha256_file(desktop_exe)}  {inst_name}")

    desktop_cer = DESKTOP_DIR / "sunao13_防伪公钥证书.cer"
    if desktop_cer.is_file():
        manifest_lines.append(f"{sha256_file(desktop_cer)}  sunao13_防伪公钥证书.cer")

    manifest_lines.append("")
    manifest_lines.append("# [2. Package Internal Files]")

    pkg_files = []
    for p in pkg_dir.rglob("*"):
        if p.is_file() and p.name != "MANIFEST.sha256":
            rel = p.relative_to(pkg_dir).as_posix()
            pkg_files.append((rel, p))

    pkg_files.sort(key=lambda x: x[0])
    for rel, p in pkg_files:
        h = sha256_file(p)
        manifest_lines.append(f"{h}  {rel}")

    (pkg_dir / "MANIFEST.sha256").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")

if __name__ == "__main__":
    ver = sys.argv[1] if len(sys.argv) > 1 else "v2.3.2_r2"
    build_package(ver)
