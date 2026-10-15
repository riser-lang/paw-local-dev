#!/usr/bin/python3
"""Daily 06:00 Asia/Shanghai incremental WPS mirror.

Reuses existing MirrorOrchestrator / CDP inventory / Reader materializer.
Does not mutate the frozen historical 710 SourceVersion queue.
Does not consume Codex/Luna inference slots.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# Parent of the `paw_reader_fidelity` package (Phase 3B item).
_READER_ROOT_val = os.environ.get("PAW_READER_FIDELITY_ROOT")
if not _READER_ROOT_val:
    raise RuntimeError(
        "PAW_READER_FIDELITY_ROOT is required "
        "(PAW Runtime Path Contract, /etc/paw-runtime-paths.env)"
    )
READER_ROOT = _READER_ROOT_val
if READER_ROOT not in sys.path:
    sys.path.insert(0, READER_ROOT)

from gpt_brain_common import FileLeaseLock, LockedJsonStore, atomic_write_json, hash_payload, read_json, utc_now
from wps_inventory_v1 import evaluate_mirror_policy
from wps_mirror_contract import production_eligibility
from wps_mirror_orchestrator import MirrorOrchestrator, MirrorOutcome, normalize_inventory_object
from wps_mirror_state import MirrorArtifactStore, MirrorStateStore, decide_incremental_identity, sha256_file

ACTOR = "paw-wps-0600-incremental-mirror"
SCHEMA = "paw-wps-0600-incremental/1"
EXCLUSION_NEEDLE = "FY26运营导师带教情况表"
FROZEN_QUEUE_HASH = "7e88ae7daf9a10f28e5a79f70e27edf95f6122ed290fef7ae741d01e12110ffe"
FROZEN_QUEUE_TOTAL = 710
SHANGHAI = ZoneInfo("Asia/Shanghai")
PROFILE_DIR = Path("/var/lib/paw-ksheet-worker/chromium-profile")
BROWSER_EXECUTABLE = "/var/lib/paw-ksheet-worker/ms-playwright/chromium-1208/chrome-linux64/chrome"
_CDP_STORE_val = os.environ.get("PAW_CDP_STORE_ROOT")
if not _CDP_STORE_val:
    raise RuntimeError(
        "PAW_CDP_STORE_ROOT is required "
        "(PAW Runtime Path Contract, /etc/paw-runtime-paths.env)"
    )
CDP_STORE = Path(_CDP_STORE_val)
BRAIN_PATH = Path("/var/lib/paw-mcp-gateway/gpt_brain_bootstrap.json")
MAX_FILE_ATTEMPTS = 3
SINGLE_FILE_TIMEOUT_SECONDS = int(os.environ.get("PAW_WPS_0600_SINGLE_FILE_TIMEOUT", "900"))
MAX_CROSS_RUN_ATTEMPTS = int(os.environ.get("PAW_WPS_0600_MAX_CROSS_RUN_ATTEMPTS", "3"))
STALE_LEASE_SECONDS = int(os.environ.get("PAW_WPS_0600_STALE_LEASE_SECONDS", "7200"))
INVENTORY_TIMEOUT_SECONDS = max(60, min(int(os.environ.get("PAW_WPS_INVENTORY_TIMEOUT", "1200")), 1800))
HANDOFF_CHANGE_TYPES = frozenset({"NEW", "CONTENT_CHANGED"})
FALSE_CLASSIFICATION_REASONS = frozenset({"MIRROR_POLICY_NOT_EVALUATED", "OBJECT_NOT_FILE"})
INTENTIONAL_SKIP_ELIGIBILITY_REASONS = frozenset({"OBJECT_NOT_FILE", "RAW_BINARY_CAPABILITY_UNPROVEN"})


def default_paths() -> dict[str, Path]:
    root = Path(os.environ.get("PAW_WPS_0600_MIRROR_ROOT", "/var/lib/paw-wps-mirror"))
    return {
        "root": root,
        "state_db": root / "state.sqlite3",
        "checkpoint": root / "checkpoint.json",
        "history": root / "run_history.json",
        "lock": root / "run.lock",
        "profile_lock": root / "chromium.lock",
        "handoff": Path(os.environ.get("PAW_WPS_0600_HANDOFF_PATH", "/var/lib/paw-mcp-gateway/paw_wps_incremental_source_queue.json")),
        "reader_root": root / "incremental_reader",
        "seed_from": Path(os.environ.get("PAW_WPS_0600_SEED_FROM", str(CDP_STORE))),
        "retention_ledger": Path(os.environ.get("PAW_PPTX_RETENTION_LEDGER", str(CDP_STORE / "pptx_retention.sqlite3"))),
    }


def _blank_checkpoint() -> dict[str, Any]:
    now = utc_now()
    return {
        "schema": SCHEMA,
        "created_at": now,
        "updated_at": now,
        "objects": {},
        "last_run_id": None,
        "last_success_at": None,
        "last_error": None,
        "auth_status": "UNKNOWN",
        "formal_state_bypass": 0,
        "frozen_710_untouched": True,
    }


def _blank_history() -> dict[str, Any]:
    return {"schema": SCHEMA + "-history", "runs": []}


def _blank_handoff() -> dict[str, Any]:
    return {
        "schema": "paw-wps-incremental-source-queue/1",
        "updated_at": utc_now(),
        "items": {},
        "formal_state_bypass": 0,
        "frozen_710_untouched": True,
    }


def live_frozen_queue() -> dict[str, Any]:
    if not BRAIN_PATH.is_file():
        return {"available": False, "queue_hash": None, "total_sources": None}
    payload = json.loads(BRAIN_PATH.read_text(encoding="utf-8"))
    frozen = ((payload.get("bootstrap") or {}).get("frozen_queue") or {})
    return {
        "available": True,
        "queue_hash": frozen.get("queue_hash"),
        "queue_version": frozen.get("queue_version"),
        "total_sources": frozen.get("total_sources"),
        "matches_required": (
            frozen.get("queue_hash") == FROZEN_QUEUE_HASH
            and int(frozen.get("total_sources") or 0) == FROZEN_QUEUE_TOTAL
        ),
    }


def chromium_profile_busy() -> bool:
    for name in ("SingletonLock", "SingletonSocket"):
        path = PROFILE_DIR / name
        if path.exists() or path.is_symlink():
            return True
    return False


def is_forbidden_source(record: Mapping[str, Any]) -> bool:
    names = (
        str(record.get("filename") or ""),
        str(record.get("display_name") or ""),
        str(record.get("fname") or ""),
    )
    return any(EXCLUSION_NEEDLE in name for name in names)


def _iso_timestamp_ok(value: Any) -> bool:
    if not value:
        return False
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return True


def adapt_live_inventory_object(item: Mapping[str, Any]) -> dict[str, Any]:
    """Adapter for live Drive-API / CDP scan objects.

    Mature ``production_eligibility`` fail-closes unless ``mirror_policy`` has
    already been evaluated *and* ``mirror_policy_evaluated_at`` is a valid ISO
    timestamp. Live ``scan_live_inventory()`` never stamps that timestamp
    (unlike the r15_b17r/t runners). Folders keep their object_type.
    """
    record = normalize_inventory_object(item)
    if not record.get("filename"):
        record["filename"] = str(record.get("display_name") or "")
    policy, rule_id, reason = evaluate_mirror_policy(record.get("display_name"), record.get("filename"))
    record["mirror_policy"] = policy
    if rule_id:
        record["mirror_exclusion_rule"] = rule_id
        record["mirror_exclusion_reason"] = reason
    if not _iso_timestamp_ok(record.get("mirror_policy_evaluated_at")):
        record["mirror_policy_evaluated_at"] = utc_now()
    return record


def wrapper_disposition(record: Mapping[str, Any]) -> tuple[str, str]:
    """Classify one adapted object at the 06:00 wrapper boundary.

    Returns (kind, reason) where kind is ELIGIBLE, EXCLUDED, INTENTIONAL_SKIP,
    or BLOCKED_BACKEND. Folders and unproven raw/media are skips, not backend
    failures.
    """
    if is_forbidden_source(record):
        return "EXCLUDED", "WPS_MIRROR_EXCLUSION_RULE_001"
    object_type = str(record.get("object_type") or "").strip().casefold()
    if object_type and object_type != "file":
        return "INTENTIONAL_SKIP", "DIRECTORY_METADATA"
    eligibility = production_eligibility(record)
    if eligibility.eligible:
        return "ELIGIBLE", eligibility.backend
    if eligibility.status == "EXCLUDED":
        return "EXCLUDED", eligibility.reason or eligibility.exclusion_rule or "EXCLUSION_RULE"
    if eligibility.reason in INTENTIONAL_SKIP_ELIGIBILITY_REASONS:
        reason = "DIRECTORY_METADATA" if eligibility.reason == "OBJECT_NOT_FILE" else "NON_TARGET_RAW_OR_MEDIA"
        return "INTENTIONAL_SKIP", reason
    return "BLOCKED_BACKEND", eligibility.reason or "NO_ACQUISITION_ROUTE"


def evaluate_business_outcome(
    counts: Mapping[str, int],
    *,
    inventory_count: int,
    file_object_count: int,
    frozen_710_unchanged: bool,
    formal_state_bypass: int,
    forbidden_excluded: bool,
) -> dict[str, Any]:
    """Reject process-level COMPLETED when file objects never entered incremental classification."""
    policy_uneval = int(counts.get("MIRROR_POLICY_NOT_EVALUATED") or 0)
    false_not_file = int(counts.get("OBJECT_NOT_FILE_FALSE_POSITIVE") or 0)
    unchanged = int(counts.get("UNCHANGED") or 0)
    committed = int(counts.get("COMMITTED_LOCAL") or 0)
    blocked = int(counts.get("BLOCKED_BACKEND") or 0)
    failed = int(counts.get("FAILED") or 0)
    classified = unchanged + committed
    blockers: list[str] = []
    if not frozen_710_unchanged:
        blockers.append("FROZEN_710_HASH_CHANGED")
    if int(formal_state_bypass or 0) != 0:
        blockers.append("FORMAL_STATE_BYPASS")
    if policy_uneval > 0:
        blockers.append("MIRROR_POLICY_NOT_EVALUATED")
    if false_not_file > 0:
        blockers.append("OBJECT_NOT_FILE_FALSE_POSITIVE")
    if int(counts.get("ELIGIBLE") or 0) > 0 and classified == 0:
        blockers.append("NO_INCREMENTAL_CLASSIFICATION")
    if file_object_count > 0 and blocked > classified and blocked > max(1, file_object_count // 2):
        blockers.append("OVERWHELMING_BLOCKED_BACKEND")
    if not forbidden_excluded:
        blockers.append("FORBIDDEN_SOURCE_NOT_EXCLUDED")
    # Single-file acquisition failures no longer fail the whole batch gate;
    # the batch finalize classifies SUCCESS / PARTIAL_SUCCESS / FAILED instead.
    if blockers:
        return {"status": "FAILED_BUSINESS_GATES", "business_ok": False, "blockers": blockers, "classified": classified}
    return {"status": "COMPLETED", "business_ok": True, "blockers": [], "classified": classified}


def repair_identity_from_seed_if_false_classification(paths: Mapping[str, Path]) -> dict[str, Any]:
    """Restore committed identity after the 06:00 false-success classification run.

    The first real run upserted BLOCKED_BACKEND while copying live version/mtime
    onto rows that still pointed at last-known-good CDP artifacts. Reuse the
    seed artifact identity so unchanged files compare against the committed
    SourceVersion, not the blocked scan. Never writes the CDP seed file.
    """
    seed = Path(paths["seed_from"]) / "state.sqlite3"
    db = Path(paths["state_db"])
    if not seed.is_file() or not db.is_file():
        return {"repaired": 0, "cleared_missing_artifact": 0, "reason": "no_seed_or_db"}
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{seed}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        seed_rows = {str(row["object_id"]): dict(row) for row in conn.execute("SELECT * FROM mirror_state")}
    except sqlite3.Error:
        return {"repaired": 0, "cleared_missing_artifact": 0, "reason": "seed_unreadable"}
    finally:
        try:
            conn.close()
        except Exception:
            pass
    live_store = MirrorStateStore(db)
    repaired = 0
    cleared = 0
    for state in live_store.all():
        if state.status != "BLOCKED_BACKEND" or state.error_class not in FALSE_CLASSIFICATION_REASONS:
            continue
        prior = seed_rows.get(state.object_id)
        if not prior:
            continue
        artifact = str(prior.get("artifact_path") or "")
        artifact_ok = bool(artifact) and Path(artifact).is_file()
        values = asdict(state)
        prior_status = str(prior.get("status") or "")
        prior_terminal = str(prior.get("terminal_routing_state") or "")
        prior_cold = (
            prior_status == "EVICTED_ANALYZED" or prior_terminal == "ANALYZED_HASH_RECEIPT"
        ) and str(prior.get("sha256") or "")
        if artifact_ok:
            values["artifact_path"] = artifact
            values["artifact_size"] = int(prior.get("artifact_size") or 0)
            values["sha256"] = str(prior.get("sha256") or "")
            values["last_success_at"] = str(prior.get("last_success_at") or state.last_success_at or "")
            values["source_version"] = str(prior.get("source_version") or "")
            values["file_type"] = str(prior.get("file_type") or state.file_type or "")
            values["backend"] = str(prior.get("backend") or state.backend or "")
            values["status"] = "COMMITTED_LOCAL"
            values["error_class"] = ""
            values["terminal_routing_state"] = "COMMITTED_LOCAL"
            if str(prior.get("source_version") or "") != str(state.source_version or ""):
                values["source_mtime"] = str(prior.get("source_mtime") or "")
                values["source_size"] = int(prior.get("source_size") or 0)
            live_store.upsert(values)
            repaired += 1
        elif prior_cold:
            values["artifact_path"] = ""
            values["artifact_size"] = int(prior.get("artifact_size") or 0)
            values["sha256"] = str(prior.get("sha256") or "")
            values["last_success_at"] = str(prior.get("last_success_at") or state.last_success_at or "")
            values["source_version"] = str(prior.get("source_version") or "")
            values["source_mtime"] = str(prior.get("source_mtime") or state.source_mtime or "")
            values["source_size"] = int(prior.get("source_size") or state.source_size or 0)
            values["file_type"] = str(prior.get("file_type") or state.file_type or "")
            values["backend"] = str(prior.get("backend") or state.backend or "")
            values["status"] = "EVICTED_ANALYZED"
            values["error_class"] = ""
            values["terminal_routing_state"] = "ANALYZED_HASH_RECEIPT"
            live_store.upsert(values)
            repaired += 1
        elif str(prior.get("status") or "") in {"COMMITTED_LOCAL", "UNCHANGED"}:
            values["artifact_path"] = ""
            values["sha256"] = ""
            values["status"] = "DISCOVERED"
            values["error_class"] = ""
            values["terminal_routing_state"] = ""
            live_store.upsert(values)
            cleared += 1
    return {"repaired": repaired, "cleared_missing_artifact": cleared, "reason": "ok"}


class LazyProductionAcquirer:
    """Open the shared WPS Chromium profile only when a real acquire is required."""

    def __init__(self) -> None:
        self._playwright = None
        self._context = None
        self._page = None
        self.opened = False
        self.acquire_calls = 0

    def _ensure(self):
        if self._page is not None:
            return self._page
        if chromium_profile_busy():
            raise RuntimeError("PROFILE_BUSY")
        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=True,
            executable_path=BROWSER_EXECUTABLE,
            accept_downloads=True,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._page.goto("https://365.kdocs.cn/latest", wait_until="domcontentloaded", timeout=45000)
        self._page.wait_for_timeout(1500)
        self.opened = True
        return self._page

    def __call__(self, record, staging_dir):
        restore_workspace_import_path()
        from wps_mirror_acquisition import acquire_production
        self.acquire_calls += 1
        try:
            return acquire_production(self._ensure(), dict(record), staging_dir)
        except ImportError as exc:
            missing = getattr(exc, "name", "") or type(exc).__name__
            return {
                "status": "FAILED",
                "error_class": "PRODUCTION_ACQUISITION_FAILED:ImportError:" + str(missing),
                "artifact_path": "",
            }

    def close(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        finally:
            self._context = None
            self._page = None
            if self._playwright is not None:
                try:
                    self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None


def restore_workspace_import_path() -> None:
    """Keep 06:00 acquisition on workspace modules after live scan.

    ``wps_teamspace_scan`` inserts ``/opt/paw-ksheet-worker`` at sys.path[0].
    That tree's ``wps_export_acquisition`` has no production export entrypoint,
    which becomes ``ImportError: wps_export_acquisition`` during KSheet acquire.
    """
    root = str(ROOT)
    if root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    opt_prefix = "/opt/paw-ksheet-worker/"
    for name in (
        "wps_export_acquisition",
        "wps_airpage_export_acquisition",
        "wps_native_acquisition",
        "wps_mirror_acquisition",
        "wps_mirror_contract",
    ):
        module = sys.modules.get(name)
        filename = str(getattr(module, "__file__", "") or "") if module is not None else ""
        if filename.startswith(opt_prefix):
            sys.modules.pop(name, None)


def seed_state_if_needed(paths: Mapping[str, Path]) -> str:
    paths["root"].mkdir(parents=True, exist_ok=True)
    db = paths["state_db"]
    seed = paths["seed_from"] / "state.sqlite3"
    if db.exists() or not seed.is_file():
        return "present" if db.exists() else "no_seed"
    shutil.copy2(seed, db)
    return "seeded"


def auth_preflight(*, page_factory: Callable[[], Any] | None = None) -> dict[str, Any]:
    """Reuse the existing WPS Chromium profile. Never print cookies or tokens."""
    if chromium_profile_busy():
        return {"ok": False, "auth_status": "PROFILE_BUSY", "message": "Chromium profile is held by another WPS/KSheet process"}
    if page_factory is not None:
        page = page_factory()
        try:
            url = str(getattr(page, "url", "") or "")
            ok = url.startswith(("https://drive.wps.cn", "https://www.kdocs.cn", "https://365.kdocs.cn"))
            return {"ok": ok, "auth_status": "AUTH_READY" if ok else "AUTH_REQUIRED", "message": "injected page factory"}
        finally:
            closer = getattr(page, "close", None)
            if callable(closer):
                closer()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"ok": False, "auth_status": "PLAYWRIGHT_MISSING", "message": "Playwright is not installed"}
    if not Path(BROWSER_EXECUTABLE).is_file():
        return {"ok": False, "auth_status": "BROWSER_UNAVAILABLE", "message": "Chromium executable missing"}
    for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        path = PROFILE_DIR / lock
        try:
            if path.exists() or path.is_symlink():
                path.unlink()
        except OSError:
            return {"ok": False, "auth_status": "PROFILE_BUSY", "message": "could not clear chromium singleton"}
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(PROFILE_DIR),
                headless=True,
                executable_path=BROWSER_EXECUTABLE,
                args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://365.kdocs.cn/latest", wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(1500)
                url = (page.url or "").casefold()
                body = ""
                try:
                    body = page.locator("body").inner_text(timeout=5000).casefold()
                except Exception:
                    body = ""
                if any(marker in url for marker in ("account.wps.cn", "account.kdocs.cn", "/passport/")):
                    return {"ok": False, "auth_status": "AUTH_REQUIRED", "message": "WPS session requires login"}
                if any(marker in body for marker in ("登录", "扫码", "sign in")) and "365.kdocs.cn" not in url and "drive.wps.cn" not in url:
                    return {"ok": False, "auth_status": "AUTH_REQUIRED", "message": "WPS session requires login"}
                ok = url.startswith(("https://drive.wps.cn", "https://www.kdocs.cn", "https://365.kdocs.cn"))
                return {"ok": ok, "auth_status": "AUTH_READY" if ok else "AUTH_REQUIRED", "message": "WPS session preflight"}
            finally:
                context.close()
    except Exception as exc:
        return {"ok": False, "auth_status": "AUTH_UNKNOWN", "message": type(exc).__name__}


def discover_inventory(*, objects: list[Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
    if objects is not None:
        return [adapt_live_inventory_object(item) for item in objects]
    from cdp_full_mirror_runner import scan_live_inventory
    raw = scan_live_inventory()
    return [adapt_live_inventory_object(item) for item in raw]


def discover_inventory_bounded(paths: Mapping[str, Path], *, timeout_seconds: int | None = None,
                               command: list[str] | None = None) -> list[dict[str, Any]]:
    """Isolate the formerly unbounded scan before any file acquisition.

    Timeout/crash leaves the last-good inventory, checkpoint objects, Reader
    and handoff intact. Only this task's process group is terminated.
    """
    paths["root"].mkdir(parents=True, exist_ok=True)
    run_id = "inventory-" + uuid.uuid4().hex[:12]
    output = paths["root"] / (run_id + ".json")
    log_path = paths["root"] / "inventory-last-run.log"
    checkpoint_store = LockedJsonStore(paths["checkpoint"])
    with checkpoint_store.mutate() as checkpoint:
        if not checkpoint: checkpoint.update(_blank_checkpoint())
        checkpoint.update(inventory_stage="RUNNING", inventory_run_id=run_id,
                          inventory_started_at=utc_now(), updated_at=utc_now())
    cmd = command or [sys.executable, str(Path(__file__).resolve()), "--worker-inventory", str(output)]
    print("WPS inventory: starting bounded metadata scan", flush=True)
    failure = None
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, start_new_session=True, cwd=str(ROOT), stdout=log, stderr=log)
        try:
            proc.wait(timeout=timeout_seconds if timeout_seconds is not None else INVENTORY_TIMEOUT_SECONDS)
            if proc.returncode != 0 or not output.is_file():
                failure = "INVENTORY_WORKER_FAILED"
        except subprocess.TimeoutExpired:
            failure = "INVENTORY_TIMEOUT"
            try: os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            proc.wait(timeout=30)
    if failure is None:
        try:
            payload = read_json(output)
            inventory = payload["documents"]
            if not isinstance(inventory, list) or not inventory:
                failure = "INVENTORY_EMPTY_OR_INVALID"
        except (OSError, ValueError, KeyError):
            failure = "INVENTORY_EMPTY_OR_INVALID"
    with checkpoint_store.mutate() as checkpoint:
        checkpoint.update(inventory_stage="FAILED" if failure else "COMPLETED",
                          inventory_finished_at=utc_now(), inventory_error=failure,
                          updated_at=utc_now())
    if failure:
        with LockedJsonStore(paths["history"]).mutate() as history:
            history.setdefault("schema", SCHEMA + "-history")
            history.setdefault("runs", []).append({"run_id":run_id,"status":failure,"at":utc_now()})
            history["runs"] = history["runs"][-50:]
        raise RuntimeError(failure + ":see_inventory-last-run.log")
    # Retain one last-good metadata snapshot; never pretend it is today's scan.
    atomic_write_json(paths["root"] / "inventory-last-good.json", payload)
    output.unlink(missing_ok=True)
    print("WPS inventory: completed, objects=" + str(len(inventory)), flush=True)
    return inventory


def _family_for_format(file_format: str) -> str:
    mapping = {"xlsx": "XLSX", "xlsm": "XLSX", "docx": "DOCX", "pptx": "PPTX", "pdf": "PDF", "doc": "LEGACY", "xls": "LEGACY"}
    return mapping.get(str(file_format or "").lower(), "")


def _existing_reader_identity_matches(existing: Mapping[str, Any], info: Mapping[str, Any], reader_root: Path) -> bool:
    """Verify existing evidence metadata, never rebuild an old Reader artifact."""
    from paw_reader_fidelity.contract import content_fingerprint
    try:
        path = Path(str(existing.get('reader_result_path') or ''))
        if not path.is_file() or not path.resolve().is_relative_to(reader_root.resolve()):
            return False
        result = read_json(path)
        return bool(result.get('source_version_linked') and
            str(result.get('source_object_id')) == str(info.get('object_id')) and
            str(result.get('source_version')) == str(info.get('source_version')) and
            result.get('raw_artifact_sha256') == info.get('sha256') and
            existing.get('content_fingerprint') == result.get('content_fingerprint') == content_fingerprint(
                result.get('structured_semantics') or {}, result.get('text_blocks') or [],
                result.get('tables') or [], result.get('relationships') or []))
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def incremental_reader_handoff(
    outcomes: list[Any],
    *,
    reader_root: Path,
    handoff_path: Path,
    read_artifact_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Hand only new/changed SourceVersions to Reader. Skip unchanged and exclusions."""
    reader_root.mkdir(parents=True, exist_ok=True)
    store = LockedJsonStore(handoff_path)
    payload = store.load() or _blank_handoff()
    payload.setdefault("items", {})
    handed = 0
    skipped = 0
    failed = 0
    failures: list[dict[str, str]] = []
    def failure(source_id: str, reason: str) -> None:
        nonlocal failed
        failed += 1
        if len(failures) < 50:
            failures.append({'source_version_id': source_id, 'reason': reason})
    if read_artifact_fn is None:
        from paw_reader_fidelity.contract import ReaderInput
        from paw_reader_fidelity.dispatch import read_artifact as _read
    else:
        ReaderInput = None  # type: ignore[assignment]
        _read = read_artifact_fn
    for outcome in outcomes:
        change = getattr(outcome, "change_type", "") or (outcome.get("change_type") if isinstance(outcome, Mapping) else "")
        status = getattr(outcome, "status", "") or (outcome.get("status") if isinstance(outcome, Mapping) else "")
        handoff = getattr(outcome, "handoff", None)
        if isinstance(outcome, Mapping):
            handoff = outcome.get("handoff")
        if status in {"EXCLUDED", "UNCHANGED"} or change not in HANDOFF_CHANGE_TYPES:
            skipped += 1
            continue
        if status != "COMMITTED_LOCAL" or handoff is None:
            skipped += 1
            continue
        info = asdict(handoff) if hasattr(handoff, "__dataclass_fields__") else dict(handoff)
        source_version_id = f"{info.get('object_id')}@{info.get('source_version')}"
        if not all(re.fullmatch(r'[A-Za-z0-9_-]{1,128}', str(info.get(key) or ''))
                   for key in ('object_id', 'source_version')):
            failure(source_version_id, 'SOURCE_IDENTITY_PATH_INVALID')
            continue
        if not re.fullmatch(r'[0-9a-f]{64}', str(info.get('sha256') or '')):
            failure(source_version_id, 'SOURCE_HASH_INVALID')
            continue
        artifact = Path(str(info.get("artifact_path") or ""))
        if not artifact.is_file() or sha256_file(artifact) != info['sha256']:
            failure(source_version_id, 'COMMITTED_ARTIFACT_HASH_MISMATCH_OR_MISSING')
            continue
        # Check before parsing: the former check after Reader caused duplicate
        # raw reads, including a second pass over every freshly handed source.
        existing = (store.load().get('items') or {}).get(source_version_id)
        if existing:
            if existing.get('sha256') != info['sha256']:
                failure(source_version_id, 'SOURCE_VERSION_IDENTITY_REUSED_WITH_DIFFERENT_SHA256')
            elif _existing_reader_identity_matches(existing, info, reader_root):
                skipped += 1
            else:
                failure(source_version_id, 'EXISTING_READER_INVALID_REPAIR_REQUIRES_EXPLICIT_SCOPE')
            continue
        out_dir = reader_root / str(info.get("object_id")) / str(info.get("source_version"))
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            if read_artifact_fn is None:
                from paw_reader_fidelity.contract import ReaderInput as _ReaderInput
                result = _read(
                    _ReaderInput(
                        path=str(artifact),
                        source_object_id=str(info.get("object_id")),
                        source_version=str(info.get("source_version")),
                        display_name=str(info.get("display_name") or ""),
                        family=_family_for_format(str(info.get("format") or artifact.suffix.lstrip("."))),
                        committed=True,
                        raw_artifact_sha256=str(info.get("sha256") or sha256_file(artifact)),
                    ),
                    out_dir,
                )
                reader_path = str(out_dir / "reader_result.json")
                fingerprint = getattr(result, "content_fingerprint", "") or ""
            else:
                result = read_artifact_fn(info, out_dir)
                reader_path = str(result.get("reader_result_path") if isinstance(result, Mapping) else out_dir / "reader_result.json")
                fingerprint = str(result.get("content_fingerprint") if isinstance(result, Mapping) else "")
        except Exception as exc:
            failure(source_version_id, 'READER_FAILED:' + type(exc).__name__)
            continue
        key = source_version_id
        entry = {
            "source_version_id": source_version_id,
            "object_id": info.get("object_id"),
            "source_version": info.get("source_version"),
            "change_type": change,
            "sha256": info.get("sha256"),
            "artifact_path": str(artifact),
            "reader_result_path": reader_path,
            "content_fingerprint": fingerprint,
            "handed_off_at": utc_now(),
            "semantic_path": "incremental_reader_gpt_brain_patrol",
            "frozen_710_untouched": True,
        }
        with store.mutate() as current:
            if not current:
                current.update(_blank_handoff())
            current.setdefault('items', {})
            concurrent = current['items'].get(key)
            if concurrent and concurrent.get('sha256') != info['sha256']:
                failure(source_version_id, 'CONCURRENT_SOURCE_VERSION_IDENTITY_CONFLICT')
            elif concurrent:
                skipped += 1
            else:
                current['items'][key] = entry
                handed += 1
            current.update(updated_at=utc_now(), frozen_710_untouched=True, formal_state_bypass=0)
    return {"handed": handed, "skipped": skipped, "failed": failed, "failures": failures,
            "handoff_path": str(handoff_path)}


