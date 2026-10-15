"""Selective visual review policy screener.

Reads frozen queue metadata and ReaderResult *structure* (visual_pages,
tables, text_blocks counts, render_mode, file existence).  Does not
re-run full-text semantic analysis of the 710 SourceVersions.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

CORE_PROJECTS = frozenset({
    "运营导师体系",
    "运营实习生",
    "星火计划新兵营",
    "兵种培训",
    "干部管理人才盘点",
})

STATUS_SKIP_NON_CORE = "SKIP_NON_CORE_PROJECT"
STATUS_SKIP_LOW_VALUE = "SKIP_LOW_VALUE_CORE_MATERIAL"
STATUS_SKIP_INDIVIDUAL = "SKIP_INDIVIDUAL_REPORT_MATERIAL"
STATUS_SKIP_NON_TEXT = "SKIP_NON_TEXT_NON_TABLE"
STATUS_SKIP_GROUNDED = "SKIP_ALREADY_GROUNDED"
STATUS_SKIP_LOW_INFO = "SKIP_LOW_INFORMATION"
STATUS_UNREADABLE = "UNREADABLE_INSUFFICIENT_QUALITY"
STATUS_NO_IMPACT = "NO_VISUAL_IMPACT"
STATUS_REVIEW_TEXT = "REVIEW_REQUIRED_TEXT"
STATUS_REVIEW_TABLE = "REVIEW_REQUIRED_TABLE"

INDIVIDUAL_RE = re.compile(
    r"(述职|竞聘|续聘|个人汇报|个人业绩|聘任汇报|转正答辩)"
)
DECORATIVE_RE = re.compile(
    r"(横幅|展架|台卡|kt\s*板|KT板|海报|易拉宝|logo|旗帜|背景板|合影|拍照墙|胸牌)",
    re.IGNORECASE,
)
PROJECT_LEVEL_RE = re.compile(
    r"(项目方案|项目简介|项目说明|项目概览|培养方案|方案汇报|方案框架|"
    r"实施方案|机制说明|架构说明|阶段汇报|进度汇报|运营汇报|项目复盘|"
    r"培训手册|指导手册|方案亮点)"
)
LOW_VALUE_RE = re.compile(
    r"(签到|座位表|问卷星打印|空白模板|模板\.|台账打印)"
)
NAME_DECK_RE = re.compile(r"^\d+[\.、]\s*\S+\.(pptx|ppt)$", re.IGNORECASE)

MIN_IMAGE_BYTES = 8 * 1024
PROXY_RENDER_MODES = frozenset({"pillow-structural-proxy", "structural-proxy"})


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _blob(source: Mapping[str, Any]) -> str:
    return " ".join(
        _text(source.get(k))
        for k in ("title", "inventory_path", "batch")
    )


def classify_project_gate(source: Mapping[str, Any]) -> tuple[str | None, str]:
    project = _text(source.get("paw_project_name"))
    if project not in CORE_PROJECTS:
        return STATUS_SKIP_NON_CORE, f"project={project or 'NONE'} not in frozen five"
    blob = _blob(source)
    title = _text(source.get("title"))
    path = _text(source.get("inventory_path"))
    if INDIVIDUAL_RE.search(blob) or NAME_DECK_RE.match(title) or "/竞聘" in path:
        return STATUS_SKIP_INDIVIDUAL, "individual/述职/竞聘/续聘 material"
    if DECORATIVE_RE.search(blob):
        return STATUS_SKIP_LOW_INFO, "decorative/promotional title or path"
    if LOW_VALUE_RE.search(blob):
        return STATUS_SKIP_LOW_VALUE, "low-value execution material"
    if not PROJECT_LEVEL_RE.search(blob):
        return STATUS_SKIP_LOW_VALUE, "not project-overview/stage/ops/review material"
    return None, "project-level core material"


def _load_reader(path: str) -> dict[str, Any] | None:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return raw if isinstance(raw, dict) else None


def iter_visual_assets(reader: Mapping[str, Any]) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    for idx, vp in enumerate(reader.get("visual_pages") or []):
        if not isinstance(vp, dict):
            continue
        path = _text(vp.get("path"))
        assets.append({
            "source": "visual_pages",
            "index": idx,
            "locator": f"visual_pages[{idx}]",
            "kind": _text(vp.get("kind")) or "visual_page",
            "page": vp.get("page"),
            "slide": vp.get("slide") if vp.get("slide") is not None else vp.get("slide_index"),
            "sheet": vp.get("sheet"),
            "path": path,
            "sha256": _text(vp.get("sha256")),
            "render_mode": _text(vp.get("render_mode")),
            "dimensions": vp.get("dimensions"),
        })
    for idx, obj in enumerate(reader.get("embedded_objects") or []):
        if not isinstance(obj, dict):
            continue
        path = _text(obj.get("path"))
        if not path.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
            continue
        assets.append({
            "source": "embedded_objects",
            "index": idx,
            "locator": f"embedded_objects[{idx}]",
            "kind": _text(obj.get("kind")) or "embedded-image",
            "page": None,
            "slide": None,
            "sheet": None,
            "path": path,
            "sha256": _text(obj.get("sha256")),
            "render_mode": "embedded",
            "dimensions": obj.get("dimensions"),
            "size": obj.get("size"),
        })
    return assets


def _usable_image(asset: Mapping[str, Any]) -> tuple[bool, str]:
    path = _text(asset.get("path"))
    if not path:
        return False, "no image path"
    p = Path(path)
    if not p.is_file():
        return False, "image file missing"
    try:
        size = p.stat().st_size
    except OSError:
        return False, "image unreadable"
    if size < MIN_IMAGE_BYTES:
        return False, f"image too small ({size} bytes)"
    return True, "ok"


def classify_visual_assets(
    source: Mapping[str, Any],
    reader: Mapping[str, Any],
    assets: list[dict[str, Any]],
) -> dict[str, Any]:
    family = _text(source.get("family")).upper()
    text_n = len(reader.get("text_blocks") or [])
    table_n = len(reader.get("tables") or [])
    flags = reader.get("fidelity_flags") if isinstance(reader.get("fidelity_flags"), dict) else {}
    native_text_pages = flags.get("native_text_pages")
    image_only = (
        native_text_pages == 0
        or (family == "PDF" and text_n == 0)
    )

    usable: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for asset in assets:
        ok, why = _usable_image(asset)
        if not ok:
            skipped.append({**asset, "skip_reason": why})
            continue
        render = _text(asset.get("render_mode"))
        if (
            render in PROXY_RENDER_MODES
            and asset.get("source") == "visual_pages"
            and (table_n > 0 or text_n >= 8)
        ):
            skipped.append({**asset, "skip_reason": "structural proxy already in text/tables"})
            continue
        if (
            family in {"XLSX", "KSHEET"}
            and table_n > 0
            and asset.get("source") == "visual_pages"
        ):
            skipped.append({**asset, "skip_reason": "sheet raster duplicates parser cells"})
            continue
        usable.append(asset)

    embedded = [a for a in usable if a.get("source") == "embedded_objects"]
    native_pages = [
        a for a in usable
        if a.get("source") == "visual_pages"
        and _text(a.get("render_mode")) not in PROXY_RENDER_MODES
    ]

    if embedded:
        status = STATUS_REVIEW_TABLE if table_n == 0 else STATUS_REVIEW_TEXT
        return {
            "status": status,
            "reason": "embedded screenshot/table image may carry unread business text",
            "assets": embedded,
            "skipped": skipped + [a | {"skip_reason": "page raster deferred; embedded image preferred"} for a in native_pages],
            "image_only": image_only,
            "text_block_count": text_n,
            "table_count": table_n,
        }
    if image_only and (embedded or native_pages or usable):
        chosen = native_pages or usable
        return {
            "status": STATUS_REVIEW_TEXT if table_n == 0 else STATUS_REVIEW_TABLE,
            "reason": "image-only source; structured text_blocks empty or native_text_pages=0",
            "assets": chosen,
            "skipped": skipped,
            "image_only": True,
            "text_block_count": text_n,
            "table_count": table_n,
        }
    if native_pages and text_n < 8:
        return {
            "status": STATUS_REVIEW_TEXT,
            "reason": "native visual pages with little structured text",
            "assets": native_pages,
            "skipped": skipped,
            "image_only": image_only,
            "text_block_count": text_n,
            "table_count": table_n,
        }
    if not usable:
        if skipped and all("too small" in _text(s.get("skip_reason")) for s in skipped):
            return {"status": STATUS_SKIP_LOW_INFO, "reason": "only tiny/decorative rasters", "assets": [], "skipped": skipped}
        if skipped and all("missing" in _text(s.get("skip_reason")) for s in skipped):
            return {"status": STATUS_UNREADABLE, "reason": "declared visuals missing on disk", "assets": [], "skipped": skipped}
        if family in {"XLSX", "KSHEET"} and table_n > 0:
            return {"status": STATUS_SKIP_GROUNDED, "reason": "spreadsheet cells already parsed", "assets": [], "skipped": skipped}
        if text_n >= 8 and not image_only:
            return {"status": STATUS_SKIP_GROUNDED, "reason": "structured text/tables cover source; no extra visual payload", "assets": [], "skipped": skipped}
        return {"status": STATUS_SKIP_NON_TEXT, "reason": "no readable text/table image remains", "assets": [], "skipped": skipped}
    return {
        "status": STATUS_SKIP_GROUNDED,
        "reason": "native/proxy page rasters duplicate structured Reader text/tables",
        "assets": [],
        "skipped": skipped + [{**a, "skip_reason": "already grounded by text/tables"} for a in usable],
        "image_only": image_only,
        "text_block_count": text_n,
        "table_count": table_n,
    }


def screen_source(source: Mapping[str, Any]) -> dict[str, Any]:
    svid = _text(source.get("source_version_id"))
    gate_status, gate_reason = classify_project_gate(source)
    base = {
        "source_version_id": svid,
        "title": _text(source.get("title")),
        "batch": _text(source.get("batch")),
        "family": _text(source.get("family")),
        "paw_project_name": _text(source.get("paw_project_name")),
        "reader_result_path": _text(source.get("reader_result_path")),
        "raw_sha256": _text(source.get("raw_sha256")),
        "source_id": _text(source.get("source_id")),
        "stable_object_id": _text(source.get("stable_object_id") or source.get("source_id")),
        "version_marker": _text(source.get("version_marker")),
        "inventory_path": _text(source.get("inventory_path")),
        "project_tier": _text(source.get("project_tier")),
        "paw_project_id": _text(source.get("paw_project_id")),
    }
    if gate_status:
        return {**base, "status": gate_status, "reason": gate_reason, "assets": [], "skipped": []}
    reader_path = _text(source.get("reader_result_path"))
    reader = _load_reader(reader_path) if reader_path else None
    if reader is None:
        return {**base, "status": STATUS_UNREADABLE, "reason": "reader_result missing", "assets": [], "skipped": []}
    assets = iter_visual_assets(reader)
    visual = classify_visual_assets(source, reader, assets)
    return {**base, **visual}


def screen_queue(queue_path: Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(queue_path).read_text(encoding="utf-8"))
    sources = payload.get("sources") or []
    return [screen_source(s) for s in sources if isinstance(s, Mapping)]
