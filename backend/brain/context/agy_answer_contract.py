"""PAW Agy Answer Contract V2 (spec §18–§25).

Turns an intent + context pack into an answer that a business user can read,
while keeping the grounding audit intact.

§18  answer shape       — answer first, then core facts, then uncertainty;
                          suggestions only on an explicit request
§19  evidence/display   — the USER sees "依据：N 条 / 查看依据", not a
                          per-sentence [E-SRC-001]; the VALIDATOR still verifies
                          every claim through a machine-only evidence map
§20  grounded synthesis — allowed, tagged ``type=synthesis``, never disguised
                          as Source text
§21  suggestions        — only on explicit request; facts and advice separated
§22  clarification      — only when the ambiguity changes the answer; offer 2–3
                          real candidates
§23  knowledge authority— Current Confirmed Knowledge > Draft; Draft is never a
                          Formal Fact; historical only on an explicit ask
§24  metric numbers     — must come from the deterministic data layer
§25  relation state     — current / superseded / historical / disputed
§26  synthesis policy   — FACT GROUNDING != RECOMMENDATION PRE-EXISTENCE.
                          Facts must come from PAW Evidence; analysis and
                          recommendations do NOT have to already exist in PAW,
                          because the model may derive them from PAW facts.
                          What stays forbidden is inventing facts, or passing a
                          recommendation off as a confirmed PAW decision.

Pure module: no I/O, no writes, no LLM.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from . import agy_context_v2 as C
from . import agy_intent_router as I

#: machine-only block the model must emit; stripped before the user sees it
EVIDENCE_MAP_OPEN = "<<<EVIDENCE_MAP"
EVIDENCE_MAP_CLOSE = "EVIDENCE_MAP>>>"
_EVIDENCE_MAP_RE = re.compile(
    re.escape(EVIDENCE_MAP_OPEN) + r"\s*(?P<body>.*?)\s*" + re.escape(EVIDENCE_MAP_CLOSE),
    re.DOTALL)

#: the answer section order the user sees (§18)
ANSWER_SECTIONS = ("answer", "core_facts", "uncertainty", "suggestions",
                   "clarification")

#: a claim that states a PAW-specific fact must be mapped to evidence
_PAW_CLAIM_RE = re.compile(
    r"(项目|知识|指标|口径|状态|进展|结论|负责人|目标|预算|人数|流程|制度|"
    r"标准|计划|决策|依据|来源)")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|人|万|元|次|天|个|条|份)?")

# --------------------------------------------------------------------------
# §26 grounded synthesis — claim kinds
# --------------------------------------------------------------------------
#: a claim is one of these three kinds.  Only ``fact`` asserts something about
#: PAW and therefore needs its own evidence id.  ``analysis`` / ``recommendation``
#: are model-derived: they need ``derived_from`` (the facts they were derived
#: from), never a Knowledge item of their own.
CLAIM_KINDS = ("fact", "analysis", "recommendation")

#: intents whose answer must integrate + judge rather than re-list material
SYNTHESIS_INTENTS = frozenset({I.ANALYZE, I.PLAN, I.WHY, I.COMPARE})

#: the kinds that must never be phrased as an existing PAW decision
DERIVED_KINDS = frozenset({"analysis", "recommendation"})

_RECOMMENDATION_HINTS_RE = re.compile(
    r"(建议|推荐|可以考虑|不妨|可以尝试|应当|应该|优先级|优先推进|优先做|"
    r"下一步|我认为|我的判断|优化方向|改进方向|可以优化|可优化|"
    r"recommend|suggest|should|next step|priority)", re.IGNORECASE)
_ANALYSIS_HINTS_RE = re.compile(
    r"(我的判断|我认为|我的分析|综合来看|综合判断|判断|分析|意味着|"
    r"可以看出|说明|基于|因此|所以|问题在于|瓶颈|缺口|尚未闭环|"
    r"implies|analysis|my assessment|means that)", re.IGNORECASE)

#: phrasing that turns a model-derived claim into a false PAW fact
_FACT_ASSERTION_RE = re.compile(
    r"(PAW\s*(?:已经|已|中)?\s*(?:正式)?(?:确认|确定|决定|批准|拍板)|"
    r"已(?:经)?\s*(?:正式)?(?:确认|确定|决定|批准|拍板)|"
    r"正式(?:确认|确定|决定|批准)|"
    r"既成事实|"
    r"confirmed|decided|approved)", re.IGNORECASE)

#: A pending/negated mention is not an assertion — "核心制度仍待确认与正式批准"
#: says the opposite of "已正式批准", and "尚未正式确认" says the opposite of
#: "正式确认".  The bare 正式确认/正式批准 alternative would otherwise match the
#: negation and reject a correct answer.
_FACT_NEGATION_RE = re.compile(
    r"(尚未|没有|未|不|还未|暂未|未曾|非|待|not|never|pending)")
#: "已正式确认的选拔复试评价体系" modifies a noun: it refers to knowledge that
#: genuinely is confirmed, rather than asserting a new status.
_FACT_ATTRIBUTIVE_RE = re.compile(r"^\s*(?:的|性)")


def _asserts_existing_decision(text: str) -> bool:
    """True when ``text`` claims a decision already exists (spec §26)."""
    for m in _FACT_ASSERTION_RE.finditer(text):
        pre = text[max(0, m.start() - 10):m.start()]
        if _FACT_NEGATION_RE.search(pre):
            continue
        if _FACT_ATTRIBUTIVE_RE.match(text[m.end():m.end() + 2]):
            continue
        return True
    return False


# --------------------------------------------------------------------------
# §22 clarification
# --------------------------------------------------------------------------
def build_clarification(pack: dict[str, Any], *,
                        candidates: Optional[list[dict]] = None,
                        reason: str = "") -> dict[str, Any]:
    """Ask only when the ambiguity changes the answer (spec §22)."""
    opts: list[dict[str, str]] = []
    for c in (candidates or [])[:3]:
        opts.append({"key": chr(ord("A") + len(opts)),
                     "label": str(c.get("name") or c.get("label") or c.get("id")),
                     "detail": str(c.get("reason") or "按此对象回答")})
    if not opts and pack.get("project"):
        p = pack["project"]
        opts.append({"key": "A", "label": str(p.get("name")),
                     "detail": "按该项目回答"})
    return {
        "needed": True,
        "reason": reason or "该问题涉及多个可能的对象，需要你确认后再回答。",
        "options": opts,
        "question": "请确认你要问的是下面哪一项？",
    }


def should_clarify(intent_info: dict[str, Any],
                   pack: dict[str, Any]) -> bool:
    """Clarification is reserved for answer-affecting ambiguity."""
    if pack.get("status") == "PROJECT_NOT_FOUND":
        return True
    return bool(intent_info.get("needs_clarification"))


# --------------------------------------------------------------------------
# §23/§24/§25 authority guards
# --------------------------------------------------------------------------
def authority_notes(items: list[dict[str, Any]]) -> list[str]:
    """Deterministic warnings the model must respect (and the gate enforces)."""
    notes: list[str] = []
    draft = [i for i in items if i.get("authority") == C.A_DRAFT]
    if draft:
        notes.append(
            "以下内容仍为 draft（未经确认），只能表述为「草稿/待确认」，"
            "不得作为正式事实：%s"
            % "、".join(i["title"][:24] for i in draft[:5]))
    rel = [i for i in items if i["layer"] == "PROJECT_RELATION"]
    disputed = [i for i in rel if i.get("relation_state") == "disputed"]
    if disputed:
        notes.append("以下项目关系证据不足，属「有争议」，不得断言为既成事实：%s"
                     % "、".join(i["title"][:30] for i in disputed[:5]))
    hist = [i for i in items if i.get("temporal_state") not in (None, "current")]
    if hist:
        notes.append("存在历史/已取代内容，仅在用户明确询问历史时才可引用。")
    if not any(i["layer"] == "CONFIRMED_KNOWLEDGE" for i in items):
        notes.append("本项目当前没有已确认（current）知识页；知识类陈述必须标注为草稿。")
    return notes


def metric_facts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The only numbers the answer may state (spec §24)."""
    out = []
    for i in items:
        if i["layer"] != "PROJECT_DATA_METRIC":
            continue
        # R6 fix: keep the full metric identity (period / dimension /
        # validity / source_ref) instead of collapsing to a scalar value.
        out.append({"metric_id": i["item_id"], "name": i["title"],
                    "value": i.get("value"), "unit": i.get("unit"),
                    "observed_at": i.get("observed_at"),
                    "observation_id": i.get("observation_id"),
                    "period": i.get("period"),
                    "dimension": i.get("dimension"),
                    "validity": i.get("validity"),
                    "source_ref": i.get("source_ref")})
    return out