def _process_one(orchestrator: MirrorOrchestrator, record: Mapping[str, Any], run_id: str, acquirer=None) -> Any:
    last = None
    delay = 1.0
    for attempt in range(1, MAX_FILE_ATTEMPTS + 1):
        last = orchestrator.process_object(record, page=None, run_id=f"{run_id}-{attempt}")
        if last.status != "FAILED":
            return last
        time.sleep(delay)
        delay = min(delay * 2, 8.0)
    return last

def _cleanup_chromium_locks() -> None:
    """Remove stale Chromium profile locks left behind by a killed worker."""
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        path = PROFILE_DIR / name
        try:
            if path.is_symlink() or path.exists():
                path.unlink()
        except OSError:
            pass

def _outcome_from_payload(payload: Mapping[str, Any]) -> MirrorOutcome:
    handoff = payload.get("handoff")
    if isinstance(handoff, Mapping):
        from wps_mirror_orchestrator import SourceVersionHandoff
        handoff = SourceVersionHandoff(**dict(handoff))
    return MirrorOutcome(
        object_id=str(payload.get("object_id") or ""),
        status=str(payload.get("status") or "FAILED"),
        change_type=str(payload.get("change_type") or ""),
        backend=str(payload.get("backend") or ""),
        error_class=str(payload.get("error_class") or ""),
        handoff=handoff,
        terminal_routing_state=str(payload.get("terminal_routing_state") or ""),
    )

