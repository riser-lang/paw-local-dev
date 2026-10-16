"""R2 routing reuses document-family decisions instead of creating a second authority."""
import json
import re
from . import storage


def explicit_projects(projects,body):
    hits=[p for p in projects if p['name'] and p['name'] in body]
    if re.search(r'归属待定|项目归属待确认|无法确定归属|待分配项目',body):return []
    for p in hits:
        name=re.escape(p['name'])
        if re.search(r'(?:不属于|并非|不归入|不是)\s*'+name+'|'+name+r'.{0,8}(?:无关|并非归属)',body):return []
    return hits


def pending(conn):
    projects=[dict(r) for r in conn.execute("SELECT id,name FROM project WHERE status NOT IN ('merged','archived','deleted') ORDER BY name,id")]
    return [{**dict(r),'kind':'assignment' if r['project_id'] is None else 'version',
             'question':'确认归属：这份输入应归入哪个已有项目？' if r['project_id'] is None else '确认文档族与版本',
             'projects':projects,'source_url':'/v1/pm/sources/'+r['source_version_id']}
            for r in conn.execute("SELECT d.*,s.title,i.family_key FROM pm_family_decision d JOIN pm_source s ON s.source_version_id=d.source_version_id JOIN pm_document_intake i ON i.source_version_id=d.source_version_id WHERE d.status='pending' ORDER BY d.created_at,d.id")]


def resolve(decision_id,project_id,provider,family_id=None,family_title=None):
    from backend.governance.committer import create_direct_user_approved_change,commit_approved_change
    with storage.connect() as conn:
        card=conn.execute("SELECT * FROM pm_family_decision WHERE id=? AND status='pending'",(decision_id,)).fetchone()
        if not card:raise ValueError('归属确认已经变化')
        if not conn.execute("SELECT 1 FROM project WHERE id=? AND status NOT IN ('merged','archived','deleted')",(project_id,)).fetchone():raise ValueError('请选择已有的有效项目')
        intake=conn.execute('SELECT * FROM pm_document_intake WHERE source_version_id=?',(card['source_version_id'],)).fetchone()
        title=family_title or intake['family_key']
        if family_id:
            family=conn.execute('SELECT * FROM pm_family WHERE id=? AND project_id=?',(family_id,project_id)).fetchone()
            if not family:raise ValueError('文档族不属于所选项目')
            title=family['title']
        payload={'decision_id':decision_id,'project_id':project_id,'source_version_id':card['source_version_id'],
                 'family_id':family_id,'family_title':title}
    _,change=create_direct_user_approved_change('pm_version','rule',payload,[payload['source_version_id']])
    result=commit_approved_change(change)
    if result['result']!='success':raise ValueError(result.get('error','归属确认失败'))
    from .updates import process_source
    assessment=process_source(payload['source_version_id'],provider)
    return {**result,'change_id':change,'assessment_status':assessment['status']}


def reject(conn,change,payload):
    row=conn.execute("SELECT * FROM pm_family_decision WHERE id=? AND status='pending'",(payload['decision_id'],)).fetchone()
    if not row:raise ValueError('归属确认已经变化')
    conn.execute('INSERT INTO pm_input_resolution VALUES(?,?,?,?,NULL,?)',
        (change['change_id'],row['id'],row['source_version_id'],storage.encoded(dict(row)),storage.now()))
    conn.execute("UPDATE pm_family_decision SET status='rejected',change_id=? WHERE id=?",(change['change_id'],row['id']))
    return row['project_id'] or row['source_version_id']


def undo_rejection(conn,change,original):
    row=conn.execute('SELECT * FROM pm_input_resolution WHERE change_id=?',(original,)).fetchone()
    if not row:return None
    decision=conn.execute('SELECT * FROM pm_family_decision WHERE id=?',(row['decision_id'],)).fetchone()
    if row['undone_by'] or decision['change_id']!=original:raise ValueError('归属事项已有后续操作')
    before=json.loads(row['previous_json'])
    conn.execute('UPDATE pm_family_decision SET status=?,change_id=? WHERE id=?',(before['status'],before['change_id'],row['decision_id']))
    conn.execute('UPDATE pm_input_resolution SET undone_by=? WHERE change_id=?',(change['change_id'],original))
    conn.execute('INSERT INTO pm_undo VALUES(?,?,?,?)',(change['change_id'],original,before['project_id'] or row['source_version_id'],storage.now()))
    return before['project_id'] or row['source_version_id']
