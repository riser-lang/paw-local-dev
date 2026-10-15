#!/usr/bin/env python3
"""Durable one-source-at-a-time GPT Brain Bootstrap worker.

The worker is deliberately a small adapter around the existing Brain claim,
semantic-read, consolidation, Data Layer, and Project Builder paths.  The
only model invocation is the audited ``codex_operator``; that operator fixes
the child model to gpt-5.6-luna and reasoning effort to high.

This process never performs self-review or Opus dispatch.  Findings are left
in GPT_ANALYZED for the existing ChatGPT-owned self-review handoff.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
GATEWAY = "/opt/paw-mcp-gateway"
if GATEWAY not in sys.path:
    sys.path.insert(0, GATEWAY)
OPERATOR_ROOT = "/opt/paw-chatgpt-mcp-r13"
if OPERATOR_ROOT not in sys.path:
    sys.path.insert(0, OPERATOR_ROOT)

from gpt_brain_bootstrap_runner import BootstrapRunner
from gpt_brain_common import FileLeaseLock, LockedJsonStore, atomic_write_json, hash_payload, read_json, utc_now
from gpt_brain_data_layer import MetricDataLayer
from gpt_brain_project_builder import ProjectBuilder, default_project_catalog
from paw_luna_bootstrap_contract import (
    SCHEMA_NAME,
    SCHEMA_PATH,
    ContractError,
    automation_terminal_contract,
    normalize_luna_contract,
    parse_and_validate,
)

from app.gpt_brain_bootstrap import AtomicJsonStore, GPTKnowledgeBrain
import codex_operator

MODEL = "gpt-5.6-luna"
REASONING = "high"
ACTOR = "paw-codex-luna-bootstrap"
QUEUE_HASH = "7e88ae7daf9a10f28e5a79f70e27edf95f6122ed290fef7ae741d01e12110ffe"
QUEUE_TOTAL = 710
DEFAULT_POLL_SECONDS = 1.0
DEFAULT_CHILD_TIMEOUT = 90 * 60
MAX_CHILD_ATTEMPTS = 3  # original child + 2 fresh repair attempts
LUNA_PARALLELISM = 3
CAPACITY_PATH = Path(os.environ.get("PAW_GPT_BRAIN_CAPACITY_PATH", "/var/lib/paw-mcp-gateway/paw_brain_claim_capacity.json"))
CHILD_REJECTED = "REJECTED_INVALID"
TRANSIENT_SUBMIT_ERRORS = {"WORKSPACE_BUSY", "LAUNCH_FAILED"}
REUSABLE_STATUSES = {"QUEUED", "RUNNING"}
TERMINAL_CHILD_STATUSES = {"COMPLETED", "FAILED", "CANCELED", "TIMEOUT", "INTERRUPTED"}


def _path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default))


def default_paths() -> dict[str, Path]:
    brain = _path("PAW_GPT_BRAIN_STATE_PATH", "/var/lib/paw-mcp-gateway/gpt_brain_bootstrap.json")
    state_root = _path("PAW_CODEX_LUNA_BOOTSTRAP_STATE_DIR", "/var/lib/paw-mcp-gateway")
    return {
        "brain": brain,
        "worker": state_root / "paw_codex_luna_bootstrap_worker.json",
        "children": state_root / "paw_codex_luna_bootstrap_children.json",
        "jobs": state_root / "paw-codex-luna-bootstrap-jobs",
        "lock": state_root / "paw_codex_luna_bootstrap_worker.lock",
        "runner": state_root / "paw_codex_luna_bootstrap_runner.json",
    }


def _blank_worker() -> dict[str, Any]:
    now = utc_now()
    return {
        "schema": "paw-codex-luna-bootstrap-worker/1",
        "status": "CREATED",
        "run_id": str(uuid.uuid4()),
        "model": MODEL,
        "reasoning": REASONING,
        "actor": ACTOR,
        "queue_hash_expected": QUEUE_HASH,
        "queue_total_expected": QUEUE_TOTAL,
        "started_at": None,
        "updated_at": now,
        "current": None,
        "current_sources": [],
        "processed_source_ids": [],
        "last_success": None,
        "last_error": None,
        "hard_blocker": None,
        "throughput": {
            "sources": [],
            "first_attempt_success": 0,
            "repair_attempt_success": 0,
            "automation_review_required": 0,
        },
        "events": [],
        "created_at": now,
    }


class WorkerLedger:
    def __init__(self, path: Path):
        self.store = LockedJsonStore(path)

    def load(self) -> dict[str, Any]:
        data = self.store.load()
        return data if data else _blank_worker()

    def save(self, data: Mapping[str, Any]) -> None:
        self.store.save(data)

    def event(self, data: dict[str, Any], kind: str, **details: Any) -> None:
        data.setdefault("events", []).append({"at": utc_now(), "type": kind, **details})
        data["updated_at"] = utc_now()


class CodexLunaAdapter:
    """ModelAdapter that persists child identity before and after submission."""

    name = "audited-codex-operator:gpt-5.6-luna:reasoning-high"

    def __init__(self, ledger_path: Path, jobs_root: Path, *, poll_seconds: float, timeout_seconds: int):
        self.store = LockedJsonStore(ledger_path)
        self.jobs_root = jobs_root
        self.poll_seconds = max(0.2, min(float(poll_seconds), 5.0))
        self.timeout_seconds = max(10 * 60, min(int(timeout_seconds), int(codex_operator.MAX_TIMEOUT_SECONDS)))
        self.last_timing: dict[str, Any] | None = None
        self.last_timings: dict[str, dict[str, Any]] = {}
        self._submit_lock = threading.Lock()
        self._ledger_lock = threading.Lock()

    def available(self) -> bool:
        return (
            codex_operator.FIXED_MODEL == MODEL
            and Path(codex_operator.CODEX_EXECUTABLE).is_file()
            and SCHEMA_PATH.is_file()
            and "luna_bootstrap_contract" in getattr(codex_operator, "ALLOWED_OUTPUT_SCHEMAS", {})
            and 'model_reasoning_effort="high"' in _codex_argv_preview()
        )

    def _ledger(self) -> dict[str, Any]:
        with self._ledger_lock:
            data = self.store.load()
            if not data:
                data = {"schema": "paw-codex-luna-bootstrap-children/2", "children": {}, "updated_at": utc_now()}
            data.setdefault("children", {})
            data["schema"] = "paw-codex-luna-bootstrap-children/2"
            return data

    def _save_child(self, key: str, entry: Mapping[str, Any]) -> None:
        """Persist one child record without clobbering sibling keys."""
        with self._ledger_lock:
            data = self.store.load() or {}
            data.setdefault("children", {})
            data["schema"] = "paw-codex-luna-bootstrap-children/2"
            data["children"][key] = dict(entry)
            data["updated_at"] = utc_now()
            self.store.save(data)

    def _save_ledger(self, data: dict[str, Any]) -> None:
        # Compatibility for single-key callers that still pass a full ledger.
        children = data.get("children") or {}
        if len(children) == 1:
            key, entry = next(iter(children.items()))
            self._save_child(str(key), entry)
            return
        with self._ledger_lock:
            live = self.store.load() or {}
            live.setdefault("children", {})
            live["schema"] = "paw-codex-luna-bootstrap-children/2"
            live["children"].update(children)
            live["updated_at"] = utc_now()
            self.store.save(live)

    def _key(self, source_version_id: str, claim_id: str) -> str:
        return f"{source_version_id}|{claim_id}"

    def _input_path(self, source_version_id: str, claim_id: str) -> Path:
        return self.jobs_root / (hash_payload({"source_version_id": source_version_id, "claim_id": claim_id})[:32] + ".input.json")

    def _write_input(self, item: Mapping[str, Any], semantic: Mapping[str, Any], path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, {
            "source_version_id": item.get("source_version_id"),
            "claim_id": item.get("claim_id"),
            "work_item": dict(item),
            "canonical_semantic_evidence": dict(semantic),
            "created_at": utc_now(),
        })

    @staticmethod
    def _token(source_version_id: str, claim_id: str, attempt: int) -> str:
        return f"PAW_OPUS_UNUSED_LUNA_BOOTSTRAP_SOURCE:{source_version_id}:{claim_id}:attempt:{int(attempt)}"

    def _find_existing_job(self, token: str, *, exclude_ids: set[str] | None = None) -> str | None:
        """Recover a submitted child if the worker died before recording its id."""
        exclude_ids = exclude_ids or set()
        matches: list[str] = []
        root = Path(codex_operator.RUN_ROOT)
        if not root.is_dir():
            return None
        for entry in root.iterdir():
            if not entry.is_dir() or not codex_operator._validate_job_id(entry.name):
                continue
            if entry.name in exclude_ids:
                continue
            task = entry / "TASK.md"
            try:
                if token in task.read_text(encoding="utf-8"):
                    matches.append(entry.name)
            except OSError:
                continue
        if len(matches) > 1:
            raise RuntimeError(f"duplicate Codex child token found: {token}")
        return matches[0] if matches else None

    def _migrate_entry(self, entry: dict[str, Any], sid: str, claim_id: str) -> dict[str, Any]:
        entry.setdefault("jobs", [])
        entry.setdefault("rejected_job_ids", [])
        entry.setdefault("transient_retry_count", 0)
        entry.setdefault("attempts", 0)
        if entry.get("codex_job_id") and not entry["jobs"]:
            job_id = str(entry["codex_job_id"])
            attempt = max(1, int(entry.get("attempts") or 1))
            entry["attempts"] = attempt
            entry["jobs"] = [{
                "attempt": attempt,
                "codex_job_id": job_id,
                "token": entry.get("token") or self._token(sid, claim_id, attempt),
                "status": "UNKNOWN",
                "migrated": True,
            }]
        return entry

    def _job_record(self, entry: dict[str, Any], job_id: str) -> dict[str, Any] | None:
        for item in entry.get("jobs") or []:
            if str(item.get("codex_job_id")) == str(job_id):
                return item
        return None

    def _mark_job(self, entry: dict[str, Any], job_id: str, **fields: Any) -> None:
        rec = self._job_record(entry, job_id)
        if rec is None:
            rec = {"codex_job_id": job_id, "attempt": int(entry.get("attempts") or 0)}
            entry.setdefault("jobs", []).append(rec)
        rec.update(fields)
        rec["updated_at"] = utc_now()

    def reject_current_child(self, source_version_id: str, claim_id: str, *, error: str, category: str) -> None:
        ledger = self._ledger()
        key = self._key(source_version_id, claim_id)
        entry = dict(ledger["children"].get(key) or {})
        job_id = str(entry.get("codex_job_id") or "")
        if not job_id:
            return
        rejected = list(entry.get("rejected_job_ids") or [])
        if job_id not in rejected:
            rejected.append(job_id)
        entry["rejected_job_ids"] = rejected
        self._mark_job(entry, job_id, status=CHILD_REJECTED, error_category=category, last_error=error[:400])
        entry["codex_job_id"] = None
        entry["last_error"] = error[:400]
        self._save_child(key, entry)

    def can_retry_child(self, source_version_id: str, claim_id: str) -> bool:
        ledger = self._ledger()
        entry = (ledger.get("children") or {}).get(self._key(source_version_id, claim_id)) or {}
        return int(entry.get("attempts") or 0) < MAX_CHILD_ATTEMPTS

    def _prompt(self, source_version_id: str, claim_id: str, input_path: Path, token: str) -> str:
        return f"""You are the semantic consolidation child for PAW GPT Brain.

