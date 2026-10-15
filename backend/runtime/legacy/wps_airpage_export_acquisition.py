#!/usr/bin/env python3
"""R15-A.5.6 bounded AirPage -> DOCX acquisition.

This module deliberately does not classify arbitrary WPS objects as AirPage.
It is an acquisition adapter for the two named, controlled A5.6 samples only.
The caller must bind the opened page to the supplied sample before the export
operation is allowed.  No browser storage, credentials, headers, or response
payloads are read or persisted.

The provider documentation reviewed for R15-A.5.6 documents AirPage as an
``otl`` Smart Document and documents a generic conversion API whose published
DOCX input list does not include ``otl``.  Consequently this module uses only
the observed WPS web UI export operation and remains separate from the formal
classifier and readiness registry.  The only response payload it reads is the
matched controlled DOCX response body; unmatched responses and raw JSON are
never persisted.
"""
from __future__ import annotations

import hashlib
import base64
import io
import os
import re
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

GROUP_ID = "3009705063"
TARGET_FOLDER_ID = "485481025119"
BACKEND = "WPS_DOCUMENT_EXPORT_DOCX"
EXPORT_FORMAT = "DOCX"

CONTROLLED_AIRPAGE_SAMPLE_NAMES = frozenset({
    "PAW_A5_6_KDOC_ANCHORED_20260828_Q9M4",
    "PAW_A5_6_KDOC_IDENTITY_20260828_X7Q9",
})
DOCX_EXPORT_LABELS = (
    "DOCX文件", "DOCX 文件", "Word文档", "Word 文档",
    "Export as Word (.docx)", "Microsoft Word (.docx)",
)

_RUN_DIR = Path(os.environ.get("RUN_DIR", "/home/ubuntu/paw-agent-runs/r15-a5-6-closure4-visible"))
_DEFAULT_ARTIFACT_DIR = _RUN_DIR / "artifacts"
_MAX_DOCX_BODY_BYTES = 100 * 1024 * 1024


@dataclass
class AirPageDocxAcquisitionResult:
    status: str = "BLOCKED"
    error_class: str = "NONE"
    object_id: str = ""
    backend: str = BACKEND
    source_file_type: str = "AIRPAGE"
    export_format: str = EXPORT_FORMAT
    artifact_path: str = ""
    artifact_size: int = 0
    sha256: str = ""
    docx_signature_valid: bool = False
    docx_zip_valid: bool = False
    content_types_present: bool = False
    document_xml_present: bool = False
    python_docx_read_valid: bool = False
    response_capture_method: str = ""
    matched_response: bool = False
    response_status: int = 0
    response_content_type: str = ""
    export_click_attempts: int = 0
    export_operation_executed: bool = False
    controlled_sample_bound: bool = False
    title_match: bool = False
    stable_url_match: bool = False
    started_at: str = ""
    finished_at: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sample_name(sample: dict) -> str:
    return str(sample.get("display_name", "") or "")


def is_controlled_airpage_sample(sample: dict) -> bool:
    """Return True only for the exact A5.6 controlled names and folder."""
    if not isinstance(sample, dict):
        return False
    return (
        bool(str(sample.get("object_id", "") or ""))
        and
        _sample_name(sample) in CONTROLLED_AIRPAGE_SAMPLE_NAMES
        and str(sample.get("group_id", "") or "") == GROUP_ID
        and str(sample.get("parent_id", "") or "") == TARGET_FOLDER_ID
        and sample.get("object_type", "file") == "file"
    )


def is_stable_document_locator(url: str) -> bool:
    """Accept only a canonical HTTPS WPS document URL as a navigation hint."""
    try:
        parts = urlsplit(str(url or ""))
    except Exception:
        return False
    return (
        parts.scheme == "https"
        and str(parts.hostname or "").casefold() in {"kdocs.cn", "www.kdocs.cn", "365.kdocs.cn"}
        and parts.path.startswith("/l/")
        and len(parts.path.split("/")) == 3
        and bool(parts.path.rsplit("/", 1)[-1])
        and not parts.query
        and not parts.fragment
    )


