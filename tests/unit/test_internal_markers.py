"""R-264: internal marker codes never reach the visible answer."""
import json

from backend.pm import internal_markers as markers


def test_marker_with_id_becomes_its_chinese_label():
    raw = "该条目被标注为「资料证据」（CONFIRMED_KNOWLEDGE:pm-source-dec5cdc0）。"
    cleaned = markers.strip(raw)
    assert "CONFIRMED_KNOWLEDGE" not in cleaned
    assert "已确认知识" in cleaned
    assert markers.found(cleaned) == []


def test_evidence_map_block_is_removed_entirely():
    raw = '可见回答。\n<<<EVIDENCE_MAP {"claims":[{"text":"x"}]} EVIDENCE_MAP>>>'
    cleaned = markers.strip(raw)
    assert cleaned == "可见回答。"
    assert "EVIDENCE_MAP" not in cleaned


def test_stray_evidence_map_tokens_are_removed():
    cleaned = markers.strip("前缀 <<<EVIDENCE_MAP 中缀 EVIDENCE_MAP>>> 后缀")
    assert "EVIDENCE_MAP" not in cleaned
    assert "前缀" in cleaned and "后缀" in cleaned


def test_layer_codes_with_colon_are_replaced():
    cleaned = markers.strip("依据 PROJECT_OUTCOME:123 与 SOURCE:E-MSG-001 与 TODO:开个会")
    for code in ("PROJECT_OUTCOME", "SOURCE", "TODO"):
        assert code not in cleaned
    assert "项目结论" in cleaned and "资料" in cleaned and "待办" in cleaned


def test_bare_prose_words_are_not_rewritten():
    raw = "英文里 TODO 是常用词，EVIDENCE 也是。"
    assert markers.strip(raw) == raw


def test_unknown_upper_code_is_left_alone():
    raw = "内部码 UNKNOWN_CODE:123 不在表中。"
    assert markers.strip(raw) == raw


def test_found_lists_remaining_codes_and_accepts_clean_text():
    assert markers.contains("回答（CONFIRMED_KNOWLEDGE:pm-source-a）")
    assert markers.found("回答（CONFIRMED_KNOWLEDGE:pm-source-a）") == ["CONFIRMED_KNOWLEDGE"]
    assert not markers.contains("干净的回答。")


def test_table_maps_every_code_to_chinese():
    table = markers.table()
    assert table and all(code and label for code, label in table)
    labels = dict(table)
    assert labels["CONFIRMED_KNOWLEDGE"] == "已确认知识"
    assert labels["EVIDENCE_MAP"] == "证据映射"
    dumped = json.dumps(labels, ensure_ascii=False)
    assert "CONFIRMED_KNOWLEDGE" in dumped
