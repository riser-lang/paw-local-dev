"""Local HTTP boundary; the portable core does not import FastAPI."""
import json
import uuid
from datetime import datetime
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from . import storage, dossier
from .budget import bounded_context

router = APIRouter()


@router.get('/suggestions')
def suggestion_list(project_id: str | None = None):
    from .suggestions import read
    with storage.connect() as conn:return {'items':read(conn,project_id)}


class GenerateAdvice(BaseModel):
    force: bool = True


@router.post('/projects/{project_id}/suggestions/generate')
def generate_advice(project_id: str,body: GenerateAdvice):
    from .suggestions import generate
    from backend.provider.adapter import get_active_provider
    try:return generate(project_id,get_active_provider(),body.force)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.post('/suggestions/{suggestion_id}/adopt')
def adopt_advice(suggestion_id: str):
    from .suggestions import adopt
    from backend.governance.service import governance
    try:return adopt(suggestion_id,governance.create_candidate)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/view/today')
def today_view(health_run: str | None = None):
    from .cockpit import today_vm
    with storage.connect() as conn:
        vm=today_vm(conn,health_run)
        vm['system_status']=local_health_items(conn)
        return vm


def local_health_items(conn):
    from . import health
    from backend.core.config import DATA_DIR,settings
    return health.read(conn,DATA_DIR,health.identity(settings.get_active_credentials()))


@router.get('/system/status')
def system_status():
    with storage.connect() as conn:return {'items':local_health_items(conn)}


@router.get('/backups')
def backup_status():
    from . import backups
    return {'items':backups.listing(),'policy':backups.policy()}


class BackupPolicy(BaseModel):
    max_count: int = Field(ge=1,le=100)
    max_bytes: int = Field(ge=65536)


@router.post('/backups/policy')
def backup_policy(body: BackupPolicy):
    from .backups import set_policy
    try:return set_policy(body.max_count,body.max_bytes)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.post('/backups/selfcheck')
def backup_selfcheck():
    from .backups import selfcheck
    try:return selfcheck()
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/view/projects/{project_id}')
def project_view(project_id: str,health_run: str | None = None):
    from .cockpit import project_vm
    try:
        with storage.connect() as conn:return project_vm(conn,project_id,health_run)
    except ValueError as exc:raise HTTPException(404,str(exc)) from exc


class InboxChoice(BaseModel):
    ids: list[str] = Field(min_length=1,max_length=100)
    choice: str
    assignments: dict = Field(default_factory=dict)


class HealthRequest(BaseModel):
    project_id: str | None = None
    reference_at: datetime | None = None
    force: bool = False


@router.post('/health-checks/run')
def run_health_checks(body: HealthRequest):
    from . import daily
    from backend.provider.adapter import get_active_provider
    with storage.connect() as conn:
        ids=[body.project_id] if body.project_id else [r['id'] for r in conn.execute('''SELECT p.id FROM project p
          LEFT JOIN (SELECT project_id,MAX(created_at) AS checked FROM pm_daily_check WHERE preview=0 GROUP BY project_id) c ON c.project_id=p.id
          WHERE p.status NOT IN ('merged','archived','deleted') ORDER BY c.checked,p.id LIMIT 10''')]
    try:return {'items':[daily.run(pid,get_active_provider(),body.reference_at,body.force) for pid in ids],'limit':10}
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/knowledge-gaps')
def knowledge_gap_list(project_id: str | None = None):
    from .knowledge_gaps import read
    with storage.connect() as conn:return {'items':read(conn,project_id)}


@router.post('/knowledge-gaps/{gap_id}/resolve')
def resolve_knowledge_gap(gap_id: str):
    with storage.connect() as conn:
        if not conn.execute('SELECT 1 FROM pm_knowledge_gap WHERE id=?',(gap_id,)).fetchone():raise HTTPException(404,'知识缺口不存在')
        conn.execute("UPDATE pm_knowledge_gap SET state='resolved' WHERE id=?",(gap_id,))
    return {'id':gap_id,'state':'resolved'}


@router.post('/inbox/decide')
def decide_inbox(body: InboxChoice):
    from .cockpit import decide
    from backend.provider.adapter import get_active_provider
    try:return decide(body.ids,body.choice,body.assignments,get_active_provider())
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/receipts')
def input_receipts():
    from .receipts import read
    from backend.provider.adapter import get_active_provider
    with storage.connect() as conn:items=read(conn)
    return {'items':items,'model_configured':bool(getattr(get_active_provider(),'api_key',None))}


