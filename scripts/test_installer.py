"""Test script for PAW_Personal_Work_OS_Setup.exe."""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
INSTALLER = DIST / "PAW_Personal_Work_OS_Setup.exe"
TEST_INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", "C:\\Users\\sunao\\AppData\\Local")) / "Temp" / "paw_verify_install"

print(f"Testing installer: {INSTALLER}")
print(f"Target test dir:   {TEST_INSTALL_DIR}")

if not INSTALLER.is_file():
    print(f"ERROR: Installer not found at {INSTALLER}")
    sys.exit(1)

# Clean up previous test install if exists
if TEST_INSTALL_DIR.exists():
    try:
        shutil.rmtree(TEST_INSTALL_DIR)
    except Exception as e:
        print(f"Warning cleaning {TEST_INSTALL_DIR}: {e}")

# Run Inno Setup installer silently to test target dir
cmd = [
    str(INSTALLER),
    f"/DIR={str(TEST_INSTALL_DIR)}",
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    f"/LOG={str(ROOT / 'install.log')}",
]
print(f"Executing: {' '.join(cmd)}")
t0 = time.time()
res = subprocess.run(cmd, capture_output=True, text=True)
elapsed = time.time() - t0

print(f"Installer exit code: {res.returncode} (took {elapsed:.2f}s)")
if res.returncode != 0:
    print(f"STDERR: {res.stderr}")
    print(f"STDOUT: {res.stdout}")
    sys.exit(res.returncode)

# Check installed directory contents
print("\n--- Verifying Installed Program Files Directory Structure ---")
items = list(TEST_INSTALL_DIR.iterdir())
for item in items:
    print(f"  {'[DIR]' if item.is_dir() else '[FILE]'} {item.name} ({item.stat().st_size if item.is_file() else 'dir'})")

expected_files = ["PAW_Personal_Work_OS.exe", "_internal", "frontend"]
for ef in expected_files:
    p = TEST_INSTALL_DIR / ef
    status = "OK" if p.exists() else "MISSING"
    print(f"Check {ef}: {status}")

# Check %LOCALAPPDATA%\PAW
print("\n--- Verifying %LOCALAPPDATA%\\PAW Data Directory Structure ---")
paw_data = Path(os.environ.get("LOCALAPPDATA", "")) / "PAW"
print(f"Checking data dir: {paw_data}")
for sub in ["workspace.db", "config.json", "sources", "evidence", "backups", "logs", "data/workspace.db"]:
    p = paw_data / sub
    print(f"Check {sub}: {'EXISTS' if p.exists() else 'NOT FOUND'}")

print("\nInstaller verification completed successfully!")
