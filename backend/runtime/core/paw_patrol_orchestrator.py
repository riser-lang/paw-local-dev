#!/usr/bin/env python3
"""GPT Patrol-bound integration orchestrator.

Ingest enqueues durable input references. Semantic Todo / Knowledge / Project
writes happen only when a frozen patrol batch is paired with a GPT Patrol
authored Semantic Integration Bundle that passes the coherence gate.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Mapping

from gpt_brain_common import LockedJsonStore, utc_now
from paw_patrol_contract import (
    BATCH_SCHEMA,
    FORMAL_STATE_BYPASS,
    KIND_CAPTURE,
    KIND_DECISION_RECEIPT,
    KIND_SOURCE,
    PATROL_READY,
    PROJECT_BOUNDARY_RECOMMENDATION,
    QUEUED_FOR_PATROL,
    STATUS_APPLIED,
    STATUS_FROZEN,
    STATUS_HELD,
    STATUS_OPEN,
    STATUS_PARTIAL_APPLY,
    USER_DECISION_RECEIVED_WAITING_NEXT_PATROL,
    WEAK_EVIDENCE_HOLD,
    blank_batch,
    blank_bundle,
    fingerprint,
    freeze_batch,
    input_ref,
    is_stale_write_plan,
    journal_entry,
    user_decision_receipt,
    validate_batch,
    validate_bundle,
    _list,
    _mapping,
    _text,
)
from paw_patrol_project_revision import apply_authored_project_revision
from paw_project_semantic_coherence import validate_coherence
from paw_capture_patrol_enqueue import overlay_capture_inputs, reconcile_unprocessed_captures

DEFAULT_STORE = Path(os.environ.get(
    "PAW_PATROL_BATCH_STORE",
    "/var/lib/paw-mcp-gateway/paw_patrol_input_batch.json",
))


class PatrolOrchestratorError(RuntimeError):
    pass


def default_store_path() -> Path:
    return Path(os.environ.get("PAW_PATROL_BATCH_STORE", str(DEFAULT_STORE)))


class PatrolOrchestrator:
    def __init__(self, store_path: str | Path | None = None, *, db_path: str | None = None):
        self.store = LockedJsonStore(store_path or default_store_path())
        self.db_path = db_path or os.environ.get("PAW_WORKSPACE_DB", os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db")))

    def load(self) -> dict[str, Any]:
        data = self.store.load()
        if not data:
            data = {
                "schema": "paw-patrol-orchestrator/1",
                "open_batch_id": None,
                "batches": {},
                "bundles": {},
                "receipts": {},
                "formal_state_bypass": FORMAL_STATE_BYPASS,
                "created_at": utc_now(),
            }
        data.setdefault("batches", {})
        data.setdefault("bundles", {})
        data.setdefault("receipts", {})
        data["formal_state_bypass"] = FORMAL_STATE_BYPASS
        return data

    def save(self, data: Mapping[str, Any]) -> None:
        payload = dict(data)
        payload["updated_at"] = utc_now()
        payload["formal_state_bypass"] = FORMAL_STATE_BYPASS
        self.store.save(payload)

    def _open_batch(self, data: dict[str, Any]) -> dict[str, Any]:
        batch_id = _text(data.get("open_batch_id"))
        batch = _mapping(data["batches"].get(batch_id)) if batch_id else {}
        if batch and not batch.get("frozen"):
            return batch
        batch_id = "patrol-batch:" + uuid.uuid4().hex
        batch = blank_batch(batch_id=batch_id)
        data["open_batch_id"] = batch_id
        data["batches"][batch_id] = batch
        return batch

    def enqueue(
        self,
        *,
        kind: str,
        ref_id: str,
        payload_ref: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = self.load()
        batch = self._open_batch(data)
        if batch.get("frozen"):
            batch = self._open_batch(data)
        refs = [(_text(x.get("kind")), _text(x.get("ref_id"))) for x in _list(batch.get("inputs")) if isinstance(x, Mapping)]
        key = (kind, _text(ref_id))
        if key in refs:
            return {
                "status": "IDEMPOTENT",
                "batch_id": batch["batch_id"],
                "kind": kind,
                "ref_id": ref_id,
                "semantic_processing": False,
                "formal_state_writes": False,
                "formal_state_bypass": FORMAL_STATE_BYPASS,
            }
        batch.setdefault("inputs", []).append(input_ref(
            kind=kind, ref_id=ref_id, payload_ref=payload_ref, extra=extra,
        ))
        batch["updated_at"] = utc_now()
        batch["semantic_processing"] = False
        data["batches"][batch["batch_id"]] = batch
        self.save(data)
        return {
            "status": QUEUED_FOR_PATROL,
            "batch_id": batch["batch_id"],
            "kind": kind,
            "ref_id": ref_id,
            "semantic_processing": False,
            "todo_written": False,
            "knowledge_written": False,
            "project_written": False,
            "formal_state_bypass": FORMAL_STATE_BYPASS,
        }

    def enqueue_source(self, source_version_id: str, *, payload_ref: str | None = None, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self.enqueue(kind=KIND_SOURCE, ref_id=source_version_id, payload_ref=payload_ref, extra=extra)

    def enqueue_capture(self, capture_id: str, *, payload_ref: str | None = None, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self.enqueue(kind=KIND_CAPTURE, ref_id=capture_id, payload_ref=payload_ref, extra=extra)

    def record_user_decision_receipt(
        self,
        *,
        receipt_id: str,
        subject_kind: str,
        subject_id: str,
        decision: str,
        original_input_ref: str | None = None,
        note: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        receipt = user_decision_receipt(
            receipt_id=receipt_id,
            subject_kind=subject_kind,
            subject_id=subject_id,
            decision=decision,
            original_input_ref=original_input_ref,
            note=note,
        )
        if extra:
            receipt["extra"] = dict(extra)
        data = self.load()
        data["receipts"][receipt["receipt_id"]] = receipt
        queued = self.enqueue(
            kind=KIND_DECISION_RECEIPT,
            ref_id=receipt["receipt_id"],
            extra={"subject_kind": subject_kind, "subject_id": subject_id, "original_input_ref": receipt["original_input_ref"]},
        )
        data = self.load()
        data["receipts"][receipt["receipt_id"]] = receipt
        self.save(data)
        return {
            "status": USER_DECISION_RECEIVED_WAITING_NEXT_PATROL,
            "receipt_id": receipt["receipt_id"],
            "immediate_execution": False,
            "batch_id": queued.get("batch_id"),
            "formal_state_bypass": FORMAL_STATE_BYPASS,
        }

    def _sidecar_for_store(self) -> Path | None:
        env = os.environ.get("PAW_CAPTURE_PATROL_SIDECAR")
        if env:
            return Path(env)
        store = Path(self.store.path)
        default = Path(os.environ.get("PAW_PATROL_BATCH_STORE") or "/var/lib/paw-mcp-gateway/paw_patrol_input_batch.json")
        try:
            if store.resolve() == Path(str(default)).resolve():
                return None
        except OSError:
            pass
        return store.parent / "capture_patrol_enqueue.json"

    def merge_pending_captures(self) -> dict[str, Any]:
        """Fold sidecar + processed_at IS NULL captures into the OPEN batch. No analysis."""
        try:
            return reconcile_unprocessed_captures(
                db_path=self.db_path,
                store_path=self.store.path,
                sidecar=self._sidecar_for_store(),
            )
        except Exception as exc:
            return {
                "status": "QUEUE_UNAVAILABLE",
                "error": str(exc)[:300],
                "semantic_processing": False,
                "formal_state_bypass": FORMAL_STATE_BYPASS,
            }

    def freeze_open_batch(self, *, cutoff_at: str | None = None) -> dict[str, Any]:
        self.merge_pending_captures()
        data = self.load()
        batch = self._open_batch(data)
        frozen = freeze_batch(batch, cutoff_at=cutoff_at)
        check = validate_batch(frozen)
        if not check["passed"]:
            raise PatrolOrchestratorError("invalid_batch:" + ",".join(check["errors"]))
        data["batches"][frozen["batch_id"]] = frozen
        if data.get("open_batch_id") == frozen["batch_id"]:
            data["open_batch_id"] = None
        self.save(data)
        return frozen

    def current_batch(self) -> dict[str, Any] | None:
        data = self.load()
        batch_id = _text(data.get("open_batch_id"))
        if batch_id:
            return dict(data["batches"].get(batch_id) or {})
        return None

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        data = self.load()
        batch = data["batches"].get(_text(batch_id))
        return dict(batch) if isinstance(batch, Mapping) else None

    def submit_bundle(self, bundle: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(bundle)
        if not _text(payload.get("write_plan_fingerprint")):
            payload["write_plan_fingerprint"] = fingerprint(payload.get("write_plan") or [])
        check = validate_bundle(payload)
        if not check["passed"]:
            return {"status": "REJECTED", "errors": check["errors"], "formal_state_bypass": 0}
        data = self.load()
        batch = _mapping(data["batches"].get(_text(payload.get("batch_id"))))
        if not batch:
            return {"status": "REJECTED", "errors": ["batch_not_found"], "formal_state_bypass": 0}
        if not batch.get("frozen"):
            return {"status": "REJECTED", "errors": ["batch_not_frozen"], "formal_state_bypass": 0}
        data["bundles"][payload["patrol_run_id"]] = payload
        self.save(data)
        return {"status": "ACCEPTED", "patrol_run_id": payload["patrol_run_id"], "batch_id": payload["batch_id"], "formal_state_bypass": FORMAL_STATE_BYPASS}

    def _load_context(self, bundle: Mapping[str, Any]) -> dict[str, Any]:
        todos: list[dict[str, Any]] = []
        explainer: dict[str, Any] = {}
        handbook: dict[str, Any] = {}
        evidence = [dict(x) for x in _list(bundle.get("evidence_delta")) if isinstance(x, Mapping)]
        if not Path(self.db_path).is_file():
            for delta in _list(bundle.get("project_explainer_delta")):
                explainer = _mapping(_mapping(delta).get("payload") or delta)
            for delta in _list(bundle.get("project_handbook_delta")):
                handbook = _mapping(_mapping(delta).get("payload") or delta)
            for delta in _list(bundle.get("todo_delta")):
                payload = _mapping(_mapping(delta).get("payload") or delta)
                if payload:
                    todos.append(payload)
            return {"todos": todos, "explainer": explainer, "handbook": handbook, "evidence": evidence}
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "todo" in tables:
                todos = [dict(r) for r in conn.execute("SELECT * FROM todo")]
            if "project_handbook" in tables:
                project_ids = []
                for delta in _list(bundle.get("project_semantic_delta")) + _list(bundle.get("project_explainer_delta")) + _list(bundle.get("project_handbook_delta")):
                    pid = _text(_mapping(delta).get("project_id") or _mapping(_mapping(delta).get("payload")).get("id") or _mapping(_mapping(delta).get("payload")).get("project_id"))
                    if pid:
                        project_ids.append(pid)
                if project_ids:
                    row = conn.execute("SELECT * FROM project_handbook WHERE project_id=?", (project_ids[0],)).fetchone()
                    if row:
                        handbook = json.loads(row["sections_json"] or "{}")
                        explainer = _mapping(handbook.get("project_explainer"))
            for delta in _list(bundle.get("project_explainer_delta")):
                payload = _mapping(_mapping(delta).get("payload") or delta)
                if payload.get("explainer") or payload.get("next_step") or payload.get("one_sentence"):
                    explainer = _mapping(payload.get("explainer") or payload)
            for delta in _list(bundle.get("project_handbook_delta")):
                payload = _mapping(_mapping(delta).get("payload") or delta)
                if payload.get("sections"):
                    handbook = _mapping(payload.get("sections"))
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()
        return {"todos": todos, "explainer": explainer, "handbook": handbook, "evidence": evidence}

    def _apply_delta_row(self, conn: sqlite3.Connection, *, target: str, operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        now = utc_now()
        target = _text(target).lower()
        operation = _text(operation).lower() or "update"
        payload = _mapping(payload)
        if target == "todo":
            todo_id = _text(payload.get("id")) or ("todo-" + uuid.uuid4().hex[:12])
            if operation == "create":
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS todo (
                        id TEXT PRIMARY KEY, title TEXT, status TEXT, project_id TEXT,
                        source_capture_id TEXT, created_at TEXT, updated_at TEXT
                    )"""
                )
                conn.execute(
                    "INSERT INTO todo(id, title, status, project_id, source_capture_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                    (
                        todo_id,
                        _text(payload.get("title")),
                        _text(payload.get("status")) or "open",
                        _text(payload.get("project_id")) or None,
                        _text(payload.get("source_capture_id")) or None,
                        now,
                        now,
                    ),
                )
                return {"target_type": "todo", "id": todo_id, "operation": "create"}
            if operation == "update":
                fields = []
                values: list[Any] = []
                for key in ("title", "status", "project_id"):
                    if key in payload:
                        fields.append(f"{key}=?")
                        values.append(payload.get(key))
                if fields:
                    values.append(todo_id)
                    conn.execute(f"UPDATE todo SET {', '.join(fields)}, updated_at=? WHERE id=?", [*values[:-1], now, todo_id])
                return {"target_type": "todo", "id": todo_id, "operation": "update"}
        if target == "knowledge":
            kid = _text(payload.get("id")) or ("knowledge-" + uuid.uuid4().hex[:12])
            conn.execute(
                """CREATE TABLE IF NOT EXISTS knowledge_page (
                    id TEXT PRIMARY KEY, title TEXT, one_line_summary TEXT, status TEXT,
                    created_at TEXT, updated_at TEXT
                )"""
            )
            if operation == "create":
                conn.execute(
                    "INSERT INTO knowledge_page(id, title, one_line_summary, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                    (kid, _text(payload.get("title")), _text(payload.get("one_line_summary") or payload.get("summary")), _text(payload.get("status")) or "active", now, now),
                )
            else:
                conn.execute(
                    "UPDATE knowledge_page SET title=COALESCE(?, title), one_line_summary=COALESCE(?, one_line_summary), status=COALESCE(?, status), updated_at=? WHERE id=?",
                    (_text(payload.get("title")) or None, _text(payload.get("one_line_summary") or payload.get("summary")) or None, _text(payload.get("status")) or None, now, kid),
                )
            return {"target_type": "knowledge", "id": kid, "operation": operation}
        if target == "project":
            pid = _text(payload.get("id") or payload.get("project_id"))
            if not pid:
                return {"target_type": "project", "status": "SKIPPED", "reason": "existing_project_required"}
            if operation in {"create", "merge", "archive", "rename"}:
                raise PatrolOrchestratorError(PROJECT_BOUNDARY_RECOMMENDATION)
            return {"target_type": "project", "id": pid, "operation": "semantic_revision_pending"}
        raise PatrolOrchestratorError("unsupported_apply_target:" + target)

    def apply_bundle(self, patrol_run_id: str, *, stale_item: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = self.load()
        bundle = _mapping(data["bundles"].get(_text(patrol_run_id)))
        if not bundle:
            return {"status": "FAILED_CLOSED", "reason": "bundle_not_found", "formal_state_bypass": 0}
        check = validate_bundle(bundle)
        if not check["passed"]:
            return {"status": "REJECTED", "errors": check["errors"], "formal_state_bypass": 0}
        if stale_item is not None and is_stale_write_plan(stale_item, bundle.get("write_plan")):
            return {"status": "REJECTED", "reason": "stale_write_plan_replay", "formal_state_bypass": 0}
        if stale_item is not None and _text(stale_item.get("stale_write_plan_fingerprint")):
            current_fp = _text(bundle.get("write_plan_fingerprint")) or fingerprint(bundle.get("write_plan") or [])
            if current_fp == _text(stale_item.get("stale_write_plan_fingerprint")):
                return {"status": "REJECTED", "reason": "stale_write_plan_replay", "formal_state_bypass": 0}

        context = self._load_context(bundle)
        gate = validate_coherence(
            bundle=bundle,
            todos=context["todos"],
            explainer=context["explainer"],
            handbook=context["handbook"],
            evidence=context["evidence"],
        )
        if PROJECT_BOUNDARY_RECOMMENDATION in gate.get("errors", []):
            bundle["status"] = STATUS_HELD
            data["bundles"][patrol_run_id] = bundle
            self.save(data)
            return {
                "status": PROJECT_BOUNDARY_RECOMMENDATION,
                "applied": False,
                "recommendations": gate.get("project_boundary_recommendations") or [],
                "gate": gate,
                "formal_state_bypass": FORMAL_STATE_BYPASS,
            }
        if WEAK_EVIDENCE_HOLD in gate.get("holds", []):
            bundle["status"] = STATUS_HELD
            data["bundles"][patrol_run_id] = bundle
            self.save(data)
            return {
                "status": WEAK_EVIDENCE_HOLD,
                "applied": False,
                "gate": gate,
                "formal_state_bypass": FORMAL_STATE_BYPASS,
            }
        if not gate["passed"]:
            return {"status": "COHERENCE_GATE_FAILED", "gate": gate, "applied": False, "formal_state_bypass": 0}

        journal = [dict(x) for x in _list(bundle.get("apply_journal")) if isinstance(x, Mapping)]
        applied_ids = {_text(x.get("op_id")) for x in journal if _text(x.get("status")) == "APPLIED"}
        results: list[dict[str, Any]] = []
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        try:
            ops: list[tuple[str, str, str, Mapping[str, Any]]] = []
            for index, raw in enumerate(_list(bundle.get("todo_delta"))):
                item = _mapping(raw)
                ops.append((f"todo:{index}", "todo", _text(item.get("operation") or "create"), _mapping(item.get("payload") or item)))
            for index, raw in enumerate(_list(bundle.get("knowledge_delta"))):
                item = _mapping(raw)
                ops.append((f"knowledge:{index}", "knowledge", _text(item.get("operation") or "update"), _mapping(item.get("payload") or item)))
            recovered = bool(applied_ids)
            for op_id, target, operation, payload in ops:
                if op_id in applied_ids:
                    results.append({"op_id": op_id, "status": "APPLIED", "recovered": True, "target_type": target})
                    continue
                try:
                    written = self._apply_delta_row(conn, target=target, operation=operation, payload=payload)
                    journal.append(journal_entry(op_id=op_id, target_type=target, status="APPLIED", detail=written))
                    results.append({"op_id": op_id, "status": "APPLIED", **written})
                except Exception as exc:
                    journal.append(journal_entry(op_id=op_id, target_type=target, status="PENDING", detail={"error": str(exc)[:300]}))
                    bundle["apply_journal"] = journal
                    bundle["status"] = STATUS_PARTIAL_APPLY
                    data["bundles"][patrol_run_id] = bundle
                    conn.commit()
                    self.save(data)
                    return {
                        "status": STATUS_PARTIAL_APPLY,
                        "results": results,
                        "pending_op": op_id,
                        "reason": str(exc)[:300],
                        "recovered_prefix": recovered,
                        "formal_state_bypass": FORMAL_STATE_BYPASS,
                    }
            conn.commit()
        except Exception as exc:
            conn.rollback()
            return {"status": "FAILED_CLOSED", "reason": str(exc)[:500], "formal_state_bypass": 0}
        finally:
            conn.close()

        revision_results: list[dict[str, Any]] = []
        for index, raw in enumerate(_list(bundle.get("project_explainer_delta")) + _list(bundle.get("project_handbook_delta"))):
            item = _mapping(raw)
            pid = _text(item.get("project_id") or _mapping(item.get("payload")).get("project_id") or _mapping(item.get("payload")).get("id"))
            if not pid:
                continue
            op_id = f"project-revision:{pid}:{index}"
            if op_id in applied_ids:
                revision_results.append({"op_id": op_id, "status": "APPLIED", "recovered": True})
                continue
            explainer = item if item.get("one_sentence") or item.get("explainer") or _mapping(item.get("payload")).get("next_step") else None
            handbook = item if item.get("sections") or _mapping(item.get("payload")).get("sections") else None
            applied = apply_authored_project_revision(
                db_path=self.db_path,
                project_id=pid,
                explainer_delta=explainer or item,
                handbook_delta=handbook or item,
                provenance=_mapping(bundle.get("provenance")),
                patrol_run_id=_text(bundle.get("patrol_run_id")),
                input_watermark=_text(item.get("input_watermark")),
            )
            if applied.get("status") not in {"COMMITTED", "IDEMPOTENT", "NO_CHANGE"}:
                journal.append(journal_entry(op_id=op_id, target_type="project", status="PENDING", detail=applied))
                bundle["apply_journal"] = journal
                bundle["status"] = STATUS_PARTIAL_APPLY
                data["bundles"][patrol_run_id] = bundle
                self.save(data)
                return {"status": STATUS_PARTIAL_APPLY, "results": results + revision_results, "pending_op": op_id, "reason": applied.get("reason"), "formal_state_bypass": 0}
            journal.append(journal_entry(op_id=op_id, target_type="project", status="APPLIED", detail=applied))
            revision_results.append({"op_id": op_id, **applied})

        bundle["apply_journal"] = journal
        bundle["status"] = STATUS_APPLIED
        data["bundles"][patrol_run_id] = bundle
        batch = _mapping(data["batches"].get(_text(bundle.get("batch_id"))))
        if batch:
            batch["status"] = STATUS_APPLIED
            batch["todo_written"] = bool(_list(bundle.get("todo_delta")))
            batch["knowledge_written"] = bool(_list(bundle.get("knowledge_delta")))
            batch["project_written"] = bool(_list(bundle.get("project_semantic_delta")) or _list(bundle.get("project_explainer_delta")) or _list(bundle.get("project_handbook_delta")))
            data["batches"][batch["batch_id"]] = batch
        self.save(data)
        return {
            "status": STATUS_APPLIED,
            "patrol_run_id": patrol_run_id,
            "results": results + revision_results,
            "gate": gate,
            "canonical_readback": True,
            "python_generated_summary": False,
            "formal_state_bypass": FORMAL_STATE_BYPASS,
        }

    def list_batch_inputs(self, batch_id: str | None = None) -> dict[str, Any]:
        data = self.load()
        batch = None
        if batch_id:
            batch = data["batches"].get(_text(batch_id))
        elif data.get("open_batch_id"):
            batch = data["batches"].get(data["open_batch_id"])
        else:
            frozen = [b for b in data["batches"].values() if isinstance(b, Mapping) and b.get("status") == STATUS_FROZEN]
            batch = frozen[-1] if frozen else None
        if not isinstance(batch, Mapping):
            return {"schema": BATCH_SCHEMA, "inputs": [], "count": 0, "formal_state_bypass": 0}
        inputs = overlay_capture_inputs(list(batch.get("inputs") or []), sidecar=self._sidecar_for_store())
        return {
            "schema": BATCH_SCHEMA,
            "batch_id": batch.get("batch_id"),
            "status": batch.get("status"),
            "cutoff_at": batch.get("cutoff_at"),
            "frozen": bool(batch.get("frozen")),
            "inputs": inputs,
            "count": len(inputs),
            "semantic_processing": False,
            "formal_state_bypass": FORMAL_STATE_BYPASS,
        }


def enqueue_mirror_source(source_version_id: str, *, store_path: str | Path | None = None, payload_ref: str | None = None) -> dict[str, Any]:
    return PatrolOrchestrator(store_path).enqueue_source(source_version_id, payload_ref=payload_ref)


def enqueue_capture_input(capture_id: str, *, store_path: str | Path | None = None) -> dict[str, Any]:
    return PatrolOrchestrator(store_path).enqueue_capture(capture_id)
