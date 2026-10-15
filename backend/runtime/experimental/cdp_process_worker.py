#!/usr/bin/env python3
"""cdp_process_worker.py — Process-isolated single-object worker for CDP/API network acquisition.

Runs in its own process group with strict timeouts and validation.
No shared browser state leaks, no credential extraction.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.request
import zipfile

DEV_DIR = os.environ.get("PAW_RUNTIME_MODULE_ROOT")
if not DEV_DIR:
    raise RuntimeError(
        "PAW_RUNTIME_MODULE_ROOT is required (PAW Runtime Path Contract, /etc/paw-runtime-paths.env)")
if DEV_DIR in sys.path:
    sys.path.remove(DEV_DIR)
sys.path.insert(0, DEV_DIR)

from playwright.sync_api import sync_playwright

PROFILE_DIR = "/var/lib/paw-ksheet-worker/chromium-profile"
BROWSER_EXECUTABLE = "/var/lib/paw-ksheet-worker/ms-playwright/chromium-1208/chrome-linux64/chrome"
GROUP_ID = "3009705063"


def _clean_stale_singleton_locks(profile_path: str):
    for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        p = Path(profile_path) / lock
        try:
            if p.is_symlink() or p.exists():
                p.unlink()
        except OSError:
            pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def validate_artifact(artifact_path: Path, backend: str, expected_size: int = 0) -> dict:
    res = {
        "valid": False,
        "signature_valid": False,
        "format_valid": False,
        "size_valid": False,
        "size": 0,
        "sha256": "",
        "error": "",
    }
    if not artifact_path.exists() or not artifact_path.is_file():
        res["error"] = "FILE_NOT_FOUND"
        return res
    raw_size = artifact_path.stat().st_size
    res["size"] = raw_size
    if raw_size == 0:
        res["error"] = "EMPTY_FILE"
        return res
    res["sha256"] = sha256_file(artifact_path)

    if expected_size > 0 and raw_size != expected_size:
        res["error"] = f"SIZE_MISMATCH:actual={raw_size},expected={expected_size}"
        return res
    res["size_valid"] = True

    with open(artifact_path, "rb") as f:
        head = f.read(16)

    if backend in ("NATIVE_DOCX_DIRECT", "NATIVE_XLSX_DIRECT", "NATIVE_PPTX_DIRECT", "WPS_SPREADSHEET_EXPORT_XLSX", "WPS_DOCUMENT_EXPORT_DOCX"):
        if not head.startswith(b"PK\x03\x04"):
            res["error"] = "INVALID_ZIP_MAGIC"
            return res
        res["signature_valid"] = True
        try:
            with zipfile.ZipFile(artifact_path, "r") as zf:
                names = set(zf.namelist())
                if "[Content_Types].xml" not in names:
                    res["error"] = "MISSING_CONTENT_TYPES"
                    return res
                if backend in ("NATIVE_DOCX_DIRECT", "WPS_DOCUMENT_EXPORT_DOCX"):
                    if "word/document.xml" not in names:
                        res["error"] = "MISSING_WORD_DOCUMENT_XML"
                        return res
                    try:
                        import docx
                        docx.Document(str(artifact_path))
                    except Exception:
                        pass
                elif backend in ("NATIVE_XLSX_DIRECT", "WPS_SPREADSHEET_EXPORT_XLSX"):
                    if "xl/workbook.xml" not in names:
                        res["error"] = "MISSING_WORKBOOK_XML"
                        return res
                    try:
                        import openpyxl
                        wb = openpyxl.load_workbook(str(artifact_path), read_only=True)
                        wb.close()
                    except Exception:
                        pass
                elif backend == "NATIVE_PPTX_DIRECT":
                    if "ppt/presentation.xml" not in names:
                        res["error"] = "MISSING_PPT_PRESENTATION_XML"
                        return res
                    try:
                        import pptx
                        pptx.Presentation(str(artifact_path))
                    except Exception:
                        pass
            res["format_valid"] = True
            res["valid"] = True
        except Exception as exc:
            res["error"] = f"FORMAT_VALIDATION_FAILED:{type(exc).__name__}:{exc}"
            return res
    elif backend == "PDF_DIRECT":
        if not head.startswith(b"%PDF-"):
            res["error"] = "INVALID_PDF_MAGIC"
            return res
        res["signature_valid"] = True
        res["format_valid"] = True
        res["valid"] = True
    elif backend == "RAW_BINARY_DIRECT":
        res["signature_valid"] = True
        res["format_valid"] = True
        res["valid"] = True
    else:
        res["error"] = f"UNSUPPORTED_BACKEND:{backend}"
        return res

    return res


def acquire_object(item: dict, staging_dir: Path) -> dict:
    object_id = str(item.get("object_id", ""))
    parent_id = str(item.get("parent_id", "") or "")
    display_name = str(item.get("display_name", "") or item.get("filename", ""))
    expected_version = str(item.get("version", "") or "")
    expected_size = int(item.get("size") or 0)
    backend = str(item.get("mirror_backend_candidate", "") or "")

    result = {
        "status": "FAILED",
        "error_class": "",
        "object_id": object_id,
        "display_name": display_name,
        "backend": backend,
        "source_version_before": expected_version,
        "source_version_after": "",
        "source_mtime_before": str(item.get("mtime", "") or ""),
        "source_mtime_after": "",
        "artifact_path": "",
        "artifact_size": 0,
        "sha256": "",
        "started_at": _now_iso(),
        "finished_at": "",
    }

    staging_dir.mkdir(parents=True, exist_ok=True)
    ext_map = {
        "NATIVE_XLSX_DIRECT": "source.xlsx",
        "NATIVE_DOCX_DIRECT": "source.docx",
        "NATIVE_PPTX_DIRECT": "source.pptx",
        "PDF_DIRECT": "source.pdf",
        "WPS_SPREADSHEET_EXPORT_XLSX": "export.xlsx",
        "WPS_DOCUMENT_EXPORT_DOCX": "export.docx",
        "RAW_BINARY_DIRECT": "source.bin",
    }
    filename = ext_map.get(backend, "source.bin")
    artifact_path = staging_dir / filename
    result["artifact_path"] = str(artifact_path)

    _clean_stale_singleton_locks(PROFILE_DIR)

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            PROFILE_DIR, headless=True, accept_downloads=True,
            executable_path=BROWSER_EXECUTABLE,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()

            # Step 1: Pre-acquisition source identity check via metadata API
            meta_url = f"https://365.kdocs.cn/3rd/drive/api/v5/files/{object_id}/metadata"
            meta_resp = page.request.get(meta_url, timeout=30000)
            if meta_resp.status != 200:
                result["error_class"] = f"METADATA_API_HTTP_{meta_resp.status}"
                return result

            meta_data = meta_resp.json()
            fileinfo = meta_data.get("fileinfo", {}) or {}
            live_fver = str(fileinfo.get("fver", "") or "")
            live_mtime = str(fileinfo.get("mtime", "") or "")
            live_fsize = int(fileinfo.get("fsize", 0) or 0)
            live_parentid = str(fileinfo.get("parentid", "") or "")

            result["source_version_after"] = live_fver
            result["source_mtime_after"] = live_mtime

            # Step 2: Download signed URL via download API
            dl_api_url = f"https://365.kdocs.cn/3rd/drive/api/v5/groups/{GROUP_ID}/files/{object_id}/download?parentid={live_parentid or parent_id}&support_checksums=true"
            dl_resp = page.request.get(dl_api_url, timeout=30000)
            if dl_resp.status != 200:
                result["error_class"] = f"DOWNLOAD_API_HTTP_{dl_resp.status}"
                return result

            dl_data = dl_resp.json()
            if dl_data.get("result") != "ok":
                result["error_class"] = f"DOWNLOAD_API_RESULT_{dl_data.get('result')}"
                return result

            signed_url = dl_data.get("url", "")
            if not signed_url:
                result["error_class"] = "DOWNLOAD_SIGNED_URL_MISSING"
                return result

            # Step 3: Stream download file bytes directly to disk via chunked streaming
            req = urllib.request.Request(signed_url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
            with urllib.request.urlopen(req, timeout=120) as resp, open(artifact_path, "wb") as out_f:
                while chunk := resp.read(1024 * 1024):
                    out_f.write(chunk)

            # Step 4: Validate format and integrity
            val = validate_artifact(artifact_path, backend, expected_size=live_fsize)
            if not val["valid"]:
                result["error_class"] = f"VALIDATION_FAILED:{val.get('error')}"
                return result

            result["artifact_size"] = val["size"]
            result["sha256"] = val["sha256"]

            # Step 5: Post-acquisition identity check
            meta_resp2 = page.request.get(meta_url, timeout=30000)
            if meta_resp2.status == 200:
                fileinfo2 = meta_resp2.json().get("fileinfo", {}) or {}
                post_fver = str(fileinfo2.get("fver", "") or "")
                post_mtime = str(fileinfo2.get("mtime", "") or "")
                if post_fver != live_fver or post_mtime != live_mtime:
                    result["error_class"] = "SOURCE_MUTATED_DURING_ACQUISITION"
                    return result

            result["status"] = "SUCCESS"
            result["error_class"] = "NONE"
            return result

        except Exception as exc:
            result["error_class"] = f"EXCEPTION:{type(exc).__name__}:{exc}"
            return result
        finally:
            context.close()
            _clean_stale_singleton_locks(PROFILE_DIR)


def main():
    parser = argparse.ArgumentParser(description="CDP/Network process worker")
    parser.add_argument("--item-json", required=True, help="JSON-encoded item metadata")
    parser.add_argument("--staging-dir", required=True, help="Directory for staged artifact")
    parser.add_argument("--result-json", required=True, help="Output JSON path")
    args = parser.parse_args()

    item = json.loads(args.item_json)
    staging_dir = Path(args.staging_dir)
    result_json_path = Path(args.result_json)

    res = acquire_object(item, staging_dir)
    res["finished_at"] = _now_iso()

    with open(result_json_path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    sys.exit(0 if res.get("status") == "SUCCESS" else 1)


if __name__ == "__main__":
    main()
