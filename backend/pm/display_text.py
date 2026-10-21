"""Plain-text normalisation for proposal display fields.

Models (and the rule fallback) sometimes echo the source's Markdown heading or
list marker into a proposal title, which then shows up as "# 青苗计划实施方案 v1"
in the review list.  Proposal *display* text is normalised here; verbatim
evidence (`raw_excerpt`, quotes, bodies) is never rewritten, so the evidence
chain keeps pointing at the original characters.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable

# Fields whose value is shown to the user as a short label or one-line summary.
DISPLAY_FIELDS = (
    "title", "name", "metric_name", "one_line_summary", "summary",
    "new_summary", "latest_progress_summary", "next_action_summary", "description",
)

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*")
_BULLET = re.compile(r"^\s{0,3}(?:[-*+•·]|\d{1,3}[.)、])\s+")
_QUOTE = re.compile(r"^\s{0,3}>+\s*")
_FENCE = re.compile(r"^\s{0,3}(?:```|~~~)")
_EMPHASIS = re.compile(r"(?<!\*)\*\*(.+?)\*\*(?!\*)|__(.+?)__|(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)|`([^`]+)`")
_WS = re.compile(r"\s+")


def clean(text: Any) -> Any:
    """Return ``text`` without Markdown markers, BOMs or hard line breaks."""
    if not isinstance(text, str):
        return text
    value = text.replace("\ufeff", "")
    lines = []
    for line in value.splitlines():
        if _FENCE.match(line):
            continue
        line = _HEADING.sub("", line)
        line = _QUOTE.sub("", line)
        line = _BULLET.sub("", line)
        lines.append(line)
    value = " ".join(lines)
    value = _EMPHASIS.sub(lambda m: next((g for g in m.groups() if g), ""), value)
    value = value.replace("**", "").replace("__", "").replace("`", "")
    return _WS.sub(" ", value).strip()


def clean_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Clean the display fields of a candidate payload in place and return it."""
    if not isinstance(payload, dict):
        return payload
    for field in DISPLAY_FIELDS:
        if field in payload and isinstance(payload[field], str):
            cleaned = clean(payload[field])
            # Never turn a usable label into an empty one.
            if cleaned:
                payload[field] = cleaned
    return payload


def clean_fields(values: Dict[str, Any], fields: Iterable[str]) -> Dict[str, Any]:
    for field in fields:
        if isinstance(values.get(field), str):
            cleaned = clean(values[field])
            if cleaned:
                values[field] = cleaned
    return values
