"""Cruise/scan timing harness (R-271).

Starts the freeform mock model (with optional latency model) and the app on a
private data dir, imports the three 青苗计划 sample files, then either scans or
cruises and reports wall-clock, per-call timings and the resulting candidates.

The mock model is used so the harness is free and repeatable: every request is
delayed by ``--latency`` seconds, and requests that did not disable thinking get
``--thinking-extra`` on top, which is how a reasoning model behaves.  Same knobs
on both branches make before/after comparable.

Usage:
  python scripts/measure_cruise.py --mode both --latency 2 --thinking-extra 25 \
      --out .r271-bench/after-both.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def http(method: str, url: str, body=None, timeout=300):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"__http_error__": exc.code, "body": exc.read().decode("utf-8", "replace")}


def wait_http(url: str, timeout=60.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                resp.read()
                return True
        except Exception:
            time.sleep(0.3)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["scan", "cruise", "both"], required=True)
    ap.add_argument("--port", type=int, default=8811)
    ap.add_argument("--mock-port", type=int, default=18011)
    ap.add_argument("--latency", type=float, default=0.0)
    ap.add_argument("--thinking-extra", type=float, default=0.0)
    ap.add_argument("--samples", default=r"D:\PAW\验收样例")
    ap.add_argument("--out", required=True)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--restart-after-setup", action="store_true",
                    help="restart the app so the saved settings (watcher off) apply before the run")
    ap.add_argument("--staged", action="store_true",
                    help="import v1 first, analyse it, then add v2 + weekly and analyse again")
    args = ap.parse_args()

    work = ROOT / ".r271-bench" / f"{args.mode}-{int(time.time())}"
    data_dir = work / "data"
    watch = work / "watch"
    data_dir.mkdir(parents=True, exist_ok=True)
    watch.mkdir(parents=True, exist_ok=True)

    port, mport = args.port, args.mock_port
    env = dict(os.environ)
    env.update({
        "PAW_DATA_DIR": str(data_dir),
        "PAW_NO_BROWSER": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    })
    mock_env = dict(os.environ)
    mock_env.update({
        "PAW_MOCK_MODE": "freeform",
        "PAW_MOCK_LATENCY": str(args.latency),
        "PAW_MOCK_THINKING_EXTRA": str(args.thinking_extra),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    })

    mock_log = open(work / "mock.log", "w", encoding="utf-8")
    app_log = open(work / "app.log", "w", encoding="utf-8")
    mock = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "mock_openai_server.py"),
                             "--port", str(mport)], cwd=str(ROOT), env=mock_env,
                            stdout=mock_log, stderr=subprocess.STDOUT)
    app = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app",
                            "--host", "127.0.0.1", "--port", str(port), "--log-level", "info"],
                           cwd=str(ROOT), env=env, stdout=app_log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        if not wait_http(f"http://127.0.0.1:{mport}/health", 30):
            raise SystemExit("mock server did not start")
        if not wait_http(base + "/v1/local/runtime/status", 180):
            raise SystemExit("app did not start")
        # Let the automatic startup cruise finish on the empty folder.
        for _ in range(120):
            st = http("GET", base + "/v1/local/runtime/status")
            if (st.get("scheduler") or {}).get("status") != "running":
                break
            time.sleep(0.5)

        settings = http("POST", base + "/v1/local/settings", {
            "provider": "custom",
            "custom_api_key": "sk-mock-bench",
            "custom_base_url": f"http://127.0.0.1:{mport}/v1",
            "custom_model": "freeform-mock",
            "work_dir": str(watch),
            "work_dirs": [str(watch)],
            "speed_mode": "standard",
            "watch_enabled": False,
        })
        project = http("POST", base + "/v1/web/workspace/projects", {"name": "青苗计划"})

        if args.restart_after_setup:
            app.terminate()
            try:
                app.wait(timeout=20)
            except Exception:
                app.kill()
            app = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app",
                                    "--host", "127.0.0.1", "--port", str(port), "--log-level", "info"],
                                   cwd=str(ROOT), env=env, stdout=app_log, stderr=subprocess.STDOUT)
            if not wait_http(base + "/v1/local/runtime/status", 180):
                raise SystemExit("app did not restart")
            for _ in range(240):
                st = http("GET", base + "/v1/local/runtime/status")
                if (st.get("scheduler") or {}).get("status") != "running":
                    break
                time.sleep(0.5)

        for name in (("青苗计划_方案_v1.md",) if args.staged else
                     ("青苗计划_方案_v1.md", "青苗计划_方案_v2.md", "青苗计划_10月周报.md")):
            shutil.copy2(Path(args.samples) / name, watch / name)

        http("POST", f"http://127.0.0.1:{mport}/stats/reset", {})
        import datetime as _dt
        copied_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
        started = time.perf_counter()
        scan_seconds = None
        cruise_result = None

        def run_scan():
            mark = time.perf_counter()
            http("POST", base + "/v1/local/scanner/start", {"directory": str(watch)})
            deadline = time.time() + 1800
            prog = {}
            while time.time() < deadline:
                prog = http("GET", base + "/v1/local/scanner/progress")
                status = str(prog.get("status") or "")
                if status in ("completed", "done", "stopped", "error", "failed", "idle"):
                    break
                if status.startswith("phase2_") and prog.get("phase2_queue_remaining") == 0 \
                        and prog.get("phase2_status") in ("complete", "completed", "done", None):
                    break
                time.sleep(0.5)
            return prog, time.perf_counter() - mark

        if args.mode in ("scan", "both"):
            prog, scan_seconds = run_scan()
            if args.staged:
                for name in ("青苗计划_方案_v2.md", "青苗计划_10月周报.md"):
                    shutil.copy2(Path(args.samples) / name, watch / name)
                prog, _later = run_scan()
                scan_seconds += _later
            elapsed = scan_seconds
            detail = prog if prog else http("GET", base + "/v1/local/scanner/progress")
        if args.mode in ("cruise", "both"):
            # Trigger the cruise directly and keep the result that actually did
            # the work; a watcher-started cruise can otherwise answer first.
            def work(result):
                result = result or {}
                return (result.get("status") == "success"
                        and (int(result.get("files_analyzed") or 0)
                             + int(result.get("files_unchanged") or 0)
                             + int(result.get("projects_analyzed") or 0)) > 0)

            result = None
            for _attempt in range(6):
                for _ in range(240):
                    st = http("GET", base + "/v1/local/runtime/status")
                    if (st.get("scheduler") or {}).get("status") != "running":
                        break
                    time.sleep(0.5)
                res = http("POST", base + "/v1/local/runtime/cruise/trigger",
                           {"analyze_projects": True, "async_mode": False}, timeout=1800)
                candidate = res.get("result") if isinstance(res, dict) else None
                if work(candidate):
                    result = candidate
                    break
                last = (http("GET", base + "/v1/local/runtime/status").get("scheduler") or {}).get("latest_cruise_result")
                if work(last):
                    result = last
                    break
            cruise_result = result
            detail = result
            elapsed = float((result or {}).get("elapsed_seconds") or 0.0)

        stats = http("GET", f"http://127.0.0.1:{mport}/stats")

        db = data_dir / "workspace.db"
        candidates = []
        if db.exists():
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            for row in conn.execute("SELECT id,candidate_type,proposed_change,status,"
                                    "source_refs,confidence,analysis_model,raw_excerpt FROM candidate"):
                item = dict(row)
                try:
                    item["proposed_change"] = json.loads(item["proposed_change"] or "{}")
                except Exception:
                    pass
                candidates.append(item)
            conn.close()

        report = {
            "mode": args.mode,
            "latency": args.latency,
            "thinking_extra": args.thinking_extra,
            "scan_seconds": round(scan_seconds, 2) if scan_seconds is not None else None,
            "cruise_result": cruise_result,
            "wall_seconds": round(elapsed, 2),
            "detail": detail,
            "mock_calls": stats.get("calls"),
            "mock_call_count": stats.get("chat_requests"),
            "candidates": candidates,
            "settings_ok": settings.get("ok"),
            "project": project,
        }

        # Batch accept (the console path) to reproduce "cannot confirm" items.
        if candidates:
            ids = [c["id"] for c in candidates if c.get("status") == "pending"]
            report["batch_accept"] = http("POST", base + "/v1/web/brain/review/batch_accept",
                                          {"candidate_ids": ids, "reviewer": "LocalUser"})
            report["after_accept"] = http("GET", base + "/v1/web/brain/review/grouped")

        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"mode": args.mode, "wall_seconds": report["wall_seconds"],
                          "calls": report["mock_call_count"],
                          "candidates": len(candidates),
                          "out": args.out}, ensure_ascii=False))
        return 0
    finally:
        for proc in (app, mock):
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        mock_log.close()
        app_log.close()


if __name__ == "__main__":
    raise SystemExit(main())
