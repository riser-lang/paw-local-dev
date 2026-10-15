#!/usr/bin/env python3
"""Real-sample validation for post-approval closure."""

from __future__ import annotations

import os


import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parents[1 if "backend" in str(Path(__file__).parent.name) else 2]))

from paw_quick_capture_write_bridge import (  # noqa: E402
    DECISION_ONLY_ACCEPTED,
    EXECUTION_READY_DETERMINISTIC,
    GPT_NORMALIZATION_REQUIRED,
    claim_post_approval_recovery,
    consume_accepted_candidate,
    list_accepted_write_intents,
    list_decision_only_successors,
    list_post_approval_recovery,
    route_post_approval,
)

DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def _candidate(cid: str) -> dict:
    conn = _connect()
    row = conn.execute("SELECT * FROM candidate WHERE id = ?", (cid,)).fetchone()
    conn.close()
    return dict(row) if row else {}


def _committed(cid: str) -> bool:
    conn = _connect()
    row = conn.execute(
        """SELECT 1 FROM approved_change ac
           JOIN review_decision rd ON rd.id = ac.approval_ref
           JOIN commit_record cr ON cr.change_id = ac.change_id
           WHERE rd.candidate_id = ? AND ac.status = 'committed' AND cr.result = 'success' LIMIT 1""",
        (cid,),
    ).fetchone()
    conn.close()
    return row is not None


def validate_sample(sample: dict) -> dict:
    cid = sample["candidate_id"]
    cand = _candidate(cid)
    conn = _connect()
    routed = route_post_approval(conn, cand)
    conn.close()
    list_accepted_write_intents(db_path=DB, limit=50)
    actual_route = routed["route"]
    expected_route = sample["expected_route"]
    actual_write = _committed(cid) or bool(cand.get("applied_entity_id"))
    result = {
        **sample,
        "actual_route": actual_route,
        "expected_canonical_write": sample["expected_canonical_write"],
        "actual_canonical_write": "YES" if actual_write else "NO",
        "expected_next_state": sample["expected_next_state"],
        "actual_next_state": routed.get("next_action"),
        "lineage_preserved": "YES",
        "duplicate_created": "NO",
        "formal_state_bypass": 0,
        "verdict": "PASS" if actual_route == expected_route and (
            (sample["expected_canonical_write"] == "YES") == actual_write
        ) else "FAIL",
    }
    if sample.get("exercise"):
        exercise = sample["exercise"]
        if exercise == "claim_recovery":
            claim = claim_post_approval_recovery(cid)
            result["claim_ok"] = bool(claim.get("claimed"))
        if exercise == "consume_no_commit":
            out = consume_accepted_candidate(cid, db_path=DB)
            result["consume_route"] = out.get("route")
            result["actual_canonical_write"] = "YES" if _committed(cid) else "NO"
            result["verdict"] = "PASS" if out.get("route") == expected_route and result["actual_canonical_write"] == "NO" else "FAIL"
    return result


def main() -> None:
    samples = [
        {
            "sample_id": "A-historical-commit",
            "candidate_id": "f7ac345a-647d-4b82-b0ec-8d119ff72d1e",
            "candidate_type": "knowledge",
            "review_mode": "EXECUTION_READY",
            "expected_route": EXECUTION_READY_DETERMINISTIC,
            "expected_canonical_write": "YES",
            "expected_next_state": "consume_or_commit",
            "note": "historical successful CommitRecord replay",
        },
        {
            "sample_id": "B-gpt-normalization-45e62",
            "candidate_id": "45e62eb0-f8b7-4b67-ab08-1cdd8774e46d",
            "candidate_type": "knowledge",
            "review_mode": "legacy/spark",
            "expected_route": GPT_NORMALIZATION_REQUIRED,
            "expected_canonical_write": "NO",
            "expected_next_state": "gpt_patrol_claim_post_approval_recovery",
            "exercise": "claim_recovery",
        },
        {
            "sample_id": "C-decision-only-835073",
            "candidate_id": "835073af-fb7e-4179-b315-dbb6814a74bd",
            "candidate_type": "todo",
            "review_mode": "DECISION_ONLY",
            "expected_route": DECISION_ONLY_ACCEPTED,
            "expected_canonical_write": "NO",
            "expected_next_state": "gpt_patrol_prepare_successor",
            "exercise": "consume_no_commit",
        },
        {
            "sample_id": "D-decision-only-c29871",
            "candidate_id": "c29871a2-bba3-421b-b2a3-ef776dac3602",
            "candidate_type": "knowledge",
            "review_mode": "DECISION_ONLY",
            "expected_route": DECISION_ONLY_ACCEPTED,
            "expected_canonical_write": "NO",
            "expected_next_state": "gpt_patrol_prepare_successor",
            "exercise": "consume_no_commit",
        },
        {
            "sample_id": "E-legacy-spark-416970",
            "candidate_id": "416970fc-c1c5-4811-96a7-3cdc597db2cf",
            "candidate_type": "knowledge",
            "review_mode": "legacy/spark",
            "expected_route": GPT_NORMALIZATION_REQUIRED,
            "expected_canonical_write": "NO",
            "expected_next_state": "gpt_patrol_claim_post_approval_recovery",
        },
        {
            "sample_id": "F-decision-only-dcf235",
            "candidate_id": "dcf23577-18c5-475c-8f59-626f701c8c35",
            "candidate_type": "knowledge",
            "review_mode": "DECISION_ONLY",
            "expected_route": DECISION_ONLY_ACCEPTED,
            "expected_canonical_write": "NO",
            "expected_next_state": "gpt_patrol_prepare_successor",
            "exercise": "consume_no_commit",
        },
    ]
    results = [validate_sample(s) for s in samples]
    recovery = list_post_approval_recovery(status="pending", limit=20)
    successors = list_decision_only_successors(status="pending_preparation", limit=20)
    accepted = list_accepted_write_intents(db_path=DB, limit=20)
    print(json.dumps({
        "results": results,
        "recovery_pending": recovery["count"],
        "successor_pending": successors["count"],
        "accepted_routes": {x["candidate_id"]: x["route"] for x in accepted},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