# --------------------------------------------------------------------------
# §18/§19 contract + prompt
# --------------------------------------------------------------------------
def synthesis_required(intent: str) -> bool:
    """True when the answer must integrate + judge, not merely re-list."""
    return intent in SYNTHESIS_INTENTS


def _claim_kind(claim: dict[str, Any]) -> str:
    """Classify one evidence-map claim.

    The model is told to emit ``kind`` explicitly.  When it does not, the kind
    is inferred from the text so an older-style map still validates the same
    way it used to: a sentence that reads like advice is held to the
    advice-derived-from-facts rule instead of the fact rule.
    """
    kind = str(claim.get("kind") or "").strip().lower()
    if kind in CLAIM_KINDS:
        return kind
    txt = str(claim.get("text") or "")
    if _RECOMMENDATION_HINTS_RE.search(txt):
        return "recommendation"
    if _ANALYSIS_HINTS_RE.search(txt):
        return "analysis"
    return "fact"


def build_answer_contract(*, message: str, intent_info: dict[str, Any],
                          pack: dict[str, Any],
                          conv: Optional[Any] = None) -> dict[str, Any]:
    """Assemble the answer contract for one turn."""
    intent = intent_info.get("intent", I.ANALYZE)
    layers = I.authoritative_layers(intent)
    items = [i for i in (pack.get("items") or []) if i["layer"] in layers]
    # Historical material is excluded unless the user explicitly asked about it.
    # DRAFT material stays in the context but carries authority=DRAFT, so the
    # model must label it and the gate rejects presenting it as confirmed.
    if conv is not None and getattr(conv, "temporal_scope", "current") != "historical" \
            and intent != I.HISTORY:
        items = [i for i in items
                 if i.get("temporal_state") not in C.HISTORICAL_TEMPORAL_STATES]

    return {
        "contract": "PAW_AGY_ANSWER_CONTRACT_V2",
        "intent": intent,
        "intent_confidence": intent_info.get("confidence"),
        "advice_allowed": I.advice_allowed(intent),
        "synthesis_required": synthesis_required(intent),
        "recommendation_allowed": I.advice_allowed(intent),
        "plan_mode": I.plan_mode(message) if intent == I.PLAN else None,
        "answer_sections": list(ANSWER_SECTIONS),
        "authoritative_layers": layers,
        "context_items": items,
        "context_summary": C.summarise(items),
        "authority_notes": authority_notes(items),
        "metric_facts": metric_facts(items),
        "evidence_index": [
            {"evidence_id": _eid(i), "layer": i["layer"], "title": i["title"],
             "authority": i.get("authority"),
             "temporal_state": i.get("temporal_state"),
             "is_formal_fact": i.get("is_formal_fact"),
             "source_ref": i.get("source_ref")}
            for i in items],
        "display_rules": {
            "show_evidence_as_link": True,
            "evidence_footer_format": "依据：N 条",
            "no_per_sentence_citations_in_display": True,
            "machine_evidence_map_required": True,
            "technical_details_collapsed": True,
        },
        "clarification": (build_clarification(pack,
                                              reason="无法定位项目或问题存在歧义。")
                          if should_clarify(intent_info, pack) else
                          {"needed": False, "options": [], "question": ""}),
    }


