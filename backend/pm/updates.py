"""Schema-checked semantic assessments; official mutations execute only in Committer."""
import json
import uuid
from . import storage,versions
from .rules import requires_user


def migrate(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS pm_revision_source(revision_id TEXT NOT NULL REFERENCES pm_section_revision(id),
      source_version_id TEXT NOT NULL REFERENCES pm_source(source_version_id),text TEXT NOT NULL,extraction_id TEXT NOT NULL DEFAULT 'initial',
      PRIMARY KEY(revision_id,source_version_id));
    INSERT OR IGNORE INTO pm_revision_source(revision_id,source_version_id,text) SELECT id,source_version_id,text FROM pm_section_revision;
    CREATE TABLE IF NOT EXISTS pm_section_assessment(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,
      section_id TEXT NOT NULL,source_version_id TEXT NOT NULL,basis_id TEXT NOT NULL,
      relation TEXT NOT NULL,text TEXT NOT NULL,quote TEXT NOT NULL,status TEXT NOT NULL,
      change_id TEXT,created_at TEXT NOT NULL,UNIQUE(project_id,section_id,source_version_id,basis_id));
    CREATE TABLE IF NOT EXISTS pm_conflict(id TEXT PRIMARY KEY,assessment_id TEXT NOT NULL UNIQUE,
      project_id TEXT NOT NULL,section_id TEXT NOT NULL,left_revision_id TEXT NOT NULL,
      left_source_id TEXT NOT NULL,right_source_id TEXT NOT NULL,left_text TEXT NOT NULL,
      right_text TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',resolved_by TEXT,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS pm_update_resolution(change_id TEXT PRIMARY KEY,assessment_id TEXT NOT NULL,
      project_id TEXT NOT NULL,previous_revision_id TEXT,revision_id TEXT,conflict_id TEXT,
      decision TEXT NOT NULL,undone_by TEXT,created_at TEXT NOT NULL);
    ''')


def current(conn,pid,section):
    return conn.execute('SELECT r.* FROM pm_section_current c JOIN pm_section_revision r ON r.id=c.revision_id WHERE c.project_id=? AND c.section_id=?',(pid,section)).fetchone()


def process_source(sid,provider,only_sections=None):
    from .semantic import invoke
    from .reread import enqueue
    with storage.connect() as conn:
        source=storage.source(conn,sid)
        coverage=conn.execute('SELECT ratio FROM pm_coverage WHERE source_version_id=?',(sid,)).fetchone()
        if coverage and coverage['ratio'] is not None and coverage['ratio']<.8:
            enqueue(conn,sid,'low_coverage',sid)
        member=conn.execute('SELECT f.* FROM pm_family_member m JOIN pm_family f ON f.id=m.family_id WHERE m.source_version_id=?',(sid,)).fetchone()
        if not member or not versions.is_current(conn,sid):
            return {'status':'needs_assignment','items':[]}
        pid=member['project_id']
        sections=[]
        for config in storage.config()['sections']:
            if only_sections and config['id'] not in only_sections:continue
            row=current(conn,pid,config['id'])
            sections.append({'section_id':config['id'],'title':config['title'],
                             'current':dict(row) if row else None})
        value={'project_id':pid,'source_version_id':sid,'source_title':source['title'],
               'body':source['body'],'sections':sections}
        result=invoke('section_update',value,provider,conn)
        # Triggers are observations, not model decisions or formal changes.
        ruling=conn.execute('SELECT * FROM pm_version_ruling WHERE family_id=? AND current_source_id=? AND previous_source_id IS NOT NULL AND undone_by IS NULL ORDER BY created_at DESC LIMIT 1',(member['id'],sid)).fetchone()
        if ruling:enqueue(conn,sid,'new_version',ruling['change_id'])
        if result['status']!='draft':return result
        output=result['output']['sections']
        allowed={s['section_id']:s for s in sections}
        if len({s['section_id'] for s in output})!=len(output) or any(s['section_id'] not in allowed or s['quote'] not in source['body'] or s['text'] not in s['quote'] for s in output):
            storage.rejection(conn,'section_update','output_reference_or_quote_invalid')
            return {'status':'rejected','items':[]}
    items=[]
    for proposed in output:
        items.append(record_assessment(pid,sid,proposed,allowed[proposed['section_id']]['current']))
    return {'status':'assessed','items':items}


def record_assessment(pid,sid,proposed,basis):
    from .reread import enqueue
    section=proposed['section_id'];basis_id=basis['id'] if basis else ''
    with storage.connect() as conn:
        existing=conn.execute('SELECT * FROM pm_section_assessment WHERE project_id=? AND section_id=? AND source_version_id=? AND basis_id=?',(pid,section,sid,basis_id)).fetchone()
        if existing:return dict(existing)
        row=current(conn,pid,section)
        if (row['id'] if row else '')!=basis_id:
            return {'status':'stale_assessment','section_id':section}
        source=storage.source(conn,sid)
        if not versions.is_current(conn,sid):return {'status':'superseded'}
        relation=proposed['relation'];text=proposed['text']
        sensitive=requires_user(text+'\n'+source['body'],section)
        projected=conn.execute('SELECT * FROM pm_version_ruling WHERE change_id=? AND current_source_id=? AND previous_source_id IS NOT NULL AND undone_by IS NULL',(row['change_id'],sid)).fetchone() if row else None
        if sensitive:
            status='needs_user'
        elif projected:
            relation='replace';status='applied'
        elif row and text in row['text']:
            relation='consistent';status='consistent'
        elif not row and relation in ('consistent','conflict'):
            status='needs_user'
        elif relation=='consistent':
            status='consistent'
        elif relation=='conflict' and row:
            status='conflict'
        elif relation=='replace':
            # A model label cannot authorize an unrelated replacement.
            status='needs_user'
        else:
            relation='supplement';status='ready'
        ident=str(uuid.uuid4())
        conn.execute('INSERT INTO pm_section_assessment VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                     (ident,pid,section,sid,basis_id,relation,text,proposed['quote'],status,projected['change_id'] if projected and status=='applied' else None,storage.now()))
        if status=='conflict':
            conflict=str(uuid.uuid4())
            conn.execute('INSERT INTO pm_conflict VALUES(?,?,?,?,?,?,?,?,?,\'open\',NULL,?)',
                (conflict,ident,pid,section,row['id'],row['source_version_id'],sid,row['text'],text,storage.now()))
            # A new comparison revision must not recursively enqueue the same bytes.
            trigger=storage.encoded({'project_id':pid,'section_id':section,'incoming_source_id':sid})
            enqueue(conn,row['source_version_id'],'conflict',trigger)
            enqueue(conn,sid,'conflict',trigger)
    if status=='ready':
        from .rules import apply_rule
        result=apply_rule('pm_section','update',{'project_id':pid,'section_id':section,'text':text,
            'source_version_id':sid,'assessment_id':ident,'expected_revision_id':basis_id},[sid],'R3')
        return {'id':ident,'status':'applied','relation':relation,'change_id':result['change_id']}
    return {'id':ident,'status':status,'relation':relation}


def apply_assessment(conn,change,payload):
    from .governance import append_revision
    row=conn.execute('SELECT * FROM pm_section_assessment WHERE id=?',(payload['assessment_id'],)).fetchone()
    if not row or row['status']!='ready' or row['relation']!='supplement':
        raise ValueError('该语义结论不能自动写入')
    if any(payload[k]!=row[k] for k in ('project_id','section_id','source_version_id','text')):
        raise ValueError('语义提议与授权载荷不一致')
    basis=current(conn,row['project_id'],row['section_id'])
    if (basis['id'] if basis else '')!=row['basis_id'] or payload['expected_revision_id']!=row['basis_id']:
        raise ValueError('栏目已有后续变更，请重新判定')
    source=storage.source(conn,row['source_version_id'])
    if requires_user(source['body']+'\n'+row['text'],row['section_id']):
        raise ValueError('R3：该类结论必须由用户决定')
    if row['quote'] not in source['body'] or row['text'] not in row['quote']:
        raise ValueError('提议没有原文依据')
    text=row['text']
    if basis and text not in basis['text']:
        text=basis['text']+'\n'+text
    elif basis:
        text=basis['text']
    parts=[dict(r) for r in conn.execute('SELECT source_version_id,text,extraction_id FROM pm_revision_source WHERE revision_id=?',(basis['id'],))] if basis else []
    same=next((p['text'] for p in parts if p['source_version_id']==row['source_version_id']),'')
    fragment=same if row['text'] in same else (same+'\n'+row['text']).strip()
    parts=[p for p in parts if p['source_version_id']!=row['source_version_id']]+[{'source_version_id':row['source_version_id'],'text':fragment}]
    revision=append_revision(conn,row['project_id'],row['section_id'],text,row['source_version_id'],change['change_id'],parts)
    conn.execute("UPDATE pm_section_assessment SET status='applied',change_id=? WHERE id=?",(change['change_id'],row['id']))
    return revision


def resolve(conn,change,payload):
    from .governance import append_revision
    row=conn.execute('SELECT * FROM pm_section_assessment WHERE id=?',(payload['assessment_id'],)).fetchone()
    if not row or row['status'] not in ('needs_user','conflict'):
        raise ValueError('待决结论已变化')
    basis=current(conn,row['project_id'],row['section_id'])
    if (basis['id'] if basis else '')!=row['basis_id']:
        raise ValueError('栏目已有后续变化，请重新判定')
    if payload['choice'] not in ('incoming','current','reject'):
        raise ValueError('请选择采纳新资料、保留当前或拒绝')
    conflict=conn.execute("SELECT * FROM pm_conflict WHERE assessment_id=? AND status='open'",(row['id'],)).fetchone()
    if row['status']=='conflict' and not conflict:raise ValueError('冲突状态已变化')
    revision=basis['id'] if basis else None
    if payload['choice']=='incoming':
        revision=append_revision(conn,row['project_id'],row['section_id'],row['text'],row['source_version_id'],change['change_id'])
    if conflict:
        conn.execute("UPDATE pm_conflict SET status='resolved',resolved_by=? WHERE id=?",(change['change_id'],conflict['id']))
    conn.execute("UPDATE pm_section_assessment SET status=?,change_id=? WHERE id=?",('rejected' if payload['choice']=='reject' else 'resolved',change['change_id'],row['id']))
    conn.execute('INSERT INTO pm_update_resolution VALUES(?,?,?,?,?,?,?,NULL,?)',
        (change['change_id'],row['id'],row['project_id'],basis['id'] if basis else None,revision,conflict['id'] if conflict else None,payload['choice'],storage.now()))
    return row['project_id']


def undo_resolution(conn,change,original):
    resolution=conn.execute('SELECT * FROM pm_update_resolution WHERE change_id=?',(original,)).fetchone()
    if not resolution:return None
    if resolution['undone_by']:raise ValueError('裁决已撤销')
    assessment=conn.execute('SELECT * FROM pm_section_assessment WHERE id=?',(resolution['assessment_id'],)).fetchone()
    if assessment['change_id']!=original:raise ValueError('待决事项已有后续裁决')
    basis=current(conn,resolution['project_id'],assessment['section_id'])
    if (basis['id'] if basis else None)!=resolution['revision_id']:raise ValueError('栏目已有后续变化')
    if resolution['revision_id']!=resolution['previous_revision_id']:
        conn.execute('UPDATE pm_section_current SET revision_id=? WHERE project_id=? AND section_id=?',(resolution['previous_revision_id'],resolution['project_id'],assessment['section_id']))
        conn.execute('UPDATE pm_section_revision SET withdrawn=1 WHERE id=?',(resolution['revision_id'],))
    if resolution['conflict_id']:
        conn.execute("UPDATE pm_conflict SET status='open',resolved_by=NULL WHERE id=?",(resolution['conflict_id'],))
    conn.execute('UPDATE pm_section_assessment SET status=?,change_id=NULL WHERE id=?',('conflict' if resolution['conflict_id'] else 'needs_user',assessment['id']))
    conn.execute('UPDATE pm_update_resolution SET undone_by=? WHERE change_id=?',(change['change_id'],original))
    conn.execute('INSERT INTO pm_undo VALUES(?,?,?,?)',(change['change_id'],original,resolution['project_id'],storage.now()))
    return resolution['project_id']


def undo_metadata(conn,original):
    conn.execute("UPDATE pm_section_assessment SET status='undone' WHERE change_id=? AND status='applied'",(original,))


def version_sensitive(conn,pid,section,sid,text,quote,basis_id):
    conn.execute('INSERT OR IGNORE INTO pm_section_assessment VALUES(?,?,?,?,?,?,?,?,\'needs_user\',NULL,?)',
        (str(uuid.uuid4()),pid,section,sid,basis_id,'replace',text,quote,storage.now()))


def read_conflicts(conn,pid=None):
    result=[dict(r) for r in conn.execute('SELECT * FROM pm_conflict WHERE (? IS NULL OR project_id=?) ORDER BY created_at DESC',(pid,pid))]
    for row in result:
        basis=current(conn,row['project_id'],row['section_id'])
        if row['status']=='open' and (not basis or basis['id']!=row['left_revision_id']):row['status']='stale'
    return result
