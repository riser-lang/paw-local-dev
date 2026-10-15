#!/usr/bin/env python3
"""supplemental_airpage_parser.py — AirPage Semantic Parser & Deterministic Markdown Generator.

Parses KDocs AirPage / .otl structured documents into canonical semantic models,
extracts blocks, headings, lists, tables, callouts, and attachment/picture references,
and deterministically renders readable Markdown representations.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple


def render_inline_nodes(node: Any) -> str:
    """Render inline text, formatting marks, emoji, and inline containers."""
    if not node:
        return ""
    if isinstance(node, list):
        return "".join(render_inline_nodes(c) for c in node)
    if not isinstance(node, dict):
        return str(node)

    ntype = node.get("type", "")

    if ntype == "text":
        t = node.get("text", "")
        marks = node.get("marks", [])
        for m in marks:
            mtype = m.get("type") if isinstance(m, dict) else ""
            if mtype == "bold":
                t = f"**{t}**"
            elif mtype == "italic":
                t = f"*{t}*"
            elif mtype == "code":
                t = f"`{t}`"
            elif mtype == "strike":
                t = f"~~{t}~~"
            elif mtype == "link":
                href = m.get("attrs", {}).get("href", "")
                t = f"[{t}]({href})"
        return t

    if ntype == "emoji":
        attrs = node.get("attrs", {})
        return attrs.get("emoji", "") or "🔔"

    if ntype == "picture":
        attrs = node.get("attrs", {})
        caption = attrs.get("caption") or attrs.get("title") or attrs.get("imgID") or "image"
        src_key = attrs.get("sourceKey") or attrs.get("imgID") or ""
        return f"![{caption}](image://{src_key})"

    # Recursively render children
    content = node.get("content", [])
    if content:
        return "".join(render_inline_nodes(c) for c in content)
    return ""


def parse_airpage_semantic_tree(raw_otl: dict, object_id: str = "", object_name: str = "") -> dict:
    """Parse raw KDocs .otl JSON into a clean, canonical semantic model and derived Markdown.

    Returns a dictionary with:
    - semantic_model: Canonical structured JSON dict
    - markdown_text: Deterministically derived Markdown
    - semantic_sha256: Hash of canonical normalized semantic_model
    - markdown_sha256: Hash of markdown_text
    """
    root_content = raw_otl.get("content", {})
    doc_version = raw_otl.get("version", 0)
    schema_version = raw_otl.get("schemaVersion", 0)
    src_doc_id = raw_otl.get("srcDocID") or raw_otl.get("id", "") or object_id

    blocks: List[Dict[str, Any]] = []
    attachments: List[Dict[str, Any]] = []
    table_count = 0
    total_chars = 0

    def extract_blocks(node: dict):
        nonlocal table_count, total_chars
        ntype = node.get("type", "")
        attrs = node.get("attrs", {})

        if ntype == "outline-title":
            title_text = render_inline_nodes(node).strip()
            if title_text:
                blocks.append({
                    "kind": "TITLE",
                    "level": 1,
                    "text": title_text,
                })
                total_chars += len(title_text)
            return

        if ntype == "heading":
            level = attrs.get("level", 2)
            # Level in KDocs is 1-indexed for headings
            h_text = render_inline_nodes(node).strip()
            if h_text:
                blocks.append({
                    "kind": "HEADING",
                    "level": min(max(int(level), 1), 6),
                    "text": h_text,
                })
                total_chars += len(h_text)
            return

        if ntype == "paragraph":
            text = render_inline_nodes(node).strip()
            list_type = attrs.get("listType", "")
            list_val = attrs.get("listValue", "")
            todo_id = attrs.get("todo_id")

            if text or todo_id:
                block_entry = {
                    "kind": "PARAGRAPH",
                    "text": text,
                    "list_type": list_type,
                    "list_value": list_val,
                    "is_todo": bool(todo_id),
                }
                blocks.append(block_entry)
                total_chars += len(text)
            return

        if ntype in ("outline-table", "table"):
            table_count += 1
            rows_data: List[List[str]] = []
            for row_node in node.get("content", []):
                if row_node.get("type") in ("outline-table-row", "table_row"):
                    row_cells = []
                    for cell_node in row_node.get("content", []):
                        cell_text = render_inline_nodes(cell_node).strip()
                        clean_cell = re.sub(r"\s*\n+\s*", "<br>", cell_text)
                        clean_cell = clean_cell.replace("|", "\\|")
                        row_cells.append(clean_cell)
                    rows_data.append(row_cells)

            blocks.append({
                "kind": "TABLE",
                "table_index": table_count,
                "rows": rows_data,
                "row_count": len(rows_data),
                "col_count": max((len(r) for r in rows_data), default=0),
            })
            for r in rows_data:
                for c in r:
                    total_chars += len(c)
            return

        if ntype == "HighlightBlock":
            callout_text = render_inline_nodes(node).strip()
            if callout_text:
                blocks.append({
                    "kind": "CALLOUT",
                    "text": callout_text,
                })
                total_chars += len(callout_text)
            return

        if ntype == "picture":
            p_attrs = node.get("attrs", {})
            img_info = {
                "img_id": str(p_attrs.get("imgID", "") or ""),
                "caption": str(p_attrs.get("caption", "") or ""),
                "title": str(p_attrs.get("title", "") or ""),
                "source_key": str(p_attrs.get("sourceKey", "") or ""),
                "width": int(p_attrs.get("oriWidth", 0) or 0),
                "height": int(p_attrs.get("oriHeight", 0) or 0),
            }
            attachments.append(img_info)
            blocks.append({
                "kind": "IMAGE_REFERENCE",
                "image": img_info
            })
            return

        # Recursive walk for other container nodes
        for child in node.get("content", []):
            if isinstance(child, dict):
                extract_blocks(child)

    extract_blocks(root_content)

    # Derived Markdown synthesis
    md_lines: List[str] = []
    # If no title block was present, synthesize one from object_name
    has_title = any(b["kind"] == "TITLE" for b in blocks)
    if not has_title and object_name:
        clean_name = re.sub(r"\.otl$", "", object_name)
        md_lines.append(f"# {clean_name}\n")

    for b in blocks:
        bkind = b["kind"]
        if bkind == "TITLE":
            md_lines.append(f"# {b['text']}\n")
        elif bkind == "HEADING":
            hashes = "#" * (b["level"] + 1)
            md_lines.append(f"{hashes} {b['text']}\n")
        elif bkind == "PARAGRAPH":
            if b.get("is_todo"):
                md_lines.append(f"- [ ] {b['text']}")
            elif b.get("list_type") in ("ordered", "circleNum"):
                val = b.get("list_value") or "1."
                md_lines.append(f"{val} {b['text']}")
            elif b.get("list_type") in ("bullet", "disc"):
                md_lines.append(f"- {b['text']}")
            else:
                md_lines.append(f"{b['text']}\n")
        elif bkind == "CALLOUT":
            clean_text = b["text"].replace("\n", "\n> ")
            md_lines.append(f"> [!NOTE]\n> {clean_text}\n")
        elif bkind == "IMAGE_REFERENCE":
            img = b["image"]
            cap = img.get("caption") or img.get("title") or img.get("img_id") or "image"
            src = img.get("source_key") or img.get("img_id") or ""
            md_lines.append(f"![{cap}](image://{src})\n")
        elif bkind == "TABLE":
            rows = b.get("rows", [])
            if rows:
                cols = max(len(r) for r in rows)
                header = rows[0]
                padded_header = list(header)
                while len(padded_header) < cols:
                    padded_header.append("")
                md_lines.append("| " + " | ".join(padded_header) + " |")
                md_lines.append("| " + " | ".join(["---"] * cols) + " |")
                for r in rows[1:]:
                    padded_r = list(r)
                    while len(padded_r) < cols:
                        padded_r.append("")
                    md_lines.append("| " + " | ".join(padded_r) + " |")
                md_lines.append("")

    markdown_text = "\n".join(md_lines).strip() + "\n"

    semantic_model = {
        "doc_id": str(src_doc_id),
        "doc_version": doc_version,
        "schema_version": schema_version,
        "block_count": len(blocks),
        "table_count": table_count,
        "attachment_count": len(attachments),
        "text_char_count": total_chars,
        "attachments": attachments,
        "blocks": blocks,
    }

    # Canonical normalized JSON serialization
    norm_json_bytes = json.dumps(semantic_model, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    semantic_sha256 = hashlib.sha256(norm_json_bytes).hexdigest()
    markdown_sha256 = hashlib.sha256(markdown_text.encode("utf-8")).hexdigest()

    return {
        "semantic_model": semantic_model,
        "markdown_text": markdown_text,
        "semantic_sha256": semantic_sha256,
        "markdown_sha256": markdown_sha256,
        "norm_json_bytes": norm_json_bytes,
    }
