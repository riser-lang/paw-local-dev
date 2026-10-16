"""The bounded Web prompt must not throw away the evidence that answers it.

A global-scope turn carries every project's records.  Uniform truncation made
the two or three items that actually answer the question exactly as short as
the dozens that do not, so a question whose figure sat mid-paragraph was
answered with a fail-closed refusal while the context clearly held the figure.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from brain.runtime import agy_v2_runtime as R  # noqa: E402


def test_ranking_prefers_the_item_that_shares_the_question():
    items = [
        {"title": "无关记录", "content": "本周推进核心模块执行与云端测试验证"},
        {"title": "预算记录", "content": "年度预算总额 1200 万元，其中培训预算 180 万元"},
    ]
    ranked = R._rank_items_by_message(items, "培训预算有多少？")
    assert ranked[1]["_rank"] > ranked[0]["_rank"]


def test_relevant_excerpt_keeps_the_sentence_that_answers():
    text = "第一句：与本问题无关。第二句：本年度培训预算为 180 万元。第三句：无关。"
    excerpt = R._relevant_excerpt(text, "培训预算有多少？", 40)
    assert "180" in excerpt


def test_relevant_excerpt_without_overlap_is_a_prefix():
    text = "无关内容" * 40
    assert R._relevant_excerpt(text, "完全不同的问法", 10) == text[:10]


def _contract(items, message):
    """A well-formed V2 contract built by the product's own builder."""
    from brain.context import agy_answer_contract as A
    from brain.context import agy_intent_router as I
    layer = items[0]["layer"] if items else "CONFIRMED_KNOWLEDGE"
    intent = "FACT_LOOKUP"
    pack = {
        "items": items,
        "evidence_index": [
            {"evidence_id": "%s:%s" % (it["layer"], it["item_id"]),
             "authority": "EVIDENCE", "title": it["title"]}
            for it in items],
    }
    return A.build_answer_contract(
        message=message,
        intent_info={"intent": intent, "confidence": "HIGH",
                     "layers": I.authoritative_layers(intent)},
        pack=pack,
    )


def test_bounded_prompt_keeps_evidence_ids_and_fits_the_boundary():
    """A large pack must still be rendered, with every id retained."""
    items = [
        {"layer": "CONFIRMED_KNOWLEDGE", "item_id": "k%d" % i,
         "title": "记录 %d 培训预算" % i,
         "content": "填充内容" * 60, "is_formal_fact": False,
         "temporal_state": "current", "authority": "EVIDENCE"}
        for i in range(40)
    ]
    prompt = R._render_bounded_prompt(_contract(items, "培训预算有多少？"),
                                      "培训预算有多少？", None)
    assert len(prompt) <= R.MAX_WEB_PROMPT_CHARS
    for i in range(40):
        assert "CONFIRMED_KNOWLEDGE:k%d" % i in prompt