@router.get('/input-decisions')
def input_decisions():
    from .routing import pending
    with storage.connect() as conn:return {'items':pending(conn)}


class AssignmentChoice(BaseModel):
    project_id: str
    family_id: str | None = None
    family_title: str | None = None


@router.post('/input-decisions/{decision_id}/resolve')
def resolve_assignment(decision_id: str,body: AssignmentChoice):
    from .routing import resolve
    from backend.provider.adapter import get_active_provider
    try:return resolve(decision_id,body.project_id,get_active_provider(),body.family_id,body.family_title)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.post('/input-decisions/{decision_id}/reject')
def reject_assignment(decision_id: str):
    from backend.governance.committer import create_direct_user_approved_change,commit_approved_change
    _,change=create_direct_user_approved_change('pm_input_decision','reject',{'decision_id':decision_id})
    result=commit_approved_change(change)
    if result['result']!='success':raise HTTPException(409,result.get('error','拒绝失败'))
    return {**result,'change_id':change}


@router.get('/coverage')
def coverage_report():
    from .coverage import read
    with storage.connect() as conn:return {'items':read(conn)}


@router.get('/projects/{project_id}/assessments')
def assessments(project_id: str):
    with storage.connect() as conn:
        return {'items':[dict(r) for r in conn.execute('SELECT * FROM pm_section_assessment WHERE project_id=? ORDER BY created_at DESC',(project_id,))]}


@router.post('/projects/{project_id}/sources/{source_id}/assess')
def assess_source(project_id: str,source_id: str):
    from .updates import process_source
    from backend.provider.adapter import get_active_provider
    with storage.connect() as conn:
        member=conn.execute('SELECT f.project_id FROM pm_family_member m JOIN pm_family f ON f.id=m.family_id WHERE m.source_version_id=?',(source_id,)).fetchone()
        if not member or member[0]!=project_id:raise HTTPException(409,'先确认项目和文档族归属')
    return process_source(source_id,get_active_provider())


@router.get('/projects/{project_id}/conflicts')
def conflicts(project_id: str):
    from .updates import read_conflicts
    with storage.connect() as conn:return {'items':read_conflicts(conn,project_id)}


class UpdateChoice(BaseModel):
    choice: str


@router.post('/assessments/{assessment_id}/resolve')
def resolve_update(assessment_id: str,body: UpdateChoice):
    from backend.governance.committer import create_direct_user_approved_change,commit_approved_change
    _,cid=create_direct_user_approved_change('pm_update_decision','resolve',{'assessment_id':assessment_id,'choice':body.choice})
    result=commit_approved_change(cid)
    if result['result']!='success':raise HTTPException(409,result.get('error','裁决失败'))
    return {**result,'change_id':cid}


@router.get('/reread')
def reread_jobs():
    with storage.connect() as conn:return {'items':[dict(r) for r in conn.execute('SELECT * FROM pm_reread_job ORDER BY created_at DESC')]}


class RereadLimit(BaseModel):
    limit: int = Field(default=5,ge=5,le=10)


@router.post('/reread/run')
def run_reread(body: RereadLimit):
    from .reread import run
    return run(body.limit)


@router.get('/projects/{project_id}/families')
def families(project_id: str):
    from .versions import read
    with storage.connect() as conn:
        return {'items':read(conn,project_id)}


@router.get('/decisions')
def family_decisions():
    with storage.connect() as conn:
        return {'items':[dict(r) for r in conn.execute("SELECT * FROM pm_family_decision WHERE status='pending' ORDER BY created_at")]}


class FamilyChoice(BaseModel):
    project_id: str
    family_id: str | None = None
    family_title: str = Field(min_length=1,max_length=300)


@router.post('/decisions/{decision_id}/resolve')
def resolve_family(decision_id: str, body: FamilyChoice):
    from .routing import resolve
    from backend.provider.adapter import get_active_provider
    try:return resolve(decision_id,body.project_id,get_active_provider(),body.family_id,body.family_title)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


class SectionProposal(BaseModel):
    section_id: str
    text: str = Field(min_length=1, max_length=50000)
    source_version_id: str


