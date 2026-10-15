import os,sys,json,uuid,threading,sqlite3
from pathlib import Path
from contextlib import closing
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
data=root/'scratch'/('source-barrier-'+uuid.uuid4().hex)
data.mkdir(parents=True)
(data/'config.json').write_text(json.dumps({'provider':'custom','work_dir':str(data/'sources'),'auto_propose':False}),encoding='utf-8')
for key in list(os.environ):
    if 'API_KEY' in key or key.endswith('_BASE_URL'):
        os.environ.pop(key)
os.environ.update(PAW_DATA_DIR=str(data),PAW_WORKSPACE_DB=str(data/'workspace.db'))
import backend.main
from backend.governance.service import governance
from backend.runtime_core.planner import agent_planner
from backend.runtime_core.scanner import scanner
import backend.runtime_core.planner as planner_module
from backend.workspace_api.repository import create_project
create_project({'name':'冲刺项目'})
evidence={'display_name':'虚构资料.txt','text_blocks':[{'index':0,'text':'冲刺项目：完成原型，准备联调。'}]}
evidence_file=data/'reader_result.json'
evidence_file.write_text(json.dumps(evidence,ensure_ascii=False),encoding='utf-8')
class Mock:
    def propose(self,evidence,projects,knowledge):
        text=evidence['text_blocks'][0]['text']
        name=projects[0]['name']
        return [dict(target_type='todo',operation='create',payload={'title':'准备联调'},raw_excerpt=text),
                dict(target_type='knowledge',operation='create',payload={'title':'原型进展'},raw_excerpt=text),
                dict(target_type='project',operation='update',payload={'project_name':name,'new_summary':text},raw_excerpt=text),
                dict(target_type='project',operation='update',payload={},raw_excerpt=text)]
planner_module.get_active_provider=lambda:Mock()
three=threading.Event();release=threading.Event();waiting=threading.Event();consumer_done=threading.Event()
errors=[]
original_create=governance.create_candidate
counter=[]
def create(**kwargs):
    counter.append(kwargs['target_type'])
    if len(counter)==4:
        three.set()
        assert release.wait(5)
    return original_create(**kwargs)
governance.create_candidate=create
original_wait=governance.wait_for_source_completion
def wait(ident,timeout=60):
    waiting.set()
    return original_wait(ident,timeout)
governance.wait_for_source_completion=wait
ident='f'*64
def produce():
    ids=None
    try:
        assert governance.claim_source(ident)
        ids=agent_planner.plan_document_impact(evidence,'virtual-source',ident)
    except Exception as e:
        errors.append(type(e).__name__)
    finally:
        governance.release_source(ident,'虚构资料.txt','mock-owner',len(ids) if ids is not None else None)
def consume():
    try:
        scanner._process_phase2_file(data/'虚构资料.txt','virtual-source',str(evidence_file),ident)
    except Exception as e:
        errors.append(type(e).__name__)
    finally:
        consumer_done.set()
producer=threading.Thread(target=produce);producer.start()
assert three.wait(5)
with closing(sqlite3.connect(data/'workspace.db')) as conn:
    partial=conn.execute('SELECT COUNT(*) FROM candidate').fetchone()[0]
    legacy=conn.execute("SELECT 1 FROM candidate WHERE source_refs LIKE ? AND analysis_model NOT LIKE 'Scanner+%' AND analysis_model NOT LIKE 'rule:%' LIMIT 1",('%"'+ident+'"%',)).fetchone() is not None
assert partial==3 and legacy is True
assert governance.is_source_analyzed(ident) is False
consumer=threading.Thread(target=consume);consumer.start()
assert waiting.wait(5)
assert not consumer_done.wait(.02)
release.set();producer.join(5);consumer.join(5)
assert not producer.is_alive() and not consumer.is_alive() and not errors
with closing(sqlite3.connect(data/'workspace.db')) as conn:
    complete=conn.execute('SELECT COUNT(*) FROM candidate').fetchone()[0]
assert complete==4 and governance.is_source_analyzed(ident) is True
print('Partial proposals: 3; legacy analyzed predicate: True; fixed predicate: False')
print('Scanner waited for owner release; final proposals: 4; completion record: complete; PASS')
