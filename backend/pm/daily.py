"""Daily observations. Formal facts are read, never repaired by this module."""
import hashlib
import json
import uuid
from datetime import datetime,timedelta,timezone
from . import storage,dossier,versions,semantic

TZ=timezone(timedelta(hours=8))


def migrate(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS pm_daily_check(id TEXT PRIMARY KEY,
      project_id TEXT NOT NULL REFERENCES project(id),reference_at TEXT NOT NULL,day TEXT NOT NULL,
      preview INTEGER NOT NULL CHECK(preview IN (0,1)),context_hash TEXT NOT NULL,result_json TEXT NOT NULL,
      created_at TEXT NOT NULL)''')
    conn.execute('CREATE INDEX IF NOT EXISTS pm_daily_check_project_day ON pm_daily_check(project_id,day,preview)')


def moment(value):
    result=datetime.fromisoformat(value.replace('Z','+00:00')) if isinstance(value,str) else value
    return result.replace(tzinfo=timezone.utc) if result and result.tzinfo is None else result


def age(value,at):
    try:return max(0,(at-moment(value)).days) if value else None
    except (ValueError,TypeError):return None


def inputs(conn,pid):
    data=dossier.read(conn,pid);sections=[]
    for section in data['sections']:
        current=section['current']
        if current:
            sections.append({'section_id':section['id'],'title':section['title'],'text':current['text'],
                'revision_id':current['id'],'source_refs':current['source_refs'],'created_at':current['created_at']})
    pending=[dict(r) for r in conn.execute("SELECT id,source_version_id,created_at FROM pm_family_decision WHERE project_id=? AND status='pending' ORDER BY id",(pid,))]
    value={'project_id':pid,'sections':sections}
    families=[tuple(r) for r in conn.execute('SELECT id,current_source_id FROM pm_family WHERE project_id=? ORDER BY id',(pid,))]
    digest=hashlib.sha256(storage.encoded([data['project'],value,pending,object_refs(conn,pid),families,legacy_outcome(conn,pid)]).encode('utf-8')).hexdigest()
    return data,value,pending,digest


def object_refs(conn,pid):
    """Current task/knowledge provenance, recovered from successful audit commits."""
    result=[];seen=set()
    tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    rows=conn.execute('''SELECT e.target_type,e.target_id,a.source_refs FROM audit_event e
      JOIN approved_change a ON a.change_id=e.change_id JOIN commit_record c ON c.id=e.commit_id
      WHERE e.event_type='commit' AND e.target_type IN ('todo','knowledge') AND c.result='success'
      AND a.source_refs IS NOT NULL ORDER BY e.created_at DESC,e.rowid DESC''')
    for row in rows:
        key=(row['target_type'],row['target_id'])
        if key in seen:continue
        seen.add(key)
        if row['target_type']=='todo' and 'todo' in tables:
            item=conn.execute("SELECT id,title FROM todo WHERE id=? AND project_id=? AND status NOT IN ('done','completed','deleted','cancelled','archived')",(row['target_id'],pid)).fetchone()
            href='/app/todos'
        elif row['target_type']=='knowledge' and {'knowledge_page','knowledge_project_link'}<=tables:
            item=conn.execute("SELECT k.id,k.title FROM knowledge_page k JOIN knowledge_project_link l ON l.knowledge_id=k.id WHERE k.id=? AND l.project_id=? AND k.status!='deprecated'",(row['target_id'],pid)).fetchone()
            href='/app/knowledge/'+row['target_id']
        else:continue
        if item:result.append({'id':item['id'],'title':item['title'],'href':href,'source_refs':json.loads(row['source_refs'])})
    return result


def legacy_outcome(conn,pid):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='project_outcome'").fetchone():return None
    row=conn.execute('SELECT executive_summary,source_evidence_refs_json FROM project_outcome WHERE project_id=?',(pid,)).fetchone()
    if not row:return None
    from backend.runtime.core.paw_source_version_governance import identify_stale_outcome_refs
    refs=json.loads(row['source_evidence_refs_json'] or '[]')
    # Reuse the legacy read-only authority checker; never call its resynthesis writer.
    observed=identify_stale_outcome_refs({'source_evidence_refs':refs},conn)
    stale=[r for r in refs if isinstance(r,dict) and isinstance(r.get('source_version_id'),str) and not versions.is_current(conn,r['source_version_id'])]
    observed['stale_refs']=list({storage.encoded(r):r for r in observed['stale_refs']+stale}.values())
    return {'summary':row['executive_summary'],**observed}


def deterministic(conn,data,value,pending,at):
    pid=data['project']['id'];findings=[]
    for section in value['sections']:
        for ref in section['source_refs']:
            if not versions.is_current(conn,ref['source_version_id']):
                findings.append({'kind':'stale_reference','title':'现有结论引用了历史版本',
                    'detail':section['title']+'仍引用已替换的资料，请核对后通过核准流程更新。',
                    'basis':[{'title':section['title'],'href':ref['source_url'],'quote':ref['text']} ]})
    for item in object_refs(conn,pid):
        for sid in item['source_refs']:
            if not isinstance(sid,str) or versions.is_current(conn,sid):continue
            try:s=storage.source(conn,sid)
            except ValueError:continue
            findings.append({'kind':'stale_reference','title':'正式待办或知识仍引用历史版本',
                'detail':item['title']+'的依据已有新版，请核对是否仍适用；不会自动改写。',
                'basis':[{'title':s['title'],'href':s['url'],'quote':s['body'][:200]}]})
    outcome=legacy_outcome(conn,pid)
    if outcome and outcome['stale_refs']:
        findings.append({'kind':'stale_reference','title':'项目综合结论引用了历史版本',
            'detail':'综合结论的部分依据已被替换，请核对后重新核准；不会自动修改原结论。',
            'basis':[{'title':'已有项目综合结论','href':'/app/projects/'+pid,'quote':outcome['summary'][:200]}]})
    for row in pending:
        days=age(row['created_at'],at)
        if days is not None and days>=7:
            s=storage.source(conn,row['source_version_id'])
            findings.append({'kind':'undecided_family','title':'文档族待裁定已超过7天','detail':s['title']+'；待裁定'+str(days)+'天。',
                'basis':[{'title':s['title'],'href':s['url'],'quote':s['body'][:200]}]})
    p=data['project'];progress=next((s for s in value['sections'] if s['section_id']=='progress'),None)
    last=progress['created_at'] if progress else p.get('created_at') or p.get('updated_at')
    days=age(last,at)
    if days is not None and days>=14 and p.get('status') not in ('done','completed','archived','deleted','merged','paused'):
        findings.append({'kind':'no_progress','title':'超过14天没有已确认的进展更新',
            'detail':('最后已确认进展' if progress else '项目建立后尚无已确认进展')+'；已过'+str(days)+'天。',
            'basis':[{'title':'进度与里程碑' if progress else '项目记录','href':'/app/pm/projects/'+pid+'#pm-sec-progress',
                'quote':progress['text'][:200] if progress else p['name']}]})
    return findings


def run(pid,provider,reference_at=None,force=False):
    at=moment(reference_at) if reference_at else datetime.now(timezone.utc);preview=reference_at is not None
    if at is None:raise ValueError('检查参考时间无效')
    with storage.connect() as conn:
        data,value,pending,digest=inputs(conn,pid)
        cached=conn.execute('SELECT * FROM pm_daily_check WHERE project_id=? AND day=? AND preview=0 AND context_hash=? ORDER BY created_at DESC,id DESC LIMIT 1',
            (pid,at.astimezone(TZ).date().isoformat(),digest)).fetchone()
        if not force and not preview and cached:
            old=json.loads(cached['result_json'])
            if not (old['semantic_status']=='needs_key' and getattr(provider,'api_key',None)):
                return {**old,'status':'cached','id':cached['id']}
        findings=deterministic(conn,data,value,pending,at)
        result=semantic.invoke('section_consistency',value,provider,conn) if len(value['sections'])>=2 else {'status':'not_enough_sections'}
        if result['status']=='draft':
            sections={s['section_id']:s for s in value['sections']};extra=[]
            for issue in result['output']['findings']:
                left=sections.get(issue['left_section']);right=sections.get(issue['right_section'])
                if not left or not right or left==right or issue['left_quote'] not in left['text'] or issue['right_quote'] not in right['text']:
                    storage.rejection(conn,'section_consistency','invalid_section_or_quote');result={'status':'rejected'};extra=[];break
                extra.append({'kind':'section_contradiction','title':'栏目可能矛盾（AI检查）',
                    'detail':left['title']+'与'+right['title']+'中的表述需要核对；不会自动改写结论。',
                    'basis':[{'title':s['title'],'href':'/app/pm/projects/'+pid+'#pm-sec-'+s['section_id'],'quote':q}
                        for s,q in [(left,issue['left_quote']),(right,issue['right_quote'])]]})
            findings+=extra
        # Another user may have committed a change while the model was running.
        if inputs(conn,pid)[3]!=digest:return {'status':'context_changed','items':[]}
        ident=uuid.uuid4().hex;now=storage.now()
        output={'id':ident,'project_id':pid,'project_name':data['project']['name'],'reference_at':at.isoformat(),
            'preview':preview,'semantic_status':result['status'],'status':'checked','checked_at':now,'items':findings,
            'thresholds':{'undecided_days':7,'no_progress_days':14}}
        conn.execute('INSERT INTO pm_daily_check VALUES(?,?,?,?,?,?,?,?)',
            (ident,pid,at.isoformat(),at.astimezone(TZ).date().isoformat(),int(preview),digest,storage.encoded(output),now))
        return output


def read(conn,pid=None,preview_id=None):
    rows=conn.execute('SELECT c.*,p.status FROM pm_daily_check c JOIN project p ON p.id=c.project_id '+
        "WHERE p.status NOT IN ('merged','deleted','archived')"+(' AND c.project_id=?' if pid else '')+
        ' ORDER BY c.created_at DESC,c.id DESC',(pid,) if pid else ()).fetchall()
    selected={}
    for r in rows:
        if r['project_id'] in selected or r['preview'] and r['id']!=preview_id:continue
        selected[r['project_id']]={**json.loads(r['result_json']),
            'stale':r['context_hash']!=inputs(conn,r['project_id'])[3] or r['day']!=datetime.now(TZ).date().isoformat()}
    if preview_id:
        preview=next((r for r in rows if r['id']==preview_id and r['preview']),None)
        if preview:selected[preview['project_id']]=json.loads(preview['result_json'])
    return list(selected.values())


def unassigned(conn,reference_at=None):
    at=moment(reference_at) if reference_at else datetime.now(timezone.utc);items=[]
    for row in conn.execute("SELECT * FROM pm_family_decision WHERE status='pending' AND project_id IS NULL ORDER BY created_at,id"):
        days=age(row['created_at'],at)
        if days is None or days<7:continue
        s=storage.source(conn,row['source_version_id'])
        items.append({'kind':'undecided_family','title':'未归属资料待裁定已超过7天','detail':s['title']+'；待裁定'+str(days)+'天。请先确认项目归属。',
            'basis':[{'title':s['title'],'href':s['url'],'quote':s['body'][:200]}]})
    return [{'id':'unassigned:'+at.date().isoformat(),'project_id':None,'project_name':'未归属资料',
        'reference_at':at.isoformat(),'preview':reference_at is not None,'semantic_status':'not_applicable','status':'checked',
        'checked_at':storage.now(),'items':items,'thresholds':{'undecided_days':7,'no_progress_days':14}}] if items else []
