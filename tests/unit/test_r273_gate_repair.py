"""The evidence gate keeps its verdict; the model gets a better chance first.

Measured on the real-model runs, answers were refused for two mechanical
reasons: the machine block was missing, and recommendation claims were phrased
as an existing decision ("已确定/已确认").  These tests pin the changes that
address that without weakening the gate:

* the format, the tagging rule and the "don't state an unconfirmed decision"
  rule now sit at the top of the prompt with worked examples;
* a block-only failure is repaired by regenerating the block for the prose
  already written, and the merged answer must still pass the same gate;
* a sentence-level failure is not repaired that way -- it needs new prose, so it
  gets the full retry, which now quotes the whole offending sentence.
"""
from __future__ import annotations

import json

import pytest

from backend.brain.context import agy_answer_contract as contract_mod
from backend.brain.api import local_agy_facade as facade


def _contract():
    return {
        "intent": "analyze",
        "advice_allowed": True,
        "synthesis_required": False,
        "evidence_index": [
            {"evidence_id": "CONFIRMED_KNOWLEDGE:k1", "authority": "CONFIRMED", "title": "访谈纪要"},
            {"evidence_id": "EVIDENCE:e1", "authority": "EVIDENCE", "title": "周报"},
        ],
        "context_items": [],
        "authority_notes": [],
        "metric_facts": [],
        "clarification": {"needed": False},
    }


def _answer(claims, prose="正文。"):
    block = {"claims": claims}
    return prose + "<<<EVIDENCE_MAP " + json.dumps(block, ensure_ascii=False) + " EVIDENCE_MAP>>>"


def test_the_format_and_tagging_rules_are_the_first_thing_in_the_prompt():
    header = contract_mod.answer_format_header(_contract())
    assert contract_mod.EVIDENCE_MAP_OPEN in header
    assert "kind=fact" in header and "kind=recommendation" in header
    # Both failing shapes get a worked example.
    assert "把 P2 的访谈纪要作为校准依据" in header
    assert "已确定" in header and "还没有定" in header
    assert len(header) < 1400, "the header has to stay cheap enough to send every turn"


def test_render_prompt_puts_the_header_before_the_detail_rules():
    prompt = contract_mod.render_prompt(_contract(), message="进展如何？")
    assert prompt.index("先看这两条") < prompt.index("证据引用规则")


@pytest.mark.parametrize("reason", [
    "missing_evidence_map", "malformed_evidence_map", "invalid_evidence_ref:EVIDENCE:zzz",
    "unbacked_analysis", "unbacked_recommendation", "uncited_paw_claim",
])
def test_block_level_failures_are_repaired_without_rewriting_the_prose(reason):
    assert facade._is_map_level_reason(reason) is True


@pytest.mark.parametrize("reason", [
    "recommendation_as_fact", "unsolicited_advice", "draft_stated_as_confirmed",
    "empty_response", "metric_value_mismatch:实施方案 v",
])
def test_sentence_level_failures_need_new_prose(reason):
    # A repaired block cannot fix a sentence that says "已确定": quoting it
    # verbatim would fail again, so these must get the full retry.
    assert facade._is_map_level_reason(reason) is False


def test_repair_prompt_carries_the_prose_the_claim_and_the_valid_ids():
    prompt = contract_mod.build_map_repair_prompt(
        _contract(), "P1 已完成 3 场访谈。", "missing_evidence_map", {})
    assert "P1 已完成 3 场访谈。" in prompt
    assert "CONFIRMED_KNOWLEDGE:k1" in prompt
    assert "不要改动" in prompt and "也不要重新回答" in prompt


def test_merged_answer_must_pass_the_same_gate():
    prose = "P1 已完成 3 场访谈。"
    repaired = ('<<<EVIDENCE_MAP {"claims":[{"kind":"fact","text":"P1 已完成 3 场访谈。",'
                '"evidence":["CONFIRMED_KNOWLEDGE:k1"]}]} EVIDENCE_MAP>>>')
    merged = contract_mod.merge_repaired_map(prose, repaired)
    assert merged.startswith("P1 已完成 3 场访谈。")
    ok, reason, _ = contract_mod.validate_answer(merged, _contract())
    assert ok, reason


