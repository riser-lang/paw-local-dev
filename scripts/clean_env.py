"""PAW Personal Work OS - Environment Cleaning and Verification Module.

Enforces zero-leak, zero-residue test isolation across all jobs:
1. Uninstall existing PAW instances (silent uninstaller if present).
2. Clean all file locations:
   - %LOCALAPPDATA%\\PAW
   - %LOCALAPPDATA%\\Programs\\PAW*
   - %APPDATA%\\PAW*
   - %ProgramData%\\PAW*
   - %TEMP%\\PAW*
   - Directory referenced by data_dir_pointer.txt
   - Shortcuts (Desktop, Start Menu, Startup)
3. Clean registry:
   - HKCU\\Software\\PAW*
   - Uninstall keys {E6F7A9D2-...}_is1 in HKCU and HKLM
4. Clean Credential Manager items containing PAW.
5. Provide strict verification report: (Location -> Exists -> File Count).
6. Negative control check mode (verifies clean detector ability to catch residues).
"""
import os
import sys
import shutil
import subprocess
import winreg
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

# Ensure UTF-8 output
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def kill_paw_processes():
    """Terminates any running PAW desktop or reader worker processes."""
    procs = ["PAW_Personal_Work_OS.exe", "PAW.exe", "paw_desktop.exe"]
    for p in procs:
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", p, "/T"],
                capture_output=True,
                check=False
            )
        except Exception:
            pass


def get_installed_exe_path() -> Optional[Path]:
    """Finds the installed PAW executable in standard installation directories."""
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "PAW Personal Work OS" / "PAW_Personal_Work_OS.exe",
        Path(os.environ.get("ProgramFiles", "")) / "PAW Personal Work OS" / "PAW_Personal_Work_OS.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "PAW Personal Work OS" / "PAW_Personal_Work_OS.exe",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def get_check_locations() -> List[Tuple[str, Path, str]]:
    """Returns list of (Label, Path, Type) to check for residue."""
    local_app_data = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local"))
    app_data = Path(os.environ.get("APPDATA", r"C:\Users\Default\AppData\Roaming"))
    program_data = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
    temp_dir = Path(os.environ.get("TEMP", r"C:\Windows\Temp"))
    user_profile = Path(os.environ.get("USERPROFILE", r"C:\Users\Default"))
    public_dir = Path(os.environ.get("PUBLIC", r"C:\Users\Public"))

    locations = [
        ("%LOCALAPPDATA%\\PAW", local_app_data / "PAW", "dir"),
        ("%LOCALAPPDATA%\\Programs\\PAW Personal Work OS", local_app_data / "Programs" / "PAW Personal Work OS", "dir"),
        ("%APPDATA%\\PAW", app_data / "PAW", "dir"),
        ("%ProgramData%\\PAW", program_data / "PAW", "dir"),
        ("%TEMP%\\PAW*", temp_dir, "pattern_paw"),
        ("Desktop Shortcut (User)", user_profile / "Desktop" / "PAW Personal Work OS.lnk", "file"),
        ("Desktop Shortcut (Public)", public_dir / "Desktop" / "PAW Personal Work OS.lnk", "file"),
        ("Start Menu Shortcut (User)", app_data / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "PAW Personal Work OS", "dir"),
        ("Start Menu Shortcut (Common)", program_data / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "PAW Personal Work OS", "dir"),
        ("Startup Shortcut", app_data / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "PAW Personal Work OS.lnk", "file"),
    ]

    # External data pointer check
    pointer_file = app_data / "PAW" / "data_dir_pointer.txt"
    if pointer_file.exists():
        try:
            with open(pointer_file, "r", encoding="utf-8") as f:
                ext_dir = f.read().strip()
                if ext_dir:
                    locations.append(("External Data Dir (from pointer)", Path(ext_dir), "dir"))
        except Exception:
            pass

    return locations


def check_registry_key_exists(hive, subkey: str) -> bool:
    try:
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ):
            return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def delete_registry_key_recursive(hive, subkey: str):
    try:
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_ALL_ACCESS) as key:
            while True:
                try:
                    sub = winreg.EnumKey(key, 0)
                    delete_registry_key_recursive(hive, f"{subkey}\\{sub}")
                except OSError:
                    break
        winreg.DeleteKey(hive, subkey)
    except FileNotFoundError:
        pass
    except Exception:
        pass


