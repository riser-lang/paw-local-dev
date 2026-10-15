"""Server-side KSheet export worker.

The only WPS interaction is the visible web UI's "导出为" -> "XLSX文件" flow.
No API, OAuth, cookie, token, session, or document body is persisted.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import tempfile
from urllib.parse import urlsplit, urlunsplit
from pathlib import Path
from typing import Any

from ksheet_pipeline_common import atomic_json, new_batch_id, read_json, utc_now

STATE_ROOT = Path(os.environ.get("PAW_KSHEET_STATE_ROOT", "/srv/paw/ksheet_exports"))
PROFILE = Path(os.environ.get("PAW_KSHEET_PROFILE", "/var/lib/paw-ksheet-worker/chromium-profile"))
MANIFEST = STATE_ROOT / "manifest.json"
WORKER_STATUS = STATE_ROOT / "worker-status.json"
PIPELINE_STATUS = STATE_ROOT / "pipeline-status.json"
TEMP_ROOT = STATE_ROOT / ".tmp"
BROWSER_EXECUTABLE = os.environ.get("PAW_KSHEET_BROWSER_EXECUTABLE", "/snap/bin/chromium")
WPS_HOME = "https://365.kdocs.cn/latest"
ONLY_NAMES = {value.strip() for value in os.environ.get("PAW_KSHEET_ONLY_NAMES", "").split(",") if value.strip()}


def _browser_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {"headless": True, "accept_downloads": True}
    if Path(BROWSER_EXECUTABLE).exists():
        kwargs["executable_path"] = BROWSER_EXECUTABLE
    kwargs["args"] = ["--no-sandbox", "--disable-dev-shm-usage"]
    return kwargs


async def _click_visible_text(page: Any, labels: tuple[str, ...], *, timeout: int = 15_000) -> str:
    """Click the first visible exact label, supporting WPS locale variants."""
    deadline = asyncio.get_running_loop().time() + timeout / 1000
    while asyncio.get_running_loop().time() < deadline:
        for label in labels:
            loc = page.get_by_text(label, exact=True)
            for index in range(await loc.count() - 1, -1, -1):
                candidate = loc.nth(index)
                if await candidate.is_visible():
                    await candidate.click(timeout=min(2_000, timeout))
                    return label
        await page.wait_for_timeout(200)
    raise RuntimeError("UI_LABEL_NOT_FOUND:" + "/".join(labels))


def _stable_url(value: str) -> str:
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _folder_hint(item: dict[str, Any]) -> str:
    parts = str(item.get("source_relative_path", "")).replace("\\", "/").split("/")
    # Search results show the cloud path without the local drive prefix.
    return "/".join(parts[-4:-1]) if len(parts) >= 4 else "/".join(parts[:-1])


async def _resolve_locator(page: Any, item: dict[str, Any]) -> str:
    """Resolve a KSheet by title plus breadcrumb, never by filename alone."""
    name = str(item.get("source_name", "")).removesuffix(".ksheet.wpsonline")
    hint = _folder_hint(item)
    hint_tail = hint.split("/")[-1] if hint else ""
    await page.goto(WPS_HOME, wait_until="domcontentloaded", timeout=60_000)
    search = page.locator('input[placeholder="Search documents by document name or creator"]').first
    await search.fill(name)
    await search.press("Enter")
    await page.wait_for_timeout(3_000)
    rows = page.locator("div.row-item-content-main").filter(has_text=name)
    for index in range(await rows.count()):
        row = rows.nth(index)
        if not await row.is_visible():
            continue
        row_text = (await row.inner_text()).replace("\\", "/")
        if hint_tail and hint_tail not in row_text:
            continue
        detail = row.locator('[action-key="detail"]').first
        if not await detail.is_visible():
            continue
        before = set(page.context.pages)
        await detail.click(timeout=15_000)
        await page.wait_for_timeout(2_000)
        candidates = [p for p in page.context.pages if p not in before]
        opened = candidates[-1] if candidates else page
        await opened.wait_for_timeout(5_000)
        locator = _stable_url(opened.url)
        if "/l/" not in locator:
            raise RuntimeError("LOCATOR_NOT_STABLE")
        item.update({
            "cloud_locator": locator,
            "resolved_url_present": True,
            "resolved_locator_type": "wps_web_search_breadcrumb",
            "resolved_at": utc_now(),
        })
        if opened is not page:
            await opened.close()
        return locator
    # Search full-text results may render the canonical WPS URL as text rather
    # than a clickable row. It is still UI evidence, so accept only a URL whose
    # surrounding text contains both the exact name and the breadcrumb hint.
    body = await page.locator("body").inner_text()
    pattern = re.compile(r"https?://(?:www\.)?kdocs\.cn/l/[A-Za-z0-9]+")
    for match in pattern.finditer(body):
        window = body[max(0, match.start() - 240):match.end() + 80]
        if name in window and "ksheet" in window.casefold() and (not hint_tail or hint_tail in window):
            locator = _stable_url(match.group(0))
            item.update({
                "cloud_locator": locator,
                "resolved_url_present": True,
                "resolved_locator_type": "wps_web_search_displayed_url",
                "resolved_at": utc_now(),
            })
            return locator
    raise RuntimeError("LOCATOR_NOT_FOUND:" + name)


async def _open_export_menu(page: Any) -> str:
    menu = page.locator("button:has(i.kd-icon-menu)").first
    deadline = asyncio.get_running_loop().time() + 30
    while asyncio.get_running_loop().time() < deadline:
        if await menu.is_visible():
            await menu.click(timeout=15_000)
            return await _click_visible_text(page, ("导出为", "Export As"), timeout=15_000)
        await page.wait_for_timeout(500)
    raise RuntimeError("EXPORT_MENU_BUTTON_NOT_FOUND")


async def _export_xlsx(page: Any) -> tuple[Any, str]:
    await _open_export_menu(page)
    async with page.expect_download(timeout=60_000) as download_info:
        label = await _click_visible_text(page, ("XLSX文件", "Export as Excel (.xlsx)"))
    return await download_info.value, label


def _status(**values: Any) -> dict[str, Any]:
    old = read_json(WORKER_STATUS, {})
    old = old if isinstance(old, dict) else {}
    old.update(values)
    old["updated_at"] = utc_now()
    return old


def write_status(**values: Any) -> dict[str, Any]:
    value = _status(**values)
    atomic_json(WORKER_STATUS, value)
    pipeline = read_json(PIPELINE_STATUS, {})
    pipeline = pipeline if isinstance(pipeline, dict) else {}
    pipeline.update({"auth_status": value.get("auth_status", "AUTH_UNKNOWN"), "server_export": value.get("export_status", "EXPORT_IDLE"), "updated_at": value["updated_at"]})
    atomic_json(PIPELINE_STATUS, pipeline)
    return value


async def _auth_ready(page: Any) -> bool:
    """Check only the authenticated page shape; never inspect or save credentials."""
    try:
        await page.goto("https://drive.wps.cn/", wait_until="domcontentloaded", timeout=30_000)
        text = (await page.locator("body").inner_text(timeout=8_000)).casefold()
        if any(host in page.url.casefold() for host in ("account.wps.cn", "account.kdocs.cn", "/passport/")):
            return False
        return bool(page.url.startswith(("https://drive.wps.cn", "https://www.kdocs.cn", "https://365.kdocs.cn"))) and not any(marker in text for marker in ("登录", "扫码", "login", "sign in"))
    except Exception:
        return False


async def export_batch() -> dict[str, Any]:
    manifest = read_json(MANIFEST, {})
    entries = [item for item in manifest.get("entries", []) if isinstance(item, dict) and item.get("enabled", True) and (not ONLY_NAMES or item.get("source_name") in ONLY_NAMES)] if isinstance(manifest, dict) else []
    batch_id = new_batch_id()
    if not entries:
        return write_status(auth_status="AUTH_UNKNOWN", browser_mode="headless", batch_id=batch_id, export_status="EXPORT_PARTIAL", success_count=0, failed_count=0, resource_status="NO_MANIFEST", message="No enabled KSheet entries in server manifest")
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return write_status(auth_status="AUTH_UNKNOWN", browser_mode="unavailable", batch_id=batch_id, export_status="EXPORT_FAILED", success_count=0, failed_count=len(entries), resource_status="PLAYWRIGHT_MISSING", message="Playwright is not installed")
    PROFILE.mkdir(parents=True, exist_ok=True)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    successes = 0
    failures = 0
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch_persistent_context(str(PROFILE), **_browser_kwargs())
        except Exception:
            return write_status(auth_status="AUTH_UNKNOWN", browser_mode="unavailable", batch_id=batch_id, export_status="EXPORT_FAILED", success_count=0, failed_count=len(entries), resource_status="BROWSER_UNAVAILABLE", message="Chromium is unavailable on the server")
        try:
            page = await browser.new_page()
            if not await _auth_ready(page):
                return write_status(auth_status="AUTH_REQUIRED", browser_mode="headless", batch_id=batch_id, export_status="EXPORT_FAILED", success_count=0, failed_count=len(entries), resource_status="AUTH_REQUIRED", message="WPS web session requires one-time login/QR bootstrap")
            started_at = utc_now()
            write_status(auth_status="AUTH_READY", browser_mode="headless", auth_last_checked_at=utc_now(), last_export_started_at=started_at, batch_id=batch_id, export_status="EXPORT_RUNNING", success_count=0, failed_count=0, resource_status="READY", message="Export running")
            for item in entries:
                item_started = asyncio.get_running_loop().time()
                try:
                    locator = item.get("cloud_locator") or await _resolve_locator(page, item)
                    item["open_result"] = "SUCCESS"
                    await page.goto(str(locator), wait_until="domcontentloaded", timeout=60_000)
                    download, export_label = await _export_xlsx(page)
                    item["export_menu_found"] = True
                    item["xlsx_item_found"] = export_label
                    suffix = ".xlsx"
                    temporary = Path(await download.path()) if await download.path() else None
                    if temporary is None or temporary.stat().st_size <= 0:
                        raise RuntimeError("XLSX_INVALID")
                    item["download_result"] = "SUCCESS"
                    item["temp_size"] = temporary.stat().st_size
                    destination = STATE_ROOT / "current" / str(item.get("source_relative_path", "")).replace(".ksheet.wpsonline", ".xlsx")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    staged = Path(tempfile.mkstemp(prefix=".ksheet-", suffix=suffix, dir=TEMP_ROOT)[1])
                    shutil.copyfile(temporary, staged)
                    if staged.stat().st_size <= 0:
                        raise RuntimeError("XLSX_INVALID")
                    # Optional validation keeps the worker runnable without openpyxl.
                    try:
                        import openpyxl
                        workbook = openpyxl.load_workbook(staged, read_only=True, data_only=True)
                        if not workbook.sheetnames:
                            raise RuntimeError("XLSX_INVALID")
                        workbook.close()
                        item["xlsx_valid"] = True
                    except ImportError:
                        item["xlsx_valid"] = "UNVERIFIED_OPENPYXL_MISSING"
                    os.replace(staged, destination)
                    os.chmod(destination, 0o660)
                    item.update({"export_status": "SUCCESS", "result": "PASS", "exported_at": utc_now(), "export_hash": hashlib.sha256(destination.read_bytes()).hexdigest(), "local_xlsx": str(destination), "final_server_path": str(destination), "duration_ms": round((asyncio.get_running_loop().time() - item_started) * 1000)})
                    item.pop("error_code", None)
                    successes += 1
                except Exception as exc:
                    item.update({"export_status": "FAILED", "result": "FAIL", "error_code": str(exc)[:80], "exported_at": utc_now(), "duration_ms": round((asyncio.get_running_loop().time() - item_started) * 1000)})
                    failures += 1
            atomic_json(MANIFEST, manifest)
            enabled_after = [entry for entry in manifest.get("entries", []) if isinstance(entry, dict) and entry.get("enabled", True)]
            manifest_failed = sum(1 for entry in enabled_after if entry.get("export_status") != "SUCCESS")
            manifest_success = len(enabled_after) - manifest_failed
            status = "EXPORT_SUCCESS" if manifest_failed == 0 else "EXPORT_PARTIAL" if manifest_success else "EXPORT_FAILED"
            return write_status(auth_status="AUTH_READY", browser_mode="headless", auth_last_checked_at=utc_now(), last_export_started_at=started_at, last_export_finished_at=utc_now(), batch_id=batch_id, export_status=status, success_count=successes, failed_count=failures, manifest_success_count=manifest_success, manifest_failed_count=manifest_failed, resource_status="READY", message="Export completed")
        finally:
            await browser.close()


async def auth_precheck() -> dict[str, Any]:
    PROFILE.mkdir(parents=True, exist_ok=True)
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return write_status(auth_status="AUTH_UNKNOWN", browser_mode="unavailable", resource_status="PLAYWRIGHT_MISSING", message="Playwright is not installed")
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch_persistent_context(str(PROFILE), **_browser_kwargs())
        except Exception:
            return write_status(auth_status="AUTH_UNKNOWN", browser_mode="unavailable", resource_status="BROWSER_UNAVAILABLE", message="Chromium is unavailable on the server")
        try:
            page = await browser.new_page()
            ready = await _auth_ready(page)
            return write_status(auth_status="AUTH_READY" if ready else "AUTH_REQUIRED", browser_mode="headless", auth_last_checked_at=utc_now(), resource_status="READY" if ready else "AUTH_REQUIRED", message="WPS session is ready" if ready else "One-time WPS login/QR bootstrap required")
        finally:
            await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("auth-precheck", "export", "status"), nargs="?", default="status")
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(read_json(WORKER_STATUS, {"auth_status": "AUTH_UNKNOWN", "export_status": "EXPORT_IDLE"}), ensure_ascii=False))
        return
    if args.command == "auth-precheck":
        result = asyncio.run(auth_precheck())
    else:
        result = asyncio.run(export_batch())
    print(json.dumps(result, ensure_ascii=False))
    # Pipeline failures are business state, not a crash loop. systemd stays
    # green while the persistent dashboard carries AUTH_REQUIRED/EXPORT_FAILED.
    raise SystemExit(0)


if __name__ == "__main__":
    main()
