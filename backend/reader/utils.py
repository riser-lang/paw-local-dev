from __future__ import annotations

import base64
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Iterable
from xml.etree import ElementTree as ET


EMU_PER_INCH = 914400


def qn(tag: str) -> str:
    prefix, local = tag.split(":", 1)
    ns = {
        "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
        "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
        "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
        "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    return "{%s}%s" % (ns[prefix], local)


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def safe_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def tool_version(executable: str) -> str:
    path = shutil.which(executable)
    if not path:
        return ""
    try:
        p = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10, check=False)
        return (p.stdout or p.stderr).strip().splitlines()[0][:300]
    except (OSError, subprocess.SubprocessError):
        return ""


def available_office_converter() -> tuple[str, str]:
    for exe in ("libreoffice", "soffice"):
        v = tool_version(exe)
        if v:
            return exe, v
    return "", ""


def run_single(command: list[str], *, timeout: int = 90) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)


def child_text(node: ET.Element, name: str) -> str:
    found = node.find(f".//{{*}}{name}")
    return safe_text("" if found is None else "".join(found.itertext()))


def dimensions(bbox: tuple[float, float, float, float]) -> dict[str, float]:
    x, y, w, h = bbox
    return {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4), "units": "in"}

