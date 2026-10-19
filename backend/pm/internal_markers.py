"""Internal marker codes must never reach a user-visible answer (R-264).

PAW's answer path cites workspace items with machine codes such as
``CONFIRMED_KNOWLEDGE:pm-source-…`` or appends a ``<<<EVIDENCE_MAP … EVIDENCE_MAP>>>``
block. Those belong to the prompt/contract side. When a model echoes one back,
the reply shown to the user must carry the human wording instead: this module is
the fallback replacement of the C-12 "code → Chinese" table for free text.

Only known marker codes are touched; an unknown upper-case token is left alone,
and a bare short code (TODO, SOURCE, …) is only replaced when it carries a colon,
so ordinary prose is not rewritten.
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

# C-12 "代号 → 中文" table, extended with the marker codes a model can echo.
MARKER_LABELS: Dict[str, str] = {
    "PROJECT_OUTCOME": "项目结论",
    "PROJECT_HANDBOOK": "项目档案",
    "CONFIRMED_KNOWLEDGE": "已确认知识",
    "PROJECT_DATA_METRIC": "项目指标",
    "APPROVED_DECISION": "已批准决策",
    "PROJECT_RELATION": "项目关系",
    "EVIDENCE_UNIT": "证据条目",
    "SOURCE_VERSION": "资料版本",
    "EVIDENCE_MAP": "证据映射",
    "KNOWLEDGE": "知识",
    "SOURCE": "资料",
    "CAPTURE": "随手记",
    "EVIDENCE": "证据",
    "TODO": "待办",
}

# Codes that are unambiguous on their own (no colon needed).
_STANDALONE = (
    "PROJECT_OUTCOME",
    "PROJECT_HANDBOOK",
    "CONFIRMED_KNOWLEDGE",
    "PROJECT_DATA_METRIC",
    "APPROVED_DECISION",
    "PROJECT_RELATION",
    "EVIDENCE_UNIT",
    "SOURCE_VERSION",
)

_EVIDENCE_MAP_BLOCK = re.compile(r"<<<\s*EVIDENCE_MAP.*?(?:EVIDENCE_MAP\s*>>>|\Z)", re.S)
_STRAY_EVIDENCE_MAP = re.compile(r"<<<\s*EVIDENCE_MAP|EVIDENCE_MAP\s*>>>")

_CODES = sorted(MARKER_LABELS, key=len, reverse=True)
_CODE_ALT = "|".join(re.escape(code) for code in _CODES)
# CODE:identifier — the machine id (pm-source-…, E-XXX-NNN, uuid, [bracket]) goes away.
_CODE_WITH_ID = re.compile(
    r"(?<![A-Za-z0-9_])(?P<code>" + _CODE_ALT + r")\s*[:：]\s*"
    r"(?:\[[^\]\n]{0,120}\]|[A-Za-z0-9][A-Za-z0-9_.:\-]{0,120})?"
)
_STANDALONE_ALT = "|".join(re.escape(code) for code in _STANDALONE)
_STANDALONE_CODE = re.compile(r"(?<![A-Za-z0-9_])(?P<code>" + _STANDALONE_ALT + r")(?![A-Za-z0-9_])")


def _label(match: re.Match) -> str:
    return MARKER_LABELS.get(match.group("code").upper(), match.group("code"))


def strip(text: str) -> str:
    """Return ``text`` with every internal marker replaced by its Chinese label."""
    if not text or not isinstance(text, str):
        return text or ""
    value = _EVIDENCE_MAP_BLOCK.sub("", text)
    value = _STRAY_EVIDENCE_MAP.sub("", value)
    value = _CODE_WITH_ID.sub(_label, value)
    value = _STANDALONE_CODE.sub(_label, value)
    # Tidy the leftovers a removed code leaves behind.
    value = re.sub(r"[（(]\s*[)）]", "", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def found(text: str) -> List[str]:
    """Marker codes that still appear in ``text`` (empty when the answer is clean).

    Only the forms :func:`strip` rewrites are reported: ``CODE:``/``CODE:id``, the
    unambiguous layer codes, and an ``EVIDENCE_MAP`` block. A bare prose word such
    as "TODO" is not a marker.
    """
    if not text or not isinstance(text, str):
        return []
    hits = set()
    for match in _CODE_WITH_ID.finditer(text):
        hits.add(match.group("code").upper())
    for match in _STANDALONE_CODE.finditer(text):
        hits.add(match.group("code").upper())
    if _EVIDENCE_MAP_BLOCK.search(text) or _STRAY_EVIDENCE_MAP.search(text):
        hits.add("EVIDENCE_MAP")
    return [code for code in _CODES if code in hits]


def contains(text: str) -> bool:
    return bool(found(text))


def table() -> List[Tuple[str, str]]:
    """The code → Chinese table, longest codes first (for docs and tests)."""
    return [(code, MARKER_LABELS[code]) for code in _CODES]
