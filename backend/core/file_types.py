"""File types PAW reads, defined once for every intake path.

The folder scanner, the watcher and the cruise used to keep their own lists; the
scanner knew about Markdown and plain text while the other two did not, so a
folder of `.md` notes was analysed by a manual scan but silently ignored by the
cruise and by the watcher.  One list keeps the three paths in step.
"""
from __future__ import annotations

from pathlib import Path

SUPPORTED_EXTENSIONS = frozenset({
    ".docx", ".xlsx", ".xlsm", ".pptx", ".pdf", ".txt", ".md", ".text",
})


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def is_temporary_file(path: Path) -> bool:
    name = path.name
    return name.startswith("~$") or name.startswith(".") or name.endswith(".tmp")
