"""Production Bootstrap Runner: resume-first, bounded, durable, fail-closed.

Orchestration / control plane only.  Semantic analysis must come from an
injected ModelAdapter or an external GPT MCP caller.  This process never
pretends Cursor is GPT Brain and never auto-marks remaining SourceVersions
CONSOLIDATED / NO_KNOWLEDGE_IMPACT.
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from gpt_brain_common import (
    COVERAGE_FINAL_STATES,
    COVERAGE_RESULT_STATES,
    FailClosed,
    FileLeaseLock,
    LAYER_SCHEMA_VERSION,
    LockedJsonStore,
    hash_payload,
    read_json,
    safe_copy,
    text,
    unique,
    utc_now,
)
from gpt_brain_data_layer import MetricDataLayer
from gpt_brain_project_builder import ProjectBuilder, default_project_catalog

DEFAULT_BRAIN_STATE = "/var/lib/paw-mcp-gateway/gpt_brain_bootstrap.json"
DEFAULT_BATCH_SIZE = 5
RUNNER_STATUS_CREATED = "CREATED"
RUNNER_STATUS_RUNNING = "RUNNING"
RUNNER_STATUS_PAUSED = "PAUSED"
RUNNER_STATUS_WAITING_GPT = "WAITING_GPT"
RUNNER_STATUS_WAITING_MODEL = "WAITING_MODEL"
RUNNER_STATUS_COMPLETED = "COMPLETED"
RUNNER_STATUS_FAILED = "FAILED"
RUNNER_STATUS_STOPPED = "STOPPED"

GPT_INVOCATION_LIVE = "LIVE"
GPT_INVOCATION_WAITING_GPT = "WAITING_GPT"
GPT_INVOCATION_BLOCKED = "BLOCKED"


class BrainControlPlane(Protocol):
    def status(self) -> dict[str, Any]: ...
    def start(self, *, model: str = "GPT-5.6 Sol", actor: str = "chatgpt") -> dict[str, Any]: ...
    def next_source_batch(self, limit: int = 20) -> dict[str, Any]: ...
    def read_source_semantic(self, source_version_id: str) -> dict[str, Any]: ...
    def record_consolidation(self, **kwargs: Any) -> dict[str, Any]: ...
    def close_bootstrap(self) -> dict[str, Any]: ...


class ModelAdapter(Protocol):
    """Optional live GPT/Workspace-Agent consumer.  Must not be Cursor-as-GPT."""

    name: str

    def available(self) -> bool: ...
    def analyze(self, work_item: Mapping[str, Any], semantic: Mapping[str, Any]) -> Mapping[str, Any] | None: ...


@dataclass
class WaitingGptAdapter:
    name: str = "none"

    def available(self) -> bool:
        return False

    def analyze(self, work_item: Mapping[str, Any], semantic: Mapping[str, Any]) -> Mapping[str, Any] | None:
        return None


@dataclass
class ScriptedAdapter:
    """Deterministic test adapter.  Never used as a live GPT stand-in."""

    scripts: dict[str, Mapping[str, Any]]
    name: str = "scripted"
    fail_ids: set[str] = field(default_factory=set)
    delay_s: float = 0.0

    def available(self) -> bool:
        return True

    def analyze(self, work_item: Mapping[str, Any], semantic: Mapping[str, Any]) -> Mapping[str, Any] | None:
        if self.delay_s:
            time.sleep(self.delay_s)
        sid = text(work_item.get("source_version_id"))
        if sid in self.fail_ids:
            raise RuntimeError(f"scripted adapter failed for {sid}")
        return self.scripts.get(sid)


def probe_gpt_invocation_path() -> dict[str, Any]:
    """Presence-only probe.  Never returns secret values."""
    names = [
        "OPENAI_API_KEY",
        "OPENAI_API_BASE",
        "OPENAI_BASE_URL",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "PAW_GPT_API_KEY",
        "PAW_GPT_INVOCATION_URL",
        "PAW_WORKSPACE_AGENT_URL",
        "CHAT_GPT_API_KEY",
        "GPT_API_KEY",
    ]
    present = [name for name in names if text(os.environ.get(name))]
    return {
        "path": GPT_INVOCATION_WAITING_GPT,
        "reason": "no outbound GPT/Workspace-Agent invocation credentials or URL configured",
        "env_names_present": present,
        "cursor_as_gpt_forbidden": True,
        "mcp_inbound_chatgpt_expected": True,
    }


def _blank_runner(run_id: str, *, actor: str, model: str, batch_size: int) -> dict[str, Any]:
    now = utc_now()
    return {
        "schema": "paw-gpt-brain-bootstrap-runner/1",
        "layer_schema_version": LAYER_SCHEMA_VERSION,
        "run_id": run_id,
        "status": RUNNER_STATUS_CREATED,
        "batch_no": 0,
        "batch_size": batch_size,
        "inflight": [],
        "completed_source_ids": [],
        "skipped_final_source_ids": [],
        "failed_source_ids": [],
        "progress": {
            "claimed": 0,
            "recorded": 0,
            "skipped_final": 0,
            "waiting_external": 0,
            "failed": 0,
            "transient_failures": 0,
            "automation_review_required": 0,
        },
        "last_success": None,
        "last_error": None,
        "retry_count": 0,
        "started_at": None,
        "updated_at": now,
        "paused_at": None,
        "stopped_at": None,
        "duration_seconds": None,
        "model": model,
        "actor": actor,
        "gpt_invocation_path": GPT_INVOCATION_WAITING_GPT,
        "frozen_queue_hash": None,
        "frozen_queue_version": None,
        "brain_run_id": None,
        "stop_requested": False,
        "pause_requested": False,
        "visual_tasks_started": 0,
        "formal_state_bypass": 0,
        "events": [],
        "created_at": now,
    }


def _event(run: dict[str, Any], event_type: str, **details: Any) -> None:
    run.setdefault("events", []).append({"at": utc_now(), "type": event_type, **details})
    run["updated_at"] = utc_now()


def _duration(run: dict[str, Any]) -> float | None:
    started = run.get("started_at")
    if not started:
        return None
    try:
        from datetime import datetime
        end = datetime.fromisoformat(run.get("updated_at") or utc_now())
        start = datetime.fromisoformat(started)
        return (end - start).total_seconds()
    except (TypeError, ValueError):
        return None


class InMemoryBrain:
    """Minimal Brain double for Runner unit tests.  Not a semantic model."""

    def __init__(self, source_ids: list[str], *, queue_hash: str = "test-queue-hash"):
        self.source_ids = list(source_ids)
        self.queue_hash = queue_hash
        self._status = "ACTIVE"
        self.states: dict[str, dict[str, Any]] = {
            sid: {"source_version_id": sid, "state": "UNSEEN", "claim_id": None, "result_refs": []}
            for sid in source_ids
        }
        self.consolidations: list[dict[str, Any]] = []
        self.closed = False
        self.identity_mismatch = False

    def mark_final(self, source_version_id: str, state: str = "CONSOLIDATED") -> None:
        self.states[source_version_id]["state"] = state

    def status(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for state in self.states.values():
            key = state.get("state") or "UNKNOWN"
            counts[key] = counts.get(key, 0) + 1
        consumed = sum(counts.get(s, 0) for s in COVERAGE_FINAL_STATES)
        total = len(self.source_ids)
        return {
            "bootstrap": {
                "run_id": "brain-test",
                "status": self._status,
                "frozen_queue": {"queue_hash": self.queue_hash, "queue_version": "v1", "total_sources": total},
                "source_state_counts": counts,
                "cursor_source_total": total,
                "bootstrap_consumed": consumed,
                "unprocessed_source": max(0, total - consumed),
            },
            "governance": {"formal_state_bypass": 0},
        }

    def start(self, *, model: str = "GPT-5.6 Sol", actor: str = "chatgpt") -> dict[str, Any]:
        if self._status == "COMPLETED":
            return self.status()
        self._status = "ACTIVE"
        return self.status()

    def next_source_batch(self, limit: int = 20) -> dict[str, Any]:
        if self.identity_mismatch:
            raise FailClosed("queue/source identity mismatch")
        if self._status != "ACTIVE":
            raise RuntimeError("Bootstrap is not ACTIVE")
        selected = []
        for sid in self.source_ids:
            if len(selected) >= limit:
                break
            state = self.states[sid]
            if state["state"] != "UNSEEN":
                continue
            claim_id = uuid.uuid4().hex
            state.update({"state": "CLAIMED", "claim_id": claim_id, "claimed_at": utc_now()})
            selected.append({"source_version_id": sid, "claim_id": claim_id, "visual_pending": False})
        return {"items": selected, "count": len(selected)}

    def read_source_semantic(self, source_version_id: str) -> dict[str, Any]:
        if source_version_id not in self.states:
            raise KeyError(source_version_id)
        return {
            "source_version_id": source_version_id,
            "available": True,
            "semantic_result": {"status": "SUCCESS", "visual_pending": False, "source_version_id": source_version_id},
        }

    def record_consolidation(self, **kwargs: Any) -> dict[str, Any]:
        sid = kwargs["source_version_id"]
        claim_id = kwargs["claim_id"]
        coverage = text(kwargs["coverage_state"]).upper()
        state = self.states[sid]
        if state.get("state") in COVERAGE_FINAL_STATES:
            if state.get("state") == coverage and state.get("claim_id") == claim_id:
                return {"status": "already_recorded", "source_state": dict(state)}
            raise ValueError("SourceVersion already has a final coverage state")
        if state.get("state") != "CLAIMED" or state.get("claim_id") != claim_id:
            raise ValueError("valid Brain source claim required")
        if coverage not in COVERAGE_RESULT_STATES:
            raise ValueError("invalid consolidation result state")
        state.update({"state": coverage, "completed_at": utc_now()})
        self.consolidations.append(dict(kwargs))
        return {"status": "recorded", "source_state": dict(state)}

    def close_bootstrap(self) -> dict[str, Any]:
        unfinished = [sid for sid, st in self.states.items() if st.get("state") not in COVERAGE_FINAL_STATES]
        if unfinished:
            raise ValueError("coverage incomplete")
        self._status = "COMPLETED"
        self.closed = True
        return self.status()


class BootstrapRunner:
    def __init__(
        self,
        *,
        store: LockedJsonStore,
        brain: BrainControlPlane,
        data_layer: MetricDataLayer | None = None,
        project_builder: ProjectBuilder | None = None,
        adapter: ModelAdapter | None = None,
        lock: FileLeaseLock | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        actor: str = "bootstrap-runner",
        model: str = "WAITING_GPT",
        processor_provenance: Mapping[str, Any] | None = None,
        max_retries: int = 3,
        backoff_seconds: tuple[float, ...] = (0.05, 0.15, 0.45),
    ):
        self.store = store
        self.brain = brain
        self.data_layer = data_layer
        self.project_builder = project_builder
        self.adapter = adapter or WaitingGptAdapter()
        self.lock = lock
        self.batch_size = max(1, min(int(batch_size), 50))
        self.actor = actor
        self.model = model
        self.processor_provenance = dict(processor_provenance or {})
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self._record_lock = threading.RLock()

    def _load(self) -> dict[str, Any]:
        data = self.store.load()
        return data if data else _blank_runner(uuid.uuid4().hex, actor=self.actor, model=self.model, batch_size=self.batch_size)

    def _save(self, run: dict[str, Any]) -> None:
        run["batch_size"] = self.batch_size
        run["duration_seconds"] = _duration(run)
        run["updated_at"] = utc_now()
        self.store.save(run)

    def status(self) -> dict[str, Any]:
        run = self._load()
        brain_status = None
        brain_error = None
        try:
            brain_status = self.brain.status()
        except Exception as exc:
            brain_error = str(exc)[:300]
        invocation = probe_gpt_invocation_path()
        if self.adapter.available() and not isinstance(self.adapter, WaitingGptAdapter):
            invocation = {
                "path": GPT_INVOCATION_LIVE,
                "reason": f"adapter={self.adapter.name}",
                "env_names_present": invocation.get("env_names_present") or [],
                "cursor_as_gpt_forbidden": True,
                "mcp_inbound_chatgpt_expected": True,
            }
        bootstrap = (brain_status or {}).get("bootstrap") or {}
        counts = bootstrap.get("source_state_counts") or {}
        return {
            "runner": safe_copy(run),
            "brain": {
                "run_id": bootstrap.get("run_id"),
                "status": bootstrap.get("status"),
                "cursor_source_total": bootstrap.get("cursor_source_total"),
                "bootstrap_consumed": bootstrap.get("bootstrap_consumed"),
                "unprocessed_source": bootstrap.get("unprocessed_source"),
                "source_state_counts": counts,
                "error": brain_error,
            },
            "gpt_invocation_path": invocation["path"],
            "gpt_invocation": invocation,
            "data_layer": self.data_layer.snapshot() if self.data_layer else None,
            "project_builder": self.project_builder.snapshot() if self.project_builder else None,
            "governance": {
                "formal_state_bypass": 0,
                "final_review_decision": "USER_AUTHORITY_PRESERVED",
                "visual_task_started": int(run.get("visual_tasks_started") or 0),
                "ui_freeze_touched": False,
            },
            "machine": {
                "run_id": run.get("run_id"),
                "status": run.get("status"),
                "batch_no": run.get("batch_no"),
                "last_success": run.get("last_success"),
                "last_error": run.get("last_error"),
                "retry_count": run.get("retry_count"),
                "gpt_invocation_path": invocation["path"],
            },
        }

    def _ensure_identity(self, run: dict[str, Any], brain_status: Mapping[str, Any]) -> None:
        bootstrap = brain_status.get("bootstrap") or {}
        frozen = bootstrap.get("frozen_queue") or {}
        queue_hash = text(frozen.get("queue_hash"))
        queue_version = text(frozen.get("queue_version"))
        if run.get("frozen_queue_hash") is None:
            run["frozen_queue_hash"] = queue_hash or None
            run["frozen_queue_version"] = queue_version or None
            run["brain_run_id"] = bootstrap.get("run_id")
            return
        if queue_hash and run.get("frozen_queue_hash") and queue_hash != run.get("frozen_queue_hash"):
            raise FailClosed("Cursor/Brain queue_hash mismatch; refusing to advance")
        if queue_version and run.get("frozen_queue_version") and queue_version != run.get("frozen_queue_version"):
            raise FailClosed("Cursor/Brain queue_version mismatch; refusing to advance")
        if bootstrap.get("run_id") and run.get("brain_run_id") and bootstrap.get("run_id") != run.get("brain_run_id"):
            raise FailClosed("Brain run_id changed; refusing to advance a different Bootstrap")

    def _refuse_historical_rerun(self, brain_status: Mapping[str, Any]) -> None:
        bootstrap = brain_status.get("bootstrap") or {}
        if text(bootstrap.get("status")).upper() == "COMPLETED":
            raise FailClosed("Bootstrap already COMPLETED; historical full rerun is prohibited")

    def start(self) -> dict[str, Any]:
        return self._with_lock(self._start_locked)

    def resume(self) -> dict[str, Any]:
        return self.start()

    def recover_from_failed(self, *, reason: str = "operator recovery from transient failure") -> dict[str, Any]:
        """Minimal formal recovery entrypoint for a FAILED runner.

        A transient environment error (e.g. historical ENOSPC while the disk
        later recovered) must not permanently poison the runner: the worker
        loop treats FAILED as terminal and refuses to resume, leaving durable
        bootstrap work stranded.  This entrypoint restores the runner to
        RUNNING from its durable progress WITHOUT re-reading completed
        sources.  Full failure history is preserved in
        failed_recovery_history.  The same fail-closed guards as start()
        apply: historical full rerun is refused and queue identity is
        re-verified before recovery.
        """
        def _recover_locked() -> dict[str, Any]:
            run = self._load()
            if run.get("status") != RUNNER_STATUS_FAILED:
                raise FailClosed(f"cannot recover from {run.get('status')}; recovery is FAILED-only")
            # Re-run the same fail-closed guards as start().
            brain_status = self.brain.status()
            self._refuse_historical_rerun(brain_status)
            self._ensure_identity(run, brain_status)
            run.setdefault("failed_recovery_history", []).append({
                "recovered_at": utc_now(),
                "reason": reason,
                "previous_status": RUNNER_STATUS_FAILED,
                "previous_last_error": run.get("last_error"),
                "previous_updated_at": run.get("updated_at"),
                "progress_snapshot": dict(run.get("progress") or {}),
            })
            run["last_error"] = None
            run["stop_requested"] = False
            run["pause_requested"] = False
            run["status"] = RUNNER_STATUS_RUNNING
            _event(run, "failed_recovery", reason=reason)
            self._save(run)
            return self.status()
        return self._with_lock(_recover_locked)

    def pause(self, reason: str = "operator pause") -> dict[str, Any]:
        run = self._load()
        if run.get("status") in {RUNNER_STATUS_COMPLETED, RUNNER_STATUS_FAILED, RUNNER_STATUS_STOPPED}:
            raise FailClosed(f"cannot pause from {run.get('status')}")
        run["pause_requested"] = True
        run["status"] = RUNNER_STATUS_PAUSED
        run["paused_at"] = utc_now()
        _event(run, "paused", reason=reason)
        self._save(run)
        return self.status()

    def stop(self, reason: str = "operator stop") -> dict[str, Any]:
        run = self._load()
        run["stop_requested"] = True
        if run.get("status") not in {RUNNER_STATUS_COMPLETED, RUNNER_STATUS_FAILED}:
            run["status"] = RUNNER_STATUS_STOPPED
            run["stopped_at"] = utc_now()
        _event(run, "stopped", reason=reason)
        self._save(run)
        return self.status()

    def run_once(self) -> dict[str, Any]:
        return self._with_lock(lambda: self._run_once_locked(loop=False))

    def run_loop(self, *, max_batches: int | None = None) -> dict[str, Any]:
        def _inner() -> dict[str, Any]:
            batches = 0
            while True:
                run = self._load()
                if run.get("stop_requested") or run.get("pause_requested"):
                    break
                if run.get("status") in {RUNNER_STATUS_COMPLETED, RUNNER_STATUS_FAILED, RUNNER_STATUS_STOPPED, RUNNER_STATUS_PAUSED}:
                    break
                if max_batches is not None and batches >= max_batches:
                    break
                self._run_once_locked(loop=True)
                batches += 1
                run = self._load()
                if run.get("status") in {RUNNER_STATUS_WAITING_GPT, RUNNER_STATUS_WAITING_MODEL}:
                    break
            return self.status()
        return self._with_lock(_inner)

    def _with_lock(self, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        if self.lock is None:
            return fn()
        run = self._load()
        self.lock.acquire(run_id=text(run.get("run_id")) or uuid.uuid4().hex, actor=self.actor)
        try:
            return fn()
        finally:
            self.lock.release()

    def _start_locked(self) -> dict[str, Any]:
        run = self._load()
        if not run.get("run_id"):
            run = _blank_runner(uuid.uuid4().hex, actor=self.actor, model=self.model, batch_size=self.batch_size)
        try:
            brain_status = self.brain.status()
            self._refuse_historical_rerun(brain_status)
            self._ensure_identity(run, brain_status)
        except FailClosed as exc:
            run["status"] = RUNNER_STATUS_FAILED
            run["last_error"] = str(exc)[:400]
            _event(run, "fail_closed", error=run["last_error"])
            self._save(run)
            raise
        bootstrap_status = text((brain_status.get("bootstrap") or {}).get("status")).upper()
        if bootstrap_status in {"READY", "WAITING_CURSOR"}:
            try:
                self.brain.start(model=self.model, actor=self.actor)
            except Exception as exc:
                if "ACTIVE" not in str(exc) and bootstrap_status != "ACTIVE":
                    run["status"] = RUNNER_STATUS_FAILED
                    run["last_error"] = str(exc)[:400]
                    _event(run, "brain_start_failed", error=run["last_error"])
                    self._save(run)
                    raise
        elif bootstrap_status == "ACTIVE":
            _event(run, "brain_already_active", brain_run_id=(brain_status.get("bootstrap") or {}).get("run_id"))
        elif bootstrap_status == "COMPLETED":
            raise FailClosed("Bootstrap already COMPLETED; historical full rerun is prohibited")
        else:
            run["status"] = RUNNER_STATUS_FAILED
            run["last_error"] = f"unexpected brain status {bootstrap_status}"
            _event(run, "brain_status_unexpected", status=bootstrap_status)
            self._save(run)
            raise FailClosed(run["last_error"])
        run["stop_requested"] = False
        run["pause_requested"] = False
        if run.get("status") in {RUNNER_STATUS_CREATED, RUNNER_STATUS_PAUSED, RUNNER_STATUS_STOPPED, RUNNER_STATUS_WAITING_GPT, RUNNER_STATUS_WAITING_MODEL}:
            run["status"] = RUNNER_STATUS_RUNNING
            run["started_at"] = run.get("started_at") or utc_now()
            _event(run, "started", resume=bool(run.get("started_at")))
        self._save(run)
        return self._run_once_locked(loop=False)

    def _run_once_locked(self, *, loop: bool) -> dict[str, Any]:
        run = self._load()
        if run.get("status") in {RUNNER_STATUS_COMPLETED, RUNNER_STATUS_FAILED}:
            if run.get("status") == RUNNER_STATUS_COMPLETED:
                raise FailClosed("Bootstrap already COMPLETED; historical full rerun is prohibited")
            return self.status()
        try:
            brain_status = self.brain.status()
            self._refuse_historical_rerun(brain_status)
            self._ensure_identity(run, brain_status)
        except FailClosed as exc:
            run["status"] = RUNNER_STATUS_FAILED
            run["last_error"] = str(exc)[:400]
            _event(run, "fail_closed", error=run["last_error"])
            self._save(run)
            raise

        if not self.adapter.available():
            run["status"] = RUNNER_STATUS_WAITING_GPT
            run["gpt_invocation_path"] = GPT_INVOCATION_WAITING_GPT
            run["last_error"] = None
            _event(run, "waiting_gpt", reason="no live GPT invocation path; inbound ChatGPT MCP remains the consumer")
            self._save(run)
            return self.status()

        run["gpt_invocation_path"] = GPT_INVOCATION_LIVE
        run["status"] = RUNNER_STATUS_RUNNING
        run["started_at"] = run.get("started_at") or utc_now()

        inflight = list(run.get("inflight") or [])
        slots = max(0, self.batch_size - len(inflight))
        if slots > 0:
            try:
                batch = self.brain.next_source_batch(slots)
            except FailClosed:
                run["status"] = RUNNER_STATUS_FAILED
                run["last_error"] = "queue/source identity mismatch"
                _event(run, "fail_closed", error=run["last_error"])
                self._save(run)
                raise
            except Exception as exc:
                run["status"] = RUNNER_STATUS_FAILED
                run["last_error"] = str(exc)[:400]
                _event(run, "claim_failed", error=run["last_error"])
                self._save(run)
                raise FailClosed(run["last_error"]) from exc
            items = list(batch.get("items") or [])
            run["batch_no"] = int(run.get("batch_no") or 0) + 1
            for item in items:
                sid = text(item.get("source_version_id"))
                owned = {
                    "source_version_id": sid,
                    "claim_id": item.get("claim_id"),
                    "retry_count": 0,
                    "claimed_at": utc_now(),
                }
                inflight.append(owned)
                run["progress"]["claimed"] = int(run["progress"].get("claimed") or 0) + 1
            run["inflight"] = inflight
            self._save(run)

        if not inflight:
            # No new claims: either waiting on external CLAIMED sources or coverage is done.
            try:
                brain_status = self.brain.status()
            except Exception:
                brain_status = {}
            counts = (brain_status.get("bootstrap") or {}).get("source_state_counts") or {}
            claimed = int(counts.get("CLAIMED") or 0)
            unseen = int(counts.get("UNSEEN") or 0)
            if unseen == 0 and claimed == 0 and int((brain_status.get("bootstrap") or {}).get("unprocessed_source") or 0) == 0:
                try:
                    self.brain.close_bootstrap()
                    run["status"] = RUNNER_STATUS_COMPLETED
                    _event(run, "bootstrap_completed")
                except Exception as exc:
                    run["status"] = RUNNER_STATUS_WAITING_GPT
                    run["last_error"] = str(exc)[:400]
                    _event(run, "close_blocked", error=run["last_error"])
            elif claimed:
                run["status"] = RUNNER_STATUS_WAITING_GPT
                run["progress"]["waiting_external"] = claimed
                _event(run, "waiting_external_claims", claimed=claimed)
            else:
                run["status"] = RUNNER_STATUS_WAITING_GPT
                _event(run, "empty_batch")
            self._save(run)
            return self.status()

        self._consume_many(run, inflight)
        self._save(run)
        return self.status()

    def _record_automation_terminal(self, run: dict[str, Any], item: Mapping[str, Any], exc: Exception) -> bool:
        """Fail closed for one Source without blocking the global claim queue.

        Used only by adapters that own durable child identity. Scripted test
        adapters keep the previous isolated-failure behaviour.
        """
        if isinstance(exc, FailClosed):
            return False
        if not hasattr(self.adapter, "reject_current_child"):
            return False
        sid = text(item.get("source_version_id"))
        claim_id = text(item.get("claim_id"))
        if not sid or not claim_id:
            return False
        try:
            self.brain.record_consolidation(
                source_version_id=sid,
                claim_id=claim_id,
                coverage_state="REVIEW_REQUIRED",
                findings=[{
                    "finding_type": "AUTOMATION_CONTRACT_VALIDATION_FAILED",
                    "severity": "HIGH",
                    "assertion": (
                        f"AUTOMATION_CONTRACT_VALIDATION_FAILED source_version_id={sid} "
                        f"claim_id={claim_id} error_category=CHILD_EXHAUSTED"
                    ),
                    "source_version_id": sid,
                    "affected_targets": [],
                }],
                knowledge_proposals=[],
                project_consolidations=[],
                metric_proposals=[],
                reason=f"AUTOMATION_CONTRACT_VALIDATION_FAILED: {exc}",
                processor_provenance=self.processor_provenance or None,
            )
        except Exception:
            return False
        run["completed_source_ids"] = unique([*run.get("completed_source_ids", []), sid])
        run["progress"]["recorded"] = int(run["progress"].get("recorded") or 0) + 1
        run["progress"]["automation_review_required"] = int(run["progress"].get("automation_review_required") or 0) + 1
        run["last_success"] = {"source_version_id": sid, "coverage_state": "REVIEW_REQUIRED", "at": utc_now()}
        run["last_error"] = None
        return True

    def _semantic_for_item(self, item: Mapping[str, Any]) -> dict[str, Any]:
        sid = text(item.get("source_version_id"))
        semantic = self.brain.read_source_semantic(sid)
        if semantic.get("source_version_id") not in {"", sid} and text(semantic.get("source_version_id")) != sid:
            raise FailClosed("semantic SourceVersion identity mismatch")
        payload = semantic.get("semantic_result") or {}
        if payload and text(payload.get("source_version_id")) not in {"", sid}:
            raise FailClosed("semantic result SourceVersion identity mismatch")
        return semantic

    def _analyze_item(self, item: Mapping[str, Any], semantic: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any] | None, Exception | None]:
        try:
            contract = self.adapter.analyze(item, semantic)
            return item, contract, None
        except Exception as exc:
            return item, None, exc

    def _apply_item_failure(self, run: dict[str, Any], item: dict[str, Any], exc: Exception) -> bool:
        """Return True if the item should remain inflight for retry."""
        sid = text(item.get("source_version_id"))
        item["last_error"] = str(exc)[:400]
        run["last_error"] = item["last_error"]
        can_retry = _retryable(exc) and int(item.get("retry_count") or 0) < self.max_retries
        if can_retry:
            item["retry_count"] = int(item.get("retry_count") or 0) + 1
            run["retry_count"] = int(run.get("retry_count") or 0) + 1
            run["progress"]["transient_failures"] = int(run["progress"].get("transient_failures") or 0) + 1
            _event(run, "partial_batch_retry", source_version_id=sid, retry=item["retry_count"], error=item["last_error"])
            delay = self.backoff_seconds[min(item["retry_count"] - 1, len(self.backoff_seconds) - 1)]
            if delay:
                time.sleep(delay)
            return True
        if self._record_automation_terminal(run, item, exc):
            _event(run, "automation_terminal_recorded", source_version_id=sid, error=item["last_error"])
            return False
        run["failed_source_ids"] = unique([*run.get("failed_source_ids", []), sid])
        run["progress"]["failed"] = int(run["progress"].get("failed") or 0) + 1
        _event(run, "partial_batch_isolated_failure", source_version_id=sid, error=item["last_error"])
        return False

    def _consume_many(self, run: dict[str, Any], items: list[dict[str, Any]]) -> None:
        """Analyze up to batch_size items in parallel; serialize Brain/sidecar writes."""
        prepared: list[tuple[dict[str, Any], dict[str, Any]]] = []
        remaining: list[dict[str, Any]] = []
        for item in items:
            try:
                semantic = self._semantic_for_item(item)
                prepared.append((item, semantic))
            except Exception as exc:
                if self._apply_item_failure(run, item, exc):
                    remaining.append(item)
        if not prepared:
            run["inflight"] = remaining
            return
        workers = max(1, min(len(prepared), self.batch_size))
        outcomes: list[tuple[Mapping[str, Any], Mapping[str, Any] | None, Exception | None]]
        if workers == 1:
            item, semantic = prepared[0]
            outcomes = [self._analyze_item(item, semantic)]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(self._analyze_item, item, semantic) for item, semantic in prepared]
                outcomes = [fut.result() for fut in futures]
        still: list[dict[str, Any]] = []
        for item, contract, error in outcomes:
            owned = dict(item)
            try:
                if error is not None:
                    raise error
                if contract is None:
                    raise RuntimeError("WAITING_MODEL: adapter returned no contract")
                with self._record_lock:
                    self._commit_contract(run, owned, contract)
            except Exception as exc:
                if self._apply_item_failure(run, owned, exc):
                    still.append(owned)
        run["inflight"] = remaining + still

    def _commit_contract(self, run: dict[str, Any], item: Mapping[str, Any], contract: Mapping[str, Any]) -> None:
        sid = text(item.get("source_version_id"))
        claim_id = text(item.get("claim_id"))
        normalized = normalize_contract(contract, source_version_id=sid, claim_id=claim_id)
        if normalized["coverage_state"] == "VISUAL_PENDING":
            run["visual_tasks_started"] = int(run.get("visual_tasks_started") or 0)
        try:
            result = self.brain.record_consolidation(
                source_version_id=sid,
                claim_id=claim_id,
                coverage_state=normalized["coverage_state"],
                findings=normalized.get("findings") or [],
                knowledge_proposals=normalized.get("knowledge_proposals") or [],
                project_consolidations=normalized.get("project_consolidations") or [],
                metric_proposals=_numeric_metric_proposals(normalized),
                reason=normalized.get("reason") or "",
                processor_provenance=self.processor_provenance or None,
            )
        except ValueError as exc:
            reject = getattr(self.adapter, "reject_current_child", None)
            if callable(reject):
                reject(sid, claim_id, error=str(exc), category="BRAIN_CONTRACT")
            can_retry = getattr(self.adapter, "can_retry_child", None)
            if callable(can_retry) and can_retry(sid, claim_id):
                raise RuntimeError(f"BRAIN_CONTRACT_RETRY: {exc}") from exc
            result = self.brain.record_consolidation(
                source_version_id=sid,
                claim_id=claim_id,
                coverage_state="REVIEW_REQUIRED",
                findings=[{
                    "finding_type": "AUTOMATION_CONTRACT_VALIDATION_FAILED",
                    "severity": "HIGH",
                    "assertion": (
                        f"AUTOMATION_CONTRACT_VALIDATION_FAILED source_version_id={sid} "
                        f"claim_id={claim_id} error_category=BRAIN_CONTRACT"
                    ),
                    "source_version_id": sid,
                    "affected_targets": [],
                }],
                knowledge_proposals=[],
                project_consolidations=[],
                metric_proposals=[],
                reason=f"AUTOMATION_CONTRACT_VALIDATION_FAILED: {exc}",
                processor_provenance=self.processor_provenance or None,
            )
            normalized = dict(normalized)
            normalized["coverage_state"] = "REVIEW_REQUIRED"
            normalized["knowledge_proposals"] = []
            normalized["project_consolidations"] = []
            normalized["metric_proposals"] = []
            normalized["data_assets"] = []
            run["progress"]["automation_review_required"] = int(run["progress"].get("automation_review_required") or 0) + 1
        sidecar_errors: list[str] = []
        if self.project_builder is not None:
            for update in list(normalized.get("project_consolidations") or []) + list(normalized.get("project_builder_updates") or []):
                if not isinstance(update, Mapping):
                    continue
                try:
                    self.project_builder.apply_update(update, source_version_id=sid)
                except Exception as exc:
                    sidecar_errors.append(f"project_builder:{exc}"[:200])
        if self.data_layer is not None:
            for metric in normalized.get("metric_proposals") or []:
                if not isinstance(metric, Mapping):
                    continue
                try:
                    self.data_layer.ingest_observation(metric, source_version_id=sid)
                except Exception as exc:
                    sidecar_errors.append(f"data_layer.metric:{exc}"[:200])
            for asset in normalized.get("data_assets") or []:
                if not isinstance(asset, Mapping):
                    continue
                try:
                    self.data_layer.ingest_data_asset(asset, source_version_id=sid)
                except Exception as exc:
                    sidecar_errors.append(f"data_layer.asset:{exc}"[:200])
        if sidecar_errors:
            _event(run, "sidecar_ingest_error", source_version_id=sid, error="; ".join(sidecar_errors)[:400])
        run["completed_source_ids"] = unique([*run.get("completed_source_ids", []), sid])
        run["progress"]["recorded"] = int(run["progress"].get("recorded") or 0) + 1
        run["last_success"] = {"source_version_id": sid, "coverage_state": normalized["coverage_state"], "at": utc_now()}
        run["last_error"] = None
        _event(run, "recorded", source_version_id=sid, coverage_state=normalized["coverage_state"], brain_status=result.get("status"))

    def _consume_one(self, run: dict[str, Any], item: Mapping[str, Any]) -> None:
        semantic = self._semantic_for_item(item)
        contract = self.adapter.analyze(item, semantic)
        if contract is None:
            raise RuntimeError("WAITING_MODEL: adapter returned no contract")
        self._commit_contract(run, item, contract)


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, FailClosed):
        return False
    if isinstance(exc, ValueError):
        return False
    return True


def normalize_contract(contract: Mapping[str, Any], *, source_version_id: str, claim_id: str) -> dict[str, Any]:
    coverage = text(contract.get("coverage_state")).upper()
    if coverage not in COVERAGE_RESULT_STATES:
        raise FailClosed(f"invalid coverage_state: {coverage or '<empty>'}")
    return {
        "source_version_id": source_version_id,
        "claim_id": claim_id,
        "coverage_state": coverage,
        "knowledge_proposals": list(contract.get("knowledge_proposals") or []),
        "project_consolidations": list(contract.get("project_consolidations") or []),
        "project_builder_updates": list(contract.get("project_builder_updates") or []),
        "metric_proposals": list(contract.get("metric_proposals") or []),
        "data_assets": list(contract.get("data_assets") or []),
        "findings": list(contract.get("findings") or []),
        "reason": text(contract.get("reason")),
        "evidence_references": list(contract.get("evidence_references") or contract.get("evidence") or []),
        "contract_hash": hash_payload({
            "source_version_id": source_version_id,
            "coverage_state": coverage,
            "knowledge_proposals": contract.get("knowledge_proposals") or [],
            "project_consolidations": contract.get("project_consolidations") or [],
            "metric_proposals": contract.get("metric_proposals") or [],
            "findings": contract.get("findings") or [],
            "reason": text(contract.get("reason")),
        }),
    }


def _numeric_metric_proposals(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Brain.record_consolidation currently requires numeric MetricProposal values.

    Free-text / mixed observations stay in the Data Layer only so they are not
    coerced into a numeric口径.
    """
    out = []
    for raw in contract.get("metric_proposals") or []:
        if not isinstance(raw, Mapping):
            continue
        kind = text(raw.get("value_kind") or "NUMERIC").upper()
        if kind in {"FREE_TEXT", "MIXED"}:
            continue
        out.append(dict(raw))
    return out