def _write_minimal_xlsx(path: Path, marker: str = "ok") -> None:
    import zipfile
    from xml.sax.saxutils import escape
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>{escape(marker)}</t></is></c></row></sheetData>'
        '</worksheet>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>'
    )
    ctypes = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", ctypes)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)

def _make_test_acquirer(mode: str):
    """Fault-injection acquirer for offline tests only (env-gated, never production)."""
    hang_object = os.environ.get("PAW_WPS_0600_TEST_HANG_OBJECT", "")
    def acquirer(record, staging_dir):
        object_id = str(record.get("object_id") or "")
        if mode == "hang" and object_id == hang_object:
            time.sleep(100000)
        path = Path(staging_dir) / "artifact.xlsx"
        _write_minimal_xlsx(path, str(record.get("version") or "v1"))
        return {"status": "ACQUIRED", "artifact_path": str(path), "format": "xlsx"}
    return acquirer

def _run_single_file_worker(payload_path: str, outcome_path: str) -> int:
    """Child entry: process exactly one acquisition-needing file, then exit."""
    payload = json.loads(Path(payload_path).read_text(encoding="utf-8"))
    record = payload["record"]
    run_id = str(payload.get("run_id") or "")
    root = Path(payload["root"])
    retention_ledger = None
    if payload.get("retention_ledger"):
        from pptx_retention_manager import PptxRetentionLedger
        retention_ledger = PptxRetentionLedger(Path(payload["retention_ledger"]))
    test_mode = os.environ.get("PAW_WPS_0600_TEST_ACQUIRER", "")
    acquirer = _make_test_acquirer(test_mode) if test_mode else LazyProductionAcquirer()
    orchestrator = MirrorOrchestrator(
        MirrorStateStore(payload.get("state_db")),
        MirrorArtifactStore(root),
        acquirer=acquirer,
        retention_ledger=retention_ledger,
    )
    try:
        outcome = orchestrator.process_object(record, page=None, run_id=run_id)
        data = asdict(outcome)
    finally:
        if isinstance(acquirer, LazyProductionAcquirer):
            acquirer.close()
    Path(outcome_path).write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    return 0

