import sqlite3
import sys
from pathlib import Path

import pytest


CORE_DIR = Path(__file__).resolve().parents[2] / "backend" / "runtime" / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from paw_retrieval_core import _knowledge_doc, _recall  # noqa: E402


@pytest.mark.parametrize("query", [
    "星火导师计划_方案_v2.docx",
    '星火"导师"计划',
    "星火(导师)计划",
    "星火*导师计划",
    "星火:导师计划",
    "星火-导师计划",
    "quarterlyreport",
    "计划",
    "导师计划方案准备",
])
def test_recall_handles_special_characters_and_finds_expected_document(query):
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE retrieval_document (document_id TEXT, title TEXT, search_text TEXT)")
        db.execute("CREATE VIRTUAL TABLE retrieval_document_fts USING fts5(title, search_text, document_id UNINDEXED, tokenize='trigram')")
        db.execute("INSERT INTO retrieval_document VALUES (?, ?, ?)", ("expected", query, query))
        db.execute("INSERT INTO retrieval_document_fts VALUES (?, ?, ?)", (query, query, "expected"))

        results = _recall(db, query, 10)

    assert [row["document_id"] for row in results] == ["expected"]


def test_recall_logs_fts_errors_without_query_text(caplog):
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE retrieval_document (document_id TEXT, title TEXT, search_text TEXT)")
        # A table with the FTS name but without MATCH support forces an SQLite error.
        db.execute("CREATE TABLE retrieval_document_fts (document_id TEXT)")
        secret_query = "private-search-term.docx"
        db.execute("INSERT INTO retrieval_document VALUES (?, ?, ?)",
                   ("expected", secret_query, secret_query))

        results = _recall(db, secret_query, 10)

    assert [row["document_id"] for row in results] == ["expected"]
    assert "retrieval FTS query failed" in caplog.text
    assert "private-search-term" not in caplog.text


def test_knowledge_index_includes_linked_source_filename():
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE knowledge_source_link (knowledge_id TEXT, source_page_id TEXT)")
        db.execute("CREATE TABLE knowledge_project_link (knowledge_id TEXT, project_id TEXT)")
        db.execute("CREATE TABLE wiki_page (id TEXT, title TEXT, wps_document_id TEXT)")
        db.execute("CREATE TABLE source_version_governance (source_version_id TEXT, logical_document_id TEXT)")
        db.execute("CREATE TABLE logical_document (logical_document_id TEXT, display_name TEXT)")
        db.execute("INSERT INTO knowledge_source_link VALUES ('k1', 'w1')")
        db.execute("INSERT INTO wiki_page VALUES ('w1', '星火导师计划方案', 'doc1@1')")
        db.execute("INSERT INTO source_version_governance VALUES ('doc1@1', 'logical1')")
        db.execute("INSERT INTO logical_document VALUES ('logical1', '星火导师计划_方案_v2.docx')")
        row = db.execute(
            "SELECT 'k1' AS id, '项目知识' AS title, '' AS one_line_summary,"
            " '{}' AS content_json, 1 AS confirmed_revision_no, 'current' AS status,"
            " 'r1' AS rev_id, 1 AS revision_no, '2026-10-17' AS updated_at,"
            " '2026-10-17' AS confirmed_at, '2026-10-17' AS rev_created_at"
        ).fetchone()

        indexed = _knowledge_doc(db, row)

    assert "星火导师计划_方案_v2.docx" in indexed["search_text"]
