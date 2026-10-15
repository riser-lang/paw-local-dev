"""Read-only PM evidence adapter shared by project/global assistant packs."""
import json
import sqlite3
from .versions import is_current


def augment(conn, items, project_id=None):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='pm_family'").fetchone():
        return items
    # Remove legacy derivations whose audited origin is a superseded source.
    blocked=set();blocked_changes=set()
    rows=conn.execute("SELECT a.target_id,c.change_id,c.source_refs FROM audit_event a JOIN approved_change c ON c.change_id=a.change_id WHERE a.event_type='commit' ORDER BY a.created_at DESC")
    latest={}
    for row in rows:
        refs=json.loads(row['source_refs'] or '[]')
        latest.setdefault(row['target_id'],refs)
        if any(isinstance(ref,str) and not is_current(conn,ref) for ref in refs):
            blocked_changes.add(row['change_id'])
    for ident,refs in latest.items():
        if any(isinstance(ref,str) and not is_current(conn,ref) for ref in refs):
            blocked.add(ident)
    items=[i for i in items if i.get('item_id') not in blocked|blocked_changes and i.get('target_type') not in ('pm_version','pm_undo') and
           (not i.get('source_version_id') or is_current(conn,i['source_version_id']))]
    sections=conn.execute('''SELECT r.*,s.title FROM pm_section_current c JOIN pm_section_revision r ON r.id=c.revision_id
      JOIN pm_source s ON s.source_version_id=r.source_version_id
      WHERE (? IS NULL OR r.project_id=?) ORDER BY r.created_at DESC LIMIT 16''',(project_id,project_id)).fetchall()
    from backend.brain.context import agy_context_v2 as C
    for row in sections:
        if is_current(conn,row['source_version_id']):
            items.append(C.make_item(layer='PROJECT_HANDBOOK',item_id='pm-section-'+row['id'],
                title=row['section_id']+' · '+row['title'],content=row['text'],
                source_ref='/v1/pm/sources/'+row['source_version_id'],
                extra={'source_version_id':row['source_version_id']}))
    rows=conn.execute('''SELECT s.*,i.project_id,f.current_source_id,m.family_id FROM pm_source s
      LEFT JOIN pm_document_intake i ON i.source_version_id=s.source_version_id
      LEFT JOIN pm_family_member m ON m.source_version_id=s.source_version_id
      LEFT JOIN pm_family f ON f.id=m.family_id
      WHERE s.kind='document' ORDER BY s.created_at DESC,s.source_version_id LIMIT 100''').fetchall()
    from backend.brain.context import agy_context_v2 as C
    for row in rows:
        if row['family_id'] and row['current_source_id'] != row['source_version_id']:
            continue
        if project_id and row['project_id'] != project_id:
            continue
        pending=conn.execute("SELECT 1 FROM pm_family_decision WHERE source_version_id=? AND project_id IS NOT NULL AND status='pending'",(row['source_version_id'],)).fetchone()
        if pending:
            continue
        items.append(C.make_item(layer='CONFIRMED_KNOWLEDGE',item_id='pm-source-'+row['source_version_id'],
            title=row['title']+'（资料证据，非正式结论）', content=row['body'][:2600],
            authority=C.A_EVIDENCE,temporal_state='current',source_ref='/v1/pm/sources/'+row['source_version_id'],
            extra={'source_version_id':row['source_version_id'],'knowledge_status':'evidence'}))
        if sum(i.get('item_id','').startswith('pm-source-') for i in items)>=8:
            break
    return items
