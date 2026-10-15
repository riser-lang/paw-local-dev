"""Reviewed compatibility repair for the recovered PAW 2.3.7 runtime.

All writes still require a review decision and go through the original committer.
Project read models use stored records only; reading never calls an AI provider.
"""
from __future__ import annotations

import copy
import json
import re
import sqlite3
import sys
import threading
import uuid
from datetime import datetime, timezone

_review_lock = threading.RLock()
_installed = False


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json(value, default):
    if value is None:
        return copy.deepcopy(default)
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return copy.deepcopy(default)
    return value


def ensure_lifecycle(conn):
    columns = {r[1] for r in conn.execute('PRAGMA table_info(luna_draft_projection)')}
    if columns and 'lifecycle' not in columns:
        conn.execute("ALTER TABLE luna_draft_projection ADD COLUMN lifecycle TEXT NOT NULL DEFAULT 'ACTIVE'")


def _resolve_id(conn, table, payload, candidate=None):
    ident = payload.get('id') or payload.get('target_id') or (candidate or {}).get('target_id')
    if ident:
        if not conn.execute(f'SELECT 1 FROM {table} WHERE id=?', (str(ident),)).fetchone():
            raise ValueError('更新目标不存在，请在编辑草稿中选择正确的记录。')
        return str(ident)
    field = 'name' if table in ('project', 'metric_definition') else 'title'
    name = payload.get(field)
    matches = conn.execute(f'SELECT id FROM {table} WHERE {field}=?', (name,)).fetchall() if name else []
    if len(matches) != 1:
        raise ValueError('无法唯一确定更新目标，请在编辑草稿中填写目标 id；原有记录未改变。')
    return matches[0][0]


_PROJECT_UPDATE_ALIASES = {
    'new_summary': 'latest_progress_summary', 'update_summary': 'latest_progress_summary',
    'progress': 'latest_progress_summary', 'statement': 'latest_progress_summary',
    'current_stage': 'stage', 'next_action': 'next_action_summary', 'next_step': 'next_action_summary',
}


_PROJECT_FIELDS = ('name', 'description', 'status', 'stage', 'priority', 'due_date',
                   'next_action_summary', 'latest_progress_summary', 'project_handbook')


def _flat_project_update(conn, candidate, payload, allow_create=True):
    """Resolve the target of an AI project update.

    Flat drafts (no governance envelope) and enveloped updates whose payload
    lacks a usable id both land here. The target comes from an explicit id, an
    explicit project name, or a project name quoted verbatim in the draft or
    its source excerpt; it is never guessed. An unknown name creates a project
    only for flat drafts (allow_create); an explicit update never does.
    """
    from backend.runtime_core.project_convergence import INACTIVE_STATUSES, normalize_project_name
    for source, dest in _PROJECT_UPDATE_ALIASES.items():
        if payload.get(source) not in (None, '') and payload.get(dest) in (None, ''):
            payload[dest] = payload[source]
    excerpt = candidate.get('raw_excerpt')
    ident = payload.get('id') or payload.get('project_id') or payload.get('target_id')
    if ident:
        if not conn.execute('SELECT 1 FROM project WHERE id=?', (str(ident),)).fetchone():
            raise ValueError('要更新的项目不存在，请在编辑草稿中选择正确的项目；原有记录未改变。')
    else:
        active = [(r[0], r[1]) for r in conn.execute('SELECT id, name, status FROM project ORDER BY created_at, id')
                  if (r[2] or 'active') not in INACTIVE_STATUSES]
        name = payload.get('project_name') or payload.get('name')
        if isinstance(name, str) and name.strip():
            key = normalize_project_name(name)
            matches = [pid for pid, pname in active if normalize_project_name(pname) == key]
            if len(matches) > 1:
                raise ValueError('项目名称对应多条记录，请在编辑草稿中选择具体项目；原有记录未改变。')
            if not matches:
                if not allow_create:
                    raise ValueError(f'找不到名为“{name.strip()}”的项目，请在编辑草稿中选择正确的项目；原有记录未改变。')
                payload['name'] = name.strip()
                return 'create', payload
            ident = matches[0]
        else:
            text = ' '.join(str(v) for v in (payload.get('latest_progress_summary'), excerpt) if v)
            matches = {pid for pid, pname in active if pname and pname in text}
            if len(matches) != 1:
                raise ValueError('无法确定要更新哪个项目，请在编辑草稿中填写项目名称；原有记录未改变。')
            ident = matches.pop()
    payload['id'] = str(ident)
    # An update that names a project but carries no field records the quoted
    # source text as progress, so approving it is never a silent no-op.
    if excerpt and not any(payload.get(f) not in (None, '') for f in _PROJECT_FIELDS if f != 'name'):
        payload['latest_progress_summary'] = str(excerpt)
    return 'update', payload


