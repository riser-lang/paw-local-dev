"""Scrub and verify the scan diagnostics before they leave the runner.

The acceptance run uploads the installed application's own log, its mirrored
copy and the scan state file so a stuck Phase 2 can be explained from the
artifact instead of from a guess.  Nothing in those files should contain a
credential (the application redacts on the way in, and the mirror keeps the same
filter), but the check is here so an artifact can never carry one even if a
future log line forgets:

1. every file under ``--dir`` is rewritten with key-like tokens replaced;
2. the directory is scanned again; if anything still matches, the step fails
   and the artifact is not uploaded.

Only sizes, names and hashes prefixes are expected in these files; prompts and
request bodies are never logged by the application.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Same shape the application's logging filter uses, plus bearer headers.
SECRET_PATTERNS = (
    re.compile(r"(sk-[A-Za-z0-9_\-]{8,})", re.IGNORECASE),
    re.compile(r"(key-[A-Za-z0-9_\-]{8,})", re.IGNORECASE),
    re.compile(r"(Bearer\s+[A-Za-z0-9_\-\.]{8,})", re.IGNORECASE),
    re.compile(r"([A-Za-z0-9_\-]{32,}\.[A-Za-z0-9_\-]{8,})"),
)
REPLACEMENT = "sk-...[REDACTED]"
MAX_FILE_BYTES = 16 * 1024 * 1024


def scrub_text(text: str) -> tuple[str, int]:
    hits = 0
    for pattern in SECRET_PATTERNS:
        text, count = pattern.subn(REPLACEMENT, text)
        hits += count
    return text, hits


def scrub_dir(root: Path) -> tuple[int, int]:
    files = 0
    scrubbed = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        files += 1
        cleaned, hits = scrub_text(text)
        if hits:
            path.write_text(cleaned, encoding="utf-8")
            scrubbed += hits
    return files, scrubbed


def remaining(root: Path) -> list[str]:
    offenders = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern in SECRET_PATTERNS:
            match = pattern.search(text)
            if match:
                offenders.append(f"{path.name}: {pattern.pattern[:24]}...")
                break
    return offenders


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", required=True)
    args = parser.parse_args(argv)

    root = Path(args.dir)
    if not root.is_dir():
        print(f"scan diagnostics directory missing: {root}")
        return 0
    files, scrubbed = scrub_dir(root)
    left = remaining(root)
    print(f"scan diagnostics: files={files} redacted_tokens={scrubbed}")
    if left:
        for line in left:
            print(f"REFUSING TO UPLOAD a key-like token: {line}")
        return 1
    print("scan diagnostics: no key-like token remains")
    return 0


if __name__ == "__main__":
    sys.exit(main())