def _eid(item: dict[str, Any]) -> str:
    return "%s:%s" % (item["layer"], item["item_id"])


def _answer_structure(contract: dict[str, Any]) -> str:
    """The section order the model must follow for this intent (§18/§26).

    A PLAN/ANALYZE answer is a piece of work, not an audit report.  The
    structure therefore leads with the judgement and only then presents the
    facts that support it; "uncertainty" stops being a mandatory section.
    """
    intent = contract.get("intent")
    advice = bool(contract["advice_allowed"])
    lines: list[str] = []

    if intent == I.PLAN and contract.get("plan_mode") == "forecast":
        lines = [
            "【回答结构（项目推演，必须按此顺序）】",
            "1. 综合判断 —— 一两句话说明这个项目接下来最可能的走向",
            "2. 当前情况 —— 只列与推演相关的 PAW 事实（进度、节点、待办、已知问题）",
            "3. 情景推演 —— 分「基准情景 / 乐观情景 / 风险情景」三种，"
            "每种写清前提假设、可能的结果和大致时间点；这是推演，不是已发生的事实",
            "4. 关键变量与预警信号 —— 哪些事情的变化会让项目滑向风险情景，"
            "现在可以观察什么信号",
            "5. 应对建议 —— 针对风险情景给出可落地的预防或补救动作，标出优先级（P1/P2/P3）",
            "6. 需要确认的部分 —— 推演依赖、但工作台里没有记录的信息",
            "所有推演内容都要写成「可能 / 预计 / 如果……则……」，不能写成已经确定的结论。",
        ]
    elif intent == I.PLAN:
        lines = [
            "【回答结构（必须按此顺序）】",
            "1. 综合判断 —— 直接回答用户真正的问题，不要先声明「依据不足」",
            "2. 当前情况 —— 只列与问题相关的 PAW 事实",
            "3. 可以继续优化的部分 —— 基于上述事实做综合推理"
            "（这是你的分析，不是新事实）",
            "4. 为什么建议这么做 —— 指出对应的现状 / 草稿 / 尚未闭环的缺口",
            "5. 下一步建议 —— 给出可落地的动作、优先级或推进顺序（如 P1/P2/P3）",
            "6. 需要确认的部分 —— 只有真正存在决策边界时才写",
            "不要机械输出「依据不足 / 不确定处 / 暂无正式事实」把回答写成审计报告。",
        ]
    elif contract.get("synthesis_required"):
        lines = [
            "【回答结构（必须按此顺序）】",
            "1. 综合判断 —— 直接回答用户真正的问题，不要只罗列条目",
            "2. 当前情况 —— 只列与问题相关的 PAW 事实",
            "3. 我的分析 —— 把事实整合、解释、判断（这是分析，不是新事实）",
        ]
        if advice:
            lines.append("4. 建议（用户已明确请求建议/方案）")
        else:
            lines.append("用户本次没有请求建议：不要输出下一步动作、推荐或方案，"
                         "但必须给出你的分析与判断，不要只复述资料。")
        lines.append("最后：需要确认的部分 —— 只有真正存在决策边界时才写")
    else:
        lines = [
            "【回答结构（必须按此顺序）】",
            "1. 结论/答案（先直接回答）",
            "2. 核心事实（支撑结论的关键事实）",
            "3. 不确定处（证据不足或存在冲突时）",
        ]
        if advice:
            lines.append("4. 建议（用户已明确请求建议/方案）")
        else:
            lines.append("4.（不要输出建议、下一步、推荐、方案、「可以考虑」等延伸内容）")

    if contract["clarification"].get("needed"):
        lines.append("另需：需要你确认（见下方澄清选项）")
    return "\n".join(lines)