def _process_one_isolated(
    record: Mapping[str, Any],
    run_id: str,
    paths: Mapping[str, Path],
) -> tuple[MirrorOutcome, str]:
    """Hard-timeout single-file execution in a killable subprocess.
    A hung Playwright / WPS export wait never raises, so try/except cannot
    interrupt it.  Acquisition-needing files run in a child process group
    that is SIGKILLed on timeout; the batch loop continues and the file is
    marked FAILED_RETRYABLE with full audit fields."""
    staging = Path(paths["root"]) / ".staging" / "isolation"
    staging.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:12]
    payload_path = staging / f"payload-{token}.json"
    outcome_path = staging / f"outcome-{token}.json"
    payload = {
        "record": dict(record),
        "run_id": run_id,
        "root": str(paths["root"]),
        "state_db": str(paths["state_db"]),
        "retention_ledger": str(paths.get("retention_ledger") or "") or None,
    }
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    object_id = str(record.get("object_id") or "")
    cmd = [sys.executable, str(Path(__file__).resolve()), "--worker-single-file", str(payload_path), str(outcome_path)]
    proc = subprocess.Popen(cmd, start_new_session=True, cwd=str(ROOT),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    timed_out = False
    try:
        proc.wait(timeout=SINGLE_FILE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(os.getpgid(proc.pid), 9)
        except OSError:
            proc.kill()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
        _cleanup_chromium_locks()
    finally:
        try:
            payload_path.unlink()
        except OSError:
            pass
    if timed_out:
        return MirrorOutcome(
            object_id, "FAILED_RETRYABLE",
            backend=str(record.get("mirror_backend_candidate") or ""),
            error_class="TIMEOUT",
            terminal_routing_state="BLOCKED_WITH_EXACT_REASON",
        ), "TIMEOUT"
    if not outcome_path.is_file():
        return MirrorOutcome(
            object_id, "FAILED_RETRYABLE",
            error_class="WORKER_CRASH_NO_OUTCOME",
            terminal_routing_state="BLOCKED_WITH_EXACT_REASON",
        ), "CRASH"
    try:
        data = json.loads(outcome_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return MirrorOutcome(
            object_id, "FAILED_RETRYABLE",
            error_class="WORKER_OUTCOME_UNREADABLE",
            terminal_routing_state="BLOCKED_WITH_EXACT_REASON",
        ), "CRASH"
    finally:
        try:
            outcome_path.unlink()
        except OSError:
            pass
    return _outcome_from_payload(data), "OK"

def recover_stale_leases(store: MirrorStateStore, *, current_run_id: str = "") -> dict[str, Any]:
    """Recover ACQUIRING / READER residues left by dead runs before a new batch."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=STALE_LEASE_SECONDS)
    recovered: list[dict[str, Any]] = []
    for state in store.all():
        if state.status not in {"ACQUIRING", "ACQUIRED", "VALIDATED", "ELIGIBLE", "READER_PROCESSING"}:
            continue
        last = str(state.last_attempt_at or "")
        try:
            ts = datetime.fromisoformat(last) if last else None
        except ValueError:
            ts = None
        if ts is not None and ts >= cutoff:
            continue
        attempt_total = int(state.attempt_count or 0) + 1
        status = "FAILED_RETRYABLE" if attempt_total < MAX_CROSS_RUN_ATTEMPTS else "EXPLICIT_HOLD"
        try:
            store.update_status(
                state.object_id, status,
                error_class="STALE_LEASE_RECOVERY",
                attempt_count=attempt_total,
                run_id=current_run_id,
                last_attempt_at=utc_now(),
                next_retry_at=utc_now() if status == "FAILED_RETRYABLE" else "",
            )
        except Exception:
            continue
        recovered.append({
            "object_id": state.object_id,
            "display_name": state.display_name,
            "from_status": state.status,
            "to_status": status,
            "reason": "STALE_LEASE_RECOVERY",
        })
    return {"recovered": recovered, "count": len(recovered)}


def run_incremental(
    *,
    inventory: list[Mapping[str, Any]],
    paths: Mapping[str, Path] | None = None,
    acquirer: Callable | None = None,
    auth: Mapping[str, Any] | None = None,
    skip_auth: bool = False,
) -> dict[str, Any]:
    paths = paths or default_paths()
    frozen_before = live_frozen_queue()
    paths["root"].mkdir(parents=True, exist_ok=True)
    seed = seed_state_if_needed(paths)
    checkpoint_store = LockedJsonStore(paths["checkpoint"])
    checkpoint = checkpoint_store.load() or _blank_checkpoint()
    history_store = LockedJsonStore(paths["history"])
    history = history_store.load() or _blank_history()
    run_id = "wps0600-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    if not skip_auth:
        auth = auth or auth_preflight()
        checkpoint["auth_status"] = auth.get("auth_status")
        if not auth.get("ok"):
            checkpoint["last_error"] = auth.get("message")
            checkpoint["updated_at"] = utc_now()
            checkpoint_store.save(checkpoint)
            history["runs"].append({"run_id": run_id, "status": "AUTH_FAILED_KEEP_LAST_GOOD", "auth": auth.get("auth_status"), "at": utc_now()})
            history["runs"] = history["runs"][-50:]
            history_store.save(history)
            frozen_after = live_frozen_queue()
            return {
                "status": "AUTH_FAILED_KEEP_LAST_GOOD",
                "run_id": run_id,
                "auth": auth,
                "seed": seed,
                "frozen_before": frozen_before,
                "frozen_after": frozen_after,
                "frozen_710_unchanged": frozen_before.get("queue_hash") == frozen_after.get("queue_hash"),
                "formal_state_bypass": 0,
            }
    else:
        auth = auth or {"ok": True, "auth_status": "SKIPPED"}
        checkpoint["auth_status"] = auth.get("auth_status")

    identity_repair = repair_identity_from_seed_if_false_classification(paths)
    restore_workspace_import_path()
    use_isolation = acquirer is None
    owned_acquirer: LazyProductionAcquirer | None = None
    if acquirer is None:
        owned_acquirer = LazyProductionAcquirer()
        acquirer = owned_acquirer
    retention_ledger = None
    ledger_path = paths.get("retention_ledger")
    if ledger_path:
        from pptx_retention_manager import PptxRetentionLedger
        retention_ledger = PptxRetentionLedger(ledger_path)
    orchestrator = MirrorOrchestrator(
        MirrorStateStore(paths["state_db"]),
        MirrorArtifactStore(paths["root"]),
        acquirer=acquirer,
        retention_ledger=retention_ledger,
    )
    stale_recovery = recover_stale_leases(orchestrator.state, current_run_id=run_id)
    outcomes = []
    handoff_totals = {'handed': 0, 'skipped': 0, 'failed': 0, 'failures': [], 'handoff_path': str(paths['handoff'])}
    isolated_acquisition_attempts = 0
    counts = {
        "NEW": 0, "CONTENT_CHANGED": 0, "UNCHANGED": 0, "RENAMED": 0, "MOVED": 0,
        "EXCLUDED": 0, "FAILED": 0, "COMMITTED_LOCAL": 0, "INTENTIONAL_SKIP": 0,
        "BLOCKED_BACKEND": 0, "MIRROR_POLICY_NOT_EVALUATED": 0, "OBJECT_NOT_FILE_FALSE_POSITIVE": 0,
        "ELIGIBLE": 0, "EVICTED_ANALYZED_UNCHANGED": 0,
        "TIMEOUT": 0, "FAILED_RETRYABLE": 0, "EXPLICIT_HOLD": 0,
    }
    failed_files: list[dict[str, Any]] = []
    file_object_count = 0
    forbidden_seen = 0
    forbidden_excluded = 0
    try:
        for raw in inventory:
            record = adapt_live_inventory_object(raw)
            object_id = str(record.get("object_id") or "")
            object_type = str(record.get("object_type") or "").strip().casefold()
            if object_type == "file":
                file_object_count += 1
            if is_forbidden_source(record):
                forbidden_seen += 1
            kind, reason = wrapper_disposition(record)
            if kind == "INTENTIONAL_SKIP":
                counts["INTENTIONAL_SKIP"] += 1
                terminal = "DIRECTORY_METADATA" if reason == "DIRECTORY_METADATA" else "EXCLUDED_INVENTORY_ONLY"
                orchestrator.state.upsert({
                    "object_id": object_id,
                    "display_name": str(record.get("display_name") or ""),
                    "parent_id": str(record.get("parent_id") or ""),
                    "file_type": str(record.get("file_type") or ("FOLDER" if reason == "DIRECTORY_METADATA" else "OTHER")),
                    "backend": "DIRECTORY_METADATA" if reason == "DIRECTORY_METADATA" else "NO_MIRROR",
                    "status": "EXCLUDED",
                    "error_class": reason,
                    "terminal_routing_state": terminal,
                })
                outcomes.append(MirrorOutcome(object_id, "INTENTIONAL_SKIP", error_class=reason, terminal_routing_state="INTENTIONAL_SKIP"))
                checkpoint["objects"][object_id] = {
                    "status": "INTENTIONAL_SKIP", "change_type": "", "at": utc_now(),
                    "reason": reason, "error": False,
                }
                continue
            if kind == "EXCLUDED":
                outcome = orchestrator.process_object(record, run_id=run_id)
                outcomes.append(outcome)
                counts["EXCLUDED"] += 1
                if is_forbidden_source(record):
                    forbidden_excluded += 1
                checkpoint["objects"][object_id] = {
                    "status": "EXCLUDED", "change_type": "", "at": utc_now(),
                    "reason": reason,
                }
                continue
            if kind != "ELIGIBLE":
                outcome = orchestrator.process_object(record, run_id=run_id)
                outcomes.append(outcome)
                counts["BLOCKED_BACKEND"] += 1
                if outcome.error_class == "MIRROR_POLICY_NOT_EVALUATED" or reason == "MIRROR_POLICY_NOT_EVALUATED":
                    counts["MIRROR_POLICY_NOT_EVALUATED"] += 1
                if outcome.error_class == "OBJECT_NOT_FILE" or reason == "OBJECT_NOT_FILE":
                    counts["OBJECT_NOT_FILE_FALSE_POSITIVE"] += 1
                checkpoint["objects"][object_id] = {
                    "status": outcome.status, "change_type": outcome.change_type,
                    "at": utc_now(), "reason": outcome.error_class or reason,
                }
                continue
            counts["ELIGIBLE"] += 1
            previous = orchestrator.state.get(object_id)
            change = decide_incremental_identity(record, previous)
            prior_attempts = int(getattr(previous, "attempt_count", 0) or 0) if previous is not None else 0
            if previous is not None and previous.status == "EXPLICIT_HOLD":
                outcome = MirrorOutcome(
                    object_id, "EXPLICIT_HOLD",
                    backend=str(record.get("mirror_backend_candidate") or ""),
                    error_class=str(previous.error_class or "HELD_FOR_MANUAL_REVIEW"),
                    terminal_routing_state="BLOCKED_WITH_EXACT_REASON",
                )
                outcomes.append(outcome)
                counts["EXPLICIT_HOLD"] += 1
                checkpoint["objects"][object_id] = {
                    "status": "EXPLICIT_HOLD", "change_type": "", "at": utc_now(),
                    "attempted": False, "reason": outcome.error_class,
                }
                checkpoint["updated_at"] = utc_now()
                checkpoint_store.save(checkpoint)
                continue
            needs_acquisition = change in {"NEW", "CONTENT_CHANGED"}
            if needs_acquisition and use_isolation:
                isolated_acquisition_attempts += 1
                outcome, isolation = _process_one_isolated(record, run_id, paths)
                if isolation == "TIMEOUT":
                    counts["TIMEOUT"] += 1
                if isolation in {"TIMEOUT", "CRASH"} or outcome.status == "FAILED":
                    attempt_total = prior_attempts + 1
                    held = attempt_total >= MAX_CROSS_RUN_ATTEMPTS
                    retry_status = "EXPLICIT_HOLD" if held else "FAILED_RETRYABLE"
                    error_class = outcome.error_class or ("TIMEOUT" if isolation == "TIMEOUT" else "ACQUISITION_FAILED")
                    try:
                        orchestrator.state.update_status(
                            object_id, retry_status,
                            error_class=error_class,
                            attempt_count=attempt_total,
                            run_id=run_id,
                            last_attempt_at=utc_now(),
                            next_retry_at="" if held else utc_now(),
                        )
                    except Exception:
                        pass
                    outcome = MirrorOutcome(
                        object_id, retry_status,
                        backend=outcome.backend,
                        error_class=error_class,
                        terminal_routing_state="BLOCKED_WITH_EXACT_REASON",
                    )
                elif outcome.status in {"COMMITTED_LOCAL", "UNCHANGED"}:
                    try:
                        orchestrator.state.update_status(
                            object_id, outcome.status,
                            attempt_count=0, run_id=run_id,
                            last_attempt_at=utc_now(), next_retry_at="",
                        )
                    except Exception:
                        pass
            else:
                outcome = _process_one(orchestrator, record, run_id)
                if outcome.status in {"COMMITTED_LOCAL", "UNCHANGED"}:
                    # Persist the routing state this outcome already carries,
                    # and record a cold receipt as EVICTED_ANALYZED — the same
                    # status pptx_retention_manager._sync_mirror_state() and
                    # _repair_false_classification() write for "analyzed, then
                    # artifact evicted".  Writing plain UNCHANGED here left the
                    # row disagreeing with the rest of the system.
                    persisted_status = outcome.status
                    extra: dict[str, Any] = {}
                    if outcome.terminal_routing_state:
                        extra["terminal_routing_state"] = outcome.terminal_routing_state
                    if (outcome.status == "UNCHANGED"
                            and outcome.terminal_routing_state == "ANALYZED_HASH_RECEIPT"):
                        persisted_status = "EVICTED_ANALYZED"
                        extra["artifact_path"] = ""
                    try:
                        orchestrator.state.update_status(
                            object_id, persisted_status,
                            attempt_count=0, run_id=run_id,
                            last_attempt_at=utc_now(), next_retry_at="",
                            **extra,
                        )
                    except Exception:
                        pass
            outcomes.append(outcome)
            if outcome.status == "UNCHANGED":
                counts["UNCHANGED"] += 1
                if outcome.terminal_routing_state == "ANALYZED_HASH_RECEIPT":
                    counts["EVICTED_ANALYZED_UNCHANGED"] += 1
                if outcome.change_type in {"RENAMED", "MOVED"}:
                    counts[outcome.change_type] += 1
            elif outcome.status == "COMMITTED_LOCAL":
                counts["COMMITTED_LOCAL"] += 1
                change_type = outcome.change_type or change
                if change_type in counts:
                    counts[change_type] += 1
            elif outcome.status == "EXCLUDED":
                counts["EXCLUDED"] += 1
            elif outcome.status == "BLOCKED_BACKEND":
                counts["BLOCKED_BACKEND"] += 1
                if outcome.error_class == "MIRROR_POLICY_NOT_EVALUATED":
                    counts["MIRROR_POLICY_NOT_EVALUATED"] += 1
                if outcome.error_class == "OBJECT_NOT_FILE":
                    counts["OBJECT_NOT_FILE_FALSE_POSITIVE"] += 1
            else:
                counts[outcome.status] = counts.get(outcome.status, 0) + 1
            checkpoint["objects"][object_id] = {
                "status": outcome.status,
                "change_type": outcome.change_type,
                "at": utc_now(),
                "attempted": True,
                "reason": outcome.error_class or "",
            }
            if outcome.status in {"FAILED", "FAILED_RETRYABLE", "EXPLICIT_HOLD"}:
                failed_files.append({
                    "source_version_id": f"{object_id}@{record.get('version', record.get('source_version', ''))}",
                    "object_id": object_id,
                    "display_name": str(record.get("display_name") or ""),
                    "error_class": outcome.error_class or "",
                    "attempt_count": prior_attempts + 1,
                    "last_attempt_at": utc_now(),
                    "run_id": run_id,
                })
            # Incremental durability: persist after every file so a mid-batch
            # failure never loses completed increments.
            checkpoint["updated_at"] = utc_now()
            checkpoint_store.save(checkpoint)
            if outcome.status == "COMMITTED_LOCAL" and outcome.handoff is not None:
                part = incremental_reader_handoff(
                    [outcome], reader_root=paths["reader_root"], handoff_path=paths["handoff"]
                )
                for name in ('handed', 'skipped', 'failed'):
                    handoff_totals[name] += part[name]
                handoff_totals['failures'].extend(part.get('failures', []))
                handoff_totals['failures'] = handoff_totals['failures'][:50]
    finally:
        if owned_acquirer is not None:
            owned_acquirer.close()
    checkpoint["last_run_id"] = run_id
    checkpoint["updated_at"] = utc_now()
    frozen_after = live_frozen_queue()
    frozen_unchanged = frozen_before.get("queue_hash") == frozen_after.get("queue_hash") and frozen_after.get("matches_required", True)
    forbidden_ok = forbidden_seen == 0 or forbidden_excluded == forbidden_seen
    business = evaluate_business_outcome(
        counts,
        inventory_count=len(inventory),
        file_object_count=file_object_count,
        frozen_710_unchanged=bool(frozen_unchanged),
        formal_state_bypass=0,
        forbidden_excluded=forbidden_ok,
    )
    if handoff_totals['failed']:
        business['business_ok'] = False
        business['status'] = 'FAILED_READER_HANDOFF'
        business.setdefault('blockers', []).append('INCREMENTAL_READER_HANDOFF_FAILED')
    failed_count = (
        int(counts.get("FAILED", 0) or 0)
        + int(counts.get("FAILED_RETRYABLE", 0) or 0)
        + int(counts.get("EXPLICIT_HOLD", 0) or 0)
    )
    timeout_count = int(counts.get("TIMEOUT", 0) or 0)
    success_count = (
        int(counts.get("COMMITTED_LOCAL", 0) or 0)
        + int(counts.get("UNCHANGED", 0) or 0)
        + int(counts.get("EXCLUDED", 0) or 0)
        + int(counts.get("INTENTIONAL_SKIP", 0) or 0)
    )
    if business["business_ok"]:
        if failed_count == 0:
            batch_status = "SUCCESS"
            checkpoint["last_success_at"] = utc_now()
            checkpoint["last_error"] = None
        else:
            batch_status = "PARTIAL_SUCCESS"
            checkpoint["last_partial_at"] = utc_now()
            checkpoint["last_error"] = None
    else:
        batch_status = "FAILED"
        checkpoint["last_error"] = ",".join(business.get("blockers") or [business["status"]])
    checkpoint_store.save(checkpoint)
    # No second Reader pass. Persist the real per-file handoff results above.
    handoff = handoff_totals
    summary = {
        "status": batch_status,
        "business_gates_status": business["status"],
        "business_ok": business["business_ok"],
        "blockers": business.get("blockers") or [],
        "SUCCESS_COUNT": success_count,
        "FAILED_COUNT": failed_count,
        "TIMEOUT_COUNT": timeout_count,
        "RETRYABLE_COUNT": int(counts.get("FAILED_RETRYABLE", 0) or 0),
        "FAILED_FILES": failed_files,
        "stale_lease_recovery": stale_recovery,
        "run_id": run_id,
        "seed": seed,
        "identity_repair": identity_repair,
        "auth": auth,
        "counts": counts,
        "inventory": len(inventory),
        "file_object_count": file_object_count,
        "handoff": handoff,
        "acquisition_calls": None if use_isolation else getattr(acquirer, "acquire_calls", None),
        "acquisition_call_count_basis": 'CHILD_PROCESS_NOT_OBSERVED' if use_isolation else 'IN_PROCESS_OBSERVED',
        "isolated_acquisition_attempts": isolated_acquisition_attempts,
        "acquisition_routing_reachable": counts.get("ELIGIBLE", 0) > 0,
        "frozen_before": frozen_before,
        "frozen_after": frozen_after,
        "frozen_710_unchanged": frozen_unchanged,
        "formal_state_bypass": 0,
        "incremental_only": True,
        "forbidden_source_excluded": forbidden_ok,
    }
    history["runs"].append({
        "status": summary["status"],
        "run_id": run_id,
        "counts": counts,
        "business_ok": business["business_ok"],
        "frozen_710_unchanged": frozen_unchanged,
        "at": utc_now(),
    })
    history["runs"] = history["runs"][-50:]
    history_store.save(history)
    return summary


def canary(root: Path) -> dict[str, Any]:
    """Bounded offline canary: new, unchanged skip, exclusion, atomic mirror, reader handoff."""
    store_dir = root / "mirror"
    calls: list[str] = []

    def make_xlsx(path: Path, marker: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import xlsxwriter
            workbook = xlsxwriter.Workbook(str(path))
            sheet = workbook.add_worksheet()
            sheet.write(0, 0, marker)
            workbook.close()
            return
        except ImportError:
            pass
        try:
            import openpyxl
            wb = openpyxl.Workbook()
            wb.active["A1"] = marker
            wb.save(str(path))
            wb.close()
            return
        except ImportError:
            pass
        import zipfile
        from xml.sax.saxutils import escape
        sheet = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>{escape(marker)}</t></is></c></row></sheetData>'
            "</worksheet>"
        )
        workbook = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
        )
        rels = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"
        )
        wb_rels = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>"
        )
        ctypes = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>"
        )
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("[Content_Types].xml", ctypes)
            zf.writestr("_rels/.rels", rels)
            zf.writestr("xl/workbook.xml", workbook)
            zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
            zf.writestr("xl/worksheets/sheet1.xml", sheet)

    def acquirer(record, staging_dir):
        calls.append(str(record.get("object_id")))
        path = Path(staging_dir) / "artifact.xlsx"
        make_xlsx(path, str(record.get("version")))
        return {"status": "ACQUIRED", "artifact_path": str(path), "format": "xlsx"}

    def fake_reader(info, out_dir: Path):
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "source_object_id": info.get("object_id"),
            "source_version": info.get("source_version"),
            "content_fingerprint": hash_payload(info),
            "reader_result_path": str(out_dir / "reader_result.json"),
        }
        (out_dir / "reader_result.json").write_text(json.dumps(payload), encoding="utf-8")
        return payload

    def record(oid="obj-1", version="v1", **updates):
        value = {
            "object_id": oid, "object_type": "file", "file_type": "NATIVE_XLSX",
            "mirror_policy": "INCLUDED", "mirror_policy_evaluated_at": utc_now(),
            "mirror_backend_candidate": "NATIVE_XLSX_DIRECT", "display_name": "sample.xlsx",
            "filename": "sample.xlsx", "extension": ".xlsx", "version": version,
            "mtime": "2026-09-05T00:00:00+00:00", "size": 12, "parent_id": "parent-1",
            "provider": "wps",
        }
        value.update(updates)
        return value

    paths = {
        "root": store_dir,
        "state_db": store_dir / "state.sqlite3",
        "checkpoint": store_dir / "checkpoint.json",
        "history": store_dir / "run_history.json",
        "lock": store_dir / "run.lock",
        "profile_lock": store_dir / "chromium.lock",
        "handoff": root / "handoff.json",
        "reader_root": store_dir / "incremental_reader",
        "seed_from": root / "missing-seed",
    }
    frozen_before = live_frozen_queue()
    first = run_incremental(inventory=[record()], paths=paths, acquirer=acquirer, skip_auth=True)
    # Patch reader with fake_reader by re-running handoff on first outcomes via second run skip + changed
    second = run_incremental(inventory=[record()], paths=paths, acquirer=acquirer, skip_auth=True)
    excluded = run_incremental(
        inventory=[record(oid="obj-x", filename=f"{EXCLUSION_NEEDLE}.xlsx", display_name=f"{EXCLUSION_NEEDLE}.xlsx")],
        paths=paths, acquirer=acquirer, skip_auth=True,
    )
    changed = run_incremental(
        inventory=[record(version="v2", mtime="2026-09-05T01:00:00+00:00")],
        paths=paths, acquirer=acquirer, skip_auth=True,
    )
    # Direct handoff proof using orchestrator outcomes reconstructed from checkpoint
    orch = MirrorOrchestrator(MirrorStateStore(paths["state_db"]), MirrorArtifactStore(paths["root"]), acquirer)
    first_out = orch.process_object(record(oid="obj-2", version="v9"), run_id="canary-handoff")
    handoff = incremental_reader_handoff(
        [first_out],
        reader_root=paths["reader_root"],
        handoff_path=paths["handoff"],
        read_artifact_fn=fake_reader,
    )
    from pptx_retention_manager import EVICTED_ANALYZED, PptxRetentionLedger
    pptx_oid = "obj-pptx-evicted"
    pptx_version = "v1"
    pptx_mtime = "2026-09-05T00:00:00+00:00"
    pptx_size = 64
    pptx_fp = "fp-pptx-cold-v1"
    pptx_sha = hashlib.sha256(b"cold-pptx-receipt-bytes").hexdigest()
    ledger_path = store_dir / "pptx_retention.sqlite3"
    PptxRetentionLedger(ledger_path).upsert({
        "object_id": pptx_oid,
        "source_version": f"{pptx_oid}@{pptx_version}",
        "source_mtime": pptx_mtime,
        "source_size": pptx_size,
        "metadata_fingerprint": pptx_fp,
        "display_name": "cold.pptx",
        "sha256": pptx_sha,
        "retention_status": EVICTED_ANALYZED,
        "original_artifact_path": str(store_dir / "missing-cold.pptx"),
        "original_size": pptx_size,
    })
    paths["retention_ledger"] = ledger_path

    def pptx_record(**updates):
        value = {
            "object_id": pptx_oid, "object_type": "file", "file_type": "NATIVE_PPTX",
            "mirror_policy": "INCLUDED", "mirror_policy_evaluated_at": utc_now(),
            "mirror_backend_candidate": "NATIVE_PPTX_DIRECT",
            "display_name": "cold.pptx", "filename": "cold.pptx", "extension": ".pptx",
            "version": pptx_version, "mtime": pptx_mtime, "size": pptx_size,
            "metadata_fingerprint": pptx_fp, "parent_id": "parent-1", "provider": "wps",
        }
        value.update(updates)
        return value

    calls_before_cold = list(calls)
    cold = run_incremental(inventory=[pptx_record()], paths=paths, acquirer=acquirer, skip_auth=True)
    cold_acquired = pptx_oid in calls[len(calls_before_cold):]
    calls_after_cold = list(calls)
    changed_pptx = run_incremental(
        inventory=[pptx_record(version="v2", mtime="2026-09-05T02:00:00+00:00", size=65, metadata_fingerprint="fp-pptx-changed")],
        paths=paths, acquirer=acquirer, skip_auth=True,
    )
    changed_acquired = pptx_oid in calls[len(calls_after_cold):]
    frozen_after = live_frozen_queue()
    cold_ok = (
        int(cold["counts"].get("EVICTED_ANALYZED_UNCHANGED") or 0) >= 1
        and int(cold["counts"].get("UNCHANGED") or 0) >= 1
        and not cold_acquired
        and changed_acquired
    )
    # The identity-changed PPTX is permitted to acquire; the stub acquirer writes xlsx
    # so format validation may fail. The contract under test is "acquisition called".
    status_ok = (
        second["counts"].get("UNCHANGED", 0) >= 1
        and excluded["counts"].get("EXCLUDED", 0) >= 1
        and changed["counts"].get("CONTENT_CHANGED", 0) + changed["counts"].get("COMMITTED_LOCAL", 0) >= 1
        and handoff["handed"] >= 1
        and frozen_before.get("queue_hash") == frozen_after.get("queue_hash")
        and cold_ok
    )
    return {
        "status": "PASS" if status_ok else "FAIL",
        "first": first,
        "unchanged_skip": second,
        "exclusion": excluded,
        "content_changed": changed,
        "evicted_analyzed_unchanged": cold,
        "evicted_identity_changed_acquire": changed_pptx,
        "reader_handoff": handoff,
        "acquire_calls": calls,
        "frozen_before": frozen_before,
        "frozen_after": frozen_after,
        "frozen_710_unchanged": frozen_before.get("queue_hash") == frozen_after.get("queue_hash"),
        "WPS_INCREMENTAL_ONLY": True,
        "WPS_FORBIDDEN_SOURCE_EXCLUDED": excluded["counts"].get("EXCLUDED", 0) >= 1,
        "WPS_DURABLE_CHECKPOINT": (paths["checkpoint"]).is_file(),
        "formal_state_bypass": 0,
    }


def mirror_result_exit_code(result: Mapping[str, Any]) -> int:
    """Preserving last-good is recovery safety, not a successful scheduled run."""
    return 0 if (result.get('status') in {'SUCCESS', 'PARTIAL_SUCCESS'} and
                 result.get('business_ok') is True) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canary", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--inventory-json", default="")
    parser.add_argument("--skip-auth", action="store_true")
    parser.add_argument("--worker-single-file", nargs=2, metavar=("PAYLOAD", "OUTCOME"), default=None)
    parser.add_argument("--worker-inventory", default="")
    args = parser.parse_args(argv)
    if args.worker_inventory:
        if chromium_profile_busy():
            raise RuntimeError("INVENTORY_PROFILE_BUSY")
        atomic_write_json(Path(args.worker_inventory), {"documents": discover_inventory(), "scanned_at": utc_now()})
        return 0
    if args.worker_single_file:
        return _run_single_file_worker(args.worker_single_file[0], args.worker_single_file[1])
    if args.canary:
        with __import__("tempfile").TemporaryDirectory(prefix="wps0600-canary-") as tmp:
            payload = canary(Path(tmp))
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, default=str)
        sys.stdout.write("\n")
        return 0 if payload.get("status") == "PASS" else 1
    paths = default_paths()
    if args.status:
        json.dump({
            "checkpoint": read_json(paths["checkpoint"]),
            "history": read_json(paths["history"]),
            "frozen": live_frozen_queue(),
            "chromium_busy": chromium_profile_busy(),
            "formal_state_bypass": 0,
        }, sys.stdout, ensure_ascii=False, indent=2, default=str)
        sys.stdout.write("\n")
        return 0
    lock = FileLeaseLock(paths["lock"], stale_seconds=6 * 3600)
    lock.acquire(run_id=str(uuid.uuid4()), actor=ACTOR)
    try:
        objects = None
        if args.inventory_json:
            raw = json.loads(Path(args.inventory_json).read_text(encoding="utf-8"))
            objects = raw.get("documents", raw) if isinstance(raw, dict) else raw
        inventory = discover_inventory(objects=objects) if objects is not None else discover_inventory_bounded(paths)
        result = run_incremental(inventory=inventory, paths=paths, skip_auth=args.skip_auth)
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2, default=str)
        sys.stdout.write("\n")
        return mirror_result_exit_code(result)
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