Read ONLY the canonical evidence JSON at {input_path}. It contains the exact
SourceVersion and claim identity. Do not read WPS raw sources, Cursor files,
Formal State, or unrelated workspace data. Do not edit any repository or
service files. Do not invoke self-review, Opus, Agy, or any other model.
Do not use the network.

Process exactly one SourceVersion: {source_version_id}
Exact claim_id: {claim_id}
Durable job token: {token}

Use the existing PAW governance semantics: Evidence Gate, Data-first, Project
Builder, deduplication, version/timeline logic, and visual evidence rules.
canonical_semantic_evidence.visual_artifacts contains already-read visual
facts (including canonical cursor_visual). visual_screener.no_further_visual_work
means Cursor policy already decided no additional image reading is required.
If visual_artifacts is non-empty or no_further_visual_work is true, consume
those facts and do not wait for another visual reread. Do not invent visual
facts. Use VISUAL_PENDING only when required visual evidence is still missing
after those artifacts. Use only evidence in the input. Preserve uncertainty
and choose one valid coverage_state: CONSOLIDATED, NO_KNOWLEDGE_IMPACT, DATA_EXTRACTED,
PROJECT_CONTEXT_ONLY, VISUAL_PENDING, REVIEW_REQUIRED, or INSUFFICIENT_EVIDENCE.
If findings are needed, include evidence-backed finding objects; they will
enter GPT Brain as GPT_ANALYZED for the existing ChatGPT-owned self-review.

