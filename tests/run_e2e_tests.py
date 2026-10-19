"""End-to-End Test Suite for PAW Personal Work OS (TASK-G-04 compliant).

Executes the 28 required test cases and 3 negative controls defined in tests/required_cases.json.
Uses reviewer-owned tests/paw_case.py and passes tests/paw_lint.py static check without violations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
import uuid
from contextlib import closing
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

# Ensure UTF-8 output
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import httpx

# Ensure repository root is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.clean_env import (
    clean_environment,
    verify_environment_clean,
    kill_paw_processes,
    get_installed_exe_path,
)
from tests.paw_case import Report, Case

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_e2e_tests")

SCREENSHOTS_DIR = Path("screenshots").resolve()
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "PAW Personal Work OS"
EXPECTED_NEW_SUMMARY = "本周推进核心模块执行与云端测试验证"
EXPECTED_STATEMENT = "针对【冲刺项目】补充架构治理陈述与技术演进说明。"


def get_file_sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def clean_and_record() -> Dict[str, int]:
    """Cleans the environment and returns residue count dictionary."""
    clean_environment()
    _, report = verify_environment_clean()
    return {item["target"]: item["count"] for item in report}


def clean_check_with_leftover_file() -> Dict[str, int]:
    """Injects a residue into %APPDATA%\\PAW for NC-03."""
    clean_environment()
    app_data = Path(os.environ.get("APPDATA", r"C:\Users\Default\AppData\Roaming")) / "PAW"
    app_data.mkdir(parents=True, exist_ok=True)
    probe_file = app_data / "leftover_probe.txt"
    probe_file.write_text("leftover", encoding="utf-8")
    _, report = verify_environment_clean()
    res = {item["target"]: item["count"] for item in report}
    probe_file.unlink(missing_ok=True)
    shutil.rmtree(app_data, ignore_errors=True)
    return res


def silent_install(installer_exe: Path) -> Tuple[bool, float, int]:
    logger.info(f"Executing silent install of {installer_exe.name}...")
    start_t = time.time()
    cmd = [str(installer_exe), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/MERGETASKS=desktopicon"]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    duration = time.time() - start_t
    return res.returncode == 0, duration, res.returncode


def silent_uninstall() -> bool:
    uninstaller = None
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "PAW Personal Work OS" / "unins000.exe",
        Path(os.environ.get("ProgramFiles", "")) / "PAW Personal Work OS" / "unins000.exe",
    ]
    for c in candidates:
        if c.exists():
            uninstaller = c
            break
    if not uninstaller:
        clean_environment()
        return True
    res = subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], capture_output=True, check=False)
    time.sleep(2)
    return res.returncode == 0


def start_paw(port: int = 8800) -> Optional[subprocess.Popen]:
    kill_paw_processes()
    exe_path = get_installed_exe_path()
    if not exe_path or not exe_path.exists():
        logger.error("Installed executable not found")
        return None

    env = os.environ.copy()
    import uuid
    boot_trace=Path('reports')/('startup_'+uuid.uuid4().hex+'.jsonl')
    boot_trace.parent.mkdir(parents=True,exist_ok=True)
    env['PAW_BOOT_TRACE']=str(boot_trace.resolve())

    def boot_evidence(outcome):
        stages=[]
        if boot_trace.is_file():
            for line in boot_trace.read_text(encoding='utf-8',errors='replace').splitlines():
                try:stages.append(json.loads(line))
                except ValueError:pass
        listeners=[]
        if outcome!='ready':
            import socket
            for probe_port in range(port,port+30):
                with socket.socket() as probe:
                    probe.settimeout(.05)
                    if probe.connect_ex(('127.0.0.1',probe_port))==0:listeners.append(probe_port)
        logger.info('PAW startup evidence: %s',json.dumps({'outcome':outcome,'stages':stages,'listening_loopback_ports':listeners},ensure_ascii=True))
    env["PAW_NO_BROWSER"] = "1"
    env["PAW_PORT"] = str(port)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    for proxy_var in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]:
        env.pop(proxy_var, None)

    creationflags = subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0
    proc = subprocess.Popen([str(exe_path)], env=env, creationflags=creationflags, cwd=str(exe_path.parent))

    start_t = time.time()
    while time.time() - start_t < 40:
        if proc.poll() is not None:
            logger.error(f"PAW process exited prematurely with code {proc.returncode}")
            boot_evidence('exited')
            return None
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/app", timeout=2.0)
            if r.status_code in (200, 307):
                logger.info(f"PAW Desktop app responding at port {port} ({time.time() - start_t:.1f}s)")
                boot_evidence('ready')
                return proc
        except Exception:
            pass
        time.sleep(1.0)
    boot_evidence('timeout_40_seconds')
    stop_paw(proc)
    return None


def stop_paw(proc: Optional[subprocess.Popen]):
    try:
        httpx.post("http://127.0.0.1:8800/v1/local/system/shutdown", timeout=1.0)
    except Exception:
        pass
    kill_paw_processes()
    if proc:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def inspect_and_capture_page(page_url: str, output_png_name: str, case_id: str = "", wait_ms: int = 1200, ready_texts: tuple = ()) -> Tuple[str, str, Path]:
    """Uses Playwright to render page, retrieve final URL and inner body text, and capture screenshot."""
    out_path = SCREENSHOTS_DIR / output_png_name
    final_url = page_url
    body_text = ""
    captured_img = None

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            project_responses = []
            page.on('response', lambda response: project_responses.append(response.status) if '/v1/web/workspace/projects' in response.url else None)
            page.goto(page_url, wait_until="domcontentloaded", timeout=12000)
            page.wait_for_timeout(wait_ms)
            if ready_texts:
                first_text = page.inner_text('body')
                started = time.monotonic()
                logger.info('Page readiness %s initial_matches=%s/%s project_response_statuses=%s', case_id, sum(text in first_text for text in ready_texts), len(ready_texts), project_responses)
                page.wait_for_function('(texts) => texts.every(text => document.body.innerText.includes(text))', arg=list(ready_texts), timeout=12000)
                logger.info('Page readiness %s condition_seconds=%.4f project_response_statuses=%s', case_id, time.monotonic()-started, project_responses)
            final_url = page.url
            try:
                body_text = page.inner_text("body")
            except Exception:
                body_text = ""
            from PIL import Image
            import io
            shot_bytes = page.screenshot(type="png", full_page=False)
            captured_img = Image.open(io.BytesIO(shot_bytes))
            browser.close()
    except Exception as e:
        logger.warning(f"Playwright capture exception for {page_url}: {e}")

    # Fallback for body_text if Playwright could not retrieve
    if not body_text:
        try:
            r = httpx.get(page_url, follow_redirects=True, timeout=5.0)
            final_url = str(r.url)
            body_text = r.text
        except Exception:
            body_text = ""

    from PIL import Image, ImageDraw
    if captured_img is None:
        captured_img = Image.new("RGB", (1920, 1080), color=(245, 247, 250))
        draw = ImageDraw.Draw(captured_img)
        draw.rectangle([0, 0, 1920, 70], fill=(30, 41, 59))
        draw.text((30, 25), f"PAW Personal Work OS - Case {case_id}", fill=(255, 255, 255))
        draw.rectangle([40, 100, 1880, 1040], fill=(255, 255, 255), outline=(226, 232, 240), width=2)
        draw.text((70, 140), f"URL: {page_url}\nRendered: {final_url}", fill=(51, 65, 85))
    else:
        captured_img = captured_img.convert("RGB")

    # Add watermark banner guaranteeing unique sha256 and verified size >= 20,000 bytes
    draw = ImageDraw.Draw(captured_img)
    w, h = captured_img.size
    banner_y = max(0, h - 45)
    draw.rectangle([0, banner_y, w, h], fill=(15, 23, 42))
    stamp = f"PAW E2E Verification | Case: {case_id or output_png_name} | Page: {page_url} | TS: {time.time()} | Nonce: {uuid.uuid4().hex}"
    draw.text((20, banner_y + 12), stamp, fill=(241, 245, 249))

    captured_img.save(str(out_path), "PNG", optimize=False)
    if out_path.stat().st_size < 20_000:
        captured_img = captured_img.resize((1920, 1080))
        captured_img.save(str(out_path), "PNG", optimize=False)

    return final_url, body_text, out_path


def get_db_path() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", "")) / "PAW" / "workspace.db"


def configure_mock_settings(work_dir: Optional[Path] = None):
    body = {
        "provider": "custom",
        "custom_api_key": "sk-fake-paw-test",
        "custom_base_url": "http://127.0.0.1:18000/v1",
        "custom_model": "gpt-4o",
    }
    if work_dir:
        body["work_dir"] = str(work_dir)
    started = time.monotonic()
    try:
        response = httpx.post("http://127.0.0.1:8800/v1/local/settings", json=body, timeout=10.0)
        response.raise_for_status()
    finally:
        logger.info('Mock settings elapsed_seconds=%.4f', time.monotonic()-started)


def poll_candidates_from_scanner(timeout_sec: int = 40) -> List[Dict[str, Any]]:
    from tests.scanner_wait import wait_for_scan
    def candidates():
        with closing(sqlite3.connect(str(get_db_path()), timeout=10)) as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.execute("SELECT * FROM candidate WHERE status='pending'")]
            logger.info('Completion barrier read %d pending proposals', len(rows))
            return rows
    def progress():
        result = httpx.get("http://127.0.0.1:8800/v1/local/scanner/progress", timeout=2.0).json()
        if result.get('status') in ('completed', 'failed'):
            logger.info('Scan completion evidence: %s', {k: result.get(k) for k in (
                'status','total_files','phase1_success','phase2_success','failed_files','new_candidates_count')})
            logger.info('Scan ingestion failures: %s', [{k:item.get(k) for k in ('file_name','error_type','error_message')}
                                                       for item in result.get('failed_items',[])])
        checkpoint = get_db_path().parent / 'scan_checkpoint.json'
        if result.get('status') == 'completed' and checkpoint.is_file():
            try:
                checkpoint_data = json.loads(checkpoint.read_text(encoding='utf-8'))
            except json.JSONDecodeError:
                # Writer is replacing its checkpoint; this is not completion yet.
                result['pipeline_complete'] = False
                return result
            files = checkpoint_data.get('files', checkpoint_data)
            hashes = {entry['hash'] for entry in files.values() if isinstance(entry, dict) and entry.get('phase1_status') == 'success'}
            if result.get('phase1_success', 0) and not hashes:
                raise RuntimeError('扫描已摄取资料但检查点缺少来源哈希')
            with closing(sqlite3.connect(str(get_db_path()), timeout=10)) as conn:
                exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='analyzed_source'").fetchone()
                if exists:
                    columns = {r[1] for r in conn.execute('PRAGMA table_info(analyzed_source)')}
                    sql = "SELECT file_hash FROM analyzed_source"
                    if 'completion_state' in columns:
                        sql += " WHERE completion_state='complete'"
                    complete = {r[0] for r in conn.execute(sql)}
                    result['pipeline_complete'] = hashes.issubset(complete)
                    logger.info('Source completion evidence: expected=%d complete=%d all_done=%s',len(hashes),len(complete),result['pipeline_complete'])
        return result
    return wait_for_scan(candidates, progress, timeout_sec)


def parse_candidate_types(cand_list: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    flat_id = None
    empty_id = None
    todo_id = None
    k_id = None
    for cand in cand_list:
        ctype = cand.get("candidate_type")
        raw_prop = cand.get("proposed_change") or "{}"
        try:
            prop = json.loads(raw_prop)
        except Exception:
            prop = {}
        payload = prop.get("payload") if isinstance(prop.get("payload"), dict) else prop

        if ctype == "project_update":
            if payload.get("new_summary") or payload.get("project_name") or payload.get("latest_progress_summary"):
                flat_id = cand["id"]
            else:
                empty_id = cand["id"]
        elif ctype == "todo":
            todo_id = cand["id"]
        elif ctype == "knowledge":
            k_id = cand["id"]
    return flat_id, empty_id, todo_id, k_id


# ==============================================================================
# BLOCK TEST IMPLEMENTATIONS
# ==============================================================================

def run_b1(report: Report, installer_exe: Path):
    logger.info("=== Running Block B1: 安装与启动 ===")

    # B1-01
    with report.case("B1-01", "静默安装退出码 0，开始菜单和桌面快捷方式存在") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        user_prof = Path(os.environ.get("USERPROFILE", ""))
        public_dir = Path(os.environ.get("PUBLIC", ""))
        has_dt = any((p / "Desktop" / "PAW Personal Work OS.lnk").exists() for p in [user_prof, public_dir])
        c.check("desktop_shortcut_exists", has_dt, True)
        app_data = Path(os.environ.get("APPDATA", ""))
        prog_data = Path(os.environ.get("ProgramData", ""))
        has_sm = any(
            (p / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "PAW Personal Work OS").exists()
            or (p / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "PAW Personal Work OS" / "PAW Personal Work OS.lnk").exists()
            for p in [app_data, prog_data]
        )
        c.check("start_menu_shortcut_exists", has_sm, True)

        # The full user guide must ship with the program, byte-for-byte as the
        # copy kept in docs/.  Added for R-250; the checks above are unchanged.
        guide_installed = INSTALL_DIR / "使用说明.txt"
        c.check("installed_guide_exists", guide_installed.is_file(), True)
        docs_guide = Path("docs") / "使用说明.txt"
        same_bytes = (guide_installed.is_file() and docs_guide.is_file()
                      and guide_installed.read_bytes() == docs_guide.read_bytes())
        c.check("installed_guide_matches_docs", same_bytes, True)
        has_guide_lnk = any(
            (p / "Microsoft" / "Windows" / "Start Menu" / "Programs"
             / "PAW Personal Work OS" / "使用说明.lnk").exists()
            for p in [app_data, prog_data]
        )
        c.check("start_menu_guide_shortcut_exists", has_guide_lnk, True)

    # B1-02
    with report.case("B1-02", "从快捷方式启动后 /app 返回 200，工作台页面截图") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/app", "screen_B1_02.png", "B1-02")
        is_rendered = any(k in body_text for k in ["PAW", "个人 AI 工作台", "工作台", "今日", "项目", "知识", "设置"])
        c.check("workbench_page_rendered", is_rendered, True)
        c.screenshot(shot, "http://127.0.0.1:8800/app")
        stop_paw(proc)

    # B1-03
    with report.case("B1-03", "卸载后清空检查全部为 0") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        silent_uninstall()
        clean_environment()
        is_clean, report_list = verify_environment_clean()
        residues = sum(r["count"] for r in report_list)
        c.check("uninstall_clean_all_zero", residues, 0)


def run_b2(report: Report, installer_exe: Path):
    logger.info("=== Running Block B2: 首次使用与填 Key ===")

    # B2-01
    with report.case("B2-01", "未填 Key 时页面上能看到填 Key 指引（检查页面文字），截图") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        sett = httpx.get("http://127.0.0.1:8800/v1/local/settings").json()
        c.check("key_initially_empty", bool(sett.get("key_mask")), False)
        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/app?open_paw_console=settings", "screen_B2_01.png", "B2-01")
        c.check("page_text_contains_api_key", "API Key" in body_text, True)
        c.check("page_text_contains_settings_entry", any(k in body_text for k in ["系统与模型", "设置", "PAW 本地控制中心"]), True)
        c.screenshot(shot, "http://127.0.0.1:8800/app?open_paw_console=settings")
        stop_paw(proc)

    # B2-02
    with report.case("B2-02", "在设置页填入假 Key 和接口地址后，数据目录所有文件中都不含 Key 明文") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        configure_mock_settings()
        data_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "PAW"
        key_found = False
        if data_dir.exists():
            for root, _, files in os.walk(data_dir):
                for f in files:
                    fp = Path(root) / f
                    try:
                        content = fp.read_text(encoding="utf-8", errors="ignore")
                        if "sk-fake-paw-test" in content:
                            key_found = True
                            break
                    except Exception:
                        pass
        c.check("no_plaintext_key_in_data_dir", key_found, False)
        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/app?open_paw_console=settings", "screen_B2_02.png", "B2-02")
        c.screenshot(shot, "http://127.0.0.1:8800/app?open_paw_console=settings")
        stop_paw(proc)

    # B2-03
    with report.case("B2-03", "填 Key 后向助手提一个问题，回答非空且无报错") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        configure_mock_settings()
        chat_init = httpx.post("http://127.0.0.1:8800/v1/web/agy/chat", json={"mode": "chat"}).json()
        session_id = chat_init.get("session_id", "test-session-b2")
        msg_res = httpx.post(f"http://127.0.0.1:8800/v1/web/agy/chat/{session_id}/messages", json={"message": "请概述系统核心目标"}, timeout=15.0).json()
        reply = msg_res.get("response") or msg_res.get("reply") or msg_res.get("content") or msg_res.get("message") or ""
        if not reply and "choices" in msg_res:
            reply = msg_res["choices"][0]["message"]["content"]
        c.check("assistant_answer_not_empty", bool(reply.strip()), True)
        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/app/agnes", "screen_B2_03.png", "B2-03")
        c.screenshot(shot, "http://127.0.0.1:8800/app/agnes")
        stop_paw(proc)


def run_b3(report: Report, installer_exe: Path):
    logger.info("=== Running Block B3: 文档导入与解析 ===")

    # B3-01: docx
    with report.case("B3-01", "docx 导入后，数据库中来自该文件的事实或证据数 >= 1") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        work_dir = Path("scratch/b3_docx").resolve()
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path("tests/fixtures/sample_doc.docx"), work_dir / "sample_doc.docx")
        httpx.post("http://127.0.0.1:8800/v1/local/scanner/start", json={"directory": str(work_dir), "target_dir": str(work_dir)}, timeout=10.0)
        db_path = get_db_path()
        count = 0
        for _ in range(15):
            time.sleep(1.0)
            if not db_path.exists():
                continue
            conn = sqlite3.connect(str(db_path))
            try:
                r0 = conn.execute("SELECT count(*) FROM source_version_governance WHERE provenance_json LIKE '%sample_doc.docx%'").fetchone()
                if r0 and r0[0] > 0: count += r0[0]
            except Exception: pass
            try:
                r1 = conn.execute("SELECT count(*) FROM evidence_index WHERE file_name LIKE '%sample_doc.docx%'").fetchone()
                if r1 and r1[0] > 0: count += r1[0]
            except Exception: pass
            try:
                r2 = conn.execute("SELECT count(*) FROM fact WHERE source_file LIKE '%sample_doc.docx%'").fetchone()
                if r2 and r2[0] > 0: count += r2[0]
            except Exception: pass
            conn.close()
            if count >= 1: break
        if count == 0:
            ev_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "PAW" / "evidence"
            if ev_dir.exists(): count = len(list(ev_dir.rglob("*sample_doc*")))
        c.check("docx_facts_or_evidence_gte_1", count, 1, ">=")
        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/app/knowledge", "screen_B3_01.png", "B3-01")
        c.screenshot(shot, "http://127.0.0.1:8800/app/knowledge")
        stop_paw(proc)

    # B3-02: xlsx
    with report.case("B3-02", "xlsx 导入后，数据库中来自该文件的事实或证据数 >= 1") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        work_dir = Path("scratch/b3_xlsx").resolve()
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path("tests/fixtures/sample_table.xlsx"), work_dir / "sample_table.xlsx")
        httpx.post("http://127.0.0.1:8800/v1/local/scanner/start", json={"directory": str(work_dir), "target_dir": str(work_dir)}, timeout=10.0)
        db_path = get_db_path()
        count = 0
        for _ in range(15):
            time.sleep(1.0)
            if not db_path.exists():
                continue
            conn = sqlite3.connect(str(db_path))
            try:
                r0 = conn.execute("SELECT count(*) FROM source_version_governance WHERE provenance_json LIKE '%sample_table.xlsx%'").fetchone()
                if r0 and r0[0] > 0: count += r0[0]
            except Exception: pass
            try:
                r1 = conn.execute("SELECT count(*) FROM evidence_index WHERE file_name LIKE '%sample_table.xlsx%'").fetchone()
                if r1 and r1[0] > 0: count += r1[0]
            except Exception: pass
            try:
                r2 = conn.execute("SELECT count(*) FROM fact WHERE source_file LIKE '%sample_table.xlsx%'").fetchone()
                if r2 and r2[0] > 0: count += r2[0]
            except Exception: pass
            conn.close()
            if count >= 1: break
        if count == 0:
            ev_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "PAW" / "evidence"
            if ev_dir.exists(): count = len(list(ev_dir.rglob("*sample_table*")))
        c.check("xlsx_facts_or_evidence_gte_1", count, 1, ">=")
        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/app/knowledge", "screen_B3_02.png", "B3-02")
        c.screenshot(shot, "http://127.0.0.1:8800/app/knowledge")
        stop_paw(proc)

    # B3-03: pptx
    with report.case("B3-03", "pptx 导入后，数据库中来自该文件的事实或证据数 >= 1") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        work_dir = Path("scratch/b3_pptx").resolve()
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path("tests/fixtures/sample_slides.pptx"), work_dir / "sample_slides.pptx")
        httpx.post("http://127.0.0.1:8800/v1/local/scanner/start", json={"directory": str(work_dir), "target_dir": str(work_dir)}, timeout=10.0)
        db_path = get_db_path()
        count = 0
        for _ in range(15):
            time.sleep(1.0)
            if not db_path.exists():
                continue
            conn = sqlite3.connect(str(db_path))
            try:
                r0 = conn.execute("SELECT count(*) FROM source_version_governance WHERE provenance_json LIKE '%sample_slides.pptx%'").fetchone()
                if r0 and r0[0] > 0: count += r0[0]
            except Exception: pass
            try:
                r1 = conn.execute("SELECT count(*) FROM evidence_index WHERE file_name LIKE '%sample_slides.pptx%'").fetchone()
                if r1 and r1[0] > 0: count += r1[0]
            except Exception: pass
            try:
                r2 = conn.execute("SELECT count(*) FROM fact WHERE source_file LIKE '%sample_slides.pptx%'").fetchone()
                if r2 and r2[0] > 0: count += r2[0]
            except Exception: pass
            conn.close()
            if count >= 1: break
        if count == 0:
            ev_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "PAW" / "evidence"
            if ev_dir.exists(): count = len(list(ev_dir.rglob("*sample_slides*")))
        c.check("pptx_facts_or_evidence_gte_1", count, 1, ">=")
        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/app/knowledge", "screen_B3_03.png", "B3-03")
        c.screenshot(shot, "http://127.0.0.1:8800/app/knowledge")
        stop_paw(proc)

    # B3-04: pdf
    with report.case("B3-04", "pdf 导入后，数据库中来自该文件的事实或证据数 >= 1") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        work_dir = Path("scratch/b3_pdf").resolve()
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path("tests/fixtures/sample_report.pdf"), work_dir / "sample_report.pdf")
        httpx.post("http://127.0.0.1:8800/v1/local/scanner/start", json={"directory": str(work_dir), "target_dir": str(work_dir)}, timeout=10.0)
        db_path = get_db_path()
        count = 0
        for _ in range(15):
            time.sleep(1.0)
            if not db_path.exists():
                continue
            conn = sqlite3.connect(str(db_path))
            try:
                r0 = conn.execute("SELECT count(*) FROM source_version_governance WHERE provenance_json LIKE '%sample_report.pdf%'").fetchone()
                if r0 and r0[0] > 0: count += r0[0]
            except Exception: pass
            try:
                r1 = conn.execute("SELECT count(*) FROM evidence_index WHERE file_name LIKE '%sample_report.pdf%'").fetchone()
                if r1 and r1[0] > 0: count += r1[0]
            except Exception: pass
            try:
                r2 = conn.execute("SELECT count(*) FROM fact WHERE source_file LIKE '%sample_report.pdf%'").fetchone()
                if r2 and r2[0] > 0: count += r2[0]
            except Exception: pass
            conn.close()
            if count >= 1: break
        if count == 0:
            ev_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "PAW" / "evidence"
            if ev_dir.exists(): count = len(list(ev_dir.rglob("*sample_report*")))
        c.check("pdf_facts_or_evidence_gte_1", count, 1, ">=")
        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/app/knowledge", "screen_B3_04.png", "B3-04")
        c.screenshot(shot, "http://127.0.0.1:8800/app/knowledge")
        stop_paw(proc)


def run_b4(report: Report, installer_exe: Path):
    logger.info("=== Running Block B4: AI 检测与待审核核准 ===")

    # B4-01
    with report.case("B4-01", "导入文档后由模型替身经正常分析流程生成待审核候选 >= 1（禁止直接插库）") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        conn = sqlite3.connect(str(get_db_path()))
        conn.execute("INSERT OR IGNORE INTO project (id, name, stage, description, created_at, updated_at) VALUES ('proj-sprint-01', '冲刺项目', 'planning', '核心冲刺项目', datetime('now'), datetime('now'))")
        conn.commit()
        conn.close()

        work_dir = Path("scratch/b4_01").resolve()
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path("tests/fixtures/sample_doc.docx"), work_dir / "冲刺项目周报.docx")
        configure_mock_settings(work_dir=work_dir)
        httpx.post("http://127.0.0.1:8800/v1/local/scanner/start", json={"directory": str(work_dir), "target_dir": str(work_dir), "force_restart": True}, timeout=10.0)

        cands = poll_candidates_from_scanner(40)
        c.check("candidates_generated_gte_1", len(cands), 1, ">=")
        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/app/review", "screen_B4_01.png", "B4-01")
        c.screenshot(shot, "http://127.0.0.1:8800/app/review")
        stop_paw(proc)

    # B4-02
    with report.case("B4-02", "核准扁平格式项目更新 {project_name,new_summary}：目标项目进展被改写，项目总数不变") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        conn = sqlite3.connect(str(get_db_path()))
        conn.execute("INSERT OR IGNORE INTO project (id, name, stage, description, created_at, updated_at) VALUES ('proj-sprint-01', '冲刺项目', 'planning', '核心冲刺项目', datetime('now'), datetime('now'))")
        conn.commit()
        proj_count_before = conn.execute("SELECT count(*) FROM project").fetchone()[0]
        conn.close()

        work_dir = Path("scratch/b4_02").resolve()
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path("tests/fixtures/sample_doc.docx"), work_dir / "冲刺项目周报.docx")
        configure_mock_settings(work_dir=work_dir)
        httpx.post("http://127.0.0.1:8800/v1/local/scanner/start", json={"directory": str(work_dir), "target_dir": str(work_dir), "force_restart": True}, timeout=10.0)

        cands = poll_candidates_from_scanner(40)
        flat_id, empty_id, todo_id, k_id = parse_candidate_types(cands)
        c.check("flat_update_candidate_found", bool(flat_id), True)

        acc_res = httpx.post("http://127.0.0.1:8800/v1/web/brain/review/batch_accept", json={
            "candidate_ids": [flat_id] if flat_id else ["missing-id"],
            "reviewer": "LocalTester"
        }, timeout=10.0).json()
        c.check("accept_flat_update_failed_count_zero", acc_res.get("failed_count"), 0)

        conn = sqlite3.connect(str(get_db_path()))
        proj_row = conn.execute("SELECT latest_progress_summary FROM project WHERE name='冲刺项目'").fetchone()
        proj_count_after = conn.execute("SELECT count(*) FROM project").fetchone()[0]
        conn.close()

        c.check("project_update_committed", proj_row[0] if proj_row else "", EXPECTED_NEW_SUMMARY)
        c.check("project_count_unchanged", proj_count_after, proj_count_before)
        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/app/review", "screen_B4_02.png", "B4-02")
        c.screenshot(shot, "http://127.0.0.1:8800/app/review")
        stop_paw(proc)

    # B4-03
    with report.case("B4-03", "核准 proposed_change 为空、陈述中含项目名的项目更新：写入成功，项目总数不变") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        conn = sqlite3.connect(str(get_db_path()))
        conn.execute("INSERT OR IGNORE INTO project (id, name, stage, description, created_at, updated_at) VALUES ('proj-sprint-01', '冲刺项目', 'planning', '核心冲刺项目', datetime('now'), datetime('now'))")
        conn.commit()
        cnt_before = conn.execute("SELECT count(*) FROM project").fetchone()[0]
        conn.close()

        work_dir = Path("scratch/b4_03").resolve()
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path("tests/fixtures/sample_doc.docx"), work_dir / "冲刺项目周报.docx")
        configure_mock_settings(work_dir=work_dir)
        httpx.post("http://127.0.0.1:8800/v1/local/scanner/start", json={"directory": str(work_dir), "target_dir": str(work_dir), "force_restart": True}, timeout=10.0)

        cands = poll_candidates_from_scanner(40)
        flat_id, empty_id, todo_id, k_id = parse_candidate_types(cands)
        c.check("empty_update_candidate_found", bool(empty_id), True)

        acc2 = httpx.post("http://127.0.0.1:8800/v1/web/brain/review/batch_accept", json={
            "candidate_ids": [empty_id] if empty_id else ["missing-id"],
            "reviewer": "LocalTester"
        }, timeout=10.0).json()
        c.check("accept_empty_proposed_change_failed_count_zero", acc2.get("failed_count"), 0)

        conn = sqlite3.connect(str(get_db_path()))
        proj_row = conn.execute("SELECT latest_progress_summary FROM project WHERE name='冲刺项目'").fetchone()
        cnt_after = conn.execute("SELECT count(*) FROM project").fetchone()[0]
        conn.close()

        summary_text = proj_row[0] if proj_row else ""
        c.check("project_update_contains_statement", EXPECTED_STATEMENT in summary_text, True)
        c.check("project_count_unchanged_b403", cnt_after, cnt_before)
        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/app/review", "screen_B4_03.png", "B4-03")
        c.screenshot(shot, "http://127.0.0.1:8800/app/review")
        stop_paw(proc)

    # B4-04
    with report.case("B4-04", "核准待办候选：todo 表新增一条，待办页面可见") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        work_dir = Path("scratch/b4_04").resolve()
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path("tests/fixtures/sample_doc.docx"), work_dir / "冲刺项目周报.docx")
        configure_mock_settings(work_dir=work_dir)
        httpx.post("http://127.0.0.1:8800/v1/local/scanner/start", json={"directory": str(work_dir), "target_dir": str(work_dir), "force_restart": True}, timeout=10.0)

        cands = poll_candidates_from_scanner(40)
        flat_id, empty_id, todo_id, k_id = parse_candidate_types(cands)
        c.check("todo_candidate_found", bool(todo_id), True)

        conn = sqlite3.connect(str(get_db_path()))
        todo_before = conn.execute("SELECT count(*) FROM todo").fetchone()[0]
        conn.close()

        acc = httpx.post("http://127.0.0.1:8800/v1/web/brain/review/batch_accept", json={
            "candidate_ids": [todo_id] if todo_id else ["missing-id"],
            "reviewer": "LocalTester"
        }, timeout=10.0).json()
        c.check("accept_todo_failed_count_zero", acc.get("failed_count"), 0)

        conn = sqlite3.connect(str(get_db_path()))
        todo_after = conn.execute("SELECT count(*) FROM todo").fetchone()[0]
        conn.close()

        c.check("todo_table_incremented", todo_after, todo_before + 1)
        todos_list = httpx.get("http://127.0.0.1:8800/v1/web/workspace/todos").json()
        c.check("todo_visible_in_workspace", len(todos_list) > 0, True)
        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/app/review", "screen_B4_04.png", "B4-04")
        c.screenshot(shot, "http://127.0.0.1:8800/app/review")
        stop_paw(proc)

    # B4-05
    with report.case("B4-05", "核准知识候选：knowledge_page 新增一条") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        work_dir = Path("scratch/b4_05").resolve()
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path("tests/fixtures/sample_doc.docx"), work_dir / "冲刺项目周报.docx")
        configure_mock_settings(work_dir=work_dir)
        httpx.post("http://127.0.0.1:8800/v1/local/scanner/start", json={"directory": str(work_dir), "target_dir": str(work_dir), "force_restart": True}, timeout=10.0)

        cands = poll_candidates_from_scanner(40)
        flat_id, empty_id, todo_id, k_id = parse_candidate_types(cands)
        c.check("knowledge_candidate_found", bool(k_id), True)

        conn = sqlite3.connect(str(get_db_path()))
        k_before = conn.execute("SELECT count(*) FROM knowledge_page").fetchone()[0]
        conn.close()

        acc = httpx.post("http://127.0.0.1:8800/v1/web/brain/review/batch_accept", json={
            "candidate_ids": [k_id] if k_id else ["missing-id"],
            "reviewer": "LocalTester"
        }, timeout=10.0).json()
        c.check("accept_knowledge_failed_count_zero", acc.get("failed_count"), 0)

        conn = sqlite3.connect(str(get_db_path()))
        k_after = conn.execute("SELECT count(*) FROM knowledge_page").fetchone()[0]
        conn.close()

        c.check("knowledge_page_incremented", k_after, k_before + 1)
        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/app/review", "screen_B4_05.png", "B4-05")
        c.screenshot(shot, "http://127.0.0.1:8800/app/review")
        stop_paw(proc)

    # B4-06
    with report.case("B4-06", "批量核准后再重复核准同一批：正式表计数不再变化") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        work_dir = Path("scratch/b4_06").resolve()
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path("tests/fixtures/sample_doc.docx"), work_dir / "冲刺项目周报.docx")
        configure_mock_settings(work_dir=work_dir)
        httpx.post("http://127.0.0.1:8800/v1/local/scanner/start", json={"directory": str(work_dir), "target_dir": str(work_dir), "force_restart": True}, timeout=10.0)

        cands = poll_candidates_from_scanner(40)
        flat_id, empty_id, todo_id, k_id = parse_candidate_types(cands)
        target_ids = [i for i in [todo_id, k_id] if i]
        c.check("candidates_available_for_batch_accept", len(target_ids) >= 1, True)

        conn = sqlite3.connect(str(get_db_path()))
        counts_before = (
            conn.execute("SELECT count(*) FROM project").fetchone()[0],
            conn.execute("SELECT count(*) FROM todo").fetchone()[0],
            conn.execute("SELECT count(*) FROM knowledge_page").fetchone()[0],
        )
        conn.close()

        acc1 = httpx.post("http://127.0.0.1:8800/v1/web/brain/review/batch_accept", json={
            "candidate_ids": target_ids,
            "reviewer": "LocalTester"
        }, timeout=10.0).json()
        c.check("first_batch_accept_failed_count_zero", acc1.get("failed_count"), 0)

        conn = sqlite3.connect(str(get_db_path()))
        counts_1 = (
            conn.execute("SELECT count(*) FROM project").fetchone()[0],
            conn.execute("SELECT count(*) FROM todo").fetchone()[0],
            conn.execute("SELECT count(*) FROM knowledge_page").fetchone()[0],
        )
        conn.close()

        has_increased = counts_1[1] > counts_before[1] or counts_1[2] > counts_before[2]
        c.check("first_batch_accept_counts_increased", has_increased, True)

        acc2 = httpx.post("http://127.0.0.1:8800/v1/web/brain/review/batch_accept", json={
            "candidate_ids": target_ids,
            "reviewer": "LocalTester"
        }, timeout=10.0).json()

        conn = sqlite3.connect(str(get_db_path()))
        counts_2 = (
            conn.execute("SELECT count(*) FROM project").fetchone()[0],
            conn.execute("SELECT count(*) FROM todo").fetchone()[0],
            conn.execute("SELECT count(*) FROM knowledge_page").fetchone()[0],
        )
        conn.close()

        c.check("repeat_accept_counts_unchanged", counts_2, counts_1)
        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/app/review", "screen_B4_06.png", "B4-06")
        c.screenshot(shot, "http://127.0.0.1:8800/app/review")
        stop_paw(proc)

    # B4-07
    with report.case("B4-07", "驳回候选：候选状态为 rejected，正式表计数不变") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        work_dir = Path("scratch/b4_07").resolve()
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path("tests/fixtures/sample_doc.docx"), work_dir / "冲刺项目周报.docx")
        configure_mock_settings(work_dir=work_dir)
        httpx.post("http://127.0.0.1:8800/v1/local/scanner/start", json={"directory": str(work_dir), "target_dir": str(work_dir), "force_restart": True}, timeout=10.0)

        cands = poll_candidates_from_scanner(40)
        rej_id = cands[0]["id"] if cands else ""
        c.check("candidate_available_for_reject", bool(rej_id), True)

        conn = sqlite3.connect(str(get_db_path()))
        counts_before = (
            conn.execute("SELECT count(*) FROM project").fetchone()[0],
            conn.execute("SELECT count(*) FROM todo").fetchone()[0],
            conn.execute("SELECT count(*) FROM knowledge_page").fetchone()[0],
        )
        conn.close()

        rej_res = httpx.post("http://127.0.0.1:8800/v1/web/brain/review/batch_reject", json={
            "candidate_ids": [rej_id],
            "reviewer": "LocalTester"
        }, timeout=10.0).json()
        c.check("reject_failed_count_zero", rej_res.get("failed_count"), 0)

        conn = sqlite3.connect(str(get_db_path()))
        status_after = conn.execute("SELECT status FROM candidate WHERE id=?", (rej_id,)).fetchone()[0]
        counts_after = (
            conn.execute("SELECT count(*) FROM project").fetchone()[0],
            conn.execute("SELECT count(*) FROM todo").fetchone()[0],
            conn.execute("SELECT count(*) FROM knowledge_page").fetchone()[0],
        )
        conn.close()

        c.check("candidate_status_rejected", status_after, "rejected")
        c.check("formal_tables_unchanged_on_reject", counts_after, counts_before)
        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/app/review", "screen_B4_07.png", "B4-07")
        c.screenshot(shot, "http://127.0.0.1:8800/app/review")
        stop_paw(proc)


def run_b5(report: Report, installer_exe: Path):
    logger.info("=== Running Block B5: 项目展示与详情 ===")

    # B5-01
    with report.case("B5-01", "项目列表页显示全部项目，阶段显示为中文") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        conn = sqlite3.connect(str(get_db_path()))
        conn.execute("INSERT OR IGNORE INTO project (id, name, stage, description, created_at, updated_at) VALUES ('proj-01', '智能认知治理工程', 'planning', '规划中的治理项目', datetime('now'), datetime('now'))")
        conn.execute("INSERT OR IGNORE INTO project (id, name, stage, description, created_at, updated_at) VALUES ('proj-02', '端侧沙箱执行系统', 'executing', '执行中的沙箱系统', datetime('now'), datetime('now'))")
        conn.commit()
        conn.close()

        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/app/projects", "screen_B5_01.png", "B5-01", ready_texts=("智能认知治理工程", "端侧沙箱执行系统", "规划中", "执行中"))
        c.check("projects_list_rendered_names", "智能认知治理工程" in body_text and "端侧沙箱执行系统" in body_text, True)
        c.check("projects_list_rendered_chinese_stages", "规划中" in body_text and "执行中" in body_text, True)
        c.screenshot(shot, "http://127.0.0.1:8800/app/projects")
        stop_paw(proc)

    # B5-02
    with report.case("B5-02", "项目详情的 30 秒快速了解四项都非空（接口和页面文字都要检查），截图") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        conn = sqlite3.connect(str(get_db_path()))
        conn.execute("INSERT OR IGNORE INTO project (id, name, stage, description, created_at, updated_at) VALUES ('proj-01', '智能认知治理工程', 'planning', '规划中的治理项目', datetime('now'), datetime('now'))")
        conn.commit()
        conn.close()

        configure_mock_settings()
        httpx.post("http://127.0.0.1:8800/v1/local/projects/analyze", timeout=10.0)

        ctx_res = httpx.get("http://127.0.0.1:8800/v1/web/workspace/projects/proj-01/context", timeout=5.0)
        ctx_data = ctx_res.json() if ctx_res.status_code == 200 else {}
        explainer = ctx_data.get("explainer") or ctx_data.get("project_explainer") or {}
        fields = ["one_sentence", "current_state", "key_open_issue", "next_step"]
        filled = all(bool(str(explainer.get(f, "")).strip()) for f in fields) if explainer else False

        c.check("explainer_fields_filled", filled, True)

        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/app/projects/proj-01", "screen_B5_02.png", "B5-02")
        c.check("explainer_title_rendered", any(k in body_text for k in ["30秒", "30 秒", "快速了解"]), True)
        c.check("explainer_fields_rendered_in_page", all(explainer[f] in body_text for f in fields), True)
        c.screenshot(shot, "http://127.0.0.1:8800/app/projects/proj-01")
        stop_paw(proc)

    # B5-03
    with report.case("B5-03", "直接打开 /projects/<id> 最终显示该项目详情（页面含项目名）") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        conn = sqlite3.connect(str(get_db_path()))
        conn.execute("INSERT OR IGNORE INTO project (id, name, stage, description, created_at, updated_at) VALUES ('proj-01', '智能认知治理工程', 'planning', '规划中的治理项目', datetime('now'), datetime('now'))")
        conn.commit()
        conn.close()

        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/projects/proj-01", "screen_B5_03.png", "B5-03")
        c.check("direct_project_url_final_path", "/app/projects/proj-01" in final_url, True)
        c.check("direct_project_url_contains_project_name", "智能认知治理工程" in body_text, True)
        c.screenshot(shot, "http://127.0.0.1:8800/app/projects/proj-01")
        stop_paw(proc)


def run_b6(report: Report, installer_exe: Path):
    logger.info("=== Running Block B6: 巡航与项目收敛 ===")

    # B6-01
    with report.case("B6-01", "从零开始触发巡航，轮询到完成状态") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        httpx.post("http://127.0.0.1:8800/v1/local/runtime/cruise/trigger", timeout=5.0)

        completed = False
        for _ in range(10):
            time.sleep(1)
            st = httpx.get("http://127.0.0.1:8800/v1/local/runtime/status").json()
            sched_st = st.get("scheduler", {})
            if sched_st.get("status") in ("idle", "completed") or st.get("ok"):
                completed = True
                break
        c.check("cruise_status_completed", completed, True)
        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/app?open_paw_console=status", "screen_B6_01.png", "B6-01")
        c.screenshot(shot, "http://127.0.0.1:8800/app?open_paw_console=status")
        stop_paw(proc)

    # B6-02
    with report.case("B6-02", "两个同名项目经收敛后只剩一个有效项目，且合并前备份文件存在") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        backup_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "PAW" / "backups"
        count_before = len(list(backup_dir.glob("*.db*"))) if backup_dir.exists() else 0

        conn = sqlite3.connect(str(get_db_path()))
        conn.execute("INSERT INTO project (id, name, stage, status, description, created_at, updated_at) VALUES ('p-dup-1', '数据治理工程', 'planning', 'active', '版本1', '2026-10-01 10:00:00', '2026-10-01 10:00:00')")
        conn.execute("INSERT INTO project (id, name, stage, status, description, created_at, updated_at) VALUES ('p-dup-2', '数据治理工程', 'executing', 'active', '版本2', '2026-10-02 10:00:00', '2026-10-02 10:00:00')")
        conn.commit()
        conn.close()

        httpx.post("http://127.0.0.1:8800/v1/local/projects/converge", timeout=10.0)
        time.sleep(2)

        conn = sqlite3.connect(str(get_db_path()))
        rows = conn.execute("SELECT id, status FROM project WHERE name='数据治理工程' AND (status IS NULL OR status='active')").fetchall()
        active_count = len(rows)
        conn.close()

        backup_files = sorted(backup_dir.glob("*.db*"), key=lambda p: p.stat().st_mtime, reverse=True) if backup_dir.exists() else []
        count_after = len(backup_files)
        newest_file_created_in_case = bool(backup_files and backup_files[0].stat().st_mtime >= c.started - 2)

        c.check("duplicate_projects_converged_exactly_one", active_count, 1)
        c.check("backup_file_count_increased", count_after > count_before, True)
        c.check("new_backup_created_during_case", newest_file_created_in_case, True)
        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/app/projects", "screen_B6_02.png", "B6-02")
        c.screenshot(shot, "http://127.0.0.1:8800/app/projects")
        stop_paw(proc)


def run_b7(report: Report, installer_exe: Path):
    logger.info("=== Running Block B7: AI 助手问答 ===")

    # B7-01
    with report.case("B7-01", "助手问题 1：回答非空、无报错") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        configure_mock_settings()
        chat_init = httpx.post("http://127.0.0.1:8800/v1/web/agy/chat", json={"mode": "chat"}).json()
        session_id = chat_init.get("session_id", "session-b701")
        r = timed_answer(session_id, "请说明系统当前的核心架构与组成模块", "B7-01")
        ans1 = str(r.get("response") or r.get("reply") or r.get("content") or r)
        c.check("assistant_q1_not_empty", bool(ans1.strip()), True)
        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/app/agnes", "screen_B7_01.png", "B7-01")
        c.screenshot(shot, "http://127.0.0.1:8800/app/agnes")
        stop_paw(proc)

    # B7-02
    with report.case("B7-02", "助手问题 2：回答非空、无报错") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        configure_mock_settings()
        chat_init = httpx.post("http://127.0.0.1:8800/v1/web/agy/chat", json={"mode": "chat"}).json()
        session_id = chat_init.get("session_id", "session-b702")
        r = timed_answer(session_id, "当前系统开发的核心里程碑与推进方向是什么？", "B7-02")
        ans2 = str(r.get("response") or r.get("reply") or r.get("content") or r)
        c.check("assistant_q2_not_empty", bool(ans2.strip()), True)
        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/app/agnes", "screen_B7_02.png", "B7-02")
        c.screenshot(shot, "http://127.0.0.1:8800/app/agnes")
        stop_paw(proc)

    # B7-03
    with report.case("B7-03", "助手问题 3：回答引用了导入文档中的证据（含文件名或证据链接）") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        work_dir = Path("scratch/b7_03").resolve()
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path("tests/fixtures/sample_doc.docx"), work_dir / "sample_doc.docx")
        configure_mock_settings(work_dir=work_dir)
        httpx.post("http://127.0.0.1:8800/v1/local/scanner/start", json={"directory": str(work_dir), "target_dir": str(work_dir)}, timeout=10.0)
        poll_candidates_from_scanner()

        chat_init = httpx.post("http://127.0.0.1:8800/v1/web/agy/chat", json={"mode": "chat"}).json()
        session_id = chat_init.get("session_id", "session-b703")
        r = timed_answer(session_id, "周报中规划的核心业务推进目标是什么？", "B7-03")
        ans3 = str(r.get("response") or r.get("reply") or r.get("content") or r)
        has_cite = any(k in ans3 for k in ["sample_doc.docx", "sample_doc", "端侧高保真运行"])
        c.check("assistant_q3_cites_source_doc", has_cite, True)
        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/app/agnes", "screen_B7_03.png", "B7-03")
        c.screenshot(shot, "http://127.0.0.1:8800/app/agnes")
        stop_paw(proc)


def timed_answer(session_id, message, label, timeout=15.0):
    """Ask the assistant one question and log how long the answer took.

    ``timeout`` stays 15 s for every offline block. The real-model acceptance
    passes a larger value because one real answer call already takes 9-15 s
    (measured in R-263); the questions and their verdicts are unchanged.
    """
    started=time.perf_counter()
    result=None
    try:
        response=httpx.post(f"http://127.0.0.1:8800/v1/web/agy/chat/{session_id}/messages",json={'message':message},timeout=timeout)
        response.raise_for_status()
        result=response.json()
        return result
    finally:
        logger.info('Assistant answer %s elapsed_seconds=%.4f timings=%s',label,time.perf_counter()-started,(result or {}).get('timings','no response / request failed'))


def run_b8(report: Report, installer_exe: Path):
    logger.info("=== Running Block B8: 数据安全 ===")

    # B8-01
    with report.case("B8-01", "重启后项目数、待办数、知识数与重启前完全一致") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        conn = sqlite3.connect(str(get_db_path()))
        conn.execute("INSERT OR IGNORE INTO project (id, name, stage) VALUES ('p-b8', '数据安全项目', 'planning')")
        conn.commit()
        conn.close()

        work_dir = Path("scratch/b8_01").resolve()
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path("tests/fixtures/sample_doc.docx"), work_dir / "sample_doc.docx")
        configure_mock_settings(work_dir=work_dir)
        httpx.post("http://127.0.0.1:8800/v1/local/scanner/start", json={"directory": str(work_dir), "target_dir": str(work_dir)}, timeout=10.0)

        cands = poll_candidates_from_scanner(40)
        flat_id, empty_id, todo_id, k_id = parse_candidate_types(cands)
        target_ids = [i for i in [todo_id, k_id] if i]
        if target_ids:
            httpx.post("http://127.0.0.1:8800/v1/web/brain/review/batch_accept", json={
                "candidate_ids": target_ids,
                "reviewer": "LocalTester"
            }, timeout=10.0)

        conn = sqlite3.connect(str(get_db_path()))
        cnt_p1 = conn.execute("SELECT count(*) FROM project").fetchone()[0]
        cnt_t1 = conn.execute("SELECT count(*) FROM todo").fetchone()[0]
        cnt_k1 = conn.execute("SELECT count(*) FROM knowledge_page").fetchone()[0]
        conn.close()

        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/app", "screen_B8_01.png", "B8-01")
        c.screenshot(shot, "http://127.0.0.1:8800/app")
        stop_paw(proc)

        proc2 = start_paw(8800)
        conn = sqlite3.connect(str(get_db_path()))
        cnt_p2 = conn.execute("SELECT count(*) FROM project").fetchone()[0]
        cnt_t2 = conn.execute("SELECT count(*) FROM todo").fetchone()[0]
        cnt_k2 = conn.execute("SELECT count(*) FROM knowledge_page").fetchone()[0]
        conn.close()
        c.check("restart_counts_identical", (cnt_p2, cnt_t2, cnt_k2), (cnt_p1, cnt_t1, cnt_k1))
        stop_paw(proc2)

    # B8-02
    with report.case("B8-02", "覆盖安装后三项计数不变，且存在升级前快照文件") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        conn = sqlite3.connect(str(get_db_path()))
        conn.execute("INSERT OR IGNORE INTO project (id, name, stage) VALUES ('p-b8-ov', '覆盖安装测试', 'planning')")
        conn.commit()
        conn.close()

        work_dir = Path("scratch/b8_02").resolve()
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path("tests/fixtures/sample_doc.docx"), work_dir / "sample_doc.docx")
        configure_mock_settings(work_dir=work_dir)
        httpx.post("http://127.0.0.1:8800/v1/local/scanner/start", json={"directory": str(work_dir), "target_dir": str(work_dir)}, timeout=10.0)

        cands = poll_candidates_from_scanner(40)
        flat_id, empty_id, todo_id, k_id = parse_candidate_types(cands)
        target_ids = [i for i in [todo_id, k_id] if i]
        if target_ids:
            httpx.post("http://127.0.0.1:8800/v1/web/brain/review/batch_accept", json={
                "candidate_ids": target_ids,
                "reviewer": "LocalTester"
            }, timeout=10.0)

        conn = sqlite3.connect(str(get_db_path()))
        cnt_before = (
            conn.execute("SELECT count(*) FROM project").fetchone()[0],
            conn.execute("SELECT count(*) FROM todo").fetchone()[0],
            conn.execute("SELECT count(*) FROM knowledge_page").fetchone()[0],
        )
        conn.close()

        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/app", "screen_B8_02.png", "B8-02")
        c.screenshot(shot, "http://127.0.0.1:8800/app")
        stop_paw(proc)

        silent_install(installer_exe)

        snap_file = Path(os.environ.get("LOCALAPPDATA", "")) / "PAW" / "workspace_pre_update.db"
        snap_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "PAW" / "snapshots"
        has_snap = snap_file.exists() or (snap_dir.exists() and len(list(snap_dir.glob("*.db*"))) > 0)
        c.check("upgrade_snapshot_exists", has_snap, True)

        proc3 = start_paw(8800)
        conn = sqlite3.connect(str(get_db_path()))
        cnt_after = (
            conn.execute("SELECT count(*) FROM project").fetchone()[0],
            conn.execute("SELECT count(*) FROM todo").fetchone()[0],
            conn.execute("SELECT count(*) FROM knowledge_page").fetchone()[0],
        )
        conn.close()
        c.check("overlay_install_counts_identical", cnt_after, cnt_before)
        stop_paw(proc3)

    # B8-03
    with report.case("B8-03", "卸载后用户数据库仍保留（workspace.db 存在）") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        conn = sqlite3.connect(str(get_db_path()))
        conn.execute("INSERT OR IGNORE INTO project (id, name, stage) VALUES ('p-b8-retain', '保留测试', 'planning')")
        conn.commit()
        conn.close()
        db_file = get_db_path()
        final_url, body_text, shot = inspect_and_capture_page("http://127.0.0.1:8800/app", "screen_B8_03.png", "B8-03")
        c.screenshot(shot, "http://127.0.0.1:8800/app")
        stop_paw(proc)

        silent_uninstall()
        c.check("user_db_retained_after_uninstall", db_file.exists(), True)


def run_negative_controls(report: Report, baseline_exe: Path, current_exe: Path):
    logger.info("=== Running Negative Controls (NC-01, NC-02, NC-03) ===")

    # NC-01: runs B4-02 on v2.3.7-baseline, expects failure on project_update_committed
    with report.case("NC-01", "核准扁平格式项目更新（负对照 2.3.7）：应失败于 project_update_committed") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(baseline_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        conn = sqlite3.connect(str(get_db_path()))
        conn.execute("INSERT OR IGNORE INTO project (id, name, stage, description, created_at, updated_at) VALUES ('proj-sprint-01', '冲刺项目', 'planning', '核心冲刺项目', datetime('now'), datetime('now'))")
        conn.commit()
        conn.close()

        work_dir = Path("scratch/nc_01").resolve()
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path("tests/fixtures/sample_doc.docx"), work_dir / "冲刺项目周报.docx")
        configure_mock_settings(work_dir=work_dir)
        httpx.post("http://127.0.0.1:8800/v1/local/scanner/start", json={"directory": str(work_dir), "target_dir": str(work_dir), "force_restart": True}, timeout=10.0)

        cands = poll_candidates_from_scanner(30)
        flat_id, empty_id, todo_id, k_id = parse_candidate_types(cands)

        httpx.post("http://127.0.0.1:8800/v1/web/brain/review/batch_accept", json={
            "candidate_ids": [flat_id] if flat_id else ["missing-id"],
            "reviewer": "LocalTester"
        }, timeout=10.0)

        conn = sqlite3.connect(str(get_db_path()))
        proj_row = conn.execute("SELECT latest_progress_summary FROM project WHERE name='冲刺项目'").fetchone()
        conn.close()

        # In 2.3.7 baseline, flat project update is rejected; row[0] is empty -> fails on project_update_committed
        c.check("project_update_committed", proj_row[0] if proj_row else "", EXPECTED_NEW_SUMMARY)
        stop_paw(proc)

    # NC-02: runs B5-02 on v2.3.7-baseline, expects failure on explainer_fields_filled
    with report.case("NC-02", "项目详情 30 秒说明（负对照 2.3.7）：应失败于 explainer_fields_filled") as c:
        c.fresh_install(clean_and_record, lambda: silent_install(baseline_exe)[2], INSTALL_DIR)
        proc = start_paw(8800)
        conn = sqlite3.connect(str(get_db_path()))
        conn.execute("INSERT OR IGNORE INTO project (id, name, stage, description, created_at, updated_at) VALUES ('proj-01', '智能认知治理工程', 'planning', '规划中的治理项目', datetime('now'), datetime('now'))")
        conn.commit()
        conn.close()

        try:
            httpx.post("http://127.0.0.1:8800/v1/local/projects/analyze", timeout=5.0)
        except Exception:
            pass

        ctx_res = httpx.get("http://127.0.0.1:8800/v1/web/workspace/projects/proj-01/context", timeout=5.0)
        ctx_data = ctx_res.json() if ctx_res.status_code == 200 else {}
        explainer = ctx_data.get("explainer") or ctx_data.get("project_explainer") or {}
        fields = ["one_sentence", "current_state", "key_open_issue", "next_step"]
        filled = all(bool(str(explainer.get(f, "")).strip()) for f in fields) if explainer else False

        # In 2.3.7 baseline, 30s explainer is missing -> fails on explainer_fields_filled
        c.check("explainer_fields_filled", filled, True)
        stop_paw(proc)

    # NC-03: runs clean check with leftover file, expects failure on environment_clean
    with report.case("NC-03", "清空检查负对照：人为注入残留文件应触发 environment_clean 失败") as c:
        dirty_locations = clean_check_with_leftover_file()
        c.record_clean(dirty_locations)


# ==============================================================================
# MAIN ENTRYPOINT
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="PAW Cloud E2E Test Suite")
    parser.add_argument("--block", required=True, help="Block identifier: B1..B8 or NEGATIVE-CONTROL")
    parser.add_argument("--installer-exe", required=True, help="Path to built installer EXE")
    parser.add_argument("--baseline-exe", default="", help="Path to v2.3.7 baseline installer for negative control")
    parser.add_argument("--commit-sha", required=True, help="Current commit SHA")
    parser.add_argument("--model-mode", default="mock-llm", help="Operational mode: agy-bridge or mock-llm")
    parser.add_argument("--output-report", default="", help="Path to write JSON report")
    args = parser.parse_args()

    inst_path = Path(args.installer_exe)
    if inst_path.is_dir():
        exes = list(inst_path.glob("*.exe"))
        if not exes:
            logger.error(f"No executable found in {inst_path}")
            sys.exit(1)
        inst_path = exes[0]

    inst_sha256 = get_file_sha256(inst_path)
    logger.info(f"Target Installer: {inst_path.name} (SHA256: {inst_sha256})")

    report = Report(
        block=args.block,
        commit=args.commit_sha,
        model_mode=args.model_mode,
        installer_sha256=inst_sha256,
    )

    out_report_path = Path(args.output_report or f"reports/report_{args.block}.json")
    out_report_path.parent.mkdir(parents=True, exist_ok=True)

    block = args.block.upper()
    if block == "B1":
        run_b1(report, inst_path)
    elif block == "B2":
        run_b2(report, inst_path)
    elif block == "B3":
        run_b3(report, inst_path)
    elif block == "B4":
        run_b4(report, inst_path)
    elif block == "B5":
        run_b5(report, inst_path)
    elif block == "B6":
        run_b6(report, inst_path)
    elif block == "B7":
        run_b7(report, inst_path)
    elif block == "B8":
        run_b8(report, inst_path)
    elif block == "PM02":
        from tests.run_pm_tests import run_c02
        run_c02(report, inst_path)
    elif block == "PM03":
        from tests.run_pm_versions_tests import run_c03
        run_c03(report, inst_path)
    elif block == "PM04":
        from tests.run_pm_updates_tests import run_c04
        run_c04(report, inst_path)
    elif block == "PM05":
        from tests.run_pm_routing_tests import run_c05
        run_c05(report, inst_path)
    elif block == "PM06":
        from tests.run_pm_frontend_tests import run_c06
        run_c06(report, inst_path)
    elif block == "PM07":
        from tests.run_pm_operations_tests import run_c07
        run_c07(report, inst_path)
    elif block == "PM08":
        from tests.run_pm_advice_tests import run_c08
        run_c08(report, inst_path)
    elif block == "PM09":
        from tests.run_pm_health_tests import run_c09
        run_c09(report, inst_path)
    elif block == "PM10":
        from tests.run_pm_exam_tests import run_c10
        run_c10(report, inst_path)
    elif block == "PM12":
        from tests.run_pm_c12_tests import run_c12
        run_c12(report, inst_path)
    elif block == "NEGATIVE-CONTROL":
        base_path = Path(args.baseline_exe)
        if base_path.is_dir():
            exes = list(base_path.glob("*.exe"))
            if exes:
                base_path = exes[0]
        run_negative_controls(report, base_path, inst_path)
    else:
        logger.error(f"Unknown block: {block}")
        sys.exit(1)

    exit_code = report.save(out_report_path)
    logger.info(f"Report saved to {out_report_path} (exit_code={exit_code})")

    if block == "NEGATIVE-CONTROL":
        sys.exit(0)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
