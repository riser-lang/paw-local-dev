#!/usr/bin/env python3
"""PAW v1.3B Retrieval Core (shadow implementation).

Contract-first minimal implementation:
- ONE derived retrieval table (retrieval_document) + ONE FTS5 virtual table.
- The index is NEVER authority: every Current query revalidates candidates
  through TemporalAuthorityResolver before returning.
- Logical modes (CURRENT / TEMPORAL / EVIDENCE) share one physical surface.
- Deterministic. MODEL_CALLS = 0. No LLM, no embeddings, no external search.

Derived-only guarantee: dropping retrieval_document / retrieval_document_fts
never touches Formal State (knowledge, project, metric, evidence,
source_version, approved_change, commit_record).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paw_temporal_authority import TemporalAuthorityResolver, query_formal_metric

SCHEMA = "paw.retrieval.v13b/1"
DEFAULT_DB = os.environ.get(
    "PAW_WORKSPACE_DB", os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db")))
READER_ROOT = os.environ.get(
    "PAW_READER_ROOT", "/var/lib/paw-wps-mirror/incremental_reader")

EFFECTIVE_CURRENT = "EFFECTIVE_CURRENT"
TEMPORAL_HISTORY_INCOMPLETE = "TEMPORAL_HISTORY_INCOMPLETE"
RETRIEVAL_STALE = "RETRIEVAL_STALE"

# ------------------------------------------------------------------
# Schema (idempotent, derived-only)
# ------------------------------------------------------------------
_DDL = [
    """
    CREATE TABLE IF NOT EXISTS retrieval_document (
        document_id TEXT PRIMARY KEY,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        revision_ref TEXT,
        title TEXT NOT NULL DEFAULT '',
        search_text TEXT NOT NULL DEFAULT '',
        authority_state TEXT,
        valid_from TEXT,
        valid_to TEXT,
        known_from TEXT,
        known_to TEXT,
        canonical INTEGER NOT NULL DEFAULT 0,
        source_refs_json TEXT NOT NULL DEFAULT '[]',
        evidence_refs_json TEXT NOT NULL DEFAULT '[]',
        project_ids_json TEXT NOT NULL DEFAULT '[]',
        input_watermark TEXT,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_retrieval_entity"
    " ON retrieval_document(entity_type, entity_id)",
    "CREATE INDEX IF NOT EXISTS idx_retrieval_watermark"
    " ON retrieval_document(input_watermark)",
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS retrieval_document_fts USING fts5(
        title, search_text,
        document_id UNINDEXED,
        tokenize='trigram'
    )
    """,
]


def _connect(db_path: str | Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(db_path), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    return db


def ensure_schema(db_path: str | Path) -> None:
    """Idempotent derived-schema migration. Never mutates Formal State."""
    with _connect(db_path) as db:
        for stmt in _DDL:
            db.execute(stmt)


def _connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


# ------------------------------------------------------------------
# Deterministic search_text extraction (no new business prose)
# ------------------------------------------------------------------
_MAX_TEXT = 20000


def _strings_from_json(node: Any, depth: int = 0, out: list[str] | None = None) -> list[str]:
    """Deterministically collect existing string values from formal JSON."""
    if out is None:
        out = []
    if depth > 6 or len(out) > 400:
        return out
    if isinstance(node, str):
        if node.strip():
            out.append(node.strip())
    elif isinstance(node, dict):
        for k in sorted(node.keys()):
            if k in ("id", "uuid", "hash", "fingerprint"):
                continue
            _strings_from_json(node[k], depth + 1, out)
    elif isinstance(node, list):
        for item in node:
            _strings_from_json(item, depth + 1, out)
    return out


def build_search_text(title: str, one_line_summary: str, content_json: Any) -> str:
    parts = [title.strip(), one_line_summary.strip()]
    if isinstance(content_json, str):
        try:
            content_json = json.loads(content_json)
        except (ValueError, TypeError):
            content_json = {}
    parts.extend(_strings_from_json(content_json))
    text = " ".join(p for p in parts if p)
    return text[:_MAX_TEXT]


# ------------------------------------------------------------------
# Bootstrap / incremental refresh
# ------------------------------------------------------------------
def _knowledge_rows(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT p.id, p.title, p.one_line_summary, p.status,"
        " p.confirmed_revision_no, p.confirmed_at, p.updated_at,"
        " r.id AS rev_id, r.revision_no, r.content_json, r.created_at AS rev_created_at"
        " FROM knowledge_page p"
        " LEFT JOIN knowledge_revision r"
        "   ON r.knowledge_id = p.id AND r.revision_no = COALESCE("
        "     p.confirmed_revision_no,"
        "     (SELECT MAX(kr.revision_no) FROM knowledge_revision kr"
        "       WHERE kr.knowledge_id = p.id))"
    ).fetchall()


def _upsert_document(db: sqlite3.Connection, doc: dict[str, Any]) -> None:
    old = db.execute(
        "SELECT rowid FROM retrieval_document_fts WHERE document_id = ?",
        (doc["document_id"],),
    ).fetchone()
    if old:
        db.execute("DELETE FROM retrieval_document_fts WHERE rowid = ?", (old[0],))
    db.execute(
        "INSERT INTO retrieval_document(document_id, entity_type, entity_id,"
        " revision_ref, title, search_text, authority_state, valid_from, valid_to,"
        " known_from, known_to, canonical, source_refs_json, evidence_refs_json,"
        " project_ids_json, input_watermark, updated_at)"
        " VALUES(:document_id, :entity_type, :entity_id, :revision_ref, :title,"
        " :search_text, :authority_state, :valid_from, :valid_to, :known_from,"
        " :known_to, :canonical, :source_refs_json, :evidence_refs_json,"
        " :project_ids_json, :input_watermark, :updated_at)"
        " ON CONFLICT(document_id) DO UPDATE SET"
        " revision_ref=excluded.revision_ref, title=excluded.title,"
        " search_text=excluded.search_text, authority_state=excluded.authority_state,"
        " valid_from=excluded.valid_from, valid_to=excluded.valid_to,"
        " known_from=excluded.known_from, known_to=excluded.known_to,"
        " canonical=excluded.canonical, source_refs_json=excluded.source_refs_json,"
        " evidence_refs_json=excluded.evidence_refs_json,"
        " project_ids_json=excluded.project_ids_json,"
        " input_watermark=excluded.input_watermark, updated_at=excluded.updated_at",
        doc,
    )
    db.execute(
        "INSERT INTO retrieval_document_fts(title, search_text, document_id)"
        " VALUES(?, ?, ?)",
        (doc["title"], doc["search_text"], doc["document_id"]),
    )


def _knowledge_doc(db: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    kid = row["id"]
    src = db.execute(
        "SELECT source_page_id FROM knowledge_source_link WHERE knowledge_id = ?",
        (kid,),
    ).fetchall()
    prj = db.execute(
        "SELECT project_id FROM knowledge_project_link WHERE knowledge_id = ?",
        (kid,),
    ).fetchall()
    confirmed = row["confirmed_revision_no"] is not None
    status = row["status"] or ""
    canonical = 1 if (confirmed and status == "current") else 0
    # Patch 1: watermark covers revision identity, not just a timestamp.
    watermark = (f"{row['rev_id']}@{row['revision_no']}|"
                 f"{row['updated_at'] or row['confirmed_at'] or ''}")
    return {
        "document_id": f"knowledge:{kid}",
        "entity_type": "knowledge",
        "entity_id": kid,
        "revision_ref": (f"knowledge_revision:{row['rev_id']}"
                         if row["rev_id"] else None),
        "title": row["title"] or "",
        "search_text": build_search_text(
            row["title"] or "", "" if canonical else row["one_line_summary"] or "", row["content_json"]),
        "authority_state": (EFFECTIVE_CURRENT if canonical else
                            ("ARCHIVED" if status == "deprecated" else status.upper() or None)),
        "valid_from": None, "valid_to": None,
        "known_from": row["rev_created_at"],
        "known_to": None,
        "canonical": canonical,
        "source_refs_json": json.dumps([r["source_page_id"] for r in src]),
        "evidence_refs_json": json.dumps([r["source_page_id"] for r in src]),
        "project_ids_json": json.dumps([r["project_id"] for r in prj]),
        "input_watermark": watermark,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def bootstrap_index(db_path: str | Path) -> dict[str, Any]:
    """Full derived index build from Formal State only. No raw Source reread."""
    t0 = time.time()
    ensure_schema(db_path)
    stats = {"knowledge_indexed": 0, "current_indexed": 0,
             "temporal_indexed": 0, "excluded_draft": 0, "excluded_no_revision": 0}
    with _connect(db_path) as db:
        for row in _knowledge_rows(db):
            if row["status"] == "draft":
                stats["excluded_draft"] += 1
                continue
            if row["rev_id"] is None:
                # No revision at all: nothing formal to index.
                stats["excluded_no_revision"] += 1
                continue
            doc = _knowledge_doc(db, row)
            _upsert_document(db, doc)
            stats["knowledge_indexed"] += 1
            if doc["canonical"]:
                stats["current_indexed"] += 1
            else:
                stats["temporal_indexed"] += 1
    stats["bootstrap_seconds"] = round(time.time() - t0, 2)
    stats["schema"] = SCHEMA
    return stats


def refresh_knowledge(db_path: str | Path, knowledge_id: str) -> dict[str, Any]:
    """Bounded incremental refresh for ONE knowledge entity."""
    ensure_schema(db_path)
    with _connect(db_path) as db:
        row = db.execute(
            "SELECT p.id, p.title, p.one_line_summary, p.status,"
            " p.confirmed_revision_no, p.confirmed_at, p.updated_at,"
            " r.id AS rev_id, r.revision_no, r.content_json,"
            " r.created_at AS rev_created_at"
            " FROM knowledge_page p"
            " LEFT JOIN knowledge_revision r"
            "   ON r.knowledge_id = p.id AND r.revision_no = p.confirmed_revision_no"
            " WHERE p.id = ?", (knowledge_id,),
        ).fetchone()
        if row is None:
            db.execute("DELETE FROM retrieval_document WHERE document_id = ?",
                       (f"knowledge:{knowledge_id}",))
            fts = db.execute(
                "SELECT rowid FROM retrieval_document_fts WHERE document_id = ?",
                (f"knowledge:{knowledge_id}",)).fetchone()
            if fts:
                db.execute("DELETE FROM retrieval_document_fts WHERE rowid = ?",
                           (fts[0],))
            return {"documents_touched": 1, "documents_deleted": 1,
                    "documents_inserted": 0, "documents_updated": 0,
                    "full_reindex_triggered": False}
        if row["status"] == "draft" or row["rev_id"] is None:
            existed = db.execute(
                "SELECT 1 FROM retrieval_document WHERE document_id = ?",
                (f"knowledge:{knowledge_id}",)).fetchone()
            if existed:
                db.execute("DELETE FROM retrieval_document WHERE document_id = ?",
                           (f"knowledge:{knowledge_id}",))
                fts = db.execute(
                    "SELECT rowid FROM retrieval_document_fts WHERE document_id = ?",
                    (f"knowledge:{knowledge_id}",)).fetchone()
                if fts:
                    db.execute("DELETE FROM retrieval_document_fts WHERE rowid = ?",
                               (fts[0],))
                return {"documents_touched": 1, "documents_deleted": 1,
                        "documents_inserted": 0, "documents_updated": 0,
                        "full_reindex_triggered": False}
            return {"documents_touched": 0, "documents_deleted": 0,
                    "documents_inserted": 0, "documents_updated": 0,
                    "full_reindex_triggered": False}
        doc = _knowledge_doc(db, row)
        _upsert_document(db, doc)
    return {"documents_touched": 1, "documents_deleted": 0,
            "documents_inserted": 0, "documents_updated": 1,
            "full_reindex_triggered": False}


# ------------------------------------------------------------------
# Lexical recall (FTS trigram + bounded LIKE fallback for short terms)
# ------------------------------------------------------------------
_LOGGER = logging.getLogger(__name__)
_FTS_TOKEN = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
_LIKE_MAX_QUERY_CHARS = 512


def _fts_query(query: str) -> str:
    """Build a quoted FTS5 query from user supplied text."""
    tokens = _FTS_TOKEN.findall(query[:_LIKE_MAX_QUERY_CHARS])
    return " AND ".join('"' + token.replace('"', '""') + '"'
                        for token in tokens)


def _like_pattern(query: str) -> str:
    """Escape LIKE metacharacters and cap the user-controlled search term."""
    term = query.strip()[:_LIKE_MAX_QUERY_CHARS]
    term = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{term}%"


def _recall(db: sqlite3.Connection, query: str, limit: int) -> list[sqlite3.Row]:
    """FTS trigram recall with a bounded LIKE fallback for every query."""
    q = query.strip()
    rows: list[sqlite3.Row] = []
    fts_query = _fts_query(q)
    if fts_query:
        try:
            rows = db.execute(
                "SELECT f.document_id, bm25(retrieval_document_fts) AS score"
                " FROM retrieval_document_fts f"
                " WHERE retrieval_document_fts MATCH ?"
                " ORDER BY score LIMIT ?",
                (fts_query, max(1, limit * 3)),
            ).fetchall()
        except sqlite3.Error as exc:
            # Query text is intentionally excluded from diagnostic logs.
            _LOGGER.warning("retrieval FTS query failed (%s); using LIKE fallback",
                            type(exc).__name__)
    if not rows and q:
        # SQL wildcards are escaped so the fallback remains a literal substring search.
        pattern = _like_pattern(q)
        rows = db.execute(
            "SELECT document_id, -1.0 AS score FROM retrieval_document"
            " WHERE search_text LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' LIMIT ?",
            (pattern, pattern, max(1, limit * 3)),
        ).fetchall()
    return rows


def _hydrate(db: sqlite3.Connection, doc_ids: list[str]) -> list[sqlite3.Row]:
    if not doc_ids:
        return []
    marks = ",".join("?" for _ in doc_ids)
    return db.execute(
        f"SELECT * FROM retrieval_document WHERE document_id IN ({marks})",
        doc_ids).fetchall()


def _result(doc: sqlite3.Row, score: float, **extra: Any) -> dict[str, Any]:
    out = {
        "schema": SCHEMA,
        "entity_type": doc["entity_type"],
        "entity_id": doc["entity_id"],
        "revision_ref": doc["revision_ref"],
        "title": doc["title"],
        "snippet": (doc["search_text"] or "")[:200],
        "score": round(float(score), 4),
        "authority_state": doc["authority_state"],
        "temporal_state": None,
        "canonical": bool(doc["canonical"]),
        "valid_from": doc["valid_from"],
        "valid_to": doc["valid_to"],
        "known_from": doc["known_from"],
        "known_to": doc["known_to"],
        "source_refs": json.loads(doc["source_refs_json"] or "[]"),
        "evidence_refs": json.loads(doc["evidence_refs_json"] or "[]"),
        "project_refs": json.loads(doc["project_ids_json"] or "[]"),
        "input_watermark": doc["input_watermark"],
        "deterministic": True,
        "model_call": False,
    }
    out.update(extra)
    return out


# ------------------------------------------------------------------
# Patch 1: knowledge lineage / retrieval safety gate
# Authority (which revision is current) is SEPARATE from canonical
# safety (does it possess governed source/evidence lineage).
# ------------------------------------------------------------------
LINEAGE_PROVEN = "PROVEN"
LINEAGE_UNTRACEABLE = "UNTRACEABLE"
EVIDENCE_LOCATOR_READY = "LOCATOR_READY"
EVIDENCE_PARTIAL = "PARTIAL"
EVIDENCE_NONE = "NONE"


def _ac_proven_knowledge_ids(db: sqlite3.Connection) -> set[str]:
    """Knowledge ids targeted by committed ApprovedChanges with governed
    source_refs. Includes slug resolution (create payloads carry slug)."""
    slug_to_id = {r["slug"]: r["id"] for r in db.execute(
        "SELECT id, slug FROM knowledge_page").fetchall()}
    proven: set[str] = set()
    for r in db.execute(
        "SELECT payload FROM approved_change"
        " WHERE target_type='knowledge' AND status='committed'"
        " AND source_refs IS NOT NULL AND source_refs != '[]'",
    ).fetchall():
        try:
            payload = json.loads(r["payload"])
        except (ValueError, TypeError):
            continue
        kid = (payload.get("id") or payload.get("knowledge_id")
               or slug_to_id.get(payload.get("slug")))
        if kid:
            proven.add(kid)
    return proven


def _knowledge_lineage(
    db: sqlite3.Connection, knowledge_id: str,
    ac_proven_ids: set[str] | None = None,
    *, revision_ref: str | None = None,
) -> dict[str, Any]:
    """Deterministic lineage inspection over EXISTING paths only.
    Paths: knowledge_source_link (-> wiki_page.wps_document_id),
    committed ApprovedChange with governed source_refs,
    revision content_json explicit source refs."""
    source_version_ids: list[str] = []
    source_page_refs: list[str] = []
    evidence_refs: list[str] = []
    # Path 1: knowledge_source_link -> wiki_page
    for r in db.execute(
        "SELECT wp.id AS source_page_id, wp.wps_document_id FROM knowledge_source_link ksl"
        " JOIN wiki_page wp ON wp.id = ksl.source_page_id"
        " WHERE ksl.knowledge_id = ?", (knowledge_id,),
    ).fetchall():
        source_page_refs.append('wiki_page:'+str(r['source_page_id']))
        value=str(r['wps_document_id'] or '')
        if re.fullmatch(r'[A-Za-z0-9_-]+@\d+',value):
            source_version_ids.append(value)
    # Path 2: committed ApprovedChange with non-empty governed source_refs
    if ac_proven_ids is None:
        ac_proven_ids = _ac_proven_knowledge_ids(db)
    ac_proven = knowledge_id in ac_proven_ids
    # Path 3: revision content_json explicit source refs
    rev_proven = False
    # An unconfirmed/newer revision must not lend its source refs to the
    # authority-selected current or historical revision.
    selected_id=(revision_ref.split(':',1)[1] if revision_ref and revision_ref.startswith('knowledge_revision:') else None)
    for r in db.execute(
        "SELECT content_json FROM knowledge_revision WHERE knowledge_id = ? AND id = ?",
        (knowledge_id,selected_id),
    ).fetchall():
        try:
            d = json.loads(r["content_json"])
        except (ValueError, TypeError):
            continue
        if isinstance(d, dict) and any(
                d.get(k) for k in ("source_refs", "source_version_ids",
                                   "evidence_refs", "sources", "source_pages")):
            rev_proven = True
            break
    proven = bool(source_page_refs) or ac_proven or rev_proven
    lineage_state = LINEAGE_PROVEN if proven else LINEAGE_UNTRACEABLE
    if source_version_ids:
        # A SourceVersion/page link is lineage, not a structural locator.
        # Never synthesize wildcard EvidenceRefs or claim exact traceability.
        evidence_state = EVIDENCE_PARTIAL
        evidence_refs = []
    elif proven:
        evidence_state = EVIDENCE_PARTIAL
    else:
        evidence_state = EVIDENCE_NONE
    return {
        "source_lineage_state": lineage_state,
        "evidence_state": evidence_state,
        "source_version_ids": sorted(dict.fromkeys(source_version_ids)),
        "source_page_refs": sorted(dict.fromkeys(source_page_refs)),
        "evidence_refs": evidence_refs,
    }


def evaluate_knowledge_retrieval_safety(
    knowledge_id: str,
    revision_no: int | None = None,
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """ONE deterministic canonical-safety gate. Calls the existing
    Authority layer (TemporalAuthorityResolver); does NOT duplicate it.
    canonical = Authority EFFECTIVE_CURRENT AND governed lineage PROVEN.
    Evidence locator completeness is disclosed separately, never flattened."""
    path = str(db_path or DEFAULT_DB)
    resolver = TemporalAuthorityResolver(path)
    auth = resolver.resolve_current(entity_type="knowledge", entity_id=knowledge_id)
    with _connect_readonly(path) as db:
        lineage = _knowledge_lineage(db, knowledge_id, revision_ref=auth.get('resolved_ref'))
    authority_state = auth.get("authority_state")
    canonical = (
        authority_state == EFFECTIVE_CURRENT
        and lineage["source_lineage_state"] == LINEAGE_PROVEN
    )
    provisional_reason = None
    if authority_state == EFFECTIVE_CURRENT and not canonical:
        provisional_reason = "LEGACY_UNTRACEABLE"
    elif authority_state == TEMPORAL_HISTORY_INCOMPLETE:
        provisional_reason = "TEMPORAL_HISTORY_INCOMPLETE"
    elif authority_state not in (EFFECTIVE_CURRENT,):
        provisional_reason = authority_state
    return {
        "schema": SCHEMA,
        "knowledge_id": knowledge_id,
        "revision_no": revision_no,
        "authority_state": authority_state,
        "source_lineage_state": lineage["source_lineage_state"],
        "evidence_state": lineage["evidence_state"],
        "canonical": canonical,
        "provisional_reason": provisional_reason,
        "source_version_ids": lineage["source_version_ids"],
        "source_page_refs": lineage["source_page_refs"],
        "evidence_refs": lineage["evidence_refs"],
        "deterministic": True,
        "model_call": False,
    }


def _formal_watermark(db: sqlite3.Connection, knowledge_id: str) -> str | None:
    """Deterministic Formal watermark: revision identity + page updated_at."""
    row = db.execute(
        "SELECT p.updated_at, r.id AS rev_id, r.revision_no"
        " FROM knowledge_page p"
        " LEFT JOIN knowledge_revision r"
        "   ON r.knowledge_id = p.id AND r.revision_no = COALESCE("
        "     p.confirmed_revision_no,"
        "     (SELECT MAX(kr.revision_no) FROM knowledge_revision kr"
        "       WHERE kr.knowledge_id = p.id))"
        " WHERE p.id = ?", (knowledge_id,),
    ).fetchone()
    if row is None or row["rev_id"] is None:
        return None
    return f"{row['rev_id']}@{row['revision_no']}|{row['updated_at'] or ''}"


def _resolved_revision_content(db, knowledge_id, auth):
    """Load exactly the authority-selected revision, never the index body.

    Index recall is derived. It cannot decide which revision supplies an
    answer, especially for as-of queries or a newer unconfirmed page summary.
    """
    ref=auth.get('resolved_ref') or ''
    if not ref.startswith('knowledge_revision:'):
        return None
    row=db.execute('SELECT id, revision_no, content_json FROM knowledge_revision WHERE knowledge_id=? AND id=?',
                   (knowledge_id,ref.split(':',1)[1])).fetchone()
    if row is None:
        return None
    return {'revision_ref':ref,'revision_no':row['revision_no'],
            'snippet':build_search_text('', '', row['content_json'])[:200]}


# ------------------------------------------------------------------
# CURRENT search (authority revalidation mandatory)
# ------------------------------------------------------------------
def search_current(
    query: str,
    *,
    entity_types: list[str] | None = None,
    project_id: str | None = None,
    limit: int = 20,
    include_provisional: bool = False,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    path = str(db_path or DEFAULT_DB)
    resolver = TemporalAuthorityResolver(path)
    items: list[dict[str, Any]] = []
    incomplete = 0
    stale_count = 0
    untraceable_count = 0
    with _connect_readonly(path) as db:
        recalled = _recall(db, query, limit)
        docs = _hydrate(db, [r["document_id"] for r in recalled])
        score_by_id = {r["document_id"]: r["score"] for r in recalled}
        ac_proven = _ac_proven_knowledge_ids(db)
        for doc in docs:
            if entity_types and doc["entity_type"] not in entity_types:
                continue
            if project_id and project_id not in json.loads(
                    doc["project_ids_json"] or "[]"):
                continue
            if doc["entity_type"] != "knowledge":
                continue
            # MANDATORY authority revalidation: never trust stored index state.
            auth = resolver.resolve_current(
                entity_type="knowledge", entity_id=doc["entity_id"])
            state = auth.get("authority_state")
            if state == EFFECTIVE_CURRENT:
                selected = _resolved_revision_content(db, doc['entity_id'], auth)
                if selected is None or selected['revision_ref'] != doc['revision_ref']:
                    stale_count += 1
                    continue
                # Patch 1: watermark staleness check (revision identity).
                formal_wm = _formal_watermark(db, doc["entity_id"])
                if formal_wm is not None and doc["input_watermark"] != formal_wm:
                    stale_count += 1
                    if include_provisional:
                        items.append(_result(
                            doc, score_by_id.get(doc["document_id"], 0.0),
                            authority_state=state, canonical=False,
                            provisional_reason="RETRIEVAL_STALE"))
                    continue
                # Patch 1: canonical safety gate (governed lineage).
                lineage = _knowledge_lineage(db, doc["entity_id"], ac_proven,revision_ref=selected['revision_ref'])
                if lineage["source_lineage_state"] == LINEAGE_PROVEN:
                    items.append(_result(
                        doc, score_by_id.get(doc["document_id"], 0.0),
                        authority_state=state,
                        canonical=True,
                        source_lineage_state=lineage["source_lineage_state"],
                        evidence_state=lineage["evidence_state"],
                        source_version_ids=lineage["source_version_ids"],
                        source_page_refs=lineage["source_page_refs"],
                        evidence_refs=lineage["evidence_refs"],
                        valid_from=auth.get("valid_from"),
                        valid_to=auth.get("valid_to"),
                        known_from=auth.get("known_from"),
                        decision_ref=auth.get("decision_ref"),
                        evidence_fingerprint=auth.get("evidence_fingerprint"),
                        snippet=selected['snippet'],
                        revision_ref=selected['revision_ref'],
                    ))
                elif include_provisional:
                    untraceable_count += 1
                    items.append(_result(
                        doc, score_by_id.get(doc["document_id"], 0.0),
                        authority_state=state, canonical=False,
                        provisional_reason="LEGACY_UNTRACEABLE",
                        source_lineage_state=lineage["source_lineage_state"],
                        evidence_state=lineage["evidence_state"]))
                else:
                    untraceable_count += 1
            elif state == TEMPORAL_HISTORY_INCOMPLETE and include_provisional:
                incomplete += 1
                items.append(_result(
                    doc, score_by_id.get(doc["document_id"], 0.0),
                    authority_state=state,
                    canonical=False,
                    provisional_reason="TEMPORAL_HISTORY_INCOMPLETE",
                ))
            elif state == TEMPORAL_HISTORY_INCOMPLETE:
                incomplete += 1
            # All other states (UNRESOLVED / ARCHIVED / FUTURE_EFFECTIVE /
            # HISTORICAL) are excluded from Current results by default.
            if len(items) >= limit:
                break
    return {"schema": SCHEMA, "query": query, "mode": "current",
            "items": items[:limit], "count": len(items[:limit]),
            "incomplete_candidate_count": incomplete,
            "stale_candidate_count": stale_count,
            "untraceable_candidate_count": untraceable_count,
            "include_provisional": include_provisional,
            "model_call": False}


# ------------------------------------------------------------------
# TEMPORAL / AS-OF search (fail closed on missing valid-time)
# ------------------------------------------------------------------
def search_as_of(
    query: str,
    as_of: str,
    *,
    entity_types: list[str] | None = None,
    project_id: str | None = None,
    limit: int = 20,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    path = str(db_path or DEFAULT_DB)
    resolver = TemporalAuthorityResolver(path)
    items: list[dict[str, Any]] = []
    incomplete = 0
    with _connect_readonly(path) as db:
        recalled = _recall(db, query, limit)
        docs = _hydrate(db, [r["document_id"] for r in recalled])
        score_by_id = {r["document_id"]: r["score"] for r in recalled}
        for doc in docs:
            if entity_types and doc["entity_type"] not in entity_types:
                continue
            if project_id and project_id not in json.loads(
                    doc["project_ids_json"] or "[]"):
                continue
            if doc["entity_type"] != "knowledge":
                continue
            auth = resolver.resolve_current(
                entity_type="knowledge", entity_id=doc["entity_id"], as_of=as_of)
            state = auth.get("authority_state")
            if state in (EFFECTIVE_CURRENT, "HISTORICAL"):
                selected = _resolved_revision_content(db, doc['entity_id'], auth)
                if selected is None:
                    incomplete += 1
                    continue
                lineage = _knowledge_lineage(db,doc['entity_id'],revision_ref=selected['revision_ref'])
                items.append(_result(
                    doc, score_by_id.get(doc["document_id"], 0.0),
                    authority_state=state,
                    temporal_state="VALID_AT_" + as_of,
                    canonical=(state == EFFECTIVE_CURRENT and lineage['source_lineage_state']==LINEAGE_PROVEN),
                    source_lineage_state=lineage['source_lineage_state'],
                    evidence_state=lineage['evidence_state'],
                    source_version_ids=lineage['source_version_ids'],
                    source_page_refs=lineage['source_page_refs'],
                    evidence_refs=lineage['evidence_refs'],
                    valid_from=auth.get("valid_from"),
                    valid_to=auth.get("valid_to"),
                    known_from=auth.get("known_from"),
                    decision_ref=auth.get("decision_ref"),
                    snippet=selected['snippet'],
                    revision_ref=selected['revision_ref'],
                ))
            elif state == TEMPORAL_HISTORY_INCOMPLETE:
                incomplete += 1
            # FUTURE_EFFECTIVE / UNRESOLVED excluded.
            if len(items) >= limit:
                break
    return {"schema": SCHEMA, "query": query, "mode": "as_of", "as_of": as_of,
            "items": items[:limit], "count": len(items[:limit]),
            "incomplete_candidate_count": incomplete,
            "temporal_warning": (TEMPORAL_HISTORY_INCOMPLETE
                                 if incomplete else None),
            "model_call": False}


# ------------------------------------------------------------------
# EVIDENCE lookup (deterministic exact, fail closed)
# ------------------------------------------------------------------
_LOCATOR_ROOTS = ("text_blocks", "tables", "visual_pages",
                  "relationships", "embedded_objects")
_LOCATOR_RE = re.compile(
    r"^(text_blocks|tables|visual_pages|relationships|embedded_objects)"
    r"\[(\d+)\]$")
_SHEET_LOCATOR_RE = re.compile(
    r"sheets\[(\d+)\]\.cells\[([A-Z]{1,3}[1-9]\d{0,6}:[A-Z]{1,3}[1-9]\d{0,6})\]\Z")
_READER_MAX_BYTES = 64 * 1024 * 1024
_EVIDENCE_PAGE_MAX_CHARS = 32000
_EVIDENCE_PAGE_MAX_CELLS = 200
_EVIDENCE_CELL_FIELDS = ("coordinate", "row", "column", "raw_value",
                         "display_value", "formula", "data_type",
                         "number_format", "style_id")


def _parse_locator(locator: str) -> tuple[str, int] | None:
    """Deterministic structural locator parser. Whitelisted roots,
    non-negative integer index only. No eval, no JSONPath, no expressions."""
    m = _LOCATOR_RE.match((locator or "").strip())
    if m is None:
        return None
    return m.group(1), int(m.group(2))


def _spreadsheet_evidence_page(data: dict, sv: str, loc: str,
                               reader_path: Path, offset: int) -> dict[str, Any]:
    """Consume the materializer's OWN exact ref and sheet-order identity.

    No invented subrange, JSONPath, formula evaluation or second ref builder.
    Cells are content projections; visual/style fidelity is not claimed.
    """
    from paw_evidence_materializer import materialize_evidence_refs, _sheet_order

    match = _SHEET_LOCATOR_RE.fullmatch(loc)
    refs = materialize_evidence_refs(
        data, sid=sv, reader_path=str(reader_path),
        artifact_hash=str(data.get("raw_artifact_sha256") or ""),
        content_fingerprint=str(data.get("content_fingerprint") or ""))
    exact_refs = [ref for ref in refs
                  if ref.get("evidence_locator") == loc
                  and ref.get("evidence_kind") == "spreadsheet_range"]
    if len(exact_refs) != 1:
        return {"status": "FAIL_CLOSED", "reason": "spreadsheet_ref_not_unique_or_materialized"}
    semantics = data.get("structured_semantics")
    sheets = semantics.get("sheets") if isinstance(semantics, dict) else None
    if not isinstance(sheets, list):
        return {"status": "FAIL_CLOSED", "reason": "spreadsheet_reader_structure_missing"}
    order = int(match.group(1))
    selected = [sheet for index, sheet in enumerate(sheets)
                if isinstance(sheet, dict) and _sheet_order(sheet, index) == order]
    if len(selected) != 1:
        return {"status": "FAIL_CLOSED", "reason": "spreadsheet_sheet_order_ambiguous"}
    sheet = selected[0]
    raw_cells = sheet.get("cells")
    if not isinstance(raw_cells, list) or not raw_cells or any(
            not isinstance(cell, dict) for cell in raw_cells):
        return {"status": "FAIL_CLOSED", "reason": "spreadsheet_cells_malformed"}
    coordinates = set()
    omitted_fields = set()
    for cell in raw_cells:
        coord = cell.get("coordinate")
        cm = re.fullmatch(r"([A-Z]{1,3})([1-9]\d{0,6})", coord) if isinstance(coord, str) else None
        row, column = cell.get("row"), cell.get("column")
        if (cm is None or isinstance(row, bool) or isinstance(column, bool)
                or not isinstance(row, int) or not isinstance(column, int)):
            return {"status": "FAIL_CLOSED", "reason": "spreadsheet_cell_identity_invalid"}
        col = 0
        for letter in cm.group(1):
            col = col * 26 + ord(letter) - ord("A") + 1
        if row != int(cm.group(2)) or column != col or coord in coordinates:
            return {"status": "FAIL_CLOSED", "reason": "spreadsheet_cell_identity_invalid"}
        coordinates.add(coord)
        omitted_fields.update(set(cell) - set(_EVIDENCE_CELL_FIELDS))
    if offset >= len(raw_cells) and offset != 0:
        return {"status": "FAIL_CLOSED", "reason": "evidence_offset_out_of_range"}
    cells = []
    chars = 2
    for cell in raw_cells[offset:offset + _EVIDENCE_PAGE_MAX_CELLS]:
        projected = {key: cell[key] for key in _EVIDENCE_CELL_FIELDS if key in cell}
        encoded = json.dumps(projected, ensure_ascii=False, separators=(",", ":"))
        if chars + len(encoded) + 1 > _EVIDENCE_PAGE_MAX_CHARS:
            if not cells:
                return {"status": "EVIDENCE_NOT_AVAILABLE", "reason": "single_cell_exceeds_read_budget"}
            break
        cells.append(projected)
        chars += len(encoded) + 1
    next_offset = offset + len(cells) if offset + len(cells) < len(raw_cells) else None
    # Ref identity is unchanged; its optional excerpt is only a bounded
    # preview, never a second unbounded copy of cell values.
    ref = dict(exact_refs[0])
    full_preview = str(ref.get("excerpt") or "")
    ref["excerpt"] = full_preview[:500]
    ref["excerpt_is_preview"] = True
    ref["preview_excerpt_truncated"] = len(full_preview) > 500
    return {"status": "PASS", "block_type": "spreadsheet_range",
            "block_index": order, "sheet_order": order,
            "sheet_title": str(sheet.get("title") or "")[:300], "sheet_hidden": sheet.get("hidden"),
            "sheet_state": sheet.get("sheet_state"), "cell_range": match.group(2),
            "cells": cells, "excerpt": ref.get("excerpt"),
            "excerpt_is_preview": True,
            "preview_excerpt_truncated": len(full_preview) > 500,
            "evidence_refs": [ref], "pagination_unit": "observed_cells_in_reader_order",
            "page_offset": offset, "page_count": len(cells),
            "total_content_units": len(raw_cells), "next_offset": next_offset,
            "content_complete_in_this_response": offset == 0 and next_offset is None,
            "reached_content_end": next_offset is None,
            "coverage_scope": "EXACT_CITED_RANGE_ONLY_NOT_ENTIRE_DOCUMENT",
            "omitted_cell_fields": sorted(omitted_fields),
            "full_visual_style_fidelity_claimed": False}


def get_evidence(
    source_version_id: str,
    locator: str = "",
    *,
    db_path: str | Path | None = None,
    evidence_offset: int = 0,
    evidence_reader_sha256: str | None = None,
) -> dict[str, Any]:
    """Deterministic structural evidence lookup. FAIL CLOSED on invalid
    input. Evidence is what exists at the exact locator - never a fact
    generator. Identity is cross-checked against ReaderResult metadata."""
    if (isinstance(evidence_offset, bool) or not isinstance(evidence_offset, int)
            or not 0 <= evidence_offset <= 1000000):
        return {"schema": SCHEMA, "status": "FAIL_CLOSED",
                "reason": "invalid_evidence_offset", "model_call": False}
    if (evidence_reader_sha256 is not None and (
            not isinstance(evidence_reader_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", evidence_reader_sha256) is None)):
        return {"schema": SCHEMA, "status": "FAIL_CLOSED",
                "reason": "invalid_evidence_reader_sha256", "model_call": False}
    if evidence_offset and evidence_reader_sha256 is None:
        return {"schema": SCHEMA, "status": "FAIL_CLOSED",
                "reason": "reader_sha256_required_for_continuation", "model_call": False}
    sv = (source_version_id or "").strip()
    if "@" not in sv:
        return {"schema": SCHEMA, "status": "FAIL_CLOSED",
                "reason": "invalid_source_version_id", "model_call": False}
    doc_id, _, ver = sv.partition("@")
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',doc_id) or not re.fullmatch(r'\d{1,32}',ver):
        return {"schema": SCHEMA, "status": "FAIL_CLOSED",
                "reason": "invalid_source_version_id", "model_call": False}
    loc = (locator or "").strip()
    if not loc:
        return {"schema": SCHEMA, "status": "FAIL_CLOSED",
                "reason": "locator_required", "source_version_id": sv,
                "model_call": False}
    parsed = _parse_locator(loc)
    sheet_match = _SHEET_LOCATOR_RE.fullmatch(loc) if len(loc) <= 64 else None
    if parsed is None and sheet_match is None:
        return {"schema": SCHEMA, "status": "FAIL_CLOSED",
                "reason": "malformed_or_unknown_locator",
                "source_version_id": sv, "locator": loc, "model_call": False}
    root, idx = parsed if parsed is not None else ("spreadsheet_range", int(sheet_match.group(1)))
    reader_path = Path(READER_ROOT) / doc_id / ver / "reader_result.json"
    if not reader_path.resolve().is_relative_to(Path(READER_ROOT).resolve()):
        return {'schema':SCHEMA,'status':'FAIL_CLOSED','reason':'reader_path_escape','model_call':False}
    if not reader_path.is_file():
        return {"schema": SCHEMA, "status": "EVIDENCE_NOT_AVAILABLE",
                "reason": "reader_result_missing",
                "source_version_id": sv, "locator": loc, "model_call": False}
    try:
        if reader_path.stat().st_size > _READER_MAX_BYTES:
            return {"schema": SCHEMA, "status": "EVIDENCE_NOT_AVAILABLE",
                    "reason": "reader_result_exceeds_read_budget", "model_call": False}
        reader_bytes = reader_path.read_bytes()
        if len(reader_bytes) > _READER_MAX_BYTES:
            return {"schema": SCHEMA, "status": "EVIDENCE_NOT_AVAILABLE",
                    "reason": "reader_result_exceeds_read_budget", "model_call": False}
        reader_sha = hashlib.sha256(reader_bytes).hexdigest()
        if evidence_reader_sha256 is not None and reader_sha != evidence_reader_sha256:
            return {"schema": SCHEMA, "status": "RETRIEVAL_STALE",
                    "reason": "reader_result_changed", "source_version_id": sv,
                    "locator": loc, "reader_result_sha256": reader_sha, "model_call": False}
        data = json.loads(reader_bytes)
    except (ValueError, OSError):
        return {"schema": SCHEMA, "status": "EVIDENCE_NOT_AVAILABLE",
                "reason": "reader_result_unreadable",
                "source_version_id": sv, "locator": loc, "model_call": False}
    # Identity cross-check against authoritative ReaderResult metadata.
    identity_level = "PATH_CONTRACT_ONLY"
    if isinstance(data, dict):
        ro = str(data.get("source_object_id") or "")
        rv = str(data.get("source_version") or "")
        if ro or rv:
            if ro != doc_id or rv != ver:
                return {"schema": SCHEMA, "status": "FAIL_CLOSED",
                        "reason": "identity_mismatch",
                        "source_version_id": sv,
                        "reader_identity": f"{ro}@{rv}", "model_call": False}
            identity_level = "READER_METADATA_VERIFIED"
    if sheet_match is not None:
        if identity_level != "READER_METADATA_VERIFIED":
            return {"schema": SCHEMA, "status": "FAIL_CLOSED",
                    "reason": "spreadsheet_reader_identity_required", "model_call": False}
        page = _spreadsheet_evidence_page(data, sv, loc, reader_path, evidence_offset)
        return {"schema": SCHEMA, "source_version_id": sv, "locator": loc,
                "reader_result_path": str(reader_path), "reader_result_sha256": reader_sha,
                "identity_verification": identity_level,
                "raw_artifact_available": data.get("raw_source_available"),
                "raw_artifact_sha256": data.get("raw_artifact_sha256"),
                "content_fingerprint": data.get("content_fingerprint"),
                "fidelity": "reader_structured_content_projection_not_visual",
                "canonical": False, "note": "evidence_describes_source_content_only",
                "model_call": False, **page}
    blocks = data.get(root) if isinstance(data, dict) else None
    if not isinstance(blocks, list):
        return {"schema": SCHEMA, "status": "FAIL_CLOSED",
                "reason": "unknown_root", "source_version_id": sv,
                "locator": loc, "model_call": False}
    if idx >= len(blocks):
        return {"schema": SCHEMA, "status": "FAIL_CLOSED",
                "reason": "index_out_of_range", "source_version_id": sv,
                "locator": loc, "root_length": len(blocks),
                "model_call": False}
    block = blocks[idx]
    excerpt = None
    if isinstance(block, str):
        excerpt = block
    elif isinstance(block, dict):
        for k in ("text", "content", "summary", "description", "title"):
            v = block.get(k)
            if isinstance(v, str) and v.strip():
                excerpt = v
                break
        if excerpt is None:
            excerpt = json.dumps(block, ensure_ascii=False)
    else:
        excerpt = json.dumps(block, ensure_ascii=False)
    if evidence_offset >= len(excerpt) and evidence_offset != 0:
        return {"schema": SCHEMA, "status": "FAIL_CLOSED",
                "reason": "evidence_offset_out_of_range", "model_call": False}
    content = excerpt[evidence_offset:evidence_offset + _EVIDENCE_PAGE_MAX_CHARS]
    next_offset = evidence_offset + len(content) if evidence_offset + len(content) < len(excerpt) else None
    # Derive actual raw artifact availability from ReaderResult metadata.
    raw_available = None
    if isinstance(data, dict) and "raw_source_available" in data:
        raw_available = bool(data.get("raw_source_available"))
    return {
        "schema": SCHEMA, "status": "PASS",
        "source_version_id": sv,
        "locator": loc,
        "block_type": root,
        "block_index": idx,
        "reader_result_path": str(reader_path),
        "excerpt": content,
        "pagination_unit": "characters",
        "page_offset": evidence_offset, "page_count": len(content),
        "total_content_units": len(excerpt), "next_offset": next_offset,
        "content_complete_in_this_response": evidence_offset == 0 and next_offset is None,
        "reached_content_end": next_offset is None,
        "coverage_scope": "EXACT_BLOCK_ONLY_NOT_ENTIRE_DOCUMENT",
        "reader_result_sha256": reader_sha,
        "evidence_refs": [{"source_version_id": sv, "evidence_locator": loc}],
        "fidelity": ("reader_result_json+reader_durable"
                     if raw_available is False else "reader_result_json"),
        "identity_verification": identity_level,
        "raw_artifact_available": raw_available,
        "canonical": False,
        "note": "evidence_describes_source_content_only",
        "model_call": False,
    }


# ------------------------------------------------------------------
# FORMAL METRIC query (reuse v1.3A; never FTS, never analysis fallback)
# ------------------------------------------------------------------
def query_formal_metrics(
    *,
    metric_id: str | None = None,
    metric_name: str | None = None,
    project_id: str | None = None,
    period: str | None = None,
    dimensions: dict[str, str] | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    path = str(db_path or DEFAULT_DB)
    with _connect_readonly(path) as db:
        if metric_id:
            ids = [metric_id]
        else:
            sql = "SELECT id FROM metric_definition WHERE 1=1"
            args: list[Any] = []
            if metric_name:
                sql += " AND name = ?"
                args.append(metric_name)
            if project_id:
                sql += " AND project_id = ?"
                args.append(project_id)
            sql += " ORDER BY id LIMIT 20"
            ids = [r[0] for r in db.execute(sql, args).fetchall()]
    results = []
    for mid in ids:
        r = query_formal_metric(
            metric_id=mid, db_path=path, period=period, dimensions=dimensions)
        results.append(r)
    return {"schema": SCHEMA, "mode": "formal_metric",
            "results": results, "count": len(results),
            "analysis_data_included": False, "model_call": False}


# ------------------------------------------------------------------
# RECENT BUSINESS CHANGES (derived SQL, no event table, no AI runs)
# ------------------------------------------------------------------
def get_recent_changes(
    *,
    since: str | None = None,
    project_id: str | None = None,
    entity_types: list[str] | None = None,
    limit: int = 50,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Derived query over commit_record + approved_change + audit_event.
    Excludes AI analysis runs, model logs, patrol runs, service restarts
    by construction (they never appear in commit_record)."""
    path = str(db_path or DEFAULT_DB)
    with _connect_readonly(path) as db:
        sql = (
            "SELECT cr.id AS commit_id, cr.committed_at, cr.result,"
            " ac.change_id, ac.target_type AS entity_type, ac.operation,"
            " ac.payload, ac.source_refs"
            " FROM commit_record cr"
            " JOIN approved_change ac ON ac.change_id = cr.change_id"
            " WHERE cr.result = 'success'"
        )
        args: list[Any] = []
        if since:
            sql += " AND cr.committed_at >= ?"
            args.append(since)
        if entity_types:
            sql += " AND ac.target_type IN (%s)" % ",".join("?" for _ in entity_types)
            args.extend(entity_types)
        if project_id:
            sql += " AND (ac.payload LIKE ?)"
            args.append(f'%"{project_id}"%')
        sql += " ORDER BY cr.committed_at DESC LIMIT ?"
        args.append(limit)
        rows = db.execute(sql, args).fetchall()
    items = []
    for r in rows:
        items.append({
            "change_type": f"{r['entity_type']}:{r['operation']}",
            "entity_type": r["entity_type"],
            "entity_id": r["change_id"],
            "committed_at": r["committed_at"],
            "change_ref": f"approved_change:{r['change_id']}",
            "commit_ref": f"commit_record:{r['commit_id']}",
            "source_refs": json.loads(r["source_refs"] or "[]"),
            "model_call": False,
        })
    return {"schema": SCHEMA, "mode": "recent_changes",
            "items": items, "count": len(items),
            "ai_runs_included": False, "model_call": False}


__all__ = [
    "SCHEMA", "ensure_schema", "bootstrap_index", "refresh_knowledge",
    "search_current", "search_as_of", "get_evidence",
    "query_formal_metrics", "get_recent_changes",
    "RETRIEVAL_STALE", "TEMPORAL_HISTORY_INCOMPLETE",
]
