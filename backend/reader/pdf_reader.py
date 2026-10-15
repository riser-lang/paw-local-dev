from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

from .contract import ReaderInput, ReaderResult, lineage, new_result
from .utils import available_office_converter, safe_text, tool_version

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None


PDF_READER_VERSION = "pypdf+ghostscript/0.1.0"
PDF_PARSER_VERSION = "pypdf+ghostscript-renderer/1"


def _annotation_value(value: Any) -> Any:
    try:
        return value.get_object()
    except Exception:
        return value


def _page_text(page: Any, page_number: int) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    def visitor(text: str, cm: list[float], tm: list[float], font_dict: Any, font_size: float) -> None:
        text = text or ""
        if not safe_text(text):
            return
        blocks.append({"page": page_number, "text": text, "bbox": {"x": round(float(tm[4]), 3), "y": round(float(tm[5]), 3), "w": None, "h": round(float(font_size or 0), 3), "units": "pt"}, "font_size": font_size})
    try:
        page.extract_text(visitor_text=visitor)
    except Exception:
        try:
            text = page.extract_text() or ""
            if safe_text(text): blocks.append({"page": page_number, "text": text, "bbox": None, "reading_order": 0})
        except Exception:
            pass
    return sorted(blocks, key=lambda x: (-float((x.get("bbox") or {}).get("y", 0) or 0), x.get("bbox", {}).get("x", 0) or 0))


def _page_links(page: Any, page_number: int) -> list[dict[str, Any]]:
    links = []
    for annotation in page.get("/Annots", []) or []:
        obj = _annotation_value(annotation)
        try:
            action = _annotation_value(obj.get("/A"))
            uri = action.get("/URI") if action else None
            rect = obj.get("/Rect")
            if uri:
                links.append({"page": page_number, "kind": "annotation-link", "target": str(uri), "rect": list(rect) if rect else None})
            else:
                links.append({"page": page_number, "kind": "annotation", "subtype": str(obj.get("/Subtype", "")), "rect": list(rect) if rect else None})
        except Exception:
            links.append({"page": page_number, "kind": "annotation", "parse_warning": True})
    return links


def _has_image(page: Any) -> bool:
    try:
        resources = _annotation_value(page.get("/Resources", {}))
        xobjects = _annotation_value(resources.get("/XObject", {}))
        return any(_annotation_value(x).get("/Subtype") == "/Image" for x in xobjects.values())
    except Exception:
        return False


def _render_pages(path: Path, result: ReaderResult, out: Path, count: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    gs = "gs"
    prefix = out / "page-"
    cmd = [gs, "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=png16m", "-r120", f"-sOutputFile={prefix}%04d.png", f"-dFirstPage=1", f"-dLastPage={count}", str(path)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=max(90, count * 15), check=False)
        if p.returncode != 0:
            raise RuntimeError((p.stderr or p.stdout)[-600:])
    except Exception as exc:
        result.warnings.append(f"PDF renderer failed: {exc}")
    for i in range(1, count + 1):
        page = out / f"page-{i:04d}.png"
        if not page.exists():
            continue
        from PIL import Image
        with Image.open(page) as im:
            dims = {"width": im.width, "height": im.height}
        result.visual_pages.append({"kind": "pdf-page", "page": i, "path": str(page), "sha256": __import__("hashlib").sha256(page.read_bytes()).hexdigest(), "dimensions": dims, "render_mode": "ghostscript"})
        result.derived_artifacts.append(lineage(result.raw_artifact_sha256, result.source_version, page, "visual-page", render_mode="ghostscript"))


def read_pdf(inp: ReaderInput, output_dir: str | Path) -> ReaderResult:
    result = new_result(inp, "PDF")
    result.reader_version = PDF_READER_VERSION
    result.parser_version = PDF_PARSER_VERSION
    if PdfReader is None:
        result.warnings.append("pypdf is unavailable")
        result.fidelity_flags = {"text_layout_parsed": False, "visual_rendered": False, "failure_explicit": True}
        return result.finalize()
    try:
        reader = PdfReader(inp.path)
        pages = []
        for number, page in enumerate(reader.pages, 1):
            blocks = _page_text(page, number)
            links = _page_links(page, number)
            has_image = _has_image(page)
            classification = "image-only" if not blocks and has_image else ("native-text" if blocks else "empty-or-unparsed")
            envelope = {"page": number, "order": number, "media_box": [float(x) for x in page.mediabox], "classification": classification, "text_block_count": len(blocks), "link_count": len(links), "has_image_xobject": has_image}
            pages.append(envelope)
            result.text_blocks.extend(blocks)
            result.relationships.extend(links)
        result.structured_semantics = {"page_count": len(pages), "pages": pages, "page_semantic_envelope": pages}
        result.tables = [{"page": p["page"], "candidate": True, "method": "text-block-alignment", "status": "candidate-only"} for p in pages if p["text_block_count"] >= 4]
        _render_pages(Path(inp.path), result, Path(output_dir) / "pages", len(pages))
        image_only = sum(p["classification"] == "image-only" for p in pages)
        result.fidelity_flags = {"text_layout_parsed": True, "visual_rendered": len(result.visual_pages) == len(pages), "visual_evidence_available": len(result.visual_pages) == len(pages), "page_envelopes": len(pages) == len(result.structured_semantics["pages"]), "native_text_pages": sum(p["classification"] == "native-text" for p in pages), "image_only_pages": image_only, "annotations_preserved": True, "failure_explicit": True}
        if image_only:
            result.warnings.append(f"{image_only} image-only/scanned page(s) retained for visual interpretation")
    except Exception as exc:
        result.warnings.append(f"PDF parse failed closed: {exc}")
        result.fidelity_flags = {"text_layout_parsed": False, "visual_rendered": False, "failure_explicit": True}
    return result.finalize()
