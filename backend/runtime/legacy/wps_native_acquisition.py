#!/usr/bin/env python3
"""R15-A.5.1 — Native XLSX Direct Acquisition Backend.

Provides:
  - NativeAcquisitionResult dataclass
  - is_a5_acceptance_authorized() — process-scoped temporary authorization
  - acquire_native_xlsx() — real download via Playwright browser context
  - validate_xlsx_artifact() — ZIP signature, Content_Types, workbook.xml, openpyxl

Security:
  - No raw response/headers/cookies/tokens saved
  - No Source/SourceVersion created
  - Staging path only: /srv/paw/wps_mirror/_acceptance/native_xlsx/
  - Production registry not modified by this module
"""
from __future__ import annotations
import os, sys, json, hashlib, zipfile, time, shutil
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

DEV_DIR = os.path.dirname(os.path.abspath(__file__))
if DEV_DIR not in sys.path:
    sys.path.insert(0, DEV_DIR)
if "/opt/paw-ksheet-worker" not in sys.path:
    sys.path.append("/opt/paw-ksheet-worker")
from wps_file_registry import is_registered_mirror_backend, is_backend_acquisition_ready, MIRROR_BACKEND_STATUS_REGISTRY
from wps_inventory_v1 import is_mirror_allowed, evaluate_mirror_policy

STAGING_BASE = "/srv/paw/wps_mirror/_acceptance/native_xlsx"
GROUP_ID = "3009705063"


@dataclass
class NativeAcquisitionResult:
    status: str = "BLOCKED"
    error_class: str = "NONE"
    object_id: str = ""
    backend: str = ""
    source_version: str = ""
    source_size: int = 0
    artifact_path: str = ""
    artifact_size: int = 0
    sha256: str = ""
    content_type_observed: str = ""
    file_signature_valid: bool = False
    format_validation_pass: bool = False
    started_at: str = ""
    finished_at: str = ""
    # XLSX validation details
    xlsx_signature_valid: bool = False
    xlsx_zip_valid: bool = False
    content_types_present: bool = False
    workbook_xml_present: bool = False
    openpyxl_read_valid: bool = False
    source_size_match: str = "NOT_COMPARABLE"


# Process-scoped acceptance authorization (not persisted, not production)
_acceptance_overrides = {}


def can_promote_backend(synthetic_pass: bool, run1_pass: bool, run2_pass: bool,
                           sha256_match: bool, artifact_size_match: bool,
                           sample_unchanged: bool, a4_regression_pass: bool,
                           db_regression_pass: bool, side_effect_guard_pass: bool,
                           chromium_clean: bool) -> bool:
    """Pure function: only return True if ALL gate conditions are met."""
    return all([
        synthetic_pass, run1_pass, run2_pass,
        sha256_match, artifact_size_match,
        sample_unchanged, a4_regression_pass,
        db_regression_pass, side_effect_guard_pass,
        chromium_clean,
    ])


def set_acceptance_override(object_id: str, backend: str):
    """Set a process-scoped temporary acceptance authorization for one object+backend."""
    _acceptance_overrides[object_id] = backend


def clear_acceptance_overrides():
    """Clear all acceptance overrides (call after tests)."""
    _acceptance_overrides.clear()


def is_a5_acceptance_authorized(object_record: dict, expected_object_id: str, expected_backend: str) -> bool:
    """Check if a specific object+backend has temporary acceptance authorization.
    
    This is NOT production acquisition_ready. It only allows a specific A5 acceptance run
    to proceed with download for one specific object.
    
    Still validates:
      - mirror_policy == INCLUDED
      - mirror_policy_evaluated_at non-empty
      - object_type == file
      - file_type == NATIVE_XLSX
      - mirror_backend_candidate == NATIVE_XLSX_DIRECT
      - stable object_id matches expected
    """
    if not isinstance(object_record, dict):
        return False
    # Check override exists for this specific object+backend
    override_backend = _acceptance_overrides.get(expected_object_id)
    if override_backend != expected_backend:
        return False
    # Still validate all policy requirements
    policy = object_record.get("mirror_policy")
    evaluated_at = object_record.get("mirror_policy_evaluated_at")
    obj_type = object_record.get("object_type")
    file_type = object_record.get("file_type")
    backend = object_record.get("mirror_backend_candidate")
    obj_id = object_record.get("object_id")
    if policy != "INCLUDED":
        return False
    if not evaluated_at:
        return False
    if obj_type != "file":
        return False
    if file_type not in ("NATIVE_XLSX", "NATIVE_DOCX", "NATIVE_PPTX", "PDF", "WPS_SPREADSHEET"):
        return False
    _backend_to_filetype = {
        "NATIVE_XLSX_DIRECT": "NATIVE_XLSX",
        "NATIVE_DOCX_DIRECT": "NATIVE_DOCX",
        "NATIVE_PPTX_DIRECT": "NATIVE_PPTX",
        "PDF_DIRECT": "PDF",
        "WPS_SPREADSHEET_EXPORT_XLSX": "WPS_SPREADSHEET",
    }
    expected_file_type = _backend_to_filetype.get(expected_backend)
    if expected_file_type and file_type != expected_file_type:
        return False
    if backend != expected_backend:
        return False
    if not is_registered_mirror_backend(expected_backend):
        return False
    if obj_id != expected_object_id:
        return False
    # Check user exclusion rule
    display_name = object_record.get("display_name", "")
    mp, _, _ = evaluate_mirror_policy(display_name)
    if mp == "EXCLUDED":
        return False
    return True


