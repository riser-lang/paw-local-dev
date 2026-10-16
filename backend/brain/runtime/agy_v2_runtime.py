"""Canonical Web Agy V2 turn builder.

This module is deliberately read-only with respect to the production
workspace database.  It resolves the project for a turn, builds the ordered
Project Context V2 pack, and returns the answer contract/prompt.  Conversation
state is bounded metadata only; chain-of-thought and model scratchpads are
never accepted or returned.
"""

from __future__ import annotations

from pathlib import Path
import os
import sys

_BACKEND_DIR = str(Path(__file__).resolve().parents[2])
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import json
import re
import sqlite3
from typing import Any, Optional

from brain.context import agy_answer_contract as A
from brain.context import agy_conversation_context as CC
from brain.context import agy_intent_router as I
from brain.context import agy_project_context_pack as PACK

PRODUCTION_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))
FAIL_CLOSED_MESSAGE = "当前没有足够的已确认工作台依据来回答这个问题。请先明确项目或补充可核对的信息。"
MAX_WEB_PROMPT_CHARS = 7600

# --- Refusal semantics (B4) -------------------------------------------------
# A refusal is not one thing.  Reporting PROJECT_NOT_FOUND as "no confirmed
# evidence" misdescribes the failure and sends the user hunting for evidence
# that was never the problem.
PROJECT_NOT_FOUND_MESSAGE = "我还没有确定你指的是哪个项目。请补充项目名称。"
AMBIGUOUS_PROJECT_MESSAGE = "我找到了多个可能的项目，请确认你指的是：%s。"
NO_EVIDENCE_MESSAGE = "当前已定位该项目，但工作台中没有足够的依据回答这个问题。"

