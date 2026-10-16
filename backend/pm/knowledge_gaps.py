"""Observed unanswered project questions, separate from formal project state."""
import hashlib
import re
from . import storage


def is_insufficient(text,mapping):
    if isinstance(mapping,dict) and mapping.get('answer_status')=='insufficient_evidence':return True
    claims=mapping.get('claims',[]) if isinstance(mapping,dict) else []
    return not claims and bool(re.match(r'^(?:目前|当前)?(?:依据不足|资料不足|没有足够(?:的)?(?:依据|资料)|未找到(?:相关)?(?:依据|资料|记录))',text.strip()))


def migrate(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS pm_knowledge_gap(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES project(id),
      question TEXT NOT NULL,occurrences INTEGER NOT NULL,first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,
      state TEXT NOT NULL DEFAULT 'open' CHECK(state IN ('open','resolved')))''')


def record(pid,question):
    if not pid or not question.strip():return None
    normalized=re.sub(r'[\s。？！?！]+','',question).casefold()
    ident=hashlib.sha256((pid+'\0'+normalized).encode('utf-8')).hexdigest();now=storage.now()
    with storage.connect() as conn:
        conn.execute('PRAGMA busy_timeout=100')
        if not conn.execute('SELECT 1 FROM project WHERE id=?',(pid,)).fetchone():return None
        conn.execute('''INSERT INTO pm_knowledge_gap VALUES(?,?,?,1,?,?,'open') ON CONFLICT(id) DO UPDATE SET
          occurrences=pm_knowledge_gap.occurrences+1,last_seen_at=excluded.last_seen_at,state='open' ''',(ident,pid,question.strip(),now,now))
    return ident


def observe(pid,question):
    try:return record(pid,question)
    except Exception:
        import logging
        logging.getLogger('paw.pm.knowledge_gaps').warning('知识缺口记录暂不可用')
        return None


def read(conn,pid=None):
    return [dict(r) for r in conn.execute("SELECT g.*,p.name AS project_name FROM pm_knowledge_gap g JOIN project p ON p.id=g.project_id WHERE g.state='open' AND p.status NOT IN ('deleted','archived','merged')"+
        (' AND g.project_id=?' if pid else '')+' ORDER BY g.last_seen_at DESC,g.id',(pid,) if pid else ())]