def default_paths(brain_state: str | os.PathLike[str] | None = None) -> dict[str, Path]:
    brain = Path(os.environ.get("PAW_GPT_BRAIN_STATE_PATH") or brain_state or DEFAULT_BRAIN_STATE)
    layer_dir = Path(os.environ.get("PAW_GPT_BRAIN_LAYER_DIR") or brain.parent)
    return {
        "brain": brain,
        "runner": layer_dir / "gpt_brain_runner.json",
        "data_layer": layer_dir / "gpt_brain_data_layer.json",
        "project_builder": layer_dir / "gpt_brain_project_builder.json",
        "lock": layer_dir / "gpt_brain_bootstrap_runner.lock",
        "layer_dir": layer_dir,
    }


def build_live_runner(
    *,
    brain: BrainControlPlane | None = None,
    adapter: ModelAdapter | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    catalog=None,
) -> BootstrapRunner:
    paths = default_paths()
    if brain is None:
        import sys
        gateway = "/opt/paw-mcp-gateway"
        if gateway not in sys.path:
            sys.path.insert(0, gateway)
        from app.gpt_brain_bootstrap import AtomicJsonStore, GPTKnowledgeBrain
        brain = GPTKnowledgeBrain(store=AtomicJsonStore(paths["brain"]))
    data_layer = MetricDataLayer(LockedJsonStore(paths["data_layer"]))
    project_builder = ProjectBuilder(LockedJsonStore(paths["project_builder"]), catalog or default_project_catalog())
    return BootstrapRunner(
        store=LockedJsonStore(paths["runner"]),
        brain=brain,
        data_layer=data_layer,
        project_builder=project_builder,
        adapter=adapter or WaitingGptAdapter(),
        lock=FileLeaseLock(paths["lock"]),
        batch_size=batch_size,
    )


__all__ = [
    "BootstrapRunner",
    "InMemoryBrain",
    "ScriptedAdapter",
    "WaitingGptAdapter",
    "build_live_runner",
    "default_paths",
    "normalize_contract",
    "probe_gpt_invocation_path",
]
