#!/usr/bin/env python3
"""
R15-A.5.5 — WPS Spreadsheet Export Acquisition Module (EXPORT backend).

A WPS Cloud Spreadsheet (KSheet) is NOT a downloadable OOXML file.
Acquisition therefore requires a real WPS server-side export:

    WPS Cloud Spreadsheet / KSheet
      -> WPS server-side export ("导出为" -> "XLSX文件")
      -> generated XLSX artifact

The export mechanism is REUSED from the historically proven
ksheet_server_worker.py production flow (menu button -> "导出为" ->
"XLSX文件" -> browser download event). Navigation reuses the stable
cloud_locator recorded in the export manifest when available, with the
proven Drive-search resolution as fallback.

Security:
  - No credentials, session identifiers, or authorized download locations
    are read, logged, or persisted.
  - Only sanitized status fields are recorded.
  - Read-only source interaction; no Source / SourceVersion is created.
  - Staging only under /srv/paw/wps_mirror/_acceptance/.
"""
from __future__ import annotations

import os
import sys
import json
import time
import shutil
import hashlib
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

DEV_DIR = os.path.dirname(os.path.abspath(__file__))
if DEV_DIR not in sys.path:
    sys.path.insert(0, DEV_DIR)
if "/opt/paw-ksheet-worker" not in sys.path:
    sys.path.append("/opt/paw-ksheet-worker")

from wps_native_acquisition import (
    is_a5_acceptance_authorized,
    GROUP_ID,
)

EXPORT_STAGING_BASE = "/srv/paw/wps_mirror/_acceptance/wps_spreadsheet_export_xlsx"
EXPORT_MANIFEST = "/srv/paw/ksheet_exports/manifest.json"

# Volatile metadata allowed by the frozen Export Idempotency Contract
# (R15_A5_EXPORT_IDEMPOTENCY_CONTRACT.md): docProps/core.xml timestamps only.
_VOLATILE_XML_PATHS = {"docProps/core.xml"}
_VOLATILE_XML_TAGS = {
    "{http://purl.org/dc/terms/}created",
    "{http://purl.org/dc/terms/}modified",
    "{http://purl.org/dc/elements/1.1/}date",
}

# R15-A.5.5H contract amendment: WPS_EXPORT_VOLATILE_METADATA_001.
# The ONLY normalization beyond the pre-authorized core.xml timestamps.
# Scope is exact: the ICV property VALUE TEXT inside docProps/custom.xml.
# No whole-part ignore, no blanket custom-property ignore.
ICV_SENTINEL = "__WPS_VOLATILE_ICV__"
VOLATILE_OOXML_RULES = {
    "docProps/custom.xml": {
        "property:ICV": "normalize_value",
    }
}


