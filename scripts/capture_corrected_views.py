"""Capture corrected PAW Personal Work OS views:
1. Today (Main Workbench, matching User Image 1)
2. AI Assistant (Sidebar 'AI助手' and header 'AI助手')
3. Control Center Settings Modal (Clean Warm Mineral, No Emojis)
4. Control Center Doctor Diagnostic Modal (Calm, Quiet, No loud green PASS badges)
"""
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
import httpx

exe = r"d:\PAW\paw-beta\dist\PAW_Personal_Work_OS\PAW_Personal_Work_OS.exe"
temp_dir = tempfile.mkdtemp(prefix="paw_corr_ui_")
env = os.environ.copy()
env["PATH"] = r"C:\Windows\System32;C:\Windows"
env["PAW_DATA_DIR"] = temp_dir
env["PAW_PORT"] = "8911"

print("Starting PAW executable on port 8911...")
proc = subprocess.Popen([exe], env=env)
time.sleep(5)

try:
    # 1. Complete onboarding so it defaults to main workbench
    httpx.post("http://127.0.0.1:8911/v1/local/onboarding", json={
        "provider": "deepseek",
        "cruise_mode": "realtime"
    }, timeout=5.0)

    out_dir = Path(r"d:\PAW\paw-beta\verification_logs\screenshots")
    out_dir.mkdir(parents=True, exist_ok=True)
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

    # Screenshot 1: Today Page (Main Workbench)
    p_today = out_dir / "corrected_1_main_today.png"
    cmd_today = [
        chrome,
        "--headless=new",
        f"--screenshot={p_today}",
        "--window-size=1440,920",
        "--virtual-time-budget=6000",
        "http://127.0.0.1:8911/app/today"
    ]
    subprocess.run(cmd_today)
    print(f"Captured Today: {p_today.stat().st_size:,} bytes")

    # Screenshot 2: AI Assistant Page
    p_assistant = out_dir / "corrected_2_ai_assistant.png"
    cmd_assistant = [
        chrome,
        "--headless=new",
        f"--screenshot={p_assistant}",
        "--window-size=1440,920",
        "--virtual-time-budget=6000",
        "http://127.0.0.1:8911/app/agnes"
    ]
    subprocess.run(cmd_assistant)
    print(f"Captured AI Assistant: {p_assistant.stat().st_size:,} bytes")

    # Screenshot 3: Control Center (Settings tab open)
    p_settings = out_dir / "corrected_3_settings_modal.png"
    cmd_settings = [
        chrome,
        "--headless=new",
        f"--screenshot={p_settings}",
        "--window-size=1440,920",
        "--virtual-time-budget=6000",
        "http://127.0.0.1:8911/app/today?open_tab=settings"
    ]
    subprocess.run(cmd_settings)
    print(f"Captured Settings Modal: {p_settings.stat().st_size:,} bytes")

    # Screenshot 4: Control Center (Doctor Diagnostic tab open)
    p_doctor = out_dir / "corrected_4_doctor_modal.png"
    cmd_doctor = [
        chrome,
        "--headless=new",
        f"--screenshot={p_doctor}",
        "--window-size=1440,920",
        "--virtual-time-budget=6000",
        "http://127.0.0.1:8911/app/today?open_tab=doctor"
    ]
    subprocess.run(cmd_doctor)
    print(f"Captured Doctor Diagnostic Modal: {p_doctor.stat().st_size:,} bytes")

finally:
    proc.terminate()
    proc.wait(timeout=3)
    shutil.rmtree(temp_dir, ignore_errors=True)

print("All corrected views captured successfully!")
