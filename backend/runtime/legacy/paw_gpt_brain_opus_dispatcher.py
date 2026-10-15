#!/usr/bin/env python3
"""Durable dispatcher for the existing GPT Brain Opus queue.

Only findings already placed in ``bootstrap.opus.pending_items`` by the
GPT-owned self-review path are eligible.  This service never claims or
records self-review and never performs GPT post-Opus consolidation.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
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

from gpt_brain_common import FileLeaseLock, LockedJsonStore, atomic_write_json, hash_payload, utc_now
from app.gpt_brain_bootstrap import AtomicJsonStore, GPTKnowledgeBrain, OPUS_MODEL_ID
from paw_gpt_review_bundle import ReviewBundleStore, pending_opus_clusters, record_opus_cluster_review
import agy_operator

MODEL = OPUS_MODEL_ID
MODE = "analyze"  # Agy's semantic review mode
ACTOR = "paw-gpt-brain-opus-dispatcher"
MAX_TIMEOUT_SECONDS = int(agy_operator.MAX_TIMEOUT_SECONDS)
DEFAULT_POLL_SECONDS = 20


def _path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default))


def default_paths() -> dict[str, Path]:
    root = _path("PAW_GPT_BRAIN_OPUS_STATE_DIR", "/var/lib/paw-mcp-gateway")
    brain = _path("PAW_GPT_BRAIN_STATE_PATH", "/var/lib/paw-mcp-gateway/gpt_brain_bootstrap.json")
    return {
        "brain": brain,
        "state": root / "paw_gpt_brain_opus_dispatcher.json",
        "jobs": root / "paw-gpt-brain-opus-jobs",
        "lock": root / "paw_gpt_brain_opus_dispatcher.lock",
    }


def _blank() -> dict[str, Any]:
    now = utc_now()
    return {
        "schema": "paw-gpt-brain-opus-dispatcher/1",
        "status": "CREATED",
        "run_id": str(uuid.uuid4()),
        "model": MODEL,
        "mode": MODE,
        "timeout_seconds": MAX_TIMEOUT_SECONDS,
        "actor": ACTOR,
        "started_at": None,
        "updated_at": now,
        "items": {},
        "last_error": None,
        "hard_blocker": None,
        "events": [],
        "created_at": now,
    }


class Ledger:
    def __init__(self, path: Path):
        self.store = LockedJsonStore(path)

    def load(self) -> dict[str, Any]:
        value = self.store.load()
        return value if value else _blank()

    def save(self, value: Mapping[str, Any]) -> None:
        data = dict(value)
        data["updated_at"] = utc_now()
        self.store.save(data)

    def event(self, value: dict[str, Any], kind: str, **details: Any) -> None:
        value.setdefault("events", []).append({"at": utc_now(), "type": kind, **details})
        value["updated_at"] = utc_now()


def _job_ended_without_delivery(job_id: str) -> bool:
    """True when a job terminated without ever delivering a result.

    CANCELLED jobs (e.g. headless permission auto-denial) never produced a
    review; re-submitting the same durable token is a retry of an unfinished
    execution, not a duplicate Opus review.  COMPLETED jobs are never matched
    here, so one-time-only recording semantics are preserved.
    """
    try:
        status = agy_operator._read_status(job_id) or {}
    except Exception:
        return False
    lifecycle = str(status.get("status") or "")
    if lifecycle in {"QUEUED", "RUNNING", "COMPLETED"}:
        return False
    return str(status.get("delivery_status") or "") != "DELIVERED"


def _find_existing_job(token: str) -> str | None:
    matches: list[str] = []
    root = Path(agy_operator.RUN_ROOT)
    if not root.is_dir():
        return None
    for entry in root.iterdir():
        if not entry.is_dir() or not agy_operator._validate_job_id(entry.name):
            continue
        try:
            if token in (entry / "TASK.md").read_text(encoding="utf-8"):
                matches.append(entry.name)
        except OSError:
            continue
    # Skip jobs that ended without delivering anything (e.g. CANCELLED by
    # headless permission auto-denial) so the durable token can be
    # re-submitted through the normal path.
    matches = [m for m in matches if not _job_ended_without_delivery(m)]
    if len(matches) > 1:
        raise RuntimeError(f"duplicate Agy job token found: {token}")
    return matches[0] if matches else None


def _command_model(job_id: str) -> str | None:
    try:
        command = json.loads((Path(agy_operator.RUN_ROOT) / job_id / "COMMAND.json").read_text(encoding="utf-8"))
        argv = command.get("argv") if isinstance(command, dict) else None
        if not isinstance(argv, list):
            return None
        for index, value in enumerate(argv[:-1]):
            if value == "--model":
                return str(argv[index + 1])
    except (OSError, ValueError, TypeError):
        return None
    return None


def _parse_review(raw: str, finding_id: str) -> dict[str, Any]:
    candidates = [raw.strip()]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw or "", flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        candidates.insert(0, fenced.group(1))
    decoder = json.JSONDecoder()
    parsed: dict[str, Any] | None = None
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                parsed = value
                break
        except ValueError:
            pass
        for idx, char in enumerate(candidate):
            if char != "{":
                continue
            try:
                value, _ = decoder.raw_decode(candidate[idx:])
            except ValueError:
                continue
            if isinstance(value, dict):
                parsed = value
                break
        if parsed is not None:
            break
    if not parsed:
        raise RuntimeError(f"Agy returned no JSON Opus review for {finding_id}")
    returned_finding = str(parsed.get("finding_id", finding_id)).strip()
    if returned_finding != finding_id:
        raise RuntimeError("Agy Opus review finding identity mismatch")
    return parsed


def _prompt(finding_id: str, input_path: Path, token: str) -> str:
    return f"""You are the independent Opus semantic reviewer for one existing PAW GPT Brain finding.

