"""PAW Governance Service - Candidate, ReviewDecision, ApprovedChange, Committer Bridge.

Strictly preserves:
- Formal State as the ONLY canonical state.
- formal_state_bypass = 0.
- Frozen payload authorization hash.
- Exact SQLite CHECK constraints on candidate and approved_change.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.core.config import DB_PATH
from backend.governance.committer import (
    PAYLOAD_SCHEMAS,
    commit_approved_change,
    _payload_hash,
)
from backend.runtime_core.event_bus import EventType, event_bus

_schema_migration_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


def _ensure_governance_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS analysis_run (
            id TEXT PRIMARY KEY,
            trigger_type TEXT NOT NULL,
            raw_text TEXT,
            raw_input_refs TEXT,
            analysis_model TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS candidate (
            id TEXT PRIMARY KEY,
            analysis_run_id TEXT NOT NULL,
            candidate_type TEXT NOT NULL CHECK (candidate_type IN (
                'fact', 'todo', 'project_update', 'knowledge', 'conflict', 'ambiguity', 'question', 'project_merge'
            )),
            source_refs TEXT,
            raw_excerpt TEXT,
            proposed_change TEXT,
            confidence TEXT NOT NULL CHECK (confidence IN ('HIGH', 'MEDIUM', 'LOW')),
            inference_level TEXT NOT NULL CHECK (inference_level IN (
                'EXPLICIT', 'STRONG_INFERENCE', 'WEAK_INFERENCE'
            )),
            recommended_action TEXT NOT NULL CHECK (recommended_action IN (
                'AUTO_WRITE', 'DRAFT_ONLY', 'ASK_USER', 'DO_NOT_WRITE'
            )),
            analysis_model TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                'pending', 'accepted', 'rejected', 'applied'
            )),
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            reviewed_by TEXT,
            applied_entity_type TEXT,
            applied_entity_id TEXT,
            dedup_fingerprint TEXT,
            last_seen_at TEXT,
            review_brief_json TEXT,
            review_display_json TEXT,
            review_internal_evidence_json TEXT,
            FOREIGN KEY (analysis_run_id) REFERENCES analysis_run(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_candidate_analysis_run ON candidate(analysis_run_id);
        CREATE INDEX IF NOT EXISTS idx_candidate_status ON candidate(status);
        CREATE INDEX IF NOT EXISTS idx_candidate_type ON candidate(candidate_type);

        CREATE TABLE IF NOT EXISTS review_decision (
            id TEXT PRIMARY KEY,
            candidate_id TEXT,
            decision TEXT NOT NULL CHECK (decision IN ('approve', 'edit_and_approve', 'reject', 'defer')),
            reviewer TEXT NOT NULL,
            review_channel TEXT NOT NULL DEFAULT 'chatgpt_explicit_user',
            approval_source TEXT NOT NULL DEFAULT 'candidate_review'
                CHECK (approval_source IN ('candidate_review', 'direct_user', 'standing_user_rule')),
            original_payload TEXT,
            reviewed_payload TEXT,
            authorization_payload_hash TEXT,
            review_notes TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS candidate_resolution (
            id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            resolution_type TEXT NOT NULL,
            resolution_status TEXT NOT NULL,
            user_response TEXT,
            selected_option TEXT,
            reanalysis_run_id TEXT,
            resolved_by TEXT,
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            reanalysis_error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_candres_candidate ON candidate_resolution(candidate_id);

        CREATE TABLE IF NOT EXISTS candidate_dedup (
            dedup_fingerprint TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_seen_at TEXT,
            FOREIGN KEY (candidate_id) REFERENCES candidate(id)
        );
        CREATE INDEX IF NOT EXISTS idx_dedup_candidate ON candidate_dedup(candidate_id);

        -- One row per file version (content sha256) that an AI pipeline has
        -- already analysed.  Shared by the watcher, cruise and folder scan so
        -- the same file version is never sent to the model twice.
        CREATE TABLE IF NOT EXISTS analyzed_source (
            file_hash TEXT PRIMARY KEY,
            file_name TEXT,
            pipeline TEXT,
            candidates_created INTEGER NOT NULL DEFAULT 0,
            analyzed_at TEXT NOT NULL
        );
        """
    )
    with _schema_migration_lock:
        columns = {r[1] for r in conn.execute('PRAGMA table_info(analyzed_source)')}
        if 'completion_state' not in columns:
            conn.execute("ALTER TABLE analyzed_source ADD COLUMN completion_state TEXT NOT NULL DEFAULT 'complete'")
        from backend.pm.authorization import migrate as migrate_authorization
        migrate_authorization(conn)