The Codex CLI enforces a server-owned JSON Schema on your final response.
Return exactly one JSON object matching that schema. Required top-level keys:
source_version_id, claim_id, coverage_state, reason, findings,
knowledge_proposals, project_consolidations, metric_proposals, data_assets.
source_version_id must be exactly {source_version_id} and claim_id must be
exactly {claim_id}. Use evidence_locator for every proposal. Numeric metrics
must set value_number to a JSON number and value_text to null. Non-numeric
observations must set value_number to null and put the text in value_text;
never put lists or objects into value_number. knowledge_proposals.action must
be one of CREATE, REVISE, MERGE, RETIRE, UNCHANGED, CONFLICT, or
INSUFFICIENT_EVIDENCE; never UPSERT. data_assets must include
source_table or title from the evidence. Do not invent metric values, dates,
dimensions, project ids, or canonical topics.
"""

    @staticmethod
    def _parse_contract(text: str, source_version_id: str, claim_id: str) -> dict[str, Any]:
        parsed, _meta = parse_and_validate(text, source_version_id=source_version_id, claim_id=claim_id)
        return normalize_luna_contract(parsed, source_version_id=source_version_id)

    def _terminal_contract(self, entry: dict[str, Any], sid: str, claim_id: str, *, category: str, error: str) -> dict[str, Any]:
        child_ids = [str(x.get("codex_job_id")) for x in (entry.get("jobs") or []) if x.get("codex_job_id")]
        contract = automation_terminal_contract(
            source_version_id=sid,
            claim_id=claim_id,
            child_ids=child_ids,
            error_category=category,
            error=error,
        )
        entry["terminal_contract_emitted"] = True
        entry["terminal_error_category"] = category
        entry["completed_at"] = utc_now()
        return contract

    def analyze(self, work_item: Mapping[str, Any], semantic: Mapping[str, Any]) -> Mapping[str, Any] | None:
        sid = str(work_item.get("source_version_id") or "").strip()
        claim_id = str(work_item.get("claim_id") or "").strip()
        if not sid or not claim_id:
            raise RuntimeError("missing SourceVersion claim identity")
        key = self._key(sid, claim_id)
        input_path = self._input_path(sid, claim_id)
        self._write_input(work_item, semantic, input_path)
        ledger = self._ledger()
        entry = self._migrate_entry(dict(ledger["children"].get(key) or {}), sid, claim_id)
        entry.update({
            "source_version_id": sid,
            "claim_id": claim_id,
            "input_path": str(input_path),
            "model": MODEL,
            "reasoning": REASONING,
            "output_schema": SCHEMA_NAME,
        })
        rejected = set(str(x) for x in (entry.get("rejected_job_ids") or []))
        job_id = str(entry.get("codex_job_id") or "") or None
        if job_id and job_id in rejected:
            job_id = None
            entry["codex_job_id"] = None
        wall_started = time.monotonic()
        inference_seconds = 0.0
        last_category = "NO_CHILD"
        last_error = "no child"

        while True:
            attempts = int(entry.get("attempts") or 0)
            if not job_id:
                if attempts >= MAX_CHILD_ATTEMPTS:
                    contract = self._terminal_contract(entry, sid, claim_id, category=last_category, error=last_error)
                    ledger["children"][key] = entry
                    self._save_child(key, entry)
                    self.last_timing = {
                        "source_version_id": sid,
                        "attempts": attempts,
                        "first_attempt": False,
                        "repair_attempt": attempts > 1,
                        "automation_terminal": True,
                        "wall_seconds": time.monotonic() - wall_started,
                        "model_inference_seconds": inference_seconds,
                        "orchestration_seconds": max(0.0, time.monotonic() - wall_started - inference_seconds),
                        "failed_child_ids": [str(x.get("codex_job_id")) for x in entry.get("jobs") or []],
                    }
                    self.last_timings[sid] = dict(self.last_timing)
                    return contract
                next_attempt = attempts + 1
                token = self._token(sid, claim_id, next_attempt)
                entry["submission_intent"] = {
                    "token": token,
                    "source_version_id": sid,
                    "claim_id": claim_id,
                    "attempt": next_attempt,
                    "at": utc_now(),
                }
                ledger["children"][key] = entry
                self._save_child(key, entry)
                existing = self._find_existing_job(token, exclude_ids=rejected)
                if existing:
                    job_id = existing
                    entry["attempts"] = next_attempt
                    entry["codex_job_id"] = job_id
                    self._mark_job(entry, job_id, attempt=next_attempt, token=token, recovered=True, status="RECOVERED")
                else:
                    with self._submit_lock:
                        result = codex_operator.submit_task(
                            self._prompt(sid, claim_id, input_path, token),
                            title=f"PAW Luna Bootstrap {sid}",
                            network_access=False,
                            timeout_seconds=self.timeout_seconds,
                            output_schema=SCHEMA_NAME,
                        )
                    if result.get("error"):
                        entry["last_error"] = result
                        entry["submission_intent"] = None
                        if result.get("error") in TRANSIENT_SUBMIT_ERRORS:
                            entry["transient_retry_count"] = int(entry.get("transient_retry_count") or 0) + 1
                            ledger["children"][key] = entry
                            self._save_child(key, entry)
                            time.sleep(self.poll_seconds)
                            continue
                        raise RuntimeError(f"Codex submission failed: {result.get('error')}")
                    job_id = str(result["job_id"])
                    entry["attempts"] = next_attempt
                    entry["codex_job_id"] = job_id
                    entry["submitted_at"] = utc_now()
                    entry["submission_intent"] = None
                    self._mark_job(
                        entry, job_id,
                        attempt=next_attempt,
                        token=token,
                        status="SUBMITTED",
                        submitted_at=entry["submitted_at"],
                    )
                ledger["children"][key] = entry
                self._save_child(key, entry)

            status = codex_operator.task_status(job_id)
            if status.get("error"):
                last_category = "CHILD_STATUS_ERROR"
                last_error = str(status.get("error"))
                rejected.add(job_id)
                entry["rejected_job_ids"] = sorted(rejected)
                self._mark_job(entry, job_id, status=CHILD_REJECTED, error_category=last_category, last_error=last_error)
                entry["codex_job_id"] = None
                job_id = None
                ledger["children"][key] = entry
                self._save_child(key, entry)
                continue
            entry["last_status"] = {k: status.get(k) for k in ("status", "exit_code", "started_at", "finished_at", "job_id")}
            lifecycle = str(status.get("status") or "")
            if lifecycle in REUSABLE_STATUSES:
                ledger["children"][key] = entry
                self._save_child(key, entry)
                time.sleep(self.poll_seconds)
                continue

            started = status.get("started_at")
            finished = status.get("finished_at")
            try:
                from datetime import datetime
                if started and finished:
                    inference_seconds += max(
                        0.0,
                        (datetime.fromisoformat(str(finished)) - datetime.fromisoformat(str(started))).total_seconds(),
                    )
            except (TypeError, ValueError):
                pass

            if lifecycle != "COMPLETED" or status.get("exit_code") not in (0, None):
                result = codex_operator.read_result(job_id, max_lines=200, max_message_chars=codex_operator.MAX_MESSAGE_CHARS)
                log_tail = str(result.get("log_tail") or "")
                last_category = f"CHILD_{lifecycle or 'UNKNOWN'}"
                last_error = f"Codex child ended {lifecycle} exit={status.get('exit_code')}"
                if "invalid_json_schema" in log_tail:
                    last_category = "OUTPUT_SCHEMA_INVALID"
                    last_error = "Codex rejected server-owned output schema"
                    entry["attempts"] = MAX_CHILD_ATTEMPTS
                rejected.add(job_id)
                entry["rejected_job_ids"] = sorted(rejected)
                self._mark_job(entry, job_id, status=CHILD_REJECTED, error_category=last_category, last_error=last_error[:400])
                entry["codex_job_id"] = None
                job_id = None
                ledger["children"][key] = entry
                self._save_child(key, entry)
                continue

            result = codex_operator.read_result(job_id, max_lines=200, max_message_chars=codex_operator.MAX_MESSAGE_CHARS)
            if result.get("error") or result.get("status") != "COMPLETED":
                last_category = "RESULT_UNAVAILABLE"
                last_error = f"Codex result unavailable for {sid}"
                rejected.add(job_id)
                entry["rejected_job_ids"] = sorted(rejected)
                self._mark_job(entry, job_id, status=CHILD_REJECTED, error_category=last_category)
                entry["codex_job_id"] = None
                job_id = None
                ledger["children"][key] = entry
                self._save_child(key, entry)
                continue
            try:
                contract = self._parse_contract(result.get("final_message", ""), sid, claim_id)
            except ContractError as exc:
                last_category = exc.category
                last_error = str(exc)
                rejected.add(job_id)
                entry["rejected_job_ids"] = sorted(rejected)
                self._mark_job(entry, job_id, status=CHILD_REJECTED, error_category=exc.category, last_error=str(exc)[:400])
                entry["codex_job_id"] = None
                job_id = None
                ledger["children"][key] = entry
                self._save_child(key, entry)
                continue
            entry["completed_at"] = utc_now()
            entry["contract_hash"] = hash_payload(contract)
            entry["codex_job_id"] = job_id
            entry["submission_intent"] = None
            self._mark_job(entry, job_id, status="ACCEPTED", completed_at=entry["completed_at"])
            ledger["children"][key] = entry
            self._save_child(key, entry)
            attempts = int(entry.get("attempts") or 1)
            wall = time.monotonic() - wall_started
            self.last_timing = {
                "source_version_id": sid,
                "attempts": attempts,
                "first_attempt": attempts == 1,
                "repair_attempt": attempts > 1,
                "automation_terminal": False,
                "wall_seconds": wall,
                "model_inference_seconds": inference_seconds,
                "orchestration_seconds": max(0.0, wall - inference_seconds),
                "accepted_child_id": job_id,
            }
            self.last_timings[sid] = dict(self.last_timing)
            return contract


def _codex_argv_preview() -> list[str]:
    return ["--model", codex_operator.FIXED_MODEL, "-c", 'model_reasoning_effort="high"']


def load_luna_parallelism() -> int:
    try:
        from app.gpt_brain_bootstrap import configured_max_active_claims
        n, _single = configured_max_active_claims(capacity_path=CAPACITY_PATH)
        return max(2, min(int(n), LUNA_PARALLELISM))
    except Exception:
        payload = read_json(CAPACITY_PATH) if CAPACITY_PATH.is_file() else {}
        try:
            n = int(payload.get("max_active_claims") or LUNA_PARALLELISM)
        except (TypeError, ValueError):
            n = LUNA_PARALLELISM
        return max(2, min(n, LUNA_PARALLELISM))


def write_capacity(*, max_active_claims: int, reason: str, resource: Mapping[str, Any] | None = None) -> None:
    payload = {
        "schema": "paw-brain-claim-capacity/1",
        "max_active_claims": int(max_active_claims),
        "global_single_flight": int(max_active_claims) <= 1,
        "allow_single_flight": int(max_active_claims) <= 1,
        "concurrency_2_unsafe": int(max_active_claims) <= 1,
        "reason": reason,
        "codex_operator_cap": 3,
        "updated_at": utc_now(),
        "resource": dict(resource or {}),
    }
    CAPACITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(CAPACITY_PATH, payload)


def build_runner(paths: Mapping[str, Path], *, poll_seconds: float, timeout_seconds: int, batch_size: int | None = None) -> BootstrapRunner:
    brain = GPTKnowledgeBrain(store=AtomicJsonStore(paths["brain"]))
    adapter = CodexLunaAdapter(paths["children"], paths["jobs"], poll_seconds=poll_seconds, timeout_seconds=timeout_seconds)
    provenance = {
        "processor": "codex",
        "operator": "audited-codex-operator",
        "model": MODEL,
        "reasoning": REASONING,
        "actor": ACTOR,
    }
    effective = max(1, min(int(batch_size or load_luna_parallelism()), LUNA_PARALLELISM))
    return BootstrapRunner(
        store=LockedJsonStore(paths["runner"]),
        brain=brain,
        data_layer=MetricDataLayer(LockedJsonStore(paths["brain"].parent / "gpt_brain_data_layer.json")),
        project_builder=ProjectBuilder(LockedJsonStore(paths["brain"].parent / "gpt_brain_project_builder.json"), default_project_catalog()),
        adapter=adapter,
        lock=None,
        batch_size=effective,
        actor=ACTOR,
        model=MODEL,
        processor_provenance=provenance,
        max_retries=2,
        backoff_seconds=(0.2, 0.5),
    )


def _ensure_brain_active(brain: GPTKnowledgeBrain) -> dict[str, Any]:
    status = brain.status()
    bootstrap = status.get("bootstrap") or {}
    frozen = bootstrap.get("frozen_queue") or {}
    if frozen.get("queue_hash") != QUEUE_HASH or int(frozen.get("total_sources") or 0) != QUEUE_TOTAL:
        raise RuntimeError("frozen queue identity does not match required 710-source queue")
    state = str(bootstrap.get("status") or "")
    if state == "ACTIVE":
        return status
    if state in {"READY", "WAITING_CURSOR"}:
        return brain.start(model=MODEL, actor=ACTOR)
    if state == "COMPLETED":
        raise RuntimeError("Bootstrap already COMPLETED; historical rerun prohibited")
    raise RuntimeError(f"Bootstrap cannot start from {state}")


def _recover_orphaned_claim(runner: BootstrapRunner, brain: GPTKnowledgeBrain, children_path: Path) -> None:
    """Reattach durable children to Brain claims after a local runner error.

    Retry budget is restored from the child ledger and is never reset to zero.
    An unrelated ChatGPT claim is left alone.
    """
    status = brain.status()
    bootstrap = status.get("bootstrap") or {}
    actives = list(bootstrap.get("active_claims") or [])
    if not actives and bootstrap.get("active_claim"):
        actives = [bootstrap["active_claim"]]
    children = LockedJsonStore(children_path).load().get("children") or {}
    run = runner._load()
    inflight = list(run.get("inflight") or [])
    recovered = 0
    for active in actives:
        if not isinstance(active, Mapping):
            continue
        sid = str(active.get("source_version_id") or "").strip()
        claim_id = str(active.get("claim_id") or "").strip()
        if not sid or not claim_id:
            continue
        child = children.get(f"{sid}|{claim_id}")
        if not isinstance(child, Mapping):
            continue
        if child.get("terminal_contract_emitted") and not child.get("codex_job_id"):
            continue
        durable_transient = int(child.get("transient_retry_count") or 0)
        found = False
        for item in inflight:
            if item.get("source_version_id") == sid and item.get("claim_id") == claim_id:
                inflight_retry = int(item.get("retry_count") or 0)
                if inflight_retry > MAX_CHILD_ATTEMPTS:
                    item["retry_count"] = durable_transient
                else:
                    item["retry_count"] = max(inflight_retry, durable_transient)
                found = True
                break
        if not found:
            inflight.append({
                "source_version_id": sid,
                "claim_id": claim_id,
                "retry_count": durable_transient,
                "claimed_at": active.get("claimed_at"),
            })
        recovered += 1
        run.setdefault("events", []).append({
            "at": utc_now(),
            "type": "orphaned_claim_recovered",
            "source_version_id": sid,
            "claim_id": claim_id,
            "codex_job_id": child.get("codex_job_id"),
            "attempts": child.get("attempts"),
            "transient_retry_count": durable_transient,
        })
    if not recovered:
        return
    run["inflight"] = inflight
    run["failed_source_ids"] = [x for x in run.get("failed_source_ids") or [] if x not in {i.get("source_version_id") for i in inflight}]
    run["status"] = "RUNNING"
    runner._save(run)


def _record_throughput(data: dict[str, Any], adapter: CodexLunaAdapter, coverage_state: str | None, source_version_id: str | None = None) -> None:
    timings = list((getattr(adapter, "last_timings", None) or {}).values())
    if source_version_id and getattr(adapter, "last_timings", None):
        one = adapter.last_timings.get(source_version_id)
        timings = [one] if one else timings
    elif getattr(adapter, "last_timing", None) and not timings:
        timings = [adapter.last_timing]
    seen = {(row.get("source_version_id"), row.get("accepted_child_id")) for row in data.setdefault("throughput", {}).setdefault("sources", [])}
    for timing in timings:
        if not isinstance(timing, Mapping):
            continue
        key = (timing.get("source_version_id"), timing.get("accepted_child_id"))
        if key in seen and key[0]:
            continue
        row = dict(timing)
        row["coverage_state"] = coverage_state
        row["at"] = utc_now()
        data.setdefault("throughput", {}).setdefault("sources", []).append(row)
        seen.add(key)
        if row.get("automation_terminal"):
            data["throughput"]["automation_review_required"] = int(data["throughput"].get("automation_review_required") or 0) + 1
        elif row.get("first_attempt"):
            data["throughput"]["first_attempt_success"] = int(data["throughput"].get("first_attempt_success") or 0) + 1
        elif row.get("repair_attempt"):
            data["throughput"]["repair_attempt_success"] = int(data["throughput"].get("repair_attempt_success") or 0) + 1


def run_forever(*, poll_seconds: float = DEFAULT_POLL_SECONDS, timeout_seconds: int = DEFAULT_CHILD_TIMEOUT) -> None:
    paths = default_paths()
    paths["jobs"].mkdir(parents=True, exist_ok=True)
    worker_store = WorkerLedger(paths["worker"])
    data = worker_store.load()
    data.setdefault("throughput", _blank_worker()["throughput"])
    lock = FileLeaseLock(paths["lock"], stale_seconds=900)
    lock.acquire(run_id=str(data.get("run_id") or uuid.uuid4()), actor=ACTOR)
    try:
        data["status"] = "RUNNING"
        data["started_at"] = data.get("started_at") or utc_now()
        data["last_error"] = None
        worker_store.event(data, "service_started", tunnel_restart_required=False, poll_seconds=poll_seconds)
        worker_store.save(data)
        if not CAPACITY_PATH.is_file():
            write_capacity(max_active_claims=LUNA_PARALLELISM, reason="codex_operator_MAX_CONCURRENT_WRITE_JOBS=3")
        brain = GPTKnowledgeBrain(store=AtomicJsonStore(paths["brain"]))
        runner = build_runner(paths, poll_seconds=poll_seconds, timeout_seconds=timeout_seconds)
        adapter = runner.adapter
        while True:
            try:
                _ensure_brain_active(brain)
                _recover_orphaned_claim(runner, brain, paths["children"])
                run = runner._load()
                if run.get("status") in {"COMPLETED"}:
                    data["status"] = "COMPLETED"
                    worker_store.event(data, "bootstrap_completed")
                    worker_store.save(data)
                    return
                if run.get("status") == "FAILED":
                    raise RuntimeError(run.get("last_error") or "existing runner failed")
                before_recorded = int((run.get("progress") or {}).get("recorded") or 0)
                result = runner._run_once_locked(loop=True)
                b = (result.get("brain") or {})
                data["brain_status"] = b
                current_list = list(runner._load().get("inflight") or [])
                data["current"] = current_list[0] if current_list else None
                data["current_sources"] = current_list
                data["luna_parallelism"] = runner.batch_size
                data["last_error"] = None
                after = runner._load()
                after_recorded = int((after.get("progress") or {}).get("recorded") or 0)
                if after_recorded > before_recorded:
                    last = after.get("last_success") or {}
                    _record_throughput(data, adapter, last.get("coverage_state"))
                    data["last_success"] = {"bootstrap_consumed": b.get("bootstrap_consumed"), "at": utc_now(), **last}
                data["status"] = "WAITING_EXTERNAL_CLAIM" if (b.get("source_state_counts") or {}).get("CLAIMED", 0) and not current_list else "RUNNING"
                worker_store.save(data)
            except Exception as exc:
                data["status"] = "BLOCKED"
                data["hard_blocker"] = str(exc)[:1000]
                data["last_error"] = data["hard_blocker"]
                worker_store.event(data, "hard_blocker", error=data["hard_blocker"])
                worker_store.save(data)
                time.sleep(max(30.0, poll_seconds))
                continue
            # Child wait already happened inside analyze(); do not add a 15s gap.
            time.sleep(0.2)
    finally:
        lock.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="perform one bounded orchestration pass")
    parser.add_argument("--status", action="store_true", help="print worker and Brain status")
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--child-timeout-seconds", type=int, default=DEFAULT_CHILD_TIMEOUT)
    args = parser.parse_args(argv)
    paths = default_paths()
    if args.status:
        worker = WorkerLedger(paths["worker"]).load()
        brain = GPTKnowledgeBrain(store=AtomicJsonStore(paths["brain"]))
        print(json.dumps({
            "worker": worker,
            "runner": read_json(paths["runner"]),
            "children": read_json(paths["children"]),
            "brain": brain.status(),
            "codex": {
                "model": MODEL,
                "reasoning": REASONING,
                "operator_fixed_model": codex_operator.FIXED_MODEL,
                "output_schema": SCHEMA_NAME,
            },
        }, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.once:
        run_forever(poll_seconds=args.poll_seconds, timeout_seconds=args.child_timeout_seconds)
        return 0
    run_forever(poll_seconds=args.poll_seconds, timeout_seconds=args.child_timeout_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
