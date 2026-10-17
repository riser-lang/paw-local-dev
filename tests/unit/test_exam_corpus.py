import json
from pathlib import Path
import pytest
from scripts.build_exam_corpus import build_corpus, load_corpus_spec, evaluate_semantics

try:
    import docx
    import openpyxl
    import pptx
    HAS_OFFICE_LIBS = True
except ImportError:
    HAS_OFFICE_LIBS = False


@pytest.mark.skipif(not HAS_OFFICE_LIBS, reason="Requires python-docx, openpyxl, python-pptx")
def test_build_exam_corpus(tmp_path):
    out_dir = tmp_path / "exam_docs"
    docs = load_corpus_spec()
    assert len(docs) == 8

    generated = build_corpus(output_dir=out_dir)
    assert len(generated) == len(docs)

    # Check each file exists and is non-empty
    for doc in docs:
        file_path = out_dir / doc["file"]
        assert file_path.exists()
        assert file_path.stat().st_size > 0


def test_evaluate_semantics_includes_excludes():
    spec = {
        "must_include": ["60人", "40人"],
        "must_not_include": ["30人"]
    }
    # Pass case
    res, reason = evaluate_semantics("团队现阶段有60人全职与40人兼职", [], spec)
    assert res is True

    # Fail includes
    res, reason = evaluate_semantics("团队现阶段有60人全职", [], spec)
    assert res is False
    assert "missing" in reason

    # Fail excludes
    res, reason = evaluate_semantics("团队现阶段有60人全职与40人兼职，原定30人", [], spec)
    assert res is False
    assert "forbidden" in reason


def test_evaluate_semantics_citation_rules():
    spec = {
        "cite_file": "方案_v2.docx",
        "must_not_cite_file": "方案_v1.docx"
    }
    # Pass
    res, reason = evaluate_semantics("正文内容", ["方案_v2.docx"], spec)
    assert res is True

    # Fail cite_file
    res, reason = evaluate_semantics("正文内容", ["方案_v3.docx"], spec)
    assert res is False
    assert "missing" in reason

    # Fail must_not_cite_file
    res, reason = evaluate_semantics("正文内容", ["方案_v2.docx", "方案_v1.docx"], spec)
    assert res is False
    assert "present" in reason


def test_evaluate_semantics_conflict_and_suggestion():
    spec_conflict = {
        "conflict_notice": ["60%", "45%"]
    }
    res, reason = evaluate_semantics("两份报告存在冲突：分别是60%和45%", [], spec_conflict)
    assert res is True
    res, reason = evaluate_semantics("只有60%", [], spec_conflict)
    assert res is False

    spec_suggestion = {
        "suggestion_label": True
    }
    res, reason = evaluate_semantics("【建议】进一步优化流程", [], spec_suggestion)
    assert res is True
    res, reason = evaluate_semantics("无法回答", [], spec_suggestion)
    assert res is False
