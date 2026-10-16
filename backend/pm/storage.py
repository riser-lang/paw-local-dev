"""SQLite adapter. Sources and AI drafts are intake, not approved business state."""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager


def now():
    return datetime.now(timezone.utc).isoformat()


def runtime_paths():
    from backend.core.config import DB_PATH,BACKUPS_DIR
    return Path(DB_PATH),Path(BACKUPS_DIR)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


@contextmanager
def connect(db_path=None):
    if db_path is None:
        from backend.core.config import DB_PATH
        db_path = DB_PATH
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def migrate(conn):
    # Called only by the existing migration entry point, outside business transactions.
    from . import health,backups
    health.migrate(conn);backups.migrate(conn)
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS pm_source (
      source_version_id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
      evidence_json TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS pm_section_revision (
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id),
      section_id TEXT NOT NULL, text TEXT NOT NULL, source_version_id TEXT NOT NULL REFERENCES pm_source(source_version_id),
      change_id TEXT NOT NULL REFERENCES approved_change(change_id), previous_id TEXT,
      withdrawn INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS pm_revision_project ON pm_section_revision(project_id, section_id, created_at);
    CREATE TABLE IF NOT EXISTS pm_section_current (
      project_id TEXT NOT NULL REFERENCES project(id), section_id TEXT NOT NULL,
      revision_id TEXT REFERENCES pm_section_revision(id), PRIMARY KEY(project_id,section_id));
    CREATE TABLE IF NOT EXISTS pm_change_snapshot (
      change_id TEXT PRIMARY KEY REFERENCES approved_change(change_id), project_id TEXT NOT NULL,
      before_json TEXT NOT NULL, after_json TEXT NOT NULL, undone_by TEXT, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS pm_undo (
      change_id TEXT PRIMARY KEY REFERENCES approved_change(change_id), original_change_id TEXT NOT NULL UNIQUE,
      project_id TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS pm_ai_explainer (
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id),
      content_json TEXT NOT NULL, evidence_json TEXT NOT NULL, label TEXT NOT NULL DEFAULT 'AI 生成',
      formal_state INTEGER NOT NULL DEFAULT 0 CHECK(formal_state=0), created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS pm_contract_rejection (
      id TEXT PRIMARY KEY, contract TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS pm_semantic_draft (
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), contract TEXT NOT NULL,
      output_json TEXT NOT NULL, created_at TEXT NOT NULL);
    ''')
    from .versions import migrate as migrate_versions
    migrate_versions(conn)
    from .coverage import migrate as migrate_coverage
    from .updates import migrate as migrate_updates
    migrate_coverage(conn)
    migrate_updates(conn)
    from .authorization import migrate as migrate_authorization
    migrate_authorization(conn)
    from .receipts import migrate as migrate_receipts
    migrate_receipts(conn)
    from .suggestions import migrate as migrate_suggestions
    migrate_suggestions(conn)
    from . import daily,knowledge_gaps
    daily.migrate(conn);knowledge_gaps.migrate(conn)


def config(path=None):
    value = json.loads(Path(path or Path(__file__).with_name('dossier_sections.json')).read_text(encoding='utf-8'))
    sections = value['sections']
    if not isinstance(sections, list) or not sections:
        raise ValueError('栏目配置不能为空')
    ids = [s['id'] for s in sections]
    if len(set(ids)) != len(ids) or any(not isinstance(i, str) or not i for i in ids):
        raise ValueError('栏目 id 无效或重复')
    if any(not isinstance(s.get('title'), str) or not s['title'] for s in sections):
        raise ValueError('栏目标题不能为空')
    if type(value['context_budget_bytes']) is not int or value['context_budget_bytes'] < 512:
        raise ValueError('上下文预算至少为 512 字节')
    return value


def store_source(conn, ident, title, body, evidence=None, kind='document'):
    conn.execute('INSERT OR IGNORE INTO pm_source VALUES (?,?,?,?,?,?)',
                 (ident, title, body, encoded(evidence or {}), kind, now()))
    row = conn.execute('SELECT body FROM pm_source WHERE source_version_id=?', (ident,)).fetchone()
    if row[0] != body:
        raise ValueError('来源版本不可覆盖；请创建新版本')
    return ident


def ingest_evidence(evidence, file_hash, db_path=None):
    from .coverage import evidence_body,save_extraction
    body = evidence_body(evidence)
    with connect(db_path) as conn:
        if not conn.execute('SELECT 1 FROM pm_source WHERE source_version_id=?',(file_hash,)).fetchone():
            store_source(conn, file_hash, evidence.get('display_name') or '输入资料', body, evidence,
                         'capture' if evidence.get('is_capture') else 'document')
        save_extraction(conn,file_hash,evidence)
        if not evidence.get('is_capture'):
            from .coverage import record
            record(conn,file_hash,evidence.get('display_name') or '输入资料',evidence)


def source(conn, ident, extraction=None):
    row = conn.execute('SELECT * FROM pm_source WHERE source_version_id=?', (ident,)).fetchone()
    if row is None:
        raise ValueError('来源版本不存在')
    result = dict(row)
    result['url'] = '/v1/pm/sources/' + ident
    if extraction=='initial':result['url']+='?extraction=initial'
    if extraction!='initial':
        selected=conn.execute('SELECT * FROM pm_source_extraction WHERE source_version_id=?'+(' AND id=?' if extraction else '')+' ORDER BY last_observed_at DESC,id DESC LIMIT 1',
                             (ident,extraction) if extraction else (ident,)).fetchone()
        if extraction and selected is None:raise ValueError('读取快照不存在')
        if selected:
            result['body']=selected['body']
            result['extraction_id']=selected['id']
            result['url']+='?extraction='+selected['id']
    result.setdefault('extraction_id','initial')
    result['initial_extraction_url']='/v1/pm/sources/'+ident+'?extraction=initial'
    return result


def save_derived(conn, project_id, value, evidence):
    from .contracts import validate_contract
    validate_contract('project_explainer', value, 'output')
    ident = str(uuid.uuid4())
    conn.execute('INSERT INTO pm_ai_explainer(id,project_id,content_json,evidence_json,created_at) VALUES(?,?,?,?,?)',
                 (ident, project_id, encoded(value), encoded(evidence), now()))
    store_source(conn, 'ai-' + ident, 'AI 生成（待核准）', encoded(value), evidence, kind='ai_generated')
    return ident


def derived(conn, project_id):
    rows = conn.execute('SELECT * FROM pm_ai_explainer WHERE project_id=? ORDER BY created_at DESC,id DESC', (project_id,)).fetchall()
    return [{**dict(r), 'content': json.loads(r['content_json']), 'evidence': json.loads(r['evidence_json'])} for r in rows]


def rejection(conn, contract, reason):
    # Do not persist arbitrary model output or exception text containing secrets.
    conn.execute('INSERT INTO pm_contract_rejection VALUES(?,?,?,?)',
                 (str(uuid.uuid4()), contract, reason, now()))
