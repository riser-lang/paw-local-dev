"""The extractive stand-in must read the request's document, not a script.

The scripted analysis returns the same four candidates whatever it reads, which
early cases depend on.  The exam needs the other mode: every candidate must be
derived from the document text in the request, and nothing may be invented.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.mock_extractive_analysis import analyse  # noqa: E402


def request(doc_name: str, lines, projects):
    blocks = "\n".join(lines)
    return (f"文档名称: {doc_name}\n已提取文本块样本:\n[0] {blocks}\n\n"
            f"现有活跃项目（完整列表，共 {len(projects)} 个）: {projects}\n"
            "项目归属规则（必须严格遵守）：\n")


def test_summary_comes_from_the_document_and_names_an_existing_project():
    text = request("记要.docx", ["甲项目。", "进度与里程碑：样机已完成。"], ["甲项目", "乙项目"])
    out = analyse(text)
    updates = out["project_update_candidates"]
    assert len(updates) == 1
    payload = updates[0]["proposed_change"]
    assert payload["project_name"] == "甲项目"
    assert payload["new_summary"] == "进度与里程碑：样机已完成"


def test_nothing_is_invented_when_the_document_names_no_existing_project():
    text = request("无关记录.docx", ["一些与本项目无关的叙述。"], ["甲项目"])
    out = analyse(text)
    assert out["project_update_candidates"] == []
    assert out["todo_candidates"] == []
    assert out["candidates"] == []


def test_pending_action_becomes_a_todo_but_finished_work_does_not():
    text = request("甲项目_记录.docx",
                   ["甲项目。", "已完成任务清点。", "需要整理复核记录。"], ["甲项目"])
    todos = analyse(text)["todo_candidates"]
    assert len(todos) == 1
    assert "整理" in todos[0]["proposed_change"]["title"]
    assert "已完成任务" not in todos[0]["proposed_change"]["title"]


def test_project_is_taken_from_the_body_before_the_title():
    text = request("共用件.docx", ["乙项目。", "乙项目已完成交付。"], ["甲项目", "乙项目"])
    payload = analyse(text)["project_update_candidates"][0]["proposed_change"]
    assert payload["project_name"] == "乙项目"


def test_status_section_contract_body_is_read_as_document_text():
    """The section-update contract carries the text in its JSON payload."""
    text = ('语义任务契约：section_update\n'
            '{"source_title":"甲项目_方案.docx","body":"甲项目。\\n目标：完成评审。",'
            '"sections":[]}')
    statements = [f["statement"] for f in analyse(text)["facts"]]
    assert any("完成评审" in s for s in statements)
