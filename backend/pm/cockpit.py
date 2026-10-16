"""Local view-model assembly. Read-only: page requests never call a model."""
import json
from pathlib import Path
from datetime import datetime,timedelta,timezone
from . import storage,dossier,receipts,updates

TZ=timezone(timedelta(hours=8))


def stamp(value):
    if not value:return None
    try:
        dt=datetime.fromisoformat(value.replace('Z','+00:00'))
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(TZ).isoformat()
    except (ValueError,TypeError):return None


def day(value):return stamp(value)[:10] if stamp(value) else None


def table(conn,name):return bool(conn.execute('SELECT 1 FROM sqlite_master WHERE name=?',(name,)).fetchone())


def source(conn,sid,excerpt=None,href=None):
    row=conn.execute('SELECT * FROM pm_source WHERE source_version_id=?',(sid,)).fetchone()
    if not row:return None
    return {'id':sid,'title':row['title'],'kind':'capture' if row['kind']=='capture' else 'other',
            'href':href or '/v1/pm/sources/'+sid,'updated_at':stamp(row['created_at']),
            'excerpt':(excerpt or row['body'])[:200]}


def sources(conn,refs):return [s for sid in refs if isinstance(sid,str) and (s:=source(conn,sid))]


def inbox(conn):
    projects={r['id']:r['name'] for r in conn.execute('SELECT id,name FROM project')}
    result=[]
    def add(kind,row,question,yes,no,pid,refs,impact='medium'):
        result.append({'id':kind+':'+row['id'],'question':question,'if_yes':yes,'if_no':no,
            'impact':impact,'project_id':pid,'project_name':projects.get(pid),'created_at':stamp(row['created_at']),
            'due_at':None,'sources':sources(conn,refs)})
    # No arbitrary page limit: count and list represent the same full pending set.
    for raw in conn.execute("SELECT * FROM candidate WHERE status='pending' ORDER BY created_at,id"):
        row=dict(raw);proposal=json.loads(row['proposed_change'] or '{}');payload=proposal.get('payload',{})
        title=payload.get('title') or payload.get('name') or row.get('raw_excerpt') or proposal.get('target_type','条目')
        add('candidate',row,'是否批准：'+str(title)[:160]+'？',
            '按所列提议的内容和操作写入正式工作区；来源和审核会保留。','拒绝这条提议，正式记录保持不变。',
            payload.get('project_id') or payload.get('id'),json.loads(row['source_refs'] or '[]'))
    for raw in conn.execute("SELECT d.*,s.title FROM pm_family_decision d JOIN pm_source s ON s.source_version_id=d.source_version_id WHERE d.status='pending'"):
        row=dict(raw);kind='assignment' if row['project_id'] is None else 'version'
        add(kind,row,('确认归属：' if kind=='assignment' else '确认文档族与版本：')+row['title']+'？',
            '先选择已有项目和文档族，再确认归属并执行版本及栏目规则。','暂不归入，来源保留，正式记录保持不变。',row['project_id'],[row['source_version_id']])
    conflicts={r['assessment_id']:r for r in updates.read_conflicts(conn) if r['status']=='open'}
    for raw in conn.execute("SELECT * FROM pm_section_assessment WHERE status IN ('needs_user','conflict')"):
        row=dict(raw)
        if row['status']=='conflict' and row['id'] not in conflicts:continue
        conflict=conflicts.get(row['id'])
        add('assessment',row,('裁决冲突：' if conflict else '确认栏目结论：')+row['text'][:140]+'？',
            '采纳新资料，替换该栏目的当前结论。','保留当前结论，拒绝这条新结论。',row['project_id'],
            [conflict['left_source_id'],row['source_version_id']] if conflict else [row['source_version_id']],'high' if conflict else 'medium')
    return result


def project_status(row,has_content,conflicts):
    if conflicts:return 'at_risk'
    return {'completed':'done','done':'done','paused':'paused','blocked':'blocked'}.get(row['status'],'active' if has_content else 'empty')


def receipt_vm(row):
    keys=('id','kind','title','project_id','project_name','href','updated_sections','note')
    return {**{k:row[k] for k in keys},'received_at':stamp(row['received_at']),
            'state':row['state'] if row['state']!='rejected' else 'pending'}


