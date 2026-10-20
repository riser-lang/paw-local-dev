"""R-268: a data/metric review card without a title or project is refused in Chinese."""
import sqlite3

import pytest

from backend import repair_v238


def test_missing_project_is_reported_for_a_data_card():
    missing = repair_v238.data_card_missing_fields(
        'data', 'create', {'name': '导师培养人数', 'project_name': ''})
    assert missing == ['project_name']


def test_missing_title_and_project_are_both_reported():
    assert repair_v238.data_card_missing_fields('data', 'create', {}) == ['title', 'project_name']


def test_name_counts_as_the_title_and_project_id_counts_as_the_project():
    payload = {'name': '导师培养人数', 'project_id': 'p-1'}
    assert repair_v238.data_card_missing_fields('data', 'create', payload) == []
    payload = {'title': '导师培养人数', 'project_name': '试点项目'}
    assert repair_v238.data_card_missing_fields('data', 'create', payload) == []


def test_other_targets_and_operations_keep_their_own_rules():
    assert repair_v238.data_card_missing_fields('knowledge', 'create', {}) == []
    assert repair_v238.data_card_missing_fields('data', 'update', {}) == []
    assert repair_v238.data_card_missing_fields('data', 'create', None) == []


def test_the_message_is_the_chinese_sentence_the_review_panel_shows():
    assert repair_v238.DATA_CARD_INCOMPLETE == '请补充标题和项目名称后再接受'


def _conn():
    conn = sqlite3.connect(':memory:')
    conn.execute("CREATE TABLE metric_definition (id TEXT, name TEXT, project_id TEXT, updated_at TEXT)")
    conn.execute("CREATE TABLE project (id TEXT, name TEXT)")
    return conn


def _candidate():
    return {'id': 'c1', 'candidate_type': 'data', 'source_refs': '[]', 'proposed_change': '{}'}


def test_normalize_refuses_a_data_card_with_no_project():
    conn = _conn()
    change = {'target_type': 'data', 'operation': 'create',
              'payload': {'name': '导师培养人数', 'project_name': ''}}
    with pytest.raises(ValueError) as err:
        repair_v238._normalize(conn, _candidate(), change, None, 'LocalUser')
    assert str(err.value) == '请补充标题和项目名称后再接受'


def test_normalize_refuses_a_data_card_with_no_title_or_name():
    conn = _conn()
    change = {'target_type': 'data', 'operation': 'create',
              'payload': {'project_name': '试点项目'}}
    with pytest.raises(ValueError) as err:
        repair_v238._normalize(conn, _candidate(), change, None, 'LocalUser')
    assert '标题' in str(err.value)


def test_normalize_accepts_a_complete_data_card():
    conn = _conn()
    conn.execute("INSERT INTO project VALUES ('p-1', '试点项目')")
    change = {'target_type': 'data', 'operation': 'create',
              'payload': {'name': '导师培养人数', 'project_name': '试点项目', 'target_value': 40}}
    target, operation, payload, refs = repair_v238._normalize(conn, _candidate(), change, None, 'LocalUser')
    assert (target, operation) == ('data', 'create')
    assert payload['project_id'] == 'p-1'