def validate_airpage_docx_artifact(artifact_path: str | os.PathLike[str]) -> dict:
    """Validate only the DOCX container/parts needed by A5.6 acceptance.

    The function reads the artifact bytes locally and returns sanitized facts;
    it never returns document text or package contents.
    """
    path = Path(artifact_path)
    result = {
        "artifact_exists": False,
        "artifact_nonzero": False,
        "artifact_size": 0,
        "sha256": "",
        "docx_signature_valid": False,
        "docx_zip_valid": False,
        "content_types_present": False,
        "document_xml_present": False,
        "python_docx_read_valid": False,
        "is_html_body": False,
        "is_json_body": False,
        "format_validation_pass": False,
    }
    if not path.is_file():
        return result
    result["artifact_exists"] = True
    raw = path.read_bytes()
    result["artifact_size"] = len(raw)
    result["artifact_nonzero"] = bool(raw)
    result["sha256"] = hashlib.sha256(raw).hexdigest()
    head = raw[:512].lower()
    result["is_html_body"] = b"<html" in head or b"<!doctype html" in head or b"<head>" in head
    result["is_json_body"] = head.strip().startswith((b"{", b"["))
    result["docx_signature_valid"] = raw[:2] == b"PK"
    if result["is_html_body"] or result["is_json_body"] or not result["docx_signature_valid"]:
        return result
    if not zipfile.is_zipfile(path):
        return result
    result["docx_zip_valid"] = True
    try:
        with zipfile.ZipFile(path, "r") as package:
            names = set(package.namelist())
        result["content_types_present"] = "[Content_Types].xml" in names
        result["document_xml_present"] = "word/document.xml" in names
    except (OSError, zipfile.BadZipFile):
        return result
    if result["content_types_present"] and result["document_xml_present"]:
        try:
            from docx import Document
            Document(str(path))
            result["python_docx_read_valid"] = True
        except Exception:
            result["python_docx_read_valid"] = False
    result["format_validation_pass"] = all(
        result[key]
        for key in (
            "artifact_exists", "artifact_nonzero", "docx_signature_valid",
            "docx_zip_valid", "content_types_present", "document_xml_present",
            "python_docx_read_valid",
        )
    )
    return result


def _docx_body_validation(body: bytes) -> dict:
    """Validate an in-memory response body before it can be persisted.

    This is deliberately stricter than checking the response MIME type.  It
    prevents an HTML/JSON error body from becoming the controlled artifact and
    avoids writing any unmatched or non-DOCX response to disk.
    """
    result = {
        "artifact_nonzero": False,
        "docx_signature_valid": False,
        "docx_zip_valid": False,
        "content_types_present": False,
        "document_xml_present": False,
        "python_docx_read_valid": False,
        "is_html_body": False,
        "is_json_body": False,
        "format_validation_pass": False,
    }
    if not isinstance(body, bytes) or not body or len(body) > _MAX_DOCX_BODY_BYTES:
        return result
    result["artifact_nonzero"] = True
    head = body[:512].lower()
    result["is_html_body"] = b"<html" in head or b"<!doctype html" in head or b"<head>" in head
    result["is_json_body"] = head.strip().startswith((b"{", b"["))
    result["docx_signature_valid"] = body[:2] == b"PK"
    if result["is_html_body"] or result["is_json_body"] or not result["docx_signature_valid"]:
        return result
    try:
        with zipfile.ZipFile(io.BytesIO(body), "r") as package:
            names = set(package.namelist())
        result["docx_zip_valid"] = True
        result["content_types_present"] = "[Content_Types].xml" in names
        result["document_xml_present"] = "word/document.xml" in names
    except (OSError, zipfile.BadZipFile):
        return result
    if result["content_types_present"] and result["document_xml_present"]:
        try:
            from docx import Document
            Document(io.BytesIO(body))
            result["python_docx_read_valid"] = True
        except Exception:
            result["python_docx_read_valid"] = False
    result["format_validation_pass"] = all(
        result[key]
        for key in (
            "artifact_nonzero", "docx_signature_valid", "docx_zip_valid",
            "content_types_present", "document_xml_present",
            "python_docx_read_valid",
        )
    )
    return result