def validate_xlsx_artifact(artifact_path: str, expected_size: int = 0) -> dict:
    """Validate a downloaded XLSX artifact.
    
    Returns dict with:
      - artifact_present: bool
      - artifact_nonzero: bool
      - artifact_size: int
      - sha256: str
      - xlsx_signature_valid: bool (PK magic bytes)
      - xlsx_zip_valid: bool (zipfile.is_zipfile)
      - content_types_present: bool
      - workbook_xml_present: bool
      - openpyxl_read_valid: bool
      - source_size_match: str (YES/NO/NOT_COMPARABLE)
      - is_html_body: bool (detect HTML login/error page)
    """
    result = {
        "artifact_present": False,
        "artifact_nonzero": False,
        "artifact_size": 0,
        "sha256": "",
        "xlsx_signature_valid": False,
        "xlsx_zip_valid": False,
        "content_types_present": False,
        "workbook_xml_present": False,
        "openpyxl_read_valid": False,
        "source_size_match": "NOT_COMPARABLE",
        "is_html_body": False,
    }
    
    if not os.path.exists(artifact_path):
        return result
    result["artifact_present"] = True
    
    file_size = os.path.getsize(artifact_path)
    result["artifact_size"] = file_size
    if file_size > 0:
        result["artifact_nonzero"] = True
    
    # SHA256
    h = hashlib.sha256()
    with open(artifact_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    result["sha256"] = h.hexdigest()
    
    # Size comparison
    if expected_size > 0:
        if file_size == expected_size:
            result["source_size_match"] = "YES"
        else:
            result["source_size_match"] = "NO"
    
    # Read first bytes for PK signature
    with open(artifact_path, "rb") as f:
        magic = f.read(4)
    if magic[:2] == b"PK":
        result["xlsx_signature_valid"] = True
    
    # Check for HTML body (login/error page)
    with open(artifact_path, "rb") as f:
        head = f.read(512).lower()
    if b"<html" in head or b"<!doctype html" in head or b"<head>" in head:
        result["is_html_body"] = True
        return result  # HTML body — not a valid XLSX
    
    # ZIP validation
    if not zipfile.is_zipfile(artifact_path):
        return result
    result["xlsx_zip_valid"] = True
    
    # Check internal structure
    try:
        with zipfile.ZipFile(artifact_path, "r") as zf:
            names = zf.namelist()
            if "[Content_Types].xml" in names:
                result["content_types_present"] = True
            if "xl/workbook.xml" in names:
                result["workbook_xml_present"] = True
            # Try openpyxl read-only validation
            try:
                from openpyxl import load_workbook
                wb = load_workbook(artifact_path, read_only=True, data_only=True)
                wb.close()
                result["openpyxl_read_valid"] = True
            except Exception:
                result["openpyxl_read_valid"] = False
    except Exception:
        pass
    
    return result


def validate_docx_artifact(artifact_path: str, expected_size: int = 0) -> dict:
    """Validate a downloaded DOCX artifact.
    
    Returns dict with:
      - artifact_present: bool
      - artifact_nonzero: bool
      - artifact_size: int
      - sha256: str
      - docx_signature_valid: bool (PK magic bytes)
      - docx_zip_valid: bool (zipfile.is_zipfile)
      - content_types_present: bool
      - document_xml_present: bool
      - rels_present: bool
      - python_docx_read_valid: bool
      - source_size_match: str (YES/NO/NOT_COMPARABLE)
      - is_html_body: bool
      - is_json_body: bool
    """
    result = {
        "artifact_present": False,
        "artifact_nonzero": False,
        "artifact_size": 0,
        "sha256": "",
        "docx_signature_valid": False,
        "docx_zip_valid": False,
        "content_types_present": False,
        "document_xml_present": False,
        "rels_present": False,
        "python_docx_read_valid": False,
        "source_size_match": "NOT_COMPARABLE",
        "is_html_body": False,
        "is_json_body": False,
    }
    
    if not os.path.exists(artifact_path):
        return result
    result["artifact_present"] = True
    
    file_size = os.path.getsize(artifact_path)
    result["artifact_size"] = file_size
    if file_size > 0:
        result["artifact_nonzero"] = True
    
    # SHA256
    h = hashlib.sha256()
    with open(artifact_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    result["sha256"] = h.hexdigest()
    
    # Size comparison
    if expected_size > 0:
        if file_size == expected_size:
            result["source_size_match"] = "YES"
        else:
            result["source_size_match"] = "NO"
    
    # Read first bytes for PK signature
    with open(artifact_path, "rb") as f:
        magic = f.read(4)
    if magic[:2] == b"PK":
        result["docx_signature_valid"] = True
    
    # Check for HTML body (login/error page)
    with open(artifact_path, "rb") as f:
        head = f.read(512).lower()
    if b"<html" in head or b"<!doctype html" in head or b"<head>" in head:
        result["is_html_body"] = True
        return result
    # Check for JSON body
    if head.strip().startswith(b"{") or head.strip().startswith(b"["):
        result["is_json_body"] = True
        return result
    
    # ZIP validation
    if not zipfile.is_zipfile(artifact_path):
        return result
    result["docx_zip_valid"] = True
    
    # Check internal structure
    try:
        with zipfile.ZipFile(artifact_path, "r") as zf:
            names = zf.namelist()
            if "[Content_Types].xml" in names:
                result["content_types_present"] = True
            if "word/document.xml" in names:
                result["document_xml_present"] = True
            if "_rels/.rels" in names:
                result["rels_present"] = True
            # Try python-docx read-only validation
            try:
                from docx import Document
                doc = Document(artifact_path)
                # Just opening successfully is enough — don't require non-empty content
                result["python_docx_read_valid"] = True
            except Exception:
                result["python_docx_read_valid"] = False
    except Exception:
        pass
    
    return result


def validate_pdf_artifact(artifact_path: str, expected_size: int = 0) -> dict:
    result = {
        "artifact_present": False, "artifact_nonzero": False, "artifact_size": 0,
        "sha256": "", "pdf_header_valid": False, "pdf_eof_marker_present": False,
        "pdf_parser_read_valid": False, "pdf_page_count": 0,
        "source_size_match": "NOT_COMPARABLE", "is_html_body": False, "is_json_body": False,
        "format_validation_pass": False,
    }
    if not os.path.exists(artifact_path):
        return result
    result["artifact_present"] = True
    file_size = os.path.getsize(artifact_path)
    result["artifact_size"] = file_size
    if file_size > 0:
        result["artifact_nonzero"] = True
    h = hashlib.sha256()
    with open(artifact_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    result["sha256"] = h.hexdigest()
    if expected_size > 0:
        result["source_size_match"] = "YES" if file_size == expected_size else "NO"
    with open(artifact_path, "rb") as f:
        head = f.read(512).lower()
    if b"<html" in head or b"<!doctype html" in head or b"<head>" in head:
        result["is_html_body"] = True
        return result
    if head.strip().startswith(b"{") or head.strip().startswith(b"["):
        result["is_json_body"] = True
        return result
    with open(artifact_path, "rb") as f:
        magic = f.read(5)
    if magic == b"%PDF-":
        result["pdf_header_valid"] = True
    else:
        return result
    with open(artifact_path, "rb") as f:
        f.seek(max(0, file_size - 1024))
        tail = f.read()
    if b"%%EOF" in tail:
        result["pdf_eof_marker_present"] = True
    try:
        from pypdf import PdfReader
        reader = PdfReader(artifact_path)
        result["pdf_parser_read_valid"] = True
        result["pdf_page_count"] = len(reader.pages)
    except Exception:
        result["pdf_parser_read_valid"] = False
        result["pdf_page_count"] = 0
    result["format_validation_pass"] = (
        result["artifact_present"] and result["artifact_nonzero"] and
        result["pdf_header_valid"] and result["pdf_eof_marker_present"] and
        result["pdf_parser_read_valid"] and result["pdf_page_count"] >= 1 and
        not result["is_html_body"] and not result["is_json_body"]
    )
    return result


def run_acceptance_acquisition_pdf(page, object_record: dict, run_label: str,
                                staging_base: str = STAGING_BASE) -> NativeAcquisitionResult:
    obj_id = object_record.get("object_id", "")
    version = str(object_record.get("version", ""))
    size = int(object_record.get("size", 0) or 0)
    mtime = object_record.get("mtime", 0)
    display_name = object_record.get("display_name", "")
    parent_id = object_record.get("parent_id", "")
    result = NativeAcquisitionResult(
        status="BLOCKED", object_id=obj_id, backend="PDF_DIRECT",
        source_version=version, source_size=size,
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    if not is_a5_acceptance_authorized(object_record, obj_id, "PDF_DIRECT"):
        result.error_class = "BACKEND_NOT_ACCEPTANCE_AUTHORIZED"
        result.status = "BLOCKED"
        result.finished_at = datetime.now(timezone.utc).isoformat()
        return result
    staging_dir = os.path.join(staging_base, obj_id, version, run_label)
    os.makedirs(staging_dir, exist_ok=True)
    artifact_path = os.path.join(staging_dir, "source.pdf")
    result.artifact_path = artifact_path
    acq = acquire_native_xlsx_via_api(page, obj_id, GROUP_ID, staging_dir,
                                        expected_version=version, expected_size=size,
                                        expected_display_name=display_name,
                                        expected_parent_id=parent_id)
    if acq.status == "SUCCESS" and os.path.exists(os.path.join(staging_dir, "source.xlsx")):
        os.rename(os.path.join(staging_dir, "source.xlsx"), artifact_path)
    if acq.status != "SUCCESS":
        result.status = "FAILED"
        result.error_class = acq.error_class
        result.finished_at = datetime.now(timezone.utc).isoformat()
        return result
    validation = validate_pdf_artifact(artifact_path, expected_size=size)
    result.artifact_size = validation["artifact_size"]
    result.sha256 = validation["sha256"]
    result.file_signature_valid = validation["pdf_header_valid"]
    result.xlsx_signature_valid = validation["pdf_header_valid"]
    result.xlsx_zip_valid = validation["pdf_eof_marker_present"]
    result.content_types_present = validation["pdf_parser_read_valid"]
    result.workbook_xml_present = validation["pdf_parser_read_valid"]
    result.openpyxl_read_valid = validation["pdf_parser_read_valid"]
    result.source_size_match = validation["source_size_match"]
    result.content_type_observed = acq.content_type_observed
    all_valid = validation["format_validation_pass"]
    result.format_validation_pass = all_valid
    if all_valid:
        result.status = "SUCCESS"
        result.error_class = "NONE"
    else:
        result.status = "FAILED"
        result.error_class = "PDF_STRUCTURE_INVALID"
    acceptance_data = {
        "object_id": obj_id, "display_name": display_name,
        "backend": "PDF_DIRECT", "inventory_version": version,
        "inventory_size": size, "inventory_mtime": mtime,
        "artifact_size": result.artifact_size, "sha256": result.sha256,
        "format_validation": all_valid, "run_label": run_label,
        "pdf_page_count": validation["pdf_page_count"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    acceptance_path = os.path.join(staging_dir, "acceptance.json")
    with open(acceptance_path, "w") as f:
        json.dump(acceptance_data, f, indent=2, ensure_ascii=False)
    result.finished_at = datetime.now(timezone.utc).isoformat()
    return result


def validate_pptx_artifact(artifact_path: str, expected_size: int = 0) -> dict:
    """Validate a downloaded PPTX artifact."""
    result = {
        "artifact_present": False, "artifact_nonzero": False, "artifact_size": 0,
        "sha256": "", "pptx_signature_valid": False, "pptx_zip_valid": False,
        "content_types_present": False, "presentation_xml_present": False,
        "rels_present": False, "python_pptx_read_valid": False,
        "source_size_match": "NOT_COMPARABLE", "is_html_body": False, "is_json_body": False,
    }
    if not os.path.exists(artifact_path):
        return result
    result["artifact_present"] = True
    file_size = os.path.getsize(artifact_path)
    result["artifact_size"] = file_size
    if file_size > 0:
        result["artifact_nonzero"] = True
    h = hashlib.sha256()
    with open(artifact_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    result["sha256"] = h.hexdigest()
    if expected_size > 0:
        result["source_size_match"] = "YES" if file_size == expected_size else "NO"
    with open(artifact_path, "rb") as f:
        magic = f.read(4)
    if magic[:2] == b"PK":
        result["pptx_signature_valid"] = True
    with open(artifact_path, "rb") as f:
        head = f.read(512).lower()
    if b"<html" in head or b"<!doctype html" in head or b"<head>" in head:
        result["is_html_body"] = True
        return result
    if head.strip().startswith(b"{") or head.strip().startswith(b"["):
        result["is_json_body"] = True
        return result
    if not zipfile.is_zipfile(artifact_path):
        return result
    result["pptx_zip_valid"] = True
    try:
        with zipfile.ZipFile(artifact_path, "r") as zf:
            names = zf.namelist()
            if "[Content_Types].xml" in names:
                result["content_types_present"] = True
            if "ppt/presentation.xml" in names:
                result["presentation_xml_present"] = True
            if "_rels/.rels" in names:
                result["rels_present"] = True
            try:
                from pptx import Presentation
                prs = Presentation(artifact_path)
                result["python_pptx_read_valid"] = True
            except Exception:
                result["python_pptx_read_valid"] = False
    except Exception:
        pass
    return result


def run_acceptance_acquisition_pptx(page, object_record: dict, run_label: str,
                                staging_base: str = STAGING_BASE) -> NativeAcquisitionResult:
    """Run a complete acceptance acquisition for one PPTX object."""
    obj_id = object_record.get("object_id", "")
    version = str(object_record.get("version", ""))
    size = int(object_record.get("size", 0) or 0)
    mtime = object_record.get("mtime", 0)
    display_name = object_record.get("display_name", "")
    parent_id = object_record.get("parent_id", "")
    result = NativeAcquisitionResult(
        status="BLOCKED", object_id=obj_id, backend="NATIVE_PPTX_DIRECT",
        source_version=version, source_size=size,
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    if not is_a5_acceptance_authorized(object_record, obj_id, "NATIVE_PPTX_DIRECT"):
        result.error_class = "BACKEND_NOT_ACCEPTANCE_AUTHORIZED"
        result.status = "BLOCKED"
        result.finished_at = datetime.now(timezone.utc).isoformat()
        return result
    staging_dir = os.path.join(staging_base, obj_id, version, run_label)
    os.makedirs(staging_dir, exist_ok=True)
    artifact_path = os.path.join(staging_dir, "source.pptx")
    result.artifact_path = artifact_path
    acq = acquire_native_xlsx_via_api(page, obj_id, GROUP_ID, staging_dir,
                                        expected_version=version, expected_size=size,
                                        expected_display_name=display_name,
                                        expected_parent_id=parent_id)
    if acq.status == "SUCCESS" and os.path.exists(os.path.join(staging_dir, "source.xlsx")):
        os.rename(os.path.join(staging_dir, "source.xlsx"), artifact_path)
    if acq.status != "SUCCESS":
        result.status = "FAILED"
        result.error_class = acq.error_class
        result.finished_at = datetime.now(timezone.utc).isoformat()
        return result
    validation = validate_pptx_artifact(artifact_path, expected_size=size)
    result.artifact_size = validation["artifact_size"]
    result.sha256 = validation["sha256"]
    result.file_signature_valid = validation["pptx_signature_valid"]
    result.xlsx_signature_valid = validation["pptx_signature_valid"]
    result.xlsx_zip_valid = validation["pptx_zip_valid"]
    result.content_types_present = validation["content_types_present"]
    result.workbook_xml_present = validation["presentation_xml_present"]
    result.openpyxl_read_valid = validation["python_pptx_read_valid"]
    result.source_size_match = validation["source_size_match"]
    result.content_type_observed = acq.content_type_observed
    all_valid = (
        validation["artifact_present"] and validation["artifact_nonzero"] and
        validation["pptx_signature_valid"] and validation["pptx_zip_valid"] and
        validation["content_types_present"] and validation["presentation_xml_present"] and
        validation["python_pptx_read_valid"] and
        not validation["is_html_body"] and not validation["is_json_body"]
    )
    result.format_validation_pass = all_valid
    if all_valid:
        result.status = "SUCCESS"
        result.error_class = "NONE"
    else:
        result.status = "FAILED"
        result.error_class = "PPTX_STRUCTURE_INVALID"
    acceptance_data = {
        "object_id": obj_id, "display_name": display_name,
        "backend": "NATIVE_PPTX_DIRECT", "inventory_version": version,
        "inventory_size": size, "inventory_mtime": mtime,
        "artifact_size": result.artifact_size, "sha256": result.sha256,
        "format_validation": all_valid, "run_label": run_label,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    acceptance_path = os.path.join(staging_dir, "acceptance.json")
    with open(acceptance_path, "w") as f:
        json.dump(acceptance_data, f, indent=2, ensure_ascii=False)
    result.finished_at = datetime.now(timezone.utc).isoformat()
    return result


def acquire_native_xlsx_via_download(page, object_id: str, display_name: str, 
                                      group_id: str, parent_id: str,
                                      staging_dir: str) -> NativeAcquisitionResult:
    """Acquire a native XLSX file via Playwright download event.
    
    Uses page.expect_download() to trigger WPS native download.
    Does NOT manually extract cookies or use requests.Session.
    """
    result = NativeAcquisitionResult(
        status="BLOCKED",
        object_id=object_id,
        backend="NATIVE_XLSX_DIRECT",
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    
    os.makedirs(staging_dir, exist_ok=True)
    artifact_path = os.path.join(staging_dir, "source.xlsx")
    result.artifact_path = artifact_path
    
    try:
        # Navigate to the file's parent folder in WPS Drive
        # Try to trigger download via WPS Drive API or UI
        # Method: Use WPS Drive download API through browser context
        
        # Construct the WPS Drive file download URL pattern
        # WPS Drive API: GET /3rd/drive/api/v5/files/{file_id}/download
        # Fallback: navigate to download API URL to trigger browser download
        meta_url = f"https://365.kdocs.cn/3rd/drive/api/v5/files/{object_id}/metadata"
        meta_resp = page.request.get(meta_url, timeout=30000)
        parent_id_dl = ""
        if meta_resp.status == 200:
            parent_id_dl = str(json.loads(meta_resp.text()).get("fileinfo", {}).get("parentid", ""))
        
        download_url = f"https://365.kdocs.cn/3rd/drive/api/v5/groups/{group_id}/files/{object_id}/download?parentid={parent_id_dl}&support_checksums=true"
        
        # Use page.expect_download() to capture the download
        with page.expect_download(timeout=60000) as download_info:
            # Navigate to download URL — browser will handle auth via existing session
            page.goto(download_url, wait_until="domcontentloaded", timeout=60000)
        
        download = download_info.value
        download.save_as(artifact_path)
        result.status = "SUCCESS"
        result.error_class = "NONE"
        
    except Exception as e:
        err_str = str(e)
        print(f"[A5-DBG] download exception: {type(e).__name__}", flush=True)
        if "timeout" in err_str.lower():
            result.error_class = "DOWNLOAD_ERROR"
        elif "net::" in err_str.lower():
            result.error_class = "HTTP_ERROR"
        else:
            result.error_class = "DOWNLOAD_ERROR"
        result.status = "FAILED"
    
    result.finished_at = datetime.now(timezone.utc).isoformat()
    return result


def acquire_native_xlsx_via_api(page, object_id: str, group_id: str,
                                 staging_dir: str,
                                 expected_version: str = "",
                                 expected_size: int = 0,
                                 expected_display_name: str = "",
                                 expected_parent_id: str = "") -> NativeAcquisitionResult:
    """Acquire a native XLSX file via browser-context API request.
    
    Uses page.request (shares browser auth context) to fetch the file.
    No manual cookie extraction.
    """
    result = NativeAcquisitionResult(
        status="BLOCKED",
        object_id=object_id,
        backend="NATIVE_XLSX_DIRECT",
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    
    os.makedirs(staging_dir, exist_ok=True)
    artifact_path = os.path.join(staging_dir, "source.xlsx")
    result.artifact_path = artifact_path
    
    try:
        # Use browser context's API request capability
        # WPS Drive download endpoint
        # Step 1: Get signed download URL from WPS Drive API
        meta_url = f"https://365.kdocs.cn/3rd/drive/api/v5/files/{object_id}/metadata"
        meta_resp = page.request.get(meta_url, timeout=30000)
        if meta_resp.status != 200:
            result.error_class = "HTTP_ERROR"
            result.status = "FAILED"
            print(f"[A5-DBG] Metadata API failed status={meta_resp.status}", flush=True)
            result.finished_at = datetime.now(timezone.utc).isoformat()
            return result
        
        meta_json = json.loads(meta_resp.text())
        fileinfo = meta_json.get("fileinfo", {})
        
        # Metadata identity validation — fail closed on any mismatch
        meta_fileid = str(fileinfo.get("fileid", ""))
        meta_groupid = str(fileinfo.get("groupid", ""))
        meta_parentid = str(fileinfo.get("parentid", ""))
        meta_fver = str(fileinfo.get("fver", ""))
        meta_fsize = int(fileinfo.get("fsize", 0) or 0)
        meta_fname = fileinfo.get("fname", "")
        
        if meta_fileid != object_id:
            result.error_class = "METADATA_MISMATCH"
            result.status = "FAILED"
            print(f"[A5-DBG] METADATA_MISMATCH: fileid {meta_fileid} != {object_id}", flush=True)
            result.finished_at = datetime.now(timezone.utc).isoformat()
            return result
        if meta_groupid != group_id:
            result.error_class = "METADATA_MISMATCH"
            result.status = "FAILED"
            print(f"[A5-DBG] METADATA_MISMATCH: groupid", flush=True)
            result.finished_at = datetime.now(timezone.utc).isoformat()
            return result
        if expected_parent_id and meta_parentid != expected_parent_id:
            result.error_class = "METADATA_MISMATCH"
            result.status = "FAILED"
            print(f"[A5-DBG] METADATA_MISMATCH: parentid", flush=True)
            result.finished_at = datetime.now(timezone.utc).isoformat()
            return result
        if expected_version and meta_fver != expected_version:
            result.error_class = "METADATA_MISMATCH"
            result.status = "FAILED"
            print(f"[A5-DBG] METADATA_MISMATCH: fver {meta_fver} != {expected_version}", flush=True)
            result.finished_at = datetime.now(timezone.utc).isoformat()
            return result
        if expected_size > 0 and meta_fsize != expected_size:
            result.error_class = "METADATA_MISMATCH"
            result.status = "FAILED"
            print(f"[A5-DBG] METADATA_MISMATCH: fsize {meta_fsize} != {expected_size}", flush=True)
            result.finished_at = datetime.now(timezone.utc).isoformat()
            return result
        if expected_display_name and meta_fname != expected_display_name:
            result.error_class = "METADATA_MISMATCH"
            result.status = "FAILED"
            print(f"[A5-DBG] METADATA_MISMATCH: fname", flush=True)
            result.finished_at = datetime.now(timezone.utc).isoformat()
            return result
        
        parent_id = meta_parentid
        print(f"[A5-DBG] Metadata OK, identity validated", flush=True)
        
        # Step 2: Get signed download URL
        dl_api_url = f"https://365.kdocs.cn/3rd/drive/api/v5/groups/{group_id}/files/{object_id}/download?parentid={parent_id}&support_checksums=true"
        dl_resp = page.request.get(dl_api_url, timeout=30000)
        print(f"[A5-DBG] Download API status={dl_resp.status}", flush=True)
        
        if dl_resp.status != 200:
            result.error_class = "HTTP_ERROR"
            result.status = "FAILED"
            print(f"[A5-DBG] Download API failed status={dl_resp.status}", flush=True)
            result.finished_at = datetime.now(timezone.utc).isoformat()
            return result
        
        dl_json = json.loads(dl_resp.text())
        
        # Download-info validation — fail closed on any mismatch
        dl_result = dl_json.get("result", "")
        dl_fver = str(dl_json.get("fver", ""))
        dl_fsize = int(dl_json.get("fsize", 0) or 0)
        signed_url = dl_json.get("url", "")
        
        if dl_result != "ok":
            result.error_class = "DOWNLOAD_INFO_RESULT_NOT_OK"
            result.status = "FAILED"
            print(f"[A5-DBG] Download-info result={dl_result}", flush=True)
            result.finished_at = datetime.now(timezone.utc).isoformat()
            return result
        if expected_version and dl_fver != expected_version:
            result.error_class = "DOWNLOAD_INFO_VERSION_MISMATCH"
            result.status = "FAILED"
            print(f"[A5-DBG] Download-info fver mismatch", flush=True)
            result.finished_at = datetime.now(timezone.utc).isoformat()
            return result
        if expected_size > 0 and dl_fsize != expected_size:
            result.error_class = "DOWNLOAD_INFO_SIZE_MISMATCH"
            result.status = "FAILED"
            print(f"[A5-DBG] Download-info fsize mismatch", flush=True)
            result.finished_at = datetime.now(timezone.utc).isoformat()
            return result
        if not signed_url:
            result.error_class = "DOWNLOAD_INFO_URL_MISSING"
            result.status = "FAILED"
            print(f"[A5-DBG] No url field in download response", flush=True)
            result.finished_at = datetime.now(timezone.utc).isoformat()
            return result
        
        # Signed URL HTTPS validation — fail closed if not https
        from urllib.parse import urlparse
        parsed = urlparse(signed_url)
        if parsed.scheme != "https":
            result.error_class = "SIGNED_URL_NON_HTTPS"
            result.status = "FAILED"
            print(f"[A5-DBG] SIGNED_URL_NON_HTTPS scheme={parsed.scheme}", flush=True)
            result.finished_at = datetime.now(timezone.utc).isoformat()
            return result
        
        # Sanitized diagnostics only — never log the signed URL itself
        print(f"[A5-DBG] SIGNED_URL_PRESENT=YES SIGNED_URL_SCHEME={parsed.scheme} SIGNED_URL_HOST_CLASS={parsed.hostname.split('.')[0] if parsed.hostname else 'unknown'} SIGNED_URL_LENGTH={len(signed_url)}", flush=True)
        
        # Step 3: Download actual file using signed URL
        response = page.request.get(signed_url, timeout=60000)
        print(f"[A5-DBG] DOWNLOAD_INFO_HTTP_STATUS={response.status}", flush=True)
        
        if response.status != 200:
            result.error_class = "HTTP_ERROR"
            result.status = "FAILED"
            print(f"[A5-DBG] File download failed status={response.status}", flush=True)
            result.finished_at = datetime.now(timezone.utc).isoformat()
            return result
        
        content_type = response.headers.get("content-type", "")
        result.content_type_observed = content_type
        print(f"[A5-DBG] DOWNLOAD_INFO_RESULT=ok body_len={len(response.body())}", flush=True)
        
        # Get body as bytes and write to file
        body = response.body()
        
        if not body or len(body) == 0:
            result.error_class = "EMPTY_FILE"
            result.status = "FAILED"
            result.finished_at = datetime.now(timezone.utc).isoformat()
            return result
        
        with open(artifact_path, "wb") as f:
            f.write(body)
        
        # Artifact size validation — must match expected source size
        if expected_size > 0 and len(body) != expected_size:
            result.error_class = "ARTIFACT_SIZE_MISMATCH"
            result.status = "FAILED"
            print(f"[A5-DBG] Artifact size {len(body)} != expected {expected_size}", flush=True)
            result.finished_at = datetime.now(timezone.utc).isoformat()
            return result
        
        result.status = "SUCCESS"
        result.error_class = "NONE"
        
    except Exception as e:
        err_str = str(e).lower()
        if "timeout" in err_str:
            result.error_class = "DOWNLOAD_ERROR"
        else:
            result.error_class = "UNKNOWN_ERROR"
        result.status = "FAILED"
    
    result.finished_at = datetime.now(timezone.utc).isoformat()
    return result


def acquire_raw_binary_via_api(page, object_id: str, group_id: str,
                               staging_dir: str,
                               expected_version: str = "",
                               expected_size: int = 0,
                               expected_display_name: str = "",
                               expected_parent_id: str = "") -> NativeAcquisitionResult:
    """Persist the provider's original binary stream without parsing it.

    The existing browser-context API implementation already validates object,
    group, parent, version, name, signed-URL scheme, HTTP status and byte
    count before writing the body.  Reuse that proven transport contract but
    do not apply an XLSX validator: legacy office, media, and OTHER files are
    preserved byte-for-byte as raw source artifacts.  This function never
    logs or returns URLs, headers, cookies, or response bodies.
    """
    result = acquire_native_xlsx_via_api(
        page, object_id, group_id, staging_dir,
        expected_version=expected_version,
        expected_size=expected_size,
        expected_display_name=expected_display_name,
        expected_parent_id=expected_parent_id,
    )
    result.backend = "RAW_BINARY_DIRECT"
    source = os.path.join(staging_dir, "source.xlsx")
    target = os.path.join(staging_dir, "source.bin")
    if result.status == "SUCCESS" and os.path.exists(source):
        os.replace(source, target)
        result.artifact_path = target
        result.artifact_size = os.path.getsize(target)
        result.format_validation_pass = True
    else:
        result.artifact_path = target
    return result


def run_acceptance_acquisition_docx(page, object_record: dict, run_label: str,
                                staging_base: str = STAGING_BASE) -> NativeAcquisitionResult:
    """Run a complete acceptance acquisition for one DOCX object.
    
    Steps:
      1. Validate eligibility
      2. Acquire via API (two-step: metadata -> download-info -> signed URL -> file)
      3. Validate artifact as DOCX
      4. Save acceptance.json
    """
    obj_id = object_record.get("object_id", "")
    version = str(object_record.get("version", ""))
    size = int(object_record.get("size", 0) or 0)
    mtime = object_record.get("mtime", 0)
    display_name = object_record.get("display_name", "")
    parent_id = object_record.get("parent_id", "")
    
    result = NativeAcquisitionResult(
        status="BLOCKED",
        object_id=obj_id,
        backend="NATIVE_DOCX_DIRECT",
        source_version=version,
        source_size=size,
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    
    # 1. Validate eligibility
    if not is_a5_acceptance_authorized(object_record, obj_id, "NATIVE_DOCX_DIRECT"):
        result.error_class = "BACKEND_NOT_ACCEPTANCE_AUTHORIZED"
        result.status = "BLOCKED"
        result.finished_at = datetime.now(timezone.utc).isoformat()
        return result
    
    # 2. Staging path
    staging_dir = os.path.join(staging_base, obj_id, version, run_label)
    os.makedirs(staging_dir, exist_ok=True)
    artifact_path = os.path.join(staging_dir, "source.docx")
    result.artifact_path = artifact_path
    
    # 3. Acquire — reuse the two-step download contract
    acq = acquire_native_xlsx_via_api(page, obj_id, GROUP_ID, staging_dir,
                                        expected_version=version,
                                        expected_size=size,
                                        expected_display_name=display_name,
                                        expected_parent_id=parent_id)
    # Rename artifact from source.xlsx to source.docx if download succeeded
    if acq.status == "SUCCESS" and os.path.exists(os.path.join(staging_dir, "source.xlsx")):
        os.rename(os.path.join(staging_dir, "source.xlsx"), artifact_path)
    
    if acq.status != "SUCCESS":
        result.status = "FAILED"
        result.error_class = acq.error_class
        result.finished_at = datetime.now(timezone.utc).isoformat()
        return result
    
    # 4. Validate artifact as DOCX
    validation = validate_docx_artifact(artifact_path, expected_size=size)
    
    result.artifact_size = validation["artifact_size"]
    result.sha256 = validation["sha256"]
    result.file_signature_valid = validation["docx_signature_valid"]
    result.xlsx_signature_valid = validation["docx_signature_valid"]
    result.xlsx_zip_valid = validation["docx_zip_valid"]
    result.content_types_present = validation["content_types_present"]
    result.workbook_xml_present = validation["document_xml_present"]
    result.openpyxl_read_valid = validation["python_docx_read_valid"]
    result.source_size_match = validation["source_size_match"]
    result.content_type_observed = acq.content_type_observed
    
    # Determine final status
    all_valid = (
        validation["artifact_present"] and
        validation["artifact_nonzero"] and
        validation["docx_signature_valid"] and
        validation["docx_zip_valid"] and
        validation["content_types_present"] and
        validation["document_xml_present"] and
        validation["python_docx_read_valid"] and
        not validation["is_html_body"] and
        not validation["is_json_body"]
    )
    result.format_validation_pass = all_valid
    
    if all_valid:
        result.status = "SUCCESS"
        result.error_class = "NONE"
    else:
        result.status = "FAILED"
        if validation["is_html_body"]:
            result.error_class = "DOCX_SIGNATURE_INVALID"
        elif not validation["docx_zip_valid"]:
            result.error_class = "DOCX_STRUCTURE_INVALID"
        elif not validation["content_types_present"]:
            result.error_class = "DOCX_STRUCTURE_INVALID"
        elif not validation["document_xml_present"]:
            result.error_class = "DOCX_STRUCTURE_INVALID"
        else:
            result.error_class = "DOCX_STRUCTURE_INVALID"
    
    # 5. Save minimal acceptance.json
    acceptance_data = {
        "object_id": obj_id,
        "display_name": display_name,
        "backend": "NATIVE_DOCX_DIRECT",
        "inventory_version": version,
        "inventory_size": size,
        "inventory_mtime": mtime,
        "artifact_size": result.artifact_size,
        "sha256": result.sha256,
        "format_validation": all_valid,
        "run_label": run_label,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    acceptance_path = os.path.join(staging_dir, "acceptance.json")
    with open(acceptance_path, "w") as f:
        json.dump(acceptance_data, f, indent=2, ensure_ascii=False)
    
    result.finished_at = datetime.now(timezone.utc).isoformat()
    return result


def run_acceptance_acquisition(page, object_record: dict, run_label: str,
                                staging_base: str = STAGING_BASE) -> NativeAcquisitionResult:
    """Run a complete acceptance acquisition for one object.
    
    Steps:
      1. Validate eligibility
      2. Acquire via API or download
      3. Validate artifact
      4. Save acceptance.json
    """
    obj_id = object_record.get("object_id", "")
    version = str(object_record.get("version", ""))
    size = int(object_record.get("size", 0) or 0)
    mtime = object_record.get("mtime", 0)
    display_name = object_record.get("display_name", "")
    parent_id = object_record.get("parent_id", "")
    
    result = NativeAcquisitionResult(
        status="BLOCKED",
        object_id=obj_id,
        backend="NATIVE_XLSX_DIRECT",
        source_version=version,
        source_size=size,
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    
    # 1. Validate eligibility
    if not is_a5_acceptance_authorized(object_record, obj_id, "NATIVE_XLSX_DIRECT"):
        result.error_class = "BACKEND_NOT_ACCEPTANCE_AUTHORIZED"
        result.status = "BLOCKED"
        result.finished_at = datetime.now(timezone.utc).isoformat()
        return result
    
    # 2. Staging path
    staging_dir = os.path.join(staging_base, obj_id, version, run_label)
    os.makedirs(staging_dir, exist_ok=True)
    artifact_path = os.path.join(staging_dir, "source.xlsx")
    result.artifact_path = artifact_path
    
    # 3. Acquire — try API first, then download
    acq = acquire_native_xlsx_via_api(page, obj_id, GROUP_ID, staging_dir,
                                        expected_version=version,
                                        expected_size=size,
                                        expected_display_name=display_name,
                                        expected_parent_id=parent_id)
    if acq.status != "SUCCESS":
        # Fallback to download event
        acq = acquire_native_xlsx_via_download(page, obj_id, display_name, GROUP_ID, parent_id, staging_dir)
    
    if acq.status != "SUCCESS":
        result.status = "FAILED"
        result.error_class = acq.error_class
        result.finished_at = datetime.now(timezone.utc).isoformat()
        return result
    
    # 4. Validate artifact
    validation = validate_xlsx_artifact(artifact_path, expected_size=size)
    
    result.artifact_size = validation["artifact_size"]
    result.sha256 = validation["sha256"]
    result.file_signature_valid = validation["xlsx_signature_valid"]
    result.xlsx_signature_valid = validation["xlsx_signature_valid"]
    result.xlsx_zip_valid = validation["xlsx_zip_valid"]
    result.content_types_present = validation["content_types_present"]
    result.workbook_xml_present = validation["workbook_xml_present"]
    result.openpyxl_read_valid = validation["openpyxl_read_valid"]
    result.source_size_match = validation["source_size_match"]
    result.content_type_observed = acq.content_type_observed
    
    # Determine final status
    all_valid = (
        validation["artifact_present"] and
        validation["artifact_nonzero"] and
        validation["xlsx_signature_valid"] and
        validation["xlsx_zip_valid"] and
        validation["content_types_present"] and
        validation["workbook_xml_present"] and
        validation["openpyxl_read_valid"] and
        not validation["is_html_body"]
    )
    result.format_validation_pass = all_valid
    
    if all_valid:
        result.status = "SUCCESS"
        result.error_class = "NONE"
    else:
        result.status = "FAILED"
        if validation["is_html_body"]:
            result.error_class = "XLSX_SIGNATURE_INVALID"
        elif not validation["xlsx_zip_valid"]:
            result.error_class = "XLSX_STRUCTURE_INVALID"
        elif not validation["content_types_present"]:
            result.error_class = "XLSX_STRUCTURE_INVALID"
        elif not validation["workbook_xml_present"]:
            result.error_class = "XLSX_STRUCTURE_INVALID"
        else:
            result.error_class = "XLSX_STRUCTURE_INVALID"
    
    # 5. Save minimal acceptance.json
    acceptance_data = {
        "object_id": obj_id,
        "display_name": display_name,
        "backend": "NATIVE_XLSX_DIRECT",
        "inventory_version": version,
        "inventory_size": size,
        "inventory_mtime": mtime,
        "artifact_size": result.artifact_size,
        "sha256": result.sha256,
        "format_validation": all_valid,
        "run_label": run_label,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    acceptance_path = os.path.join(staging_dir, "acceptance.json")
    with open(acceptance_path, "w") as f:
        json.dump(acceptance_data, f, indent=2, ensure_ascii=False)
    
    result.finished_at = datetime.now(timezone.utc).isoformat()
    return result