def test_a_repaired_block_with_a_bad_reference_is_still_refused():
    prose = "P1 已完成 3 场访谈。"
    bad = ('<<<EVIDENCE_MAP {"claims":[{"kind":"fact","text":"P1 已完成 3 场访谈。",'
           '"evidence":["CONFIRMED_KNOWLEDGE:missing"]}]} EVIDENCE_MAP>>>')
    merged = contract_mod.merge_repaired_map(prose, bad)
    ok, reason, _ = contract_mod.validate_answer(merged, _contract())
    assert ok is False and reason.startswith("invalid_evidence_ref")


def test_a_repair_without_a_block_is_not_glued_onto_the_answer():
    assert contract_mod.merge_repaired_map("正文。", "只有一句话，没有机器块") is None


def test_a_bare_json_repair_is_accepted_and_still_validated():
    prose = "P1 已完成 3 场访谈。"
    bare = '{"claims":[{"kind":"fact","text":"P1 已完成 3 场访谈。","evidence":["CONFIRMED_KNOWLEDGE:k1"]}]}'
    merged = contract_mod.merge_repaired_map(prose, bare)
    assert merged is not None and contract_mod.EVIDENCE_MAP_OPEN in merged
    ok, reason, _ = contract_mod.validate_answer(merged, _contract())
    assert ok, reason


def test_an_unclosed_marker_in_the_prose_does_not_swallow_the_repaired_block():
    """The real PM-U4-01 shape: `<<<EVIDENCE_MAP` with no closing marker."""
    prose = "## 1. 综合判断\n\n建议先把口径统一。<<<EVIDENCE_MAP {\"claims\":["
    repaired = ('<<<EVIDENCE_MAP {"claims":[{"kind":"recommendation","text":"建议先把口径统一。",'
                '"derived_from":["CONFIRMED_KNOWLEDGE:k1"]}]} EVIDENCE_MAP>>>')
    merged = contract_mod.merge_repaired_map(prose, repaired)
    assert merged is not None
    assert merged.count("<" + "<<" + "EVIDENCE_MAP") == 1, "the stray opening marker must be removed"
    ok, reason, _ = contract_mod.validate_answer(merged, _contract())
    assert ok, reason


def test_the_header_asks_for_splitting_a_mixed_sentence():
    header = contract_mod.answer_format_header(_contract())
    assert "拆成两条" in header


def test_retry_text_shows_the_whole_offending_sentence_and_what_to_write_instead():
    claim = "建议把 P2 阶段的访谈纪要作为下一步校准依据，并在 2026-10-25 前完成校准"
    suffix = facade._build_retry_suffix("recommendation_as_fact", {"claim": claim}, _contract())
    assert claim in suffix, "a truncated prefix hides what the model got wrong"
    assert "已确认" in suffix or "既成事实" in suffix or "已确定" in suffix


def test_a_confirmed_decision_written_as_recommendation_is_refused():
    # The shape the real-model run actually produced.
    refused = _answer([{"kind": "recommendation", "text": "P2 阶段的校准范围已确定，基准是 2026-10-25。",
                        "derived_from": ["CONFIRMED_KNOWLEDGE:k1"]}])
    ok, reason, detail = contract_mod.validate_answer(refused, _contract())
    assert ok is False and reason == "recommendation_as_fact"
    assert "已确定" in detail["claim"]

    # The same advice phrased as a proposal passes.
    allowed = _answer([{"kind": "recommendation", "text": "建议把校准基准定在 2026-10-25，目前尚未确定。",
                        "derived_from": ["CONFIRMED_KNOWLEDGE:k1"]}])
    ok, reason, _ = contract_mod.validate_answer(allowed, _contract())
    assert ok, reason


def test_an_action_sentence_tagged_as_fact_still_passes_as_a_plain_fact():
    # The gate does not forbid fact-tagged prose by itself; it forbids a claim
    # without a citation and a recommendation that asserts an existing decision.
    ok, reason, _ = contract_mod.validate_answer(
        _answer([{"kind": "fact", "text": "P1 已完成 3 场访谈。",
                  "evidence": ["CONFIRMED_KNOWLEDGE:k1"]}]), _contract())
    assert ok, reason
    missing_citation = _answer([{"kind": "fact", "text": "P1 已完成 3 场访谈。"}])
    ok, reason, _ = contract_mod.validate_answer(missing_citation, _contract())
    assert ok is False and reason in ("uncited_fact", "uncited_paw_claim")
