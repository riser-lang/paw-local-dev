import json
import sqlite3
from pathlib import Path
import pytest
from backend.pm import storage,versions
from backend.governance import committer
from backend.pm.source_fingerprint import proposal_fingerprint


def test_duplicate_content_has_distinct_review_for_distinct_version():
    first=proposal_fingerprint('project','update',{'id':'p','summary':'完成原型'},['version-one'])
    newer=proposal_fingerprint('project','update',{'id':'p','summary':'完成原型'},['version-two'])
    assert first!=newer
    assert first==proposal_fingerprint('project','update',{'summary':'完成原型','id':'p'},['version-one'])


def test_same_source_set_deduplicates_independent_of_reference_order():
    assert proposal_fingerprint('knowledge','create',{'title':'记录'},['a','b'])==proposal_fingerprint('knowledge','create',{'title':'记录'},['b','a','a'])


@pytest.fixture
def database(tmp_path,monkeypatch):
    db=tmp_path/'test.db'
    monkeypatch.setenv('PAW_WORKSPACE_DB',str(db))
    real_connect=storage.connect
    monkeypatch.setattr(storage,'connect',lambda db_path=None:real_connect(db))
    with storage.connect() as conn:
        conn.executescript('''
          CREATE TABLE project(id TEXT PRIMARY KEY,name TEXT,status TEXT,description TEXT,latest_progress_summary TEXT,next_action_summary TEXT,updated_at TEXT);
          CREATE TABLE review_decision(id TEXT PRIMARY KEY,candidate_id TEXT,decision TEXT,reviewer TEXT,review_channel TEXT,approval_source TEXT,original_payload TEXT,reviewed_payload TEXT,authorization_payload_hash TEXT,review_notes TEXT,created_at TEXT);
          CREATE TABLE approved_change(change_id TEXT PRIMARY KEY,target_type TEXT,operation TEXT,payload TEXT,approval_ref TEXT,source_refs TEXT,expected_target_version TEXT,idempotency_key TEXT,created_at TEXT,status TEXT);
          CREATE TABLE commit_record(id TEXT PRIMARY KEY,change_id TEXT,transaction_id TEXT,result TEXT,committed_at TEXT,error TEXT);
          CREATE TABLE audit_event(id TEXT PRIMARY KEY,event_type TEXT,actor TEXT,target_type TEXT,target_id TEXT,change_id TEXT,commit_id TEXT,details TEXT,created_at TEXT);
        ''')
        conn.execute("INSERT INTO project VALUES('p','星河项目','active','原始身份','初始','下一步','initial')")
        storage.migrate(conn)
    return db


def ingest(sid,title,body):
    with storage.connect() as conn:
        storage.store_source(conn,sid,title,body)
    return versions.process_source(sid)


def current():
    with storage.connect() as conn:
        return versions.read(conn,'p')[0]


def test_effective_dates_outrank_version_and_invalid_dates_are_not_used():
    assert versions.metadata('方案_v3.docx','生效日期：2026年10月12日')['effective_date']=='2026-10-12'
    assert versions.metadata('方案_v3.docx','生效日期：2026-02-31')['effective_date'] is None
    assert versions.metadata('方案_v3.docx','生效日期：2026-10-12；生效日期：2026-10-14')['ambiguous_date']
    assert versions.metadata('方案_v2.1.docx','')['version']==[2,1]


def test_version_and_effective_precedence_and_rule_audit(database):
    ingest('a','方案_v3.docx','星河项目。生效日期：2026-10-12。旧版')
    ingest('b','方案_v2.docx','星河项目。生效日期：2026-10-14。新版')
    value=current()
    assert value['current_source_id']=='b'
    assert {v['source_version_id']:v['status'] for v in value['versions']}=={'a':'superseded','b':'current'}
    with storage.connect() as conn:
        audits=[dict(r) for r in conn.execute('SELECT r.review_channel,r.review_notes,c.status FROM review_decision r JOIN approved_change c ON c.approval_ref=r.id')]
    assert len(audits)==2
    assert all(r['review_channel']=='standing_user_rule' and versions.RULE_SOURCE in r['review_notes'] and r['status']=='committed' for r in audits)


def test_similar_name_and_uncertain_project_never_auto_join(database):
    ingest('a','系统方案_v1.docx','星河项目。第一阶段')
    ingest('b','系统方案修订讨论.docx','星河项目。未确定的资料')
    ingest('c','系统方案_v3.docx','没有明确归属')
    assert current()['current_source_id']=='a'
    with storage.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM pm_family_member').fetchone()[0]==1
        assert conn.execute("SELECT COUNT(*) FROM pm_family_decision WHERE status='pending'").fetchone()[0]==2


def test_references_resynthesize_atomically_and_undo_restores(database):
    ingest('a','方案_v1.docx','星河项目。旧版结论')
    _,cid=committer.create_direct_user_approved_change('pm_section','update',{'project_id':'p','section_id':'progress','text':'旧版结论','source_version_id':'a'},['a'])
    assert committer.commit_approved_change(cid)['result']=='success'
    ruling=ingest('b','方案_v2.docx','星河项目。新版结论')
    with storage.connect() as conn:
        row=conn.execute('SELECT r.* FROM pm_section_current c JOIN pm_section_revision r ON r.id=c.revision_id').fetchone()
        assert row['source_version_id']=='b'
        assert '新版结论' in row['text'] and '旧版结论' not in row['text']
    _,undo=committer.create_direct_user_approved_change('pm_undo','restore',{'change_id':ruling['change_id']})
    assert committer.commit_approved_change(undo)['result']=='success'
    assert current()['current_source_id']=='a'
    with storage.connect() as conn:
        assert conn.execute('SELECT r.source_version_id FROM pm_section_current c JOIN pm_section_revision r ON r.id=c.revision_id').fetchone()[0]=='a'
        assert conn.execute("SELECT status FROM pm_resynthesis WHERE change_id=?",(ruling['change_id'],)).fetchone()[0]=='undone'


def test_superseded_proposal_cannot_become_formal(database):
    ingest('a','方案_v1.docx','星河项目。旧版')
    ingest('b','方案_v2.docx','星河项目。新版')
    # The same check is used for ordinary project/todo/knowledge executors.
    with storage.connect() as conn:
        assert not versions.is_current(conn,'a')
        assert versions.is_current(conn,'b')
    _,cid=committer.create_direct_user_approved_change('pm_section','update',{'project_id':'p','section_id':'progress','text':'旧版','source_version_id':'a'},['a'])
    result=committer.commit_approved_change(cid)
    assert result['result']=='failure' and '已被替换' in result['error']


def test_current_assistant_evidence_excludes_old_derivations(database):
    from backend.pm.assistant_context import augment
    ingest('a','方案_v1.docx','星河项目。过期方案')
    ingest('b','方案_v2.docx','星河项目。有效方案')
    with storage.connect() as conn:
        items=augment(conn,[],'p')
    text=json.dumps(items,ensure_ascii=False)
    assert '有效方案' in text and '过期方案' not in text
    assert all(not i['is_formal_fact'] for i in items)
