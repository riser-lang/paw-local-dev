#!/usr/bin/env python3
"""
R15-A.5.5H — Single-RUN export worker (subprocess-isolated).

Executes exactly ONE production export session in its own process so a hung
browser protocol can never wedge the acceptance runner. The runner enforces a
hard wall-clock timeout on this process and treats expiry as RUN FAILED.

Prints exactly ONE JSON line (the sanitized result) to stdout; all diagnostics
go to stderr. No credentials or raw payloads are recorded.
"""
import sys
import json
import time
import subprocess
from dataclasses import asdict

DEV_DIR = os.path.dirname(os.path.abspath(__file__))
if DEV_DIR not in sys.path:
    sys.path.insert(0, DEV_DIR)
if "/opt/paw-ksheet-worker" not in sys.path:
    sys.path.append("/opt/paw-ksheet-worker")

PROFILE_DIR = "/var/lib/paw-ksheet-worker/chromium-profile"
BROWSER_EXECUTABLE = "/var/lib/paw-ksheet-worker/ms-playwright/chromium-1208/chrome-linux64/chrome"
BACKEND = "WPS_SPREADSHEET_EXPORT_XLSX"


def log(msg):
    print(f"[A55H-WORKER] {msg}", file=sys.stderr, flush=True)


def main():
    run_label = sys.argv[1]
    baseline_json = sys.argv[2]
    sample = json.loads(sys.argv[3])

    from playwright.sync_api import sync_playwright
    from wps_native_acquisition import set_acceptance_override, clear_acceptance_overrides
    from wps_export_acquisition import run_acceptance_acquisition_export

    baseline_identity = None
    if baseline_json and baseline_json != "-":
        baseline_identity = json.loads(baseline_json)

    result = None
    ident = {}
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            PROFILE_DIR, headless=False, accept_downloads=True,
            executable_path=BROWSER_EXECUTABLE,
            args=["--no-sandbox", "--disable-gpu", "--display=:99"],
            viewport={"width": 1280, "height": 800},
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            set_acceptance_override(str(sample["object_id"]), BACKEND)
            result, ident = run_acceptance_acquisition_export(
                page, sample, run_label, baseline_identity=baseline_identity)
            clear_acceptance_overrides()
        finally:
            try:
                context.close()
            except Exception:
                pass
    subprocess.run(["pkill", "-f", "chrome-linux64/chrome"], capture_output=True)
    time.sleep(1)
    proc = subprocess.run(["pgrep", "-c", "-f", "chrome-linux64/chrome"],
                          capture_output=True, text=True)
    try:
        chrome_after = int(proc.stdout.strip())
    except (ValueError, IndexError):
        chrome_after = 0

    payload = {"result": asdict(result), "identity": ident, "chrome_after": chrome_after}
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