def _click_visible_text_sync(page, labels, timeout_ms=30000):
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for label in labels:
            try:
                locator = page.get_by_text(label, exact=True)
                for index in range(locator.count() - 1, -1, -1):
                    candidate = locator.nth(index)
                    if candidate.is_visible():
                        candidate.click(timeout=min(2000, timeout_ms))
                        return label
            except Exception:
                pass
        page.wait_for_timeout(300)
    raise RuntimeError("UI_LABEL_NOT_FOUND:" + "/".join(labels))


def _open_export_menu_sync(page):
    menu = page.locator("button:has(i.kd-icon-menu)").first
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if menu.is_visible():
                menu.click(timeout=15000)
                return _click_visible_text_sync(page, ("导出为", "Export As"), timeout_ms=15000)
        except Exception:
            pass
        page.wait_for_timeout(500)
    raise RuntimeError("EXPORT_MENU_BUTTON_NOT_FOUND")


def _click_docx_export_sync(page, timeout_ms=30000):
    """Expose the export submenu, then click only an explicit DOCX label."""
    try:
        return _click_visible_text_sync(page, DOCX_EXPORT_LABELS, timeout_ms=min(timeout_ms, 2500))
    except Exception:
        _open_export_menu_sync(page)
        return _click_visible_text_sync(page, DOCX_EXPORT_LABELS, timeout_ms=timeout_ms)


def _controlled_docx_response_matches(response, object_id: str) -> bool:
    """Match only a successful generated DOCX response for this object.

    The URL is inspected in memory only.  No URL, query string, or response
    headers are logged.  The version is intentionally opaque but must occupy
    exactly one non-empty path segment.
    """
    try:
        if response.status != 200:
            return False
        path = urlsplit(str(response.url or "")).path
    except Exception:
        return False
    escaped_id = re.escape(str(object_id or ""))
    return bool(escaped_id and re.fullmatch(
        rf"/tmp/exportfiles/{escaped_id}/docx/[^/]+", path
    ))


def _controlled_docx_url_matches(url: str, object_id: str) -> bool:
    """Match the generated DOCX path without exposing URL credentials."""
    try:
        path = urlsplit(str(url or "")).path
    except Exception:
        return False
    escaped_id = re.escape(str(object_id or ""))
    return bool(escaped_id and re.fullmatch(
        rf"/tmp/exportfiles/{escaped_id}/docx/[^/]+", path
    ))


def _decode_cdp_chunk(data, base64_encoded: bool) -> bytes:
    """Decode one Fetch/IO body chunk, rejecting malformed base64."""
    if not isinstance(data, str):
        raise RuntimeError("FETCH_BODY_CHUNK_INVALID")
    if not base64_encoded:
        return data.encode("utf-8")
    try:
        return base64.b64decode(data, validate=True)
    except (ValueError, TypeError):
        raise RuntimeError("FETCH_BODY_BASE64_INVALID") from None


def _bounded_append(buffer: bytearray, chunk: bytes) -> None:
    if len(buffer) + len(chunk) > _MAX_DOCX_BODY_BYTES:
        raise RuntimeError("FETCH_BODY_TOO_LARGE")
    buffer.extend(chunk)


