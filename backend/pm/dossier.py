import json
from . import storage


def read(conn, project_id):
    row = conn.execute('SELECT * FROM project WHERE id=?', (project_id,)).fetchone()
    if row is None:
        raise ValueError('项目不存在')
    sections = []
    for section in storage.config()['sections']:
        current = conn.execute('SELECT revision_id FROM pm_section_current WHERE project_id=? AND section_id=?', (project_id, section['id'])).fetchone()
        current_id = current[0] if current else None
        rows = conn.execute('SELECT * FROM pm_section_revision WHERE project_id=? AND section_id=? ORDER BY created_at DESC,id DESC', (project_id, section['id'])).fetchall()
        revisions = [{**dict(r), 'source_url': '/v1/pm/sources/' + r['source_version_id']} for r in rows]
        for revision in revisions:
            revision['source_refs']=[{**dict(r),'source_url':'/v1/pm/sources/'+r['source_version_id']+'?extraction='+r['extraction_id']} for r in conn.execute('SELECT source_version_id,text,extraction_id FROM pm_revision_source WHERE revision_id=?',(revision['id'],))]
            primary=next((r for r in revision['source_refs'] if r['source_version_id']==revision['source_version_id']),None)
            if primary:revision['source_url']=primary['source_url']
        sections.append({**section, 'current': next((r for r in revisions if r['id'] == current_id), None),
                         'history': [r for r in revisions if r['id'] != current_id]})
    return {'project': dict(row), 'sections': sections, 'ai_generated': storage.derived(conn, project_id),
            'undo_records': [dict(r) for r in conn.execute('SELECT * FROM pm_undo WHERE project_id=? ORDER BY created_at DESC', (project_id,))]}
