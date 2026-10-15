import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
import pytest
from backend.pm import storage, dossier
from backend.pm.budget import bounded_context, size
from backend.pm.contracts import validate_contract
from backend.pm.semantic import invoke
from backend.pm.legacy_context import bounded_legacy


@pytest.fixture
def conn():
    with sqlite3.connect(':memory:') as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE project(id TEXT PRIMARY KEY,name TEXT,description TEXT)')
        conn.execute('CREATE TABLE approved_change(change_id TEXT PRIMARY KEY)')
        conn.execute("INSERT INTO project VALUES('p1','星河试验','原始登记')")
        storage.migrate(conn)
        yield conn


def valid_explainer():
    return dict(one_sentence='星河试验', current_state='完成原型', key_open_issue='缺少第二阶段资料', next_step='建议补充资料')


def test_additive_migration_is_repeatable_and_preserves_old_rows(conn):
    storage.migrate(conn)
    assert dict(conn.execute('SELECT * FROM project').fetchone()) == {'id':'p1','name':'星河试验','description':'原始登记'}


def test_source_is_immutable_and_readable(conn):
    storage.store_source(conn, 'v1', '虚构资料', '第一阶段完成')
    with pytest.raises(ValueError, match='不可覆盖'):
        storage.store_source(conn, 'v1', '虚构资料', '第二阶段完成')
    assert storage.source(conn, 'v1')['body'] == '第一阶段完成'


def test_draft_storage_does_not_change_formal_project(conn):
    before = [tuple(r) for r in conn.execute('SELECT * FROM project')]
    storage.save_derived(conn, 'p1', valid_explainer(), {'project_id':'p1'})
    after = [tuple(r) for r in conn.execute('SELECT * FROM project')]
    assert after == before
    draft = dossier.read(conn, 'p1')['ai_generated'][0]
    assert draft['formal_state'] == 0
    assert draft['label'] == 'AI 生成'
    assert draft['content']['current_state'] == '完成原型'


def test_sections_follow_config_in_order(conn):
    config = storage.config()
    result = dossier.read(conn, 'p1')
    assert [(s['id'],s['title']) for s in result['sections']] == [(s['id'],s['title']) for s in config['sections']]
    assert all(s['current'] is None for s in result['sections'])


@pytest.mark.parametrize('bad', [None, [], {}, {'one_sentence':'x'}, {**valid_explainer(),'extra':'x'}, {**valid_explainer(),'next_step':''}, {**valid_explainer(),'current_state':1}])
def test_contract_rejects_bad_explainers(bad):
    with pytest.raises(ValueError):
        validate_contract('project_explainer', bad, 'output')


def test_missing_key_never_calls_model(conn):
    def call(**_):
        pytest.fail('no-key path called the model')
    result = invoke('project_explainer', {'project':{},'todos':[],'knowledge':[]}, SimpleNamespace(api_key='',chat_completion=call), conn)
    assert result == {'status':'needs_key','message':'需要填写 Key 才能生成'}
    assert conn.execute('SELECT COUNT(*) FROM pm_ai_explainer').fetchone()[0] == 0


def test_invalid_model_output_is_rejected_and_only_rejection_is_recorded(conn):
    provider=SimpleNamespace(api_key='sk-fake-paw-test',chat_completion=lambda **_: ('{"one_sentence":1}',{},None))
    result=invoke('project_explainer', {'project':{},'todos':[],'knowledge':[]}, provider, conn)
    assert result['status'] == 'rejected'
    assert conn.execute('SELECT reason FROM pm_contract_rejection').fetchone()[0] == 'output_schema_invalid'
    assert conn.execute('SELECT COUNT(*) FROM pm_ai_explainer').fetchone()[0] == 0


@pytest.mark.parametrize('budget', [512,1024,4096])
def test_budget_counts_utf8_and_keeps_evidence_links(budget):
    original = {'project':{'id':'p1'},'sections':[{'id':'progress','current':{'text':'完成试验。'*4000,'source_url':'/v1/pm/sources/v1'},'history':[]}], 'ai_generated':[]}
    result=bounded_context(original, budget)
    assert size(result) <= budget
    assert result['truncated'] is True
    assert result['stage_summaries'][0]['source_url'] == '/v1/pm/sources/v1'
    assert result['stage_summaries'][0]['summary_kind'] == 'stored_excerpt'
    assert original['sections'][0]['current']['text'] == '完成试验。'*4000


def test_budget_too_small_fails_instead_of_exceeding_limit():
    with pytest.raises(ValueError, match='预算不足'):
        bounded_context({'project':{'id':'p1'},'sections':[],'padding':'x'*100}, 10)


def test_legacy_endpoint_uses_same_budget_and_keeps_small_responses():
    small={'project':{'id':'p1','name':'星河试验'},'knowledge':[]}
    assert bounded_legacy(small,512) is small
    large={'project':{'id':'p1','name':'星河试验','description':'原始资料'*10000},
           'knowledge':[{'id':'k1','title':'原始规则','one_line_summary':'已确认资料'*1000}]}
    result=bounded_legacy(large,1024)
    assert size(result) <= 1024
    assert result['stage_summaries'][0]['source_url'] == '/v1/web/workspace/knowledge/k1'
    assert large['project']['description'] == '原始资料'*10000
