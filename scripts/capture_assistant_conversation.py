"""Test AI Assistant end-to-end and capture screenshot of conversation UI."""
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
import httpx

exe = r"d:\PAW\paw-beta\dist\PAW_Personal_Work_OS\PAW_Personal_Work_OS.exe"
temp_dir = tempfile.mkdtemp(prefix="paw_chat_ui_")
env = os.environ.copy()
env["PATH"] = r"C:\Windows\System32;C:\Windows"
env["PAW_DATA_DIR"] = temp_dir
env["PAW_PORT"] = "8912"

print("Starting PAW on port 8912...")
proc = subprocess.Popen([exe], env=env)
time.sleep(5)

try:
    # 1. Onboarding
    httpx.post("http://127.0.0.1:8912/v1/local/onboarding", json={
        "provider": "deepseek",
        "cruise_mode": "realtime"
    }, timeout=5.0)

    # 2. Create chat session
    chat_init = httpx.post("http://127.0.0.1:8912/v1/web/agy/chat", json={"mode": "chat"}, timeout=5.0).json()
    session_id = chat_init["session_id"]
    print("Created chat session:", session_id)

    # 3. Send test message 1: "你好，请用一句话介绍你自己"
    t0 = time.time()
    res1 = httpx.post(
        f"http://127.0.0.1:8912/v1/web/agy/chat/{session_id}/messages",
        json={"message": "你好，请用一句话介绍你自己"},
        timeout=10.0
    ).json()
    t1 = time.time()
    print(f"Message 1 response ({t1-t0:.2f}s):", repr(res1.get("response")[:120]))

    # 4. Send test message 2: "帮我总结一下今天页面上的待办情况"
    t0 = time.time()
    res2 = httpx.post(
        f"http://127.0.0.1:8912/v1/web/agy/chat/{session_id}/messages",
        json={"message": "帮我总结一下今天页面上的待办情况"},
        timeout=10.0
    ).json()
    t1 = time.time()
    print(f"Message 2 response ({t1-t0:.2f}s):", repr(res2.get("response")[:120]))

    # 5. Capture browser screenshot of the live conversation on /app/agnes
    out_dir = Path(r"d:\PAW\paw-beta\verification_logs\screenshots")
    out_dir.mkdir(parents=True, exist_ok=True)
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    p_chat = out_dir / "corrected_5_ai_assistant_live_chat.png"

    cmd = [
        chrome,
        "--headless=new",
        f"--screenshot={p_chat}",
        "--window-size=1440,920",
        "--virtual-time-budget=8000",
        "http://127.0.0.1:8912/app/agnes"
    ]
    subprocess.run(cmd)
    print(f"Captured live conversation UI: {p_chat.stat().st_size:,} bytes")

finally:
    proc.terminate()
    proc.wait(timeout=3)
    shutil.rmtree(temp_dir, ignore_errors=True)

print("AI assistant conversation test finished successfully!")
