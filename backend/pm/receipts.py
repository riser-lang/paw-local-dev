"""Immutable input receipts; destinations and updates derive from committed governance."""
import json
import uuid
from . import storage
from .authorization import for_change


def migrate(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS pm_input_receipt(id TEXT PRIMARY KEY,input_key TEXT NOT NULL UNIQUE,
      source_version_id TEXT NOT NULL REFERENCES pm_source(source_version_id),kind TEXT NOT NULL,
      title TEXT NOT NULL,received_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS pm_input_resolution(change_id TEXT PRIMARY KEY,decision_id TEXT NOT NULL,
      source_version_id TEXT NOT NULL,previous_json TEXT NOT NULL,undone_by TEXT,created_at TEXT NOT NULL);
    ''')


def register(evidence,sid):
    capture=evidence.get('is_capture')
    key='capture:'+str(evidence['capture_id']) if capture else 'file:'+sid
    title='随手记：'+('\n'.join(str(b.get('text','')) for b in evidence.get('text_blocks',[]))).strip()[:60] if capture else evidence.get('display_name') or '输入资料'
    with storage.connect() as conn:
        conn.execute('INSERT OR IGNORE INTO pm_input_receipt VALUES(?,?,?,?,?,?)',
            (str(uuid.uuid4()),key,sid,'capture' if capture else 'new_file',title,storage.now()))


def read(conn,pid=None):
    configs={s['id']:s['title'] for s in storage.config()['sections']}
    items=[]
    for raw in conn.execute('SELECT * FROM pm_input_receipt ORDER BY received_at DESC,id DESC'):
        item=dict(raw);sid=item['source_version_id']
        family=conn.execute('SELECT f.* FROM pm_family_member m JOIN pm_family f ON f.id=m.family_id WHERE m.source_version_id=?',(sid,)).fetchone()
        approval=None
        if family:
            for row in conn.execute('SELECT a.change_id,a.payload FROM pm_version_ruling v JOIN approved_change a ON a.change_id=v.change_id WHERE v.family_id=? AND v.undone_by IS NULL AND a.status=\'committed\' ORDER BY v.created_at DESC',(family['id'],)):
                if json.loads(row['payload']).get('source_version_id')==sid:approval=row['change_id'];break
        assigned=bool(family and approval)
        project=conn.execute('SELECT id,name FROM project WHERE id=?',(family['project_id'],)).fetchone() if assigned else None
        card=conn.execute('SELECT * FROM pm_family_decision WHERE source_version_id=?',(sid,)).fetchone()
        sections=[];changes=[]
        for revision in conn.execute('''SELECT r.* FROM pm_section_revision r JOIN approved_change a ON a.change_id=r.change_id
          WHERE r.withdrawn=0 AND a.status='committed' AND (r.source_version_id=? OR EXISTS(
            SELECT 1 FROM pm_revision_source rs WHERE rs.revision_id=r.id AND rs.source_version_id=?))
          ORDER BY r.created_at''',(sid,sid)):
            # A retained old fragment does not make an unrelated later write this input's change.
            refs=json.loads(conn.execute('SELECT source_refs FROM approved_change WHERE change_id=?',(revision['change_id'],)).fetchone()[0] or '[]')
            if sid not in refs:continue
            if revision['section_id'] not in sections:sections.append(revision['section_id'])
            if revision['change_id'] not in changes:changes.append(revision['change_id'])
        state='routed' if assigned else 'rejected' if card and card['status']=='rejected' else 'needs_decision' if card and card['status']=='pending' else 'pending'
        note='等待你确认归属' if state=='needs_decision' and card['project_id'] is None else '等待你确认文档族' if state=='needs_decision' else '已拒绝归属，正式记录未改变' if state=='rejected' else '等待处理'
        if assigned:
            note='已归入；更新了'+ '、'.join(configs.get(s,s) for s in sections) if sections else '已归入；尚无新的已提交栏目变更'
            statuses={r[0] for r in conn.execute('SELECT status FROM pm_section_assessment WHERE source_version_id=?',(sid,))}
            if not sections and 'consistent' in statuses:note='已归入；与当前结论一致，未重复更新'
            elif 'needs_user' in statuses or 'conflict' in statuses:note+='；部分结论需要你决定'
        item.update(project_id=project['id'] if project else None,project_name=project['name'] if project else None,
            href='/app/pm/projects/'+project['id'] if project else None,state=state,note=note,
            updated_sections=[configs.get(s,s) for s in sections],section_ids=sections,change_ids=changes,
            assignment_change_id=approval,authorization=for_change(conn,approval) if approval else None,
            decision_id=card['id'] if card and card['status']=='pending' else None,
            rejection_change_id=card['change_id'] if card and card['status']=='rejected' else None,
            source_url='/v1/pm/sources/'+sid)
        if pid is None or item['project_id']==pid:items.append(item)
    return items
