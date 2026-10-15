"""Agy Chat History Store — assistant interaction history, NOT Formal State.

Deliberately independent of the canonical workspace database.  A conversation
here never becomes Knowledge / Project / Candidate / Todo / ApprovedChange by
itself; only an explicit user request may start the governance chain.  That is
what keeps ``FORMAL_STATE_BYPASS = 0`` meaningful.

Persisted
    the VISIBLE transcript only — what the user typed, and the final answer Agy
    showed the user, plus a thin grounding summary
    (project_id / intent / grounded / evidence_count).

Never persisted
    chain-of-thought, hidden reasoning, the system prompt, the internal PAW
    prompt, the machine evidence map, or any credential/token.

Storage is a small standalone SQLite file under the agent-jobs directory, in
keeping with the existing SQLite + local files + lightweight Python
architecture.  No new daemon, no external database.

Retention
    Rolling 7 days, not a weekly batch wipe (spec §11).  Messages older than
    the window are deleted; a session that no longer has any message inside the
    window is deleted outright, together with its local runtime session JSON.
    There is deliberately **no archive**: this store must never become a second
    permanent knowledge base.  If a conversation turns out to matter, the user
    asks for it to be recorded into the workspace, and that goes through PAW
    Governance like everything else.

Read-only with respect to PAW canonical data: this module never opens
``workspace.db``.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional
import os
from backend.core.config import DATA_DIR

HISTORY_DB = Path(os.getenv("PAW_CHAT_HISTORY_DB", str(DATA_DIR / "chat_history.db")))
RUNTIME_SESSION_DIR = DATA_DIR / "agy_sessions"
try:
    RUNTIME_SESSION_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

#: Rolling retention window (spec §11).
RETENTION_DAYS = 7

#: deterministic title budget (spec §17.5) — never call a model for this
MAX_TITLE_CHARS = 28

ROLE_USER = "user"
ROLE_AGY = "agy"
ROLES = (ROLE_USER, ROLE_AGY)

STATUS_PENDING = "pending"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUSES = (STATUS_PENDING, STATUS_SUCCESS, STATUS_FAILED)

MAX_MESSAGES_PER_SESSION = 2000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agy_chat_session (
    session_id      TEXT PRIMARY KEY,
    title           TEXT NOT NULL DEFAULT '',
    model           TEXT NOT NULL DEFAULT '',
    mode            TEXT NOT NULL DEFAULT 'chat',
    project_id      TEXT,
    conversation_id TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    message_count   INTEGER NOT NULL DEFAULT 0,
    archived        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_agy_chat_session_recent
    ON agy_chat_session(archived, updated_at DESC);

CREATE TABLE IF NOT EXISTS agy_chat_message (
    message_id     TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL,
    role           TEXT NOT NULL,
    content        TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'success',
    project_id     TEXT,
    intent         TEXT,
    grounded       INTEGER,
    evidence_count INTEGER
);
CREATE INDEX IF NOT EXISTS idx_agy_chat_message_session
    ON agy_chat_message(session_id, created_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def title_from(text: str) -> str:
    """Deterministic title from the first user message (spec §17.5).

    No model call, no extra quota: collapse whitespace and truncate.
    """
    flat = " ".join(str(text or "").split())
    if not flat:
        return "新对话"
    if len(flat) <= MAX_TITLE_CHARS:
        return flat
    return flat[:MAX_TITLE_CHARS] + "…"


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    HISTORY_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(HISTORY_DB), timeout=15.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=15000")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_schema() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)


# ---------------------------------------------------------------- sessions
def create_session(session_id: str, *, model: str = "", mode: str = "chat",
                   project_id: Optional[str] = None,
                   conversation_id: Optional[str] = None,
                   title: str = "") -> dict[str, Any]:
    ensure_schema()
    ts = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO agy_chat_session "
            "(session_id,title,model,mode,project_id,conversation_id,"
            " created_at,updated_at,message_count,archived) "
            "VALUES (?,?,?,?,?,?,?,?,0,0)",
            (session_id, title, model, mode, project_id, conversation_id, ts, ts))
    return get_session(session_id) or {}


def touch_session(session_id: str, *, project_id: Optional[str] = None,
                  conversation_id: Optional[str] = None,
                  model: Optional[str] = None,
                  mode: Optional[str] = None) -> None:
    sets = ["updated_at = ?"]
    args: list[Any] = [_now()]
    if project_id:
        sets.append("project_id = ?")
        args.append(project_id)
    if conversation_id:
        sets.append("conversation_id = ?")
        args.append(conversation_id)
    if model:
        sets.append("model = ?")
        args.append(model)
    if mode:
        sets.append("mode = ?")
        args.append(mode)
    args.append(session_id)
    with _connect() as conn:
        conn.execute("UPDATE agy_chat_session SET %s WHERE session_id = ?"
                     % ", ".join(sets), args)


def set_title_if_empty(session_id: str, title: str) -> None:
    if not title:
        return
    with _connect() as conn:
        conn.execute(
            "UPDATE agy_chat_session SET title = ? "
            "WHERE session_id = ? AND (title IS NULL OR title = '')",
            (title, session_id))


def get_session(session_id: str) -> Optional[dict[str, Any]]:
    ensure_schema()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM agy_chat_session WHERE session_id = ?",
            (session_id,)).fetchone()
    return dict(row) if row else None


def list_sessions(limit: int = 50, *, include_archived: bool = False
                  ) -> list[dict[str, Any]]:
    """Most-recently-updated first — the order the sidebar renders."""
    ensure_schema()
    limit = max(1, min(int(limit or 50), 200))
    where = "" if include_archived else "WHERE archived = 0"
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM agy_chat_session %s "
            "ORDER BY updated_at DESC, rowid DESC LIMIT ?" % where,
            (limit,)).fetchall()
    return [dict(r) for r in rows]


def delete_session(session_id: str, *, purge_runtime: bool = True) -> bool:
    """Hard-delete a conversation.  There is no archive (spec §11).

    ``ARCHIVE_SUPPORT = NO``: a chat that the user is finished with must leave
    no permanent row behind, otherwise this store slowly becomes a second
    knowledge base.  The runtime session JSON is removed too, so the
    ``conversation_id`` reference dies with it.
    """
    ensure_schema()
    with _connect() as conn:
        cur = conn.execute("DELETE FROM agy_chat_message WHERE session_id = ?",
                           (session_id,))
        del_messages = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        cur = conn.execute("DELETE FROM agy_chat_session WHERE session_id = ?",
                           (session_id,))
        deleted = bool(cur.rowcount and cur.rowcount > 0)
    if purge_runtime:
        _purge_runtime_session(session_id)
    return deleted or del_messages > 0


def _purge_runtime_session(session_id: str) -> bool:
    """Remove the adapter's local session JSON for this conversation."""
    if not session_id or "/" in session_id or "\\" in session_id:
        return False
    try:
        path = RUNTIME_SESSION_DIR / ("%s.session.json" % session_id)
        if path.is_file():
            path.unlink()
            return True
    except OSError:
        return False
    return False