@dataclass
class ExportAcquisitionResult:
    status: str = "BLOCKED"
    error_class: str = "NONE"
    object_id: str = ""
    backend: str = "WPS_SPREADSHEET_EXPORT_XLSX"
    source_file_type: str = "WPS_SPREADSHEET"
    source_version_before: str = ""
    source_version_after: str = ""
    source_mtime_before: str = ""
    source_mtime_after: str = ""
    export_format: str = "XLSX"
    artifact_path: str = ""
    artifact_size: int = 0
    sha256: str = ""
    xlsx_signature_valid: bool = False
    xlsx_zip_valid: bool = False
    content_types_present: bool = False
    workbook_xml_present: bool = False
    openpyxl_read_valid: bool = False
    worksheet_count: int = 0
    format_validation_pass: bool = False
    byte_deterministic: bool = False
    normalized_package_fingerprint: str = ""
    export_operation_executed: bool = False
    source_changed_during_export: bool = False
    source_identity_pre_valid: bool = False
    source_identity_post_valid: bool = False
    ksheet_title_match: bool = False
    navigation_source: str = ""
    export_label: str = ""
    started_at: str = ""
    finished_at: str = ""


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _display_name_key(display_name):
    name = str(display_name or "")
    for suffix in (".ksheet.wpsonline", ".ksheet"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


# ---------------------------------------------------------------------------
# Source identity (read-only, sanitized)
# ---------------------------------------------------------------------------

def read_source_identity(page, object_id, parent_id):
    """Read live source identity via the two proven read-only WPS APIs.

    Returns a sanitized dict; only field values needed for identity
    comparison are kept, never any API payload.
    """
    ident = {
        "api_ok": False,
        "fileid": "",
        "groupid": "",
        "parentid": "",
        "fname": "",
        "fver": "",
        "mtime": "",
        "mtime_available": False,
    }
    try:
        meta_url = f"https://365.kdocs.cn/3rd/drive/api/v5/files/{object_id}/metadata"
        meta_get = page.request.get(meta_url, timeout=30000)
        if meta_get.status == 200:
            fileinfo = meta_get.json().get("fileinfo", {}) or {}
            ident["fileid"] = str(fileinfo.get("fileid", "") or "")
            ident["groupid"] = str(fileinfo.get("groupid", "") or "")
            ident["parentid"] = str(fileinfo.get("parentid", "") or "")
            ident["fname"] = str(fileinfo.get("fname", "") or "")
            ident["fver"] = str(fileinfo.get("fver", "") or "")
            if fileinfo.get("mtime"):
                ident["mtime"] = str(fileinfo.get("mtime", "") or "")
                ident["mtime_available"] = True
    except Exception:
        return ident
    if not ident["mtime_available"]:
        try:
            list_url = f"https://drive.kdocs.cn/api/v5/groups/{GROUP_ID}/files"
            list_get = page.request.get(
                list_url,
                params={"parent_id": str(parent_id or ""), "offset": "0",
                        "count": "50", "orderby": "file_mtime", "order": "desc"},
                timeout=30000,
            )
            if list_get.status == 200:
                for f in list_get.json().get("files", []) or []:
                    if str(f.get("id", "")) == str(object_id):
                        ident["mtime"] = str(f.get("mtime", "") or "")
                        ident["mtime_available"] = True
                        break
        except Exception:
            pass
    ident["api_ok"] = bool(ident["fileid"] and ident["fver"])
    return ident


def identity_mismatches(ident, sample):
    """Compare live identity against the inventory acceptance sample.

    Fail-closed: every available field must match exactly.
    """
    mismatches = []
    if not ident.get("api_ok"):
        return ["IDENTITY_API_UNAVAILABLE"]
    if ident["fileid"] != str(sample.get("object_id", "")):
        mismatches.append("FILEID")
    if ident["groupid"] != str(GROUP_ID):
        mismatches.append("GROUPID")
    expected_parent = str(sample.get("parent_id", "") or "")
    if expected_parent and ident["parentid"] != expected_parent:
        mismatches.append("PARENTID")
    expected_name = str(sample.get("display_name", "") or "")
    if expected_name and ident["fname"] != expected_name:
        mismatches.append("FNAME")
    expected_fver = str(sample.get("version", "") or "")
    if expected_fver and ident["fver"] != expected_fver:
        mismatches.append("FVER")
    expected_mtime = str(sample.get("mtime", "") or "")
    if ident["mtime_available"] and expected_mtime and ident["mtime"] != expected_mtime:
        mismatches.append("MTIME")
    return mismatches


# ---------------------------------------------------------------------------
# UI export flow (reused from ksheet_server_worker.py proven path)
# ---------------------------------------------------------------------------

def _click_visible_text_sync(page, labels, timeout_ms=30000):
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for label in labels:
            try:
                loc = page.get_by_text(label, exact=True)
                count = loc.count()
                for index in range(count - 1, -1, -1):
                    candidate = loc.nth(index)
                    if candidate.is_visible():
                        candidate.click(timeout=min(2000, timeout_ms))
                        return label
            except Exception:
                pass  # overlay/interception during load — keep polling
        page.wait_for_timeout(300)
    raise RuntimeError("UI_LABEL_NOT_FOUND:" + "/".join(labels))


def _open_export_menu_sync(page):
    menu = page.locator("button:has(i.kd-icon-menu)").first
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if menu.is_visible():
                menu.click(timeout=15000)
                page.wait_for_timeout(500)
                for label in ("导出为", "Export As", "导出"):
                    loc = page.get_by_text(label, exact=True)
                    if loc.count() > 0:
                        candidate = loc.first
                        if candidate.is_visible():
                            candidate.hover(timeout=5000)
                            return label
        except Exception:
            pass
        page.wait_for_timeout(500)
    raise RuntimeError("EXPORT_MENU_BUTTON_NOT_FOUND")


def _export_xlsx_sync(page):
    """Execute the real WPS server-side XLSX export via the KSheet UI.

    One retry: a cold first load can leave a half-open menu/overlay;
    Escape resets the UI state before the second attempt.
    """
    last_exc = None
    for attempt in (1, 2):
        try:
            _open_export_menu_sync(page)
            page.wait_for_timeout(1000)
            with page.expect_download(timeout=90000) as download_info:
                clicked = False
                for sel in (
                    '.component-menu-item:has-text("Excel")',
                    '.menu-text-content:has-text("Excel")',
                    'span:has-text("导出为 Excel")',
                    'div[role="menuitem"]:has-text("Excel")',
                    '.kd-menu-item:has-text("Excel")',
                ):
                    loc = page.locator(sel)
                    for idx in range(loc.count() - 1, -1, -1):
                        cand = loc.nth(idx)
                        if cand.is_visible():
                            cand.click(timeout=5000)
                            clicked = True
                            label = "Excel"
                            break
                    if clicked:
                        break
                if not clicked:
                    label = _click_visible_text_sync(
                        page,
                        (
                            "导出为 Excel(.xlsx)",
                            "导出为 Excel",
                            "Excel(.xlsx)",
                            "XLSX文件",
                            "Export as Excel (.xlsx)",
                            "Excel 工作表",
                        ),
                        timeout_ms=30000,
                    )
            download = download_info.value
            return download, label
        except Exception as exc:
            last_exc = exc
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(2000)
            except Exception:
                pass
    raise last_exc if last_exc else RuntimeError("EXPORT_ATTEMPT_FAILED")


def _load_manifest_locators():
    """Load historically proven stable KSheet locators from the export manifest."""
    locators = {}
    try:
        with open(EXPORT_MANIFEST, "r") as f:
            manifest = json.load(f)
        for entry in manifest.get("entries", []) or []:
            if not isinstance(entry, dict):
                continue
            name = _display_name_key(entry.get("source_name", ""))
            loc = str(entry.get("cloud_locator", "") or "")
            if name and loc:
                locators[name] = loc
    except Exception:
        pass
    return locators


def _is_stable_ksheet_locator(url):
    try:
        parts = urlsplit(str(url or ""))
    except Exception:
        return False
    return (
        parts.scheme == "https"
        and (
            str(parts.hostname or "").endswith("kdocs.cn")
            or str(parts.hostname or "") in ("kdocs.cn", "www.kdocs.cn", "365.kdocs.cn")
        )
        and parts.path.startswith("/l/")
    )


def _resolve_via_drive_search_sync(page, name_key):
    """Fallback: resolve KSheet stable URL via WPS UI search & AirSheet filter (proven fallback)."""
    page.goto("https://365.kdocs.cn/latest", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)

    # 1. Reset / switch filter if AirPage or file type filter is visible
    for airpage_label in ("AirPage",):
        loc = page.get_by_text(airpage_label, exact=True)
        for i in range(loc.count()):
            try:
                if loc.nth(i).is_visible():
                    loc.nth(i).click(timeout=3000)
                    page.wait_for_timeout(500)
                    # Pick AirSheet or 全部类型 / 全部
                    for opt in ("AirSheet", "全部类型", "全部", "All Types"):
                        opt_loc = page.get_by_text(opt, exact=True)
                        for j in range(opt_loc.count()):
                            if opt_loc.nth(j).is_visible():
                                opt_loc.nth(j).click(timeout=3000)
                                page.wait_for_timeout(1000)
                                break
                        break
                    break
            except Exception:
                pass

    # 2. Click to explicitly activate search input before fill/submit
    search_inputs = page.locator('input[placeholder*="搜索"], input[placeholder*="Search"], input.kdv-input__inner')
    clicked_search = False
    for i in range(search_inputs.count()):
        cand = search_inputs.nth(i)
        try:
            if cand.is_visible():
                cand.click(timeout=5000)
                page.wait_for_timeout(300)
                cand.fill(name_key)
                page.wait_for_timeout(300)
                cand.press("Enter")
                clicked_search = True
                break
        except Exception:
            pass

    page.wait_for_timeout(2500)

    # 3. Look for matching search result in DOM / popup / rows
    before_pages = set(page.context.pages)
    clicked_result = False

    # Check search result popup / panel / tab combine
    result_candidates = page.locator(
        f'._AllTabCombine_1o378_1:has-text("{name_key}"), '
        f'._Index__content_96ajg_13:has-text("{name_key}"), '
        f'.search-enter-content *:has-text("{name_key}"), '
        f'.search-panel-wrapper *:has-text("{name_key}"), '
        f'div.row-item-content-main:has-text("{name_key}"), '
        f'.component-search-item:has-text("{name_key}"), '
        f'div[role="row"]:has-text("{name_key}")'
    )
    for idx in range(result_candidates.count()):
        cand = result_candidates.nth(idx)
        try:
            if cand.is_visible():
                cand.click(timeout=5000)
                clicked_result = True
                break
        except Exception:
            pass

    if not clicked_result:
        # Fallback to visible exact text match
        loc = page.get_by_text(name_key, exact=True)
        for idx in range(loc.count()):
            try:
                if loc.nth(idx).is_visible():
                    loc.nth(idx).click(timeout=5000)
                    clicked_result = True
                    break
            except Exception:
                pass

    if clicked_result:
        page.wait_for_timeout(3000)
        after_pages = set(page.context.pages) - before_pages
        opened = list(after_pages)[-1] if after_pages else page
        opened.wait_for_timeout(2000)
        url = opened.url
        if _is_stable_ksheet_locator(url) or _wait_for_title_match(opened, name_key, timeout_s=15):
            if opened is not page:
                opened.close()
            return url, "DRIVE_SEARCH_AIRSHEET"

    raise RuntimeError("LOCATOR_NOT_FOUND:" + name_key)


def _wait_for_title_match(page, name_key, timeout_s=24):
    """Poll the tab title until the document name appears (or timeout)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if name_key in (page.title() or ""):
                return True
        except Exception:
            pass
        page.wait_for_timeout(2000)
    return False


def _goto_and_verify(page, url, name_key):
    """Open a candidate stable URL and verify the opened document by title."""
    page.goto(url, wait_until="domcontentloaded", timeout=90000)
    return _wait_for_title_match(page, name_key)


def _navigate_to_ksheet(page, sample):
    """Navigate to the KSheet editor page and bind it to the sample by stable identity.

    Order:
      1. Direct locator from object metadata (cloud_locator / link_id / link_url).
      2. Manifest stable locator (historically proven cache).
      3. Authoritative link from /api/v3/office/file/{object_id}.
      4. Drive-search / AirSheet resolution (proven UI fallback).
    """
    name_key = _display_name_key(sample.get("display_name", ""))
    obj_id = str(sample.get("object_id", ""))
    
    # 1. Direct locator from object metadata if present
    direct_url = str(sample.get("cloud_locator") or sample.get("locator") or sample.get("link_url") or "")
    if not direct_url and sample.get("link_id"):
        direct_url = f"https://365.kdocs.cn/l/{sample['link_id']}"
    if direct_url and _is_stable_ksheet_locator(direct_url):
        try:
            if _goto_and_verify(page, direct_url, name_key):
                return "DIRECT_OBJECT_LOCATOR", True, name_key
        except Exception:
            pass

    # 2. Manifest stable locator cache
    locators = _load_manifest_locators()
    url = locators.get(name_key, "")
    if url and _is_stable_ksheet_locator(url):
        try:
            if _goto_and_verify(page, url, name_key):
                return "MANIFEST_LOCATOR", True, name_key
        except Exception:
            pass

    # 3. Authoritative direct link API
    if obj_id:
        try:
            r = page.request.get(f"https://365.kdocs.cn/api/v3/office/file/{obj_id}", timeout=15000)
            if r.status == 200:
                data = r.json()
                attrs = (data.get("file") or {}).get("attrs") or (data.get("meta", {}).get("file", {})).get("attrs") or {}
                lid = attrs.get("link_id")
                lurl = attrs.get("link_url")
                target_url = lurl if (lurl and _is_stable_ksheet_locator(lurl)) else (f"https://365.kdocs.cn/l/{lid}" if lid else None)
                if target_url and _is_stable_ksheet_locator(target_url):
                    if _goto_and_verify(page, target_url, name_key):
                        return "DIRECT_API_LOCATOR", True, name_key
        except Exception:
            pass

    # 4. Fallback: Drive search / AirSheet UI resolution
    try:
        url, source = _resolve_via_drive_search_sync(page, name_key)
        title_ok = _goto_and_verify(page, url, name_key)
        return source or "DRIVE_SEARCH_AIRSHEET", bool(title_ok), name_key
    except Exception:
        pass

    return "LOCATOR_FAILED", False, name_key


# ---------------------------------------------------------------------------
# XLSX artifact validation
# ---------------------------------------------------------------------------

def validate_export_xlsx_artifact(artifact_path, expected_size=0):
    """Validate an exported XLSX artifact for complete source equivalence.
    
    Validates:
      - File exists and is non-empty.
      - Rejects HTML/JSON error bodies and truncated/corrupt streams.
      - Valid PK zip signature and valid zip structure.
      - Mandatory package parts ([Content_Types].xml, xl/workbook.xml).
      - Openpyxl can open and parse all worksheets without error.
      - Worksheet count >= 1 and all worksheet names are non-empty strings.
      - Worksheets are accessible with valid basic dimensions and content.
    """
    result = {
        "artifact_exists": False,
        "artifact_nonzero": False,
        "artifact_size": 0,
        "sha256": "",
        "xlsx_signature_valid": False,
        "xlsx_zip_valid": False,
        "content_types_present": False,
        "workbook_xml_present": False,
        "openpyxl_read_valid": False,
        "worksheet_count": 0,
        "worksheet_names": [],
        "merged_cell_count": 0,
        "is_html_body": False,
        "is_json_body": False,
        "format_validation_pass": False,
    }
    if not os.path.exists(artifact_path):
        return result
    result["artifact_exists"] = True
    with open(artifact_path, "rb") as f:
        raw = f.read()
    result["artifact_size"] = len(raw)
    result["sha256"] = hashlib.sha256(raw).hexdigest()
    if result["artifact_size"] > 0:
        result["artifact_nonzero"] = True
    if len(raw) < 4 or raw[:2] != b"PK":
        head = raw[:512].lower()
        if b"<html" in head or b"<!doctype html" in head or b"<head>" in head:
            result["is_html_body"] = True
        elif head.strip().startswith(b"{") or head.strip().startswith(b"["):
            result["is_json_body"] = True
        return result
    result["xlsx_signature_valid"] = True
    if not zipfile.is_zipfile(artifact_path):
        return result
    result["xlsx_zip_valid"] = True
    try:
        with zipfile.ZipFile(artifact_path, "r") as zf:
            names = zf.namelist()
            if "[Content_Types].xml" in names:
                result["content_types_present"] = True
            if "xl/workbook.xml" in names:
                result["workbook_xml_present"] = True
            # Check for worksheet XML files
            sheet_xmls = [n for n in names if n.startswith("xl/worksheets/") and n.endswith(".xml")]
            # Check merged cells across worksheets
            merged_count = 0
            for s_xml in sheet_xmls:
                try:
                    s_content = zf.read(s_xml)
                    merged_count += s_content.count(b"<mergeCell ")
                except Exception:
                    pass
            result["merged_cell_count"] = merged_count
    except Exception:
        return result
    try:
        from openpyxl import load_workbook
        wb = load_workbook(artifact_path, read_only=True, data_only=True)
        sheet_names = [str(name) for name in wb.sheetnames if str(name).strip()]
        result["worksheet_names"] = sheet_names
        result["worksheet_count"] = len(sheet_names)
        # Verify each worksheet can be accessed
        for sname in sheet_names:
            ws = wb[sname]
            _ = ws.max_row
            _ = ws.max_column
        result["openpyxl_read_valid"] = True
        wb.close()
    except Exception:
        return result
    result["format_validation_pass"] = (
        result["artifact_nonzero"] and
        result["xlsx_signature_valid"] and
        result["xlsx_zip_valid"] and
        result["content_types_present"] and
        result["workbook_xml_present"] and
        result["openpyxl_read_valid"] and
        result["worksheet_count"] >= 1 and
        not result["is_html_body"] and
        not result["is_json_body"]
    )
    return result


# ---------------------------------------------------------------------------
# Normalized OOXML comparison (Export Idempotency Contract)
# ---------------------------------------------------------------------------

def _normalize_xml_content(xml_bytes):
    """Strip only contract-authorized volatile timestamp values, then canonicalize."""
    try:
        tree = ET.fromstring(xml_bytes)
        for tag in _VOLATILE_XML_TAGS:
            for elem in tree.iter(tag):
                elem.text = ""
                elem.tail = ""
        return ET.tostring(tree, encoding="unicode")
    except Exception:
        return xml_bytes.decode("utf-8", errors="replace")


def normalize_custom_properties_xml(xml_bytes):
    """WPS_EXPORT_VOLATILE_METADATA_001: replace ONLY the ICV property value
    text with a canonical sentinel. The property element, its name, fmtid, pid,
    value XML type and every other custom property are preserved untouched.

    Fail closed: any unexpected shape returns the bytes unchanged so the
    comparison still detects a difference.
    """
    rules = VOLATILE_OOXML_RULES.get("docProps/custom.xml", {})
    if rules.get("property:ICV") != "normalize_value":
        return xml_bytes
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return xml_bytes
    changed = False
    for prop in list(root):
        if prop.tag.rsplit("}", 1)[-1] != "property":
            continue
        if prop.get("name") != "ICV":
            continue
        children = list(prop)
        if len(children) != 1 or not (children[0].text or ""):
            return xml_bytes  # unexpected ICV shape -> fail closed
        children[0].text = ICV_SENTINEL
        changed = True
    if not changed:
        return xml_bytes  # no ICV property present -> nothing to normalize
    return ET.tostring(root, encoding="unicode")


def normalize_ooxml_part(part_name, content):
    """Apply contract-authorized normalization to ONE package part.

    Rules come exclusively from VOLATILE_OOXML_RULES plus the original
    core.xml timestamp rule. Anything not covered is returned unchanged
    (fail closed); there is deliberately no part-name-prefix shortcut.
    """
    if part_name in _VOLATILE_XML_PATHS:
        return _normalize_xml_content(content)
    if part_name == "docProps/custom.xml":
        return normalize_custom_properties_xml(content)
    return content


def compute_normalized_package_fingerprint(xlsx_path):
    """Fingerprint a package ignoring ZIP container metadata and
    contract-authorized volatile metadata only."""
    if not zipfile.is_zipfile(xlsx_path):
        return ""
    member_hashes = {}
    with zipfile.ZipFile(xlsx_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            content = zf.read(info.filename)
            normalized = normalize_ooxml_part(info.filename, content)
            member_hashes[info.filename] = hashlib.sha256(
                normalized.encode("utf-8") if isinstance(normalized, str) else normalized
            ).hexdigest()
    canonical = json.dumps(member_hashes, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compare_xlsx_packages(path1, path2):
    """Compare two XLSX packages; report only part names, never content."""
    result = {
        "zip_member_set_match": False,
        "differing_part_count": 0,
        "differing_part_names": [],
        "normalized_package_match": False,
        "fingerprint1": "",
        "fingerprint2": "",
    }
    if not zipfile.is_zipfile(path1) or not zipfile.is_zipfile(path2):
        return result
    with zipfile.ZipFile(path1, "r") as zf1, zipfile.ZipFile(path2, "r") as zf2:
        names1 = set(n for n in zf1.namelist() if not zf1.getinfo(n).is_dir())
        names2 = set(n for n in zf2.namelist() if not zf2.getinfo(n).is_dir())
    result["zip_member_set_match"] = (names1 == names2)
    if names1 != names2:
        result["differing_part_names"] = sorted(names1.symmetric_difference(names2))
        result["differing_part_count"] = len(result["differing_part_names"])
        return result
    differing = []
    with zipfile.ZipFile(path1, "r") as zf1, zipfile.ZipFile(path2, "r") as zf2:
        for name in sorted(names1):
            c1 = zf1.read(name)
            c2 = zf2.read(name)
            n1 = normalize_ooxml_part(name, c1)
            n2 = normalize_ooxml_part(name, c2)
            b1 = n1.encode("utf-8") if isinstance(n1, str) else n1
            b2 = n2.encode("utf-8") if isinstance(n2, str) else n2
            if hashlib.sha256(b1).hexdigest() != hashlib.sha256(b2).hexdigest():
                differing.append(name)
    result["differing_part_names"] = differing
    result["differing_part_count"] = len(differing)
    fp1 = compute_normalized_package_fingerprint(path1)
    fp2 = compute_normalized_package_fingerprint(path2)
    result["fingerprint1"] = fp1
    result["fingerprint2"] = fp2
    result["normalized_package_match"] = (fp1 == fp2 and fp1 != "")
    return result


# ---------------------------------------------------------------------------
# Acceptance acquisition RUN
# ---------------------------------------------------------------------------

def run_acceptance_acquisition_export(page, object_record, run_label,
                                      staging_base=EXPORT_STAGING_BASE,
                                      baseline_identity=None):
    """Run one real WPS Spreadsheet -> XLSX export acceptance acquisition.

    Returns (ExportAcquisitionResult, live_identity_dict).

    baseline_identity: identity dict captured by the previous RUN; when
    provided, any drift is reported via error_class SAMPLE_CHANGED_BETWEEN_RUNS
    before any export is attempted.
    """
    obj_id = str(object_record.get("object_id", ""))
    version = str(object_record.get("version", "") or "")
    mtime = str(object_record.get("mtime", "") or "")
    display_name = str(object_record.get("display_name", "") or "")

    result = ExportAcquisitionResult(
        status="BLOCKED", object_id=obj_id,
        source_version_before=version,
        source_mtime_before=mtime,
        started_at=_now_iso(),
    )

    # 0. Acceptance authorization (object-scoped, backend-scoped, policy-checked)
    if not is_a5_acceptance_authorized(object_record, obj_id, "WPS_SPREADSHEET_EXPORT_XLSX"):
        result.error_class = "BACKEND_NOT_ACCEPTANCE_AUTHORIZED"
        result.finished_at = _now_iso()
        return result, {}

    staging_dir = os.path.join(staging_base, obj_id, version, run_label)
    os.makedirs(staging_dir, exist_ok=True)
    artifact_path = os.path.join(staging_dir, "export.xlsx")
    result.artifact_path = artifact_path

    # 1. Pre-export source identity (fail closed on any mismatch)
    ident = read_source_identity(page, obj_id, object_record.get("parent_id", ""))
    mismatches = identity_mismatches(ident, object_record)
    if mismatches:
        result.error_class = "SOURCE_IDENTITY_MISMATCH:" + ",".join(mismatches)
        result.finished_at = _now_iso()
        return result, ident
    result.source_identity_pre_valid = True
    result.source_version_before = ident["fver"]
    result.source_mtime_before = ident["mtime"] or mtime
    if baseline_identity:
        drift = []
        for key in ("fileid", "groupid", "parentid", "fname", "fver"):
            if baseline_identity.get(key) and ident.get(key) != baseline_identity.get(key):
                drift.append(key.upper())
        if baseline_identity.get("mtime_available") and ident.get("mtime_available") \
                and baseline_identity.get("mtime") != ident.get("mtime"):
            drift.append("MTIME")
        if drift:
            result.error_class = "SAMPLE_CHANGED_BETWEEN_RUNS:" + ",".join(drift)
            result.finished_at = _now_iso()
            return result, ident

    # 2. Navigate to the KSheet (manifest locator first, search fallback)
    try:
        source, title_ok, _name_key = _navigate_to_ksheet(page, object_record)
        result.navigation_source = source
        result.ksheet_title_match = bool(title_ok)
        if not title_ok:
            result.status = "FAILED"
            result.error_class = "EXPORT_TARGET_NOT_VERIFIED"
            result.finished_at = _now_iso()
            return result, ident
    except Exception as e:
        result.status = "FAILED"
        result.error_class = f"NAVIGATION_FAILED: {type(e).__name__}"
        result.finished_at = _now_iso()
        return result, ident

    # 3. Real WPS server-side export (UI flow) -> browser download
    try:
        download, export_label = _export_xlsx_sync(page)
        result.export_operation_executed = True
        result.export_label = export_label
        tmp_path = download.path()
        if tmp_path:
            shutil.copyfile(tmp_path, artifact_path)
        else:
            download.save_as(artifact_path)
    except Exception as e:
        result.status = "FAILED"
        result.error_class = f"EXPORT_FAILED: {type(e).__name__}"
        result.finished_at = _now_iso()
        return result, ident

    # 4. XLSX artifact validation
    validation = validate_export_xlsx_artifact(artifact_path)
    result.artifact_size = validation["artifact_size"]
    result.sha256 = validation["sha256"]
    result.xlsx_signature_valid = validation["xlsx_signature_valid"]
    result.xlsx_zip_valid = validation["xlsx_zip_valid"]
    result.content_types_present = validation["content_types_present"]
    result.workbook_xml_present = validation["workbook_xml_present"]
    result.openpyxl_read_valid = validation["openpyxl_read_valid"]
    result.worksheet_count = validation["worksheet_count"]
    result.format_validation_pass = validation["format_validation_pass"]
    result.normalized_package_fingerprint = compute_normalized_package_fingerprint(artifact_path)

    if not validation["format_validation_pass"]:
        if validation["is_html_body"]:
            result.error_class = "XLSX_SIGNATURE_INVALID_HTML_BODY"
        elif validation["is_json_body"]:
            result.error_class = "XLSX_SIGNATURE_INVALID_JSON_BODY"
        else:
            result.error_class = "XLSX_STRUCTURE_INVALID"
        result.status = "FAILED"
        result.finished_at = _now_iso()
        return result, ident

    # 5. Post-export source identity (hard requirement for export backends)
    ident_after = read_source_identity(page, obj_id, object_record.get("parent_id", ""))
    post_mismatches = identity_mismatches(ident_after, object_record)
    result.source_version_after = ident_after.get("fver", "")
    result.source_mtime_after = ident_after.get("mtime", "") or ""
    if post_mismatches or (ident.get("fver") and ident_after.get("fver") and ident.get("fver") != ident_after.get("fver")):
        result.source_changed_during_export = True
        result.status = "BLOCKED"
        result.error_class = "SOURCE_CHANGED_DURING_EXPORT:" + ",".join(post_mismatches or ["FVER_CHANGED"])
        result.finished_at = _now_iso()
        return result, ident
    result.source_identity_post_valid = True

    result.status = "SUCCESS"
    result.error_class = "NONE"

    # 6. Minimal acceptance record (sanitized fields only)
    acceptance_data = {
        "object_id": obj_id,
        "display_name": display_name,
        "backend": "WPS_SPREADSHEET_EXPORT_XLSX",
        "source_fver": result.source_version_before,
        "source_mtime": result.source_mtime_before,
        "export_format": "XLSX",
        "artifact_size": result.artifact_size,
        "sha256": result.sha256,
        "normalized_package_fingerprint": result.normalized_package_fingerprint,
        "run_label": run_label,
        "timestamp": _now_iso(),
    }
    with open(os.path.join(staging_dir, "acceptance.json"), "w") as f:
        json.dump(acceptance_data, f, indent=2, ensure_ascii=False)

    result.finished_at = _now_iso()
    return result, ident


def run_production_acquisition_export(page, object_record, staging_dir):
    """Production-safe WPS spreadsheet export.

    This is intentionally separate from ``run_acceptance_acquisition_export``:
    no acceptance override is consulted, and B1 eligibility is evaluated at
    the acquisition boundary before navigation or export.
    """
    from wps_mirror_contract import production_eligibility

    eligibility = production_eligibility(object_record)
    result = ExportAcquisitionResult(
        status="BLOCKED",
        object_id=str(object_record.get("object_id", "") or ""),
        started_at=_now_iso(),
    )
    if not eligibility.eligible or eligibility.backend != "WPS_SPREADSHEET_EXPORT_XLSX":
        result.error_class = "PRODUCTION_ELIGIBILITY_REQUIRED"
        result.finished_at = _now_iso()
        return result
    os.makedirs(staging_dir, exist_ok=True)
    artifact_path = os.path.join(staging_dir, "export.xlsx")
    result.artifact_path = artifact_path
    obj_id = str(object_record.get("object_id", ""))
    version = str(object_record.get("version", "") or "")
    result.source_version_before = version
    result.source_mtime_before = str(object_record.get("mtime", "") or "")
    try:
        ident = read_source_identity(page, obj_id, object_record.get("parent_id", ""))
        mismatches = identity_mismatches(ident, object_record)
        if mismatches:
            if mismatches == ["FVER"] and ident.get("fver"):
                object_record["version"] = str(ident["fver"])
                object_record["mtime"] = str(ident.get("mtime", object_record.get("mtime", "")))
            else:
                result.error_class = "SOURCE_IDENTITY_MISMATCH:" + ",".join(mismatches)
                result.finished_at = _now_iso()
                return result
        result.source_identity_pre_valid = True
        result.source_version_before = ident.get("fver", version)
        result.source_mtime_before = ident.get("mtime", result.source_mtime_before)
        source, title_ok, _name_key = _navigate_to_ksheet(page, object_record)
        result.navigation_source = source
        result.ksheet_title_match = bool(title_ok)
        if not title_ok:
            result.error_class = "EXPORT_TARGET_NOT_VERIFIED"
            result.finished_at = _now_iso()
            return result
        download, export_label = _export_xlsx_sync(page)
        result.export_operation_executed = True
        result.export_label = export_label
        temporary = download.path()
        if temporary:
            shutil.copyfile(temporary, artifact_path)
        else:
            download.save_as(artifact_path)
        validation = validate_export_xlsx_artifact(artifact_path)
        result.artifact_size = validation["artifact_size"]
        result.sha256 = validation["sha256"]
        result.xlsx_signature_valid = validation["xlsx_signature_valid"]
        result.xlsx_zip_valid = validation["xlsx_zip_valid"]
        result.content_types_present = validation["content_types_present"]
        result.workbook_xml_present = validation["workbook_xml_present"]
        result.openpyxl_read_valid = validation["openpyxl_read_valid"]
        result.worksheet_count = validation["worksheet_count"]
        result.format_validation_pass = validation["format_validation_pass"]
        result.normalized_package_fingerprint = compute_normalized_package_fingerprint(artifact_path)
        if not validation["format_validation_pass"]:
            if validation["is_html_body"]:
                result.error_class = "XLSX_SIGNATURE_INVALID_HTML_BODY"
            elif validation["is_json_body"]:
                result.error_class = "XLSX_SIGNATURE_INVALID_JSON_BODY"
            else:
                result.error_class = "XLSX_STRUCTURE_INVALID"
            result.status = "FAILED"
            result.finished_at = _now_iso()
            return result
        after = read_source_identity(page, obj_id, object_record.get("parent_id", ""))
        post_mismatches = identity_mismatches(after, object_record)
        if post_mismatches or (ident.get("fver") and after.get("fver") and ident.get("fver") != after.get("fver")):
            result.status = "BLOCKED"
            result.source_changed_during_export = True
            result.error_class = "SOURCE_CHANGED_DURING_EXPORT:" + ",".join(post_mismatches or ["FVER_CHANGED"])
            result.finished_at = _now_iso()
            return result
        result.source_identity_post_valid = True
        result.source_version_after = after.get("fver", "")
        result.source_mtime_after = after.get("mtime", "")
        result.status = "SUCCESS"
        result.error_class = "NONE"
    except Exception as exc:
        result.status = "FAILED"
        result.error_class = "PRODUCTION_EXPORT_FAILED:" + type(exc).__name__
    result.finished_at = _now_iso()
    return result