def _normalize(conn, candidate, change, edited, reviewer):
    from backend.governance.service import CANDIDATE_TO_TARGET_TYPE
    from backend.governance.committer import validate_change_request
    if isinstance(change, dict) and 'payload' in change and 'target_type' in change:
        target = change['target_type']
        operation = change.get('operation') or 'create'
        payload = copy.deepcopy(change['payload'] if edited is None else edited)
    else:
        target = CANDIDATE_TO_TARGET_TYPE.get(candidate['candidate_type'], 'knowledge')
        operation = 'create'
        payload = copy.deepcopy(change if edited is None else edited)
    if not isinstance(payload, dict):
        raise ValueError('AI 提议格式错误：payload 必须是对象，请编辑草稿后重试。')
    flat = not (isinstance(change, dict) and 'payload' in change and 'target_type' in change)
    if target == 'project' and ((flat and candidate['candidate_type'] == 'project_update') or operation == 'update'):
        operation, payload = _flat_project_update(conn, candidate, payload, allow_create=flat)
    aliases = {
        'project': {'current_stage': 'stage', 'next_action': 'next_action_summary', 'summary': 'description', 'progress': 'latest_progress_summary'},
        'knowledge': {'summary': 'one_line_summary', 'body': 'content'},
        'todo': {'due_date': 'due_at'},
    }
    for source, dest in aliases.get(target, {}).items():
        if source in payload and payload.get(dest) in (None, ''):
            payload[dest] = payload[source]
    table = {'project': 'project', 'todo': 'todo', 'knowledge': 'knowledge_page', 'data': 'metric_definition'}.get(target)
    if table:
        if operation in ('update', 'archive', 'complete', 'restore'):
            payload['id'] = _resolve_id(conn, table, payload, candidate)
        # A present JSON null must not override a NOT NULL SQLite default.
        for col in conn.execute(f'PRAGMA table_info({table})'):
            name, required, default = col[1], col[3], col[4]
            if required and name in payload and payload[name] is None:
                if operation == 'create' and default is not None:
                    payload[name] = conn.execute('SELECT ' + default).fetchone()[0]
                elif operation != 'create':
                    del payload[name]
        name_field = 'name' if target in ('project', 'data') else 'title'
        if operation == 'create' and (not isinstance(payload.get(name_field), str) or not payload[name_field].strip()):
            raise ValueError('标题或项目名称不能为空，请编辑草稿后重试。')
        if 'project_name' in payload and not payload.get('project_id'):
            matches = conn.execute('SELECT id FROM project WHERE name=?', (payload['project_name'],)).fetchall()
            if len(matches) == 1:
                payload['project_id'] = matches[0][0]
            elif matches:
                raise ValueError('项目名称对应多条记录，请填写 project_id。')
        if payload.get('project_id') and not conn.execute('SELECT 1 FROM project WHERE id=?', (payload['project_id'],)).fetchone():
            raise ValueError('关联项目不存在，请编辑草稿后重试。')
    refs = _json(candidate.get('source_refs'), [])
    if not isinstance(refs, list):
        raise ValueError('来源引用必须为列表，请编辑草稿后重试。')
    if target == 'knowledge' and operation in ('create', 'update'):
        payload['_reviewed_by'] = reviewer
        payload['_source_refs'] = refs
    validate_change_request(target, operation, payload, refs)
    return target, operation, payload, refs


