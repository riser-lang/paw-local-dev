#!/usr/bin/env python3
"""Authoritative Capture patrol lifecycle projection.

Single source of truth for whether a Capture belongs in GPT semantic patrol
queues/counters versus human-review presentation.

Patrol-eligible states:
  waiting_patrol  — never semantically consumed by GPT patrol
  waiting_gpt     — user answered; GPT patrol must continue

Non-patrol states:
  waiting_human   — pending Candidate needs user review first
  gpt_resolved    — processed_at set; patrol complete for this Capture
"""

from __future__ import annotations

from pathlib import Path
import os


import json
import sqlite3
from typing import Any, Mapping

PRODUCTION_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))

WAITING_PATROL = "waiting_patrol"
WAITING_HUMAN = "waiting_human"
WAITING_GPT = "waiting_gpt"
GPT_RESOLVED = "gpt_resolved"

PATROL_ELIGIBLE_STATES = frozenset({WAITING_PATROL, WAITING_GPT})

LIFECYCLE_DISPLAY_LABELS = {
    WAITING_PATROL: "待巡航整理",
    WAITING_HUMAN: "待你确认",
    WAITING_GPT: "待巡航整理",
    GPT_RESOLVED: "已整理",
}


def _capture_refs(capture_id: str) -> tuple[str, ...]:
    return (capture_id, f"capture:{capture_id}")


def _source_refs_match(raw: Any, capture_id: str) -> bool:
    blob = raw if isinstance(raw, str) else json.dumps(raw or "", ensure_ascii=False)
    return any(token in blob for token in _capture_refs(capture_id))


def _candidates_for_capture(conn: sqlite3.Connection, capture_id: str) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM candidate").fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        if _source_refs_match(row["source_refs"], capture_id):
            out.append(dict(row))
    return out


def _resolutions_for_candidates(conn: sqlite3.Connection, candidate_ids: list[str]) -> list[dict[str, Any]]:
    if not candidate_ids:
        return []
    try:
        placeholders = ",".join("?" * len(candidate_ids))
        rows = conn.execute(
            f"SELECT * FROM candidate_resolution WHERE candidate_id IN ({placeholders}) ORDER BY created_at",
            tuple(candidate_ids),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(row) for row in rows]


def needs_review_candidate(candidates: list[Mapping[str, Any]], resolutions: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    closed = {
        row["candidate_id"]
        for row in resolutions
        if str(row.get("resolution_status") or "") in {"resolved", "ignored", "reanalysis_failed"}
    }
    for cand in candidates:
        if cand.get("status") == "pending" and cand["id"] not in closed:
            return dict(cand)
    return None


def waiting_gpt_resolution(candidates: list[Mapping[str, Any]], resolutions: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    pending_ids = {c["id"] for c in candidates if c.get("status") == "pending"}
    accepted_web = []
    for cand in candidates:
        proposed = cand.get("proposed_change")
        if isinstance(proposed, str):
            try:
                proposed = json.loads(proposed)
            except (json.JSONDecodeError, TypeError):
                proposed = {}
        metadata = proposed.get("review_metadata") if isinstance(proposed, dict) and isinstance(proposed.get("review_metadata"), dict) else {}
        if cand.get("status") == "accepted" and metadata.get("review_origin") == "web":
            accepted_web.append(cand)
    for row in resolutions:
        if row.get("candidate_id") not in pending_ids and not any(c["id"] == row.get("candidate_id") for c in accepted_web):
            continue
        if str(row.get("resolution_status") or "") not in {"resolved", "reanalysis_failed"}:
            continue
        if (row.get("user_response") or "").strip() or row.get("selected_option"):
            return dict(row)
    for cand in accepted_web:
        proposed = cand.get("proposed_change")
        if isinstance(proposed, str):
            try:
                proposed = json.loads(proposed)
            except (json.JSONDecodeError, TypeError):
                proposed = {}
        metadata = proposed.get("review_metadata") if isinstance(proposed, dict) and isinstance(proposed.get("review_metadata"), dict) else {}
        notes = (metadata.get("review_notes") or "").strip()
        if notes:
            return {
                "candidate_id": cand["id"],
                "user_response": notes,
                "selected_option": None,
                "resolution_status": "accepted_web",
            }
    return None


def patrol_state(
    capture: Mapping[str, Any],
    candidates: list[Mapping[str, Any]],
    resolutions: list[Mapping[str, Any]],
) -> str:
    if capture.get("processed_at"):
        return GPT_RESOLVED
    if needs_review_candidate(candidates, resolutions):
        return WAITING_HUMAN
    if waiting_gpt_resolution(candidates, resolutions):
        return WAITING_GPT
    return WAITING_PATROL


def patrol_state_for_capture(conn: sqlite3.Connection, capture: Mapping[str, Any]) -> str:
    cid = str(capture["id"])
    candidates = _candidates_for_capture(conn, cid)
    resolutions = _resolutions_for_candidates(conn, [c["id"] for c in candidates])
    return patrol_state(capture, candidates, resolutions)


def is_patrol_eligible(capture: Mapping[str, Any], patrol_state_value: str | None = None) -> bool:
    if capture.get("processed_at"):
        return False
    state = patrol_state_value or WAITING_PATROL
    return state in PATROL_ELIGIBLE_STATES


def lifecycle_display_label(patrol_state_value: str) -> str:
    return LIFECYCLE_DISPLAY_LABELS.get(patrol_state_value, "待巡航整理")


def enrich_capture_projection(conn: sqlite3.Connection, capture: Mapping[str, Any]) -> dict[str, Any]:
    state = patrol_state_for_capture(conn, capture)
    out = dict(capture)
    out["patrol_state"] = state
    out["lifecycle_display_label"] = lifecycle_display_label(state)
    out["patrol_eligible"] = is_patrol_eligible(capture, state)
    return out


def _as_capture_dict(row: sqlite3.Row | tuple[Any, ...] | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    if isinstance(row, sqlite3.Row):
        return dict(row)
    columns = ("id", "daily_id", "raw_text", "captured_at", "source", "processed_at", "created_at", "updated_at")
    return {key: row[idx] for idx, key in enumerate(columns) if idx < len(row)}


def list_patrol_eligible_captures(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    order: str = "captured_at ASC",
) -> list[dict[str, Any]]:
    if conn.row_factory is None:
        conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"SELECT * FROM capture WHERE processed_at IS NULL ORDER BY {order}"
    ).fetchall()
    eligible: list[dict[str, Any]] = []
    for row in rows:
        capture = _as_capture_dict(row)
        state = patrol_state_for_capture(conn, capture)
        if is_patrol_eligible(capture, state):
            item = enrich_capture_projection(conn, capture)
            eligible.append(item)
            if limit is not None and len(eligible) >= limit:
                break
    return eligible


def count_patrol_eligible_captures(conn: sqlite3.Connection) -> int:
    if conn.row_factory is None:
        conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM capture WHERE processed_at IS NULL").fetchall()
    count = 0
    for row in rows:
        capture = _as_capture_dict(row)
        state = patrol_state_for_capture(conn, capture)
        if is_patrol_eligible(capture, state):
            count += 1
    return count
