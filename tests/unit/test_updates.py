import json
from types import SimpleNamespace
import pytest
from backend.pm import storage,updates,versions
from backend.pm.rules import requires_user
from backend.governance import committer
from tests.unit.test_versions import database,ingest


def provider(text,relation='supplement'):
    return SimpleNamespace(api_key='fake',chat_completion=lambda **_: (json.dumps({'sections':[{'section_id':'progress','relation':relation,'text':text,'quote':text}]}),{},None))


def seed():
    ingest('a','方案_v1.docx','星河项目。原型已完成。')
    _,cid=committer.create_direct_user_approved_change('pm_section','update',{'project_id':'p','section_id':'progress','text':'原型已完成','source_version_id':'a'},['a'])
    assert committer.commit_approved_change(cid)['result']=='success'
    with storage.connect() as conn:return dict(updates.current(conn,'p','progress'))


def test_consistent_does_not_create_revision_or_decision(database):
    initial=seed()
    ingest('b','现场纪要.docx','星河项目。原型已完成。')
    result=updates.process_source('b',provider('原型已完成','consistent'))
    assert result['items'][0]['status']=='consistent'
    with storage.connect() as conn:
        assert updates.current(conn,'p','progress')['id']==initial['id']
        assert conn.execute("SELECT COUNT(*) FROM pm_section_assessment WHERE status IN ('needs_user','conflict')").fetchone()[0]==0


def test_supplement_goes_through_rule_commit_and_undo(database):
    initial=seed()
    ingest('b','现场纪要.docx','星河项目。联调已启动。')
    result=updates.process_source('b',provider('联调已启动'))
    cid=result['items'][0]['change_id']
    with storage.connect() as conn:
        current=updates.current(conn,'p','progress')
        assert current['text']=='原型已完成\n联调已启动'
        assert {r[0] for r in conn.execute('SELECT source_version_id FROM pm_revision_source WHERE revision_id=?',(current['id'],))}=={'a','b'}
        assert conn.execute('SELECT review_channel FROM review_decision r JOIN approved_change a ON a.approval_ref=r.id WHERE a.change_id=?',(cid,)).fetchone()[0]=='standing_user_rule'
    _,undo=committer.create_direct_user_approved_change('pm_undo','restore',{'change_id':cid})
    assert committer.commit_approved_change(undo)['result']=='success'
    with storage.connect() as conn:
        assert updates.current(conn,'p','progress')['id']==initial['id']
        assert conn.execute('SELECT status FROM pm_section_assessment WHERE change_id=?',(cid,)).fetchone()[0]=='undone'


def test_conflict_keeps_current_until_user_and_undo_reopens(database):
    initial=seed()
    ingest('b','现场纪要.docx','星河项目。原型尚未完成。')
    result=updates.process_source('b',provider('原型尚未完成','conflict'))
    ident=result['items'][0]['id']
    with storage.connect() as conn:
        assert updates.current(conn,'p','progress')['id']==initial['id']
        assert updates.read_conflicts(conn,'p')[0]['status']=='open'
    _,cid=committer.create_direct_user_approved_change('pm_update_decision','resolve',{'assessment_id':ident,'choice':'incoming'})
    assert committer.commit_approved_change(cid)['result']=='success'
    with storage.connect() as conn:
        assert updates.current(conn,'p','progress')['text']=='原型尚未完成'
        assert updates.read_conflicts(conn,'p')[0]['status']=='resolved'
    _,undo=committer.create_direct_user_approved_change('pm_undo','restore',{'change_id':cid})
    assert committer.commit_approved_change(undo)['result']=='success'
    with storage.connect() as conn:
        assert updates.current(conn,'p','progress')['id']==initial['id']
        assert updates.read_conflicts(conn,'p')[0]['status']=='open'


@pytest.mark.parametrize('text',['新项目：创建试验','KPI 目标值改为 20','政策调整','绩效规则变更','人员选择方案','赛制安排'])
def test_user_exclusive_categories_never_automatically_commit(database,text):
    initial=seed()
    ingest('b','现场纪要.docx','星河项目。'+text)
    result=updates.process_source('b',provider(text))
    assert result['items'][0]['status']=='needs_user'
    with storage.connect() as conn:assert updates.current(conn,'p','progress')['id']==initial['id']
    assert requires_user(text)