_DECIDED_STATUSES = ("accepted", "applied", "rejected")


def _find_active_project_by_name(name: str) -> Optional[str]:
    """Id of the oldest active project whose normalized name equals ``name``'s."""
    from backend.runtime_core.project_convergence import INACTIVE_STATUSES, normalize_project_name

    key = normalize_project_name(name)
    if not key:
        return None
    conn = get_db()
    try:
        for row in conn.execute("SELECT id, name, status FROM project ORDER BY created_at, id"):
            if (row["status"] or "active") not in INACTIVE_STATUSES and normalize_project_name(row["name"]) == key:
                return row["id"]
        return None
    finally:
        conn.close()
_source_lock = threading.Condition()
_sources_in_progress: set = set()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    _ensure_governance_schema(conn)
    return conn


# Mapping from candidate_type to approved_change target_type
CANDIDATE_TO_TARGET_TYPE = {
    "todo": "todo",
    "knowledge": "knowledge",
    "project_update": "project",
    "project": "project",
    "fact": "knowledge",
    "data": "data",
    "project_merge": "project_merge",
}

# Mapping to candidate_type allowed values: ('fact', 'todo', 'project_update', 'knowledge', 'conflict', 'ambiguity', 'question', 'project_merge')
TARGET_TO_CANDIDATE_TYPE = {
    "todo": "todo",
    "knowledge": "knowledge",
    "project": "project_update",
    "project_update": "project_update",
    "data": "fact",
    "fact": "fact",
    "project_merge": "project_merge",
}


