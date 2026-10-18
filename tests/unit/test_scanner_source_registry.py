import sqlite3
from pathlib import Path

from backend.runtime_core import scanner as scanner_module
from backend.workspace_api import db as workspace_db
from backend.workspace_api import repository


def test_scanned_source_is_registered_with_full_filename_and_updated_on_rescan(tmp_path, monkeypatch):
    database_path = tmp_path / "workspace.db"
    monkeypatch.setattr(workspace_db, "DB_PATH", database_path)
    with sqlite3.connect(database_path) as conn:
        conn.execute(
            """CREATE TABLE wiki_page (
                id TEXT PRIMARY KEY, domain_id TEXT, title TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE, summary TEXT NOT NULL DEFAULT '',
                category TEXT, wps_document_id TEXT, wps_url TEXT,
                knowledge_status TEXT NOT NULL DEFAULT 'current',
                last_compiled_at TEXT, last_linted_at TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                drive_id TEXT, parent_id TEXT, source_path TEXT,
                source_kind TEXT, source_origin TEXT, canonical_state TEXT,
                canonical_source_id TEXT, content_hash TEXT, wps_updated_at TEXT,
                indexed_at TEXT
            )"""
        )

    def governance_connection():
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE logical_document (id TEXT, content_hash TEXT, display_name TEXT, "
            "status TEXT, created_at TEXT, updated_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE source_version_governance (source_version_id TEXT, logical_document_id TEXT, "
            "governance_status TEXT, authority_rank INTEGER, authority_reason TEXT, confidence TEXT, "
            "provenance_json TEXT, created_at TEXT, updated_at TEXT)"
        )
        return conn

    monkeypatch.setattr(scanner_module, "get_db", governance_connection)
    source_path = Path(tmp_path) / "星火导师计划_方案_v2.docx"
    source_path.write_bytes(b"first version")
    scanner = scanner_module.PawScanner()

    scanner._register_source_identity_and_version(
        source_path, source_path.stem, "a" * 64, 1_750_000_000, source_path.stat().st_size
    )
    scanner._register_source_identity_and_version(
        source_path, source_path.stem, "b" * 64, 1_750_000_001, source_path.stat().st_size
    )

    sources = repository.list_wiki(limit=500)
    assert len(sources) == 1
    assert sources[0]["title"] == source_path.name
    assert sources[0]["source_origin"] == "local_scan"
    assert sources[0]["source_path"] == str(source_path.resolve())
    assert sources[0]["source_kind"] == "document"
    assert sources[0]["content_hash"] == "b" * 64