def _read_fetch_stream_body_sync(cdp_session, stream_handle: str) -> bytes:
    """Read a Fetch response stream sequentially, bounded in memory."""
    assembled = bytearray()
    try:
        while True:
            item = cdp_session.send("IO.read", {"handle": stream_handle})
            if not isinstance(item, dict):
                raise RuntimeError("FETCH_STREAM_READ_INVALID")
            _bounded_append(
                assembled,
                _decode_cdp_chunk(item.get("data", ""), bool(item.get("base64Encoded", False))),
            )
            if item.get("eof"):
                return bytes(assembled)
    finally:
        try:
            cdp_session.send("IO.close", {"handle": stream_handle})
        except Exception:
            pass


def _resolve_paused_fetch_request(cdp_session, request_id: str) -> None:
    """Resume one paused request, preferring response-stage continuation."""
    try:
        cdp_session.send("Fetch.continueResponse", {"requestId": request_id})
    except Exception:
        cdp_session.send("Fetch.continueRequest", {"requestId": request_id})


def _fetch_enable_for_docx(cdp_session) -> None:
    cdp_session.send("Fetch.enable", {
        "patterns": [{
            "urlPattern": "*://*/tmp/exportfiles/*/docx/*",
            "requestStage": "Response",
        }],
    })


def _fetch_disable_and_detach(cdp_session) -> None:
    if cdp_session is None:
        return
    try:
        cdp_session.send("Fetch.disable")
    finally:
        try:
            cdp_session.detach()
        except Exception:
            pass


def _capture_paused_fetch_attempt_sync(page, object_id: str, use_stream: bool, click_timeout_ms: int = 30000):
    """Capture one fresh export click from a response-stage paused request.

    The CDP event callback reads only the currently bound, exact-path response
    while it is paused.  All other intercepted requests are immediately
    resumed and no body is read for them.
    """
    cdp_session = page.context.new_cdp_session(page)
    state = {"captured": None, "error": None, "seen": False}

    def on_request_paused(params):
        request_id = str(params.get("requestId", "") or "")
        request = params.get("request") or {}
        url = request.get("url", "") if isinstance(request, dict) else ""
        is_match = (
            int(params.get("responseStatusCode", 0) or 0) == 200
            and _controlled_docx_url_matches(url, object_id)
        )
        if not is_match:
            try:
                _resolve_paused_fetch_request(cdp_session, request_id)
            except Exception:
                state["error"] = RuntimeError("FETCH_UNRELATED_RESUME_FAILED")
            return
        state["seen"] = True
        try:
            if use_stream:
                stream_info = cdp_session.send("Fetch.takeResponseBodyAsStream", {"requestId": request_id})
                handle = str((stream_info or {}).get("stream", "") or "")
                if not handle:
                    raise RuntimeError("FETCH_STREAM_HANDLE_MISSING")
                body = _read_fetch_stream_body_sync(cdp_session, handle)
            else:
                body_info = cdp_session.send("Fetch.getResponseBody", {"requestId": request_id})
                body = _decode_cdp_chunk(
                    (body_info or {}).get("body", ""),
                    bool((body_info or {}).get("base64Encoded", False)),
                )
            validation = _docx_body_validation(body)
            if not validation["format_validation_pass"]:
                raise RuntimeError("DOCX_RESPONSE_BODY_INVALID")
            state["captured"] = body
        except Exception as exc:
            state["error"] = exc
        finally:
            try:
                _resolve_paused_fetch_request(cdp_session, request_id)
            except Exception as exc:
                if state["error"] is None:
                    state["error"] = exc

    try:
        _fetch_enable_for_docx(cdp_session)
        cdp_session.on("Fetch.requestPaused", on_request_paused)
        label = _click_docx_export_sync(page, timeout_ms=click_timeout_ms)
        deadline = time.time() + 90
        while time.time() < deadline and state["captured"] is None and state["error"] is None:
            page.wait_for_timeout(100)
        if state["captured"] is None:
            if state["error"] is not None:
                raise state["error"]
            raise RuntimeError("FETCH_MATCHED_RESPONSE_TIMEOUT" if state["seen"] else "FETCH_RESPONSE_TIMEOUT")
        return state["captured"], label
    finally:
        try:
            cdp_session.remove_listener("Fetch.requestPaused", on_request_paused)
        except Exception:
            pass
        _fetch_disable_and_detach(cdp_session)


