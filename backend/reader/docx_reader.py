from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any
from xml.etree import ElementTree as ET
import zipfile

from .contract import ReaderInput, ReaderResult, lineage, new_result
from .utils import available_office_converter, local, qn, safe_text, sha256_bytes

try:
    from docx import Document
except ImportError:  # pragma: no cover
    Document = None


DOCX_READER_VERSION = "python-docx+ooxml/0.1.0"
DOCX_PARSER_VERSION = "python-docx+zip-ooxml+Pillow/1"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _run_style(run: Any) -> dict[str, Any]:
    font = run.font
    out = {"style": getattr(run.style, "name", "") if run.style else "", "text": run.text or ""}
    for attr in ("name", "size", "bold", "italic", "underline"):
        try:
            value = getattr(font, attr)
            out[attr] = float(value.pt) if attr == "size" and value else value
        except Exception:
            pass
    try:
        out["color"] = str(font.color.rgb) if font.color.rgb else ""
    except Exception:
        pass
    return out


def _paragraph_record(p: Any, index: int, where: str = "body") -> dict[str, Any]:
    style = getattr(p.style, "name", "") if p.style else ""
    elem = p._p
    numpr = elem.find(f".//{{{W}}}numPr")
    ilvl = numid = None
    if numpr is not None:
        n = numpr.find(f"{{{W}}}ilvl"); i = numpr.find(f"{{{W}}}numId")
        ilvl = n.get(f"{{{W}}}val") if n is not None else None; numid = i.get(f"{{{W}}}val") if i is not None else None
    breaks = [x.get(f"{{{W}}}type", "line") for x in elem.findall(f".//{{{W}}}br")]
    return {"index": index, "where": where, "text": p.text or "", "style": style, "heading_level": int(style.split()[-1]) if style.lower().startswith("heading ") and style.split()[-1].isdigit() else None, "list": {"num_id": numid, "level": ilvl} if numid or ilvl else None, "runs": [_run_style(r) for r in p.runs], "breaks": breaks}


def _table_record(table: Any, index: int, where: str = "body") -> dict[str, Any]:
    rows = []
    for r, row in enumerate(table.rows):
        cells = []
        for c, cell in enumerate(row.cells):
            tc = cell._tc
            cells.append({"row": r, "column": c, "text": cell.text, "grid_span": (tc.find(f".//{{{W}}}gridSpan").get(f"{{{W}}}val") if tc.find(f".//{{{W}}}gridSpan") is not None else "1"), "v_merge": (tc.find(f".//{{{W}}}vMerge").get(f"{{{W}}}val", "continue") if tc.find(f".//{{{W}}}vMerge") is not None else None), "xml_sha256": sha256_bytes(ET.tostring(tc, encoding="utf-8"))})
        rows.append(cells)
    return {"index": index, "where": where, "rows": rows}


