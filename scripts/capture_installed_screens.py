import os, subprocess
from pathlib import Path

chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
out_dir = Path(r"d:\PAW\paw-beta\verification_logs\screenshots")
out_dir.mkdir(parents=True, exist_ok=True)

targets = [
    ("installed_1_today.png", "http://127.0.0.1:8800/app/today"),
    ("installed_2_projects.png", "http://127.0.0.1:8800/app/projects"),
    ("installed_3_batch_review.png", "http://127.0.0.1:8800/app?open_tab=batch_review"),
    ("installed_4_cruise_status.png", "http://127.0.0.1:8800/app?open_tab=status"),
    ("installed_5_evolution.png", "http://127.0.0.1:8800/app?open_tab=evolution"),
]

for fname, url in targets:
    out_path = out_dir / fname
    cmd = [
        chrome,
        "--headless=new",
        f"--screenshot={out_path}",
        "--window-size=1440,920",
        "--virtual-time-budget=6000",
        url
    ]
    subprocess.run(cmd)
    print(f"Captured {fname}: {out_path.stat().st_size:,} bytes")
