"""Local view-model assembly. Read-only: page requests never call a model."""
import json
import sqlite3
from pathlib import Path
from datetime import datetime,timedelta,timezone
from . import storage,dossier,receipts,updates,decision_copy

TZ=timezone(timedelta(hours=8))


def stamp(value):
    if not value:return None
    try:
        dt=datetime.fromisoformat(value.replace('Z','+00:00'))
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(TZ).isoformat()
    except (ValueError,TypeError):return None


def day(value):return stamp(value)[:10] if stamp(value) else None


def table(conn,name):return bool(conn.execute('SELECT 1 FROM sqlite_master WHERE name=?',(name,)).fetchone())


def _retired_source_title(conn,source_refs):
    """Title of the source a retired proposal came from, when it can be read."""
    try:
        refs=json.loads(source_refs or '[]')
        refs=refs.get('refs',[]) if isinstance(refs,dict) else refs
        if not refs:return None
        row=conn.execute('SELECT title FROM pm_source WHERE source_version_id=?',(str(refs[0]),)).fetchone()
        return row[0] if row else None
    except (ValueError,TypeError,sqlite3.Error):
        return None


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
    def add(kind,row,question,yes,no,pid,refs,impact='medium',excerpt=None):
        result.append({'id':kind+':'+row['id'],'question':question,'if_yes':yes,'if_no':no,
            'impact':impact,'project_id':pid,'project_name':projects.get(pid),'created_at':stamp(row['created_at']),
            'due_at':None,'sources':sources(conn,refs),'excerpt':excerpt})
    if table(conn, 'candidate'):
        for raw in conn.execute("SELECT * FROM candidate WHERE status='pending' ORDER BY created_at,id"):
            row=dict(raw);proposal=json.loads(row['proposed_change'] or '{}');payload=proposal.get('payload',{})
            copy=decision_copy.candidate(proposal,projects,row.get('raw_excerpt'))
            add('candidate',row,copy['question'],copy['if_yes'],copy['if_no'],copy['project_id'],
                json.loads(row['source_refs'] or '[]'),excerpt=row.get('raw_excerpt') or copy['excerpt'])
    for raw in conn.execute("SELECT d.*,s.title FROM pm_family_decision d JOIN pm_source s ON s.source_version_id=d.source_version_id WHERE d.status='pending'"):
        row=dict(raw);kind='assignment' if row['project_id'] is None else 'version'
        add(kind,row,('确认归属：' if kind=='assignment' else '确认文档族与版本：')+row['title']+'？',
            '先选择已有项目和文档族，再确认归属并执行版本及栏目规则。','暂不归入，来源保留，正式记录保持不变。',row['project_id'],[row['source_version_id']])
    conflicts={r['assessment_id']:r for r in updates.read_conflicts(conn) if r['status']=='open'}
    for raw in conn.execute("SELECT * FROM pm_section_assessment WHERE status IN ('needs_user','conflict')"):
        row=dict(raw)
        if row['status']=='conflict' and row['id'] not in conflicts:continue
        conflict=conflicts.get(row['id'])
        section=next((s['title'] for s in storage.config()['sections'] if s['id']==row['section_id']),row['section_id'])
        target='【'+projects.get(row['project_id'],'所选项目')+'】的「'+section+'」'
        add('assessment',row,('用新资料解决' if conflict else '更新')+target+('的冲突？' if conflict else '？'),
            target+'改为「'+row['text']+'」。','拒绝这条新结论；'+target+'保持现状。',row['project_id'],
            [conflict['left_source_id'],row['source_version_id']] if conflict else [row['source_version_id']],'high' if conflict else 'medium',excerpt=row['text'])
    return result


def project_status(row,has_content,conflicts):
    if conflicts:return 'at_risk'
    return {'completed':'done','done':'done','paused':'paused','blocked':'blocked'}.get(row['status'],'active' if has_content else 'empty')


def receipt_vm(row):
    keys=('id','kind','title','project_id','project_name','href','updated_sections','note')
    return {**{k:row[k] for k in keys},'received_at':stamp(row['received_at']),
            'state':row['state'] if row['state']!='rejected' else 'pending'}


def suggestion_vm(conn,pid=None):
    from .suggestions import read
    return [{'id':r['id'],'text':r['text'],'project_id':r['project_id'],'project_name':r['project_name'],
        'state':r['state'],'category_label':{'gaps':'缺口','risks':'风险','next':'下一步'}[r['category']],
        'generated_at':stamp(r['last_generated_at']),
        'basis':[{k:stamp(v) if k=='updated_at' else v for k,v in b.items() if k!='source_version_id'} for b in r['basis']]}
        for r in read(conn,pid)]


