import json
import sqlite3
import pytest
from backend.pm import authorization,storage,dossier
from backend.governance import committer
from tests.unit.test_versions import database,ingest


def test_legacy_enum_upgrade_is_additive_and_preserves_original_audit():
    with sqlite3.connect(':memory:') as conn:
        conn.row_factory=sqlite3.Row
        conn.execute('''CREATE TABLE review_decision(id TEXT PRIMARY KEY,candidate_id TEXT,decision TEXT,
          reviewer TEXT,review_channel TEXT,approval_source TEXT CHECK(approval_source IN ('candidate_review','direct_user')),
          original_payload TEXT,reviewed_payload TEXT,authorization_payload_hash TEXT,review_notes TEXT,created_at TEXT)''')
        # Synthetic historical audit fixture: no business entity is written from it.
        values=('old',None,'approve','standing_user_rule','standing_user_rule','direct_user','{}',None,'hash',authorization.RULE_SOURCE+'；R1/R2','historical')
        conn.execute('INSERT INTO review_decision VALUES(?,?,?,?,?,?,?,?,?,?,?)',values)
        columns=[r[1] for r in conn.execute('PRAGMA table_info(review_decision)')]
        original=tuple(conn.execute('SELECT '+','.join(columns)+' FROM review_decision').fetchone())
        authorization.migrate(conn);authorization.migrate(conn)
        assert tuple(conn.execute('SELECT '+','.join(columns)+' FROM review_decision').fetchone())==original
        assert authorization.legacy_slot(conn,'standing_user_rule')=='candidate_review'
        old=conn.execute('SELECT * FROM pm_review_decision_audit').fetchone()
        assert old['approval_source']=='standing_user_rule' and old['legacy_approval_source']=='direct_user'
        assert authorization.describe(old)['legacy_record'] is True
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_rule_and_hand_approval_have_distinct_immutable_provenance(database):
    first=ingest('a','方案_v1.docx','星河项目。完成原型。')
    with storage.connect() as conn:
        source=authorization.for_change(conn,first['change_id'])
        assert source['approval_source']=='standing_user_rule'
        assert '按用户常设规则自动写入' in source['label'] and authorization.RULE_SOURCE in source['label']
        row=conn.execute('SELECT r.* FROM review_decision r JOIN approved_change a ON a.approval_ref=r.id WHERE a.change_id=?',(first['change_id'],)).fetchone()
        assert row['approval_source_v2']=='standing_user_rule'
        assert row['approval_source']!='direct_user'
        detail=json.loads(conn.execute('SELECT details FROM audit_event WHERE change_id=?',(first['change_id'],)).fetchone()[0])
        assert detail['authorization']['approval_source']=='standing_user_rule'
    _,change=committer.create_direct_user_approved_change('pm_section','update',{'project_id':'p','section_id':'progress','text':'完成原型','source_version_id':'a'},['a'])
    assert committer.commit_approved_change(change)['result']=='success'
    with storage.connect() as conn:
        current=next(s for s in dossier.read(conn,'p')['sections'] if s['id']=='progress')['current']
        assert current['authorization']['approval_source']=='direct_user'
        assert current['authorization']['label']=='用户亲手批准'


def test_standing_rule_cannot_masquerade_without_rule_provenance(database):
    with pytest.raises(ValueError,match='规则来源'):
        committer.create_review_decision_atomic(None,'approve',reviewer='test_actor',review_channel='web_review',
            approval_source='standing_user_rule',original_payload={'title':'fictional'},target_type='todo',operation='create')
    with storage.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM review_decision').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM approved_change').fetchone()[0]==0
