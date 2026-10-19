"""R-264: only PM-S1-03's rule changed, plus the internal-marker check."""
from tests.acceptance_verdicts import judge_answer

EXAM = {
    "X01": {
        "id": "X01",
        "q": "星火导师计划第一期要培养多少名导师？",
        "must_include": ["40"],
        "must_not_include": ["30 名", "30名"],
        "cite_file": "星火导师计划_方案_v2.docx",
        "must_not_cite_file": "星火导师计划_方案_v1.docx",
    },
}

CORE = "青禾试点已完成首轮访谈，下一步整理访谈纪要"


def test_pm_s1_03_passes_on_the_core_sentence_without_the_prefix():
    answer = f"结论：{CORE}。"
    passed, reason = judge_answer("PM-S1-03", answer, ["青禾试点详情.md（资料证据，非正式结论）"], {})
    assert passed is True
    assert reason == "core_sentence_and_source"


def test_pm_s1_03_ignores_punctuation_changes():
    answer = "文件里写着：本地扫描正文样本：青禾试点已完成首轮访谈 下一步整理访谈纪要"
    passed, _ = judge_answer("PM-S1-03", answer, ["青禾试点详情.md"], {})
    assert passed is True


def test_pm_s1_03_still_requires_the_source():
    passed, reason = judge_answer("PM-S1-03", f"{CORE}。", ["青禾试点周报.pptx"], {})
    assert passed is False
    assert reason == "source_missing"


def test_pm_s1_03_fails_without_the_core_sentence():
    passed, reason = judge_answer("PM-S1-03", "青禾试点已经招募了 9 名导师。", ["青禾试点详情.md"], {})
    assert passed is False
    assert reason == "core_sentence_missing"


def test_visible_internal_marker_fails_any_question():
    answer = f"该条目被标注为资料证据（CONFIRMED_KNOWLEDGE:pm-source-dec5cdc0）。{CORE}。"
    passed, reason = judge_answer("PM-S1-03", answer, ["青禾试点详情.md"], {})
    assert passed is False
    assert reason.startswith("internal_marker_visible:")
    assert "CONFIRMED_KNOWLEDGE" in reason


def test_evidence_map_block_fails_the_marker_check():
    answer = '回答正文\n<<<EVIDENCE_MAP {"claims":[]} EVIDENCE_MAP>>>'
    passed, reason = judge_answer("X01", answer, ["星火导师计划_方案_v2.docx"], EXAM)
    assert passed is False
    assert reason.startswith("internal_marker_visible:")


def test_other_questions_keep_their_existing_verdict():
    passed, _ = judge_answer("X01", "第一期培养 40 名导师。", ["星火导师计划_方案_v2.docx"], EXAM)
    assert passed is True
    failed, _ = judge_answer("X01", "第一期培养 30 名导师。", ["星火导师计划_方案_v1.docx"], EXAM)
    assert failed is False