def test_invalid_quote_is_discarded_without_formal_change(database):
    initial=seed()
    ingest('b','现场纪要.docx','星河项目。联调已启动。')
    result=updates.process_source('b',provider('模型虚构的数字 999'))
    assert result['status']=='rejected'
    with storage.connect() as conn:
        assert updates.current(conn,'p','progress')['id']==initial['id']
        assert conn.execute('SELECT reason FROM pm_contract_rejection').fetchone()[0]=='output_reference_or_quote_invalid'


def test_model_cannot_authorize_unrelated_replacement(database):
    initial=seed()
    ingest('b','现场纪要.docx','星河项目。联调已启动。')
    result=updates.process_source('b',provider('联调已启动','replace'))
    assert result['items'][0]['status']=='needs_user'
    with storage.connect() as conn:assert updates.current(conn,'p','progress')['id']==initial['id']


def test_new_version_refreshes_only_its_part_of_a_multi_source_conclusion(database):
    seed()
    ingest('b','现场纪要.docx','星河项目。联调已启动。')
    updates.process_source('b',provider('联调已启动'))
    ingest('c','方案_v2.docx','星河项目。原型已经归档。')
    with storage.connect() as conn:
        row=updates.current(conn,'p','progress')
        assert '原型已经归档' in row['text'] and '联调已启动' in row['text']
        assert '原型已完成' not in row['text']
        assert {r[0] for r in conn.execute('SELECT source_version_id FROM pm_revision_source WHERE revision_id=?',(row['id'],))}=={'b','c'}


def test_new_version_additions_are_not_lost_when_old_sentence_remains(database):
    initial=seed()
    ruling=ingest('b','方案_v2.docx','星河项目。原型已完成，联调已启动。')
    with storage.connect() as conn:
        row=updates.current(conn,'p','progress')
        assert row['source_version_id']=='b'
        assert '原型已完成' in row['text'] and '联调已启动' in row['text']
    _,undo=committer.create_direct_user_approved_change('pm_undo','restore',{'change_id':ruling['change_id']})
    assert committer.commit_approved_change(undo)['result']=='success'
    with storage.connect() as conn:assert updates.current(conn,'p','progress')['id']==initial['id']


def test_conflict_reassessment_does_not_amplify_reread_queue(database):
    seed()
    ingest('b','现场纪要.docx','星河项目。原型尚未完成。')
    updates.process_source('b',provider('原型尚未完成','conflict'))
    _,cid=committer.create_direct_user_approved_change('pm_section','update',{'project_id':'p','section_id':'progress','text':'原型已经完成','source_version_id':'a'},['a'])
    assert committer.commit_approved_change(cid)['result']=='success'
    updates.process_source('b',provider('原型尚未完成','conflict'))
    with storage.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM pm_reread_job').fetchone()[0]==2
        assert {c['status'] for c in updates.read_conflicts(conn,'p')}=={'open','stale'}


def test_more_than_default_eight_configured_sections_are_supported(database,monkeypatch):
    configs=[{'id':'custom'+str(i),'title':'自定栏目'+str(i),'project_fields':[]} for i in range(9)]
    monkeypatch.setattr(storage,'config',lambda:{'sections':configs,'context_budget_bytes':16384})
    body='星河项目。\n'+'\n'.join('事实片段'+str(i) for i in range(9))
    ingest('a','资料_v1.docx',body)
    output={'sections':[{'section_id':c['id'],'relation':'supplement','text':'事实片段'+str(i),'quote':'事实片段'+str(i)} for i,c in enumerate(configs)]}
    model=SimpleNamespace(api_key='fake',chat_completion=lambda **_: (json.dumps(output),{},None))
    result=updates.process_source('a',model)
    assert len(result['items'])==9 and all(i['status']=='applied' for i in result['items'])
    with storage.connect() as conn:
        assert {r[0] for r in conn.execute('SELECT section_id FROM pm_section_current')}=={s['id'] for s in configs}