@router.get('/projects/{project_id}')
def get_dossier(project_id: str):
    try:
        with storage.connect() as conn:
            return dossier.read(conn, project_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get('/projects/{project_id}/context')
def context(project_id: str):
    try:
        with storage.connect() as conn:
            result = bounded_context(dossier.read(conn, project_id), storage.config()['context_budget_bytes'])
        return Response(storage.encoded(result).encode('utf-8'), media_type='application/json')
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/sources/{source_version_id}')
def get_source(source_version_id: str,extraction: str | None = None):
    try:
        with storage.connect() as conn:
            return storage.source(conn, source_version_id,extraction)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get('/projects/{project_id}/sources')
def project_sources(project_id: str):
    with storage.connect() as conn:
        rows = conn.execute('SELECT DISTINCT s.source_version_id,s.title,s.kind FROM pm_source s JOIN pm_section_revision r ON r.source_version_id=s.source_version_id WHERE r.project_id=?', (project_id,)).fetchall()
        return {'items': [{**dict(r), 'url': '/v1/pm/sources/' + r['source_version_id']} for r in rows]}


@router.post('/projects/{project_id}/sections/propose')
def propose_section(project_id: str, body: SectionProposal):
    from backend.governance.service import governance
    with storage.connect() as conn:
        try:
            dossier.read(conn, project_id)
            evidence = storage.source(conn, body.source_version_id)
            if body.section_id not in {s['id'] for s in storage.config()['sections']}:
                raise ValueError('未知栏目')
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    cid = governance.create_candidate('pm_section', 'update',
        {'project_id': project_id, **body.model_dump()}, raw_excerpt=evidence['body'],
        source_refs=[body.source_version_id], analysis_model='user:section_proposal')
    return {'candidate_id': cid, 'status': 'pending_review'}


@router.post('/projects/{project_id}/derived/{draft_id}/adopt')
def adopt(project_id: str, draft_id: str):
    from backend.governance.service import governance
    with storage.connect() as conn:
        row = conn.execute('SELECT * FROM pm_ai_explainer WHERE id=? AND project_id=?', (draft_id, project_id)).fetchone()
        if row is None:
            raise HTTPException(404, '派生说明不存在')
        value = json.loads(row['content_json'])
    cid = governance.create_candidate('project', 'update',
        {'id': project_id, 'latest_progress_summary': value['current_state'], 'next_action_summary': value['next_step']},
        raw_excerpt=storage.encoded(value), source_refs=['ai-' + draft_id],
        analysis_model='user:adopt_ai_explainer', review_brief={'summary': '采纳 AI 生成说明；核准后才会成为正式项目记录'})
    return {'candidate_id': cid, 'status': 'pending_review'}


@router.post('/changes/{change_id}/undo')
def undo(change_id: str):
    from backend.governance.committer import create_direct_user_approved_change, commit_approved_change
    _, approved = create_direct_user_approved_change('pm_undo', 'restore', {'change_id': change_id})
    result = commit_approved_change(approved)
    if result['result'] != 'success':
        raise HTTPException(409, result.get('error', '撤销失败'))
    return result


@router.post('/projects/{project_id}/synthesize')
def synthesize(project_id: str):
    from backend.provider.adapter import get_active_provider
    from .semantic import invoke
    with storage.connect() as conn:
        data = dossier.read(conn, project_id)
        ids = {r['source_version_id'] for s in data['sections'] for r in ([s['current']] if s['current'] else [])}
        value = {'project_id': project_id, 'sections': data['sections'], 'sources': [storage.source(conn, i) for i in sorted(ids)]}
        result = invoke('dossier_synthesis', value, get_active_provider(), conn)
        if result['status'] == 'draft':
            allowed = {s['id'] for s in data['sections']}
            if any(s['source_version_id'] not in ids or s['section_id'] not in allowed for s in result['output']['sections']):
                storage.rejection(conn, 'dossier_synthesis', 'output_reference_invalid')
                return {'status': 'rejected', 'message': '输出来源或栏目无效'}
            conn.execute('INSERT INTO pm_semantic_draft VALUES(?,?,?,?,?)',
                         (str(uuid.uuid4()), project_id, 'dossier_synthesis', storage.encoded(result['output']), storage.now()))
        return result
