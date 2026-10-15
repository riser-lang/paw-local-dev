"""Exercise the reviewed target resolver without importing the app or its DB.

These tests use invented projects in an isolated, in-memory SQLite database.
They verify resolution only: approval and persistence stay in governance.
"""
import sqlite3

import pytest

from backend.repair_v238 import _flat_project_update


@pytest.fixture
def conn():
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE project (id TEXT PRIMARY KEY, name TEXT, status TEXT, created_at TEXT)')
        db.executemany('INSERT INTO project VALUES (?, ?, ?, ?)', [
            ('p-alpha', '星河试验', 'active', '2026-01-01'),
            ('p-beta', '松林试验', 'active', '2026-01-02'),
            ('p-archived', '历史试验', 'archived', '2026-01-03'),
        ])
        yield db


@pytest.mark.parametrize('allow_create', [True, False])
def test_explicit_id_wins_over_excerpt(conn, allow_create):
    operation, payload = _flat_project_update(
        conn, {'raw_excerpt': '松林试验已启动'},
        {'id': 'p-alpha', 'progress': '完成样机'}, allow_create=allow_create)
    assert operation == 'update'
    assert payload['id'] == 'p-alpha'
    assert payload['latest_progress_summary'] == '完成样机'


@pytest.mark.parametrize('allow_create', [True, False])
def test_project_name_resolves(conn, allow_create):
    operation, payload = _flat_project_update(
        conn, {}, {'project_name': '星河试验项目', 'new_summary': '完成测试'}, allow_create=allow_create)
    assert operation == 'update'
    assert payload['id'] == 'p-alpha'
    assert payload['latest_progress_summary'] == '完成测试'


def test_unknown_name_flat_proposes_create_without_writing(conn):
    before = conn.total_changes
    operation, payload = _flat_project_update(conn, {}, {'project_name': '虚构新试验'})
    assert operation == 'create'
    assert payload['name'] == '虚构新试验'
    assert conn.total_changes == before
    assert conn.execute('SELECT COUNT(*) FROM project').fetchone()[0] == 3


def test_unknown_name_enveloped_update_rejected(conn):
    before = conn.total_changes
    with pytest.raises(ValueError, match='找不到名为'):
        _flat_project_update(conn, {}, {'project_name': '虚构新试验'}, allow_create=False)
    assert conn.total_changes == before


@pytest.mark.parametrize('allow_create', [True, False])
def test_verbatim_excerpt_resolves_and_retains_progress(conn, allow_create):
    excerpt = '星河试验：完成原型验证，准备下一轮。'
    operation, payload = _flat_project_update(conn, {'raw_excerpt': excerpt}, {}, allow_create=allow_create)
    assert operation == 'update'
    assert payload == {'id': 'p-alpha', 'latest_progress_summary': excerpt}


@pytest.mark.parametrize('allow_create', [True, False])
def test_two_project_mentions_rejected_without_writing(conn, allow_create):
    before = conn.total_changes
    with pytest.raises(ValueError, match='无法确定要更新哪个项目'):
        _flat_project_update(conn, {'raw_excerpt': '星河试验和松林试验都有新进展'}, {}, allow_create=allow_create)
    assert conn.total_changes == before


def test_missing_explicit_id_never_falls_back_to_name(conn):
    with pytest.raises(ValueError, match='要更新的项目不存在'):
        _flat_project_update(conn, {}, {'id': 'missing', 'name': '星河试验'})


def test_archived_project_not_inferred_from_excerpt(conn):
    with pytest.raises(ValueError, match='无法确定要更新哪个项目'):
        _flat_project_update(conn, {'raw_excerpt': '历史试验完成了'}, {})
