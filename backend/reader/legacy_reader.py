from __future__ import annotations

from pathlib import Path
import json
import struct
import subprocess
from typing import Any

from .contract import ReaderInput, ReaderResult, artifact_sha256, lineage, new_result
from .utils import available_office_converter


LEGACY_READER_VERSION = "legacy-raw+validated-conversion/0.1.0"
LEGACY_PARSER_VERSION = "raw-canonical+optional-conversion/1"


def validate_ole_biff_route(path: str | Path, output_dir: str | Path, source_version: str, raw_hash: str) -> dict[str, Any] | None:
    """Create a read-only validated route for WPS OLE/BIFF files.

    Some committed WPS ``.xls`` files are valid OLE containers but cannot be
    opened by the installed LibreOffice build.  We do not claim a converted
    workbook in that case.  Instead, validate the complete BIFF record
    framing and retain a deterministic inventory of the Workbook stream as a
    derived evidence artifact.  The raw file remains untouched.
    """
    try:
        import olefile
        if not olefile.isOleFile(str(path)):
            return None
        with olefile.OleFileIO(str(path)) as ole:
            if not ole.exists("Workbook"):
                return None
            stream = ole.openstream("Workbook").read()
        if len(stream) < 8:
            return None
        counts: dict[str, int] = {}
        positions: list[dict[str, int]] = []
        offset = 0
        while offset + 4 <= len(stream):
            record_offset = offset
            record_id, size = struct.unpack_from("<HH", stream, offset)
            offset += 4
            if offset + size > len(stream):
                return None
            key = f"0x{record_id:04x}"
            counts[key] = counts.get(key, 0) + 1
            if len(positions) < 5000:
                positions.append({"offset": record_offset, "record_id": record_id, "size": size})
            offset += size
        if offset != len(stream) or not counts:
            return None
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        inventory_path = out / "legacy_biff_inventory.json"
        inventory = {
            "format": "OLE2-BIFF-record-framing",
            "source_version": source_version,
            "parent_raw_artifact_sha256": raw_hash,
            "workbook_stream_bytes": len(stream),
            "workbook_stream_sha256": artifact_sha256_bytes(stream),
            "record_count": sum(counts.values()),
            "record_type_counts": dict(sorted(counts.items())),
            "record_inventory_truncated": sum(counts.values()) > len(positions),
            "records": positions,
            "validated_complete_record_framing": True,
        }
        inventory_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {
            "status": "PASS",
            "route": "validated-ole-biff-inventory",
            "path": str(inventory_path),
            "sha256": artifact_sha256(inventory_path),
            "parent_raw_artifact_sha256": raw_hash,
            "source_version": source_version,
            "record_count": inventory["record_count"],
            "workbook_stream_bytes": len(stream),
        }
    except (ImportError, OSError, ValueError, struct.error):
        return None


def artifact_sha256_bytes(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def convert_legacy(path: str | Path, output_dir: str | Path, source_version: str, raw_hash: str) -> dict[str, Any]:
    """Convert only into a self-owned derived directory; never overwrite raw."""
    exe, version = available_office_converter()
    if not exe:
        return {"status": "BLOCKED", "reason": "NO_LIBREOFFICE_CONVERTER", "parent_raw_artifact_sha256": raw_hash, "source_version": source_version}
    path, out = Path(path), Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    target_ext = ".docx" if path.suffix.lower() == ".doc" else ".xlsx"
    cmd = [exe, "--headless", "--convert-to", target_ext[1:], "--outdir", str(out), str(path)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        target = out / (path.stem + target_ext)
        if p.returncode != 0 or not target.exists() or target.stat().st_size == 0:
            return {"status": "BLOCKED", "reason": "CONVERSION_FAILED", "stderr": (p.stderr or p.stdout)[-500:], "parent_raw_artifact_sha256": raw_hash, "source_version": source_version, "conversion_tool": exe, "conversion_tool_version": version}
        return {"status": "PASS", "path": str(target), "sha256": artifact_sha256(target), "parent_raw_artifact_sha256": raw_hash, "source_version": source_version, "conversion_tool": exe, "conversion_tool_version": version}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "BLOCKED", "reason": "CONVERSION_EXCEPTION", "error": str(exc), "parent_raw_artifact_sha256": raw_hash, "source_version": source_version}


def read_legacy(inp: ReaderInput, output_dir: str | Path, *, attempt_conversion: bool = True) -> ReaderResult:
    result = new_result(inp, "LEGACY")
    result.reader_version = LEGACY_READER_VERSION
    result.parser_version = LEGACY_PARSER_VERSION
    result.structured_semantics = {"raw_canonical": True, "raw_path": inp.path, "extension": Path(inp.path).suffix.lower(), "conversion_attempted": attempt_conversion}
    result.embedded_objects.append({"kind": "raw-canonical", "path": inp.path, "sha256": result.raw_artifact_sha256, "source_version": result.source_version})
    if attempt_conversion:
        conversion = convert_legacy(inp.path, Path(output_dir) / "converted", result.source_version, result.raw_artifact_sha256)
        result.structured_semantics["derived_route"] = conversion
        result.derived_artifacts.append(conversion)
        if conversion.get("status") != "PASS":
            fallback = validate_ole_biff_route(inp.path, Path(output_dir) / "validated", result.source_version, result.raw_artifact_sha256)
            if fallback and fallback.get("status") == "PASS":
                result.structured_semantics["derived_route"] = fallback
                result.derived_artifacts.append(fallback)
                result.fidelity_flags = {"raw_canonical": True, "validated_derived_parse_route": True, "failure_explicit": True}
                result.warnings.append(f"legacy office conversion blocked; validated OLE/BIFF inventory retained: {conversion.get('reason', 'unknown')}")
            else:
                result.warnings.append(f"legacy conversion blocked: {conversion.get('reason', 'unknown')}; raw artifact unchanged")
        else:
            result.fidelity_flags = {"raw_canonical": True, "validated_derived_parse_route": True, "failure_explicit": True}
    if not result.fidelity_flags:
        result.fidelity_flags = {"raw_canonical": True, "validated_derived_parse_route": False, "failure_explicit": True}
    return result.finalize()