def project_vm(conn,pid):
    data=dossier.read(conn,pid);p=data['project'];entries={};changes={};all_sources={}
    section_titles={s['id']:s['title'] for s in data['sections']}
    current_ids={s['current']['id'] for s in data['sections'] if s['current']}
    for section in data['sections']:
        entries[section['id']]=[]
        if not section['current']:
            original='\n'.join(str(p.get(field)) for field in section.get('project_fields',[]) if p.get(field))
            if original:
                ref={'id':'project:'+pid,'title':'已有项目记录','kind':'other','href':'/app/projects/'+pid,
                    'updated_at':stamp(p.get('updated_at')),'excerpt':original[:200]}
                entries[section['id']]=[{'id':'legacy:'+pid+':'+section['id'],'text':original,'kind':'fact',
                    'sources':[ref],'updated_at':stamp(p.get('updated_at')),
                    'tag':{'text':'已有正式记录 · 原审批见旧档案','tone':'neutral'}}]
        for rev in ([section['current']] if section['current'] else [])+section['history']:
            refs=[source(conn,r['source_version_id'],r['text'],r['source_url']) for r in rev['source_refs']]
            refs=[r for r in refs if r] or sources(conn,[rev['source_version_id']])
            for ref in refs:all_sources[ref['id']]=ref
            previous=conn.execute('SELECT text FROM pm_section_revision WHERE id=?',(rev['previous_id'],)).fetchone()
            undone=bool(rev['withdrawn']);cid=rev['change_id']
            if cid not in changes:
                # One marker per committed change, even when it changed several columns.
                related=conn.execute('SELECT id,section_id,text,previous_id,withdrawn FROM pm_section_revision WHERE change_id=?',(cid,)).fetchall()
                reversible=all(r['id'] in current_ids for r in related) and not undone
                changes[cid]={'id':cid,'at':stamp(rev['created_at']),'section_key':section['id'],
                    'title':(refs[0]['title'] if refs else '核准输入')+' → '+'、'.join(dict.fromkeys(section_titles.get(r['section_id'],r['section_id']) for r in related)),
                    'before':previous[0] if previous else None,'after':rev['text'],'source':refs[0] if refs else None,
                    'revertible':reversible,'reverted':undone}
            if section['current'] and rev['id']==section['current']['id']:
                entries[section['id']]=[{'id':rev['id'],'text':rev['text'],'kind':'fact','sources':refs,
                    'updated_at':stamp(rev['created_at']),'history':[{'at':stamp(h['created_at']),'text':h['text'],
                        'source':source(conn,h['source_version_id'])} for h in section['history']],
                    'revert_change_id':cid if changes[cid]['revertible'] else None,
                    'tag':{'text':rev['authorization']['label'],'tone':'neutral'}}]
    # Approved tasks are execution milestones. Never extract guessed dates from prose.
    milestones=[]
    if table(conn,'todo'):
        for raw in conn.execute("SELECT * FROM todo WHERE project_id=? AND status NOT IN ('deleted','cancelled') ORDER BY created_at,id",(pid,)):
            t=dict(raw);start=day(t.get('start_at'));end=day(t.get('due_at'));done=t['status'] in ('done','completed')
            ref={'id':'todo:'+t['id'],'title':t['title'],'kind':'todo','href':'/app/todos','updated_at':stamp(t['updated_at']),'excerpt':(t.get('description') or t['title'])[:200]}
            all_sources[ref['id']]=ref
            milestones.append({'id':t['id'],'title':t['title'],'plan_start':start,'plan_end':end,
                'actual_start':None,'actual_end':day(t.get('completed_at')) if done else None,
                'status':'done' if done else 'in_progress' if t['status']=='in_progress' else 'not_started','source':ref,'note':'来自已批准的项目待办'})
            if not start and not end:
                gap={'id':'date-gap:'+t['id'],'text':t['title']+'：日期待补','kind':'fact','sources':[ref],'updated_at':stamp(t['updated_at'])}
                for s in data['sections']:
                    if s.get('view_kind')=='gaps' or s['id']=='gaps':entries[s['id']].append(gap)
    metrics=[]
    if table(conn,'knowledge_page') and table(conn,'knowledge_project_link'):
        for k in conn.execute('SELECT k.* FROM knowledge_page k JOIN knowledge_project_link l ON l.knowledge_id=k.id WHERE l.project_id=?',(pid,)):
            ref={'id':'knowledge:'+k['id'],'title':k['title'],'kind':'knowledge','href':'/app/knowledge/'+k['id'],
                'updated_at':stamp(k['updated_at']),'excerpt':k['one_line_summary'][:200]}
            all_sources[ref['id']]=ref
    if table(conn,'metric_definition') and table(conn,'metric_observation'):
        for raw in conn.execute("SELECT * FROM metric_definition WHERE project_id=? AND status!='deprecated'",(pid,)):
            m=dict(raw);observations=[dict(o) for o in conn.execute('SELECT * FROM metric_observation WHERE metric_definition_id=? ORDER BY observed_at DESC LIMIT 12',(m['id'],))]
            metrics.append({'id':m['id'],'name':m['name'],'unit':m['unit'],
                'current':{'value':observations[0]['value'],'period':None,'as_of':day(observations[0]['observed_at'])} if observations else None,
                'target':{'value':m['target_value'],'period':None} if m['target_value'] is not None else None,
                'trend':[{'period':o['observed_at'],'value':o['value']} for o in reversed(observations)],
                'higher_is_better':True if m['direction']=='higher_better' else False if m['direction']=='lower_better' else None,'source':None})
    titles={s['id']:s['title'] for s in data['sections']}
    conflicts=[]
    for c in updates.read_conflicts(conn,pid):
        if c['status']=='stale':continue
        resolution=conn.execute('SELECT decision,created_at FROM pm_update_resolution WHERE change_id=?',(c['resolved_by'],)).fetchone()
        conflicts.append({'id':c['id'],'topic':titles.get(c['section_id'],c['section_id']),
            'left':{'label':'当前结论','text':c['left_text'],'source':source(conn,c['left_source_id'])},
            'right':{'label':'另一份有效资料','text':c['right_text'],'source':source(conn,c['right_source_id'])},
            'options':[{'key':'current','label':'保留当前'},{'key':'incoming','label':'采纳新资料'},{'key':'later','label':'稍后'}],
            'resolved':{'choice':resolution['decision'],'at':stamp(resolution['created_at'])} if resolution else None})
    view_kinds=json.loads(Path(__file__).with_name('dossier_views.json').read_text(encoding='utf-8'))
    configured={s['id']:view_kinds.get(s['id'],'list') for s in storage.config()['sections']}
    return {'schema':'paw.pm.project/1','generated_at':stamp(storage.now()),'today':day(storage.now()),
        'project':{'id':pid,'name':p['name'],'status':project_status(p,bool(any(entries.values()) or milestones or all_sources),any(not c['resolved'] for c in conflicts)),
            'one_liner':next((e['text'] for s in data['sections'] if s['id']=='status' for e in entries[s['id']]),None),
            'updated_at':stamp(p.get('updated_at'))},
        'sections':[{'key':s['id'],'title':s['title'],'kind':configured[s['id']],'empty_text':None} for s in data['sections']],
        'entries':entries,'metrics':metrics,'milestones':milestones,'changes':sorted(changes.values(),key=lambda c:c['at'],reverse=True),
        'conflicts':conflicts,'sources':list(all_sources.values()),'receipts':[receipt_vm(r) for r in receipts.read(conn,pid)]}


