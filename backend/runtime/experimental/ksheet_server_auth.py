"""Start a temporary localhost-only WPS browser bootstrap session.

Access is intentionally limited to an SSH port-forward. The helper never reads
or persists browser storage, cookies, tokens, or page contents.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import time
from pathlib import Path

from ksheet_pipeline_common import atomic_json, utc_now

ROOT = Path(os.environ.get("PAW_KSHEET_STATE_ROOT", "/srv/paw/ksheet_exports"))
PROFILE = Path(os.environ.get("PAW_KSHEET_PROFILE", "/var/lib/paw-ksheet-worker/chromium-profile"))
PID_FILE = ROOT / "auth-bootstrap.pids"
STATUS = ROOT / "auth-bootstrap-status.json"
DISPLAY = ":99"


def _spawn(command: list[str], env: dict[str, str] | None = None) -> subprocess.Popen:
    return subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def start() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    if PID_FILE.exists():
        raise SystemExit("bootstrap_already_running")
    env = os.environ.copy()
    env["DISPLAY"] = DISPLAY
    browser = "/var/lib/paw-ksheet-worker/ms-playwright/chromium-1208/chrome-linux64/chrome"
    processes = [
        _spawn(["/usr/bin/Xvfb", DISPLAY, "-screen", "0", "1440x900x24", "-nolisten", "tcp"], env),
        _spawn(["/usr/bin/openbox"], env),
    ]
    time.sleep(1)
    processes.extend([
        _spawn(["/usr/bin/x11vnc", "-display", DISPLAY, "-localhost", "-rfbport", "5901", "-nopw", "-forever", "-shared"], env),
        _spawn(["/usr/bin/websockify", "--web=/usr/share/novnc", "127.0.0.1:6080", "127.0.0.1:5901"]),
        _spawn([browser, "--display=" + DISPLAY, "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--new-window", "--window-position=0,0", "--window-size=1440,900", "--user-data-dir=" + str(PROFILE), "https://drive.wps.cn/"], env),
    ])
    PID_FILE.write_text("\n".join(str(process.pid) for process in processes), encoding="ascii")
    atomic_json(STATUS, {"status": "RUNNING", "started_at": utc_now(), "access": "localhost_only_via_ssh", "port": 6080})
    print("RUNNING")


def stop() -> None:
    if PID_FILE.exists():
        for value in PID_FILE.read_text(encoding="ascii").splitlines():
            try:
                os.kill(int(value), signal.SIGTERM)
            except (ValueError, ProcessLookupError):
                pass
        PID_FILE.unlink(missing_ok=True)
    atomic_json(STATUS, {"status": "STOPPED", "stopped_at": utc_now()})
    print("STOPPED")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("start", "stop", "status"))
    args = parser.parse_args()
    if args.command == "start":
        start()
    elif args.command == "stop":
        stop()
    else:
        print(STATUS.read_text(encoding="utf-8") if STATUS.exists() else '{"status":"STOPPED"}')


if __name__ == "__main__":
    main()
