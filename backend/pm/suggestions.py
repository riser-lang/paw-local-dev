"""Grounded, nonformal project advice. Explicit adoption only proposes a candidate."""
import hashlib
import json
import threading
import uuid
from . import storage,semantic,dossier,versions
from .answer_advice import unsupported

_adoption_lock=threading.RLock()


def migrate(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS pm_suggestion(
      id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES project(id),category TEXT NOT NULL,
      text TEXT NOT NULL,basis_json TEXT NOT NULL,context_hash TEXT NOT NULL,fingerprint TEXT NOT NULL UNIQUE,
      label TEXT NOT NULL DEFAULT 'AI 建议' CHECK(label='AI 建议'),
      formal_state INTEGER NOT NULL DEFAULT 0 CHECK(formal_state=0),
      created_at TEXT NOT NULL,last_generated_at TEXT NOT NULL,superseded_at TEXT,candidate_id TEXT)''')


def context(conn,pid):
    data=dossier.read(conn,pid)
    refs=conn.execute('''SELECT source_version_id FROM pm_source WHERE source_version_id IN
      (SELECT current_source_id FROM pm_family WHERE project_id=?) OR source_version_id IN
      (SELECT source_version_id FROM pm_section_revision WHERE project_id=?)
      ORDER BY created_at DESC,source_version_id LIMIT 12''',(pid,pid)).fetchall()
    evidence={}
    for row in refs:
        if not versions.is_current(conn,row[0]):continue
        item=storage.source(conn,row[0])
        if item['body'].strip():evidence[row[0]]=item
    value={'project':{'id':pid,'name':data['project']['name']},
        'sources':[{'source_version_id':sid,'title':s['title'],'body':s['body'],'source_url':s['url']} for sid,s in evidence.items()],
        'facts':[{'section_id':s['id'],'text':s['current']['text']} for s in data['sections'] if s['current']]}
    digest=hashlib.sha256(storage.encoded(value).encode('utf-8')).hexdigest()
    return value,evidence,digest


def read(conn,pid=None):
    sql="SELECT s.*,p.name AS project_name FROM pm_suggestion s JOIN project p ON p.id=s.project_id WHERE s.superseded_at IS NULL AND p.status NOT IN ('merged','archived','deleted')"
    rows=conn.execute(sql+(' AND s.project_id=?' if pid else '')+' ORDER BY s.last_generated_at DESC,s.id',(pid,) if pid else ()).fetchall()
    items=[]
    for raw in rows:
        row=dict(raw);basis=json.loads(row.pop('basis_json'))
        if not basis or any(not versions.is_current(conn,b['source_version_id']) for b in basis):continue
        status=None
        if row['candidate_id']:
            candidate=conn.execute('SELECT status FROM candidate WHERE id=?',(row['candidate_id'],)).fetchone()
            status=candidate[0] if candidate else None
        row.update(basis=basis,state='adopted' if status=='applied' else 'declined' if status=='rejected' else 'pending_review' if status else 'open')
        items.append(row)
    return items


def generate(pid,provider,force=False):
    if not getattr(provider,'api_key',None):return {'status':'needs_key','message':'需要填写 Key 才能生成建议','items':[]}
    with storage.connect() as conn:
        value,evidence,digest=context(conn,pid)
        if not evidence:return {'status':'needs_evidence','message':'需要先补充能归属到该项目的资料，才能生成带依据的建议','items':[]}
        existing=read(conn,pid)
        if not force and existing and all(r['context_hash']==digest for r in existing):
            return {'status':'cached','items':existing}
        # No DML precedes this call: the provider must not inherit a write lock.
        result=semantic.invoke('project_advice',value,provider,conn)
        if result['status']!='draft':return {**result,'items':[]}
        output=result['output']['items'];prepared=[]
        try:
            if {i['category'] for i in output}!={'gaps','risks','next'}:raise ValueError('missing_advice_category')
            for item in output:
                if not item['text'].startswith('建议'):raise ValueError('suggestion_label_missing')
                basis=[]
                for ref in item['basis']:
                    source=evidence.get(ref['source_version_id'])
                    if not source or ref['quote'] not in source['body']:raise ValueError('ungrounded_advice_basis')
                    if not versions.is_current(conn,ref['source_version_id']):raise ValueError('stale_advice_basis')
                    basis.append({'id':ref['source_version_id'],'source_version_id':ref['source_version_id'],
                        'title':source['title'],'kind':'capture' if source['kind']=='capture' else 'other',
                        'href':source['url'],'excerpt':ref['quote'],'updated_at':source['created_at']})
                if unsupported(item['text'],'\n'.join(b['title']+'\n'+b['excerpt'] for b in basis)):
                    raise ValueError('unbacked_advice_entity')
                fingerprint=hashlib.sha256(storage.encoded([pid,item['category'],item['text'],
                    sorted((b['id'],b['href'],b['excerpt']) for b in basis)]).encode('utf-8')).hexdigest()
                prepared.append((item,basis,fingerprint))
            if context(conn,pid)[2]!=digest:raise ValueError('advice_context_changed')
        except ValueError as exc:
            storage.rejection(conn,'project_advice',str(exc))
            return {'status':'rejected','message':'建议未通过依据校验，已丢弃本次输出','items':[]}
        now=storage.now();fingerprints=[p[2] for p in prepared]
        conn.execute('UPDATE pm_suggestion SET superseded_at=? WHERE project_id=? AND superseded_at IS NULL AND fingerprint NOT IN ('+','.join('?' for _ in fingerprints)+')',(now,pid,*fingerprints))
        for item,basis,fingerprint in prepared:
            conn.execute('''INSERT INTO pm_suggestion(id,project_id,category,text,basis_json,context_hash,fingerprint,created_at,last_generated_at)
              VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET superseded_at=NULL,last_generated_at=excluded.last_generated_at,context_hash=excluded.context_hash''',
              (uuid.uuid4().hex,pid,item['category'],item['text'],storage.encoded(basis),digest,fingerprint,now,now))
        return {'status':'draft','label':'AI 建议','formal_state':False,'items':read(conn,pid)}


def adopt(ident,make_candidate):
    with _adoption_lock:
        with storage.connect() as conn:
            item=next((r for r in read(conn) if r['id']==ident),None)
            if item is None:raise ValueError('建议不存在或依据已过期，请重新生成')
            if item['candidate_id']:return {'candidate_id':item['candidate_id'],'status':item['state']}
        cid=make_candidate('todo','create',{'project_id':item['project_id'],'title':item['text'][:100],'description':item['text']},
            raw_excerpt='AI 建议（推断，尚未成为正式待办）：'+item['text'],
            source_refs=list(dict.fromkeys(b['source_version_id'] for b in item['basis'])),
            analysis_model='user:adopt_pm_suggestion',review_brief={'summary':'将AI建议转为待办候选；用户核准后才写入'})
        with storage.connect() as conn:conn.execute('UPDATE pm_suggestion SET candidate_id=? WHERE id=?',(cid,ident))
        return {'candidate_id':cid,'status':'pending_review'}