def project_vm(conn,pid,health_run=None,include_weekly=True):
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
    metrics=[];pending_targets=[]
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
    if table(conn,'candidate'):
        candidate_columns={row[1] for row in conn.execute('PRAGMA table_info(candidate)')}
        type_column='candidate_type' if 'candidate_type' in candidate_columns else 'target_type' if 'target_type' in candidate_columns else None
        if not type_column:
            candidate_columns=set()
        if type_column:
            source_column='source_refs' if 'source_refs' in candidate_columns else "'[]'"
            excerpt_column='raw_excerpt' if 'raw_excerpt' in candidate_columns else "''"
            sql=f"SELECT id,{type_column} AS candidate_type,proposed_change,{excerpt_column} AS raw_excerpt,{source_column} AS source_refs FROM candidate WHERE status='pending' ORDER BY created_at,id"
        else:
            sql=None
        previous_by_label={}
        retired_refs={}
        def _target_fields(raw):
            """(payload, label, target) when this candidate proposes a target."""
            try:
                proposed=json.loads(raw['proposed_change'] or '{}')
            except (ValueError,TypeError):
                return None
            payload=proposed.get('payload') if isinstance(proposed,dict) else None
            if not isinstance(payload,dict):payload=proposed if isinstance(proposed,dict) else {}
            project_ref=payload.get('project_id') or payload.get('resolved_project_id')
            project_name=payload.get('project_name') or payload.get('project')
            if project_ref and str(project_ref)!=pid:return None
            if project_name and str(project_name).strip()!=p['name']:return None
            target=payload.get('target_value',payload.get('target'))
            label=str(payload.get('metric_name') or payload.get('name') or payload.get('title') or raw['raw_excerpt'] or '').strip()
            if str(raw['candidate_type'] or '').lower() not in {'metric','data','data_asset'} and target is None:return None
            if target is None:return None
            return payload,label,target
        # The value a pending change moves away from is still readable after the
        # source version that proposed it was replaced: that proposal is retired
        # (it can never be accepted) but its target is the "30" of "from 30 to
        # 40".  Seed the comparison from every candidate that already left the
        # review list, so retiring an old version's proposal never hides the
        # pending change of the current version.
        if sql:
            for raw in conn.execute(sql.replace("status='pending'","status<>'pending'")):
                seeded=_target_fields(raw)
                if seeded:
                    # Rows are ordered oldest first: the last retired value is the
                    # one this pending change actually moves away from.
                    previous_by_label[seeded[1]]=seeded[2]
                    retired_refs[seeded[1]]=raw['source_refs']
        for raw in (conn.execute(sql) if sql else ()):
            fields=_target_fields(raw)
            if not fields:continue
            payload,label,target=fields
            previous=payload.get('previous_target',payload.get('current_target'))
            if previous is None and table(conn,'metric_definition'):
                match=conn.execute('SELECT target_value FROM metric_definition WHERE project_id=? AND name=? ORDER BY id LIMIT 1',(pid,label)).fetchone()
                previous=match[0] if match else None
            from_history=False
            if previous is None:
                previous=previous_by_label.get(label)
                from_history=previous is not None
            previous_by_label.setdefault(label,target)
            if previous is None:continue
            source_label=''
            try:
                refs_value=json.loads(raw['source_refs'] or '[]')
                refs=refs_value.get('refs',[]) if isinstance(refs_value,dict) else refs_value
                if refs:
                    ref=conn.execute('SELECT title FROM pm_source WHERE source_version_id=?',(str(refs[0]),)).fetchone()
                    source_label=ref[0] if ref else ''
            except (ValueError,TypeError,sqlite3.Error):
                pass
            pending_targets.append({'id':str(raw['id']),'name':label or '项目目标','previous':previous,
                'target':target,'pending_label':f'待确认：目标由 {previous:g} 改为 {target:g}' if isinstance(previous,(int,float)) and isinstance(target,(int,float)) else f'待确认：目标由 {previous} 改为 {target}','effective_date':payload.get('effective_date') or payload.get('valid_from'),
                'source_title':source_label,
                # The value above may come from the version that this one replaces;
                # naming that file is the evidence for both the reviewer and the
                # page ("由 30 改为 40" needs to say which document said 30).
                'previous_source_title':_retired_source_title(conn,retired_refs.get(label)) if from_history else None})
        # A pending risk in a scanned weekly report should be visible in the
        # dossier's risk section while the user reviews the source evidence.
        if 'risks' in entries and sql:
            for raw in (conn.execute(sql) if sql else ()):
                excerpt=str(raw['raw_excerpt'] or '').strip()
                if not excerpt or not any(word in excerpt for word in ('风险','阻碍','问题','尚未','延期')):
                    continue
                try:
                    proposed=json.loads(raw['proposed_change'] or '{}')
                except (ValueError,TypeError):
                    continue
                payload=proposed.get('payload') if isinstance(proposed,dict) else None
                payload=payload if isinstance(payload,dict) else proposed if isinstance(proposed,dict) else {}
                project_ref=payload.get('project_id') or payload.get('resolved_project_id')
                project_name=payload.get('project_name') or payload.get('project')
                if project_ref and str(project_ref)!=pid:continue
                if project_name and str(project_name).strip()!=p['name']:continue
                entries['risks'].append({'id':'pending-risk:'+str(raw['id']),'text':excerpt,
                    'kind':'fact','sources':[],'updated_at':None,
                    'tag':{'text':'待确认','tone':'warning'}})
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
    weekly_rep=None
    if include_weekly:
        try:
            from backend.provider.adapter import get_active_provider
            provider=get_active_provider()
        except Exception:
            provider=None
        from . import weekly
        weekly_rep=weekly.build_project_weekly_report(conn,pid,provider)
    return {'schema':'paw.pm.project/1','generated_at':stamp(storage.now()),'today':day(storage.now()),
        'project':{'id':pid,'name':p['name'],'status':project_status(p,bool(any(entries.values()) or milestones or all_sources),any(not c['resolved'] for c in conflicts)),
            'one_liner':next((e['text'] for s in data['sections'] if s['id']=='status' for e in entries[s['id']]),None),
            'updated_at':stamp(p.get('updated_at'))},
        'sections':[{'key':s['id'],'title':s['title'],'kind':configured[s['id']],'empty_text':None} for s in data['sections']],
        'entries':entries,'metrics':metrics,'pending_targets':pending_targets,'milestones':milestones,'changes':sorted(changes.values(),key=lambda c:c['at'],reverse=True),
        'conflicts':conflicts,'sources':list(all_sources.values()),'receipts':[receipt_vm(r) for r in receipts.read(conn,pid)],
        'ai_suggestions':suggestion_vm(conn,pid), 'weekly_report':weekly_rep, **health_vm(conn,pid,health_run)}