# ---------------------------------------------------------------- messages
def add_message(session_id: str, role: str, content: str, *,
                status: str = STATUS_SUCCESS,
                message_id: Optional[str] = None,
                project_id: Optional[str] = None,
                intent: Optional[str] = None,
                grounded: Optional[bool] = None,
                evidence_count: Optional[int] = None,
                set_title: bool = False) -> str:
    """Append one visible message.

    ``message_id`` is honoured when supplied so a client retry updates the
    existing row instead of creating a duplicate (spec §17.7).  A caller-supplied
    id is a *client* id: it is only unique within one session, while the primary
    key is global.  It is therefore namespaced by session — a replayed or
    colliding id can then neither drop this message nor rewrite a message that
    belongs to another session, and a retry inside the same session still maps
    to the same row, which is what idempotency requires.
    """
    if role not in ROLES:
        raise ValueError("invalid role: %s" % role)
    if status not in STATUSES:
        raise ValueError("invalid status: %s" % status)
    ensure_schema()
    if message_id:
        mid = "s:%s|%s" % (session_id, message_id)
    else:
        mid = "%s:%s" % ("u" if role == ROLE_USER else "a",
                         uuid.uuid4().hex)
    ts = _now()
    with _connect() as conn:
        existing = conn.execute(
            "SELECT 1 FROM agy_chat_message WHERE message_id = ? "
            "AND session_id = ?",
            (mid, session_id)).fetchone()
        if existing:
            conn.execute(
                "UPDATE agy_chat_message SET content = ?, status = ?, "
                "created_at = ? WHERE message_id = ?",
                (content, status, ts, mid))
        else:
            conn.execute(
                "INSERT INTO agy_chat_message "
                "(message_id,session_id,role,content,created_at,status,"
                " project_id,intent,grounded,evidence_count) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (mid, session_id, role, content, ts, status,
                 project_id, intent,
                 None if grounded is None else int(bool(grounded)),
                 evidence_count))
        conn.execute(
            "UPDATE agy_chat_session SET updated_at = ?, message_count = "
            "(SELECT COUNT(*) FROM agy_chat_message WHERE session_id = ?) "
            "WHERE session_id = ?", (ts, session_id, session_id))
        if set_title and role == ROLE_USER:
            conn.execute(
                "UPDATE agy_chat_session SET title = ? "
                "WHERE session_id = ? AND (title IS NULL OR title = '')",
                (title_from(content), session_id))
    return mid


