"""Verdicts for the real-model acceptance's eight questions (R-264).

Kept free of Windows-only imports so the unit suite can assert the rules on the
Linux unit runner: the acceptance script itself imports the installed-app
harness, which needs Windows.
"""
from __future__ import annotations

import json
import re

# PM-S1-03 accepts the core sentence with other punctuation and without the
# "本地扫描正文样本：" prefix; the source file must still be cited.
DETAIL_CORE_SENTENCE = "青禾试点已完成首轮访谈，下一步整理访谈纪要"


def _compact(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text or "")


def core_sentence_in(text: str) -> bool:
    """True when the answer carries the core sentence, ignoring punctuation."""
    return _compact(DETAIL_CORE_SENTENCE) in _compact(text)


def judge_answer(ident: str, text: str, sources: list, exam_questions: dict):
    """Verdict for one question. R-264 changes PM-S1-03 only and adds the marker check."""
    from backend.pm import internal_markers
    from scripts.build_exam_corpus import evaluate_semantics

    markers = internal_markers.found(text)
    if markers:
        # A visible internal marker is a failure of its own, for every question.
        return False, "internal_marker_visible:" + ",".join(markers)
    sources_json = json.dumps(sources or [], ensure_ascii=False)
    if ident == "PM-U1-03":
        passed = "18" in text and "青禾试点计划_v2.docx" in sources_json
        return passed, "current_target_and_source" if passed else "target_or_source_missing"
    if ident == "PM-S1-03":
        core = core_sentence_in(text)
        cited = "青禾试点详情.md" in sources_json
        if core and cited:
            return True, "core_sentence_and_source"
        return False, "source_missing" if core else "core_sentence_missing"
    if ident in {"PM-U4-01", "X19", "X20"} and ident not in exam_questions:
        passed = "建议" in text and bool(sources)
        return passed, "advice_and_sources" if passed else "advice_or_sources_missing"
    passed, reason = evaluate_semantics(text, sources or [], exam_questions[ident])
    return bool(passed), str(reason or "")
