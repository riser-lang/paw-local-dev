#!/usr/bin/env python3
"""PAW v1.3B Patch 1: retrieval post-commit refresh hook.

Wires bounded retrieval refresh into the governed commit lifecycle at the
narrowest safe point: AFTER the Formal State commit has succeeded.

Contract:
- Formal State commit is canonical; retrieval is derived.
- A refresh failure must NEVER roll back a valid Formal commit.
- A refresh failure must be detectable (stale watermark) and must never
  silently pretend success.
- One knowledge change -> bounded refresh (normally 1 document).
- No new queue, no new daemon, no new scheduler: in-process only,
  reusing the existing governed committer integration entrypoint.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import paw_retrieval_core as rc

SCHEMA = "paw.retrieval.refresh.v13b-p1/1"


def _knowledge_ids_from_result(
    db_path: str, target_type: str, target_id: str | None,
) -> list[str]:
    """Resolve the knowledge ids affected by one governed commit."""
    if target_type == "knowledge" and target_id:
        return [target_id]
    if target_type == "knowledge_project_link" and target_id:
        # link/unlink affects the knowledge document's project metadata
        try:
            db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            rows = db.execute(
                "SELECT knowledge_id FROM knowledge_project_link"
                " WHERE knowledge_id = ? OR project_id = ?",
                (target_id, target_id)).fetchall()
            db.close()
            return [r[0] for r in rows]
        except sqlite3.Error:
            return []
    return []


def refresh_after_commit(
    result: dict[str, Any],
    *,
    db_path: str,
) -> dict[str, Any]:
    """Post-commit bounded retrieval refresh.

    Called ONLY after commit_approved_change returned success.
    result: the committer return dict (commit_id/result/target_id plus
    the approved_change row is re-read for target_type).
    """
    out = {
        "schema": SCHEMA,
        "hook_invoked": True,
        "refresh_attempted": False,
        "refresh_ok": None,
        "documents_touched": 0,
        "full_reindex_triggered": False,
        "stale_detected": False,
        "error": None,
        "model_call": False,
    }
    if result.get("result") != "success":
        out["hook_invoked"] = False
        return out
    commit_id = result.get("commit_id")
    target_id = result.get("target_id")
    if not commit_id:
        return out
    # Resolve target_type from the committed change (authoritative);
    # fall back to explicit target info in the result dict.
    target_type = result.get("target_type")
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT target_type, target_id FROM audit_event"
            " WHERE commit_id = ? AND event_type = 'commit'",
            (commit_id,)).fetchone()
        db.close()
        if row is not None:
            target_type = row["target_type"]
            if row["target_id"]:
                target_id = row["target_id"]
    except sqlite3.Error as e:
        out["error"] = f"hook_target_lookup_failed: {e}"
        out["stale_detected"] = True
        return out
    kids = _knowledge_ids_from_result(db_path, target_type or "", target_id)
    if not kids:
        # Non-knowledge commit (todo/project/data/metric): no retrieval
        # document refresh required. Metrics are not FTS-indexed.
        return out
    out["refresh_attempted"] = True
    try:
        stats_total = 0
        for kid in kids:
            stats = rc.refresh_knowledge(db_path, kid)
            stats_total += stats.get("documents_touched", 0)
        out["refresh_ok"] = True
        out["documents_touched"] = stats_total
    except Exception as e:  # noqa: BLE001
        # Formal commit stays committed. Stale state is detectable via
        # watermark mismatch at query time (RETRIEVAL_STALE).
        out["refresh_ok"] = False
        out["stale_detected"] = True
        out["error"] = f"refresh_failed: {e}"
    return out


__all__ = ["refresh_after_commit", "SCHEMA"]
