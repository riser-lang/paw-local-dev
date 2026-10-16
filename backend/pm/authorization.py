"""Additive approval provenance, including databases with the original CHECK enum."""
import re
import threading

SOURCES={'candidate_review','direct_user','standing_user_rule'}
RULE_SOURCE='用户 2026-10-15 设定'
_lock=threading.RLock()


def migrate(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_decision'").fetchone():return
    with _lock:
        columns={r[1] for r in conn.execute('PRAGMA table_info(review_decision)')}
        if 'approval_source_v2' not in columns:
            conn.execute("ALTER TABLE review_decision ADD COLUMN approval_source_v2 TEXT CHECK (approval_source_v2 IS NULL OR approval_source_v2 IN ('candidate_review','direct_user','standing_user_rule'))")
        # Existing immutable audit rows are not rewritten. The old enum is retained.
        conn.execute('''CREATE VIEW IF NOT EXISTS pm_review_decision_audit AS
          SELECT id,candidate_id,decision,reviewer,review_channel,
          approval_source AS legacy_approval_source,
          COALESCE(approval_source_v2,CASE WHEN reviewer='standing_user_rule'
            AND review_channel='standing_user_rule' AND review_notes LIKE '用户 2026-10-15 设定%'
            THEN 'standing_user_rule' ELSE approval_source END) AS approval_source,
          approval_source_v2,original_payload,reviewed_payload,authorization_payload_hash,review_notes,created_at
          FROM review_decision''')


def legacy_slot(conn,source):
    if source not in SOURCES:raise ValueError('未知审批来源')
    sql=conn.execute("SELECT sql FROM sqlite_master WHERE name='review_decision'").fetchone()[0]
    constraint=re.search(r'CHECK\s*\(\s*approval_source\s+IN\s*\(([^)]*)\)',sql,re.I)
    # Upgraded v1 databases keep their original enum; v2 is the authoritative source.
    if source=='standing_user_rule' and constraint and "'standing_user_rule'" not in constraint[1]:
        return 'candidate_review'
    return source


def describe(row):
    if row is None:return {'approval_source':'unknown','label':'审批来源待补','rule_source':None,'legacy_record':False}
    value=dict(row)
    source=value.get('approval_source_v2') or value.get('approval_source')
    legacy=not value.get('approval_source_v2') and value.get('reviewer')=='standing_user_rule' and value.get('review_channel')=='standing_user_rule' and str(value.get('review_notes') or '').startswith(RULE_SOURCE)
    if legacy:source='standing_user_rule'
    if source=='standing_user_rule':
        notes=str(value.get('review_notes') or '')
        rule=notes.split('；',1)[1] if '；' in notes else ''
        label='按用户常设规则自动写入（'+rule+'，'+RULE_SOURCE+'）'
    elif source=='direct_user':label='用户亲手批准'
    elif source=='candidate_review':label='用户亲手批准（候选审核）'
    else:label='审批来源待补'
    return {'approval_source':source,'label':label,'rule_source':RULE_SOURCE if source=='standing_user_rule' else None,'legacy_record':bool(legacy)}


def for_change(conn,change_id):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='pm_review_decision_audit'").fetchone():return describe(None)
    row=conn.execute('SELECT r.* FROM pm_review_decision_audit r JOIN approved_change a ON a.approval_ref=r.id WHERE a.change_id=?',(change_id,)).fetchone()
    return describe(row)
