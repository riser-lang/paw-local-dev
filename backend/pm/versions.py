"""R1/R2 document-family decisions. Intake is nonformal; mutations run in Committer."""
import difflib
import json
import re
import uuid
from datetime import date
from pathlib import Path
from . import storage

RULE_SOURCE = '用户 2026-10-15 设定'
VERSION = re.compile(r'(?i)(?:[ _.-]*(?:v|版本)[ _.-]*)(\d+(?:\.\d+)*)$')
EFFECTIVE = re.compile(r'(?:生效日期|生效时间|effective\s*(?:date|from))\s*[:：]?\s*(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?', re.I)


def migrate(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS pm_document_intake(
      source_version_id TEXT PRIMARY KEY REFERENCES pm_source(source_version_id), project_id TEXT,
      family_key TEXT NOT NULL, version_json TEXT NOT NULL, effective_date TEXT, observed_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS pm_family(
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), title TEXT NOT NULL,
      current_source_id TEXT REFERENCES pm_source(source_version_id), UNIQUE(project_id,title));
    CREATE TABLE IF NOT EXISTS pm_family_member(
      source_version_id TEXT PRIMARY KEY REFERENCES pm_source(source_version_id),
      family_id TEXT NOT NULL REFERENCES pm_family(id));
    CREATE TABLE IF NOT EXISTS pm_family_decision(
      id TEXT PRIMARY KEY, source_version_id TEXT NOT NULL UNIQUE REFERENCES pm_source(source_version_id),
      project_id TEXT, options_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
      prompt TEXT NOT NULL, change_id TEXT, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS pm_version_ruling(
      change_id TEXT PRIMARY KEY REFERENCES approved_change(change_id), family_id TEXT NOT NULL,
      previous_source_id TEXT, current_source_id TEXT NOT NULL, snapshot_json TEXT NOT NULL,
      rule_source TEXT NOT NULL, undone_by TEXT, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS pm_resynthesis(
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL, family_id TEXT NOT NULL,
      source_version_id TEXT NOT NULL, change_id TEXT NOT NULL, status TEXT NOT NULL,
      UNIQUE(change_id));
    ''')


def metadata(title, body):
    stem = Path(title).stem.strip()
    match = VERSION.search(stem)
    key = stem[:match.start()].strip(' _.-') if match else stem
    dates = set()
    for found in EFFECTIVE.finditer(body):
        try:
            dates.add(date(*map(int, found.groups())).isoformat())
        except ValueError:
            pass
    return {'family_key': key, 'version': list(map(int, match[1].split('.'))) if match else [],
            'effective_date': next(iter(dates)) if len(dates) == 1 else None,
            'ambiguous_date': len(dates) > 1}


def rank(row):
    # Missing effective date cannot outrank an explicit date. Version precedes observation time.
    return (row['effective_date'] or '', tuple(json.loads(row['version_json'])), row['observed_at'])


def _family_tail(title, project_name=None):
    """The part of a family key that identifies the document, not the project.

    Two files of one project normally share the project name ("青苗计划_方案_v1"
    and "青苗计划_10月周报").  Comparing the full titles made the *different*
    documents look like one family, so the two versions fell back to "needs
    assignment" and no version ever superseded the other.  Comparing the tails
    keeps that decision about the document itself.
    """
    value = str(title or '').strip()
    name = str(project_name or '').strip()
    if name and value.startswith(name):
        value = value[len(name):]
    return value.strip(' _.-')


def similar_family(left, right, project_name=None):
    """True when two family keys plausibly name the same document."""
    a, b = _family_tail(left, project_name), _family_tail(right, project_name)
    if not a or not b:
        return False
    if a == b:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= .55


def superseded_siblings(conn, sid):
    """Sibling source versions of the same document family that outrank ``sid``.

    Used before proposing: a version that a newer version already replaced must
    not send new proposals to the review list.  Works even while the family
    still waits for the user's assignment decision, because the family key and
    the version rank are known from intake.
    """
    row = conn.execute('SELECT family_key,project_id FROM pm_document_intake WHERE source_version_id=?', (sid,)).fetchone()
    if not row or not row['family_key']:
        return []
    peers = [dict(r) for r in conn.execute(
        'SELECT source_version_id,version_json,effective_date,observed_at FROM pm_document_intake WHERE family_key=?',
        (row['family_key'],))]
    mine = next((p for p in peers if p['source_version_id'] == sid), None)
    if not mine:
        return []
    return [p['source_version_id'] for p in peers
            if p['source_version_id'] != sid and rank(p) > rank(mine)]


def withdraw_pending_candidates(conn, source_ids, reason='来源版本已被替换'):
    """Retire pending candidates that were derived from replaced source versions.

    They can never be accepted ("来源版本已被替换" from the committer), so leaving
    them in the review list is what looked like "a few items that cannot be
    confirmed".  The decision is recorded like any other review decision.
    """
    wanted = {str(s) for s in source_ids if s}
    if not wanted:
        return []
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'candidate' not in tables or 'review_decision' not in tables:
        # Minimal schemas (unit fixtures, imported documents only) have no review
        # list to clean up.
        return []
    withdrawn = []
    rows = conn.execute("SELECT id,proposed_change FROM candidate WHERE status='pending'").fetchall()
    for row in rows:
        try:
            refs = json.loads(conn.execute('SELECT source_refs FROM candidate WHERE id=?', (row['id'],)).fetchone()[0] or '[]')
        except (TypeError, ValueError):
            continue
        if not wanted.intersection({str(r) for r in refs}):
            continue
        conn.execute('''INSERT INTO review_decision (id,candidate_id,decision,reviewer,review_channel,
            approval_source,original_payload,reviewed_payload,authorization_payload_hash,review_notes,created_at)
            VALUES (?,?,'reject',?,'web_review','candidate_review',?,NULL,NULL,?,?)''',
            (str(uuid.uuid4()), row['id'], 'system:version-superseded',
             row['proposed_change'], reason, storage.now()))
        conn.execute("UPDATE candidate SET status='rejected',reviewed_at=?,reviewed_by=? WHERE id=?",
                     (storage.now(), 'system:version-superseded', row['id']))
        withdrawn.append(row['id'])
    return withdrawn


def withdraw_candidates_for(sids, reason='来源版本已被替换'):
    """Open its own connection and retire candidates of replaced versions."""
    if not sids:
        return []
    with storage.connect() as conn:
        return withdraw_pending_candidates(conn, sids, reason)


def source_metadata(source):
    value=metadata(source['title'],source['body'])
    if source['kind']=='capture':
        value={'family_key':'随手记-'+source['source_version_id'],'version':[],
               'effective_date':None,'ambiguous_date':False}
    return value


def process_source(source_id):
    """Called by normal Planner intake. Never writes family/current pointers directly."""
    with storage.connect() as conn:
        s = storage.source(conn, source_id)
        if s['kind'] not in ('document','capture'):
            return
        meta = source_metadata(s)
        projects = [dict(r) for r in conn.execute("SELECT id,name FROM project WHERE status NOT IN ('merged','archived','deleted')")]
        from .routing import explicit_projects
        hits = explicit_projects(projects, s['title'] + '\n' + s['body'])
        pid = hits[0]['id'] if len(hits) == 1 else None
        conn.execute('INSERT OR IGNORE INTO pm_document_intake VALUES(?,?,?,?,?,?)',
                     (source_id, pid, meta['family_key'], storage.encoded(meta['version']), meta['effective_date'], s['created_at']))
        if conn.execute('SELECT 1 FROM pm_family_member WHERE source_version_id=?', (source_id,)).fetchone():
            return
        families = [dict(r) for r in conn.execute('SELECT * FROM pm_family WHERE project_id=?', (pid,))]
        exact = next((f for f in families if f['title'] == meta['family_key']), None)
        project_name = None
        if pid:
            row = conn.execute('SELECT name FROM project WHERE id=?', (pid,)).fetchone()
            project_name = row['name'] if row else None
        similar = [f for f in families
                   if similar_family(f['title'], meta['family_key'], project_name)] if s['kind']=='document' else []
        if not pid or meta['ambiguous_date'] or (not exact and similar):
            conn.execute('INSERT OR IGNORE INTO pm_family_decision(id,source_version_id,project_id,options_json,prompt,created_at) VALUES(?,?,?,?,?,?)',
                         (str(uuid.uuid4()),source_id,pid,storage.encoded([f['id'] for f in similar]),
                          '需要你决定：确认资料归属与文档族。选择后会参与该族版本裁定；拒绝则保持正式记录不变。',storage.now()))
            return
        payload = {'project_id': pid, 'source_version_id': source_id,
                   'family_id': exact['id'] if exact else None, 'family_title': meta['family_key']}
    from .rules import apply_rule
    return apply_rule('pm_version', 'rule', payload, [source_id], 'R1/R2')


def execute(conn, change, payload):
    from .governance import append_revision, project_row
    sid, pid = payload['source_version_id'], payload['project_id']
    intake = conn.execute('SELECT * FROM pm_document_intake WHERE source_version_id=?',(sid,)).fetchone()
    if not intake:
        raise ValueError('资料尚未完成摄取')
    family = conn.execute('SELECT * FROM pm_family WHERE id=?',(payload.get('family_id'),)).fetchone()
    if not family and not payload.get('family_id'):
        family=conn.execute('SELECT * FROM pm_family WHERE project_id=? AND title=?',(pid,payload['family_title'])).fetchone()
    user_decision = payload.get('decision_id')
    if user_decision:
        card = conn.execute("SELECT * FROM pm_family_decision WHERE id=? AND status='pending'",(user_decision,)).fetchone()
        if not card or card['source_version_id'] != sid:
            raise ValueError('归族确认已经变化')
    elif intake['project_id'] != pid or intake['family_key'] != payload['family_title']:
        raise ValueError('R2：归属或文档族必须由用户确认')
    if not user_decision:
        meta=source_metadata(storage.source(conn,sid))
        similar=conn.execute('SELECT title FROM pm_family WHERE project_id=? AND title<>?',(pid,intake['family_key'])).fetchall()
        project_name = conn.execute('SELECT name FROM project WHERE id=?',(pid,)).fetchone()[0]
        if meta['ambiguous_date'] or (storage.source(conn,sid)['kind']=='document' and not family and any(similar_family(r[0],intake['family_key'],project_name) for r in similar)):
            raise ValueError('R2：不确定的文档族需要用户确认')
    if family and (family['project_id'] != pid or (not user_decision and family['title'] != intake['family_key'])):
        raise ValueError('R2：不能自动归入不确定的文档族')
    fid = family['id'] if family else str(uuid.uuid4())
    previous = family['current_source_id'] if family else None
    if not family:
        conn.execute('INSERT INTO pm_family VALUES(?,?,?,NULL)', (fid,pid,payload['family_title']))
    member = conn.execute('SELECT family_id FROM pm_family_member WHERE source_version_id=?',(sid,)).fetchone()
    member_before=member[0] if member else None
    if member:
        withdrawn=conn.execute('SELECT 1 FROM pm_version_ruling WHERE change_id=? AND undone_by IS NOT NULL',(card['change_id'],)).fetchone() if user_decision and card['change_id'] else None
        if not withdrawn:raise ValueError('资料版本已裁定')
        if member[0]!=fid:
            if conn.execute('SELECT 1 FROM pm_section_current c JOIN pm_revision_source rs ON rs.revision_id=c.revision_id WHERE rs.source_version_id=?',(sid,)).fetchone():raise ValueError('原归属仍有当前结论，请先撤销')
            conn.execute('UPDATE pm_family_member SET family_id=? WHERE source_version_id=?',(fid,sid))
    else:conn.execute('INSERT INTO pm_family_member VALUES(?,?)',(sid,fid))
    candidates = conn.execute('SELECT i.* FROM pm_document_intake i JOIN pm_family_member m ON m.source_version_id=i.source_version_id WHERE m.family_id=?',(fid,)).fetchall()
    winner = max(candidates, key=rank)['source_version_id']
    snapshot = {'sections': [], 'project_before': {}, 'project_after': {}, 'legacy': [],
                'intake_project_before':intake['project_id'],'decision_before':dict(card) if user_decision else None,
                'member_family_before':member_before}
    legacy_exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='source_version_governance'").fetchone()
    if legacy_exists:
        legacy_id='pm-'+fid
        conn.execute("INSERT OR IGNORE INTO logical_document VALUES(?,?,?,'active',?,?)",(legacy_id,fid,payload['family_title'],storage.now(),storage.now()))
        for item in candidates:
            old=conn.execute('SELECT * FROM source_version_governance WHERE source_version_id=?',(item['source_version_id'][:16],)).fetchone()
            if old:
                snapshot['legacy'].append(dict(old))
                conn.execute('UPDATE source_version_governance SET logical_document_id=?,governance_status=?,effective_from=?,authority_rank=1,authority_reason=?,updated_at=? WHERE source_version_id=?',
                    (legacy_id,'current' if item['source_version_id']==winner else 'superseded',item['effective_date'],RULE_SOURCE+'；R1',storage.now(),old['source_version_id']))
        conn.execute('INSERT INTO source_version_decision_log VALUES(?,?,?,?,?,?,?)',
                     (str(uuid.uuid4()),legacy_id,storage.encoded({'current':winner,'previous':previous}),change['approval_ref'],change['change_id'],storage.encoded([pid]),storage.now()))
    if winner != previous:
        conn.execute('UPDATE pm_family SET current_source_id=? WHERE id=?',(winner,fid))
        if previous:
            new_source = storage.source(conn,winner)
            rows = conn.execute('''SELECT r.* FROM pm_section_current c JOIN pm_section_revision r ON r.id=c.revision_id
              WHERE c.project_id=? AND (r.source_version_id IN (SELECT source_version_id FROM pm_family_member WHERE family_id=?)
              OR EXISTS(SELECT 1 FROM pm_revision_source rs JOIN pm_family_member m ON m.source_version_id=rs.source_version_id WHERE rs.revision_id=r.id AND m.family_id=?))''',(pid,fid,fid)).fetchall()
            project = project_row(conn,pid)
            for row in rows:
                # A reference never relabels old prose as a new source. Use an explicit verbatim excerpt.
                replacement='新版原文节选：' + new_source['body'][:2000]
                family_sources={r[0] for r in conn.execute('SELECT source_version_id FROM pm_family_member WHERE family_id=?',(fid,))}
                parts=[dict(r) for r in conn.execute('SELECT source_version_id,text,extraction_id FROM pm_revision_source WHERE revision_id=?',(row['id'],))]
                other_parts=[p for p in parts if p['source_version_id'] not in family_sources]
                parts=[{'source_version_id':winner,'text':replacement}]+other_parts
                text='\n'.join(p['text'] for p in parts)
                from .rules import requires_user
                if requires_user(text,row['section_id']):
                    from .updates import version_sensitive
                    version_sensitive(conn,pid,row['section_id'],winner,replacement,new_source['body'][:2000],row['id'])
                    continue
                revision = append_revision(conn,pid,row['section_id'],text,winner,change['change_id'],parts)
                snapshot['sections'].append({'section_id':row['section_id'],'before':row['id'],'after':revision})
                section = next(s for s in storage.config()['sections'] if s['id']==row['section_id'])
                for field in section.get('project_fields',[]):
                    if field in ('latest_progress_summary','next_action_summary'):
                        snapshot['project_before'][field] = project[field]
                        snapshot['project_after'][field] = text
                        conn.execute('UPDATE project SET '+field+'=? WHERE id=?',(text,pid))
        conn.execute('INSERT INTO pm_resynthesis VALUES(?,?,?,?,?,?)',
                     (str(uuid.uuid4()),pid,fid,winner,change['change_id'],'references_refreshed' if previous else 'initial'))
    conn.execute('INSERT INTO pm_version_ruling VALUES(?,?,?,?,?,?,NULL,?)',
                 (change['change_id'],fid,previous,winner,storage.encoded(snapshot),RULE_SOURCE,storage.now()))
    if winner != previous:
        # The ruling makes one version current: proposals that came from the
        # replaced versions cannot become formal state any more, so they leave
        # the review list instead of waiting there forever.
        replaced = [r['source_version_id'] for r in candidates if r['source_version_id'] != winner]
        withdraw_pending_candidates(conn, replaced)
    if user_decision:
        conn.execute('UPDATE pm_document_intake SET project_id=? WHERE source_version_id=?',(pid,sid))
        conn.execute("UPDATE pm_family_decision SET status='resolved',change_id=? WHERE id=?",(change['change_id'],user_decision))
    return fid


def undo(conn, change, original):
    ruling = conn.execute('SELECT * FROM pm_version_ruling WHERE change_id=?',(original,)).fetchone()
    if not ruling:
        return None
    family = conn.execute('SELECT * FROM pm_family WHERE id=?',(ruling['family_id'],)).fetchone()
    latest = conn.execute('SELECT change_id FROM pm_version_ruling WHERE family_id=? AND undone_by IS NULL ORDER BY created_at DESC,change_id DESC LIMIT 1',(family['id'],)).fetchone()
    if ruling['undone_by'] or latest[0] != original or family['current_source_id'] != ruling['current_source_id']:
        raise ValueError('版本已有后续裁定，请先撤销后续变更')
    if conn.execute('SELECT 1 FROM pm_section_current c JOIN pm_section_revision r ON r.id=c.revision_id WHERE c.project_id=? AND r.source_version_id=? AND r.change_id<>? AND r.created_at>?',
        (family['project_id'],ruling['current_source_id'],original,ruling['created_at'])).fetchone():
        raise ValueError('新版已有后续栏目变更，请先撤销栏目变更')
    snap = json.loads(ruling['snapshot_json'])
    for section in snap['sections']:
        current = conn.execute('SELECT revision_id FROM pm_section_current WHERE project_id=? AND section_id=?',(family['project_id'],section['section_id'])).fetchone()
        if not current or current[0] != section['after']:
            raise ValueError('栏目已有后续变更')
    project = conn.execute('SELECT * FROM project WHERE id=?',(family['project_id'],)).fetchone()
    if any(project[k] != v for k,v in snap['project_after'].items()):
        raise ValueError('正式项目已有后续变更')
    for section in snap['sections']:
        conn.execute('UPDATE pm_section_current SET revision_id=? WHERE project_id=? AND section_id=?',(section['before'],family['project_id'],section['section_id']))
        conn.execute('UPDATE pm_section_revision SET withdrawn=1 WHERE id=?',(section['after'],))
    for key,value in snap['project_before'].items():
        conn.execute('UPDATE project SET '+key+'=? WHERE id=?',(value,family['project_id']))
    for row in snap.get('legacy',[]):
        conn.execute('UPDATE source_version_governance SET logical_document_id=?,governance_status=?,effective_from=?,authority_rank=?,authority_reason=?,updated_at=? WHERE source_version_id=?',
            (row['logical_document_id'],row['governance_status'],row['effective_from'],row['authority_rank'],row['authority_reason'],storage.now(),row['source_version_id']))
    conn.execute('UPDATE pm_family SET current_source_id=? WHERE id=?',(ruling['previous_source_id'],family['id']))
    conn.execute('UPDATE pm_version_ruling SET undone_by=? WHERE change_id=?',(change['change_id'],original))
    if snap.get('member_family_before'):
        sid=json.loads(conn.execute('SELECT payload FROM approved_change WHERE change_id=?',(original,)).fetchone()[0])['source_version_id']
        conn.execute('UPDATE pm_family_member SET family_id=? WHERE source_version_id=?',(snap['member_family_before'],sid))
    before=snap.get('decision_before')
    if before:
        conn.execute('UPDATE pm_family_decision SET status=?,change_id=? WHERE id=?',(before['status'],original,before['id']))
        conn.execute('UPDATE pm_document_intake SET project_id=? WHERE source_version_id=?',(snap.get('intake_project_before'),before['source_version_id']))
    elif ruling['previous_source_id'] is None:
        sid=ruling['current_source_id']
        conn.execute("INSERT OR IGNORE INTO pm_family_decision(id,source_version_id,project_id,options_json,prompt,change_id,created_at) VALUES(?,?,?,?,?,?,?)",
            (str(uuid.uuid4()),sid,family['project_id'],'[]','需要你决定：归属裁定已撤销，请重新确认。',original,storage.now()))
    conn.execute("UPDATE pm_resynthesis SET status='undone' WHERE change_id=?",(original,))
    conn.execute('INSERT INTO pm_undo VALUES(?,?,?,?)',(change['change_id'],original,family['project_id'],storage.now()))
    return family['project_id']


def is_current(conn, sid):
    row = conn.execute('SELECT f.current_source_id,m.source_version_id FROM pm_family_member m JOIN pm_family f ON f.id=m.family_id WHERE m.source_version_id=? OR (length(?)=16 AND substr(m.source_version_id,1,16)=?)',(sid,sid,sid)).fetchone()
    return row is None or row[0] == row[1]


def read(conn, pid):
    families=[]
    for row in conn.execute('SELECT * FROM pm_family WHERE project_id=? ORDER BY title',(pid,)):
        members=[{**dict(r),'status':'current' if r['source_version_id']==row['current_source_id'] else 'superseded', 'source_url':'/v1/pm/sources/'+r['source_version_id']}
                 for r in conn.execute('SELECT s.source_version_id,s.title FROM pm_family_member m JOIN pm_source s ON s.source_version_id=m.source_version_id WHERE m.family_id=? ORDER BY s.created_at',(row['id'],))]
        rulings=[dict(r) for r in conn.execute('SELECT * FROM pm_version_ruling WHERE family_id=? ORDER BY created_at DESC',(row['id'],))]
        from .authorization import for_change
        for ruling in rulings:ruling['authorization']=for_change(conn,ruling['change_id'])
        families.append({**dict(row),'versions':members,'rulings':rulings})
    return families
