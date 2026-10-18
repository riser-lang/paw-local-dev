from backend.brain.review.review_presentation_service import _user_facing_review_title


def test_english_legacy_review_title_uses_candidate_type_label():
    assert _user_facing_review_title("project_update", "project update") == "项目更新待确认事项"


def test_chinese_review_title_is_preserved():
    assert _user_facing_review_title("project_update", "青禾项目指标更新") == "青禾项目指标更新"
