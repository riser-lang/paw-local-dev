"""Effective Review Projection — single source of truth for review state.

Deterministic, NON-LLM projection derived from:
  Candidate + Candidate Resolution + ReviewDecision + ApprovedChange

All consumers (Web, Home, R13E, Spark) MUST use this service.
No consumer may query raw candidate.status and infer review state independently.

Effective states:
  NEEDS_REVIEW              — Candidate still needs user action, no closed resolution
  RESOLVED                  — Candidate has closed resolution (resolved/ignored)
  ACCEPTED_WAITING_COMMIT   — User approved, but ApprovedChange not yet committed
  REJECTED                  — User rejected
  APPLIED_OR_COMMITTED      — ApprovedChange committed / Candidate applied
  REVIEWED_WAITING_GPT      — User response is durable; GPT patrol owns follow-up
"""

from __future__ import annotations

from pathlib import Path
import os


import sqlite3
from typing import Any, Optional
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PRODUCTION_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))
BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")

# Effective Review State constants
NEEDS_REVIEW = "NEEDS_REVIEW"
RESOLVED = "RESOLVED"
ACCEPTED_WAITING_COMMIT = "ACCEPTED_WAITING_COMMIT"
REVIEWED_WAITING_GPT = "REVIEWED_WAITING_GPT"
REJECTED = "REJECTED"
APPLIED_OR_COMMITTED = "APPLIED_OR_COMMITTED"

# Quick Record / write-intent types stay visible as waiting-write until CommitRecord.
WRITE_INTENT_TYPES = frozenset({"todo", "knowledge", "project_update"})


