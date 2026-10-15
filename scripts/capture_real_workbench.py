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

# 1. Complete onboarding so popup does not cover the workbench
httpx.post("http://127.0.0.1:8898/v1/local/onboarding", json={
    "provider": "deepseek",
    "cruise_mode": "realtime"
}, timeout=5.0)

out_dir = Path(r"d:\PAW\paw-beta\verification_logs\screenshots")
out_dir.mkdir(parents=True, exist_ok=True)
chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

routes = [
    ("today", "http://127.0.0.1:8898/app/today", out_dir / "real_workbench_1_today.png"),
    ("projects", "http://127.0.0.1:8898/app/projects", out_dir / "real_workbench_2_projects.png"),
    ("knowledge", "http://127.0.0.1:8898/app/knowledge", out_dir / "real_workbench_3_knowledge.png"),
    ("files", "http://127.0.0.1:8898/app/files", out_dir / "real_workbench_4_files.png"),
    ("review", "http://127.0.0.1:8898/app/review", out_dir / "real_workbench_5_review.png"),
    ("assistant", "http://127.0.0.1:8898/app/agy", out_dir / "real_workbench_6_assistant.png"),
]

for name, url, out_path in routes:
    cmd = [
        chrome,
        "--headless=new",
        f"--screenshot={out_path}",
        "--window-size=1440,920",
        "--virtual-time-budget=6000",
        url
    ]
    subprocess.run(cmd)
    size = out_path.stat().st_size if out_path.exists() else 0
    print(f"Captured {name}: {size:,} bytes -> {out_path.name}")

proc.terminate()
proc.wait(timeout=3)
shutil.rmtree(temp_dir, ignore_errors=True)
print("All workbench views captured successfully!")