def answer_structure(contract: dict[str, Any]) -> str:
    """Public alias so the runtime's bounded fallback prompt can reuse it."""
    return _answer_structure(contract)


def render_prompt(contract: dict[str, Any], *, message: str,
                  conv: Optional[Any] = None) -> str:
    """Render the model prompt for this contract."""
    parts: list[str] = []
    intent = contract["intent"]

    parts.append(
        "你是 PAW 工作台助手 Agy。PAW 提供可靠的工作台事实，你负责理解、综合与回答。\n"
        "你的长期认知层是 PAW 已经沉淀的项目结论、项目手册、已确认知识、"
        "项目数据/指标、项目关系、已批准决策、待办、证据与来源版本；原始来源只在最后兜底。\n"
        "你不是「文件清单检索器」，也不是只会复述正式知识的审计机器人："
        "先直接回答用户真正的问题，再给支撑事实，最后才说明不确定处。")

    parts.append("\n" + _answer_structure(contract))

    parts.append("\n【事实与推理边界（核心规则）】\n"
                 "- 所有关于 PAW 当前状态、历史、数字、决定、人员、规则的"
                 "事实陈述必须由提供的 PAW Evidence 支持。\n"
                 "- 不得创造 PAW 中不存在的事实：不得编造指标数值、已确认决策、"
                 "历史、项目状态、人员、薪酬金额或规则。\n"
                 "- 但对于分析、判断、方案、优化、下一步、建议类问题，你必须基于已有 "
                 "Evidence 做综合推理，而不是只复述资料。\n"
                 "- 推理与建议不要求它本身已经作为 PAW 正式事实存在；"
                 "「建议本身尚未存在于 PAW」不是拒绝给出建议的理由。\n"
                 "- 这类内容必须明确表达为「分析 / 判断 / 建议 / 可以考虑」，"
                 "不能表达成「PAW 已确定 / 已确认 / 已决定」。\n"
                 "- 建议应尽可能说明它是基于哪些现状、问题、草稿或事实推导出来的。\n"
                 "- 只有当 Evidence 不充分到无法合理推导时，才说明具体缺少什么。")

    parts.append("\n【权威规则】\n"
                 "- 当前已确认知识 > 草稿；草稿只能表述为「草稿/待确认」，"
                 "永远不是正式事实。\n"
                 "- 指标数值只能来自给定的数据层事实，禁止自行计算或改写数字。\n"
                 "- 项目关系必须区分 current / superseded / historical / disputed。\n"
                 "- 历史内容仅在用户明确询问历史时才可引用。")
    if contract["authority_notes"]:
        parts.append("\n【必须遵守的权威提示】\n- " + "\n- ".join(contract["authority_notes"]))

    if contract["metric_facts"]:
        parts.append("\n【可引用的指标数值（唯一来源）】\n" + json.dumps(
            contract["metric_facts"], ensure_ascii=False, indent=1))

    if contract["clarification"].get("needed"):
        parts.append("\n【需要用户确认】\n%s\n%s" % (
            contract["clarification"]["question"],
            json.dumps(contract["clarification"]["options"], ensure_ascii=False)))

    parts.append("\n【证据引用规则】\n"
                 "用户界面不逐句显示引用编号，但你必须输出机器可读的证据映射，用于审计。\n"
                 "- 事实陈述：kind=fact，用 evidence 指向支撑它的证据 id。\n"
                 "- 分析与建议：kind=analysis 或 kind=recommendation，用 derived_from "
                 "指向支撑该推理的事实证据 id；建议本身不需要（也不可能）"
                 "有对应的已有 Knowledge 条目。\n"
                 "- 严禁把 kind=recommendation 的内容写成 PAW 已确认 / 已决定。\n"
                 "%s\n%s\n%s"
                 % (EVIDENCE_MAP_OPEN,
                    '{"claims":['
                    '{"kind":"fact","text":"...","evidence":["LAYER:item_id"]},'
                    '{"kind":"recommendation","text":"...",'
                    '"derived_from":["LAYER:item_id"]}]}',
                    EVIDENCE_MAP_CLOSE))
    parts.append("可用的证据 id：\n" + "\n".join(
        "- %s  [%s] %s" % (e["evidence_id"], e["authority"], e["title"])
        for e in contract["evidence_index"]))

    parts.append("\n--- PAW 上下文（按权威顺序）---\n"
                 + C.render_context_block(contract["context_items"]))
    if conv is not None:
        parts.append("\n" + conv.prompt_block())
    parts.append("\n--- 用户消息 ---\n%s" % message)
    return "\n".join(parts)


