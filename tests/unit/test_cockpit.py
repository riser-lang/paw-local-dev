from types import SimpleNamespace
import pytest
from backend.pm import storage,cockpit,updates
from tests.unit.test_versions import database,ingest
from tests.unit.test_routing import intake


@pytest.fixture(autouse=True)
def isolated_backups(database,monkeypatch,tmp_path):
    monkeypatch.setattr(storage,'runtime_paths',lambda:(database,tmp_path/'backups'))


def prepare(conn):
    conn.execute('CREATE TABLE candidate(id TEXT PRIMARY KEY,status TEXT,created_at TEXT,proposed_change TEXT,raw_excerpt TEXT,source_refs TEXT)')


def test_empty_views_do_not_guess_dates_progress_or_ai(database):
    with storage.connect() as conn:
        prepare(conn)
        before=conn.total_changes
        vm=cockpit.today_vm(conn)
        assert conn.total_changes==before
        assert vm['ai_suggestions']==[] and vm['decisions']==[]
        row=vm['portfolio']['projects'][0]
        assert row['start'] is None and row['end'] is None and row['progress'] is None
        assert row['one_liner'] is None
        assert len(vm['system_status'])==5
        assert cockpit.stamp('2026-10-15T20:00:00Z')=='2026-10-16T04:00:00+08:00'
        assert cockpit.day('not a date') is None


def test_inbox_counts_one_card_per_assignment_and_deferral_keeps_pending(database):
    intake('a','项目归属待确认。原型。')
    with storage.connect() as conn:
        prepare(conn);items=cockpit.inbox(conn)
        assert len(items)==1 and items[0]['id'].startswith('assignment:')
        assert items[0]['if_yes'] and items[0]['if_no'] and items[0]['sources'][0]['id']=='a'
    result=cockpit.decide([items[0]['id']],'later',{},SimpleNamespace(api_key=None))
    assert result['items'][0]['ok']
    with storage.connect() as conn:
        assert len(cockpit.inbox(conn))==1
        assert conn.execute("SELECT decision FROM review_decision WHERE review_channel='local_pm_inbox'").fetchone()[0]=='defer'


def test_batch_decisions_commit_and_undo_restores_real_conclusion(database):
    ingest('a','独立资料.docx','星河项目。完成原型。整理验收。')
    with storage.connect() as conn:
        prepare(conn)
        for section,text in [('progress','完成原型'),('next','整理验收')]:
            updates.version_sensitive(conn,'p',section,'a',text,text,'')
        items=cockpit.inbox(conn)
        count=conn.execute('SELECT COUNT(*) FROM approved_change').fetchone()[0]
    result=cockpit.decide([i['id'] for i in items],'yes',{},SimpleNamespace(api_key=None))
    assert all(r['ok'] for r in result['items'])
    with storage.connect() as conn:
        assert cockpit.inbox(conn)==[]
        assert conn.execute('SELECT COUNT(*) FROM approved_change').fetchone()[0]==count+2
        vm=cockpit.project_vm(conn,'p')
        assert vm['entries']['progress'][0]['text']=='完成原型'
        assert vm['entries']['progress'][0]['sources'][0]['excerpt']=='完成原型'
        assert vm['entries']['progress'][0]['tag']['text']=='用户亲手批准'
        assert len(vm['changes'])==2
        assert all(c['revertible'] for c in vm['changes'])


def test_approved_task_missing_dates_is_shared_by_milestones_and_gaps(database):
    with storage.connect() as conn:
        prepare(conn)
        conn.execute('CREATE TABLE todo(id TEXT,project_id TEXT,title TEXT,description TEXT,status TEXT,start_at TEXT,due_at TEXT,completed_at TEXT,created_at TEXT,updated_at TEXT)')
        conn.execute("INSERT INTO todo VALUES('t','p','验收里程碑',NULL,'open',NULL,NULL,NULL,'2026-10-16T00:00:00Z','2026-10-16T00:00:00Z')")
        vm=cockpit.project_vm(conn,'p')
        assert vm['milestones'][0]['title']=='验收里程碑'
        assert vm['milestones'][0]['plan_start'] is None and vm['milestones'][0]['actual_start'] is None
        assert '日期待补' in vm['entries']['gaps'][0]['text']
        assert vm['entries']['gaps'][0]['sources'][0]['kind']=='todo'
