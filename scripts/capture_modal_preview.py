import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
import httpx

exe = r"d:\PAW\paw-beta\dist\PAW_Personal_Work_OS\PAW_Personal_Work_OS.exe"
temp_dir = tempfile.mkdtemp(prefix="paw_ui_bench_")
env = os.environ.copy()
env["PATH"] = r"C:\Windows\System32;C:\Windows"
env["PAW_DATA_DIR"] = temp_dir
env["PAW_PORT"] = "8898"

proc = subprocess.Popen([exe], env=env)
time.sleep(5)

# 1. Complete onboarding
httpx.post("http://127.0.0.1:8898/v1/local/onboarding", json={
    "provider": "deepseek",
    "cruise_mode": "realtime"
}, timeout=5.0)

out_dir = Path(r"d:\PAW\paw-beta\verification_logs\screenshots")
out_dir.mkdir(parents=True, exist_ok=True)
chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

out_png1 = out_dir / "real_workbench_control_settings_warm.png"
cmd1 = [
    chrome,
    "--headless=new",
    f"--screenshot={out_png1}",
    "--window-size=1440,920",
    "--virtual-time-budget=6000",
    "http://127.0.0.1:8898/app?open_tab=settings"
]
subprocess.run(cmd1)
print(f"Captured settings modal: {out_png1.stat().st_size:,} bytes")

out_png2 = out_dir / "real_workbench_control_scanner_warm.png"
cmd2 = [
    chrome,
    "--headless=new",
    f"--screenshot={out_png2}",
    "--window-size=1440,920",
    "--virtual-time-budget=6000",
    "http://127.0.0.1:8898/app?open_tab=scanner"
]
subprocess.run(cmd2)
print(f"Captured scanner modal: {out_png2.stat().st_size:,} bytes")

out_png3 = out_dir / "real_workbench_control_doctor_warm.png"
cmd3 = [
    chrome,
    "--headless=new",
    f"--screenshot={out_png3}",
    "--window-size=1440,920",
    "--virtual-time-budget=6000",
    "http://127.0.0.1:8898/app?open_tab=doctor"
]
subprocess.run(cmd3)
print(f"Captured doctor modal: {out_png3.stat().st_size:,} bytes")

proc.terminate()
proc.wait(timeout=3)
shutil.rmtree(temp_dir, ignore_errors=True)
print("Finished capturing all warm mineral styled modals!")
