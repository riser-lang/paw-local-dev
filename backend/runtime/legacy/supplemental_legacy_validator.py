#!/usr/bin/env python3
"""supplemental_legacy_validator.py — Format & integrity validator for legacy binary XLS/DOC files."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict
import olefile

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def validate_legacy_ole_file(file_path: Path, expected_family: str, expected_size: int = 0) -> dict:
    """Validate a legacy OLE Compound binary file (.xls or .doc).

    Checks:
    - File existence & non-zero size
    - Size matching expected inventory/metadata size (if provided)
    - OLE magic bytes header (D0 CF 11 E0 A1 B1 1A E1)
    - olefile structure validation and stream cataloging
    - Family-specific streams (Workbook/Book for XLS, WordDocument for DOC)
    """
    res: Dict[str, Any] = {
        "valid": False,
        "signature_valid": False,
        "format_valid": False,
        "size_valid": False,
        "size": 0,
        "sha256": "",
        "ole_streams": [],
        "family": expected_family,
        "error": "NONE",
    }

    if not file_path.exists() or not file_path.is_file():
        res["error"] = "FILE_NOT_FOUND"
        return res

    raw_size = file_path.stat().st_size
    res["size"] = raw_size
    if raw_size == 0:
        res["error"] = "EMPTY_FILE"
        return res

    res["sha256"] = sha256_file(file_path)

    if expected_size > 0 and raw_size != expected_size:
        res["error"] = f"SIZE_MISMATCH:actual={raw_size},expected={expected_size}"
        return res
    res["size_valid"] = True

    # Check magic header
    with open(file_path, "rb") as f:
        head = f.read(8)
    if not head.startswith(OLE_MAGIC):
        res["error"] = f"INVALID_OLE_MAGIC:head_hex={head.hex()}"
        return res
    res["signature_valid"] = True

    # Structural OLE validation via olefile
    try:
        if not olefile.isOleFile(str(file_path)):
            res["error"] = "OLEFILE_IS_OLE_FALSE"
            return res

        with olefile.OleFileIO(str(file_path)) as ole:
            streams = ole.listdir()
            stream_names = ["/".join(s) for s in streams]
            res["ole_streams"] = stream_names

            if expected_family in ("XLS", "SPREADSHEET"):
                # XLS typically has 'Workbook' or 'Book' stream
                has_sheet_stream = any("Workbook" in s or "Book" in s for s in stream_names)
                if not has_sheet_stream:
                    res["error"] = f"MISSING_XLS_WORKBOOK_STREAM:streams={stream_names}"
                    return res
            elif expected_family in ("DOC", "DOCUMENT"):
                # DOC typically has 'WordDocument' stream
                has_doc_stream = any("WordDocument" in s or "1Table" in s or "0Table" in s for s in stream_names)
                if not has_doc_stream:
                    res["error"] = f"MISSING_WORD_DOCUMENT_STREAM:streams={stream_names}"
                    return res

        res["format_valid"] = True
        res["valid"] = True
        return res

    except Exception as exc:
        res["error"] = f"OLE_PARSING_EXCEPTION:{type(exc).__name__}:{exc}"
        return res


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: supplemental_legacy_validator.py <file_path> <XLS|DOC>")
        sys.exit(1)
    p = Path(sys.argv[1])
    fam = sys.argv[2]
    out = validate_legacy_ole_file(p, fam)
    print(out)
