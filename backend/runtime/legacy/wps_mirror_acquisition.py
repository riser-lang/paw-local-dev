#!/usr/bin/env python3
"""R15-B1 production acquisition facade.

Every public acquisition call re-evaluates B1 production eligibility.  The
facade returns sanitized metadata and delegates to the already proven A5
format validators and browser-context acquisition primitives.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

from wps_mirror_contract import ProductionEligibility, production_eligibility


@dataclass
class ProductionAcquisitionResult:
    status: str = "BLOCKED"
    error_class: str = "NONE"
    object_id: str = ""
    backend: str = ""
    source_version: str = ""
    artifact_path: str = ""
    artifact_size: int = 0
    sha256: str = ""
    format: str = ""
    mime_type: str = ""
    format_validation_pass: bool = False
    acquisition_method: str = ""
    started_at: str = ""
    finished_at: str = ""

    def sanitized(self) -> dict:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _blocked(record, eligibility: ProductionEligibility) -> ProductionAcquisitionResult:
    return ProductionAcquisitionResult(
        status="BLOCKED",
        error_class="PRODUCTION_ELIGIBILITY_REQUIRED:" + (eligibility.reason or eligibility.status),
        object_id=str(record.get("object_id", "") or "") if isinstance(record, dict) else "",
        backend=eligibility.backend,
        source_version=str(record.get("version", "") or "") if isinstance(record, dict) else "",
        started_at=_now(),
        finished_at=_now(),
    )

def _from_native(raw, backend: str, file_format: str, mime_type: str, artifact_path: str) -> ProductionAcquisitionResult:
    result = ProductionAcquisitionResult(
        status="ACQUIRED" if getattr(raw, "status", "") == "SUCCESS" else getattr(raw, "status", "FAILED"),
        error_class=getattr(raw, "error_class", "UNKNOWN_ERROR"),
        object_id=str(getattr(raw, "object_id", "") or ""),
        backend=backend,
        source_version=str(getattr(raw, "source_version", "") or ""),
        artifact_path=artifact_path,
        artifact_size=int(getattr(raw, "artifact_size", 0) or 0),
        sha256=str(getattr(raw, "sha256", "") or ""),
        format=file_format,
        mime_type=mime_type,
        format_validation_pass=bool(getattr(raw, "format_validation_pass", False)),
        acquisition_method="browser_context_api",
        started_at=str(getattr(raw, "started_at", "") or _now()),
        finished_at=str(getattr(raw, "finished_at", "") or _now()),
    )
    if result.status == "ACQUIRED" and not result.format_validation_pass:
        result.status = "FAILED"
        result.error_class = "FORMAT_VALIDATION_REQUIRED"
    return result


def acquire_production(page, object_record: dict, staging_dir: str | os.PathLike[str]) -> ProductionAcquisitionResult:
    """Acquire one eligible object through the exact six-backend dispatcher."""
    eligibility = production_eligibility(object_record)
    if not eligibility.eligible:
        return _blocked(object_record, eligibility)
    target = Path(staging_dir)
    target.mkdir(parents=True, exist_ok=True)
    backend = eligibility.backend
    version = str(object_record.get("version", object_record.get("source_version", "")) or "")
    try:
        if backend in {"NATIVE_XLSX_DIRECT", "NATIVE_DOCX_DIRECT", "NATIVE_PPTX_DIRECT", "PDF_DIRECT"}:
            from wps_native_acquisition import (
                GROUP_ID,
                acquire_native_xlsx_via_api,
                validate_docx_artifact,
                validate_pdf_artifact,
                validate_pptx_artifact,
                validate_xlsx_artifact,
            )
            raw = acquire_native_xlsx_via_api(
                page,
                str(object_record["object_id"]),
                GROUP_ID,
                str(target),
                expected_version=version,
                expected_size=int(object_record.get("size", 0) or 0),
                expected_display_name=str(object_record.get("display_name", "") or ""),
                expected_parent_id=str(object_record.get("parent_id", "") or ""),
            )
            suffix, mime = {
                "NATIVE_XLSX_DIRECT": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                "NATIVE_DOCX_DIRECT": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                "NATIVE_PPTX_DIRECT": ("pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
                "PDF_DIRECT": ("pdf", "application/pdf"),
            }[backend]
            source_path = target / "source.xlsx"
            artifact_path = target / ("source." + suffix)
            if raw.status == "SUCCESS" and suffix != "xlsx" and source_path.exists():
                os.replace(source_path, artifact_path)
            elif suffix == "xlsx":
                artifact_path = source_path
            if raw.status != "SUCCESS":
                return _from_native(raw, backend, suffix, mime, str(artifact_path))
            validators = {
                "xlsx": validate_xlsx_artifact,
                "docx": validate_docx_artifact,
                "pptx": validate_pptx_artifact,
                "pdf": validate_pdf_artifact,
            }
            validation = validators[suffix](str(artifact_path), expected_size=int(object_record.get("size", 0) or 0))
            raw.artifact_path = str(artifact_path)
            raw.artifact_size = validation.get("artifact_size", 0)
            raw.sha256 = validation.get("sha256", "")
            if "format_validation_pass" in validation:
                raw.format_validation_pass = bool(validation.get("format_validation_pass"))
            else:
                pass_keys = {
                    "xlsx": ("artifact_present", "artifact_nonzero", "xlsx_signature_valid", "xlsx_zip_valid", "content_types_present", "workbook_xml_present", "openpyxl_read_valid"),
                    "docx": ("artifact_present", "artifact_nonzero", "docx_signature_valid", "docx_zip_valid", "content_types_present", "document_xml_present", "python_docx_read_valid"),
                    "pptx": ("artifact_present", "artifact_nonzero", "pptx_signature_valid", "pptx_zip_valid", "content_types_present", "presentation_xml_present", "python_pptx_read_valid"),
                    "pdf": ("artifact_present", "artifact_nonzero", "pdf_header_valid", "pdf_eof_marker_present", "pdf_parser_read_valid", "pdf_page_count"),
                }
                raw.format_validation_pass = all(validation.get(key, False) for key in pass_keys[suffix]) and not validation.get("is_html_body", False) and not validation.get("is_json_body", False)
            if not raw.format_validation_pass:
                raw.status = "FAILED"
                raw.error_class = "FORMAT_VALIDATION_FAILED"
            return _from_native(raw, backend, suffix, mime, str(artifact_path))

        if backend == "RAW_BINARY_DIRECT":
            from wps_native_acquisition import acquire_raw_binary_via_api, GROUP_ID
            raw = acquire_raw_binary_via_api(
                page,
                str(object_record["object_id"]),
                GROUP_ID,
                str(target),
                expected_version=version,
                expected_size=int(object_record.get("size", 0) or 0),
                expected_display_name=str(object_record.get("display_name", "") or ""),
                expected_parent_id=str(object_record.get("parent_id", "") or ""),
            )
            name = str(object_record.get("filename") or object_record.get("display_name") or "")
            suffix = name.rsplit(".", 1)[-1].casefold() if "." in name.rsplit("/", 1)[-1] else "bin"
            return ProductionAcquisitionResult(
                status="ACQUIRED" if raw.status == "SUCCESS" else raw.status,
                error_class=raw.error_class,
                object_id=raw.object_id,
                backend=backend,
                source_version=version,
                artifact_path=raw.artifact_path,
                artifact_size=raw.artifact_size,
                sha256=raw.sha256,
                format=suffix,
                mime_type="application/octet-stream",
                format_validation_pass=bool(raw.status == "SUCCESS" and raw.format_validation_pass),
                acquisition_method="browser_context_api_raw_stream",
                started_at=raw.started_at,
                finished_at=raw.finished_at,
            )

        if backend == "WPS_SPREADSHEET_EXPORT_XLSX":
            from wps_export_acquisition import run_production_acquisition_export
            raw = run_production_acquisition_export(page, object_record, str(target))
            return ProductionAcquisitionResult(
                status="ACQUIRED" if raw.status == "SUCCESS" else raw.status,
                error_class=raw.error_class,
                object_id=raw.object_id,
                backend=backend,
                source_version=raw.source_version_after or raw.source_version_before,
                artifact_path=raw.artifact_path,
                artifact_size=raw.artifact_size,
                sha256=raw.sha256,
                format="xlsx",
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                format_validation_pass=bool(raw.xlsx_signature_valid and raw.xlsx_zip_valid and raw.format_validation_pass),
                acquisition_method="wps_ui_export_download",
                started_at=raw.started_at,
                finished_at=raw.finished_at,
            )

        if backend == "WPS_DOCUMENT_EXPORT_DOCX":
            from wps_airpage_export_acquisition import run_production_airpage_docx_acquisition
            raw = run_production_airpage_docx_acquisition(page, object_record, str(target))
            return ProductionAcquisitionResult(
                status="ACQUIRED" if raw.status == "SUCCESS" else raw.status,
                error_class=raw.error_class,
                object_id=raw.object_id,
                backend=backend,
                source_version=version,
                artifact_path=raw.artifact_path,
                artifact_size=raw.artifact_size,
                sha256=raw.sha256,
                format="docx",
                mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                format_validation_pass=bool(raw.docx_signature_valid and raw.docx_zip_valid and raw.format_validation_pass),
                acquisition_method=raw.response_capture_method or "fetch_response_stage",
                started_at=raw.started_at,
                finished_at=raw.finished_at,
            )
    except Exception as exc:
        missing = ""
        if isinstance(exc, ImportError):
            missing = ":" + str(getattr(exc, "name", "") or "")
        return ProductionAcquisitionResult(
            status="FAILED", error_class="PRODUCTION_ACQUISITION_FAILED:" + type(exc).__name__ + missing,
            object_id=str(object_record.get("object_id", "") or ""), backend=backend,
            source_version=version, started_at=_now(), finished_at=_now(),
        )
    return ProductionAcquisitionResult(
        status="BLOCKED", error_class="BLOCKED_BACKEND", object_id=str(object_record.get("object_id", "") or ""),
        backend=backend, source_version=version, started_at=_now(), finished_at=_now(),
    )
