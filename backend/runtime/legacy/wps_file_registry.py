#!/usr/bin/env python3
"""R15-A.4/R15-B1.5 file semantics and source-acquisition routing.

Semantic classification is intentionally kept separate from acquisition
routing.  A suffix can be a safe routing hint without being a provider-native
type assertion (most importantly for ``.otl``).
"""
from __future__ import annotations

FILE_FAMILY_REGISTRY = {
    "SPREADSHEET": "Spreadsheet files",
    "DOCUMENT": "Document files",
    "PRESENTATION": "Presentation files",
    "PDF": "PDF documents",
    "MEDIA_IMAGE": "Standalone image files",
    "MEDIA_VIDEO": "Standalone video files",
    "MEDIA_AUDIO": "Standalone audio files",
    "OTHER": "Other file types",
    "UNKNOWN": "Unclassified files",
}

FILE_TYPE_REGISTRY = {
    "WPS_SPREADSHEET": {"ext": [".ksheet"], "family": "SPREADSHEET"},
    "WPS_DOCUMENT": {"ext": [], "family": "DOCUMENT"},
    "WPS_PRESENTATION": {"ext": [], "family": "PRESENTATION"},
    "NATIVE_XLSX": {"ext": [".xlsx"], "family": "SPREADSHEET"},
    "NATIVE_DOCX": {"ext": [".docx"], "family": "DOCUMENT"},
    "NATIVE_PPT": {"ext": [".ppt"], "family": "PRESENTATION"},
    "NATIVE_PPTX": {"ext": [".pptx"], "family": "PRESENTATION"},
    "NATIVE_XLS": {"ext": [".xls"], "family": "SPREADSHEET"},
    "NATIVE_DOC": {"ext": [".doc"], "family": "DOCUMENT"},
    "PDF": {"ext": [".pdf"], "family": "PDF"},
    "IMAGE": {"ext": [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif", ".svg", ".heic"], "family": "MEDIA_IMAGE"},
    "VIDEO": {"ext": [".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv"], "family": "MEDIA_VIDEO"},
    "AUDIO": {"ext": [".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"], "family": "MEDIA_AUDIO"},
    "OTHER": {"ext": [], "family": "OTHER"},
    "UNKNOWN": {"ext": [], "family": "UNKNOWN"},
}

_EXT_TO_TYPE = {}
for _ftype, _info in FILE_TYPE_REGISTRY.items():
    for _ext in _info["ext"]:
        _EXT_TO_TYPE[_ext.lower()] = _ftype

MIRROR_BACKEND_REGISTRY = {
    "WPS_SPREADSHEET": "WPS_SPREADSHEET_EXPORT_XLSX",
    "WPS_DOCUMENT": "WPS_DOCUMENT_EXPORT_DOCX",
    "WPS_PRESENTATION": "WPS_PRESENTATION_EXPORT_PPTX",
    "NATIVE_XLSX": "NATIVE_XLSX_DIRECT",
    "NATIVE_DOCX": "NATIVE_DOCX_DIRECT",
    "NATIVE_PPT": "NATIVE_PPT_DIRECT_OR_CONVERT",
    "NATIVE_PPTX": "NATIVE_PPTX_DIRECT",
    "NATIVE_XLS": "NATIVE_XLS_DIRECT_OR_CONVERT",
    "NATIVE_DOC": "NATIVE_DOC_DIRECT_OR_CONVERT",
    "PDF": "PDF_DIRECT",
    "IMAGE": "NO_MIRROR",
    "VIDEO": "NO_MIRROR",
    "AUDIO": "NO_MIRROR",
    "OTHER": "UNSUPPORTED",
    "UNKNOWN": "NEEDS_CLASSIFICATION",
}

MIRROR_BACKEND_REGISTRY["RAW_BINARY"] = "RAW_BINARY_DIRECT"

MEDIA_EXCLUDED_FAMILIES = {"MEDIA_IMAGE", "MEDIA_VIDEO", "MEDIA_AUDIO"}

USER_DECLARED_EXCLUDED_UI_CATEGORIES = frozenset({
    "Forms",
    "Image",
    "DBSheet",
    "Scanned Copy",
    "OFD",
    "Others",
})

USER_DECLARED_INCLUDED_UI_CATEGORIES = frozenset({
    "AirSheet",
    "Docs",
    "Slides",
    "Sheets",
    "PDF",
})

def is_user_declared_no_mirror_ui_category(category_name: str) -> bool:
    return str(category_name or "").strip() in USER_DECLARED_EXCLUDED_UI_CATEGORIES

MIRROR_BACKEND_STATUS_REGISTRY = {
    "WPS_SPREADSHEET_EXPORT_XLSX": {"registered": True, "capability_status": "CAPABILITY_PROVEN", "acquisition_ready": True},
    "WPS_DOCUMENT_EXPORT_DOCX": {"registered": True, "capability_status": "CAPABILITY_PROVEN", "acquisition_ready": True},
    "WPS_PRESENTATION_EXPORT_PPTX": {"registered": True, "capability_status": "UNVALIDATED", "acquisition_ready": False},
    "NATIVE_XLSX_DIRECT": {"registered": True, "capability_status": "READY", "acquisition_ready": True},
    "NATIVE_DOCX_DIRECT": {"registered": True, "capability_status": "READY", "acquisition_ready": True},
    "NATIVE_PPTX_DIRECT": {"registered": True, "capability_status": "READY", "acquisition_ready": True},
    "NATIVE_PPT_DIRECT_OR_CONVERT": {"registered": True, "capability_status": "UNVALIDATED", "acquisition_ready": False},
    "NATIVE_XLS_DIRECT_OR_CONVERT": {"registered": True, "capability_status": "UNVALIDATED", "acquisition_ready": False},
    "NATIVE_DOC_DIRECT_OR_CONVERT": {"registered": True, "capability_status": "UNVALIDATED", "acquisition_ready": False},
    "PDF_DIRECT": {"registered": True, "capability_status": "READY", "acquisition_ready": True},
    "NO_MIRROR": {"registered": True, "capability_status": "BLOCKED", "acquisition_ready": False},
    "UNSUPPORTED": {"registered": True, "capability_status": "BLOCKED", "acquisition_ready": False},
    "NEEDS_CLASSIFICATION": {"registered": True, "capability_status": "BLOCKED", "acquisition_ready": False},
    "RAW_BINARY_DIRECT": {"registered": True, "capability_status": "PARTIAL", "acquisition_ready": False},
}


def get_mirror_backend_status(backend_name):
    return MIRROR_BACKEND_STATUS_REGISTRY.get(backend_name)


def is_registered_mirror_backend(backend_name):
    return backend_name in MIRROR_BACKEND_STATUS_REGISTRY


def is_backend_acquisition_ready(backend_name):
    status = MIRROR_BACKEND_STATUS_REGISTRY.get(backend_name)
    if not status:
        return False
    return status.get("acquisition_ready", False)


def validate_backend_readiness_exact(expected_ready_set):
    for backend_name, entry in MIRROR_BACKEND_STATUS_REGISTRY.items():
        actual_ready = entry.get("acquisition_ready", False) is True
        expected_ready = backend_name in expected_ready_set
        if actual_ready != expected_ready:
            return False
    return True


def get_acquisition_ready_backends():
    return {
        backend_name for backend_name, entry in MIRROR_BACKEND_STATUS_REGISTRY.items()
        if entry.get("acquisition_ready", False) is True
    }


WPS_UI_SEARCH_PROVIDER = "wps_ui_search_v6"


def classify_file(filename=None, extension=None, provider_metadata=None):
    if isinstance(provider_metadata, dict):
        if (
            provider_metadata.get("provider_path") == WPS_UI_SEARCH_PROVIDER
            and provider_metadata.get("provider_namespace") == "yundoc"
            and provider_metadata.get("provider_object_type") == "file"
            and str(provider_metadata.get("provider_filename", "")).lower().endswith(".otl")
        ):
            return "WPS_DOCUMENT", "DOCUMENT"

    ext = None
    if extension:
        ext = extension.lower()
        if not ext.startswith("."):
            ext = "." + ext
        if ext in _EXT_TO_TYPE:
            file_type = _EXT_TO_TYPE[ext]
            file_family = FILE_TYPE_REGISTRY[file_type]["family"]
            return file_type, file_family
    if filename:
        lower_name = filename.lower()
        for check_ext in sorted(_EXT_TO_TYPE.keys(), key=len, reverse=True):
            if lower_name.endswith(check_ext):
                ext = check_ext
                break
    if ext and ext in _EXT_TO_TYPE:
        file_type = _EXT_TO_TYPE[ext]
        file_family = FILE_TYPE_REGISTRY[file_type]["family"]
        return file_type, file_family
    return "UNKNOWN", "UNKNOWN"


def semantic_classification(record):
    if not isinstance(record, dict):
        return "UNKNOWN", "UNKNOWN"
    provider = record.get("provider_metadata")
    if not isinstance(provider, dict):
        provider = {
            "provider_path": record.get("provider_path"),
            "provider_namespace": record.get("provider_namespace", record.get("group_source")),
            "provider_object_type": record.get("provider_object_type", record.get("ftype")),
            "provider_filename": record.get("provider_filename", record.get("filename", record.get("display_name"))),
        }
    return classify_file(record.get("filename", record.get("display_name")), record.get("extension"), provider)


_DIRECT_BINARY_SUFFIXES = frozenset({".xlsx", ".docx", ".pptx", ".pdf"})


def _record_filename(record):
    return str(record.get("filename") or record.get("fname") or record.get("display_name") or record.get("name") or "")


def derive_acquisition_route(record):
    if not isinstance(record, dict):
        return {"route": "BLOCKED", "backend": "BLOCKED_BACKEND", "evidence": "INVALID_OBJECT_RECORD", "format": "", "mime_type": ""}
    names = (str(record.get("filename") or ""), str(record.get("display_name") or ""))
    if any("FY26运营导师带教情况表" in name for name in names):
        return {"route": "INVENTORY_ONLY", "backend": "NO_MIRROR", "evidence": "WPS_MIRROR_EXCLUSION_RULE_001", "format": "", "mime_type": ""}
    if record.get("object_type") != "file":
        return {"route": "DIRECTORY_METADATA", "backend": "DIRECTORY_METADATA", "evidence": "OBJECT_TYPE_FOLDER", "format": "", "mime_type": ""}
    filename = _record_filename(record)
    
    # Check macOS shadow metadata files (e.g. ._filename or __MACOSX)
    if filename.startswith("._") or "/._" in filename or "__MACOSX" in filename:
        return {"route": "INVENTORY_ONLY", "backend": "NO_MIRROR", "evidence": "MACOS_METADATA_SHADOW_FILE", "format": "bin", "mime_type": "application/octet-stream"}

    suffix = "." + filename.rsplit(".", 1)[-1].casefold() if "." in filename.rsplit("/", 1)[-1] else ""
    if not suffix and record.get("extension"):
        suffix = str(record.get("extension")).casefold()
        if not suffix.startswith("."):
            suffix = "." + suffix
    if suffix == ".otl":
        return {"route": "AIRPAGE_EXPORT_DOCX", "backend": "WPS_DOCUMENT_EXPORT_DOCX", "evidence": "OTL_SUFFIX_EDITOR_ROUTE_HINT", "format": "docx", "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    if suffix == ".ksheet":
        return {"route": "KSHEET_EXPORT_XLSX", "backend": "WPS_SPREADSHEET_EXPORT_XLSX", "evidence": "KSHEET_SUFFIX_EXPORT_ROUTE", "format": "xlsx", "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
    if suffix in _DIRECT_BINARY_SUFFIXES:
        mime = {
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ".pdf": "application/pdf",
        }[suffix]
        return {"route": "NATIVE_DIRECT_BINARY", "backend": {".xlsx": "NATIVE_XLSX_DIRECT", ".docx": "NATIVE_DOCX_DIRECT", ".pptx": "NATIVE_PPTX_DIRECT", ".pdf": "PDF_DIRECT"}[suffix], "evidence": "NATIVE_SUFFIX_DIRECT_BINARY", "format": suffix[1:], "mime_type": mime}
    evidence = "RAW_BINARY_EXTENSION_FALLBACK" if suffix else "RAW_BINARY_NO_EXTENSION_FALLBACK"
    return {"route": "RAW_BINARY_FALLBACK", "backend": "RAW_BINARY_DIRECT", "evidence": evidence, "format": suffix[1:] if suffix else "bin", "mime_type": "application/octet-stream"}


def get_mirror_backend(file_type, mirror_policy="INCLUDED"):
    if mirror_policy == "EXCLUDED":
        return "NO_MIRROR"
    file_family = FILE_TYPE_REGISTRY.get(file_type, {}).get("family", "UNKNOWN")
    if file_family in MEDIA_EXCLUDED_FAMILIES:
        return "NO_MIRROR"
    return MIRROR_BACKEND_REGISTRY.get(file_type, "NEEDS_CLASSIFICATION")


def get_mirror_exclusion_reason(mirror_policy, file_family):
    if mirror_policy == "EXCLUDED":
        return "USER_DECLARED_NO_BUSINESS_VALUE"
    if file_family in MEDIA_EXCLUDED_FAMILIES:
        return "MEDIA_TYPE_EXCLUDED"
    return None