# --------------------------------------------------------------------------
# §19/§20 validation + display
# --------------------------------------------------------------------------
def extract_evidence_map(answer: str) -> tuple[str, Optional[dict]]:
    """Split the machine-only evidence map out of the answer text."""
    text = answer or ""
    m = _EVIDENCE_MAP_RE.search(text)
    if not m:
        return text, None
    visible = (text[:m.start()] + text[m.end():]).strip()
    body = m.group("body").strip()
    try:
        return visible, json.loads(body)
    except (ValueError, TypeError):
        return visible, None


def validate_answer(answer: str, contract: dict[str, Any]) -> tuple[bool, str, dict]:
    """Deterministic grounding gate.

    Runs on the RAW model output (evidence map included) and returns
    ``(ok, reason, detail)``.
    """
    raw = answer or ""
    if not raw.strip():
        return False, "empty_response", {}
    visible, evmap = extract_evidence_map(raw)
    valid_ids = {e["evidence_id"] for e in contract["evidence_index"]}

    # 1. the evidence map must be present whenever there is anything to cite
    if valid_ids and evmap is None:
        return False, "missing_evidence_map", {}
    if evmap is not None and not isinstance(evmap,dict):
        return False,"malformed_evidence_map",{}

    claims = (evmap or {}).get("claims") or []
    if not isinstance(claims, list):
        return False, "malformed_evidence_map", {}
    if any(not isinstance(c,dict) or not isinstance(c.get('text'),str) for c in claims):
        return False, "malformed_claim", {}
    for claim in claims:
        for field in ('evidence','derived_from'):
            refs=claim.get(field) or []
            if not isinstance(refs,list) or any(not isinstance(r,str) for r in refs):
                return False,"malformed_evidence_refs",{}

    # 2. every referenced evidence id must exist (both the fact citation and
    #    the ``derived_from`` citation of a model-derived claim)
    for c in claims:
        for eid in (list(c.get("evidence") or []) + list(c.get("derived_from") or [])):
            if eid not in valid_ids:
                return False, "invalid_evidence_ref:%s" % eid, {"evidence": eid}

    # 3. a PAW-specific FACT claim must be mapped to evidence; a model-derived
    #    ANALYSIS/RECOMMENDATION claim must instead cite the facts it was
    #    derived from, and must never be phrased as an existing PAW decision
    #    (§26 — fact grounding != recommendation pre-existence).
    for c in claims:
        txt = str(c.get("text") or "")
        kind = _claim_kind(c)
        if kind in DERIVED_KINDS:
            derived = [e for e in (c.get("derived_from") or c.get("evidence") or [])]
            if not derived:
                return False, "unbacked_%s" % kind, {"claim": txt[:80]}
            if _asserts_existing_decision(txt):
                return False, "recommendation_as_fact", {"claim": txt[:80]}
        elif valid_ids:
            if contract.get('advice_allowed') and not c.get('evidence'):
                return False,"uncited_fact",{"claim":txt[:80]}
            if _PAW_CLAIM_RE.search(txt) and not (c.get("evidence") or []):
                return False, "uncited_paw_claim", {"claim": txt[:80]}

    # 4. advice must not appear unless the user asked for it
    if not contract["advice_allowed"]:
        # Only USER-DIRECTED recommendations count.  The bare nouns 建议 /
        # 推荐 / 下一步 / 方案 are legitimate inside a factual description of
        # the project's own draft material ("关于变革的具体方案与细则目前仍为
        # 草稿"), so matching them rejected correct, well-grounded answers.
        if re.search(r"(建议你|建议您|我建议|我们建议|建议先|建议优先|"
                     r"下一步建议|推荐你|推荐您|我推荐|不妨|可以考虑|"
                     r"可以尝试|我认为应该|你应该|"
                     r"you should|i recommend|i suggest|we recommend|"
                     r"we suggest)", visible, re.IGNORECASE):
            return False, "unsolicited_advice", {}

    # 5. §24 — a number attached to a metric name must match the data layer
    allowed = {(m["name"], str(m["value"])) for m in contract["metric_facts"]
               if m.get("value") is not None}
    if allowed:
        for m in contract["metric_facts"]:
            if m.get("value") is None:
                continue
            name = re.escape(m["name"])
            for hit in re.finditer(name + r"[^0-9]{0,20}?(\d+(?:\.\d+)?)", visible):
                if hit.group(1) != str(m["value"]):
                    return False, "metric_value_mismatch:%s" % m["name"], {
                        "stated": hit.group(1), "authoritative": str(m["value"])}

    # 6. §23 — draft content must not be stated as a confirmed fact
    draft_titles = [e["title"] for e in contract["evidence_index"]
                    if e["authority"] == C.A_DRAFT]
    if draft_titles and re.search(r"(已确认|正式确认|已经确定|confirmed)", visible):
        if not re.search(r"(草稿|待确认|draft|未确认)", visible):
            return False, "draft_stated_as_confirmed", {}

    kinds = [_claim_kind(c) for c in claims]
    return True, "ok", {
        "claims": len(claims),
        "evidence_index": len(valid_ids),
        "claim_kinds": {k: kinds.count(k) for k in CLAIM_KINDS},
        "synthesis_required": bool(contract.get("synthesis_required")),
        "recommendation_allowed": bool(contract.get("recommendation_allowed")),
    }