def today_vm(conn):
    pending=inbox(conn);projects=[]
    for raw in conn.execute("SELECT * FROM project WHERE status NOT IN ('merged','archived','deleted') ORDER BY name,id"):
        p=dict(raw);vm=project_vm(conn,p['id']);events=[]
        for change in vm['changes']:
            events.append({'id':change['id'],'type':'new_source','date':day(change['at']),
                'title':change['title'],'href':'/app/pm/projects/'+p['id']+'#pm-change-'+change['id'],'source':change['source']})
        projects.append({**vm['project'],'start':None,'end':day(p.get('due_date')),'progress':None,
            'events':events,'href':'/app/pm/projects/'+p['id']})
    status=[{'key':k,'label':label,'state':'unknown','summary':'尚无成功记录','last_success_at':None}
        for k,label in [('patrol','巡航'),('scan','扫描'),('backup','备份'),('model','模型连通'),('disk','磁盘')]]
    return {'schema':'paw.pm.today/1','generated_at':stamp(storage.now()),'today':day(storage.now()),
        'system_status':status,'decisions':pending,'portfolio':{'projects':projects},'ai_suggestions':[],
        'receipts':[receipt_vm(r) for r in receipts.read(conn)]}


def decide(ids,choice,selections,provider):
    from backend.governance import committer
    from backend.governance.service import governance
    from .routing import resolve
    if choice not in ('yes','no','later') or not ids or len(ids)!=len(set(ids)):raise ValueError('请选择待决事项及有效操作')
    with storage.connect() as conn:live={r['id'] for r in inbox(conn)}
    if any(i not in live for i in ids):raise ValueError('待决列表已经变化，请刷新后再处理')
    from . import backups
    if choice!='later':backups.create('pre_batch_commit')
    results=[]
    for full in ids:
        kind,ident=full.split(':',1)
        try:
            if choice=='later':
                decision,_=committer.create_review_decision_atomic(ident if kind=='candidate' else None,'defer',
                    reviewer='LocalUser',review_channel='local_pm_inbox',approval_source='direct_user',original_payload={'inbox_id':full})
                result={'decision_id':decision,'status':'deferred'}
            elif kind=='candidate':
                result=governance.accept_and_commit_candidate(ident,reviewer='LocalUser') if choice=='yes' else governance.reject_candidate(ident)
            else:
                if kind in ('assignment','version') and choice=='yes':
                    selected=selections.get(full,{})
                    result=resolve(ident,selected.get('project_id'),provider,selected.get('family_id'),selected.get('family_title'))
                else:
                    target='pm_input_decision' if kind in ('assignment','version') else 'pm_update_decision'
                    payload={'decision_id':ident} if target=='pm_input_decision' else {'assessment_id':ident,'choice':'incoming' if choice=='yes' else 'reject'}
                    _,cid=committer.create_direct_user_approved_change(target,'reject' if target=='pm_input_decision' else 'resolve',payload)
                    result={**committer.commit_approved_change(cid),'change_id':cid}
                    if result['result']!='success':raise ValueError(result.get('error','写入失败'))
            results.append({'id':full,'ok':True,'result':result})
        except (ValueError,KeyError,RuntimeError) as exc:results.append({'id':full,'ok':False,'error':str(exc)})
    backup_error=None
    if choice!='later' and any(r['ok'] for r in results):
        try:backups.create('post_batch_commit')
        except Exception:backup_error='决定已提交，但完成后备份失败，请检查系统状态'
    return {'items':results,'backup_error':backup_error}
