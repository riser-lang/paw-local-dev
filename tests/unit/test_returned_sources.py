from backend.brain.api.local_agy_facade import _include_mentioned_evidence_files


def _current_evidence():
    return (
        [{"evidence_id": "SOURCE:s1"}],
        [{"layer": "SOURCE", "item_id": "s1", "title": "详情资料",
          "provenance": ["青禾试点详情.md(None)"]}],
    )


def test_adds_a_current_evidence_file_named_in_accepted_prose():
    evidence_index, context_items = _current_evidence()

    result = _include_mentioned_evidence_files(
        ["详情资料"], "回答正文提到了《青禾试点详情.md》。",
        evidence_index, context_items, grounded=True,
    )

    assert result == ["详情资料", "青禾试点详情.md"]


def test_does_not_add_a_filename_outside_this_turns_evidence():
    evidence_index, context_items = _current_evidence()

    result = _include_mentioned_evidence_files(
        ["详情资料"], "回答正文还提到另一份未提供的方案.docx。",
        evidence_index, context_items, grounded=True,
    )

    assert result == ["详情资料"]


def test_refusal_leaves_the_existing_source_list_unchanged():
    evidence_index, context_items = _current_evidence()
    existing = ["已有来源"]

    result = _include_mentioned_evidence_files(
        existing, "拒答正文仍提到了青禾试点详情.md。",
        evidence_index, context_items, grounded=False,
    )

    assert result == existing