def build_evidence_footer(contract: dict[str, Any]) -> dict[str, Any]:
    """The ONLY evidence surface the user sees (spec §19)."""
    idx = contract["evidence_index"]
    return {
        "label": "依据：%d 条" % len(idx),
        "expand_label": "查看依据",
        "items": [{"title": e["title"], "layer": e["layer"],
                   "authority": e["authority"],
                   "temporal_state": e["temporal_state"],
                   "is_formal_fact": e["is_formal_fact"]}
                  for e in idx],
    }


def build_response(*, message: str, intent_info: dict[str, Any],
                   pack: dict[str, Any], answer: str,
                   conv: Optional[Any] = None,
                   synthesis: bool = False) -> dict[str, Any]:
    """Package the final user-facing response (display already de-cited)."""
    contract = build_answer_contract(message=message, intent_info=intent_info,
                                     pack=pack, conv=conv)
    ok, reason, detail = validate_answer(answer, contract)
    visible, evmap = extract_evidence_map(answer)
    if not ok:
        return {
            "contract": contract["contract"],
            "intent": contract["intent"],
            "answer": "",
            "answer_type": "fail_closed",
            "gate": reason,
            "gate_detail": detail,
            "evidence": build_evidence_footer(contract),
            "clarification": contract["clarification"],
        }
    return {
        "contract": contract["contract"],
        "intent": contract["intent"],
        "answer": visible,
        "answer_type": "synthesis" if synthesis else "grounded",
        "gate": reason,
        "gate_detail": detail,
        "evidence": build_evidence_footer(contract),
        "evidence_map": evmap,
        "clarification": contract["clarification"],
        "authority_notes": contract["authority_notes"],
        "metric_facts": contract["metric_facts"],
    }