def accept_and_commit_candidate(self, candidate_id, reviewer='LocalUser', review_notes='', edited_payload=None):
    from backend.governance import committer
    from backend.governance.service import get_db
    from backend.runtime_core.event_bus import EventType, event_bus
    reviewer = str(reviewer or 'LocalUser')
    with _review_lock:
        conn = get_db()
        try:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM candidate WHERE id=?', (candidate_id,)).fetchone()
            if row is None:
                raise ValueError('审核项不存在。')
            candidate = dict(row)
            if candidate['status'] in ('applied', 'committed'):
                conn.rollback()
                return {'candidate_id': candidate_id, 'status': 'already_processed', 'current_status': candidate['status']}
            if candidate['status'] not in ('pending', 'accepted'):
                raise ValueError('该审核项已驳回或关闭，无法再次核准。')
            # Recover an interruption between approval and canonical commit.
            existing = conn.execute(
                "SELECT ac.* FROM approved_change ac JOIN review_decision rd ON rd.id=ac.approval_ref "
                "WHERE rd.candidate_id=? AND ac.status IN ('pending','committed') ORDER BY ac.created_at DESC LIMIT 1",
                (candidate_id,),
            ).fetchone()
            if existing:
                change_id, decision_id = existing['change_id'], existing['approval_ref']
                target, operation = existing['target_type'], existing['operation']
                conn.rollback()
            else:
                change = _json(candidate['proposed_change'], None)
                target, operation, payload, refs = _normalize(conn, candidate, change, edited_payload, reviewer)
                decision_id, change_id = str(uuid.uuid4()), str(uuid.uuid4())
                now = _now()
                decision = 'edit_and_approve' if edited_payload is not None else 'approve'
                conn.execute(
                    "INSERT INTO review_decision (id,candidate_id,decision,reviewer,review_channel,approval_source,"
                    "original_payload,reviewed_payload,authorization_payload_hash,review_notes,created_at) "
                    "VALUES (?,?,?,?,'web_review','candidate_review',?,?,?,?,?)",
                    (decision_id,candidate_id,decision,reviewer,json.dumps(change,ensure_ascii=False),
                     json.dumps(payload,ensure_ascii=False),committer._payload_hash(payload),review_notes,now),
                )
                conn.execute(
                    "INSERT INTO approved_change (change_id,target_type,operation,payload,approval_ref,source_refs,"
                    "expected_target_version,idempotency_key,created_at,status) VALUES (?,?,?,?,?,?,NULL,?,?,'pending')",
                    (change_id,target,operation,json.dumps(payload,ensure_ascii=False),decision_id,
                     json.dumps(refs,ensure_ascii=False),f'candidate-{candidate_id}-review-{decision_id}',now),
                )
                conn.execute("UPDATE candidate SET status='accepted',reviewed_at=?,reviewed_by=? WHERE id=?",
                             (now,reviewer,candidate_id))
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        try:
            result = committer.commit_approved_change(change_id)
        except Exception:
            with get_db() as conn:
                conn.execute("UPDATE candidate SET status='pending' WHERE id=? AND status='accepted'", (candidate_id,))
            raise
        if result.get('result') != 'success':
            with get_db() as conn:
                conn.execute("UPDATE candidate SET status='pending' WHERE id=? AND status='accepted'", (candidate_id,))
            raise RuntimeError('审核写入失败，可编辑后重试：' + str(result.get('error') or '未知错误'))
        target_id = result.get('target_id')
        with get_db() as conn:
            if target_id is None:
                receipt = conn.execute("SELECT target_id FROM audit_event WHERE change_id=? AND event_type='commit' ORDER BY created_at DESC LIMIT 1", (change_id,)).fetchone()
                target_id = receipt[0] if receipt else None
            if target_id is None:
                raise RuntimeError('提交收据缺少目标标识，请保留此审核项并检查审计记录。')
            conn.execute("UPDATE candidate SET status='applied',applied_entity_type=?,applied_entity_id=? WHERE id=?",
                         (target,str(target_id),candidate_id))
        event_bus.publish(EventType.COMMITTED, payload={
            'candidate_id':candidate_id,'target_type':target,'target_id':str(target_id),
            'commit_id':result.get('commit_id'),'operation':operation,'reviewer':reviewer,'timestamp':_now(),
        }, source='committer')
        return {'candidate_id':candidate_id,'status':'applied','decision_id':decision_id,
                'approved_change_id':change_id,'commit_id':result.get('commit_id'),
                'target_id':target_id,'target_type':target,'operation':operation}


def _link_reviewed_knowledge(conn, knowledge_id, payload):
    from backend.governance import committer
    if payload.get('project_id'):
        committer._execute_knowledge_project_link(conn, {
            'knowledge_id':knowledge_id,'project_id':payload['project_id'],'relation_type':'primary',
        })
    for ref in payload.get('_source_refs') or []:
        sid = ref.get('source_id') or ref.get('source_page_id') if isinstance(ref, dict) else ref
        if isinstance(sid, str) and conn.execute('SELECT 1 FROM wiki_page WHERE id=?',(sid,)).fetchone():
            conn.execute("INSERT OR IGNORE INTO knowledge_source_link "
                         "(knowledge_id,source_page_id,source_role,created_at) VALUES (?,?,'supporting',?)",
                         (knowledge_id,sid,_now()))
    if payload.get('_reviewed_by'):
        revision = conn.execute('SELECT MAX(revision_no) FROM knowledge_revision WHERE knowledge_id=?',(knowledge_id,)).fetchone()[0]
        conn.execute("UPDATE knowledge_page SET status='current',confirmed_revision_no=?,confirmed_at=?,confirmed_by=? WHERE id=?",
                     (revision,_now(),payload['_reviewed_by'],knowledge_id))


