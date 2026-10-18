"""Read the text body for locally scanned source registry entries."""
from __future__ import annotations

import json
from pathlib import Path

from backend.core.config import EVIDENCE_DIR


def local_scan_source_content(source: dict) -> str | None:
    """Read a scanned source body without disclosing its filesystem path."""
    path = Path(str(source.get("source_path") or ""))
    if not path.is_file():
        return None
    if path.suffix.lower() in {".md", ".txt", ".text"}:
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:20000]
        except OSError:
            return None
    digest = str(source.get("content_hash") or "")
    if len(digest) < 8:
        return None
    # The scanner names each immutable extraction directory from the source
    # filename and content hash. Verify the raw hash before using the text.
    try:
        candidates = EVIDENCE_DIR.glob(f"{path.stem}_{digest[:8]}/reader_result.json")
        for result_path in candidates:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if result.get("raw_artifact_sha256") != digest:
                continue
            chunks: list[str] = []

            def collect(value):
                if isinstance(value, dict):
                    for key, child in value.items():
                        if key in {"text", "content", "paragraph_text"} and isinstance(child, str):
                            if child.strip():
                                chunks.append(child.strip())
                        else:
                            collect(child)
                elif isinstance(value, list):
                    for child in value:
                        collect(child)

            collect(result.get("text_blocks", []))
            collect(result.get("structured_semantics", {}))
            body = "\n".join(dict.fromkeys(chunks))
            return body[:20000] or None
    except (OSError, ValueError, TypeError):
        return None
    return None
