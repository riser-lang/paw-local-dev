"""Extractive analysis stand-in for the standard exam.

The scripted analysis in ``mock_openai_server`` returns the same four
candidates whatever document it reads.  Early B/PM cases are written against
that script, so it must stay the default.  For the exam it is useless: a real
model would never answer that way, and the fixed candidates overwrite real
extracted state.

This mode derives every candidate from the document text carried in the
request.  Rules are vocabulary-only and generic: no project, file or question
name is assumed, and nothing is invented when the request does not contain it.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

# --- Request parsing --------------------------------------------------------

_DOC_NAME_RE = re.compile(r"文档名称[:：]\s*([^\n]+)")
_BLOCKS_RE = re.compile(r"^\s*\[(\d+)\]\s?(.*)$", re.M)
_ANALYSIS_JSON_RE = re.compile(r'\{"project_id".*', re.S)


def _active_projects(text: str) -> List[str]:
    """Project names the request itself lists as existing."""
    m = re.search(r"现有活跃项目[^\n]*?[:：]\s*\[(.*?)\]", text, re.S)
    if not m:
        return []
    return [n for n in re.findall(r"['\"]([^'\"]+)['\"]", m.group(1)) if n.strip()]


def _document_name(text: str) -> str:
    m = _DOC_NAME_RE.search(text)
    if m:
        return m.group(1).strip()
    # The section-update contract carries the title inside its JSON payload.
    m = re.search(r'"source_title"\s*:\s*"([^"]+)"', text)
    return m.group(1) if m else ""


def _text_blocks(text: str) -> List[str]:
    """The document's own text, as the request lists it.

    The block sample is rendered as "[0] first line" followed by the block's
    remaining lines, so a multi-line block is one entry terminated by the next
    "[n]" marker or by the blank line before the next section.
    """
    blocks: List[str] = []
    current: List[str] = []

    def flush():
        if current:
            joined = "\n".join(current).strip()
            if joined:
                blocks.append(joined)
            current.clear()

    for line in text.splitlines():
        marker = _BLOCKS_RE.match(line)
        if marker:
            flush()
            rest = marker.group(2).strip()
            if rest:
                current.append(rest)
            continue
        stripped = line.strip()
        if not stripped:
            # A blank line ends the block sample section entirely.
            if current:
                flush()
            continue
        lowered = stripped
        if re.match(r"^(现有活跃项目|项目归属规则|请基于上述文档|文档名称)", lowered):
            flush()
            continue
        if current:
            current.append(stripped)
    flush()

    body = re.findall(r'"body"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    for raw in body:
        try:
            decoded = raw.encode("utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8")
        except Exception:
            decoded = raw
        blocks += [b.strip() for b in decoded.split("\n") if b.strip()]
    return [b for b in blocks if b]


def _sentences(blocks: List[str]) -> List[str]:
    out: List[str] = []
    for block in blocks:
        for line in block.split("\n"):
            for piece in re.split(r"[。；;]\s*", line):
                piece = piece.strip(" \t\r\n　")
                if piece:
                    out.append(piece)
    return out


# --- Generic markers --------------------------------------------------------
# Status language only: these say whether a sentence reports progress, a goal,
# or an unfinished action.  They never name a domain, a project or a figure.
_PROGRESS_RE = re.compile(
    r"已完成|完成|已交付|已上线|已招募|进行中|推进|进展|启动|落地|上线|达成|"
    r"已通过|首批|阶段|里程碑|prototype|done|completed")
_GOAL_RE = re.compile(r"目标|计划|拟|希望|要求达到|力争|规划|指标|目标值")
_ADJUST_RE = re.compile(r"调整|改为|变更为|更新为|上调|下调|替代|修订")
_RISK_RE = re.compile(r"风险|阻碍|尚未|未提名|待定|延期|问题")
_PENDING_RE = re.compile(r"需要|待办|待定|下周|本月|后续|接下来|下一步|计划|拟|尚未|未开始|跟进|准备|安排|未完成")
_FINISHED_RE = re.compile(r"已完成|已交付|已上线|已通过|已经完成")
# A to-do must name an action, not just carry a status word.
_ACTION_RE = re.compile(r"完成|整理|提交|确定|组织|推进|跟进|准备|安排|召开|复核|复读|梳理|落实|开展|制定|评审|申报|招募|输出|上线|验收")
# Spreadsheet cells and heading fragments are not a progress statement.
_HEADING_NOISE_RE = re.compile(r"^(?:工作表|[A-Z]\d+[:：]|表头|行数据|headers_footers|footnotes|endnotes|comments)")
# A row of column labels is a header, not content: it is short tokens separated
# only by whitespace, with no sentence structure.
_COLUMN_HEADER_RE = re.compile(r"^\S+(?:\s{1,}\S+){1,}$")


def _project_for(title: str, blocks: List[str], active: List[str]) -> str:
    """Project subject of the document, from the request only.

    Preference order: an existing project named in the document text, then one
    named in the document title.  A document that names no existing project
    yields no update - the stand-in does not invent ownership.
    """
    body = "\n".join(blocks)
    for name in sorted(active, key=len, reverse=True):
        if name and name in body:
            return name
    for name in sorted(active, key=len, reverse=True):
        if name and name in title:
            return name
    return ""


def _first(sentences: List[str], pattern) -> str:
    for sentence in sentences:
        if pattern.search(sentence):
            return sentence
    return ""


def analyse(text: str) -> Dict[str, Any]:
    """Build the analysis envelope entirely from the request's document text."""
    title = _document_name(text)
    blocks = _text_blocks(text)
    active = _active_projects(text)
    sentences = _sentences(blocks)
    project = _project_for(title, blocks, active)

    project_updates: List[Dict[str, Any]] = []
    if project and sentences:
        # The summary is the document's own sentence about where things stand,
        # preferring one that states progress over one that only names a plan.
        usable = [s for s in sentences
                  if len(s) >= 4 and not _HEADING_NOISE_RE.match(s)
                  and not _COLUMN_HEADER_RE.match(s)]
        summary = (_first(usable, _PROGRESS_RE)
                   or _first(usable, _ADJUST_RE)
                   or _first(usable, _GOAL_RE))
        if summary:
            project_updates.append({
                "statement": f"{project}：{summary}",
                "confidence": "HIGH",
                "inference_level": "EXPLICIT",
                "recommended_action": "DRAFT_ONLY",
                "proposed_change": {"project_name": project, "new_summary": summary},
            })

    todos: List[Dict[str, Any]] = []
    # A to-do needs an explicit unfinished action.  A heading or a metadata
    # line can carry a status word by coincidence, so the sentence must also
    # name an action and must not already report the work as done.
    pending = ""
    for sentence in sentences:
        if _FINISHED_RE.search(sentence) or _COLUMN_HEADER_RE.match(sentence):
            continue
        if _ACTION_RE.search(sentence) and _PENDING_RE.search(sentence):
            pending = sentence
            break
    if pending:
        todos.append({
            "statement": pending,
            "confidence": "HIGH",
            "inference_level": "EXPLICIT",
            "recommended_action": "DRAFT_ONLY",
            "proposed_change": {"title": pending[:120], "description": pending[:300],
                                "priority": "medium"},
        })

    facts = [{"statement": b[:300], "confidence": "HIGH", "inference_level": "EXPLICIT"}
             for b in blocks[:4]]

    candidates: List[Dict[str, Any]] = []
    for update in project_updates:
        candidates.append({
            "target_type": "project", "operation": "update",
            "candidate_type": "project_update",
            "payload": update["proposed_change"],
            "raw_excerpt": update["statement"],
            "evidence_locators": ["text_blocks[0]"],
            "confidence": "HIGH",
            "summary": update["proposed_change"]["new_summary"][:120],
        })
    for todo in todos:
        candidates.append({
            "target_type": "todo", "operation": "create", "candidate_type": "todo",
            "payload": todo["proposed_change"], "raw_excerpt": todo["statement"],
            "evidence_locators": ["text_blocks[0]"], "confidence": "HIGH",
            "summary": "待办：" + todo["proposed_change"]["title"][:80],
        })

    return {
        "facts": facts,
        "events": [], "decisions": [], "plans": [], "ideas": [], "questions": [],
        "completed_actions": [], "pending_actions": [], "status_changes": [],
        "people": [], "dates": [], "projects": [],
        "todo_candidates": todos,
        "project_update_candidates": project_updates,
        "knowledge_candidates": [],
        "conflicts": [], "ambiguities": [],
        "candidates": candidates,
    }