def health_vm(conn,pid=None,health_run=None):
    from . import daily,knowledge_gaps
    checks=daily.read(conn,pid,health_run)
    if not pid:
        reference=next((r['reference_at'] for r in checks if r['preview']),None)
        checks+=daily.unassigned(conn,reference)
    return {'health_checks':checks,'knowledge_gaps':knowledge_gaps.read(conn,pid)}


def today_vm(conn,health_run=None):
    pending=inbox(conn);projects=[]
    for raw in conn.execute("SELECT * FROM project WHERE status NOT IN ('merged','archived','deleted') ORDER BY name,id"):
        p=dict(raw);vm=project_vm(conn,p['id']);events=[]
        for change in vm['changes']:
            events.append({'id':change['id'],'type':'new_source','date':day(change['at']),
                'title':change['title'],'href':'/app/pm/projects/'+p['id']+'#pm-change-'+change['id'],'source':change['source']})
        projects.append({**vm['project'],'start':None,'end':day(p.get('due_date')),'progress':None,
            'events':events,'href':'/app/pm/projects/'+p['id']})
    try:
        from backend.provider.adapter import get_active_provider
        provider=get_active_provider()
    except Exception:
        provider=None
    status=[{'key':k,'label':label,'state':'unknown','summary':'尚无成功记录','last_success_at':None}
        for k,label in [('patrol','巡航'),('scan','扫描'),('backup','备份'),('model','模型连通'),('disk','磁盘')]]
    from . import evolution, weekly
    evo_metrics=evolution.read_trends(conn)
    cross_rep=weekly.build_cross_project_comparison(conn,provider)
    return {'schema':'paw.pm.today/1','generated_at':stamp(storage.now()),'today':day(storage.now()),
        'system_status':status,'decisions':pending,'portfolio':{'projects':projects},'ai_suggestions':suggestion_vm(conn),
        'receipts':[receipt_vm(r) for r in receipts.read(conn)],
        'evolution_metrics':evo_metrics,'cross_project_report':cross_rep,'weekly_reports':cross_rep.get('projects',[]),
        **health_vm(conn,health_run=health_run)}


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
