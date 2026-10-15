"""Test AI Assistant with active Provider and capture screenshot of model response."""
import os
import shutil
import subprocess
import tempfile
import time
import threading
from pathlib import Path
from http.server import HTTPServer
import httpx

import sys
sys.path.insert(0, r"d:\PAW\paw-beta")
from scripts.mock_openai_server import MockOpenAIHandler

# 1. Start mock server on 8999
server = HTTPServer(("127.0.0.1", 8999), MockOpenAIHandler)
th = threading.Thread(target=server.serve_forever, daemon=True)
th.start()
print("Mock OpenAI server running on 8999...")

exe = r"d:\PAW\paw-beta\dist\PAW_Personal_Work_OS\PAW_Personal_Work_OS.exe"
temp_dir = tempfile.mkdtemp(prefix="paw_chat_llm_")
env = os.environ.copy()
env["PATH"] = r"C:\Windows\System32;C:\Windows"
env["PAW_DATA_DIR"] = temp_dir
env["PAW_PORT"] = "8913"

print("Starting PAW on port 8913...")
proc = subprocess.Popen([exe], env=env)
time.sleep(5)

try:
    # 2. Configure provider to use mock OpenAI
    httpx.post("http://127.0.0.1:8913/v1/local/settings", json={
        "provider": "custom",
        "custom_api_key": "sk-paw-editorial-test-key",
        "custom_base_url": "http://127.0.0.1:8999",
        "custom_model": "gpt-4o",
        "cruise_mode": "realtime"
    }, timeout=5.0)

    # 3. Create chat session
    chat_init = httpx.post("http://127.0.0.1:8913/v1/web/agy/chat", json={"mode": "chat"}, timeout=5.0).json()
    session_id = chat_init["session_id"]
    print("Created chat session with active provider:", session_id)

    # 4. Send greeting message
    res = httpx.post(
        f"http://127.0.0.1:8913/v1/web/agy/chat/{session_id}/messages",
        json={"message": "你好，请用一句话介绍你自己"},
        timeout=10.0
    ).json()
    print("Provider response:", repr(res.get("response")[:120]))

    # 5. Capture screenshot
    out_dir = Path(r"d:\PAW\paw-beta\verification_logs\screenshots")
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    p_chat = out_dir / "corrected_6_ai_assistant_provider_active.png"

    cmd = [
        chrome,
        "--headless=new",
        f"--screenshot={p_chat}",
        "--window-size=1440,920",
        "--virtual-time-budget=8000",
        "http://127.0.0.1:8913/app/agnes"
    ]
    subprocess.run(cmd)
    print(f"Captured active provider chat UI: {p_chat.stat().st_size:,} bytes")

finally:
    proc.terminate()
    proc.wait(timeout=3)
    server.shutdown()
    shutil.rmtree(temp_dir, ignore_errors=True)

print("Active provider test finished successfully!")
