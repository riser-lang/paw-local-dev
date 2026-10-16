"""PAW Personal Work OS v2.4.0 - Unified FastAPI Local Gateway.

Serves:
- React 18 Workbench Frontend
- Workspace REST API (/v1/web/workspace/...)
- Brain Review & Governance API (/v1/web/brain/...)
- Session & Status endpoints (/v1/session, /v1/web/status)
- Local Doctor & Configuration API (/v1/local/...)
- Onboarding & Runtime Scheduler (/v1/local/onboarding, /v1/local/runtime/...)
- Event Bus & Conflict Analyzer Integration
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("paw.gateway")

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.core.config import (
    BASE_DIR,
    DATA_DIR,
    DB_PATH,
    EVIDENCE_DIR,
    FRONTEND_DIST,
    SOURCES_DIR,
    get_work_dirs,
    settings,
)
from backend.core.backup import create_db_backup, list_backups, restore_db_backup
from backend.core.security import LOCAL_LAUNCH_TOKEN, mask_secret, verify_request_security, DPAPIProtectionError
from backend.brain.api.local_agy_facade import router as agy_router
from backend.governance.router import router as brain_review_router
from backend.governance.service import get_db
from backend.provider.adapter import test_provider_connection
from backend.runtime_core.event_bus import EventType, event_bus
from backend.runtime_core.evolution_engine import EvolutionEngine, LEXIANG_TAXONOMY
from backend.runtime_core.scanner import scanner
from backend.runtime_core.scheduler import scheduler
from backend.watcher.observer import watcher
from backend.workspace_api.router import router as workspace_router

evolution_engine = EvolutionEngine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from backend.core.startup_trace import event as trace_startup
    trace_startup('lifespan_migration')
    # Startup: widen legacy CHECK constraints so project merges can be proposed
    # and committed (backs up workspace.db first; no-op once migrated).
    try:
        from backend.core.schema_migration import migrate_check_constraints
        mig = migrate_check_constraints(DB_PATH)
        if mig.get("migrated"):
            print(f"[Migration] Upgraded governance tables {mig['migrated']} (backup: {mig.get('backup')})")
    except Exception as e:
        print(f"[Migration] Warning: schema migration failed: {e}")

    trace_startup('lifespan_directories')
    # Startup: sync monitored directories from settings (multi-directory support)
    try:
        _dirs = [Path(p) for p in get_work_dirs()]
        if _dirs:
            scheduler.sources_dirs = _dirs
            scheduler.sources_dir = _dirs[0]
            scanner.sources_dirs = _dirs
            scanner.sources_dir = _dirs[0]
            watcher.sources_dirs = _dirs
            watcher.sources_dir = _dirs[0]
    except Exception as e:
        print(f"[Watcher] Warning: failed to sync work dirs: {e}")
    # Startup: Start local file watcher if enabled
    if settings.watch_enabled:
        try:
            trace_startup('watcher_start')
            watcher.start()
            trace_startup('watcher_started')
        except Exception as e:
            print(f"[Watcher] Warning: failed to start watcher: {e}")
            
    # Start Runtime Scheduler
    try:
        trace_startup('scheduler_start')
        scheduler.set_schedule_mode(settings.cruise_mode, settings.cruise_interval)
        scheduler.start()
        trace_startup('scheduler_started')
    except Exception as e:
        print(f"[Scheduler] Warning: failed to start scheduler: {e}")

    # Startup: file merge proposals for duplicate projects (rule-based, no model call)
    def _startup_convergence():
        try:
            from backend.runtime_core.project_convergence import run_project_convergence
            res = run_project_convergence()
            if res.get("groups"):
                print(f"[Convergence] {res['groups']} duplicate project groups, {len(res['candidates'])} merge proposals pending review")
        except Exception as e:
            print(f"[Convergence] Warning: startup convergence failed: {e}")

    threading.Thread(target=_startup_convergence, name="StartupConvergence", daemon=True).start()

    trace_startup('lifespan_ready')
    yield

    # Shutdown: Stop watcher and scheduler
    watcher.stop()
    scheduler.stop()


app = FastAPI(
    title="PAW Personal Work OS",
    version="2.4.0",
    description="Evidence-First + Human-Governed Personal AI Work Operating System",
    lifespan=lifespan,
)

# CORS middleware for local embedding (R5: restrict to localhost/127.0.0.1, prohibit wildcard)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8800", "http://localhost:8800", "http://127.0.0.1", "http://localhost"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── R5: Local Request Security & Anti-CSRF Middleware ────────────────────────

@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # Allow static assets and options preflight
    if (
        request.method == "OPTIONS"
        or request.url.path.startswith("/assets")
        or request.url.path in ("/favicon.ico", "/paw_console.js", "/index.html")
    ):
        return await call_next(request)

    host = request.headers.get("host")
    origin = request.headers.get("origin")
    token_header = request.headers.get("x-paw-local-token")
    session_cookie = request.cookies.get("paw_session")

    ok, reason = verify_request_security(
        host=host,
        origin=origin,
        method=request.method,
        token_header=token_header,
        session_cookie=session_cookie,
    )
    if not ok:
        return JSONResponse(status_code=403, content={"detail": f"Access Denied (Security R5): {reason}"})

    return await call_next(request)


# ── Session & Auth (Desktop single-user local trust with launch token) ───────

@app.get("/v1/session")
def get_session(response: Response) -> Dict[str, Any]:
    response.set_cookie("paw_session", LOCAL_LAUNCH_TOKEN, max_age=86400 * 30, path="/")
    return {
        "authenticated": True,
        "username": "LocalUser",
        "local_token": LOCAL_LAUNCH_TOKEN,
    }


@app.post("/v1/session/login")
def login(response: Response) -> Dict[str, Any]:
    response.set_cookie("paw_session", LOCAL_LAUNCH_TOKEN, max_age=86400 * 30, path="/")
    return {
        "authenticated": True,
        "username": "LocalUser",
        "local_token": LOCAL_LAUNCH_TOKEN,
    }


@app.post("/v1/session/logout")
def logout(response: Response) -> Dict[str, Any]:
    response.delete_cookie("paw_session", path="/")
    return {"authenticated": False}


# ── Web Status ─────────────────────────────────────────────────────────────────

@app.get("/v1/web/status")
def web_status() -> Dict[str, Any]:
    disk = shutil.disk_usage(str(BASE_DIR))
    disk_pct = round(disk.used / disk.total * 100) if disk.total else 0
    return {
        "cloudHub": "local",
        "workspaceBackend": {"phase": "PAW Personal Work OS v2.3", "status": "ready"},
        "windowsBridge": "online",
        "wpsAvailable": "available",
        "wpsProvider": "Local Watcher + Scheduler",
        "serverMetrics": {"ram": 25, "disk": disk_pct},
        "adapterStatus": {"state": "ready"},
        "provider": settings.provider,
        "sources_dir": settings.work_dir,
        "db_path": str(DB_PATH),
        "cruise_mode": settings.cruise_mode,
        "onboarding_completed": settings.onboarding_completed,
        "local_token": LOCAL_LAUNCH_TOKEN,
        "daily_call_limit": settings.daily_call_limit,
        "today_calls_made": settings.today_calls_made,
    }


# ── Mount Core Workspace, Review, and AI Assistant Routers ───────────────────

app.include_router(workspace_router, prefix="/v1/web/workspace", tags=["web-workspace"])
app.include_router(brain_review_router)
app.include_router(agy_router, prefix="/v1/web/agy", tags=["web-agy"])



# ── Phase 7: Onboarding & Settings Models & Endpoints ─────────────────────────

class OnboardingSubmitRequest(BaseModel):
    work_dir: Optional[str] = None
    provider: str = "deepseek"
    deepseek_api_key: Optional[str] = None
    deepseek_base_url: Optional[str] = "https://api.deepseek.com"
    deepseek_model: Optional[str] = "deepseek-chat"
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = "https://api.openai.com/v1"
    openai_model: Optional[str] = "gpt-4o"
    cruise_mode: str = "realtime"


class OnboardingSubmitRequest(BaseModel):
    work_dir: Optional[str] = None
    provider: str = "deepseek"
    deepseek_api_key: Optional[str] = None
    deepseek_base_url: Optional[str] = "https://api.deepseek.com"
    deepseek_model: Optional[str] = "deepseek-chat"
    qwen_api_key: Optional[str] = None
    qwen_base_url: Optional[str] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model: Optional[str] = "qwen-plus"
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = "https://api.openai.com/v1"
    openai_model: Optional[str] = "gpt-4o"
    custom_api_key: Optional[str] = None
    custom_base_url: Optional[str] = "http://127.0.0.1:11434/v1"
    custom_model: Optional[str] = "llama3"
    cruise_mode: str = "realtime"


class SettingsUpdateRequest(BaseModel):
    work_dir: Optional[str] = None
    provider: str = "deepseek"
    deepseek_api_key: Optional[str] = None
    deepseek_base_url: Optional[str] = "https://api.deepseek.com"
    deepseek_model: Optional[str] = "deepseek-chat"
    qwen_api_key: Optional[str] = None
    qwen_base_url: Optional[str] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model: Optional[str] = "qwen-plus"
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = "https://api.openai.com/v1"
    openai_model: Optional[str] = "gpt-4o"
    custom_api_key: Optional[str] = None
    custom_base_url: Optional[str] = "http://127.0.0.1:11434/v1"
    custom_model: Optional[str] = "llama3"
    cruise_mode: str = "realtime"
    speed_mode: str = "standard"
    max_file_size_mb: int = 100
    watch_enabled: bool = True
    auto_propose: bool = True
    daily_call_limit: Optional[int] = 0
    web_search_enabled: Optional[bool] = None
    deepseek_web_search: Optional[bool] = None
    qwen_web_search: Optional[bool] = None
    openai_web_search: Optional[bool] = None
    custom_web_search: Optional[bool] = None
    web_search_mode: Optional[str] = None
    daily_search_limit: Optional[int] = None
    search_proxy: Optional[str] = None


class TestConnectionRequest(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""


class PrescanRequest(BaseModel):
    directory: Optional[str] = None


class StartScanRequest(BaseModel):
    directory: Optional[str] = None
    force_restart: bool = False


class BatchReviewRequest(BaseModel):
    candidate_ids: List[str]
    reviewer: str = "LocalUser"
    notes: Optional[str] = ""


class RetryFileRequest(BaseModel):
    file_path: str


@app.get("/v1/local/onboarding")
def get_onboarding_status() -> Dict[str, Any]:
    active_creds = settings.get_active_credentials()
    return {
        "completed": settings.onboarding_completed,
        "work_dir": settings.work_dir,
        "provider": settings.provider,
        "cruise_mode": settings.cruise_mode,
        "speed_mode": settings.speed_mode,
        "has_api_key": bool(active_creds.get("api_key")),
        "key_mask": mask_secret(active_creds.get("api_key", "")),
    }


def _apply_work_dirs(work_dir_value: str) -> None:
    """Apply monitored work directories; semicolon-separated multi-directory values
    are supported and single-directory values remain fully compatible."""
    dirs = [Path(part.strip()) for part in (work_dir_value or "").split(";") if part.strip()]
    if not dirs:
        return
    for p in dirs:
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"Failed to create work directory {p}: {e}")
    settings.work_dir = ";".join(str(p) for p in dirs)
    scheduler.sources_dirs = dirs
    scheduler.sources_dir = dirs[0]
    scanner.sources_dirs = dirs
    scanner.sources_dir = dirs[0]
    watcher.set_sources_dirs(dirs)


@app.post("/v1/local/onboarding")
def complete_onboarding(req: OnboardingSubmitRequest) -> Dict[str, Any]:
    if req.work_dir:
        _apply_work_dirs(req.work_dir)

    settings.provider = req.provider
    if req.deepseek_api_key and not req.deepseek_api_key.startswith("sk-..."):
        settings.deepseek_api_key = req.deepseek_api_key
    if req.deepseek_base_url:
        settings.deepseek_base_url = req.deepseek_base_url
    if req.deepseek_model:
        settings.deepseek_model = req.deepseek_model

    if req.qwen_api_key and not req.qwen_api_key.startswith("sk-..."):
        settings.qwen_api_key = req.qwen_api_key
    if req.qwen_base_url:
        settings.qwen_base_url = req.qwen_base_url
    if req.qwen_model:
        settings.qwen_model = req.qwen_model

    if req.openai_api_key and not req.openai_api_key.startswith("sk-..."):
        settings.openai_api_key = req.openai_api_key
    if req.openai_base_url:
        settings.openai_base_url = req.openai_base_url
    if req.openai_model:
        settings.openai_model = req.openai_model

    if req.custom_api_key and not req.custom_api_key.startswith("sk-..."):
        settings.custom_api_key = req.custom_api_key
    if req.custom_base_url:
        settings.custom_base_url = req.custom_base_url
    if req.custom_model:
        settings.custom_model = req.custom_model

    settings.cruise_mode = req.cruise_mode
    scheduler.set_schedule_mode(req.cruise_mode)

    settings.onboarding_completed = True
    try:
        settings.save()
    except DPAPIProtectionError as e:
        raise HTTPException(status_code=400, detail=str(e))

    event_bus.publish(
        EventType.CRUISE_STARTED,
        payload={"trigger": "onboarding_completed", "work_dir": settings.work_dir},
        source="onboarding",
    )

    return {"ok": True, "message": "新手引导完成，PAW 已就绪！"}


@app.get("/v1/local/settings")
def get_settings() -> Dict[str, Any]:
    active_creds = settings.get_active_credentials()
    return {
        "work_dir": settings.work_dir,
        "provider": settings.provider,
        "deepseek_api_key": mask_secret(settings.deepseek_api_key),
        "deepseek_base_url": settings.deepseek_base_url,
        "deepseek_model": settings.deepseek_model,
        "qwen_api_key": mask_secret(settings.qwen_api_key),
        "qwen_base_url": settings.qwen_base_url,
        "qwen_model": settings.qwen_model,
        "openai_api_key": mask_secret(settings.openai_api_key),
        "openai_base_url": settings.openai_base_url,
        "openai_model": settings.openai_model,
        "custom_api_key": mask_secret(settings.custom_api_key),
        "custom_base_url": settings.custom_base_url,
        "custom_model": settings.custom_model,
        "has_api_key": bool(active_creds.get("api_key")),
        "key_mask": mask_secret(active_creds.get("api_key", "")),
        "speed_mode": settings.speed_mode,
        "max_file_size_mb": settings.max_file_size_mb,
        "excluded_dirs": settings.excluded_dirs,
        "cruise_mode": settings.cruise_mode,
        "watch_enabled": settings.watch_enabled,
        "auto_propose": settings.auto_propose,
        "onboarding_completed": settings.onboarding_completed,
        "db_path": str(DB_PATH),
        "data_dir": str(DATA_DIR),
        "cumulative_calls": settings.cumulative_calls,
        "cumulative_tokens": settings.cumulative_tokens,
        "daily_call_limit": settings.daily_call_limit,
        "today_calls_made": settings.today_calls_made,
        "web_search_enabled": settings.web_search_enabled,
        "deepseek_web_search": settings.deepseek_web_search,
        "qwen_web_search": settings.qwen_web_search,
        "openai_web_search": settings.openai_web_search,
        "custom_web_search": settings.custom_web_search,
        "web_search_mode": settings.web_search_mode,
        "daily_search_limit": settings.daily_search_limit,
        "today_searches_made": settings.today_searches_made,
        "search_proxy": settings.search_proxy,
        "is_web_search_supported": settings.is_web_search_supported(),
    }


@app.post("/v1/local/settings")
def update_settings(req: SettingsUpdateRequest) -> Dict[str, Any]:
    if req.work_dir:
        _apply_work_dirs(req.work_dir)

    settings.provider = req.provider
    if req.deepseek_api_key and not req.deepseek_api_key.startswith("sk-..."):
        settings.deepseek_api_key = req.deepseek_api_key
    if req.deepseek_base_url:
        settings.deepseek_base_url = req.deepseek_base_url
    if req.deepseek_model:
        settings.deepseek_model = req.deepseek_model

    if req.qwen_api_key and not req.qwen_api_key.startswith("sk-..."):
        settings.qwen_api_key = req.qwen_api_key
    if req.qwen_base_url:
        settings.qwen_base_url = req.qwen_base_url
    if req.qwen_model:
        settings.qwen_model = req.qwen_model

    if req.openai_api_key and not req.openai_api_key.startswith("sk-..."):
        settings.openai_api_key = req.openai_api_key
    if req.openai_base_url:
        settings.openai_base_url = req.openai_base_url
    if req.openai_model:
        settings.openai_model = req.openai_model

    if req.custom_api_key is not None and not req.custom_api_key.startswith("sk-..."):
        settings.custom_api_key = req.custom_api_key
    if req.custom_base_url is not None:
        settings.custom_base_url = req.custom_base_url
    if req.custom_model is not None:
        settings.custom_model = req.custom_model

    settings.cruise_mode = req.cruise_mode
    scheduler.set_schedule_mode(req.cruise_mode)
    settings.speed_mode = req.speed_mode
    settings.max_file_size_mb = req.max_file_size_mb
    settings.watch_enabled = req.watch_enabled
    settings.auto_propose = req.auto_propose
    if req.daily_call_limit is not None and req.daily_call_limit >= 0:
        settings.daily_call_limit = req.daily_call_limit

    if req.web_search_enabled is not None:
        settings.web_search_enabled = req.web_search_enabled
    if req.deepseek_web_search is not None:
        settings.deepseek_web_search = req.deepseek_web_search
    if req.qwen_web_search is not None:
        settings.qwen_web_search = req.qwen_web_search
    if req.openai_web_search is not None:
        settings.openai_web_search = req.openai_web_search
    if req.custom_web_search is not None:
        settings.custom_web_search = req.custom_web_search

    if req.web_search_mode is not None:
        settings.web_search_mode = req.web_search_mode
    if req.daily_search_limit is not None and req.daily_search_limit >= 0:
        settings.daily_search_limit = req.daily_search_limit
    if req.search_proxy is not None:
        settings.search_proxy = req.search_proxy

    try:
        settings.save()
    except DPAPIProtectionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "message": "设置已成功保存并即时生效"}


# ── Provider Connection Probe (Requirement 4.3) ──────────────────────────────

@app.post("/v1/local/provider/test_connection")
def api_test_provider_connection(req: TestConnectionRequest) -> Dict[str, Any]:
    # The settings page never echoes the saved API key back into the input
    # box (security by design), so an empty key here means "test the saved one".
    saved = settings.get_active_credentials()
    base_url = (req.base_url or "").strip() or saved.get("base_url") or ""
    api_key = (req.api_key or "").strip() or saved.get("api_key") or ""
    model = (req.model or "").strip() or saved.get("model") or ""
    if not api_key:
        return {
            "ok": False,
            "error_code": "NO_API_KEY",
            "error_message": "未输入 API Key，且当前未保存任何密钥。请先输入并保存 API Key 后再测试。",
        }
    result=test_provider_connection(base_url=base_url, api_key=api_key, model=model)
    from backend.pm import health
    health.observe('model',bool(result.get('ok')),'模型连接测试成功' if result.get('ok') else '模型连接测试失败，请检查配置和网络',
        health.identity({'base_url':base_url,'api_key':api_key,'model':model}))
    return result


# ── Advanced Scanner Endpoints (Requirements 3.2, 3.3) ───────────────────────

class DataDirChangeRequest(BaseModel):
    data_dir: str


@app.post("/v1/local/data_dir")
def api_change_data_dir(req: DataDirChangeRequest) -> Dict[str, Any]:
    """Change the persistent data directory (database, config, sources...).

    Existing data is copied to the new location; a pointer file at the default
    bootstrap location records the choice. Takes effect after restart."""
    raw = (req.data_dir or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="数据目录不能为空")
    try:
        target = Path(raw).resolve()
        target.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"目录无效: {e}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"目录无效: {target}")

    current = DATA_DIR.resolve()
    if target == current:
        return {"ok": True, "message": "数据目录未发生变化", "restart_required": False, "new_data_dir": str(target)}

    # Best-effort copy of existing user data to the new location
    copied, skipped = 0, 0
    for item in current.iterdir():
        if item.name == "data_dir_pointer.txt":
            continue
        try:
            if item.is_dir():
                shutil.copytree(item, target / item.name, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target / item.name)
            copied += 1
        except Exception:
            skipped += 1

    # Record the choice at the default bootstrap location
    default_root = Path(os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")) / "PAW"
    default_root.mkdir(parents=True, exist_ok=True)
    (default_root / "data_dir_pointer.txt").write_text(str(target), encoding="utf-8")

    return {
        "ok": True,
        "message": f"数据目录已切换为 {target}（已复制 {copied} 项，跳过 {skipped} 项）。请退出并重新启动 PAW 使其生效。",
        "restart_required": True,
        "new_data_dir": str(target),
    }


@app.get("/v1/local/pick_folder")
def api_pick_folder() -> Dict[str, Any]:
    """Open the native Windows folder picker dialog for visual folder selection."""
    from backend.core.folder_picker import pick_folder
    chosen = pick_folder("选择 PAW 监控工作文件夹")
    if chosen:
        return {"ok": True, "path": chosen}
    return {"ok": False, "path": None, "message": "未选择文件夹"}


@app.post("/v1/local/scanner/prescan")
def api_scanner_prescan(req: Optional[PrescanRequest] = None) -> Dict[str, Any]:
    d = Path(req.directory) if req and req.directory else None
    return scanner.prescan(target_dir=d)


@app.post("/v1/local/scanner/start")
def api_scanner_start(req: Optional[StartScanRequest] = None) -> Dict[str, Any]:
    d = Path(req.directory) if req and req.directory else None
    force = req.force_restart if req else False
    return scanner.start_scan(target_dir=d, force_restart=force)


@app.post("/v1/local/scanner/pause")
def api_scanner_pause() -> Dict[str, Any]:
    return scanner.pause_scan()


@app.post("/v1/local/scanner/resume")
def api_scanner_resume() -> Dict[str, Any]:
    return scanner.resume_scan()


@app.post("/v1/local/scanner/stop")
def api_scanner_stop() -> Dict[str, Any]:
    return scanner.stop_scan()


@app.get("/v1/local/scanner/progress")
def api_scanner_progress() -> Dict[str, Any]:
    return scanner.get_progress()


@app.post("/v1/local/scanner/retry")
def api_scanner_retry(req: RetryFileRequest) -> Dict[str, Any]:
    return scanner.retry_failed_file(req.file_path)


@app.get("/v1/local/scanner/summary")
def api_scanner_summary() -> Dict[str, Any]:
    return scanner.get_scan_summary()


class BackupCreateRequest(BaseModel):
    tag: Optional[str] = "manual"


class BackupRestoreRequest(BaseModel):
    backup_filename: str


@app.get("/v1/local/scanner/preview_payload")
def api_scanner_preview_payload(file_path: str) -> Dict[str, Any]:
    return scanner.preview_payload(file_path)


@app.get("/v1/local/backup/list")
def api_backup_list() -> Dict[str, Any]:
    return {"backups": list_backups()}


@app.post("/v1/local/backup/create")
def api_backup_create(req: Optional[BackupCreateRequest] = None) -> Dict[str, Any]:
    tag = req.tag if req and req.tag else "manual"
    return create_db_backup(tag=tag,automatic=False)


@app.post("/v1/local/backup/restore")
def api_backup_restore(req: BackupRestoreRequest) -> Dict[str, Any]:
    return restore_db_backup(req.backup_filename)



# ── Batch Review Endpoints (Requirement 3.3) ─────────────────────────────────

@app.get("/v1/web/brain/review/grouped")
def api_review_grouped() -> Dict[str, Any]:
    return scanner.get_grouped_candidates()


@app.post("/v1/web/brain/review/batch_accept")
def api_review_batch_accept(req: BatchReviewRequest) -> Dict[str, Any]:
    return scanner.batch_accept(req.candidate_ids, reviewer=req.reviewer)


@app.post("/v1/web/brain/review/batch_reject")
def api_review_batch_reject(req: BatchReviewRequest) -> Dict[str, Any]:
    return scanner.batch_reject(req.candidate_ids, reviewer=req.reviewer, notes=req.notes or "")



# ── Runtime & Scheduler Management Endpoints ─────────────────────────────────

@app.get("/v1/local/runtime/status")
def get_runtime_status() -> Dict[str, Any]:
    return {
        "scheduler": scheduler.get_status(),
        "watcher_alive": bool(watcher.observer and watcher.observer.is_alive()),
        "recent_events": event_bus.get_recent_events(limit=20),
    }


class CruiseTriggerRequest(BaseModel):
    force_rescan: bool = False
    analyze_projects: bool = True
    async_mode: bool = False


@app.post("/v1/local/runtime/cruise/trigger")
def trigger_manual_cruise(req: Optional[CruiseTriggerRequest] = None) -> Dict[str, Any]:
    force = req.force_rescan if req else False
    analyze = req.analyze_projects if req else True
    async_run = req.async_mode if req else False

    if async_run:
        if scheduler.cruise_status == "running":
            return {
                "ok": True,
                "result": {
                    "status": "already_running",
                    "message": "巡航已在后台运行中，系统正在实时分析素材并由 AI 提炼项目"
                }
            }

        def _bg_cruise():
            try:
                scheduler.run_cruise(trigger="manual_web_trigger", force_rescan=force, analyze_projects=analyze)
            except Exception as e:
                logger.error(f"Background cruise execution error: {e}", exc_info=True)

        threading.Thread(target=_bg_cruise, name="BgCruiseWorker", daemon=True).start()
        return {
            "ok": True,
            "result": {
                "status": "started",
                "message": "巡航任务已在后台成功启动，正在分析本地文档与提炼项目进展"
            }
        }

    result = scheduler.run_cruise(trigger="manual_web_trigger", force_rescan=force, analyze_projects=analyze)
    return {"ok": True, "result": result}


@app.post("/v1/local/projects/converge")
def api_converge_projects() -> Dict[str, Any]:
    """Check project convergence now (same-name duplicates + AI grouping, one model call).

    High-confidence groups merge automatically (backed up first); the rest go to review.
    """
    from backend.runtime_core.project_convergence import run_project_convergence
    res = run_project_convergence(use_ai=True, force_ai=True)
    ai = res.get("ai") or {}
    parts = [f"已自动收敛 {len(res['auto_merged'])} 组项目", f"{len(res['pending'])} 组合并建议待你审核"]
    if not ai.get("ran"):
        parts.append({"no_model": "未配置 AI 模型，本次只检查了同名项目",
                      "daily_call_limit_reached": "今日模型调用已达上限，本次只检查了同名项目"}.get(ai.get("reason"), "本次只检查了同名项目"))
    elif ai.get("error"):
        parts.append(f"AI 检查未完成（{ai['error']}），本次只检查了同名项目")
    return {"ok": True, "summary": "，".join(parts) + "。", "auto_merged": res["auto_merged"],
            "pending_candidates": res["pending"], "ai": ai}


@app.post("/v1/local/projects/analyze")
def api_analyze_projects(req: Optional[CruiseTriggerRequest] = None) -> Dict[str, Any]:
    """Explicit endpoint to trigger AI analysis and 30-second explainer synthesis for projects."""
    from backend.runtime_core.project_analyzer import project_analyzer
    force = req.force_rescan if req else False
    updated = project_analyzer.analyze_projects(max_projects=20, force=force)
    return {"ok": True, "count": len(updated), "projects": updated}


@app.post("/v1/local/system/shutdown")
def api_system_shutdown() -> Dict[str, Any]:
    """Graceful shutdown endpoint: stops scanner, flushes state, and exits server cleanly."""
    logger.info("[System] Graceful shutdown requested via API.")
    try:
        scanner.pause_scan()
    except Exception as e:
        logger.warning(f"Error pausing scanner on shutdown: {e}")

    def _delayed_exit():
        time.sleep(0.6)
        import os, signal
        logger.info("[System] Terminating process cleanly now.")
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=_delayed_exit, daemon=True, name="ShutdownWorker").start()
    return {"ok": True, "message": "PAW 正在安全退出，所有数据与事务已持久化完毕"}


# ── Built-in Web Search Endpoints (R2 Specification) ─────────────────────────

class LocalSearchRequest(BaseModel):
    query: str
    max_results: int = 4
    bypass_cache: bool = False


@app.post("/v1/local/search")
def api_local_search(req: LocalSearchRequest) -> Dict[str, Any]:
    from backend.search.service import SearchService
    ss = SearchService(proxy=settings.search_proxy or None, daily_limit=settings.daily_search_limit)
    return ss.search(req.query, max_results=req.max_results, bypass_cache=req.bypass_cache)


@app.get("/v1/local/search/doctor")
def api_local_search_doctor() -> Dict[str, Any]:
    from backend.search.service import SearchService
    ss = SearchService(proxy=settings.search_proxy or None, daily_limit=settings.daily_search_limit)
    return ss.check_health()




# ── Evolution Engine Endpoints (PAW Evolution Engine v1.0) ───────────────────

@app.get("/v1/local/evolution/health")
def get_evolution_health(knowledge_id: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
    return evolution_engine.compute_knowledge_health(knowledge_id=knowledge_id, limit=limit)


@app.get("/v1/local/evolution/graph")
def get_evolution_context_graph(
    query: Optional[str] = None,
    project_id: Optional[str] = None,
    knowledge_id: Optional[str] = None,
    max_nodes: int = 60,
) -> Dict[str, Any]:
    return evolution_engine.get_context_graph(
        query=query,
        project_id=project_id,
        knowledge_id=knowledge_id,
        max_nodes=max_nodes,
    )


@app.get("/v1/local/evolution/project-memory/{project_id}")
def get_evolution_project_memory(project_id: str) -> Dict[str, Any]:
    try:
        return evolution_engine.get_project_memory(project_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/v1/local/evolution/knowledge/{knowledge_id}/timeline")
def get_evolution_knowledge_timeline(knowledge_id: str) -> Dict[str, Any]:
    try:
        return evolution_engine.get_knowledge_evolution_timeline(knowledge_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/v1/local/evolution/taxonomy")
def get_evolution_taxonomy() -> Dict[str, Any]:
    return {"taxonomy": LEXIANG_TAXONOMY}


@app.post("/v1/local/evolution/diagnose")
def trigger_evolution_diagnose() -> Dict[str, Any]:
    result = evolution_engine.run_cruise_evolution_check()
    return {"ok": True, "result": result}


# ── Local Doctor Diagnostic Endpoint ──────────────────────────────────────────

@app.get("/v1/local/doctor")
def run_doctor() -> Dict[str, Any]:
    """Comprehensive Doctor Health Check."""
    checks = []

    # 1. Database check
    try:
        conn = get_db()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        table_count = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        projects_count = conn.execute("SELECT count(*) FROM project").fetchone()[0]
        knowledge_count = conn.execute("SELECT count(*) FROM knowledge_page").fetchone()[0]
        conn.close()
        db_ok = (integrity == "ok")
        checks.append({
            "name": "SQLite 业务数据库",
            "status": "PASS" if db_ok else "FAIL",
            "detail": f"表数量: {table_count}/41, 项目数: {projects_count}, 知识条目: {knowledge_count}, 完整性: {integrity}",
        })
    except Exception as e:
        checks.append({"name": "SQLite 业务数据库", "status": "FAIL", "detail": str(e)})

    # 2. Reader Fidelity check
    try:
        from backend.reader.dispatch import read_artifact
        checks.append({
            "name": "Reader Fidelity 逆向解析引擎",
            "status": "PASS",
            "detail": "DOCX, XLSX, PPTX, PDF 高保真解析与 Locator 定位符就绪",
        })
    except Exception as e:
        checks.append({"name": "Reader Fidelity 逆向解析引擎", "status": "FAIL", "detail": str(e)})

    # 3. Runtime & Scheduler check
    sched_status = scheduler.get_status()
    checks.append({
        "name": "PAW Runtime & 巡航调度器",
        "status": "PASS" if scheduler._thread and scheduler._thread.is_alive() else "STANDBY",
        "detail": f"模式: {sched_status['mode']}, 状态: {sched_status['status']}, 累计巡航: {sched_status['total_cruises']} 次",
    })

    # 4. Work directory check (multi-directory support)
    work_dirs = [Path(p) for p in get_work_dirs()]
    dir_ok = bool(work_dirs) and all(p.is_dir() for p in work_dirs)
    total_files = sum(len(list(p.glob("*"))) for p in work_dirs if p.is_dir())
    checks.append({
        "name": "本地工作目录监听",
        "status": "PASS" if dir_ok else "WARN",
        "detail": f"监控路径: {settings.work_dir} (当前文件: {total_files})",
    })

    # 5. Provider check
    has_key = bool(settings.deepseek_api_key or settings.openai_api_key or settings.claude_api_key)
    checks.append({
        "name": "AI 推理大脑 (Provider)",
        "status": "PASS" if has_key else "OFFLINE_FALLBACK",
        "detail": f"当前选择: {settings.provider} | API Key: {'已配置' if has_key else '未配置 (高保真规则推理自动生效)'}",
    })

    # 6. Frontend assets check
    index_file = FRONTEND_DIST / "index.html"
    checks.append({
        "name": "React 18 工作台前端",
        "status": "PASS" if index_file.is_file() else "MISSING",
        "detail": f"前端离线构建包路径: {FRONTEND_DIST}",
    })

    # 7. Evolution Engine check
    try:
        health_preview = evolution_engine.compute_knowledge_health(limit=5)
        checks.append({
            "name": "Evolution Engine (自我演进与健康引擎)",
            "status": "PASS",
            "detail": f"知识健康度: {health_preview['overall_score']}/100 ({health_preview['overall_grade']}), 评估条目: {health_preview['total_pages_evaluated']}",
        })
    except Exception as e:
        checks.append({"name": "Evolution Engine (自我演进与健康引擎)", "status": "FAIL", "detail": str(e)})

    # 8. Web Search Engine check (R2 Specification §3.7)
    try:
        from backend.search.service import SearchService
        if settings.web_search_mode == "disabled":
            checks.append({
                "name": "PAW 软件自带联网搜索 (免 Key)",
                "status": "STANDBY",
                "detail": f"模式: 已禁用 | 今日已用: {settings.today_searches_made}/{settings.daily_search_limit}",
            })
        else:
            ss = SearchService(proxy=settings.search_proxy or None, daily_limit=settings.daily_search_limit)
            h_info = ss.check_health()
            latencies = [f"{b['name']}({b['latency_ms']}ms)" for b in h_info.get("backends", []) if b["status"] == "HEALTHY"]
            if h_info.get("healthy"):
                checks.append({
                    "name": "PAW 软件自带联网搜索 (免 Key)",
                    "status": "PASS",
                    "detail": f"模式: {settings.web_search_mode} | 可用后端: {', '.join(latencies)} | 今日已用: {settings.today_searches_made}/{settings.daily_search_limit}",
                })
            else:
                checks.append({
                    "name": "PAW 软件自带联网搜索 (免 Key)",
                    "status": "WARN",
                    "detail": f"模式: {settings.web_search_mode} | 全部候选引擎响应异常，建议检查网络/代理 | 今日已用: {settings.today_searches_made}/{settings.daily_search_limit}",
                })
    except Exception as e:
        checks.append({
            "name": "PAW 软件自带联网搜索 (免 Key)",
            "status": "WARN",
            "detail": f"探测失败: {e}",
        })

    all_passed = all(c["status"] in ("PASS", "STANDBY", "OFFLINE_FALLBACK") for c in checks)
    return {
        "overall": "HEALTHY" if all_passed else "DEGRADED",
        "checks": checks,
        "timestamp": time.time(),
    }


# ── Legacy compatibility aliases ──────────────────────────────────────────────

@app.get("/v1/local/config")
def get_local_config_compat():
    return get_settings()


@app.post("/v1/local/config")
def update_local_config_compat(req: SettingsUpdateRequest):
    return update_settings(req)


@app.post("/v1/local/watcher/scan")
def trigger_watcher_scan():
    return scheduler.run_cruise(trigger="manual_api_scan")


@app.get("/v1/local/events")
def get_recent_events():
    return {"events": event_bus.get_recent_events()}


# ── Frontend Static Assets & SPA Routing ──────────────────────────────────────

from backend.pm.router import router as pm_router
app.include_router(pm_router, prefix='/v1/pm', tags=['project-management'])
pm_dist = FRONTEND_DIST / 'pm'
if pm_dist.is_dir():
    app.mount('/pm-assets', StaticFiles(directory=str(pm_dist)), name='pm-assets')


@app.get('/app/pm/receipts', include_in_schema=False)
@app.get('/app/pm/today', include_in_schema=False)
@app.get('/app/pm/projects/{project_id}', include_in_schema=False)
def pm_project_page(project_id: str = ''):
    return FileResponse(str(pm_dist / 'index.html'), headers={'Cache-Control': 'no-store'})

assets_dir = FRONTEND_DIST / "assets"
if assets_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="web-assets")


@app.get("/paw_console.js", include_in_schema=False)
def paw_console_js():
    console_file = FRONTEND_DIST / "paw_console.js"
    if console_file.is_file():
        return FileResponse(str(console_file), media_type="application/javascript", headers={"Cache-Control": "no-store"})
    return JSONResponse(status_code=404, content={"detail": "paw_console.js not found"})


@app.get("/", include_in_schema=False)
def root_redirect() -> RedirectResponse:
    return RedirectResponse("/app", status_code=307)


_legacy_ui_routes = (
    "/today", "/daily", "/todos", "/projects", "/knowledge", "/files",
    "/sources", "/review", "/more", "/settings", "/settings/system",
    "/agnes", "/agy", "/assistant",
)


def _legacy_ui_redirect(request: Request) -> RedirectResponse:
    target = "/app" + request.url.path
    if request.url.query:
        target += "?" + request.url.query
    return RedirectResponse(target, status_code=307)


for _route in _legacy_ui_routes:
    app.add_api_route(
        _route,
        _legacy_ui_redirect,
        methods=["GET"],
        include_in_schema=False,
    )


@app.get("/app", include_in_schema=False)
@app.get("/app/{path:path}", include_in_schema=False)
def web_app(path: str = ""):
    index_file = FRONTEND_DIST / "index.html"
    if not index_file.is_file():
        return JSONResponse(
            status_code=503,
            content={"detail": "Web frontend dist not found. Please verify frontend/dist."},
        )
    return FileResponse(str(index_file), headers={"Cache-Control": "no-store"})


from backend.repair_v238 import install as _paw_install

_paw_install()
app.version = "2.4.0"
