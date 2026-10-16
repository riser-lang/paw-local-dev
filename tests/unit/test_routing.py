from types import SimpleNamespace
import json
from backend.pm import storage,versions,receipts,routing,updates
from backend.governance import committer
from tests.unit.test_versions import database


def model():
    return SimpleNamespace(api_key='fake',chat_completion=lambda **_: (json.dumps({'sections':[{'section_id':'progress','relation':'supplement','text':'联调已启动','quote':'联调已启动'}]}),{},None))


def intake(sid,body='星河项目。联调已启动。',capture=None):
    evidence={'display_name':'真实输入.docx','text_blocks':[{'text':body}],'is_capture':bool(capture),'capture_id':capture}
    with storage.connect() as conn:storage.store_source(conn,sid,evidence['display_name'],body,kind='capture' if capture else 'document')
    receipts.register(evidence,sid);versions.process_source(sid)
    return evidence


def undo(change):
    _,cid=committer.create_direct_user_approved_change('pm_undo','restore',{'change_id':change})
    assert committer.commit_approved_change(cid)['result']=='success'


def test_exact_routing_receipt_uses_committed_column_and_rule_source(database):
    evidence=intake('a');updates.process_source('a',model())
    receipts.register(evidence,'a')
    with storage.connect() as conn:
        rows=receipts.read(conn)
        assert len(rows)==1
        assert rows[0]['state']=='routed' and rows[0]['project_id']=='p'
        assert rows[0]['updated_sections']==['进度与里程碑']
        assert rows[0]['authorization']['approval_source']=='standing_user_rule'
        assert updates.current(conn,'p','progress')['text']=='联调已启动'


def test_ambiguous_input_only_routes_after_user_and_can_be_reassigned_after_undo(database):
    with storage.connect() as conn:conn.execute("INSERT INTO project VALUES('q','山川项目','active','','','','initial')")
    intake('a','归属待定。联调已启动。')
    with storage.connect() as conn:
        first=receipts.read(conn)[0]
        assert first['state']=='needs_decision' and first['project_id'] is None
        assert first['note']=='等待你确认归属'
        assert conn.execute('SELECT COUNT(*) FROM pm_section_revision').fetchone()[0]==0
        card=routing.pending(conn)[0]
    result=routing.resolve(card['id'],'p',model())
    with storage.connect() as conn:
        assigned=receipts.read(conn)[0]
        assert assigned['state']=='routed' and assigned['authorization']['approval_source']=='direct_user'
        assert assigned['updated_sections']==['进度与里程碑']
        section=updates.current(conn,'p','progress')['change_id']
    undo(section);undo(result['change_id'])
    with storage.connect() as conn:assert receipts.read(conn)[0]['state']=='needs_decision'
    rerouted=routing.resolve(card['id'],'q',model())
    with storage.connect() as conn:
        assert receipts.read(conn)[0]['project_id']=='q'
        assert updates.current(conn,'p','progress') is None
        assert updates.current(conn,'q','progress')['text']=='联调已启动'


def test_reject_and_undo_are_governed_and_keep_input_readable(database):
    intake('a','项目归属待确认。联调已启动。')
    with storage.connect() as conn:card=routing.pending(conn)[0]
    _,cid=committer.create_direct_user_approved_change('pm_input_decision','reject',{'decision_id':card['id']})
    assert committer.commit_approved_change(cid)['result']=='success'
    with storage.connect() as conn:
        assert receipts.read(conn)[0]['state']=='rejected'
        assert storage.source(conn,'a')['body']=='项目归属待确认。联调已启动。'
        assert conn.execute('SELECT COUNT(*) FROM pm_section_revision').fetchone()[0]==0
    undo(cid)
    with storage.connect() as conn:assert receipts.read(conn)[0]['state']=='needs_decision'


def test_distinct_capture_inputs_with_identical_text_keep_separate_receipts(database):
    intake('a',capture='capture-one');intake('b',capture='capture-two')
    updates.process_source('a',model());updates.process_source('b',model())
    with storage.connect() as conn:
        rows=receipts.read(conn)
        assert len(rows)==2 and {r['kind'] for r in rows}=={'capture'}
        assert {r['project_id'] for r in rows}=={'p'}
        assert all('随手记' in r['title'] for r in rows)


def test_literal_negation_is_not_high_confidence(database):
    intake('a','不属于星河项目。联调已启动。')
    with storage.connect() as conn:
        assert receipts.read(conn)[0]['state']=='needs_decision'
        assert conn.execute('SELECT COUNT(*) FROM pm_family_member').fetchone()[0]==0