def _stored_explainer(project, todos=(), pending=(), previous=None):
    previous = previous if isinstance(previous, dict) else {}
    stages = {'planning':'计划中','executing':'执行中','reviewing':'复盘中','done':'已完成','completed':'已完成'}
    next_todo = next((t for t in todos if t.get('status') not in ('done','cancelled','archived')), None)
    return {
        **previous,
        'one_sentence':previous.get('one_sentence') or project.get('description') or f"{project['name']}：项目背景与目标尚未记录。",
        'current_state':previous.get('current_state') or project.get('latest_progress_summary') or stages.get(project.get('stage'),project.get('stage')) or '进展尚未记录。',
        'key_open_issue':previous.get('key_open_issue') or (pending[0].get('title') if pending else None) or '关键问题尚未记录。',
        'next_step':previous.get('next_step') or project.get('next_action_summary') or (next_todo.get('title') if next_todo else None) or '下一步尚未记录。',
    }


def _local_handbook(context, explainer):
    p = context['project']
    todos, pending = context.get('todos',[]), context.get('pending',[])
    knowledge = context.get('knowledge',[])
    open_todos = [t for t in todos if t.get('status') not in ('done','cancelled','archived')]
    completed = [t for t in todos if t.get('status') == 'done']
    handbook = {
        'project_explainer':explainer,
        'why_and_background':p.get('description') or '项目背景尚未记录。',
        'project_goals':'具体目标尚未记录；请补充项目说明或审核相关文档提议。',
        'scope':[{'title':k['title'],'text':k.get('one_line_summary') or ''} for k in knowledge],
        'out_of_scope':[], 'how_it_works':[],
        'historical_evolution':[], 'historical_key_decisions':context.get('decisions',[]),
        'data_evolution':[], 'current_effective_rules':[{'title':k['title'],'text':k.get('one_line_summary') or ''} for k in knowledge if k.get('knowledge_type')=='rule'],
        'current_phase_and_status':explainer['current_state'],
        'latest_progress':p.get('latest_progress_summary') or '最新进展尚未记录。',
        'completed_milestones':completed, 'current_todos_and_open_items':open_todos+pending,
        'risks_blockers_ambiguities':pending, 'cross_project_relations':[],
        'next_actions':[{'text':t['title']} for t in open_todos] or p.get('next_action_summary') or [],
        'future_direction':[], 'evidence_index':context.get('sources',[]),
        '_provenance':{'note':'由已有项目登记、已审核知识及待办汇总；尚未记录的内容显示为空。'},
    }
    # Earlier executors persisted only eight project fields. Recover additional
    # explicitly reviewed detail from successful approval receipts, not proposals.
    from backend.workspace_api.db import connect
    with connect() as conn:
        receipts = conn.execute(
            "SELECT ac.payload FROM approved_change ac JOIN audit_event ae ON ae.change_id=ac.change_id "
            "WHERE ac.target_type='project' AND ac.status='committed' AND ae.event_type='commit' "
            "AND ae.target_id=? ORDER BY ac.created_at", (p['id'],),
        ).fetchall()
    aliases = {'goals':'project_goals','objectives':'project_goals','background':'why_and_background',
               'operating_model':'how_it_works','risks':'risks_blockers_ambiguities','future_plan':'future_direction'}
    for receipt in receipts:
        payload = _json(receipt[0],{})
        if not isinstance(payload,dict):
            continue
        for key,value in payload.items():
            key = aliases.get(key,key)
            if key in handbook and key not in ('_provenance','project_explainer','evidence_index') and value not in (None,'',[],{}):
                handbook[key] = value
    return handbook


def _replace_references(old, new):
    for name, module in list(sys.modules.items()):
        if not name.startswith('backend.') or module is None:
            continue
        for key, value in list(vars(module).items()):
            if value is old:
                setattr(module,key,new)