def answer_contract_spec() -> dict[str, Any]:
    return {
        "version": "PAW_AGY_ANSWER_CONTRACT_V2",
        "answer_sections": list(ANSWER_SECTIONS),
        "display_rules": {
            "evidence_footer_format": "依据：N 条",
            "expand_label": "查看依据",
            "no_per_sentence_citations_in_display": True,
            "machine_evidence_map_required": True,
        },
        "synthesis": {"allowed": True, "tag": "type=synthesis",
                      "never_disguised_as_source_text": True},
        "suggestions": {"only_on_explicit_request": True,
                        "permitted_intents": sorted(I.ADVICE_PERMITTED),
                        "facts_and_advice_separated": True},
        "grounded_synthesis": {
            "fact_grounding": "FACT_STATEMENTS_MUST_BE_EVIDENCE_BACKED",
            "recommendation_pre_existence_required": False,
            "model_derived_analysis_allowed": True,
            "model_derived_recommendation_allowed": True,
            "derived_claims_must_cite_supporting_facts": True,
            "derived_claims_must_not_pose_as_paw_facts": True,
            "synthesis_required_intents": sorted(SYNTHESIS_INTENTS),
            "claim_kinds": list(CLAIM_KINDS),
        },
        "clarification": {"only_when_answer_affecting": True,
                          "max_options": 3, "options_must_be_real": True},
        "knowledge_authority": {"confirmed_over_draft": True,
                                "draft_is_never_formal_fact": True,
                                "historical_only_on_explicit_request": True},
        "metrics": {"numbers_only_from_data_layer": True,
                    "model_may_not_invent_numbers": True},
        "relations": {"states": ["current", "superseded", "historical",
                                 "disputed"]},
        "fail_closed": {"on_zero_evidence": True,
                        "on_missing_evidence_map": True,
                        "on_metric_mismatch": True,
                        "on_unsolicited_advice": True,
                        "on_unbacked_analysis": True,
                        "on_unbacked_recommendation": True,
                        "on_recommendation_as_fact": True},
    }