Read ONLY the Brain queue item JSON at {input_path}. Do not read WPS raw
sources, Cursor artifacts, Formal State, or unrelated workspace data. Do not
perform GPT self-review, post-Opus consolidation, user decisions, or writes.
Review only finding_id={finding_id}.

Durable dispatch token: {token}
Return exactly one JSON object and no markdown with keys:
finding_id, outcome, reason, review.
The outcome must describe the expert review without changing GPT Brain state;
the dispatcher will pass the review object to the existing one-time Brain
record_opus_review gate. Include evidence-grounded corrections or confirmation
inside review, and preserve uncertainty.
"""


def _cluster_prompt(cluster_id: str, input_path: Path, token: str) -> str:
    return f"""You are the independent Opus semantic reviewer for one PAW GPT Brain
decision cluster. Review the cluster once. Do not impersonate GPT post-Opus
consolidation, do not write Formal State, and do not call CLEAR.

Read ONLY the queue item JSON at {input_path}.
Durable dispatch token: {token}
Review only cluster_id={cluster_id}.

Return exactly one JSON object and no markdown with keys:
cluster_id, finding_id, outcome, reason, review.
finding_id may repeat cluster_id. Preserve uncertainty.
"""


def _incremental_cluster_prompt(cluster_key: str, input_path: Path, token: str) -> str:
    return f"""You are PAW's independent Opus expert reviewer for one versioned incremental cluster.