def _connect(db_path: str = PRODUCTION_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _closed_resolution_ids(conn: sqlite3.Connection) -> set[str]:
    """Ids of candidates whose resolution is closed (resolved | ignored)."""
    try:
        rows = conn.execute(
            "SELECT candidate_id FROM candidate_resolution WHERE resolution_status IN ('resolved','ignored','reanalysis_failed')"
        ).fetchall()
        return {r[0] for r in rows}
    except sqlite3.OperationalError:
        return set()


def _has_approved_change(conn: sqlite3.Connection, candidate_id: str) -> bool:
    """Check if any ApprovedChange exists (regardless of status) for this candidate."""
    try:
        rows = conn.execute(
            """SELECT ac.change_id FROM approved_change ac
               JOIN review_decision rd ON rd.id = ac.approval_ref
               WHERE rd.candidate_id = ?""",
            (candidate_id,)
        ).fetchall()
    except sqlite3.OperationalError:
        return False
    return len(rows) > 0


def _has_committed_change(conn: sqlite3.Connection, candidate_id: str) -> bool:
    """Check if an ApprovedChange exists and is committed for this candidate."""
    try:
        rows = conn.execute(
            """SELECT ac.status FROM approved_change ac
               JOIN review_decision rd ON rd.id = ac.approval_ref
               WHERE rd.candidate_id = ?""",
            (candidate_id,)
        ).fetchall()
    except sqlite3.OperationalError:
        return False
    if not rows:
        return False
    return any(r["status"] == "committed" for r in rows)


def _committed_candidate_ids(conn: sqlite3.Connection) -> set[str]:
    """Set-level prefetch of every candidate that has a committed ApprovedChange.

    Semantically equivalent to calling _has_committed_change per candidate, but
    resolves in a single query.  Used by the summary path so the accepted-candidate
    loop no longer issues O(N) per-row SQL (production: ~6k accepted rows).
    """
    try:
        rows = conn.execute(
            """SELECT DISTINCT rd.candidate_id FROM approved_change ac
               JOIN review_decision rd ON rd.id = ac.approval_ref
               WHERE ac.status = 'committed'"""
        ).fetchall()
        return {r[0] for r in rows}
    except sqlite3.OperationalError:
        return set()


def _approved_candidate_ids(conn: sqlite3.Connection) -> set[str]:
    """Set-level prefetch of every candidate with any ApprovedChange."""
    try:
        rows = conn.execute(
            """SELECT DISTINCT rd.candidate_id FROM approved_change ac
               JOIN review_decision rd ON rd.id = ac.approval_ref"""
        ).fetchall()
        return {r[0] for r in rows}
    except sqlite3.OperationalError:
        return set()


def _is_decision_only_row(row: sqlite3.Row | dict) -> bool:
    """Row-based variant of _is_decision_only — no per-row SQL.

    The summary loop already holds the full candidate row; re-fetching it by id
    inside _is_decision_only doubled the query count for no benefit.
    """
    row = dict(row)
    if row.get("review_brief_json"):
        import json
        try:
            brief = json.loads(row["review_brief_json"])
            if "review_mode" in brief:
                return brief.get("review_mode") == "DECISION_ONLY"
        except (json.JSONDecodeError, TypeError):
            pass
    from brain.review.review_presentation_service import _determine_review_mode, _safe_json_parse
    proposed = _safe_json_parse(row.get("proposed_change"))
    return _determine_review_mode(row, proposed) == "DECISION_ONLY"


def _is_decision_only(conn: sqlite3.Connection, candidate_id: str) -> bool:
    """Check if a candidate's review_brief has review_mode = DECISION_ONLY.

    Legacy candidates without review_brief_json fallback to deterministic
    mode inference based on candidate_type and proposed_change.
    """
    try:
        row = conn.execute(
            "SELECT candidate_type, proposed_change, review_brief_json FROM candidate WHERE id = ?",
            (candidate_id,)
        ).fetchone()
        if not row:
            return False
        if row["review_brief_json"]:
            import json
            try:
                brief = json.loads(row["review_brief_json"])
                if "review_mode" in brief:
                    return brief.get("review_mode") == "DECISION_ONLY"
            except (json.JSONDecodeError, TypeError):
                pass
        # Fallback for legacy candidates without review_brief_json
        from brain.review.review_presentation_service import _determine_review_mode, _safe_json_parse
        proposed = _safe_json_parse(row["proposed_change"])
        return _determine_review_mode(dict(row), proposed) == "DECISION_ONLY"
    except sqlite3.OperationalError:
        return False


def _is_web_reviewed_write_intent(row: sqlite3.Row | dict) -> bool:
    """Web review is authorization input for the next GPT patrol, not a commit."""
    row = dict(row)
    if row.get("candidate_type") not in WRITE_INTENT_TYPES:
        return False
    proposed = row.get("proposed_change")
    if isinstance(proposed, str):
        import json
        try:
            proposed = json.loads(proposed)
        except (json.JSONDecodeError, TypeError):
            proposed = {}
    if not isinstance(proposed, dict):
        return False
    metadata = proposed.get("review_metadata")
    return isinstance(metadata, dict) and metadata.get("review_origin") == "web"


def _human_response_resolution(conn: sqlite3.Connection, candidate_id: str) -> sqlite3.Row | None:
    """Return a closed resolution containing a user response/choice, if any."""
    try:
        return conn.execute(
            """SELECT * FROM candidate_resolution
               WHERE candidate_id = ?
                 AND resolution_status IN ('resolved','reanalysis_failed')
                 AND (NULLIF(TRIM(COALESCE(user_response, '')), '') IS NOT NULL
                      OR selected_option IS NOT NULL)
               ORDER BY created_at DESC LIMIT 1""",
            (candidate_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None


def get_effective_review_state(candidate_id: str, db_path: str | None = None) -> Optional[str]:
    """Get the effective review state for a single candidate."""
    conn = _connect(db_path or PRODUCTION_DB)
    try:
        row = conn.execute(
            "SELECT * FROM candidate WHERE id = ?",
            (candidate_id,)
        ).fetchone()
        if row is None:
            return None

        raw_status = row["status"]
        keys = set(row.keys())
        candidate_type = row["candidate_type"] if "candidate_type" in keys else ""
        applied_entity_id = row["applied_entity_id"] if "applied_entity_id" in keys else None
        closed_ids = _closed_resolution_ids(conn)

        if raw_status == "pending":
            if candidate_id in closed_ids:
                return REVIEWED_WAITING_GPT if _human_response_resolution(conn, candidate_id) else RESOLVED
            return NEEDS_REVIEW

        if raw_status == "accepted":
            if _has_committed_change(conn, candidate_id) or applied_entity_id:
                return APPLIED_OR_COMMITTED
            # Write-intent accepts remain visible until the governed committer
            # produces a CommitRecord. Direction-only types have no write phase.
            if candidate_type in WRITE_INTENT_TYPES:
                if _is_web_reviewed_write_intent(row):
                    return REVIEWED_WAITING_GPT
                return ACCEPTED_WAITING_COMMIT
            if _is_decision_only(conn, candidate_id) and not _has_approved_change(conn, candidate_id):
                return RESOLVED
            return ACCEPTED_WAITING_COMMIT

        if raw_status == "rejected":
            return REJECTED

        if raw_status == "applied":
            return APPLIED_OR_COMMITTED

        return NEEDS_REVIEW
    finally:
        conn.close()


def get_effective_review_items(
    limit: int = 100,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    db_path = db_path or PRODUCTION_DB
    """Get all candidates with effective state = NEEDS_REVIEW.

    This is the ONLY function that should be used to populate
    'pending review' / 'needs your attention' lists.
    """
    conn = _connect(db_path)
    try:
        closed_ids = _closed_resolution_ids(conn)

        rows = conn.execute(
            "SELECT * FROM candidate WHERE status = 'pending' ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()

        result = []
        from brain.review.review_presentation_service import present_candidate
        for row in rows:
            if row["id"] not in closed_ids:
                # Fail closed: an unrenderable candidate must not be exposed
                # as an actionable review item with meaningless UI copy.
                item = dict(row)
                presentation = present_candidate(item, db_path=db_path)
                if presentation.get("presentation_status") != "PRESENTATION_INVALID":
                    # SYSTEM_REMEDIATION items are system-side evidence
                    # deficiencies, not user business decisions.  They stay
                    # out of the user review list via this projection rule
                    # only: the Candidate, its status and its lineage are
                    # never modified here.
                    if presentation.get("routing") == "SYSTEM_REMEDIATION":
                        continue
                    result.append(item)
        return result
    finally:
        conn.close()


def get_effective_review_summary(db_path: str | None = None) -> dict[str, Any]:
    """Get summary of all effective review states.

    Returns counts for each effective state plus raw counts for diagnostics.
    """
    conn = _connect(db_path or PRODUCTION_DB)
    try:
        closed_ids = _closed_resolution_ids(conn)

        raw_total = conn.execute("SELECT COUNT(*) FROM candidate").fetchone()[0]
        raw_pending = conn.execute("SELECT COUNT(*) FROM candidate WHERE status='pending'").fetchone()[0]
        raw_accepted = conn.execute("SELECT COUNT(*) FROM candidate WHERE status='accepted'").fetchone()[0]
        raw_rejected = conn.execute("SELECT COUNT(*) FROM candidate WHERE status='rejected'").fetchone()[0]
        raw_applied = conn.execute("SELECT COUNT(*) FROM candidate WHERE status='applied'").fetchone()[0]

        pending_rows = conn.execute(
            "SELECT id FROM candidate WHERE status = 'pending'"
        ).fetchall()

        effective_needs_review = 0
        effective_resolved = 0
        effective_reviewed_waiting_gpt = 0

        from brain.review.review_presentation_service import present_candidate
        for row in pending_rows:
            if row["id"] in closed_ids:
                if _human_response_resolution(conn, row["id"]):
                    effective_reviewed_waiting_gpt += 1
                else:
                    effective_resolved += 1
            elif present_candidate(dict(row), db_path=db_path).get("presentation_status") != "PRESENTATION_INVALID":
                effective_needs_review += 1

        accepted_rows = conn.execute(
            "SELECT * FROM candidate WHERE status = 'accepted'"
        ).fetchall()
        # Set-level prefetch: one query each instead of per-row SQL in the loop.
        # Production has ~6k accepted candidates; the previous per-row calls made
        # this summary take >60s and timed out the web review list endpoint.
        committed_ids = _committed_candidate_ids(conn)
        approved_ids = _approved_candidate_ids(conn)
        effective_accepted_waiting = 0
        effective_applied = 0
        effective_resolved_from_decision_only = 0
        for row in accepted_rows:
            keys = set(row.keys())
            ctype = row["candidate_type"] if "candidate_type" in keys else ""
            applied_entity_id = row["applied_entity_id"] if "applied_entity_id" in keys else None
            if row["id"] in committed_ids or applied_entity_id:
                effective_applied += 1
            elif ctype in WRITE_INTENT_TYPES:
                if _is_web_reviewed_write_intent(row):
                    effective_reviewed_waiting_gpt += 1
                else:
                    effective_accepted_waiting += 1
            elif _is_decision_only_row(row) and row["id"] not in approved_ids:
                effective_resolved_from_decision_only += 1
            else:
                effective_accepted_waiting += 1
        effective_resolved += effective_resolved_from_decision_only

        effective_rejected = raw_rejected
        effective_applied += raw_applied

        return {
            "effective": {
                "needs_review": effective_needs_review,
                "resolved": effective_resolved,
                "reviewed_waiting_gpt": effective_reviewed_waiting_gpt,
                "accepted_waiting_commit": effective_accepted_waiting,
                "rejected": effective_rejected,
                "applied_or_committed": effective_applied,
            },
            "raw": {
                "total": raw_total,
                "pending": raw_pending,
                "accepted": raw_accepted,
                "rejected": raw_rejected,
                "applied": raw_applied,
            },
        }
    finally:
        conn.close()


def _business_now() -> datetime:
    return datetime.now(BUSINESS_TIMEZONE)


def format_review_timestamp(ts: str | None) -> str | None:
    """Convert UTC ISO timestamp to Beijing time for display."""
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(BUSINESS_TIMEZONE).isoformat()
    except (ValueError, TypeError):
        return ts


# ── V2 canonical compatibility surface ────────────────────────────────────
# The V2 projection is the sole effective review implementation.  The legacy
# service name remains as a stable import for older callers, but delegates to
# the canonical V2 router and returns only USER_REVIEW_REQUIRED rows.
_legacy_get_effective_review_summary = get_effective_review_summary


def get_effective_review_items(
    limit: int = 100,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Return raw candidate rows for the V2 user-review route only.

    Presentation callers need the original candidate columns, while the V2
    projection owns the routing decision.  This adapter deliberately excludes
    AUTO_FLOW_SAFE and AUTO_CLOSE_NO_VALUE rows.
    """
    from .effective_review_projection_v2 import project_v2

    path = db_path or PRODUCTION_DB
    projection = project_v2(limit=max(1, int(limit)), db_path=path)
    ids = [item.get("candidate_id") for item in projection["user_review_items"]]
    if not ids:
        return []
    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        marks = ",".join("?" for _ in ids)
        rows = conn.execute(
            "SELECT * FROM candidate WHERE id IN (%s)" % marks, tuple(ids)
        ).fetchall()
        by_id = {row["id"]: dict(row) for row in rows}
        return [by_id[item_id] for item_id in ids if item_id in by_id]
    finally:
        conn.close()


def get_effective_review_summary(db_path: str | None = None) -> dict[str, Any]:
    """Return legacy effective/raw fields plus V2 route counts."""
    from .effective_review_projection_v2 import project_v2

    path = db_path or PRODUCTION_DB
    projection = project_v2(limit=1000, db_path=path)
    legacy = _legacy_get_effective_review_summary(db_path=path)
    route_counts = projection["counts"]
    legacy["effective"].update({
        "auto_flow_safe": route_counts["auto_flow_safe_count"],
        "auto_closed": route_counts["auto_closed_count"],
        "user_review_required": route_counts["user_review_required_count"],
    })
    legacy["routing"] = {
        "effective_total": projection["effective_pending_baseline"],
        "auto_flow_safe": route_counts["auto_flow_safe_count"],
        "auto_close_no_value": route_counts["auto_closed_count"],
        "user_review_required": route_counts["user_review_required_count"],
    }
    return legacy
