"""Effective Review Projection V2 (spec §11).

"待我审核" (needs-my-review) returns ONLY ``USER_REVIEW_REQUIRED`` items.
``AUTO_FLOW_SAFE`` and ``AUTO_CLOSE_NO_VALUE`` are removed from the user's main
review list but remain fully auditable and are exposed as counts.

Compatibility rules honoured:
  * a candidate with a closed ``candidate_resolution`` is never effective-pending
  * raw ``candidate.status='pending'`` is NEVER treated as effective review state
  * the existing Effective Review Projection filter chain is reused when the
    production service is importable (closed resolution -> presentation validity
    -> SYSTEM_REMEDIATION), and reproduced locally otherwise

READ-ONLY: no writes, no candidate mutation, no ReviewDecision creation.
"""

from __future__ import annotations

from pathlib import Path
import os


import sqlite3
from typing import Any, Callable, Optional

from . import review_routing_policy as P
from . import review_router_v2 as R

PRODUCTION_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))


def _closed_ids(conn: sqlite3.Connection) -> set[str]:
    try:
        rows = conn.execute(
            "SELECT candidate_id FROM candidate_resolution "
            "WHERE resolution_status IN ('resolved','ignored','reanalysis_failed')"
        ).fetchall()
        return {r[0] for r in rows}
    except sqlite3.Error:
        return set()


def _production_presenter() -> Optional[Callable[..., dict]]:
    """Prefer the shipped presentation service so routing stays compatible."""
    try:
        from brain.review.review_presentation_service import present_candidate
        return present_candidate
    except Exception:  # noqa: BLE001 - fall back to the local equivalent
        return None


def _local_presentation(item: dict[str, Any]) -> dict[str, Any]:
    """Minimal equivalent of the shipped presentation validity check."""
    has_display = bool(item.get("review_display_json"))
    has_brief = bool(item.get("review_brief_json"))
    status = "OK" if (has_display or has_brief) else "PRESENTATION_INVALID"
    return {"presentation_status": status, "routing": "USER_REVIEW"}


def _effective_rows(conn: sqlite3.Connection, limit: int, db_path: str = PRODUCTION_DB) -> tuple[list[dict], dict, dict]:
    """Reproduce the Effective Review Projection, then hand rows to the router.

    Returns ``(effective_rows, stats, presentations)`` where ``presentations``
    maps candidate id -> shipped presentation output (the authoritative signal
    for whether a human can read the item).
    """
    closed = _closed_ids(conn)
    rows = conn.execute(
        "SELECT * FROM candidate WHERE status = 'pending' ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()

    presenter = _production_presenter()
    presenter_source = "production" if presenter else "local_equivalent"

    effective: list[dict] = []
    presentations: dict[str, dict] = {}
    stats = {"raw_pending_scanned": len(rows), "closed_resolution_filtered": 0,
             "presentation_invalid_filtered": 0, "system_remediation_filtered": 0,
             "presenter_source": presenter_source}
    for row in rows:
        item = dict(row)
        if item["id"] in closed:
            stats["closed_resolution_filtered"] += 1
            continue
        presentation = (presenter(item, db_path=db_path) if presenter
                        else _local_presentation(item))
        if presentation.get("presentation_status") == "PRESENTATION_INVALID":
            stats["presentation_invalid_filtered"] += 1
            continue
        if presentation.get("routing") == "SYSTEM_REMEDIATION":
            stats["system_remediation_filtered"] += 1
            continue
        effective.append(item)
        presentations[item["id"]] = presentation
    return effective, stats, presentations


def project_v2(limit: int = 1000, db_path: str = PRODUCTION_DB,
               conn: Optional[sqlite3.Connection] = None) -> dict[str, Any]:
    """Return the V2 projection: user list + counts + full audit trail."""
    own = conn is None
    conn = conn or R.connect_readonly(db_path)
    try:
        effective, stats, presentations = _effective_rows(conn, limit, db_path=db_path)
        decisions = R.route_items(effective, conn, presentations=presentations)

        by_route: dict[str, list[dict]] = {r: [] for r in P.ROUTES}
        for d in decisions:
            by_route.setdefault(d["route"], []).append(d)

        user_items = by_route[P.USER_REVIEW_REQUIRED]
        auto_flow = by_route[P.AUTO_FLOW_SAFE]
        auto_closed = by_route[P.AUTO_CLOSE_NO_VALUE]

        return {
            "artifact": "Effective_Review_Projection_V2",
            "version": "V2",
            "policy": "REVIEW_ROUTER_V2",
            "effective_pending_baseline": len(decisions),
            "counts": {
                "auto_flow_safe_count": len(auto_flow),
                "auto_closed_count": len(auto_closed),
                "user_review_required_count": len(user_items),
            },
            "user_review_items": user_items,
            "auto_flow_safe": [{"candidate_id": d["candidate_id"],
                                "candidate_type": d.get("candidate_type"),
                                "reason_codes": d["reason_codes"],
                                "target_type": d["target_type"],
                                "recommended_review_decision":
                                    d["recommended_review_decision"]}
                               for d in auto_flow],
            "auto_close_no_value": [{"candidate_id": d["candidate_id"],
                                     "candidate_type": d.get("candidate_type"),
                                     "reason_codes": d["reason_codes"],
                                     "system_remediation": d["system_remediation"],
                                     "recommended_review_decision":
                                         d["recommended_review_decision"]}
                                    for d in auto_closed],
            "system_remediation_queue": [d["candidate_id"] for d in auto_closed
                                         if d["system_remediation"]],
            "target_mismatches": [{"candidate_id": d["candidate_id"],
                                   "declared_target": d["declared_target"],
                                   "target_type": d["target_type"],
                                   "why": d["target_why"]}
                                  for d in decisions if d["target_mismatch"]],
            "filter_stats": stats,
            "compatibility": {
                "uses_closed_resolution": True,
                "raw_pending_is_not_effective": True,
                "governance_chain_preserved": True,
                "no_writes": True,
            },
        }
    finally:
        if own:
            conn.close()


def get_user_review_items(limit: int = 1000, db_path: str = PRODUCTION_DB) -> list[dict]:
    """The ONLY list the frontend '待我审核' should render."""
    return project_v2(limit=limit, db_path=db_path)["user_review_items"]


def get_summary_v2(db_path: str = PRODUCTION_DB) -> dict[str, Any]:
    """Counts for the frontend badge — consistent with the item list by construction."""
    proj = project_v2(db_path=db_path)
    return {"effective": {"needs_review": proj["counts"]["user_review_required_count"],
                          "auto_flow_safe": proj["counts"]["auto_flow_safe_count"],
                          "auto_closed": proj["counts"]["auto_closed_count"]},
            "baseline_effective_pending": proj["effective_pending_baseline"]}
