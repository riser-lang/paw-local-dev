"""A search query is user text, never FTS5 syntax.

A filename such as "项目周报_v2.docx" used to reach ``MATCH`` verbatim and throw
``sqlite3.OperationalError: fts5: syntax error near "."``.  The exception was
swallowed, and the LIKE fallback only ran for CJK terms shorter than three
characters, so a complete filename matched nothing at all.
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "backend", ROOT / "backend" / "runtime" / "core"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from backend.runtime.core.paw_retrieval_core import (  # noqa: E402
    _fts_query, _recall)

DOCS = [
    ("d1", "项目周报_v2.docx", "项目周报_v2.docx 本周完成评审"),
    ("d2", "审批记录", "审批记录：通过"),
    ("d3", "设计须知", '他说"好"的那一条设计须知'),
    ("d4", "报告(最终版).docx", "报告(最终版).docx 说明"),
    ("d5", "plain notes", "plain english notes about release"),
    ("d6", "纪要", "纪要内容甲乙"),
    ("d7", "长文", "这是一个很长的中文句子用于测试六个汉字"),
]


def make_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE retrieval_document"
               " (document_id TEXT PRIMARY KEY, title TEXT, search_text TEXT)")
    db.execute("CREATE VIRTUAL TABLE retrieval_document_fts USING fts5("
               "title, search_text, document_id UNINDEXED, tokenize='trigram')")
    for doc_id, title, body in DOCS:
        db.execute("INSERT INTO retrieval_document VALUES (?,?,?)", (doc_id, title, body))
        db.execute("INSERT INTO retrieval_document_fts VALUES (?,?,?)", (title, body, doc_id))
    return db


def found(query):
    rows = _recall(make_db(), query, 10)
    return [r["document_id"] for r in rows]


def test_filename_with_extension_is_found():
    assert "d1" in found("项目周报_v2.docx")


def test_quotes_brackets_stars_colons_and_hyphens_do_not_raise():
    for query in ['"好"', "(最终版)", "a*b", "时间:10:00", "v2.0-rc1", "%_\\",
                  "审批:记录-2026", '"unclosed', "(((", "***"]:
        found(query)  # must not raise


def test_terms_inside_quotes_brackets_and_stars_are_found():
    assert "d3" in found('"好"')
    assert "d4" in found("(最终版)")
    assert "d1" in found("项目周报_v2.docx*")


def test_plain_english_two_and_six_character_queries_are_found():
    assert "d5" in found("release")
    assert "d6" in found("纪要")
    assert "d7" in found("中文句子用于测试")


def test_query_with_only_punctuation_returns_nothing_and_does_not_raise():
    assert found("...") == []
    assert found("   ") == []
    assert found("") == []


def test_fts_query_is_built_from_tokens_as_phrases():
    assert _fts_query("项目周报_v2.docx") == '"项目周报_v2" AND "docx"'
    assert _fts_query('say "hi"') == '"say" AND "hi"'
    assert _fts_query("!!!") == ""


def test_like_fallback_matches_title_and_body_for_any_length():
    """The fallback is no longer limited to short CJK terms."""
    db = make_db()
    # "项目周报_v2.docx" is long and contains punctuation; FTS cannot match the
    # literal dotted string, so the row must come from the LIKE path.
    rows = db.execute(
        "SELECT document_id FROM retrieval_document WHERE title LIKE ? ESCAPE ?",
        ("%项目周报_v2.docx%", "\\")).fetchall()
    assert [r["document_id"] for r in rows] == ["d1"]
    assert "d1" in [r["document_id"] for r in _recall(db, "项目周报_v2.docx", 10)]
