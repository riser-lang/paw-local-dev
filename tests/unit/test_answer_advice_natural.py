from backend.pm import answer_advice


def test_plain_advice_is_grounded_to_retrieved_risk_and_source():
    source_id = "SOURCE:weekly"
    contract = {
        "advice_allowed": True,
        "context_items": [{
            "layer": "SOURCE", "item_id": "weekly", "title": "项目周报",
            "content": "风险：两个校区尚未提名导师人选。下一步：完成剩余导师招募。",
        }],
        "evidence_index": [{"evidence_id": source_id, "title": "项目周报"}],
    }

    cleaned, metadata = answer_advice.clean(
        "已发现两个校区尚未提名导师人选。建议先核对校区提名进度，再跟进导师招募。",
        contract,
    )

    assert metadata["status"] == "natural_language_grounded"
    assert "建议" in cleaned
    visible, mapping = answer_advice.extract(cleaned)
    assert mapping and mapping["claims"]
    assert any(claim.get("derived_from") == [source_id] for claim in mapping["claims"])
    assert "〔依据：项目周报〕" in visible


def test_plain_advice_without_retrieved_evidence_stays_unverified():
    cleaned, metadata = answer_advice.clean(
        "建议扩大团队并增加预算。",
        {"advice_allowed": True, "context_items": [], "evidence_index": []},
    )

    assert cleaned == "建议扩大团队并增加预算。"
    assert metadata["status"] == "unverified_map"