class GovernanceService:
    def __init__(self) -> None:
        self.db_path = DB_PATH

    # ── File-version registry shared by watcher, cruise and folder scan ──────

    def is_source_analyzed(self, file_hash: str) -> bool:
        # A legacy candidate row may be the FIRST of several rows still being
        # filed. It is never a completion signal while any pipeline owns it.
        with _source_lock:
            if file_hash in _sources_in_progress:
                return False
        return self._is_source_recorded(file_hash)

    def wait_for_source_completion(self, file_hash: str, timeout: float = 60) -> None:
        from backend.pm.source_barrier import wait_until_idle
        wait_until_idle(_source_lock, _sources_in_progress, file_hash, timeout)

    def _is_source_recorded(self, file_hash: str) -> bool:
        """True when this exact file version was analysed before.

        Also recognises versions analysed by releases that predate the
        registry, via the file hash recorded in candidate.source_refs.
        """
        if not file_hash:
            return False
        conn = get_db()
        try:
            recorded = conn.execute("SELECT completion_state FROM analyzed_source WHERE file_hash = ?", (file_hash,)).fetchone()
            if recorded:
                return recorded[0] == 'complete'
            # Only AI-pipeline candidates count: phase-1 version-review questions
            # (Scanner+...) and rule-engine proposals also carry the file hash
            # but do not mean the model has read the file.
            return conn.execute(
                "SELECT 1 FROM candidate WHERE source_refs LIKE ? "
                "AND analysis_model NOT LIKE 'Scanner+%' AND analysis_model NOT LIKE 'rule:%' LIMIT 1",
                (f'%"{file_hash}"%',),
            ).fetchone() is not None
        finally:
            conn.close()

    def claim_source(self, file_hash: str) -> bool:
        """Reserve a file version for analysis.

        Returns False when it was already analysed or another pipeline is
        analysing it right now; the caller must then skip the model call.
        Callers that get True must call ``release_source`` when done.
        """
        with _source_lock:
            if file_hash in _sources_in_progress:
                return False
            _sources_in_progress.add(file_hash)
        try:
            if self._is_source_recorded(file_hash):
                self.release_source(file_hash)
                return False
        except Exception:
            self.release_source(file_hash)
            raise
        return True

    def release_source(self, file_hash: str, file_name: str = "", pipeline: str = "",
                       candidates_created: Optional[int] = None) -> None:
        """Release a claim; when ``candidates_created`` is given, record the version as analysed."""
        try:
            if candidates_created is not None or pipeline:
                conn = get_db()
                try:
                    with conn:
                        conn.execute(
                            "INSERT OR REPLACE INTO analyzed_source (file_hash, file_name, pipeline, candidates_created, analyzed_at, completion_state) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (file_hash, file_name, pipeline, int(candidates_created or 0), _now_iso(),
                             'complete' if candidates_created is not None else 'failed'),
                        )
                finally:
                    conn.close()
        finally:
            with _source_lock:
                _sources_in_progress.discard(file_hash)
                _source_lock.notify_all()

    def list_candidates(self, status: str = "pending", limit: int = 500) -> List[Dict[str, Any]]:
        conn = get_db()
        try:
            if status == "all":
                cur = conn.execute(
                    "SELECT * FROM candidate ORDER BY created_at DESC LIMIT ?", (limit,)
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM candidate WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                )
            rows = cur.fetchall()
            results = []
            for r in rows:
                d = dict(r)
                for jf in ["proposed_change", "review_brief_json", "review_display_json", "review_internal_evidence_json", "source_refs"]:
                    if d.get(jf) and isinstance(d[jf], str):
                        try:
                            d[jf] = json.loads(d[jf])
                        except Exception:
                            pass
                results.append(d)
            return results
        finally:
            conn.close()

    def get_candidate(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        conn = get_db()
        try:
            row = conn.execute("SELECT * FROM candidate WHERE id = ?", (candidate_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            for jf in ["proposed_change", "review_brief_json", "review_display_json", "review_internal_evidence_json", "source_refs"]:
                if d.get(jf) and isinstance(d[jf], str):
                    try:
                        d[jf] = json.loads(d[jf])
                    except Exception:
                        pass
            return d
        finally:
            conn.close()

    def create_candidate(
        self,
        target_type: str,
        operation: str,
        payload: Dict[str, Any],
        raw_excerpt: str = "",
        source_refs: Optional[List[str]] = None,
        confidence: str = "HIGH",
        analysis_model: str = "DeepSeek-V3",
        review_brief: Optional[Dict[str, Any]] = None,
        evidence_locators: Optional[List[str]] = None,
    ) -> str:
        """Create a candidate with an underlying analysis_run entry."""
        cid = _uuid()
        run_id = _uuid()
        now = _now_iso()
        source_refs_list = source_refs or []
        
        # Normalize target type
        committer_target = CANDIDATE_TO_TARGET_TYPE.get(target_type, target_type)
        candidate_type = TARGET_TO_CANDIDATE_TYPE.get(target_type, "knowledge")

        # Pre-normalize payload for committer consistency
        if isinstance(payload, dict):
            if committer_target == "knowledge":
                from backend.governance.committer import _normalize_knowledge_status, _normalize_knowledge_type
                if "status" in payload:
                    payload["status"] = _normalize_knowledge_status(payload["status"])
                if "knowledge_type" in payload:
                    payload["knowledge_type"] = _normalize_knowledge_type(payload["knowledge_type"])
                if not payload.get("slug") and payload.get("title"):
                    title_slug = re.sub(r"[^\w\u4e00-\u9fa5]+", "-", str(payload["title"])).strip("-")[:40]
                    payload["slug"] = f"doc-{title_slug or _uuid()[:8]}"
            elif committer_target == "project" and operation == "update":
                if not payload.get("id") and payload.get("name"):
                    conn_tmp = get_db()
                    try:
                        match = conn_tmp.execute("SELECT id FROM project WHERE name = ?", (payload["name"],)).fetchone()
                        if not match:
                            match = conn_tmp.execute("SELECT id FROM project WHERE name LIKE ?", (f"%{payload['name'][:8]}%",)).fetchone()
                        if match:
                            payload["id"] = match["id"]
                        else:
                            operation = "create"
                    finally:
                        conn_tmp.close()
            if committer_target == "project" and operation == "create" and payload.get("name"):
                # A "new" project whose name matches an active project is the same
                # project: propose an update instead of creating a duplicate.
                existing_id = _find_active_project_by_name(payload["name"])
                if existing_id:
                    payload["id"] = existing_id
                    operation = "update"

        # Validate schema according to committer schema
        target_schemas = PAYLOAD_SCHEMAS.get(committer_target, {})
        required_fields = target_schemas.get(operation)
        if required_fields is None:
            raise ValueError(f"Unsupported committer target/operation: {committer_target}.{operation}")

        # Construct fingerprint to avoid duplicate entries
        from backend.pm.source_fingerprint import proposal_fingerprint
        dedup_fp = proposal_fingerprint(committer_target,operation,payload,source_refs_list)

        # Wrap payload with operation metadata so consumer knows exact committer action
        proposed_change_data = {
            "target_type": committer_target,
            "operation": operation,
            "payload": payload,
        }

        display_json = {
            "title": payload.get("title") or payload.get("name") or f"{committer_target} {operation}",
            "description": payload.get("description") or raw_excerpt[:120],
            "target_type": committer_target,
            "operation": operation,
            "locators": evidence_locators or [],
        }

        conn = get_db()
        try:
            with conn:
                # The same proposal was already approved or rejected: the user has
                # decided it, so do not put it back into the review queue.
                decided = conn.execute(
                    "SELECT id FROM candidate WHERE dedup_fingerprint = ? AND status IN (?, ?, ?) LIMIT 1",
                    (dedup_fp, *_DECIDED_STATUSES),
                ).fetchone()
                if decided:
                    return decided["id"]
                # Check if identical pending candidate already exists to prevent duplicate proposals
                existing = conn.execute(
                    "SELECT id FROM candidate WHERE dedup_fingerprint = ? AND status = 'pending'",
                    (dedup_fp,),
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE candidate SET last_seen_at = ? WHERE id = ?",
                        (now, existing["id"]),
                    )
                    return existing["id"]

                # 1. Insert analysis_run row to satisfy Foreign Key
                conn.execute(
                    """INSERT INTO analysis_run (
                        id, trigger_type, raw_text, raw_input_refs, analysis_model,
                        started_at, completed_at, created_at
                    ) VALUES (?, 'document_ingest', ?, ?, ?, ?, ?, ?)""",
                    (
                        run_id,
                        raw_excerpt,
                        json.dumps(source_refs_list),
                        analysis_model,
                        now,
                        now,
                        now,
                    ),
                )

                # 2. Insert candidate row
                conn.execute(
                    """INSERT INTO candidate (
                        id, analysis_run_id, candidate_type, source_refs, raw_excerpt,
                        proposed_change, confidence, inference_level, recommended_action,
                        analysis_model, status, created_at, dedup_fingerprint,
                        review_brief_json, review_display_json, review_internal_evidence_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'EXPLICIT', 'AUTO_WRITE', ?, 'pending', ?, ?, ?, ?, ?)""",
                    (
                        cid,
                        run_id,
                        candidate_type,
                        json.dumps(source_refs_list),
                        raw_excerpt,
                        json.dumps(proposed_change_data, ensure_ascii=False),
                        confidence if confidence in ("HIGH", "MEDIUM", "LOW") else "HIGH",
                        analysis_model,
                        now,
                        dedup_fp,
                        json.dumps(review_brief or {}, ensure_ascii=False),
                        json.dumps(display_json, ensure_ascii=False),
                        json.dumps({"evidence_locators": evidence_locators or []}, ensure_ascii=False),
                    ),
                )
            return cid
        finally:
            conn.close()

    def reject_candidate(self, candidate_id: str, reviewer: str = "LocalUser", notes: str = "") -> Dict[str, Any]:
        now = _now_iso()
        conn = get_db()
        try:
            with conn:
                cur = conn.execute("SELECT * FROM candidate WHERE id = ?", (candidate_id,))
                c = cur.fetchone()
                if not c:
                    raise ValueError(f"Candidate {candidate_id} not found")
                if c["status"] != "pending":
                    raise ValueError(f"Candidate status is {c['status']}, cannot reject")

                rd_id = _uuid()
                conn.execute(
                    """INSERT INTO review_decision (
                        id, candidate_id, decision, reviewer, review_channel,
                        approval_source, original_payload, reviewed_payload,
                        authorization_payload_hash, review_notes, created_at
                    ) VALUES (?, ?, 'reject', ?, 'web_review', 'candidate_review', ?, NULL, NULL, ?, ?)""",
                    (rd_id, candidate_id, reviewer, c["proposed_change"], notes, now),
                )

                conn.execute(
                    "UPDATE candidate SET status = 'rejected', reviewed_at = ?, reviewed_by = ? WHERE id = ?",
                    (now, reviewer, candidate_id),
                )
            return {"candidate_id": candidate_id, "status": "rejected", "decision_id": rd_id}
        finally:
            conn.close()

    def accept_and_commit_candidate(
        self,
        candidate_id: str,
        reviewer: str = "LocalUser",
        review_notes: str = "",
        edited_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Strict governance closure:

        Candidate -> ReviewDecision (approve) -> ApprovedChange -> Committer -> CommitRecord
        """
        now = _now_iso()
        conn = get_db()
        try:
            cur = conn.execute("SELECT * FROM candidate WHERE id = ?", (candidate_id,))
            c = cur.fetchone()
            if not c:
                raise ValueError(f"Candidate {candidate_id} not found")
            if c["status"] != "pending":
                if c["status"] in ("accepted", "rejected", "committed", "applied"):
                    return {
                        "ok": True,
                        "status": "already_processed",
                        "message": "Already processed",
                        "candidate_id": candidate_id,
                        "current_status": c["status"],
                    }
                raise ValueError(f"Candidate status is {c['status']}, cannot approve")

            change_data = json.loads(c["proposed_change"])
            if isinstance(change_data, dict) and "payload" in change_data and "target_type" in change_data:
                target_type = change_data["target_type"]
                operation = change_data["operation"]
                raw_payload = edited_payload if edited_payload is not None else change_data["payload"]
            else:
                target_type = CANDIDATE_TO_TARGET_TYPE.get(c["candidate_type"], "knowledge")
                operation = "create"
                raw_payload = edited_payload if edited_payload is not None else change_data

            # Normalization fallback for committer payload requirements
            if isinstance(raw_payload, dict):
                if target_type == "knowledge":
                    if operation == "create":
                        if not raw_payload.get("slug"):
                            title_slug = re.sub(r"[^\w\u4e00-\u9fa5]+", "-", str(raw_payload.get("title", ""))).strip("-")[:40]
                            raw_payload["slug"] = f"doc-{title_slug or _uuid()[:8]}"
                    from backend.governance.committer import _normalize_knowledge_status, _normalize_knowledge_type
                    if "status" in raw_payload:
                        raw_payload["status"] = _normalize_knowledge_status(raw_payload["status"])
                    if "knowledge_type" in raw_payload:
                        raw_payload["knowledge_type"] = _normalize_knowledge_type(raw_payload["knowledge_type"])
                elif target_type == "project" and operation == "update":
                    if not raw_payload.get("id"):
                        cand_target_id = c["target_id"] if "target_id" in c.keys() else None
                        if cand_target_id:
                            raw_payload["id"] = cand_target_id
                        elif raw_payload.get("name"):
                            match_row = conn.execute("SELECT id FROM project WHERE name = ?", (raw_payload["name"],)).fetchone()
                            if not match_row:
                                match_row = conn.execute("SELECT id FROM project WHERE name LIKE ?", (f"%{raw_payload['name'][:8]}%",)).fetchone()
                            if match_row:
                                raw_payload["id"] = match_row["id"]
                    if not raw_payload.get("latest_progress_summary"):
                        raw_payload["latest_progress_summary"] = str(raw_payload.get("note") or raw_payload.get("description") or f"{raw_payload.get('name', '项目')} 治理更新")

            auth_hash = _payload_hash(raw_payload)
            decision = "edit_and_approve" if edited_payload is not None else "approve"

            rd_id = _uuid()
            ac_id = _uuid()

            with conn:
                # 1. Create ReviewDecision
                conn.execute(
                    """INSERT INTO review_decision (
                        id, candidate_id, decision, reviewer, review_channel,
                        approval_source, original_payload, reviewed_payload,
                        authorization_payload_hash, review_notes, created_at
                    ) VALUES (?, ?, ?, ?, 'web_review', 'candidate_review', ?, ?, ?, ?, ?)""",
                    (
                        rd_id,
                        candidate_id,
                        decision,
                        reviewer,
                        json.dumps(change_data, ensure_ascii=False),
                        json.dumps(raw_payload, ensure_ascii=False),
                        auth_hash,
                        review_notes,
                        now,
                    ),
                )

                # 2. Create ApprovedChange with status = 'pending'
                conn.execute(
                    """INSERT INTO approved_change (
                        change_id, target_type, operation, payload, approval_ref,
                        source_refs, expected_target_version, idempotency_key, created_at, status
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, 'pending')""",
                    (
                        ac_id,
                        target_type,
                        operation,
                        json.dumps(raw_payload, ensure_ascii=False),
                        rd_id,
                        c["source_refs"],
                        f"candidate-{candidate_id}",
                        now,
                    ),
                )

                # 3. Update Candidate status to accepted
                conn.execute(
                    "UPDATE candidate SET status = 'accepted', reviewed_at = ?, reviewed_by = ? WHERE id = ?",
                    (now, reviewer, candidate_id),
                )
        finally:
            conn.close()

        # 4. Deterministic Committer execution
        try:
            commit_result = commit_approved_change(ac_id)
        except Exception as e:
            conn = get_db()
            try:
                with conn:
                    conn.execute("UPDATE candidate SET status = 'pending', reviewed_at = NULL, reviewed_by = NULL WHERE id = ?", (candidate_id,))
            finally:
                conn.close()
            raise RuntimeError(f"Committer execution exception: {e}")

        if commit_result.get("result") != "success":
            conn = get_db()
            try:
                with conn:
                    conn.execute("UPDATE candidate SET status = 'pending', reviewed_at = NULL, reviewed_by = NULL WHERE id = ?", (candidate_id,))
            finally:
                conn.close()
            raise RuntimeError(f"Committer failed: {commit_result.get('error')}")

        committed_id = commit_result.get("target_id")

        # 5. Mark Candidate as applied with entity linkage
        conn = get_db()
        try:
            with conn:
                conn.execute(
                    """UPDATE candidate SET
                        status = 'applied',
                        applied_entity_type = ?,
                        applied_entity_id = ?
                       WHERE id = ?""",
                    (target_type, str(committed_id), candidate_id),
                )
        finally:
            conn.close()

        # 6. Publish COMMITTED event on EventBus (Requirement 7.3.2)
        event_bus.publish(
            EventType.COMMITTED,
            payload={
                "candidate_id": candidate_id,
                "target_type": target_type,
                "target_id": str(committed_id),
                "commit_id": commit_result.get("commit_id"),
                "operation": operation,
                "reviewer": reviewer,
                "timestamp": now,
            },
            source="committer",
        )

        return {
            "candidate_id": candidate_id,
            "status": "applied",
            "decision_id": rd_id,
            "approved_change_id": ac_id,
            "commit_id": commit_result.get("commit_id"),
            "target_id": committed_id,
            "target_type": target_type,
            "operation": operation,
        }


governance = GovernanceService()
