import sqlite3

from backend.workspace_api import repository
from backend.workspace_api.schemas import KnowledgeRevisionResponse


def test_legacy_scalar_revision_content_is_preserved_as_detail_object(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE knowledge_page (id TEXT PRIMARY KEY, title TEXT);
        CREATE TABLE knowledge_revision (
            id TEXT, knowledge_id TEXT, revision_no INTEGER, content_json TEXT,
            change_summary TEXT, compiled_at TEXT, created_at TEXT
        );
        CREATE TABLE knowledge_source_link (
            knowledge_id TEXT, source_page_id TEXT, source_role TEXT, note TEXT
        );
        CREATE TABLE wiki_page (id TEXT, title TEXT, category TEXT, wps_document_id TEXT);
        CREATE TABLE knowledge_project_link (
            knowledge_id TEXT, project_id TEXT, relation_type TEXT
        );
        CREATE TABLE project (id TEXT, name TEXT);
        INSERT INTO knowledge_page (id, title) VALUES ('k1', '旧格式知识');
        INSERT INTO knowledge_revision
            (id, knowledge_id, revision_no, content_json, compiled_at, created_at)
            VALUES ('r1', 'k1', 1, '"正文内容"', '2026-10-18', '2026-10-18');
        """
    )
    monkeypatch.setattr(repository, "connect", lambda: conn)

    detail = repository.get_knowledge("k1")

    assert detail["latest_revision"]["content_json"] == {"content": "正文内容"}
    response = KnowledgeRevisionResponse.model_validate(detail["latest_revision"])
    assert response.content_json == {"content": "正文内容"}