def clean_environment():
    """Uninstalls and removes all PAW files, processes, shortcuts, and registry entries."""
    kill_paw_processes()

    # 1. Run silent uninstaller if present
    local_programs = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "PAW Personal Work OS"
    uninstaller = local_programs / "unins000.exe"
    if uninstaller.exists():
        try:
            subprocess.run(
                [str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                timeout=30,
                capture_output=True,
                check=False
            )
        except Exception:
            pass

    kill_paw_processes()

    # 2. Delete file locations
    for label, path, p_type in get_check_locations():
        try:
            if p_type == "pattern_paw" and path.exists():
                for item in path.glob("PAW*"):
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        item.unlink(missing_ok=True)
            elif p_type == "dir" and path.exists():
                shutil.rmtree(path, ignore_errors=True)
            elif p_type == "file" and path.exists():
                path.unlink(missing_ok=True)
        except Exception:
            pass

    # 3. Clean Registry
    delete_registry_key_recursive(winreg.HKEY_CURRENT_USER, r"Software\PAW")
    delete_registry_key_recursive(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{E6F7A9D2-C06E-44E2-81A4-0B0B4C8360D1}_is1")
    delete_registry_key_recursive(winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{E6F7A9D2-C06E-44E2-81A4-0B0B4C8360D1}_is1")

    # 4. Clean Credential Manager
    try:
        res = subprocess.run(["cmdkey", "/list"], capture_output=True, text=True, check=False)
        for line in res.stdout.splitlines():
            if "Target:" in line and "PAW" in line:
                target = line.split("Target:")[1].strip()
                subprocess.run(["cmdkey", f"/delete:{target}"], capture_output=True, check=False)
    except Exception:
        pass


def verify_environment_clean() -> Tuple[bool, List[Dict[str, Any]]]:
    """Inspects all target locations and reports residue status."""
    report = []
    is_completely_clean = True

    # 1. File locations
    for label, path, p_type in get_check_locations():
        exists = False
        count = 0
        if p_type == "pattern_paw":
            if path.exists():
                items = list(path.glob("PAW*"))
                exists = len(items) > 0
                count = len(items)
        elif p_type == "dir":
            if path.exists():
                exists = True
                count = sum(1 for _ in path.rglob("*"))
        elif p_type == "file":
            exists = path.exists()
            count = 1 if exists else 0

        status = "CLEAN" if not exists else "RESIDUE"
        if exists:
            is_completely_clean = False

        report.append({
            "target": label,
            "type": "File/Directory",
            "exists": exists,
            "count": count,
            "status": status,
        })

    # 2. Registry locations
    reg_keys = [
        ("HKCU\\Software\\PAW", winreg.HKEY_CURRENT_USER, r"Software\PAW"),
        ("HKCU\\Uninstall\\{E6F7A9D2-...}", winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{E6F7A9D2-C06E-44E2-81A4-0B0B4C8360D1}_is1"),
        ("HKLM\\Uninstall\\{E6F7A9D2-...}", winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{E6F7A9D2-C06E-44E2-81A4-0B0B4C8360D1}_is1"),
    ]
    for label, hive, subkey in reg_keys:
        exists = check_registry_key_exists(hive, subkey)
        status = "CLEAN" if not exists else "RESIDUE"
        if exists:
            is_completely_clean = False
        report.append({
            "target": label,
            "type": "Registry",
            "exists": exists,
            "count": 1 if exists else 0,
            "status": status,
        })

    # 3. Credential Manager
    cred_exists = False
    try:
        res = subprocess.run(["cmdkey", "/list"], capture_output=True, text=True, check=False)
        cred_exists = any("PAW" in line for line in res.stdout.splitlines() if "Target:" in line)
    except Exception:
        pass
    if cred_exists:
        is_completely_clean = False
    report.append({
        "target": "Windows Credential Manager (PAW entries)",
        "type": "Credential",
        "exists": cred_exists,
        "count": 1 if cred_exists else 0,
        "status": "CLEAN" if not cred_exists else "RESIDUE",
    })

    return is_completely_clean, report


def print_clean_report(report: List[Dict[str, Any]]):
    print("\n| 检查目标位置 | 类别 | 是否存在 | 残留数量 | 判定状态 |")
    print("|---|---|:---:|:---:|:---:|")
    for r in report:
        exists_str = "存在" if r["exists"] else "不存在"
        status_md = f"**{r['status']}**" if r['status'] == "RESIDUE" else r['status']
        print(f"| {r['target']} | {r['type']} | {exists_str} | {r['count']} | {status_md} |")


def run_negative_control_clean_check() -> bool:
    """Deliberately creates a dummy file in %APPDATA%\\PAW, verifies detector fails, then cleans up."""
    print("\n>>> [清空检查负对照测试] 开始验证清空检测器的灵敏度...")
    clean_environment()

    app_data = Path(os.environ.get("APPDATA", r"C:\Users\Default\AppData\Roaming")) / "PAW"
    app_data.mkdir(parents=True, exist_ok=True)
    probe_file = app_data / "negative_control_residue_probe.txt"
    probe_file.write_text("NEGATIVE_CONTROL_RESIDUE_PROBE", encoding="utf-8")

    is_clean, report = verify_environment_clean()
    probe_file.unlink(missing_ok=True)
    shutil.rmtree(app_data, ignore_errors=True)

    if not is_clean:
        print("[PASS] 清空检查负对照成功：检测器准确捕获到了人为注入的残留文件，并报告了 RESIDUE！")
        return True
    else:
        print("[FAIL] 清空检查负对照失败：检测器未能发现人为注入的残留文件！")
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="PAW Clean Environment Utility")
    parser.add_argument("--clean", action="store_true", help="Execute full uninstallation and cleaning")
    parser.add_argument("--verify", action="store_true", help="Verify environment is completely clean")
    parser.add_argument("--negative-control", action="store_true", help="Run negative control test for clean verification")
    args = parser.parse_args()

    if args.clean:
        print("Executing full environment clean...")
        clean_environment()
        print("Clean operation completed.")

    if args.negative_control:
        success = run_negative_control_clean_check()
        sys.exit(0 if success else 1)

    if args.verify or (not args.clean and not args.negative_control):
        is_clean, report = verify_environment_clean()
        print_clean_report(report)
        if is_clean:
            print("\n>> VERDICT: CLEAN [所有位置均已确认为空]")
            sys.exit(0)
        else:
            print("\n>> VERDICT: RESIDUE DETECTED [存在残留，清空未完成]")
            sys.exit(1)


if __name__ == "__main__":
    main()
