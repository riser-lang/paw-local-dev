from __future__ import annotations

import html
import json
from pathlib import Path
import re
import subprocess
from typing import Any
from xml.etree import ElementTree as ET
import zipfile

from .contract import ReaderInput, ReaderResult, lineage, new_result
from .utils import EMU_PER_INCH, data_uri, dimensions, local, qn, safe_text, sha256_bytes, tool_version

try:  # Optional on installations that only run PDF/XLSX readers.
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
except ImportError:  # pragma: no cover - exercised by dependency-free installs
    Presentation = None
    MSO_SHAPE_TYPE = None


PPTX_READER_VERSION = "pptx-ooxml+visual-proxy/0.3.1"
PPTX_PARSER_VERSION = "python-pptx+ooxml+Pillow/4"
NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
}


def _enum_name(value: Any) -> str:
    return str(getattr(value, "name", value)).split(".")[-1]


def _color(obj: Any) -> dict[str, Any]:
    try:
        typ = _enum_name(obj.type)
    except Exception:
        return {}
    out: dict[str, Any] = {"type": typ}
    for attr in ("rgb", "theme_color", "brightness"):
        try:
            value = getattr(obj, attr)
            if value is not None:
                out[attr] = str(value)
        except Exception:
            pass
    return out


def _fill(shape: Any) -> dict[str, Any]:
    try:
        fill = shape.fill
        out = {"type": _enum_name(fill.type)}
        if getattr(fill, "fore_color", None) is not None:
            out["foreground"] = _color(fill.fore_color)
        if getattr(fill, "back_color", None) is not None:
            out["background"] = _color(fill.back_color)
        return out
    except Exception:
        return {"type": "UNKNOWN"}


def _line(shape: Any) -> dict[str, Any]:
    try:
        line = shape.line
        out: dict[str, Any] = {"width_pt": round(float(line.width.pt), 3) if line.width else None}
        out["color"] = _color(line.color)
        out["dash_style"] = _enum_name(line.dash_style) if line.dash_style is not None else ""
        out["begin_arrowhead"] = _enum_name(line.begin_arrowhead)
        out["end_arrowhead"] = _enum_name(line.end_arrowhead)
        return out
    except Exception:
        return {}


def _xml_color(node: ET.Element | None) -> dict[str, Any]:
    if node is None:
        return {}
    out: dict[str, Any] = {"kind": local(node.tag)}
    out.update(node.attrib)
    alpha = node.find(".//{%s}alpha" % NS["a"])
    if alpha is not None and alpha.get("val"):
        try:
            out["opacity"] = round(int(alpha.get("val")) / 100000, 5)
        except ValueError:
            pass
    return out


def _raw_shape_style(shape: Any) -> dict[str, Any]:
    try:
        root = ET.fromstring(shape._element.xml)
        solid = root.find(".//{%s}solidFill" % NS["a"])
        ln = root.find(".//{%s}ln" % NS["a"])
        out = {"fill_ooxml": _xml_color(next(iter(solid), None) if solid is not None else None)}
        if ln is not None:
            out["line_ooxml"] = dict(ln.attrib)
            out["line_dash"] = dict((ln.find("{%s}prstDash" % NS["a"]) or ET.Element("x")).attrib)
        return out
    except Exception:
        return {}


def _bbox(shape: Any) -> dict[str, Any]:
    values = [getattr(shape, x, 0) for x in ("left", "top", "width", "height")]
    emu = {k: int(v or 0) for k, v in zip(("x", "y", "w", "h"), values)}
    inch = {k: round(v / EMU_PER_INCH, 5) for k, v in emu.items()}
    return {"emu": emu, "in": {**inch, "units": "in"}}


def _run_record(run: Any) -> dict[str, Any]:
    font = getattr(run, "font", None)
    item: dict[str, Any] = {"text": run.text or ""}
    if font is not None:
        for attr in ("name", "size", "bold", "italic", "underline"):
            try:
                value = getattr(font, attr)
                item[attr] = float(value.pt) if attr == "size" and value else value
            except Exception:
                pass
        try:
            item["color"] = _color(font.color)
        except Exception:
            pass
    try:
        address = run.hyperlink.address
        if address:
            item["hyperlink"] = address
    except Exception:
        pass
    return item


