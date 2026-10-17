import sqlite3
import sys
from pathlib import Path

import pytest


CORE_DIR = Path(__file__).resolve().parents[2] / "backend" / "runtime" / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from paw_retrieval_core import _recall  # noqa: E402


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
