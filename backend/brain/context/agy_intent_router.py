"""PAW Agy Intent Router V2 (spec §17).

Deterministic classification of a user message into one of nine intents.  The
intent decides the answer shape and which context layers are authoritative —
it never decides the answer itself.

    FACT_LOOKUP    a specific fact / value / definition
    CURRENT_STATUS "现在怎么样" / 进度 / 当前状态
    WHY            causal explanation of a decision or outcome
    COMPARE        two or more things side by side
    ANALYZE        analysis over existing evidence
    PLAN           planning / next steps (explicit request only)
    SOURCE_TRACE   where does this come from / 出处 / 依据
    HISTORY        explicitly historical ("以前"、"上一版")
    AMBIGUOUS      cannot be determined -> ask for clarification

Pure module: no I/O, no writes, no LLM.
"""
from __future__ import annotations

import re
from typing import Any

FACT_LOOKUP = "FACT_LOOKUP"
CURRENT_STATUS = "CURRENT_STATUS"
WHY = "WHY"
COMPARE = "COMPARE"
ANALYZE = "ANALYZE"
PLAN = "PLAN"
SOURCE_TRACE = "SOURCE_TRACE"
HISTORY = "HISTORY"
AMBIGUOUS = "AMBIGUOUS"

INTENTS = (FACT_LOOKUP, CURRENT_STATUS, WHY, COMPARE, ANALYZE, PLAN,
           SOURCE_TRACE, HISTORY, AMBIGUOUS)

#: intents that permit suggestions/advice in the answer (spec §21)
ADVICE_PERMITTED = frozenset({PLAN})

#: intents whose authoritative layers are the formal ones
FORMAL_LAYER_INTENTS = frozenset({FACT_LOOKUP, CURRENT_STATUS, WHY, COMPARE,
                                  ANALYZE})

_PATTERNS: tuple[tuple[str, str], ...] = (
    (SOURCE_TRACE, r"(出处|来源|依据|证据|谁说的|哪里来|source|evidence|"
                   r"reference|provenance|trace)"),
    (HISTORY, r"(历史|以前|之前|曾经|上一版|旧版|更早|过去|superseded|"
              r"previous|earlier|history|used to)"),
    (COMPARE, r"(对比|比较|相比|差异|区别|哪个更好|vs\.?|versus|compare|"
              r"difference)"),
    (WHY, r"(为什么|为何|原因|怎么导致|why|what caused|reason for)"),
    (PLAN, r"(计划|规划|下一步|怎么办|建议|方案|怎么做|如何推进|"
           r"优化|改进|改善|提升|完善|痛点|不足之处|短板|"
           r"推演|预测|预判|展望|走向|会怎样|会怎么样|如果.{0,12}(会|怎么|怎样)|假如|"
           r"风险|情景|后续(发展|走势|会)|可能出现|"
           r"可以做什么|能做什么|有什么可以|有哪些可以|"
           r"plan|next step|recommend|suggest|propose|optimi[sz]e|"
           r"improve|enhance)"),
    (ANALYZE, r"(分析|评估|判断|总结|归纳|趋势|analy[sz]e|assess|evaluate|"
              r"summar(y|ise|ize)|trend)"),
    (CURRENT_STATUS, r"(现在|目前|当前|进展|进度|状态|到什么程度|latest|"
                     r"current status|progress|where are we)"),
    (FACT_LOOKUP, r"(是什么|是多少|多少|哪一[个条]|谁|定义|口径|数值|"
                  r"what is|how many|who|which|definition|value)"),
)

#: a message that is only a pronoun / too short to be self-contained
_TOO_SHORT_RE = re.compile(r"^[\s。，,.!?！？、]*(它|他|她|这个|那个|此|该|"
                           r"这|那|然后呢|继续|嗯|哦)"
                           r"(?:呢|吗|吧|了|啊|嘛|的)?"
                           r"[\s。，,.!?！？、]*$")


def _s(v: Any) -> str:
    return "" if v is None else str(v)


def classify_intent(message: str, *,
                    has_active_project: bool = False,
                    has_context: bool = False) -> dict[str, Any]:
    """Classify one user message.

    Returns ``{intent, confidence, matched_pattern, needs_clarification,
    requires_advice_permission}``.
    """
    msg = _s(message).strip()
    if not msg:
        return {"intent": AMBIGUOUS, "confidence": "LOW",
                "matched_pattern": "empty_message",
                "needs_clarification": True,
                "requires_advice_permission": True}

    if _TOO_SHORT_RE.match(msg) and not has_active_project and not has_context:
        return {"intent": AMBIGUOUS, "confidence": "LOW",
                "matched_pattern": "pronoun_without_antecedent",
                "needs_clarification": True,
                "requires_advice_permission": True}

    for intent, pattern in _PATTERNS:
        if re.search(pattern, msg, re.IGNORECASE):
            return {"intent": intent, "confidence": "HIGH",
                    "matched_pattern": pattern,
                    "needs_clarification": False,
                    "requires_advice_permission": intent not in ADVICE_PERMITTED}

    # a short self-contained noun phrase is a fact lookup
    if len(msg) <= 30:
        return {"intent": FACT_LOOKUP, "confidence": "MEDIUM",
                "matched_pattern": "short_noun_phrase",
                "needs_clarification": False,
                "requires_advice_permission": True}

    return {"intent": ANALYZE, "confidence": "LOW",
            "matched_pattern": "default_analyze",
            "needs_clarification": False,
            "requires_advice_permission": True}


def authoritative_layers(intent: str) -> list[str]:
    """Which context layers may be used as the basis for this intent."""
    if intent == HISTORY:
        return ["HISTORICAL_SUPERSEDED", "SOURCE_VERSION", "RAW_SOURCE",
                "EVIDENCE_UNIT"]
    if intent == SOURCE_TRACE:
        return ["EVIDENCE_UNIT", "SOURCE_VERSION", "RAW_SOURCE"]
    if intent == PLAN:
        return ["PROJECT_OUTCOME", "PROJECT_HANDBOOK", "CONFIRMED_KNOWLEDGE",
                "PROJECT_DATA_METRIC", "PROJECT_RELATION", "APPROVED_DECISION",
                "TODO"]
    return ["PROJECT_OUTCOME", "PROJECT_HANDBOOK", "CONFIRMED_KNOWLEDGE",
            "PROJECT_DATA_METRIC", "PROJECT_RELATION", "APPROVED_DECISION",
            "TODO", "EVIDENCE_UNIT", "SOURCE_VERSION"]


_FORECAST_RE = re.compile(r"(推演|预测|预判|展望|走向|会怎样|会怎么样|如果.{0,12}(会|怎么|怎样)|假如|"
                          r"风险|情景|后续(发展|走势|会)|可能出现|forecast|scenario|what if|risk)",
                          re.IGNORECASE)


def plan_mode(message: str) -> str:
    """Within PLAN: "forecast" for scenario / risk projection, else "plan" (optimise / next steps)."""
    return "forecast" if _FORECAST_RE.search(_s(message)) else "plan"


def advice_allowed(intent: str) -> bool:
    """Suggestions only on an explicit planning/advice request (spec §21)."""
    return intent in ADVICE_PERMITTED


def intent_spec() -> dict[str, Any]:
    return {
        "version": "AGY_INTENT_ROUTER_V2",
        "intents": list(INTENTS),
        "advice_permitted_intents": sorted(ADVICE_PERMITTED),
        "clarification_only_when": [
            "intent == AMBIGUOUS",
            "the ambiguity changes which project/entity the answer is about",
        ],
        "default_intent": ANALYZE,
    }
