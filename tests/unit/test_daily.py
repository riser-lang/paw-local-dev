import json
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from backend.pm import storage,daily,knowledge_gaps
from backend.governance import committer
from tests.unit.test_versions import database,ingest


def provider(output):return SimpleNamespace(api_key='fake-unit-key',chat_completion=lambda **_:(json.dumps(output,ensure_ascii=False),{},None))


def section(section,text,sid):
    _,cid=committer.create_direct_user_approved_change('pm_section','update',{'project_id':'p','section_id':section,'text':text,'source_version_id':sid},[sid])
    assert committer.commit_approved_change(cid)['result']=='success'


def test_daily_caches_same_day_and_flags_all_four_issues_without_formal_repair(database):
    ingest('a','方案_v1.docx','星河项目。交付状态为已完成。')
    section('progress','交付状态为已完成','a')
    with storage.connect() as conn:
        conn.execute('CREATE TABLE todo(id TEXT PRIMARY KEY,domain_id TEXT,project_id TEXT,title TEXT,description TEXT,status TEXT,priority TEXT,due_at TEXT,start_at TEXT,source_type TEXT,ai_generated INTEGER,created_at TEXT,updated_at TEXT)')
    _,cid=committer.create_direct_user_approved_change('todo','create',{'project_id':'p','title':'复核交付记录'},['a'])
    assert committer.commit_approved_change(cid)['result']=='success'
    ingest('b','方案_v2.docx','星河项目。交付状态为已完成。交付状态为尚未完成。')
    section('risks','交付状态为尚未完成','b');section('progress','交付状态为已完成','b')
    ingest('c','待裁定.docx','星河项目。生效日期：2026-10-01。生效日期：2026-10-02。')
    pair={'left_section':'progress','right_section':'risks','left_quote':'交付状态为已完成','right_quote':'交付状态为尚未完成'}
    with storage.connect() as conn:
        before=[tuple(r) for r in conn.execute('SELECT * FROM project')];count=conn.execute('SELECT COUNT(*) FROM approved_change').fetchone()[0]
    now=daily.run('p',provider({'findings':[pair]}));again=daily.run('p',provider({'invalid':True}))
    assert again['status']=='cached' and again['id']==now['id']
    future=(datetime.now(timezone.utc)+timedelta(days=30)).isoformat()
    result=daily.run('p',provider({'findings':[pair]}),future)
    assert result['preview'] and {i['kind'] for i in result['items']}=={'stale_reference','undecided_family','no_progress','section_contradiction'}
    with storage.connect() as conn:
        assert daily.read(conn,'p')[0]['id']==now['id']
        assert daily.read(conn,'p',result['id'])[0]['preview']
        assert [tuple(r) for r in conn.execute('SELECT * FROM project')]==before
        assert conn.execute('SELECT COUNT(*) FROM approved_change').fetchone()[0]==count


def test_unknown_or_unquoted_sections_are_discarded_and_nokey_does_not_invent_check(database):
    ingest('a','方案_v1.docx','星河项目。原型已完成。风险待核实。')
    section('progress','原型已完成','a');section('risks','风险待核实','a')
    result=daily.run('p',provider({'findings':[{'left_section':'progress','right_section':'risks','left_quote':'虚构','right_quote':'风险待核实'}]}))
    assert result['semantic_status']=='rejected' and not result['items']
    result=daily.run('p',SimpleNamespace(api_key=None),force=True)
    assert result['semantic_status']=='needs_key' and not result['items']
    def call(**_):
        with storage.connect() as other:storage.rejection(other,'unit','writer released before semantic call')
        return json.dumps({'findings':[]}),{},None
    result=daily.run('p',SimpleNamespace(api_key='fake-unit-key',chat_completion=call))
    assert result['semantic_status']=='draft' and result['status']=='checked'


def test_gap_deduplication_is_per_project_and_reopens_after_new_unknown_answer(database):
    first=knowledge_gaps.record('p','预算是多少？');second=knowledge_gaps.record('p','预算是多少?')
    assert first==second
    with storage.connect() as conn:
        rows=knowledge_gaps.read(conn);assert len(rows)==1 and rows[0]['occurrences']==2
        conn.execute("UPDATE pm_knowledge_gap SET state='resolved' WHERE id=?",(first,))
    assert knowledge_gaps.record('unknown','不写不存在项目的问题') is None
    knowledge_gaps.record('p','预算是多少？')
    with storage.connect() as conn:assert knowledge_gaps.read(conn)[0]['occurrences']==3


def test_insufficient_answer_detection_does_not_log_quoted_facts_or_configuration_errors():
    assert knowledge_gaps.is_insufficient('目前依据不足，无法确定。',{'claims':[]})
    assert knowledge_gaps.is_insufficient('已有部分资料，但预算仍未知。',{'answer_status':'insufficient_evidence','claims':[{'text':'部分资料'}]})
    assert not knowledge_gaps.is_insufficient('没有足够资料，是风险栏的原文。',{'claims':[{'text':'原文','evidence':['source:a']}]})
    assert not knowledge_gaps.is_insufficient('需要填写 Key 才能生成。',None)