def _capture_docx_response_body_fetch_sync(page, object_id: str):
    """Use getResponseBody first, then one fresh stream fallback if needed."""
    last_exc = None
    for attempt, use_stream in ((1, False), (2, True)):
        try:
            body, label = _capture_paused_fetch_attempt_sync(page, object_id, use_stream)
            return body, label, attempt, "fetch_response_stage_stream" if use_stream else "fetch_response_stage_get_response_body"
        except Exception as exc:
            last_exc = exc
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(1000)
            except Exception:
                pass
    raise last_exc if last_exc else RuntimeError("FETCH_RESPONSE_CAPTURE_FAILED")


def _persist_captured_docx_body(body: bytes, artifact_path: str | os.PathLike[str]) -> None:
    """Persist an already-validated matched DOCX response body."""
    if not _docx_body_validation(body)["format_validation_pass"]:
        raise RuntimeError("DOCX_RESPONSE_BODY_INVALID")
    Path(artifact_path).write_bytes(body)


def _wait_for_title_match(page, expected_name: str, timeout_s: int = 24) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if expected_name and expected_name in (page.title() or ""):
                return True
        except Exception:
            pass
        page.wait_for_timeout(1000)
    return False


def run_airpage_docx_acquisition(
    page,
    sample: dict,
    run_label: str,
    artifact_dir: str | os.PathLike[str] = _DEFAULT_ARTIFACT_DIR,
) -> AirPageDocxAcquisitionResult:
    """Acquire one controlled AirPage sample through the WPS UI.

    This routine does not consult or alter the formal classifier/readiness
    registry.  It is intentionally usable to validate acquisition independently
    while A5.6's machine-classifier gate remains unproven.
    """
    result = AirPageDocxAcquisitionResult(
        object_id=str(sample.get("object_id", "") or "") if isinstance(sample, dict) else "",
        started_at=_now_iso(),
    )
    if not is_controlled_airpage_sample(sample):
        result.error_class = "CONTROLLED_SAMPLE_REQUIRED"
        result.finished_at = _now_iso()
        return result
    result.controlled_sample_bound = True
    locator = str(sample.get("cloud_locator", "") or "")
    if not is_stable_document_locator(locator):
        result.error_class = "STABLE_AIRPAGE_LOCATOR_REQUIRED"
        result.finished_at = _now_iso()
        return result

    target_dir = Path(artifact_dir)
    if target_dir.resolve() != _DEFAULT_ARTIFACT_DIR.resolve():
        result.error_class = "ARTIFACT_DIR_MUST_BE_RUN_DIR_ARTIFACTS"
        result.finished_at = _now_iso()
        return result
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_id = result.object_id or _sample_name(sample)
    artifact_name = {
        "sample1": "sample1.docx",
        "sample2": "sample2.docx",
    }.get(str(run_label), f"{safe_id}.{run_label}.docx")
    artifact_path = target_dir / artifact_name
    result.artifact_path = str(artifact_path)

    try:
        page.goto(locator, wait_until="domcontentloaded", timeout=90000)
        result.stable_url_match = is_stable_document_locator(page.url)
        result.title_match = _wait_for_title_match(page, _sample_name(sample))
        if not result.stable_url_match or not result.title_match:
            result.error_class = "AIRPAGE_TARGET_NOT_VERIFIED"
            result.finished_at = _now_iso()
            return result
        body, _label, click_attempts, capture_method = _capture_docx_response_body_fetch_sync(
            page, result.object_id
        )
        result.export_operation_executed = True
        result.response_capture_method = capture_method
        result.matched_response = True
        result.response_status = 200
        result.export_click_attempts = click_attempts
        _persist_captured_docx_body(body, artifact_path)
    except Exception as exc:
        result.status = "FAILED"
        result.error_class = f"EXPORT_FAILED:{type(exc).__name__}"
        result.finished_at = _now_iso()
        return result

    validation = validate_airpage_docx_artifact(artifact_path)
    result.artifact_size = validation["artifact_size"]
    result.sha256 = validation["sha256"]
    result.docx_signature_valid = validation["docx_signature_valid"]
    result.docx_zip_valid = validation["docx_zip_valid"]
    result.content_types_present = validation["content_types_present"]
    result.document_xml_present = validation["document_xml_present"]
    result.python_docx_read_valid = validation["python_docx_read_valid"]
    if validation["format_validation_pass"]:
        result.status = "SUCCESS"
        result.error_class = "NONE"
    else:
        result.status = "FAILED"
        result.error_class = "DOCX_STRUCTURE_INVALID"
    result.finished_at = _now_iso()
    return result


