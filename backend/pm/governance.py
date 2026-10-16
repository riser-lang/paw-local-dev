"""Only called INSIDE the existing Committer's validated transaction."""
import json
import uuid
from . import storage


def project_row(conn, ident):
    row = conn.execute('SELECT * FROM project WHERE id=?', (ident,)).fetchone()
    return dict(row) if row else None


def append_revision(conn, project_id, section_id, text, source_id, change_id, source_parts=None):
    if section_id not in {s['id'] for s in storage.config()['sections']}:
        raise ValueError('未知栏目')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('结论不能为空')
    storage.source(conn, source_id)
    from .versions import is_current
    if not is_current(conn,source_id):
        raise ValueError('来源版本已被替换，不能作为栏目当前结论')
    for part in source_parts or []:
        storage.source(conn,part['source_version_id'])
        if not is_current(conn,part['source_version_id']):raise ValueError('补充结论仍含过期来源，请先处理新版结论')
    old = conn.execute('SELECT revision_id FROM pm_section_current WHERE project_id=? AND section_id=?', (project_id, section_id)).fetchone()
    previous = old[0] if old else None
    if previous:
        prior = conn.execute('SELECT text,source_version_id FROM pm_section_revision WHERE id=?', (previous,)).fetchone()
        if (prior['text'], prior['source_version_id']) == (text, source_id):
            return previous
    ident = str(uuid.uuid4())
    conn.execute('INSERT INTO pm_section_revision(id,project_id,section_id,text,source_version_id,change_id,previous_id,created_at) VALUES(?,?,?,?,?,?,?,?)',
                 (ident, project_id, section_id, text, source_id, change_id, previous, storage.now()))
    conn.execute('INSERT INTO pm_section_current VALUES(?,?,?) ON CONFLICT(project_id,section_id) DO UPDATE SET revision_id=excluded.revision_id',
                 (project_id, section_id, ident))
    for part in source_parts or [{'source_version_id':source_id,'text':text}]:
        extraction=part.get('extraction_id') or storage.source(conn,part['source_version_id'])['extraction_id']
        conn.execute('INSERT INTO pm_revision_source VALUES(?,?,?,?)',(ident,part['source_version_id'],part['text'],extraction))
    return ident


def record_project_change(conn, change, payload, ident, before):
    if change['target_type'] != 'project':
        return
    if change['operation'] not in ('create', 'update'):
        return
    refs = json.loads(change['source_refs'] or '[]')
    source_id = next((r for r in refs if isinstance(r, str) and conn.execute('SELECT 1 FROM pm_source WHERE source_version_id=?', (r,)).fetchone()), None)
    if source_id is None:
        # The explicit reviewed input is itself an auditable source; label it as such.
        source_id = 'review-' + change['change_id']
        storage.store_source(conn, source_id, '用户核准输入', storage.encoded(payload), kind='reviewed_input')
    after = project_row(conn, ident)
    conn.execute('INSERT INTO pm_change_snapshot VALUES(?,?,?,?,NULL,?)',
                 (change['change_id'], ident, storage.encoded(before), storage.encoded(after), storage.now()))
    for section in storage.config()['sections']:
        fields = section.get('project_fields', [])
        if any(f in payload for f in fields):
            text = '\n'.join(str(after[f]) for f in fields if after.get(f))
            if text:
                append_revision(conn, ident, section['id'], text, source_id, change['change_id'])


def execute(conn, change, payload):
    from . import routing
    if change['target_type']=='pm_input_decision':return routing.reject(conn,change,payload)
    from . import versions
    if change['target_type'] == 'pm_version':
        return versions.execute(conn,change,payload)
    from . import updates
    if change['target_type'] == 'pm_update_decision':
        return updates.resolve(conn,change,payload)
    if change['target_type'] == 'pm_section':
        if project_row(conn, payload['project_id']) is None:
            raise ValueError('项目不存在')
        if payload.get('assessment_id'):
            return updates.apply_assessment(conn,change,payload)
        return append_revision(conn, payload['project_id'], payload['section_id'], payload['text'], payload['source_version_id'], change['change_id'])
    if change['target_type'] != 'pm_undo':
        raise ValueError('未知项目管理写入类型')
    original = payload['change_id']
    input_target=routing.undo_rejection(conn,change,original)
    if input_target is not None:return input_target
    version_project = versions.undo(conn,change,original)
    if version_project is not None:
        updates.undo_metadata(conn,original)
        return version_project
    resolution_project=updates.undo_resolution(conn,change,original)
    if resolution_project is not None:return resolution_project
    rows = conn.execute('SELECT * FROM pm_section_revision WHERE change_id=?', (original,)).fetchall()
    snap = conn.execute('SELECT * FROM pm_change_snapshot WHERE change_id=?', (original,)).fetchone()
    if not rows and not snap:
        raise ValueError('没有可撤销的栏目变更')
    project_id = snap['project_id'] if snap else rows[0]['project_id']
    if conn.execute('SELECT 1 FROM pm_undo WHERE original_change_id=?', (original,)).fetchone():
        raise ValueError('变更已撤销')
    for row in rows:
        current = conn.execute('SELECT revision_id FROM pm_section_current WHERE project_id=? AND section_id=?', (project_id, row['section_id'])).fetchone()
        if not current or current[0] != row['id']:
            raise ValueError('该栏目有后续变更，请先撤销后续变更')
    if snap:
        before, after = json.loads(snap['before_json']), json.loads(snap['after_json'])
        if before is None:
            raise ValueError('项目创建不能作为栏目更新撤销')
        current = project_row(conn, project_id)
        fields = [k for k in before if k not in ('id','created_at','updated_at') and before[k] != after[k]]
        if any(current[k] != after[k] for k in fields):
            raise ValueError('正式项目已有后续修改，不能覆盖')
        if fields:
            conn.execute('UPDATE project SET ' + ','.join(k + '=?' for k in fields) + ',updated_at=? WHERE id=?',
                         tuple(before[k] for k in fields) + (storage.now(), project_id))
        conn.execute('UPDATE pm_change_snapshot SET undone_by=? WHERE change_id=?', (change['change_id'], original))
    for row in rows:
        conn.execute('UPDATE pm_section_current SET revision_id=? WHERE project_id=? AND section_id=?', (row['previous_id'], project_id, row['section_id']))
        conn.execute('UPDATE pm_section_revision SET withdrawn=1 WHERE id=?', (row['id'],))
    conn.execute('INSERT INTO pm_undo VALUES(?,?,?,?)', (change['change_id'], original, project_id, storage.now()))
    updates.undo_metadata(conn,original)
    return project_id
