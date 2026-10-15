"""Standard streams for windowless child processes.

A frozen, windowless PAW started with pipes gets the system ANSI code page for
stdout/stderr (cp1252 on English Windows), so writing a Chinese file name
crashes the child with a 'charmap' UnicodeEncodeError. The parent reads child
output as UTF-8 (see backend/pm/reader_process.py), so children must write
UTF-8 and never fail on an unencodable character.
"""
from __future__ import annotations

import json
import sys


def force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError):
            pass


def emit_json(payload: dict) -> None:
    """Write one JSON object as pure ASCII, safe under any stream encoding."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=True))
    sys.stdout.flush()