def install():
    global _installed
    if _installed:
        return
    _installed = True
    from backend.governance import service, committer
    from backend.workspace_api import db, repository
    from backend.core.config import DB_PATH
    service.GovernanceService.accept_and_commit_candidate = accept_and_commit_candidate
    original_migrate = db.migrate
    def migrate():
        if DB_PATH.exists():
            with sqlite3.connect(DB_PATH) as conn:
                columns = {r[1] for r in conn.execute('PRAGMA table_info(luna_draft_projection)')}
                backup = DB_PATH.parent / 'backups' / 'workspace.pre-v2.3.8.db'
                if columns and 'lifecycle' not in columns and not backup.exists():
                    backup.parent.mkdir(parents=True,exist_ok=True)
                    with sqlite3.connect(backup) as dest:
                        conn.backup(dest)
        result = original_migrate()
        with sqlite3.connect(DB_PATH) as conn:
            ensure_lifecycle(conn)
        return result
    _replace_references(original_migrate,migrate)
    migrate()
    # The raw draft module creates an index before its old-table migration.
    import paw_luna_draft_store as draft_store
    original_ensure = draft_store.ensure_schema
    def ensure_schema(db_path=None):
        with draft_store.connect(db_path) as conn:
            ensure_lifecycle(conn)
        return original_ensure(db_path)
    draft_store.ensure_schema = ensure_schema
    for name in ('_execute_knowledge_create','_execute_knowledge_update'):
        original = getattr(committer,name)
        def execute(conn,payload,_original=original):
            ident = _original(conn,payload)
            _link_reviewed_knowledge(conn,ident,payload)
            return ident
        # The executor dispatch dictionary holds references, too.
        setattr(committer,name,execute)
        for value in vars(committer).values():
            if isinstance(value,dict):
                for key,executor in list(value.items()):
                    if executor is original:
                        value[key] = execute
    original_context = repository.project_context
    def project_context(project_id):
        context = original_context(project_id)
        if context is None:
            return None
        for todo in context.get('todos',[]):
            if todo.get('project_id') == context['project']['id']:
                todo['project_name'] = context['project']['name']
        explainer = _stored_explainer(context['project'],context.get('todos',[]),context.get('pending',[]),
                                    context.get('project_explainer') or context.get('explainer'))
        context['project_explainer'] = context['explainer'] = explainer
        context['project']['explainer'] = explainer
        if not context.get('project_handbook'):
            context['project_handbook'] = _local_handbook(context,explainer)
            context['handbook_quality_gate'] = 'PARTIAL'
            context['handbook_metadata'] = {'authority':'stored_record_read_model','formal_state':False}
        return context
    _replace_references(original_context,project_context)
    original_list = repository.list_projects
    def list_projects(limit=100):
        result = original_list(limit)
        items = result.get('items',[]) if isinstance(result,dict) else result
        for item in items:
            item['explainer'] = _stored_explainer(item,previous=item.get('explainer'))
        return result
    _replace_references(original_list,list_projects)
    # Count only new commits; repeated clicks do not create duplicate receipts.
    from backend.runtime_core.scanner import scanner
    scanner_class = type(scanner)
    def batch_accept(self,candidate_ids,reviewer='LocalUser'):
        accepted,already,failed = [],[],[]
        if candidate_ids:
            from backend.runtime_core import scanner as scanner_module
            scanner_module.create_db_backup(tag='pre_batch_commit')
        for cid in dict.fromkeys(candidate_ids):
            try:
                result = service.governance.accept_and_commit_candidate(cid,reviewer=reviewer,review_notes='用户批量核准写入')
                (already if result['status']=='already_processed' else accepted).append(cid)
            except Exception as exc:
                failed.append({'id':cid,'error':str(exc)})
        return {'ok':not failed,'accepted_count':len(accepted),'commit_records_created':len(accepted),
                'failed_count':len(failed),'accepted_ids':accepted,'already_processed_ids':already,'failed_details':failed}
    scanner_class.batch_accept = batch_accept
    from backend.main import app
    from fastapi.responses import RedirectResponse
    from urllib.parse import quote
    # The original bare page aliases cover list pages but omit detail URLs.
    for prefix in ('projects','knowledge','files','sources'):
        def redirect_detail(path:str,_prefix=prefix):
            return RedirectResponse('/app/'+_prefix+'/'+quote(path,safe='/'))
        app.add_api_route('/'+prefix+'/{path:path}',redirect_detail,methods=['GET'],include_in_schema=False)