def run_production_airpage_docx_acquisition(page, object_record, staging_dir):
    """Production WPS_DOCUMENT export using the proven response-stage path.

    Unlike the bounded A5.6 routine, this accepts any object that has already
    passed the B1 production contract and has a stable document locator.  The
    capture implementation remains Fetch response-stage -> getResponseBody,
    with a fresh stream fallback, 100 MiB bound, in-memory validation, and
    cleanup of Fetch/CDP resources.
    """
    from wps_mirror_contract import production_eligibility

    result = AirPageDocxAcquisitionResult(
        object_id=str(object_record.get("object_id", "") or ""),
        started_at=_now_iso(),
    )
    eligibility = production_eligibility(object_record)
    if not eligibility.eligible or eligibility.backend != BACKEND:
        result.error_class = "PRODUCTION_ELIGIBILITY_REQUIRED"
        result.finished_at = _now_iso()
        return result
    locator = str(object_record.get("cloud_locator") or object_record.get("locator") or "")
    if not is_stable_document_locator(locator):
        result.error_class = "STABLE_AIRPAGE_LOCATOR_REQUIRED"
        result.finished_at = _now_iso()
        return result
    target = Path(staging_dir)
    target.mkdir(parents=True, exist_ok=True)
    result.artifact_path = str(target / "export.docx")
    try:
        page.goto(locator, wait_until="domcontentloaded", timeout=90000)
        result.stable_url_match = is_stable_document_locator(page.url)
        result.title_match = _wait_for_title_match(page, str(object_record.get("display_name", "") or ""))
        if not result.stable_url_match or not result.title_match:
            result.error_class = "AIRPAGE_TARGET_NOT_VERIFIED"
            result.finished_at = _now_iso()
            return result
        body, _label, attempts, method = _capture_docx_response_body_fetch_sync(page, result.object_id)
        result.export_operation_executed = True
        result.response_capture_method = method
        result.export_click_attempts = attempts
        result.matched_response = True
        result.response_status = 200
        _persist_captured_docx_body(body, result.artifact_path)
        validation = validate_airpage_docx_artifact(result.artifact_path)
        result.artifact_size = validation["artifact_size"]
        result.sha256 = validation["sha256"]
        result.docx_signature_valid = validation["docx_signature_valid"]
        result.docx_zip_valid = validation["docx_zip_valid"]
        result.content_types_present = validation["content_types_present"]
        result.document_xml_present = validation["document_xml_present"]
        result.python_docx_read_valid = validation["python_docx_read_valid"]
        if not validation["format_validation_pass"]:
            raise RuntimeError("DOCX_STRUCTURE_INVALID")
        result.status = "SUCCESS"
        result.error_class = "NONE"
    except Exception as exc:
        result.status = "FAILED"
        result.error_class = "PRODUCTION_EXPORT_FAILED:" + type(exc).__name__
    result.finished_at = _now_iso()
    return result
