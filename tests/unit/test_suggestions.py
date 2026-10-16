import json
from types import SimpleNamespace
import pytest
from backend.pm import storage,suggestions
from tests.unit.test_versions import database,ingest
from tests.unit.test_cockpit import prepare


def output(quote='原型已完成',sid='a'):
    return {'items':[{'category':kind,'text':'建议核对资料中的原型进展，确认后续安排。',
        'basis':[{'source_version_id':sid,'quote':quote}]} for kind in ('gaps','risks','next')]}


def provider(value,callback=None):
    def call(**kwargs):
        if callback:callback()
        return json.dumps(value,ensure_ascii=False),{},None
    return SimpleNamespace(api_key='fake-unit-key',chat_completion=call)


def formal():
    with storage.connect() as conn:
        return {'projects':[tuple(r) for r in conn.execute('SELECT * FROM project')],
            'approved':conn.execute('SELECT COUNT(*) FROM approved_change').fetchone()[0],
            'sections':conn.execute('SELECT COUNT(*) FROM pm_section_revision').fetchone()[0]}


def seed():ingest('a','方案_v1.docx','星河项目。原型已完成。下一步需要整理验收。')


def test_advice_is_separate_grounded_and_generation_has_no_write_lock(database):
    seed();before=formal();calls=[]
    def observed():
        calls.append(True)
        with storage.connect() as other:
            other.execute("INSERT INTO pm_system_health(key,last_attempt_at,state,summary) VALUES('scan',?,'ok','unit observation')",(storage.now(),))
    result=suggestions.generate('p',provider(output(),observed))
    assert result['status']=='draft' and len(result['items'])==3 and calls==[True]
    assert formal()==before
    assert {i['category'] for i in result['items']}=={'gaps','risks','next'}
    assert all(i['formal_state']==0 and i['label']=='AI 建议' and i['basis'][0]['excerpt']=='原型已完成' for i in result['items'])
    cached=suggestions.generate('p',provider(output(),lambda:pytest.fail('unchanged context should reuse valid advice')))
    assert cached['status']=='cached' and len(cached['items'])==3


def test_invalid_contract_is_discarded_and_previous_valid_advice_preserved(database):
    seed();suggestions.generate('p',provider(output()));before=formal()
    result=suggestions.generate('p',provider({'bad_schema':'invalid-marker'}),True)
    assert result['status']=='rejected' and formal()==before
    with storage.connect() as conn:
        assert len(suggestions.read(conn,'p'))==3
        assert conn.execute("SELECT COUNT(*) FROM pm_contract_rejection WHERE contract='project_advice' AND reason='output_schema_invalid'").fetchone()[0]==1
        assert not conn.execute("SELECT 1 FROM pm_suggestion WHERE text LIKE '%invalid-marker%'").fetchone()


def test_schema_valid_but_invented_quote_cannot_be_stored(database):
    seed();before=formal()
    assert suggestions.generate('p',provider(output('并不存在的原文')))['status']=='rejected'
    with storage.connect() as conn:assert suggestions.read(conn,'p')==[]
    assert formal()==before


def test_no_key_and_no_evidence_do_not_call_model(database):
    bad=lambda **kwargs:pytest.fail('model must not be called')
    assert suggestions.generate('p',SimpleNamespace(api_key=None,chat_completion=bad))['status']=='needs_key'
    assert suggestions.generate('p',SimpleNamespace(api_key='fake',chat_completion=bad))['status']=='needs_evidence'


def test_old_basis_is_hidden_and_cannot_be_adopted_after_supersession(database):
    seed();old=suggestions.generate('p',provider(output()))['items'][0]
    ingest('b','方案_v2.docx','星河项目。原型已完成。联调已启动。')
    before=formal()
    with storage.connect() as conn:assert suggestions.read(conn,'p')==[]
    with pytest.raises(ValueError,match='过期'):suggestions.adopt(old['id'],lambda *a,**kw:pytest.fail('stale suggestion must not create candidate'))
    assert formal()==before


def test_adoption_is_idempotent_and_pending_does_not_mean_formal_adoption(database):
    seed();item=suggestions.generate('p',provider(output()))['items'][0]
    with storage.connect() as conn:prepare(conn)
    calls=[];before=formal()
    def candidate(*args,**kwargs):
        calls.append((args,kwargs))
        with storage.connect() as conn:conn.execute('INSERT INTO candidate VALUES(?,?,?,?,?,?)',('cid','pending',storage.now(),'{}','AI建议','[]'))
        return 'cid'
    assert suggestions.adopt(item['id'],candidate)['status']=='pending_review'
    assert suggestions.adopt(item['id'],candidate)['candidate_id']=='cid' and len(calls)==1
    with storage.connect() as conn:
        assert next(i for i in suggestions.read(conn,'p') if i['id']==item['id'])['state']=='pending_review'
    assert formal()==before