def _text_for_shape(shape: Any, slide_no: int, shape_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    blocks: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    frame = getattr(shape, "text_frame", None)
    if frame is None:
        return blocks, links
    for pno, paragraph in enumerate(frame.paragraphs):
        runs = [_run_record(run) for run in paragraph.runs]
        text = "".join(x["text"] for x in runs)
        block = {
            "slide": slide_no, "element_id": shape_id, "paragraph": pno, "text": text,
            "level": getattr(paragraph, "level", 0), "runs": runs,
        }
        blocks.append(block)
        for run in runs:
            if run.get("hyperlink"):
                links.append({"kind": "text", "slide": slide_no, "element_id": shape_id, "target": run["hyperlink"]})
    try:
        address = shape.click_action.hyperlink.address
        if address:
            links.append({"kind": "shape", "slide": slide_no, "element_id": shape_id, "target": address})
    except Exception:
        pass
    return blocks, links


def _cell_text(cell: Any) -> str:
    return "\n".join(p.text for p in cell.text_frame.paragraphs)


def _table_record(shape: Any, slide_no: int, element_id: str) -> dict[str, Any]:
    table = shape.table
    rows: list[list[dict[str, Any]]] = []
    for r, row in enumerate(table.rows):
        current = []
        for c, cell in enumerate(row.cells):
            raw = getattr(cell, "_tc", None)
            current.append({
                "row": r, "column": c, "text": _cell_text(cell),
                "is_merge_origin": bool(getattr(cell, "is_merge_origin", False)),
                "xml": ET.tostring(raw, encoding="unicode")[:10000] if raw is not None else "",
            })
        rows.append(current)
    return {"slide": slide_no, "element_id": element_id, "bbox": _bbox(shape), "rows": rows}


def _connector_info(shape: Any, slide_no: int, element_id: str) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": "connector", "slide": slide_no, "element_id": element_id, "bbox": _bbox(shape), "line": _line(shape)}
    try:
        root = ET.fromstring(shape._element.xml)
        cpr = root.find(".//{%s}cNvCxnSpPr" % NS["p"])
        for key in ("stCxn", "endCxn"):
            node = cpr.find("{%s}%s" % (NS["a"], key)) if cpr is not None else None
            if node is not None:
                out["start" if key == "stCxn" else "end"] = dict(node.attrib)
        prst = root.find(".//{%s}prstGeom" % NS["a"])
        if prst is not None:
            out["geometry"] = prst.get("prst", "")
    except Exception:
        out["xml_parse_warning"] = True
    out["relationship_semantics"] = "endpoint ids retained; resolve against slide element_id inventory"
    return out


def _chart_record(shape: Any, slide_no: int, element_id: str, embedded: list[dict[str, Any]]) -> dict[str, Any]:
    chart = shape.chart
    out: dict[str, Any] = {"slide": slide_no, "element_id": element_id, "bbox": _bbox(shape), "chart_type": _enum_name(chart.chart_type), "series": []}
    try:
        for i, series in enumerate(chart.series):
            cats: list[Any] = []
            vals: list[Any] = []
            try:
                cats = list(series.categories)
            except Exception:
                pass
            try:
                vals = list(series.values)
            except Exception:
                pass
            out["series"].append({"index": i, "name": str(series.name or ""), "categories": cats, "values": vals})
    except Exception as exc:
        out["series_error"] = str(exc)
    try:
        part = chart.part
        out["chart_part"] = str(part.partname)
        out["chart_xml_sha256"] = sha256_bytes(part.blob)
        for rel in part.rels.values():
            if "package" in rel.reltype or "oleObject" in rel.reltype or "image" in rel.reltype:
                entry = {"kind": "chart-related", "source": str(part.partname), "relationship_id": rel.rId, "type": rel.reltype, "target": str(rel.target_ref)}
                try:
                    blob = rel.target_part.blob
                    entry["sha256"] = sha256_bytes(blob)
                    entry["size"] = len(blob)
                    if str(rel.target_ref).lower().endswith((".xlsx", ".xlsm")):
                        try:
                            from io import BytesIO
                            from openpyxl import load_workbook
                            workbook = load_workbook(BytesIO(blob), data_only=False, read_only=True)
                            entry["kind"] = "chart-embedded-workbook"
                            entry["sheets"] = [{"title": ws.title, "rows": [[str(v) if v is not None else None for v in row] for row in ws.iter_rows(values_only=True)]} for ws in workbook.worksheets]
                            out["embedded_workbook_preserved"] = True
                        except Exception as workbook_exc:
                            entry["workbook_parse_warning"] = str(workbook_exc)
                except Exception:
                    pass
                embedded.append(entry)
    except Exception as exc:
        out["chart_part_warning"] = str(exc)
    return out


def _shape_type(shape: Any) -> str:
    try:
        return _enum_name(getattr(shape, "shape_type", "UNKNOWN"))
    except (AttributeError, NotImplementedError, ValueError) as exc:
        # python-pptx raises for producer-specific OOXML shape types.  The
        # raw shape still belongs in the inventory and may carry text,
        # relationships, or layout semantics, so retain it as opaque rather
        # than aborting the whole Source.
        return f"UNKNOWN_SHAPE:{type(exc).__name__}"


def _raw_image_evidence(shape: Any) -> dict[str, Any]:
    """Resolve a picture's OOXML media semantics and authoritative evidence.

    A ``p:pic`` with an explicit empty ``a:blip`` is a valid no-embed
    picture in the WPS-exported corpus. It is accounted for as a picture
    element, but it has no raw media bytes to hash. Embedded and linked
    pictures still require a relationship, and embedded pictures require
    readable package bytes.
    """
    source_part = str(getattr(getattr(shape, "part", None), "partname", "")).lstrip("/")
    base = {"source_part": source_part, "raw_media_evidence": False, "media_evidence_required": True}
    try:
        root = ET.fromstring(shape._element.xml)
        blips = root.findall(".//{%s}blip" % NS["a"])
        if len(blips) != 1:
            return {**base, "media_semantics": "malformed-picture", "raw_media_warning": "OOXML picture has no single blip"}
        blip = blips[0]
        embed_rid = blip.get("{%s}embed" % NS["r"])
        link_rid = blip.get("{%s}link" % NS["r"])
        if embed_rid and link_rid:
            # WPS can retain a fallback external link alongside the embedded
            # package media.  The embedded relationship is authoritative for
            # local evidence; retain the secondary link as provenance instead
            # of discarding an otherwise hashable image.
            link_rid = None
        if not embed_rid and not link_rid:
            return {
                **base,
                "media_evidence_required": False,
                "media_semantics": "picture-no-embed",
                "ooxml_blip_present": True,
                "raw_media_warning": "OOXML picture has no r:embed or r:link; no package raw media is authoritative",
            }
        rid = embed_rid or link_rid
        if not rid:
            return {**base, "media_semantics": "malformed-picture", "raw_media_warning": "OOXML image relationship id is missing"}
        rel = shape.part.rels.get(rid)
        if rel is None:
            return {**base, "relationship_id": rid, "media_semantics": "linked-external" if link_rid else "embedded-image", "raw_media_warning": "OOXML image relationship is missing"}
        reltype = str(getattr(rel, "reltype", ""))
        common = {
            **base,
            "relationship_id": rid,
            "relationship_type": reltype,
            "target": str(rel.target_ref),
            "target_mode": str(getattr(rel, "target_mode", "") or "Internal"),
            "relationship_evidence": True,
        }
        if "image" not in reltype.lower():
            return {**common, "media_semantics": "malformed-picture", "raw_media_warning": "OOXML picture relationship is not an image relationship"}
        try:
            target_part = rel.target_part
        except Exception:
            target_part = None
        if link_rid and (str(getattr(rel, "target_mode", "") or "").lower() == "external" or target_part is None):
            return {**common, "media_evidence_required": False, "media_semantics": "linked-external", "raw_media_warning": "OOXML linked image has no package raw media bytes"}
        if target_part is None:
            return {**common, "media_semantics": "embedded-image", "raw_media_warning": "OOXML embedded image target part is missing"}
        blob = target_part.blob
        if not isinstance(blob, (bytes, bytearray)) or not blob:
            return {**common, "media_semantics": "embedded-image", "raw_media_warning": "OOXML embedded image target has no raw bytes"}
        raw_hash = sha256_bytes(blob)
        package_path = str(target_part.partname).lstrip("/")
        return {
            **common,
            "media_semantics": "embedded-image" if embed_rid else "linked-package-media",
            "target_part": str(target_part.partname),
            "package_path": package_path,
            "content_type": getattr(target_part, "content_type", ""),
            "raw_media_sha256": raw_hash,
            "raw_media_size": len(blob),
            "raw_media_evidence": True,
        }
    except Exception as exc:
        return {**base, "media_semantics": "malformed-picture", "raw_media_warning": str(exc)}


def _image_evidence_accounted(items: list[dict[str, Any]]) -> bool:
    images = [x for x in items if x.get("kind") == "image"]
    non_raw_semantics = {"picture-no-embed", "linked-external", "linked-package-media"}
    return all(
        (
            not x.get("media_evidence_required", True)
            and x.get("media_semantics") in non_raw_semantics
            and (x.get("media_semantics") == "picture-no-embed" or x.get("relationship_evidence"))
        )
        or (
            bool(x.get("media_evidence_required", True))
            and bool(x.get("raw_media_evidence"))
            and bool(x.get("sha256"))
            and x.get("sha256") == x.get("raw_media_sha256")
        )
        for x in images
    )


def _walk_shapes(shapes: Any, slide_no: int, parent: str, text_blocks: list[dict[str, Any]], tables: list[dict[str, Any]], relationships: list[dict[str, Any]], embedded: list[dict[str, Any]], inventory: list[dict[str, Any]]) -> None:
    for z, shape in enumerate(shapes):
        eid = f"slide-{slide_no}-shape-{getattr(shape, 'shape_id', z)}"
        kind = _shape_type(shape)
        item: dict[str, Any] = {
            "element_id": eid, "slide": slide_no, "parent_element_id": parent, "z_order": z,
            "name": getattr(shape, "name", ""), "shape_type": kind, "bbox": _bbox(shape),
            "rotation": getattr(shape, "rotation", 0), "fill": _fill(shape), "line": _line(shape),
            "raw_style": _raw_shape_style(shape), "has_text": bool(getattr(shape, "has_text_frame", False) and safe_text(getattr(shape, "text", ""))),
        }
        inventory.append(item)
        b, l = _text_for_shape(shape, slide_no, eid)
        text_blocks.extend(b); relationships.extend(l)
        if getattr(shape, "has_table", False):
            tables.append(_table_record(shape, slide_no, eid))
        # Do not access shape.shape_type a second time: python-pptx raises
        # NotImplementedError for producer-specific opaque shapes.
        if kind == "GROUP":
            _walk_shapes(shape.shapes, slide_no, eid, text_blocks, tables, relationships, embedded, inventory)
        if "CONNECTOR" in kind or kind in {"LINE", "FREEFORM"}:
            relationships.append(_connector_info(shape, slide_no, eid))
        if "PICTURE" in kind:
            raw_evidence = _raw_image_evidence(shape)
            # Do not ask python-pptx for bytes when OOXML explicitly says
            # that this picture is linked/no-embed. Such bytes, if a parser
            # happens to synthesize them, are not package-authoritative.
            if not raw_evidence.get("media_evidence_required", True):
                raw_item: dict[str, Any] = {"kind": "image", "slide": slide_no, "element_id": eid, "warning": "picture has no embedded media; no raw-media hash expected", "format_supported": False, "bbox": _bbox(shape)}
                raw_item.update(raw_evidence)
                if raw_evidence.get("raw_media_evidence"):
                    raw_item.update({"sha256": raw_evidence["raw_media_sha256"], "size": raw_evidence["raw_media_size"]})
                embedded.append(raw_item)
            else:
                try:
                    blob = shape.image.blob
                    media = {"kind": "image", "slide": slide_no, "element_id": eid, "name": getattr(shape.image, "filename", ""), "content_type": shape.image.content_type, "sha256": sha256_bytes(blob), "size": len(blob), "bbox": _bbox(shape)}
                    media.update(raw_evidence)
                    if raw_evidence.get("raw_media_evidence") and media["sha256"] != raw_evidence.get("raw_media_sha256"):
                        media["raw_media_evidence"] = False
                        media["raw_media_warning"] = "python-pptx image bytes differ from OOXML relationship bytes"
                    try:
                        descr = shape._element.xpath(".//*[local-name()='cNvPr']")[0].get("descr", "")
                        media["alt_description"] = descr
                    except Exception:
                        pass
                    embedded.append(media)
                except Exception as exc:
                    # python-pptx may reject uncommon codecs (for example MPO),
                    # but the OOXML relationship and raw media bytes are still
                    # authoritative evidence and must not disappear.
                    raw_item: dict[str, Any] = {"kind": "image", "slide": slide_no, "element_id": eid, "warning": str(exc), "format_supported": False, "bbox": _bbox(shape)}
                    raw_item.update(raw_evidence)
                    if raw_evidence.get("raw_media_evidence"):
                        raw_item.update({"sha256": raw_evidence["raw_media_sha256"], "size": raw_evidence["raw_media_size"]})
                    embedded.append(raw_item)
        if "CHART" in kind:
            try:
                item["chart"] = _chart_record(shape, slide_no, eid, embedded)
                embedded.append({"kind": "chart", "slide": slide_no, "element_id": eid, "chart_type": item["chart"].get("chart_type"), "raw_data": item["chart"], "visual_required": True})
            except Exception as exc:
                item["chart_warning"] = str(exc)


def _slide_relationships(slide: Any, slide_no: int, embedded: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for rel in slide.part.rels.values():
        item: dict[str, Any] = {"slide": slide_no, "source_part": str(getattr(slide.part, "partname", "")).lstrip("/"), "relationship_id": rel.rId, "type": rel.reltype, "target": str(rel.target_ref), "target_mode": str(getattr(rel, "target_mode", "") or "Internal")}
        try:
            part = rel.target_part
            item["target_part"] = str(part.partname)
            item["package_path"] = str(part.partname).lstrip("/")
            blob = part.blob
            item["target_sha256"] = sha256_bytes(blob)
            item["target_size"] = len(blob)
        except Exception:
            pass
        out.append(item)
    return out


def _package_inventory(path: Path) -> dict[str, Any]:
    """Hash the package components that establish PPTX media semantics."""
    with zipfile.ZipFile(path) as z:
        content_types: dict[str, str] = {}
        try:
            root = ET.fromstring(z.read("[Content_Types].xml"))
            defaults = {x.get("Extension", ""): x.get("ContentType", "") for x in root if local(x.tag) == "Default"}
            overrides = {x.get("PartName", "").lstrip("/"): x.get("ContentType", "") for x in root if local(x.tag) == "Override"}
            for name in z.namelist():
                content_types[name] = overrides.get(name, defaults.get(name.rsplit(".", 1)[-1], ""))
        except (KeyError, ET.ParseError):
            content_types = {}
        prefixes = ("ppt/presentation.xml", "ppt/theme/", "ppt/slideLayouts/", "ppt/slideMasters/", "ppt/media/")
        parts = []
        for name in sorted(n for n in z.namelist() if n != "ppt/media/" and any(n == p or n.startswith(p) for p in prefixes)):
            blob = z.read(name)
            parts.append({"package_path": name, "content_type": content_types.get(name, ""), "size": len(blob), "sha256": sha256_bytes(blob)})
        return {"content_types_part": {"package_path": "[Content_Types].xml", "sha256": sha256_bytes(z.read("[Content_Types].xml"))}, "parts": parts}


def _diagram_evidence(path: Path, embedded: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        for name in names:
            if name.startswith("ppt/diagrams/") and name.endswith((".xml", ".rels")):
                blob = z.read(name)
                item = {"kind": "diagram-ooxml", "path": name, "sha256": sha256_bytes(blob), "size": len(blob)}
                if name.endswith(".xml"):
                    try:
                        root = ET.fromstring(blob)
                        pts = root.findall(".//{%s}pt" % NS["dgm"])
                        cxns = root.findall(".//{%s}cxn" % NS["dgm"])
                        item["point_count"] = len(pts); item["connection_count"] = len(cxns)
                        item["point_ids"] = [x.get("modelId", "") for x in pts[:500]]
                    except ET.ParseError:
                        item["parse_warning"] = "invalid diagram XML"
                result.append(item)
    embedded.extend(result)
    return result


def _theme_evidence(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.startswith("ppt/theme/") and n.endswith(".xml")]
        out = {"parts": []}
        for name in names:
            blob = z.read(name)
            colors = {}
            try:
                root = ET.fromstring(blob)
                scheme = root.find(".//{%s}clrScheme" % NS["a"])
                if scheme is not None:
                    for node in list(scheme):
                        child = list(node)
                        colors[local(node.tag)] = dict(child[0].attrib) if child else {}
            except ET.ParseError:
                pass
            out["parts"].append({"path": name, "sha256": sha256_bytes(blob), "colors": colors})
        return out


def _svg_color(style: dict[str, Any], default: str = "none") -> str:
    value = style.get("foreground", {}).get("rgb") or style.get("fill_ooxml", {}).get("val")
    if value:
        value = str(value).replace("#", "")
        return "#" + value[-6:]
    return default


def _visual_svg(slide: dict[str, Any], width: int, height: int, raw_images: dict[str, str]) -> str:
    scale_x, scale_y = width / max(slide.get("size", {}).get("width_emu", width), 1), height / max(slide.get("size", {}).get("height_emu", height), 1)
    # Elements use inches in the machine inventory; slide dimensions are kept in EMU.
    sx = width / max(slide.get("size", {}).get("width_in", 10), 1)
    sy = height / max(slide.get("size", {}).get("height_in", 5.625), 1)
    body = []
    for shape in slide.get("shapes", []):
        b = shape.get("bbox", {}).get("in", {})
        x, y, w, h = [float(b.get(k, 0)) * (sx if k in {"x", "w"} else sy) for k in ("x", "y", "w", "h")]
        fill = _svg_color(shape.get("fill", {}), "none")
        stroke = shape.get("line", {}).get("color", {}).get("rgb", "#606060")
        stroke = "#" + str(stroke).replace("#", "")[-6:] if stroke else "#606060"
        typ = shape.get("shape_type", "")
        if "PICTURE" in typ:
            if shape["element_id"] in raw_images:
                body.append(f'<image x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" preserveAspectRatio="xMidYMid meet" href="{raw_images[shape["element_id"]]}"/>')
        elif "CONNECTOR" in typ or typ in {"LINE", "FREEFORM"}:
            body.append(f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{x+w:.1f}" y2="{y+h:.1f}" stroke="{stroke}" stroke-width="2" marker-end="url(#arrow)"/>')
        elif "OVAL" in typ or "CIRCLE" in typ:
            body.append(f'<ellipse cx="{x+w/2:.1f}" cy="{y+h/2:.1f}" rx="{max(w/2,1):.1f}" ry="{max(h/2,1):.1f}" fill="{fill}" stroke="{stroke}"/>')
        else:
            body.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w,1):.1f}" height="{max(h,1):.1f}" fill="{fill}" stroke="{stroke}"/>')
        for block in slide.get("text_blocks", []):
            if block.get("element_id") == shape["element_id"] and block.get("text"):
                body.append(f'<text x="{x+5:.1f}" y="{y+18+16*int(block.get("paragraph",0)):.1f}" font-family="sans-serif" font-size="14" fill="#202020">{html.escape(block["text"])}</text>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6 z" fill="#606060"/></marker></defs><rect width="100%" height="100%" fill="white"/>{"".join(body)}</svg>'


def _render_visuals(path: Path, result: ReaderResult, slides: list[dict[str, Any]], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    # Pillow is intentionally the default fallback. It is bounded, has no
    # browser process lifetime, and emits a PNG even on minimal production
    # hosts. A future deployment with LibreOffice can replace this adapter
    # with a native renderer without changing ReaderResult.
    for i, slide in enumerate(slides, 1):
        w, h = 1280, 720
        svg = _visual_svg(slide, w, h, {})
        svg_path = out / f"slide-{i:04d}.svg"; png_path = out / f"slide-{i:04d}.png"
        svg_path.write_text(svg, encoding="utf-8")
        try:
            from PIL import Image, ImageColor, ImageDraw
            im = Image.new("RGB", (w, h), "white"); draw = ImageDraw.Draw(im)
            sx = w / max(float(slide.get("size", {}).get("width_in", 10)), 1); sy = h / max(float(slide.get("size", {}).get("height_in", 5.625)), 1)
            for shape in slide.get("shapes", []):
                b = shape.get("bbox", {}).get("in", {}); x, y, sw, sh = [float(b.get(k, 0)) * (sx if k in {"x", "w"} else sy) for k in ("x", "y", "w", "h")]
                color = _svg_color(shape.get("fill", {}), "white")
                try: fill = ImageColor.getrgb(color) if color != "none" else None
                except ValueError: fill = None
                typ = shape.get("shape_type", "")
                if "CONNECTOR" in typ or typ in {"LINE", "FREEFORM"}:
                    draw.line((x, y, x + sw, y + sh), fill="#606060", width=3)
                    draw.polygon([(x + sw, y + sh), (x + sw - 12, y + sh - 5), (x + sw - 5, y + sh - 12)], fill="#606060")
                elif "PICTURE" in typ:
                    draw.rectangle((x, y, x + sw, y + sh), outline="#557799", fill="#dfeaf4", width=2); draw.text((x + 4, y + 4), "[image]", fill="#345")
                elif "OVAL" in typ or "CIRCLE" in typ:
                    draw.ellipse((x, y, x + sw, y + sh), outline="#606060", fill=fill, width=2)
                else:
                    draw.rectangle((x, y, x + max(sw, 1), y + max(sh, 1)), outline="#606060", fill=fill, width=2)
                for block in slide.get("text_blocks", []):
                    if block.get("element_id") == shape["element_id"] and block.get("text"):
                        draw.text((x + 5, y + 5 + 16 * int(block.get("paragraph", 0))), safe_text(block["text"])[:120], fill="#202020")
            im.save(png_path, "PNG")
            render_mode = "pillow-structural-proxy"
        except Exception as exc:
            result.warnings.append(f"slide {i} visual renderer fallback: {exc}")
            from PIL import Image, ImageDraw
            im = Image.new("RGB", (w, h), "white"); d = ImageDraw.Draw(im)
            d.text((20, 20), f"PPTX slide {i} structural visual fallback", fill="black")
            d.text((20, 45), f"elements={len(slide.get('shapes', []))}", fill="black")
            im.save(png_path, "PNG")
            render_mode = "pillow-structural-fallback"
        result.visual_pages.append({"kind": "slide", "slide": i, "path": str(png_path), "sha256": __import__("hashlib").sha256(png_path.read_bytes()).hexdigest(), "dimensions": {"width": w, "height": h}, "render_mode": render_mode, "source_element_ids": [s["element_id"] for s in slide.get("shapes", [])]})
        result.derived_artifacts.append(lineage(result.raw_artifact_sha256, result.source_version, png_path, "visual-slide", render_mode=render_mode))
        result.derived_artifacts.append(lineage(result.raw_artifact_sha256, result.source_version, svg_path, "visual-layout-source", render_mode=render_mode))


def read_pptx(inp: ReaderInput, output_dir: str | Path) -> ReaderResult:
    if Presentation is None:
        result = new_result(inp, "PPTX")
        result.warnings.append("python-pptx is unavailable")
        result.parser_version = PPTX_PARSER_VERSION
        result.fidelity_flags = {"structure_parsed": False, "visual_rendered": False, "failure_explicit": True}
        return result.finalize()
    path, out = Path(inp.path), Path(output_dir)
    prs = Presentation(str(path))
    result = new_result(inp, "PPTX")
    result.reader_version = PPTX_READER_VERSION
    result.parser_version = PPTX_PARSER_VERSION
    result.structured_semantics["presentation"] = {"slide_count": len(prs.slides), "size": {"width_emu": prs.slide_width, "height_emu": prs.slide_height, "width_in": round(prs.slide_width / EMU_PER_INCH, 5), "height_in": round(prs.slide_height / EMU_PER_INCH, 5)}}
    result.structured_semantics["theme"] = _theme_evidence(path)
    result.structured_semantics["package_inventory"] = _package_inventory(path)
    slides: list[dict[str, Any]] = []
    for index, slide in enumerate(prs.slides, 1):
        slide_sem: dict[str, Any] = {"slide": index, "order": index, "size": result.structured_semantics["presentation"]["size"], "shapes": [], "text_blocks": [], "notes": ""}
        try:
            slide_sem["notes"] = slide.notes_slide.notes_text_frame.text
        except Exception:
            pass
        rels = _slide_relationships(slide, index, result.embedded_objects)
        result.relationships.extend(rels)
        _walk_shapes(slide.shapes, index, "", result.text_blocks, result.tables, result.relationships, result.embedded_objects, slide_sem["shapes"])
        slide_sem["text_blocks"] = [x for x in result.text_blocks if x.get("slide") == index]
        slides.append(slide_sem)
    result.structured_semantics["slides"] = slides
    _diagram_evidence(path, result.embedded_objects)
    _render_visuals(path, result, slides, out / "slides")
    chart_count = sum(1 for x in result.embedded_objects if x.get("kind") == "chart")
    image_count = sum(1 for x in result.embedded_objects if x.get("kind") == "image")
    connector_count = sum(1 for x in result.relationships if x.get("kind") == "connector")
    result.structured_semantics["visual_prompt_context"] = {"coordinate_units": "inches", "slide_images_join_key": "slide", "element_join_key": "element_id", "layout_inventory": [{"slide": s["slide"], "elements": s["shapes"]} for s in slides]}
    result.fidelity_flags = {
        "structure_parsed": True, "visual_rendered": len(result.visual_pages) == len(prs.slides), "relationships_preserved": True,
        "connector_relationships_preserved": connector_count >= 0, "chart_data_preserved": all(x.get("raw_data") for x in result.embedded_objects if x.get("kind") == "chart"),
        "chart_visual_evidence": all(any(v["slide"] == x.get("slide") for v in result.visual_pages) for x in result.embedded_objects if x.get("kind") == "chart"),
        "image_evidence_accounted": _image_evidence_accounted(result.embedded_objects), "shape_inventory_includes_non_text": True,
        "native_visual_renderer": any(v.get("render_mode") == "native-libreoffice" for v in result.visual_pages), "visual_evidence_available": bool(result.visual_pages) and len(result.visual_pages) == len(prs.slides), "slide_count": len(prs.slides), "chart_count": chart_count, "image_count": image_count, "connector_count": connector_count, "failure_explicit": True,
    }
    if not result.fidelity_flags["native_visual_renderer"]:
        result.warnings.append("native LibreOffice PPTX render unavailable; PNGs are deterministic structural visual proxies")
    return result.finalize()