Read ONLY the supplied cluster JSON at {input_path}; do not read frozen
historical sources, all new Source, Formal State, or unrelated workspace data.
Do not make a user business decision and do not write canonical state.
Review only cluster_key={cluster_key}. Durable one-time token: {token}
Return exactly one JSON object with keys cluster_key, outcome, reason, review.
outcome must be SAFE_TO_APPLY, USER_DECISION_REQUIRED, or
INSUFFICIENT_EVIDENCE. Preserve uncertainty and evidence locators.
"""


def dispatch_incremental_cluster(
    cluster: Mapping[str, Any],
    *,
    jobs_dir: Path | None = None,
) -> dict[str, Any]:
    """Dispatch one incremental cluster through this existing Opus runtime.

    The incremental pipeline owns its durable item state and canonical
    transitions.  This shared dispatcher owns only the Agy lifecycle,
    one-time token recovery, exact-model verification and expert result.
    """
    cluster_key = str(cluster.get("cluster_key") or "").strip()
    if not cluster_key:
        raise RuntimeError("incremental cluster_key is required")
    token = f"PAW_OPUS_INCREMENTAL:{cluster_key}"
    jobs = jobs_dir or default_paths()["jobs"]
    jobs.mkdir(parents=True, exist_ok=True)
    input_path = jobs / ("incremental-" + hash_payload({"cluster_key": cluster_key})[:32] + ".input.json")
    atomic_write_json(input_path, {"cluster_key": cluster_key, "queue_item": dict(cluster), "created_at": utc_now(), "mode": "versioned_incremental"})
    job_id = _find_existing_job(token)
    if job_id and _job_ended_without_delivery(job_id):
        job_id = None
    if not job_id:
        submitted = agy_operator.submit_task(
            _incremental_cluster_prompt(cluster_key, input_path, token),
            title=f"PAW incremental Opus {cluster_key}", model=MODEL, mode=MODE,
            sandbox=False, timeout_seconds=MAX_TIMEOUT_SECONDS, profile="semantic_review",
        )
        if submitted.get("error"):
            return {"status": "FAILED", "error": str(submitted.get("error"))[:1000], "model": MODEL, "mode": "versioned_incremental"}
        job_id = str(submitted.get("job_id") or "")
    status = agy_operator.task_status(job_id)
    lifecycle = str(status.get("status") or "")
    if lifecycle in {"QUEUED", "RUNNING"}:
        return {"status": "RUNNING", "job_id": job_id, "model": MODEL, "mode": "versioned_incremental", "input_path": str(input_path), "token": token}
    if lifecycle != "COMPLETED" or status.get("exit_code") not in (0, None):
        return {"status": "FAILED", "job_id": job_id, "error": f"agy_{lifecycle.lower()}", "model": MODEL, "mode": "versioned_incremental"}
    result = agy_operator.read_result(job_id, max_lines=200)
    command_model = _command_model(job_id)
    result_model = result.get("model")
    status_model = status.get("model")
    check = agy_operator.verify_semantic_review_runtime_models(
        MODE, command_model=command_model, status_model=status_model,
        result_model=result_model, require_all=True,
    )
    if not check.get("ok"):
        return {"status": "FAILED", "job_id": job_id, "error": "opus_effective_model_mismatch", "verification": check, "model": MODEL}
    parsed = _parse_review(result.get("response", ""), cluster_key)
    review = parsed.get("review") if isinstance(parsed.get("review"), Mapping) else parsed
    return {
        "status": "COMPLETED", "job_id": job_id, "model": MODEL,
        "mode": "versioned_incremental", "outcome": parsed.get("outcome"),
        "reason": parsed.get("reason"), "review": dict(review),
        "dispatcher_verification": {"verified": True, "effective_model": MODEL,
            "command_model": command_model, "status_model": status_model,
            "result_model": result_model, "one_time_token": token},
    }


def _queue_items(brain: GPTKnowledgeBrain) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    queue = brain.opus_status()
    if queue.get("effective_model_required") != MODEL:
        raise RuntimeError("Brain Opus model requirement mismatch")
    # A terminal/paused historical queue must not load old findings or bundles.
    # Incremental expert work has its separate, unchanged dispatch entrypoint.
    if str(queue.get("bootstrap_status")) != "ACTIVE":
        return queue, {}, {}
    data = brain._data()  # read-only snapshot; only Brain methods below write
    findings = data.get("findings") or {}
    pending = list((queue.get("opus") or {}).get("pending_items") or [])
    items: dict[str, Any] = {}
    clusters: dict[str, Any] = {}
    known_clusters = {item.get("id"): item for item in pending_opus_clusters()}
    for item_id in pending:
        if str(item_id).startswith("opus-cluster:"):
            cluster = known_clusters.get(item_id)
            if cluster is None:
                store = ReviewBundleStore()
                cluster = (store.load().get("opus_clusters") or {}).get(item_id)
            if not isinstance(cluster, Mapping):
                raise RuntimeError(f"Opus queue references missing cluster: {item_id}")
            clusters[item_id] = dict(cluster)
            continue
        finding = findings.get(item_id)
        if not isinstance(finding, Mapping):
            raise RuntimeError(f"Opus queue references missing finding: {item_id}")
        if finding.get("state") != "EXPERT_REVIEW_REQUIRED":
            raise RuntimeError(f"Opus queue item is not OPUS_REQUIRED: {item_id}")
        items[item_id] = dict(finding)
    return queue, items, clusters


def _dispatch_cluster(brain: GPTKnowledgeBrain, ledger: Ledger, paths: Mapping[str, Path], state: dict[str, Any], cluster_id: str, cluster: Mapping[str, Any]) -> dict[str, Any]:
    token = f"PAW_OPUS_DISPATCH:{cluster_id}"
    entry = dict(state.get("items", {}).get(cluster_id) or {})
    entry.update({"cluster_id": cluster_id, "token": token, "model": MODEL, "mode": MODE, "timeout_seconds": MAX_TIMEOUT_SECONDS})
    input_path = paths["jobs"] / (hash_payload({"cluster_id": cluster_id})[:32] + ".input.json")
    atomic_write_json(input_path, {"cluster_id": cluster_id, "queue_item": dict(cluster), "created_at": utc_now()})
    entry["input_path"] = str(input_path)
    job_id = entry.get("agy_job_id") or _find_existing_job(token)
    if job_id and _job_ended_without_delivery(job_id):
        # Ledger still points at a dead job (e.g. CANCELLED); forget it so the
        # durable token is re-submitted through the normal path below.
        entry["agy_job_id"] = None
        entry["resubmitted_after"] = {"previous_job_id": job_id, "at": utc_now()}
        job_id = None
    if not job_id:
        entry["submission_intent"] = {"token": token, "cluster_id": cluster_id, "at": utc_now()}
        state.setdefault("items", {})[cluster_id] = entry
        state["status"] = "SUBMITTING"
        ledger.save(state)
        job_id = _find_existing_job(token)
        if not job_id:
            result = agy_operator.submit_task(
                _cluster_prompt(cluster_id, input_path, token),
                title=f"PAW Opus semantic review {cluster_id}",
                model=MODEL,
                mode=MODE,
                sandbox=False,
                timeout_seconds=MAX_TIMEOUT_SECONDS,
                profile="semantic_review",
            )
            if result.get("error"):
                raise RuntimeError(f"Agy submission failed: {result.get('error')}")
            job_id = str(result["job_id"])
    entry["agy_job_id"] = job_id
    entry["submission_intent"] = None
    state.setdefault("items", {})[cluster_id] = entry
    state["status"] = "RUNNING"
    ledger.save(state)
    status = agy_operator.task_status(job_id)
    entry["last_status"] = status
    lifecycle = str(status.get("status") or "")
    if lifecycle in {"QUEUED", "RUNNING"}:
        state["items"][cluster_id] = entry
        ledger.save(state)
        return state
    if lifecycle != "COMPLETED" or status.get("exit_code") not in (0, None):
        raise RuntimeError(f"Agy Opus job {job_id} ended {lifecycle}")
    result = agy_operator.read_result(job_id, max_lines=200)
    command_model = _command_model(job_id)
    result_model = result.get("model")
    status_model = status.get("model")
    check = agy_operator.verify_semantic_review_runtime_models(
        "semantic_review",
        command_model=command_model,
        status_model=status_model,
        result_model=result_model,
        require_all=True,
    )
    if not check.get("ok"):
        raise RuntimeError(f"Agy effective model verification failed for {job_id}: {check.get('error')}")
    review = _parse_review(result.get("response", ""), cluster_id)
    review_payload = review.get("review") if isinstance(review.get("review"), Mapping) else review
    review_payload = dict(review_payload)
    review_payload["dispatcher_verification"] = {
        "verified": True,
        "effective_model": MODEL,
        "status_model": status_model,
        "command_model": command_model,
        "result_model": result_model,
        "mode": MODE,
        "timeout_seconds": MAX_TIMEOUT_SECONDS,
        "agy_job_id": job_id,
        "cluster": True,
    }
    recorded = record_opus_cluster_review(cluster_id=cluster_id, effective_model=MODEL, review=review_payload, brain=brain)
    entry["recorded_at"] = utc_now()
    entry["record_status"] = recorded.get("status")
    entry["verified_model"] = MODEL
    state["items"][cluster_id] = entry
    state["last_success"] = {"cluster_id": cluster_id, "agy_job_id": job_id, "at": utc_now()}
    ledger.event(state, "opus_cluster_recorded", cluster_id=cluster_id, agy_job_id=job_id, model=MODEL)
    ledger.save(state)
    return state


def _mark_idle(ledger: Ledger, state: dict[str, Any], *, reason: str, bootstrap_status: Any) -> dict[str, Any]:
    """Persist lifecycle transitions, never an identical idle heartbeat.

    All existing events/items remain intact. No historical audit is rotated or
    pruned here; avoiding timer-only writes requires no recovery assumption.
    """
    idle_reason = {"reason": reason, "bootstrap_status": bootstrap_status}
    if (state.get("status") == "IDLE" and state.get("idle_reason") == idle_reason
            and state.get("last_error") is None and state.get("hard_blocker") is None):
        return state
    state["status"] = "IDLE"
    state["idle_reason"] = idle_reason
    state["last_error"] = None
    state["hard_blocker"] = None
    ledger.event(state, reason, bootstrap_status=bootstrap_status)
    ledger.save(state)
    return state


def _mark_blocked(ledger: Ledger, state: dict[str, Any], error: str) -> dict[str, Any]:
    """Keep the first failure and changed errors, not duplicate poll events."""
    error = error[:1000]
    if (state.get("status") == "BLOCKED" and state.get("hard_blocker") == error
            and state.get("last_error") == error):
        return state
    state["status"] = "BLOCKED"
    state["hard_blocker"] = error
    state["last_error"] = error
    ledger.event(state, "hard_blocker", error=error)
    ledger.save(state)
    return state


def dispatch_once(brain: GPTKnowledgeBrain, ledger: Ledger, paths: Mapping[str, Path], *, poll_seconds: float) -> dict[str, Any]:
    queue, items, clusters = _queue_items(brain)
    state = ledger.load()
    if str(queue.get("bootstrap_status")) != "ACTIVE":
        return _mark_idle(ledger, state, reason="queue_not_active", bootstrap_status=queue.get("bootstrap_status"))
    if not items and not clusters:
        return _mark_idle(ledger, state, reason="queue_empty", bootstrap_status=queue.get("bootstrap_status"))
    # Historical bootstrap can contain hundreds of independent expert
    # clusters.  Keep the dispatcher bounded on the 2GB host: one external
    # Opus/Agy job per patrol cycle, with the remainder staying durable in the
    # Brain queue.  This is also the serialisation boundary for incremental
    # versioned clusters.
    if clusters:
        cluster_id, cluster = next(iter(clusters.items()))
        return _dispatch_cluster(brain, ledger, paths, state, cluster_id, cluster)
    if items:
        finding_id, finding = next(iter(items.items()))
        items = {finding_id: finding}
    paths["jobs"].mkdir(parents=True, exist_ok=True)
    for cluster_id, cluster in clusters.items():
        state = _dispatch_cluster(brain, ledger, paths, state, cluster_id, cluster)
    for finding_id, finding in items.items():
        token = f"PAW_OPUS_DISPATCH:{finding_id}"
        entry = dict(state.get("items", {}).get(finding_id) or {})
        entry.update({"finding_id": finding_id, "token": token, "model": MODEL, "mode": MODE, "timeout_seconds": MAX_TIMEOUT_SECONDS})
        input_path = paths["jobs"] / (hash_payload({"finding_id": finding_id})[:32] + ".input.json")
        atomic_write_json(input_path, {"finding_id": finding_id, "queue_item": finding, "created_at": utc_now()})
        entry["input_path"] = str(input_path)
        job_id = entry.get("agy_job_id") or _find_existing_job(token)
        if job_id and _job_ended_without_delivery(job_id):
            entry["agy_job_id"] = None
            entry["resubmitted_after"] = {"previous_job_id": job_id, "at": utc_now()}
            job_id = None
        if job_id:
            entry["agy_job_id"] = job_id
        else:
            # Durable intent closes the crash window between Agy submission
            # and finding_id -> agy_job_id persistence. Recovery scans TASK.md
            # for the stable token before submitting anything new.
            entry["submission_intent"] = {"token": token, "finding_id": finding_id, "at": utc_now()}
            state.setdefault("items", {})[finding_id] = entry
            state["status"] = "SUBMITTING"
            ledger.save(state)
            existing = _find_existing_job(token)
            if existing:
                job_id = existing
            else:
                result = agy_operator.submit_task(
                    _prompt(finding_id, input_path, token),
                    title=f"PAW Opus semantic review {finding_id}",
                    model=MODEL,
                    mode=MODE,
                    sandbox=False,
                    timeout_seconds=MAX_TIMEOUT_SECONDS,
                    profile="semantic_review",
                )
                if result.get("error"):
                    raise RuntimeError(f"Agy submission failed: {result.get('error')}")
                job_id = str(result["job_id"])
            entry["agy_job_id"] = job_id
        entry["submission_intent"] = None
        state.setdefault("items", {})[finding_id] = entry
        state["status"] = "RUNNING"
        ledger.save(state)

        status = agy_operator.task_status(job_id)
        entry["last_status"] = status
        state["items"][finding_id] = entry
        ledger.save(state)
        lifecycle = str(status.get("status") or "")
        if lifecycle in {"QUEUED", "RUNNING"}:
            continue
        if lifecycle != "COMPLETED" or status.get("exit_code") not in (0, None):
            raise RuntimeError(f"Agy Opus job {job_id} ended {lifecycle}")

        result = agy_operator.read_result(job_id, max_lines=200)
        command_model = _command_model(job_id)
        result_model = result.get("model")
        status_model = status.get("model")
        check = agy_operator.verify_semantic_review_runtime_models(
            "semantic_review",
            command_model=command_model,
            status_model=status_model,
            result_model=result_model,
            require_all=True,
        )
        if not check.get("ok"):
            raise RuntimeError(f"Agy effective model verification failed for {job_id}: {check.get('error')}")
        review = _parse_review(result.get("response", ""), finding_id)
        review_payload = review.get("review") if isinstance(review.get("review"), Mapping) else review
        review_payload = dict(review_payload)
        review_payload["dispatcher_verification"] = {
            "verified": True,
            "effective_model": MODEL,
            "status_model": status_model,
            "command_model": command_model,
            "result_model": result_model,
            "mode": MODE,
            "timeout_seconds": MAX_TIMEOUT_SECONDS,
            "agy_job_id": job_id,
        }
        recorded = brain.record_opus_review(finding_id=finding_id, effective_model=MODEL, review=review_payload)
        entry["recorded_at"] = utc_now()
        entry["record_status"] = recorded.get("state")
        entry["verified_model"] = MODEL
        state["items"][finding_id] = entry
        state["last_success"] = {"finding_id": finding_id, "agy_job_id": job_id, "at": utc_now()}
        state["status"] = "RUNNING"
        ledger.event(state, "opus_recorded", finding_id=finding_id, agy_job_id=job_id, model=MODEL)
        ledger.save(state)
    return state


def run_forever(*, poll_seconds: float = DEFAULT_POLL_SECONDS, once: bool = False) -> int:
    paths = default_paths()
    ledger = Ledger(paths["state"])
    state = ledger.load()
    lock = FileLeaseLock(paths["lock"], stale_seconds=900)
    lock.acquire(run_id=str(state.get("run_id") or uuid.uuid4()), actor=ACTOR)
    try:
        state["status"] = "RUNNING"
        state["started_at"] = state.get("started_at") or utc_now()
        state["last_error"] = None
        ledger.event(state, "service_started", model=MODEL, mode=MODE, timeout_seconds=MAX_TIMEOUT_SECONDS)
        ledger.save(state)
        brain = GPTKnowledgeBrain(store=AtomicJsonStore(paths["brain"]))
        while True:
            try:
                state = dispatch_once(brain, ledger, paths, poll_seconds=poll_seconds)
                if state.get("hard_blocker") is not None:
                    state["hard_blocker"] = None
                    ledger.save(state)
            except Exception as exc:
                state = ledger.load()
                state = _mark_blocked(ledger, state, str(exc))
            if once:
                return 1 if state.get("status") == "BLOCKED" else 0
            time.sleep(max(1.0, poll_seconds))
    finally:
        lock.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    args = parser.parse_args(argv)
    paths = default_paths()
    if args.status:
        brain = GPTKnowledgeBrain(store=AtomicJsonStore(paths["brain"]))
        print(json.dumps({"dispatcher": Ledger(paths["state"]).load(), "queue": brain.opus_status(), "model": MODEL, "mode": MODE, "timeout_seconds": MAX_TIMEOUT_SECONDS}, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.once:
        return run_forever(poll_seconds=args.poll_seconds, once=True)
    run_forever(poll_seconds=args.poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