def _render_native(path: Path, result: ReaderResult, out: Path) -> bool:
    exe, version = available_office_converter()
    if not exe:
        return False
    out.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "--headless", "--convert-to", "pdf", "--outdir", str(out), str(path)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        pdf = out / (path.stem + ".pdf")
        if p.returncode != 0 or not pdf.exists():
            return False
        result.derived_artifacts.append(lineage(result.raw_artifact_sha256, result.source_version, pdf, "docx-to-pdf", conversion_tool=exe, conversion_tool_version=version))
        gs = ["gs", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=png16m", "-r120", f"-sOutputFile={out / 'page-%04d.png'}", str(pdf)]
        rp = subprocess.run(gs, capture_output=True, text=True, timeout=180, check=False)
        if rp.returncode != 0:
            return False
        for image in sorted(out.glob("page-*.png")):
            from PIL import Image
            with Image.open(image) as im: dims = {"width": im.width, "height": im.height}
            result.visual_pages.append({"kind": "docx-page", "path": str(image), "sha256": sha256_bytes(image.read_bytes()), "dimensions": dims, "render_mode": "native-libreoffice"})
            result.derived_artifacts.append(lineage(result.raw_artifact_sha256, result.source_version, image, "visual-page", render_mode="native-libreoffice"))
        return bool(result.visual_pages)
    except (OSError, subprocess.SubprocessError):
        return False


def read_docx(inp: ReaderInput, output_dir: str | Path) -> ReaderResult:
    result = new_result(inp, "DOCX")
    result.reader_version = DOCX_READER_VERSION
    result.parser_version = DOCX_PARSER_VERSION
    if Document is None:
        result.warnings.append("python-docx is unavailable")
        result.fidelity_flags = {"structure_parsed": False, "visual_rendered": False, "failure_explicit": True}
        return result.finalize()
    try:
        doc = Document(inp.path)
        result.text_blocks = [_paragraph_record(p, i) for i, p in enumerate(doc.paragraphs)]
        result.tables = [_table_record(t, i) for i, t in enumerate(doc.tables)]
        result.structured_semantics = {"paragraph_count": len(result.text_blocks), "table_count": len(result.tables), "sections": [], "headers_footers": [], "footnotes": [], "endnotes": [], "comments": []}
        for i, section in enumerate(doc.sections):
            result.structured_semantics["sections"].append({"index": i, "start_type": str(section.start_type), "page_width": section.page_width, "page_height": section.page_height, "header_distance": section.header_distance, "footer_distance": section.footer_distance})
            for label, container in (("header", section.header), ("footer", section.footer)):
                result.structured_semantics["headers_footers"].append({"section": i, "kind": label, "paragraphs": [_paragraph_record(p, j, label) for j, p in enumerate(container.paragraphs)], "tables": [_table_record(t, j, label) for j, t in enumerate(container.tables)]})
        with zipfile.ZipFile(inp.path) as z:
            names = set(z.namelist())
            result.structured_semantics["package_inventory"] = {
                "part_count": len(names),
                "parts": [{"path": name, "sha256": sha256_bytes(z.read(name)), "size": z.getinfo(name).file_size} for name in sorted(names)],
                "evidence_kind": "OOXML-package-hash-inventory",
            }
            for name in sorted(names):
                if name.startswith("word/media/"):
                    blob = z.read(name); target = Path(output_dir) / "embedded" / Path(name).name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(blob)
                    item = {"kind": "image", "package_path": name, "path": str(target), "sha256": sha256_bytes(blob), "size": len(blob), "relationship_evidence": True}
                    result.embedded_objects.append(item); result.derived_artifacts.append(lineage(result.raw_artifact_sha256, result.source_version, target, "embedded-image"))
            for name, key in (("word/footnotes.xml", "footnotes"), ("word/endnotes.xml", "endnotes"), ("word/comments.xml", "comments")):
                if name in names:
                    blob = z.read(name); result.structured_semantics[key] = {"path": name, "sha256": sha256_bytes(blob), "xml": blob.decode("utf-8", "replace")[:200000]}
            rel_name = "word/_rels/document.xml.rels"
            if rel_name in names:
                root = ET.fromstring(z.read(rel_name))
                for rel in root:
                    result.relationships.append({"source": "word/document.xml", "relationship_id": rel.get("Id", ""), "type": rel.get("Type", ""), "target": rel.get("Target", "")})
        for p in doc.paragraphs:
            for run in p.runs:
                for rel in result.relationships:
                    if rel.get("relationship_id") and rel.get("target") and (rel["relationship_id"] in ET.tostring(p._p, encoding="unicode")):
                        rel["location"] = "paragraph"
        result.structured_semantics["drawing_inventory"] = []
        root = ET.fromstring(doc.part.blob if hasattr(doc.part, "blob") else zipfile.ZipFile(inp.path).read("word/document.xml"))
        for node in root.findall(".//{%s}anchor" % "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing") + root.findall(".//{%s}inline" % "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"):
            extent = node.find("{%s}extent" % "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing")
            docpr = node.find("{%s}docPr" % "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing")
            blip = node.find(".//{http://schemas.openxmlformats.org/drawingml/2006/main}blip")
            result.structured_semantics["drawing_inventory"].append({"kind": local(node.tag), "name": docpr.get("name", "") if docpr is not None else "", "description": docpr.get("descr", "") if docpr is not None else "", "extent": dict(extent.attrib) if extent is not None else {}, "relationship_id": blip.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed", "") if blip is not None else ""})
        rendered = _render_native(Path(inp.path), result, Path(output_dir) / "pages")
        result.fidelity_flags = {"structure_parsed": True, "tables_parsed": True, "images_accounted": all(x.get("sha256") for x in result.embedded_objects), "drawings_accounted": True, "headers_footers_preserved": True, "notes_parts_preserved": True, "visual_rendered": rendered, "visual_evidence_available": rendered, "native_visual_renderer": rendered, "failure_explicit": True}
        if not rendered:
            result.warnings.append("native DOCX visual pagination unavailable; structure and embedded-image evidence retained")
    except Exception as exc:
        result.warnings.append(f"DOCX parse failed closed: {exc}")
        result.fidelity_flags = {"structure_parsed": False, "visual_rendered": False, "failure_explicit": True}
    return result.finalize()