def set_message_status(message_id: str, status: str, *,
                       content: Optional[str] = None) -> bool:
    if status not in STATUSES:
        raise ValueError("invalid status: %s" % status)
    ensure_schema()
    if content is None:
        with _connect() as conn:
            cur = conn.execute(
                "UPDATE agy_chat_message SET status = ? WHERE message_id = ?",
                (status, message_id))
    else:
        with _connect() as conn:
            cur = conn.execute(
                "UPDATE agy_chat_message SET status = ?, content = ? "
                "WHERE message_id = ?", (status, content, message_id))
    return cur.rowcount > 0


def list_messages(session_id: str, limit: int = MAX_MESSAGES_PER_SESSION
                  ) -> list[dict[str, Any]]:
    """Visible transcript in send order.  Never returns internal machinery."""
    ensure_schema()
    limit = max(1, min(int(limit or MAX_MESSAGES_PER_SESSION),
                       MAX_MESSAGES_PER_SESSION))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT message_id, role, content, created_at, status, "
            "project_id, intent, grounded, evidence_count "
            "FROM agy_chat_message WHERE session_id = ? "
            "ORDER BY rowid ASC LIMIT ?", (session_id, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["grounded"] = None if d["grounded"] is None else bool(d["grounded"])
        out.append(d)
    return out


def session_detail(session_id: str, limit: int = MAX_MESSAGES_PER_SESSION
                   ) -> Optional[dict[str, Any]]:
    session = get_session(session_id)
    if not session:
        return None
    return {
        "session_id": session["session_id"],
        "title": session["title"] or title_from(""),
        "model": session["model"],
        "mode": session["mode"],
        "project_id": session["project_id"],
        "conversation_id": session["conversation_id"],
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
        "message_count": session["message_count"],
        "archived": bool(session["archived"]),
        "messages": list_messages(session_id, limit),
    }


def session_summaries(limit: int = 50) -> list[dict[str, Any]]:
    """Exactly the fields the history list needs — no internal ids leaked."""
    out = []
    for s in list_sessions(limit):
        out.append({
            "session_id": s["session_id"],
            "title": s["title"] or title_from(""),
            "project_id": s["project_id"],
            "created_at": s["created_at"],
            "updated_at": s["updated_at"],
            "message_count": s["message_count"],
        })
    return out


def stats() -> dict[str, Any]:
    ensure_schema()
    with _connect() as conn:
        sessions = conn.execute(
            "SELECT COUNT(*) FROM agy_chat_session WHERE archived = 0"
        ).fetchone()[0]
        messages = conn.execute(
            "SELECT COUNT(*) FROM agy_chat_message").fetchone()[0]
    return {"store": str(HISTORY_DB), "sessions": sessions,
            "messages": messages, "canonical_db": "UNTOUCHED"}


# ---------------------------------------------------------------- retention
def run_retention(*, days: int = RETENTION_DAYS,
                  now: Optional[datetime] = None,
                  purge_runtime: bool = True) -> dict[str, Any]:
    """Rolling retention sweep (spec §11).

    Rolling, not a weekly batch wipe: *every* message older than the window is
    removed on *every* run, so the store never holds a conversation older than
    ``days`` regardless of when the sweep happens.

    A session whose messages all fell outside the window is deleted outright —
    metadata, transcript and its local runtime session JSON.  There is no
    archive tier.

    Timestamps are compared through SQLite's ``datetime()`` so that the ISO
    strings actually stored (``...+00:00``, with or without microseconds)
    normalise to the same UTC form instead of relying on string ordering.
    """
    ensure_schema()
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    window = max(1, int(days))
    cutoff = (reference - timedelta(days=window)).isoformat()

    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM agy_chat_message WHERE datetime(created_at) < datetime(?)",
            (cutoff,))
        deleted_messages = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

        # Sessions that no longer hold a single surviving message.
        rows = conn.execute(
            "SELECT s.session_id AS session_id FROM agy_chat_session s "
            "WHERE NOT EXISTS (SELECT 1 FROM agy_chat_message m "
            "                  WHERE m.session_id = s.session_id)"
        ).fetchall()
        doomed = [str(r["session_id"]) for r in rows]
        for session_id in doomed:
            conn.execute("DELETE FROM agy_chat_session WHERE session_id = ?",
                         (session_id,))

        remaining_messages = conn.execute(
            "SELECT COUNT(*) FROM agy_chat_message").fetchone()[0]
        remaining_sessions = conn.execute(
            "SELECT COUNT(*) FROM agy_chat_session").fetchone()[0]

    purged = 0
    if purge_runtime:
        for session_id in doomed:
            if _purge_runtime_session(session_id):
                purged += 1

    return {
        "retention_days": window,
        "cutoff": cutoff,
        "deleted_message_count": int(deleted_messages),
        "deleted_session_count": len(doomed),
        "remaining_message_count": int(remaining_messages),
        "remaining_session_count": int(remaining_sessions),
        "runtime_sessions_purged": purged,
        "archive_support": "NO",
        "canonical_workspace_db_writes": 0,
    }
