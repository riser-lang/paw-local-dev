"""PAW v1.3 TD-002 — the single user-facing Knowledge Retrieval Contract.

Every user-callable *knowledge* search resolves recall through
``paw_retrieval_core.search_current``: FTS trigram + short-CJK fallback +
authority revalidation + lineage gate.  This module is the ONE adapter between
that contract and the workspace DTO shape, so no surface can drift back to an
ad-hoc substring scan.

What this module adds on top of the retrieval core
--------------------------------------------------
``rc.search_current`` passes the caller's text straight into an FTS5 ``MATCH``
expression.  FTS5 rejects queries containing operators such as ``(`` ``)``
``"`` ``*`` or ``-``; ``_recall`` swallows that error and returns no rows, so
titles like ``聘任会全流程ppt(1)`` and ``个性化-初试答案`` became unsearchable.
That is a real recall regression against the legacy surface this module
replaces — measured on production: of 67 queries, 21 lost rows, and the
hyphen/operator class accounted for most of them.

The fix is deterministic and monotone: when the raw query yields nothing, the
query is retried ONCE as a quoted FTS5 phrase.  A retry can only turn an empty
result into a non-empty one — it can never narrow a result set that already
matched — so the retry is driven by the empty result alone, not by a
character class, which could always miss a case.  Nothing else about the
retrieval core is changed.

One residual, disclosed difference: a trigram index cannot serve a query
shorter than 3 characters.  The retrieval core already covers 1-2 character
CJK queries with a bounded LIKE fallback; a 1-2 character ASCII query has no
such fallback, and a substring scan for one returns hundreds of unrelated rows
(measured: ``1`` -> 603, ``ab`` -> 285).  Such a query is reported as
``below_trigram_minimum`` rather than answered with a noise set.

Contract
--------
* Read-only. Never writes Formal State. ``NEW_TABLES = 0``.
* Deterministic. ``MODEL_CALLS = 0``. No LLM, no embeddings.
* Authority / lineage / evidence states are disclosed, never flattened.
* Presentation metadata is hydrated from ``knowledge_page`` only to preserve
  the published MCP DTO fields; it never overrides retrieval authority.
* Two legs, and only two: ``knowledge_search`` (canonical, indexed,
  relevance-ranked) and ``search_provisional`` (non-current pointers, scope
  enforced in SQL so it can never return canonical knowledge).
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

# The Retrieval Contract lives in the release runtime.  ``PAW_RUNTIME_MODULE_ROOT``
# is the explicit PAW Runtime Path Contract value; the release symlink is the
# documented default.  ``append`` (not ``insert``) so the divergent
# brain_runtime fork is never shadowed.
_RUNTIME_ROOT = os.environ.get("PAW_RUNTIME_MODULE_ROOT") or str(Path(__file__).resolve().parents[1 if Path(__file__).parent.name == "backend" else 2] / "runtime")
if _RUNTIME_ROOT not in sys.path:
    sys.path.append(_RUNTIME_ROOT)

import paw_retrieval_core as _rc  # noqa: E402

from .db import DB_PATH  # noqa: E402

SCHEMA = "paw.retrieval-contract.v13/1"
# The provisional-material pointer leg is part of the same contract, but it is
# a DIFFERENT kind of recall (see ``search_provisional``).
PROVISIONAL_SCHEMA = "paw.retrieval-contract.v13/1#provisional"

# FTS5 operator / punctuation characters.  Kept as documentation of the class
# of query that breaks the raw FTS expression; the retry itself is driven by
# "the raw query returned nothing" rather than by this pattern, because the
# retry is monotone and a pattern can always miss a character.
_FTS_OPERATOR_RE = re.compile(r'[()*":^~{}\[\]\\\-/]')

# A trigram index cannot serve a query shorter than 3 characters.  The
# retrieval core covers the 1-2 character CJK case with a bounded LIKE
# fallback; a 1-2 character ASCII query has no such fallback, and a substring
# scan for one would return hundreds of unrelated rows (measured on production:
# "1" -> 603 rows, "ab" -> 285).  Such a query is reported as
# ``below_trigram_minimum`` rather than answered with a noise set.
_TRIGRAM_MIN = 3

# Display metadata hydrated onto contract hits so the published MCP DTO shape
# (id, title, summary, knowledge_type, status, updated_at) is preserved.
_META_COLUMNS = ("id", "title", "one_line_summary", "knowledge_type", "status", "updated_at")


def _contract_query(text: str) -> str:
    """Deterministic phrase form of a query (FTS5 quoted string)."""
    return '"' + text.replace('"', '""') + '"'


def _connect(db_path: str | os.PathLike[str]) -> sqlite3.Connection:
    db = sqlite3.connect(str(db_path), timeout=30)
    db.row_factory = sqlite3.Row
    return db


def _hydrate_meta(db: sqlite3.Connection, ids: list[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    rows = db.execute(
        f"SELECT {', '.join(_META_COLUMNS)} FROM knowledge_page WHERE id IN ({marks})",
        ids,
    ).fetchall()
    return {str(r["id"]): dict(r) for r in rows}


def knowledge_search(
    query: str,
    *,
    limit: int = 20,
    include_provisional: bool = False,
    db_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """The single user-facing knowledge search.

    Returns a dict with the published contract keys:
    ``items`` (each carrying the retrieval authority plus hydrated display
    metadata), ``count``, ``matched_total``, ``admitted_total``,
    ``excluded_provisional_count``, ``authority_scope``,
    ``governance_columns_present`` and ``model_call``.
    """
    path = str(db_path or DB_PATH)
    text = (query or "").strip()
    if not text:
        return {
            "items": [], "count": 0, "matched_total": 0, "admitted_total": 0,
            "excluded_provisional_count": 0,
            "authority_scope": "current_confirmed_only" if not include_provisional
                               else "includes_provisional",
            "governance_columns_present": True, "query": text,
            "retrieval_query_mode": "empty", "model_call": False,
        }

    # Fail closed exactly like the surface this replaces: without the Knowledge
    # Confirmation Gate there is no provable canonical state.
    governed = True
    try:
        with _connect(path) as db:
            cols = {str(r[1]) for r in db.execute("PRAGMA table_info(knowledge_page)").fetchall()}
        governed = "confirmed_revision_no" in cols
    except sqlite3.Error:
        governed = False
    if not governed and not include_provisional:
        return {
            "items": [], "count": 0, "matched_total": 0, "admitted_total": 0,
            "excluded_provisional_count": 0,
            "authority_scope": "current_confirmed_only",
            "governance_columns_present": False,
            "fail_closed_reason": "KNOWLEDGE_GOVERNANCE_COLUMNS_ABSENT",
            "query": text, "retrieval_query_mode": "n/a", "model_call": False,
        }

    # --- Retrieval Contract ------------------------------------------------
    # A raw query that contains an FTS5 operator (``聘任会全流程ppt(1)``,
    # ``个性化-初试答案``) makes the MATCH expression invalid; ``_recall``
    # swallows that error and returns nothing, so a perfectly ordinary title
    # became unsearchable.  The retry is a quoted FTS5 phrase, which is
    # MONOTONE: it can only turn an empty result into a non-empty one, never
    # narrow a result set that already matched.  So the retry is driven purely
    # by "the raw query matched nothing" — no character class can miss a case.
    mode = "raw"
    result = _rc.search_current(
        text, limit=limit, include_provisional=include_provisional, db_path=path)
    if not result.get("count"):
        retry = _rc.search_current(
            _contract_query(text), limit=limit,
            include_provisional=include_provisional, db_path=path)
        if retry.get("count"):
            result, mode = retry, "quoted_phrase_retry"
        elif len(text) < _TRIGRAM_MIN:
            # Cannot be served by a trigram index, and a substring scan would
            # return noise.  Say so instead of implying "no such knowledge".
            mode = "below_trigram_minimum"
        elif _FTS_OPERATOR_RE.search(text):
            # The query contains an FTS5 operator and the quoted phrase form
            # also matched nothing.  Distinguished from a plain miss so a
            # caller can tell a parser artefact from a genuine no-match.
            mode = "no_match_fts_operator_query"
        else:
            mode = "no_match"

    raw_items = result.get("items") or []
    ids = [str(i.get("entity_id")) for i in raw_items if i.get("entity_id")]

    try:
        with _connect(path) as db:
            meta = _hydrate_meta(db, ids)
    except sqlite3.Error:
        meta = {}

    items: list[dict[str, Any]] = []
    for hit in raw_items:
        kid = str(hit.get("entity_id") or "")
        m = meta.get(kid, {})
        items.append({
            # Published MCP DTO fields (preserved shape).
            "id": kid,
            "title": m.get("title") or hit.get("title") or "",
            "summary": m.get("one_line_summary") or "",
            "one_line_summary": m.get("one_line_summary") or "",
            "knowledge_type": m.get("knowledge_type"),
            "status": m.get("status"),
            "updated_at": m.get("updated_at"),
            # Retrieval Contract disclosure (never flattened).
            "authority_state": hit.get("authority_state"),
            "canonical": bool(hit.get("canonical")),
            "provisional_reason": hit.get("provisional_reason"),
            "source_lineage_state": hit.get("source_lineage_state"),
            "evidence_state": hit.get("evidence_state"),
            "score": hit.get("score"),
            "snippet": hit.get("snippet"),
        })

    canonical_count = sum(1 for i in items if i["canonical"])
    matched_total = len(items) + int(result.get("stale_candidate_count") or 0) \
        + int(result.get("untraceable_candidate_count") or 0) \
        + int(result.get("incomplete_candidate_count") or 0)
    return {
        "items": items,
        "count": len(items),
        "matched_total": matched_total,
        "admitted_total": canonical_count,
        "excluded_provisional_count": max(0, matched_total - canonical_count),
        "authority_scope": "current_confirmed_only" if not include_provisional
                           else "includes_provisional",
        "governance_columns_present": governed,
        "incomplete_candidate_count": result.get("incomplete_candidate_count", 0),
        "untraceable_candidate_count": result.get("untraceable_candidate_count", 0),
        "stale_candidate_count": result.get("stale_candidate_count", 0),
        "query": text,
        "retrieval_contract": SCHEMA,
        "retrieval_query_mode": mode,
        "model_call": False,
    }


def search_provisional(
    query: str,
    *,
    limit: int = 20,
    db_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Provisional-material POINTER search.  Never a canonical knowledge search.

    Why this is a separate leg and not part of the canonical index
    -------------------------------------------------------------
    ``paw_retrieval_core.bootstrap_index`` deliberately excludes
    ``status='draft'`` from the retrieval index, and ``refresh_knowledge``
    actively *evicts* a page from the index when its status becomes draft.
    That is a governance decision, not an oversight: ungoverned material must
    never be able to enter canonical recall.  So a draft can never be served
    by ``search_current``, and it must not be.

    Callers that legitimately need to point at provisional material therefore
    need a recall path that is *structurally incapable* of returning canonical
    knowledge.  This is that path.

    Guarantees
    ----------
    * Scope is enforced in SQL: ``p.status <> 'current'``.  A canonical
      (``status='current'``) row can never be returned, whatever the query.
    * Read-only.  ``NEW_TABLES = 0``.  Deterministic.  ``MODEL_CALLS = 0``.
    * The result is a pointer set, ordered by recency — NOT relevance-ranked
      retrieval, and every item says so (``retrieval_path``,
      ``relevance_ranked=False``).
    * ``authority_state`` is disclosed per hit using the same canonical
      ``knowledge_authority_state`` policy, so provisional material can never
      be mistaken for canonical.
    """
    path = str(db_path or DB_PATH)
    text = (query or "").strip()
    empty = {
        "items": [], "count": 0, "matched_total": 0, "query": text,
        "retrieval_contract": PROVISIONAL_SCHEMA,
        "retrieval_path": "provisional_pointer",
        "relevance_ranked": False,
        "authority_scope": "non_current_only",
        "model_call": False,
    }
    if not text:
        return empty

    try:
        from .current_context_policy import knowledge_authority_state
    except Exception:  # pragma: no cover - policy module must exist
        return empty

    like = f"%{text}%"
    latest = (
        "LEFT JOIN (SELECT knowledge_id, MAX(revision_no) AS latest_revision_no "
        "           FROM knowledge_revision GROUP BY knowledge_id) lrm "
        "  ON lrm.knowledge_id = p.id "
        "LEFT JOIN knowledge_revision lr "
        "  ON lr.knowledge_id = p.id AND lr.revision_no = lrm.latest_revision_no"
    )
    # Scope is part of the WHERE clause, not a post-filter, so no canonical row
    # is ever materialised.
    scope = "p.status <> 'current'"
    match = ("(p.title LIKE ? OR p.one_line_summary LIKE ? "
             " OR COALESCE(lr.content_json, '') LIKE ?)")

    try:
        with _connect(path) as db:
            total = db.execute(
                f"SELECT COUNT(*) AS n FROM knowledge_page p {latest} "
                f"WHERE {scope} AND {match}", (like, like, like)).fetchone()["n"]
            rows = db.execute(
                "SELECT p.id, p.title, p.knowledge_type, p.status, "
                "       p.one_line_summary, p.updated_at, p.confirmed_revision_no, "
                "       COALESCE(lrm.latest_revision_no, 0) AS latest_revision_no "
                f"FROM knowledge_page p {latest} "
                f"WHERE {scope} AND {match} "
                "ORDER BY p.updated_at DESC, p.id LIMIT ?",
                (like, like, like, int(limit))).fetchall()
    except sqlite3.Error:
        return empty

    items: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        authority = knowledge_authority_state(record)
        # Defence in depth: the policy decides again, and a row the policy
        # calls canonical is dropped rather than leaked through this leg.
        if authority == "CURRENT_CONFIRMED":
            continue
        items.append({
            "id": str(record.get("id") or ""),
            "title": record.get("title") or "",
            "summary": record.get("one_line_summary") or "",
            "one_line_summary": record.get("one_line_summary") or "",
            "knowledge_type": record.get("knowledge_type"),
            "status": record.get("status"),
            "updated_at": record.get("updated_at"),
            "authority_state": authority,
            "canonical": False,
            "provisional_reason": "NOT_IN_CANONICAL_INDEX",
            "source_lineage_state": None,
            "evidence_state": None,
            "score": None,
            "snippet": None,
            "retrieval_path": "provisional_pointer",
            "relevance_ranked": False,
        })

    return {
        "items": items,
        "count": len(items),
        "matched_total": total,
        "query": text,
        "retrieval_contract": PROVISIONAL_SCHEMA,
        "retrieval_path": "provisional_pointer",
        "relevance_ranked": False,
        "authority_scope": "non_current_only",
        "model_call": False,
    }


__all__ = ["SCHEMA", "PROVISIONAL_SCHEMA", "knowledge_search", "search_provisional"]
