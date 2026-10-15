"""Local HTTP boundary; the portable core does not import FastAPI."""
import json
import uuid
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from . import storage, dossier
from .budget import bounded_context

router = APIRouter()


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
    from backend.governance.committer import create_direct_user_approved_change,commit_approved_change
    with storage.connect() as conn:
        card=conn.execute("SELECT * FROM pm_family_decision WHERE id=? AND status='pending'",(decision_id,)).fetchone()
        if not card:
            raise HTTPException(409,'待决事项已变化')
        sid=card['source_version_id']
    _,cid=create_direct_user_approved_change('pm_version','rule',
        {**body.model_dump(),'source_version_id':sid,'decision_id':decision_id},[sid])
    result=commit_approved_change(cid)
    if result['result']!='success':
        raise HTTPException(409,result.get('error','裁定失败'))
    return {**result,'change_id':cid}


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
def get_source(source_version_id: str):
    try:
        with storage.connect() as conn:
            return storage.source(conn, source_version_id)
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
