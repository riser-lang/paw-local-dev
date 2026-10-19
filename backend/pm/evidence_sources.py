"""Original files behind cited evidence items (R-267).

An assistant answer may cite a knowledge page; the user then needs the document
that knowledge came from, not only the page title. These helpers read the file
names an evidence item already carries in ``source_refs``/``provenance`` (e.g.
``"星火导师计划_10月周报.pptx(None)"``) and return clean names. Nothing here
knows a specific file name: every value comes from the evidence item itself.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

# A ref is either a plain file name or "<file name>(<wps id / None>)".
_TRAILING_ID = re.compile(r"^(?P<name>.+?)\s*[（(](?P<inner>[^（()）]*)[）)]\s*$")
_ID_LIKE = re.compile(r"^[A-Za-z0-9_\-]{0,40}$")


def clean_source_name(value: Any) -> str:
    """Strip a trailing machine id from a source reference, keeping the file name."""
    text = str(value or "").strip()
    if not text:
        return ""
    match = _TRAILING_ID.match(text)
    if match:
        inner = match.group("inner").strip()
        if not inner or inner.lower() in {"none", "null", "nan"} or _ID_LIKE.fullmatch(inner):
            text = match.group("name").strip()
    return text


def source_files_for(item: Dict[str, Any]) -> List[str]:
    """Original file names of one evidence item (deduped, API paths skipped)."""
    if not isinstance(item, dict):
        return []
    names: List[str] = []
    for key in ("source_refs", "provenance"):
        refs = item.get(key) or []
        if isinstance(refs, (str, bytes)):
            refs = [refs]
        if not isinstance(refs, Iterable):
            continue
        for ref in refs:
            name = clean_source_name(ref)
            if not name or name.startswith("/v1/") or name.startswith("/app"):
                continue
            if name not in names:
                names.append(name)
    return names


def evidence_key(item: Dict[str, Any]) -> str:
    """The id a citation uses for this item: "<layer>:<item_id or entity_id>"."""
    if not isinstance(item, dict):
        return ""
    item_id = item.get("item_id") or item.get("entity_id") or ""
    layer = item.get("layer") or ""
    return "%s:%s" % (layer, item_id) if layer and item_id else ""


def files_for_cited(context_items: Iterable[Dict[str, Any]], cited_ids: Iterable[str]) -> List[str]:
    """Original files of the cited items; with no citation, of every item."""
    wanted = {str(value) for value in cited_ids or [] if value}
    files: List[str] = []
    for item in context_items or []:
        if not isinstance(item, dict):
            continue
        if wanted and evidence_key(item) not in wanted:
            continue
        for name in source_files_for(item):
            if name not in files:
                files.append(name)
    return files


def cited_ids_from_map(answer_map: Any) -> List[str]:
    """Evidence ids an answer's EVIDENCE_MAP points at."""
    ids: List[str] = []
    if not isinstance(answer_map, dict):
        return ids
    for claim in answer_map.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        for key in ("evidence", "derived_from"):
            refs = claim.get(key) or []
            if isinstance(refs, (str, bytes)):
                refs = [refs]
            for ref in refs if isinstance(refs, Iterable) else []:
                value = str(ref or "").strip()
                if value and value not in ids:
                    ids.append(value)
    return ids


def append_sources_footer(text: str, files: Iterable[str]) -> str:
    """Append "来源：…" for original files the answer does not already name."""
    body = str(text or "").rstrip()
    missing = [name for name in (files or []) if name and name not in body]
    if not missing:
        return body
    return body + "\n\n来源：" + "、".join(missing)


def _json_list(value: Any) -> List[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item]
    text = str(value or "").strip()
    if not text.startswith("["):
        return [text] if text else []
    try:
        import json
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _source_title(conn: Any, reference: str) -> str:
    reference = str(reference or "").strip()
    if not reference:
        return ""
    try:
        row = conn.execute(
            "SELECT title FROM pm_source WHERE source_version_id=? OR source_version_id LIKE ?",
            (reference, reference + "%"),
        ).fetchone()
    except Exception:  # noqa: BLE001 - a missing table only means "no original file"
        return ""
    if not row:
        return ""
    try:
        return str(row[0] or "")
    except (IndexError, KeyError, TypeError):
        return ""


def knowledge_source_files(conn: Any, knowledge_id: str) -> List[str]:
    """Original documents a knowledge page was created from.

    The creation candidate records the source hashes (``source_refs``); the
    source registry maps a hash to the file name. No file name is assumed here.
    """
    if conn is None or not knowledge_id:
        return []
    try:
        rows = conn.execute(
            "SELECT source_refs FROM candidate WHERE applied_entity_id=? AND source_refs IS NOT NULL"
            " ORDER BY created_at DESC",
            (str(knowledge_id),),
        ).fetchall()
    except Exception:  # noqa: BLE001
        return []
    files: List[str] = []
    for row in rows:
        raw = row[0] if not isinstance(row, dict) else row.get("source_refs")
        for reference in _json_list(raw):
            name = _source_title(conn, reference)
            if name and name not in files:
                files.append(name)
    return files
