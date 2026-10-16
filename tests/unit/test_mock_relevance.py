from scripts.mock_grounded_answer import answer


def prompt(question):return '''可用的证据 id：
- PROJECT_HANDBOOK:one [FORMAL] 研发记录
--- PAW 上下文 V2（按权威顺序）---
## PROJECT_HANDBOOK
- [one] 研发记录
进度与里程碑：样机已完成。下一步：整理复核记录。
--- 用户消息 ---
'''+question


def test_unknown_topic_does_not_turn_unrelated_excerpts_into_answer():
    assert answer(prompt('虚构项目的采购预算总额是多少？')).startswith('依据不足')


def test_progress_synonyms_and_advice_are_generic_and_supported():
    assert '样机已完成' in answer(prompt('虚构项目目前做到哪里？'))
    assert '建议：' in answer(prompt('虚构项目后续最好如何优化？'))


def test_consistency_uses_configured_section_labels_within_version_excerpts():
    from scripts.mock_grounded_answer import section_consistency
    body='新版原文节选：虚构项目。\n当前进度：交付状态为已完成。\n风险记录：交付状态为尚未完成。'
    output=section_consistency({'sections':[{'section_id':'one','title':'当前进度','text':body},
        {'section_id':'two','title':'风险记录','text':body}]})
    assert output=={'findings':[{'left_section':'one','right_section':'two','left_quote':'交付状态为已完成','right_quote':'交付状态为尚未完成'}]}