# --- Distinctive partial project matching (B2 step 5) -----------------------
# Deterministic only: normalized text + longest-prefix containment + unique
# hit.  No embedding, no LLM guess, no threshold-free Levenshtein, and never
# an unconditional top-1 pick.
MIN_PARTIAL_MATCH_CJK = 3
_PARTIAL_STRIP_RE = re.compile(
    r"[\s\u3000\-_/\\·、，。；：！？（）()\[\]【】「」《》\"'“”‘’]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _char_terms(text: str) -> set[str]:
    """Generic CJK bigrams + latin words; no domain vocabulary."""
    out: set[str] = set()
    for run in re.findall(r"[\u4e00-\u9fff]+", str(text or "")):
        out.add(run)
        for i in range(len(run) - 1):
            out.add(run[i:i + 2])
    for word in re.findall(r"[A-Za-z0-9_]{2,}", str(text or "")):
        out.add(word.lower())
    return out


def _rank_items_by_message(items: list[dict[str, Any]], message: str) -> list[dict[str, Any]]:
    """Score context items by generic term overlap with the question.

    Returns copies carrying a transient ``_rank`` used only for prompt
    compaction.  Deterministic; no external model and no domain word list.
    """
    want = _char_terms(message)
    # Question scaffolding carries no retrieval signal.
    want -= _char_terms("请问目前现在是什么是否多少哪里哪些有关情况资料项目这个我们能否后续最好如何优化怎么建议")
    ranked = []
    for item in items:
        if want:
            score = len(want & _char_terms("%s %s" % (item.get("title", ""), item.get("content", ""))))
        else:
            score = 0
        ranked.append({**item, "_rank": score})
    return ranked


def _relevant_excerpt(text: str, message: str, limit: int) -> str:
    """Longest question-overlapping window of ``text``, up to ``limit`` chars."""
    text = str(text or "")
    if len(text) <= limit:
        return text
    want = _char_terms(message)
    want -= _char_terms("请问目前现在是什么是否多少哪里哪些有关情况资料项目这个我们能否后续最好如何优化怎么建议")
    if not want:
        return text[:limit]
    pieces = [p for p in re.split(r"(?<=[。；\n])", text) if p]
    if len(pieces) <= 1:
        return text[:limit]
    # Sliding window over consecutive sentences: keep the densest run that fits.
    best_start, best_score = 0, -1.0
    for start in range(len(pieces)):
        used, score = 0, 0
        for end in range(start, len(pieces)):
            used += len(pieces[end])
            if used > limit:
                break
            score += len(want & _char_terms(pieces[end]))
            density = score / max(used, 1)
            if score > 0 and density > best_score:
                best_start, best_score = start, density
    chunk = "".join(pieces[best_start:])
    return chunk[:limit]


def _render_bounded_prompt(contract: dict[str, Any], message: str,
                           conv: CC.ConversationContext) -> str:
    """Render the V2 contract within the Web helper's hard input boundary.

    The contract retains the complete evidence index for validation.  Only the
    model-facing prose is compacted deterministically when a large project
    pack would exceed the helper's 8k input limit; no raw source or evidence
    id is dropped.
    """
    show_metrics = bool(re.search(
        r"(指标|数值|多少|同比|产能|次数|比例|百分比|%|率|金额|多少钱)",
        message,
    ))
    prompt_contract = contract
    if not show_metrics and contract.get("metric_facts"):
        prompt_contract = dict(contract)
        prompt_contract["metric_facts"] = []
        prompt_contract["context_items"] = [
            ({**item, "content": "指标已关联；用户未直接询问指标时不要输出数字。"}
             if item.get("layer") == "PROJECT_DATA_METRIC" else item)
            for item in contract.get("context_items", [])
        ]
    prompt = A.render_prompt(prompt_contract, message=message, conv=conv)
    if len(prompt) <= MAX_WEB_PROMPT_CHARS:
        return prompt

    # Uniform truncation is the wrong lever: it shrinks the two or three items
    # that actually answer the question by exactly as much as the dozens that do
    # not, so a global-scope question ("培训预算有多少？") loses the figure it
    # asked for while a wall of unrelated summaries survives.  Compaction is
    # therefore relevance-first: question-matched items keep a much larger
    # budget and the remainder is cut hard, so total size falls while the
    # answering evidence stays readable.
    items = _rank_items_by_message(list(contract.get("context_items", [])), message)
    # ``title_chars`` also bounds the evidence-index titles: the index repeats
    # one line per item, so at 29 items it alone costs ~2.5k characters and is
    # what actually pushes a global-scope turn past the boundary.
    for content_chars, title_chars, keep_chars, keep_titles in (
            (200, 40, 600, 60), (120, 28, 400, 44), (80, 22, 260, 32),
            (48, 16, 180, 24), (24, 12, 120, 16), (0, 8, 80, 10)):
        compact = dict(contract)
        compact["authority_notes"] = [str(n)[:180] for n in contract.get("authority_notes", [])[:4]]
        compact["metric_facts"] = contract.get("metric_facts", [])
        trimmed = []
        for item in items:
            hot = item.get("_rank", 0) > 0
            c_chars = keep_chars if hot else content_chars
            t_chars = keep_titles if hot else title_chars
            content = str(item.get("content", ""))
            if c_chars <= 0:
                content = "" if not hot else _relevant_excerpt(content, message, keep_chars)
            elif hot:
                # Keep the sentences that overlap the question rather than the
                # first N characters; the figure usually sits mid-paragraph.
                content = _relevant_excerpt(content, message, c_chars)
            else:
                content = content[:c_chars]
            trimmed.append({**item, "title": str(item.get("title", ""))[:t_chars],
                            "content": content})
        compact["context_items"] = trimmed
        compact["evidence_index"] = [
            {**entry, "title": str(entry.get("title", ""))[:title_chars]}
            for entry in contract.get("evidence_index", [])
        ]
        prompt = A.render_prompt(compact, message=message, conv=conv)
        if len(prompt) <= MAX_WEB_PROMPT_CHARS:
            return prompt

    # Last resort: keep every evidence id and the full text of the best-matching
    # items, and drop the prose of the rest entirely.  Dropping prose is safe
    # only because no evidence id is ever removed, so the grounding gate can
    # still validate the answer.
    compact = dict(contract)
    compact["authority_notes"] = [str(n)[:180] for n in contract.get("authority_notes", [])[:4]]
    compact["metric_facts"] = contract.get("metric_facts", [])
    compact["context_items"] = [
        {**item, "title": str(item.get("title", ""))[:60],
         "content": (_relevant_excerpt(str(item.get("content", "")), message, 600)
                     if item.get("_rank", 0) > 0 else "（内容已省略，证据 id 仍可用于引用）")}
        for item in items
    ]
    prompt = A.render_prompt(compact, message=message, conv=conv)
    if len(prompt) <= MAX_WEB_PROMPT_CHARS:
        return prompt

    # Keep every evidence id available in a purpose-built bounded prompt.  The
    # context block keeps the canonical marker and the same title/content shape
    # as the rendered contract, so a reader of this prompt sees one consistent
    # context format instead of a second, differently-labelled dialect.
    lines = [
        "你是 PAW 工作台助手 Agy，使用 PAW Agy V2 回答。你是工作助手，不是文件清单"
        "检索器，也不是只会复述正式知识的审计机器人：先直接回答用户真正的问题，再给支撑事实。",
        "权威顺序：项目结论→项目手册→当前已确认知识→指标/数据→当前关系→已批准决策→Todo→EvidenceUnit→SourceVersion。",
        "规则：Draft 只能标注为草稿/待确认，不是正式事实；指标数字只能引用数据层；"
        "历史/已取代只在明确询问历史时引用。",
        "事实与推理边界：关于 PAW 状态/历史/数字/决定/人员/规则的事实陈述必须由 PAW Evidence 支持，"
        "不得创造 PAW 中不存在的事实；但分析、判断、方案、优化、下一步、建议类内容必须基于已有 Evidence "
        "做综合推理，而不是只复述资料——推理与建议不要求它本身已经作为 PAW 正式事实存在，"
        "也不得因为「建议尚无对应正式 Knowledge」而拒绝给出建议。",
        "表达纪律：分析/建议必须明确写成「分析 / 判断 / 建议 / 可以考虑」，"
        "不得写成「PAW 已确定 / 已确认 / 已决定」；并尽量说明它是基于哪些现状、草稿或事实推导出来的。",
        ("允许输出建议、下一步与优先级（用户已明确请求）。"
         if contract.get("advice_allowed") else
         "用户本次没有请求建议：不要输出下一步动作、推荐或方案，"
         "但必须给出你的分析与判断，不要只复述资料。"),
    ]
    lines.append(A.answer_structure(contract))
    notes = contract.get("authority_notes") or []
    if notes:
        lines.append("权威提示：" + "；".join(str(n)[:120] for n in notes[:2]))
    metrics = contract.get("metric_facts") or []
    if metrics and show_metrics:
        lines.append("唯一可引用指标：" + json.dumps(metrics, ensure_ascii=False, separators=(",", ":")))
    elif metrics:
        lines.append("当前问题未直接询问指标：不得输出任何指标数字；如需指标请先让用户明确询问。")
    lines.extend([
        "必须输出机器证据映射，且只能使用下列 evidence id。"
        "事实用 kind=fact + evidence；分析与建议用 kind=analysis/recommendation + "
        "derived_from（指向支撑它的事实证据 id，建议本身不需要有对应 Knowledge）：",
        "<<<EVIDENCE_MAP",
        '{"claims":[{"kind":"fact","text":"...","evidence":["LAYER:item_id"]},'
        '{"kind":"recommendation","text":"...","derived_from":["LAYER:item_id"]}]}',
        "EVIDENCE_MAP>>>",
        "证据 id：",
    ])
    for entry in contract.get("evidence_index", []):
        lines.append("- %s [%s]" % (entry["evidence_id"], entry.get("authority")))
    header = lines
    tail = []
    if conv is not None:
        tail.append(conv.prompt_block())
    tail.append("用户消息：" + message)

    def assemble(content_chars, title_chars, relevant_chars):
        body = ["--- PAW 上下文 V2（按权威顺序）---"]
        for item in items:
            flag = "正式/当前" if item.get("is_formal_fact") else "非正式或待确认"
            content = str(item.get("content", ""))
            if item.get("layer") == "PROJECT_DATA_METRIC" and not show_metrics:
                content = "指标已关联；用户未直接询问指标时不要输出数字。"
            elif item.get("_rank", 0) > 0 and relevant_chars:
                # Same "- [evidence_id] title\n  content" shape as the rendered
                # contract, so the evidence id stays visible next to its text.
                content = _relevant_excerpt(content, message, relevant_chars)
            else:
                content = content[:content_chars]
            body.append("- [%s] %s\n  %s（%s）" % (
                item.get("item_id", ""), str(item.get("title", ""))[:title_chars],
                content, flag))
        return "\n".join(header + body + tail)

    prompt = assemble(40, 24, 300)
    if len(prompt) <= MAX_WEB_PROMPT_CHARS:
        return prompt
    # The evidence index alone can exceed the boundary when a workspace has very
    # many records.  Evidence ids are never dropped (the grounding gate needs
    # them), so the remaining prose is shrunk instead until the prompt fits.
    for content_chars, title_chars, relevant_chars in (
            (120, 24, 120), (60, 16, 60), (24, 10, 24), (0, 6, 0)):
        prompt = assemble(content_chars, title_chars, relevant_chars)
        if len(prompt) <= MAX_WEB_PROMPT_CHARS:
            return prompt
    return prompt


def _normalize_text(text: str) -> str:
    """Case-folded, punctuation- and whitespace-free form used for matching."""
    return _PARTIAL_STRIP_RE.sub("", str(text or "")).casefold()


def _distinctive_partial_matches(rows, message: str) -> list[dict[str, Any]]:
    """Projects the user can name by their distinctive short form.

    A project matches when the longest prefix of its normalized name that is at
    least MIN_PARTIAL_MATCH_CJK Chinese characters long also occurs in the
    normalized message.  Purely deterministic; the caller decides what to do
    when more than one project matches.
    """
    norm_msg = _normalize_text(message)
    if not norm_msg:
        return []
    out: list[dict[str, Any]] = []
    for pid, name in rows:
        norm_name = _normalize_text(name)
        if len(_CJK_RE.findall(norm_name)) < MIN_PARTIAL_MATCH_CJK:
            continue
        best = 0
        for cut in range(len(norm_name), MIN_PARTIAL_MATCH_CJK - 1, -1):
            prefix = norm_name[:cut]
            if len(_CJK_RE.findall(prefix)) < MIN_PARTIAL_MATCH_CJK:
                continue
            if prefix in norm_msg:
                best = cut
                break
        if best:
            out.append({"project_id": pid, "project_name": name,
                        "matched_text": norm_name[:best],
                        "match_length": best})
    out.sort(key=lambda m: (-m["match_length"], str(m["project_name"])))
    return out


def _refusal_for(resolution: dict[str, Any], pack: dict[str, Any],
                 contract: dict[str, Any]):
    """Classify a fail-closed turn instead of calling everything NO_EVIDENCE."""
    if resolution.get("ambiguous"):
        names = [str(c.get("project_name")) for c in
                 (resolution.get("candidates") or [])[:3]
                 if c.get("project_name")]
        return "AMBIGUOUS_PROJECT", {
            "status": "AMBIGUOUS_PROJECT",
            "message": (AMBIGUOUS_PROJECT_MESSAGE % " / ".join(names))
                       if names else PROJECT_NOT_FOUND_MESSAGE,
            "candidates": (resolution.get("candidates") or [])[:3],
            "resolution_method": resolution.get("resolution_method"),
        }
    if not (resolution.get("project_id") or resolution.get("project_name") or pack.get("project_id") or pack.get("project")):
        return "PROJECT_NOT_FOUND", {
            "status": "PROJECT_NOT_FOUND",
            "message": PROJECT_NOT_FOUND_MESSAGE,
            "candidates": [],
            "resolution_method": resolution.get("resolution_method"),
        }
    if not contract.get("evidence_index"):
        if pack.get("unlimited_project"):
            return "NO_EVIDENCE", {
                "status": "NO_EVIDENCE",
                "message": "当前工作台中尚未收录足够的已确认依据回答此问题（全局检索，未限定项目）。",
                "candidates": [],
                "resolution_method": "global_workspace_fallback",
            }
        return "NO_EVIDENCE", {
            "status": "NO_EVIDENCE",
            "message": NO_EVIDENCE_MESSAGE,
            "candidates": [],
            "resolution_method": resolution.get("resolution_method"),
        }
    return "RESOLVED", None


def _project_for_message(conn: sqlite3.Connection, message: str,
                         project_id: Optional[str],
                         project_name: Optional[str]) -> dict[str, Any]:
    if project_id:
        row = conn.execute("SELECT id, name FROM project WHERE id = ?",
                           (project_id,)).fetchone()
        if row:
            return {"project_id": row[0], "project_name": row[1],
                    "resolution_method": "explicit"}
    if project_name:
        row = conn.execute("SELECT id, name FROM project WHERE name = ? "
                           "ORDER BY COALESCE(status, '') = 'merged', created_at",
                           (project_name,)).fetchone()
        if row:
            return {"project_id": row[0], "project_name": row[1],
                    "resolution_method": "explicit_name"}

    rows = conn.execute(
        # Merged projects were folded into a kept project; they must not make a
        # name ambiguous after the user approved the merge.
        "SELECT id, name FROM project WHERE COALESCE(status, '') NOT IN ('archived', 'merged') "
        "ORDER BY length(name) DESC"
    ).fetchall()
    # Existing behaviour preserved, with one deliberate hardening: a project
    # whose normalized name is shorter than MIN_PARTIAL_MATCH_CJK characters is
    # too generic to bind a turn on its own.  Without this floor a project
    # literally named "运营" would hijack every message containing that word.
    matches = [dict(project_id=r[0], project_name=r[1])
               for r in rows
               if r[1] and r[1] in message
               and len(_normalize_text(r[1])) >= MIN_PARTIAL_MATCH_CJK]
    if len(matches) == 1:
        return {**matches[0], "resolution_method": "message_project_name",
                "ambiguous": False}
    if len(matches) > 1:
        return {"project_id": None, "project_name": None,
                "resolution_method": "ambiguous_message_project_name",
                "ambiguous": True, "candidates": matches[:3]}
    # B2 step 5 — unique distinctive partial match (common short form).
    partial = _distinctive_partial_matches(rows, message)
    if len(partial) == 1:
        best = partial[0]
        return {"project_id": best["project_id"],
                "project_name": best["project_name"],
                "resolution_method": "unique_distinctive_partial_match",
                "ambiguous": False,
                "matched_text": best["matched_text"]}
    if len(partial) > 1:
        return {"project_id": None, "project_name": None,
                "resolution_method": "ambiguous_distinctive_partial_match",
                "ambiguous": True,
                "candidates": [{"project_id": m["project_id"],
                                "project_name": m["project_name"]}
                               for m in partial[:3]]}

    return {"project_id": None, "project_name": None,
            "resolution_method": "unresolved", "ambiguous": False,
            "candidates": []}


# --- Follow-up intent inheritance (C6) --------------------------------------
# An elliptical continuation ("那如果只能先做一项呢？") carries none of the
# router's intent keywords, so the deterministic router falls back to the weak
# ``short_noun_phrase`` FACT_LOOKUP default (agy_intent_router, len <= 30).
# Inside an active thread that default is wrong twice over: it strips the
# synthesis structure the thread is already in, and it withdraws advice
# permission, so a legitimate follow-up is answered as a strict fact lookup --
# or, when the model answers it properly, refused by the grounding gate.
# A continuation inherits the previous turn's synthesis intent instead.
#
# The inheritance is deliberately narrow: it only ever fires on the weak
# ``short_noun_phrase`` fallback (never on a matched pattern), it needs an
# already-resolved active project, and a message that asks for a specific fact
# keeps the strict factual mode even inside a synthesis thread (spec §9 CASE 5
# reverse test).
_FOLLOWUP_ANAPHORA_RE = re.compile(
    r"(那|那么|这|这样|这些|这个|它|其|上面|前述|前面|刚才|刚刚|你说的|"
    r"你刚才|别的|其他|其它|剩余|剩下|其余|接着|然后|还有|另外|再|继续|"
    r"如果|要是|假如|"
    r"\bthat\b|\bthen\b|\bso\b|\bit\b|\bwhat about\b|\bhow about\b)",
    re.IGNORECASE)
_HARD_FACT_RE = re.compile(
    r"(是什么|是多少|多少|哪一[个条]|谁|什么时候|何时|哪一年|哪年|几号|几日|"
    r"定义|口径|数值|正式确认|已确认|已经确认|正式确定|正式批准|已批准|"
    r"批准|拍板|生效|发布了吗|"
    r"what is|how many|who|when|which|definition|value)",
    re.IGNORECASE)


def _inherit_followup_intent(intent: dict[str, Any], message: str,
                             conv: CC.ConversationContext,
                             has_project: bool) -> dict[str, Any]:
    """Let an elliptical follow-up continue the thread's synthesis intent."""
    if intent.get("intent") != I.FACT_LOOKUP:
        return intent
    if intent.get("matched_pattern") != "short_noun_phrase":
        return intent
    previous = str(conv.last_user_intent or "")
    if previous not in A.SYNTHESIS_INTENTS:
        return intent
    if not has_project:
        return intent
    msg = str(message or "")
    # The anaphor is required: a bare short phrase that merely happens to end
    # in "吗/呢" ("兵种培训吗") is not evidence that the user is continuing the
    # thread, and guessing there would over-answer a plain fact question.
    if not _FOLLOWUP_ANAPHORA_RE.search(msg):
        return intent
    if _HARD_FACT_RE.search(msg):
        return intent
    return {**intent, "intent": previous, "confidence": "MEDIUM",
            "matched_pattern": "followup_inherit:%s" % previous,
            "requires_advice_permission": previous not in I.ADVICE_PERMITTED}


def build_v2_turn(message: str, mode: str = "chat", *,
                  project_id: Optional[str] = None,
                  conversation: Optional[dict[str, Any]] = None,
                  db_path: str = PRODUCTION_DB) -> dict[str, Any]:
    """Build one canonical V2 prompt and its bounded updated conversation."""
    conv = CC.ConversationContext.from_dict(conversation or {})
    conn = PACK.connect_readonly(db_path)
    try:
        reference = conv.resolve_reference(message)
        selected_id = project_id or reference.get("project_id")
        selected_name = reference.get("project_name") if not project_id else None
        resolution = _project_for_message(conn, message, selected_id,
                                           selected_name)
        if resolution.get("ambiguous"):
            resolution = {**resolution, "project_id": None,
                          "project_name": None}
        if resolution.get("project_id") or resolution.get("project_name"):
            pack = PACK.build_project_context_pack(
                conn,
                project_id=resolution.get("project_id"),
                project_name=resolution.get("project_name"),
                include_historical=conv.wants_historical(message),
                include_raw_source=False,
            )
        else:
            pack = PACK.build_global_workspace_context_pack(
                conn,
                query_text=message,
                include_historical=conv.wants_historical(message),
            )
    finally:
        conn.close()

    active_project = pack.get("project") or {}
    active_id = pack.get("project_id") or (
        reference.get("project_id") if reference.get("refers_to_active") else None)
    active_name = pack.get("project_name") or (
        reference.get("project_name") if reference.get("refers_to_active") else None)
    intent = I.classify_intent(
        message,
        has_active_project=bool(active_id or conv.active_project_id),
        has_context=bool(conversation),
    )
    intent = _inherit_followup_intent(
        intent, message, conv, bool(active_id or conv.active_project_id))
    conv.update(
        user_message=message,
        intent=intent["intent"],
        project_id=active_id,
        project_name=active_name,
        entities=[active_name] if active_name else None,
    )
    contract = A.build_answer_contract(
        message=message, intent_info=intent, pack=pack, conv=conv)
    resolution_status, refusal = _refusal_for(resolution, pack, contract)
    # A document question can have usable workspace evidence without naming a project.
    # Keep that evidence grounded instead of rerouting the turn to general chat/web.
    if pack.get('status') == 'GLOBAL_WORKSPACE_SCOPE' and contract.get('evidence_index') and re.search(r'文档|资料|周报|文件|工作台|记录|例会|项目|预算|方案|进度|多少|谁|什么|哪|如何|怎么', message):
        resolution_status,refusal='GLOBAL_WORKSPACE',None
    prompt = _render_bounded_prompt(contract, message, conv)
    return {
        "prompt": prompt,
        "pack": pack,
        "contract": contract,
        "intent": intent,
        "conversation": conv,
        "project_resolution": resolution,
        "project": active_project,
        "resolution_status": resolution_status,
        "refusal": refusal,
        "fail_closed": bool(refusal) or not bool(contract.get("evidence_index")),
        "contract_version": "PAW_AGY_ANSWER_CONTRACT_V2",
    }


def grounding_metadata(turn: dict[str, Any], *, grounded: bool,
                       gate: str = "") -> dict[str, Any]:
    contract = turn["contract"]
    pack = turn["pack"]
    meta = {
        "contract": turn["contract_version"],
        "contract_version": turn["contract_version"],
        "context_pack": pack.get("pack_version"),
        "grounded": grounded,
        "intent": turn["intent"].get("intent"),
        "project_id": pack.get("project_id"),
        "project_name": pack.get("project_name"),
        "evidence_count": len(contract.get("evidence_index") or []),
        "layers": contract.get("authoritative_layers"),
        "authority_notes": contract.get("authority_notes") or [],
        "metric_fact_count": len(contract.get("metric_facts") or []),
        "clarification": contract.get("clarification"),
        "resolution_status": turn.get("resolution_status"),
        "resolution_method": (turn.get("project_resolution") or {}).get(
            "resolution_method"),
        "fail_closed": bool(turn.get("fail_closed")),
    }
    if gate:
        meta["gate"] = gate
    return meta
