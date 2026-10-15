"""Capture Auto Analysis Orchestration Service — Phase D2.

Quick Capture → save → background analysis → analysis_run → Candidate(pending)
→ Pending Review.  Automatic analysis is NOT automatic canonical writing.

Responsibilities (analyze_capture):
  1. load Capture
  2. fingerprint
  3. idempotency (in-flight / already-analyzed)
  4. create analysis_run(status=running)
  5. two_pass_analysis (workspace_extract = read-only boundary)
  6. persist safe Candidate(s) via the candidate repository (status=pending)
  7. complete analysis_run
  8. mark capture processed (ONLY after success)

Failure semantics:
  - analysis_run → failed (safe error metadata, no secrets / CoT)
  - capture.processed_at stays NULL → durable recovery re-picks it

Durability:
  - Immediate trigger: FastAPI BackgroundTasks (after capture commit / response)
  - Durable signal: capture.processed_at IS NULL (+ analysis_run fingerprint)
  - Recovery: startup scan + low-frequency in-process loop (bounded batch,
    fixed activation boundary — post-activation captures are always recoverable,
    pre-activation backlog is never bulk-analyzed). No new tables.

Safety:
  - Never calls accept/reject/apply; never writes Todo/Project/Knowledge.
  - AUTO_WRITE_ENABLED stays FALSE; engine stays PERSIST_CANDIDATES=False
    (the engine never persists; this service persists explicitly).
"""

from __future__ import annotations

from pathlib import Path


import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

PRODUCTION_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))

# Feature flag: PAW_CAPTURE_AUTO_ANALYSIS. Production default is OFF.
# Quick Capture save no longer schedules this service. Isolated tests/calibration
# may still call analyze_capture() directly after setting the flag.
FLAG_ENV = "PAW_CAPTURE_AUTO_ANALYSIS"

# A 'running' analysis_run older than this is treated as abandoned (the
# process died mid-run) and may be retried.
RUNNING_STALE_SECONDS = 600

# ── Fixed activation boundary (V2 final semantics) ────────────────────────
# Recovery eligibility is NOT a rolling window: any capture created at or
# after the auto-analysis activation moment stays recoverable forever
# (processed_at IS NULL) — 10 minutes, 8 hours, 48 hours or 3 days later.
# Captures created BEFORE the boundary are legacy backlog and are never
# bulk-analyzed by recovery.
FLAG_ACTIVATED_AT_ENV = "PAW_CAPTURE_AUTO_ANALYSIS_ACTIVATED_AT"
# First official production enablement of AGNES_CAPTURE_AUTO_ANALYSIS_V1:
# paw-actions PID 2724166 startup, 2026-08-19T08:16:54+00:00.
DEFAULT_ACTIVATION_AT = "2026-08-19T08:16:54+00:00"

RECOVERY_INTERVAL_SECONDS = 300
RECOVERY_STARTUP_DELAY_SECONDS = 5
RECOVERY_BATCH = 3

# Bound concurrent model calls (BackgroundTask + recovery share this).
MAX_CONCURRENT_MODEL_CALLS = 2

_capture_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_model_semaphore = threading.Semaphore(MAX_CONCURRENT_MODEL_CALLS)
_enabled_cache: Optional[bool] = None


def _parse_utc(value: str) -> Optional[datetime]:
    """Parse an ISO datetime, normalizing to tz-aware UTC (naive = UTC)."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except (ValueError, AttributeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# Computed once at process start from env (fallback: in-code constant).
# NEVER regenerated from now() — restarting must not move the boundary.
ACTIVATION_BOUNDARY: Optional[datetime] = _parse_utc(
    os.getenv(FLAG_ACTIVATED_AT_ENV) or DEFAULT_ACTIVATION_AT
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_ts() -> float:
    return time.time()


def enabled() -> bool:
    """PAW_CAPTURE_AUTO_ANALYSIS flag (production default OFF). Cached per process."""
    global _enabled_cache
    if _enabled_cache is None:
        value = os.getenv(FLAG_ENV, "0")
        _enabled_cache = value.strip().lower() not in {"0", "false", "no", "off", ""}
    return _enabled_cache


def _capture_lock(capture_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _capture_locks.get(capture_id)
        if lock is None:
            lock = threading.Lock()
            _capture_locks[capture_id] = lock
        return lock


def _parse_refs_meta(raw_input_refs: Any) -> dict:
    """analysis_run.raw_input_refs is either a plain refs list (legacy) or a
    {refs, fingerprint, status, error} meta dict."""
    if not raw_input_refs:
        return {"refs": [], "fingerprint": None, "status": None, "error": None}
    try:
        data = json.loads(raw_input_refs) if isinstance(raw_input_refs, str) else raw_input_refs
    except (json.JSONDecodeError, TypeError):
        return {"refs": [], "fingerprint": None, "status": None, "error": None}
    if isinstance(data, dict):
        refs = data.get("refs") or []
        return {
            "refs": refs if isinstance(refs, list) else [refs],
            "fingerprint": data.get("fingerprint"),
            "status": data.get("status"),
            "error": data.get("error"),
        }
    if isinstance(data, list):
        return {"refs": data, "fingerprint": None, "status": None, "error": None}
    return {"refs": [], "fingerprint": None, "status": None, "error": None}


def _analysis_runs_for_capture(capture_id: str, db_path: str) -> list[dict]:
    """All analysis_run rows whose refs include capture_id, newest first."""
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.execute("PRAGMA query_only=ON")
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT id, started_at, completed_at, raw_input_refs FROM analysis_run "
                "ORDER BY started_at DESC"
            ).fetchall()
        finally:
            con.close()
    except Exception:
        return []
    result = []
    for row in rows:
        meta = _parse_refs_meta(row["raw_input_refs"])
        if capture_id not in meta["refs"]:
            continue
        started_ts = 0.0
        try:
            started_ts = datetime.fromisoformat(str(row["started_at"])).timestamp()
        except (ValueError, TypeError):
            pass
        result.append({
            "analysis_id": row["id"],
            "started_at": row["started_at"],
            "started_ts": started_ts,
            "completed_at": row["completed_at"],
            "status": str(meta.get("status") or ("completed" if row["completed_at"] else "running")).lower(),
            "error": meta["error"],
            "fingerprint": meta["fingerprint"],
        })
    return result


def _classify_error(message: str) -> dict:
    """Safe error metadata — never secrets, prompts, CoT or raw reasoning."""
    compact = (message or "").lower()
    if "timeout" in compact:
        return {"code": "timeout", "message": "Agnes 分析超时"}
    if "openclaw" in compact or "runtime_unavailable" in compact:
        return {"code": "runtime_unavailable", "message": "Agnes 运行时不可用"}
    if "adapter_error" in compact:
        return {"code": "runtime_unavailable", "message": "Agnes 运行时不可用"}
    if "not found" in compact or "capture_not_found" in compact:
        return {"code": "capture_not_found", "message": "记录不存在"}
    return {"code": "analysis_error", "message": "分析失败，可稍后自动重试"}


def _proposed_change_for(candidate_type: str, item: Any) -> dict:
    """Map a CandidateItem to a safe proposed_change payload (apply-ready for todo)."""
    proposed = getattr(item, "proposed_change", None)
    if isinstance(proposed, dict):
        return proposed
    statement = str(getattr(item, "statement", "") or "")
    if candidate_type == "todo":
        return {"title": statement, "description": None}
    return {"statement": statement}


def _value(item: Any) -> Any:
    """Unwrap enum-like values (Confidence/InferenceLevel/WriteAction)."""
    return getattr(item, "value", item)


def _persist_candidates(db_path: str, analysis_run_id: str, output: Any, capture_id: str) -> list[str]:
    """Persist safe Candidate drafts (status=pending) via the sealed repository.

    Explicit persistence — the engine itself never persists
    (PERSIST_CANDIDATES=False). Duplicate candidates are skipped by the
    repository's own fingerprint dedup.
    """
    from brain.analysis.engine import DEFAULT_MODEL as _DEFAULT_MODEL
    from brain.candidates import repository as cand_repo

    groups = [
        (getattr(output, "todo_candidates", []), "todo"),
        (getattr(output, "project_update_candidates", []), "project_update"),
        (getattr(output, "knowledge_candidates", []), "knowledge"),
        (getattr(output, "conflicts", []), "conflict"),
        (getattr(output, "ambiguities", []), "ambiguity"),
    ]
    persisted: list[str] = []
    for items, ctype in groups:
        for item in items:
            result = cand_repo.create_candidate(
                db_path=db_path,
                analysis_run_id=analysis_run_id,
                candidate_type=ctype,
                source_refs=[capture_id],
                proposed_change=_proposed_change_for(ctype, item),
                confidence=str(_value(getattr(item, "confidence", None)) or "LOW"),
                inference_level=str(_value(getattr(item, "inference_level", None)) or "WEAK_INFERENCE"),
                recommended_action=str(_value(getattr(item, "recommended_action", None)) or "DRAFT_ONLY"),
                analysis_model=_DEFAULT_MODEL,
            )
            if isinstance(result, dict) and result.get("id"):
                persisted.append(result["id"])
    return persisted


def _mark_capture_processed(capture_id: str, db_path: str) -> None:
    """Mark capture processed with an explicit db_path (testable)."""
    if db_path == PRODUCTION_DB:
        from workspace_api.repository import mark_capture_processed as _mark
        _mark(capture_id)
        return
    import sqlite3
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "UPDATE capture SET processed_at = ?, updated_at = ? WHERE id = ? AND processed_at IS NULL",
            (_now_iso(), _now_iso(), capture_id),
        )
        con.commit()
    finally:
        con.close()


def analyze_capture(capture_id: str, db_path: Optional[str] = None) -> dict:
    """Analyze a single capture (idempotent). Safe under same-process
    concurrency (BackgroundTask vs recovery scan)."""
    db_path = db_path or PRODUCTION_DB
    with _capture_lock(capture_id):
        return _analyze_capture_locked(capture_id, db_path)


def _analyze_capture_locked(capture_id: str, db_path: str) -> dict:
    if not enabled():
        return {"status": "disabled", "capture_id": capture_id}

    from brain.analysis import repository as repo
    from brain.analysis import engine as engine

    capture = repo.get_capture(capture_id, db_path)
    if not capture:
        return {"status": "error", "capture_id": capture_id, "error_code": "capture_not_found"}
    if capture.get("processed_at"):
        return {"status": "skipped", "reason": "already_processed", "capture_id": capture_id}

    fingerprint = repo.compute_capture_fingerprint(capture)
    existing = _analysis_runs_for_capture(capture_id, db_path)
    now = _now_ts()

    for run in existing:
        if run["status"].upper() == "COMPLETED" and not run.get("error"):
            # Reconcile: analysis succeeded but processed_at never set (crash
            # between complete_analysis_run and mark_processed).
            if not capture.get("processed_at"):
                _mark_capture_processed(capture_id, db_path)
            return {"status": "skipped", "reason": "already_analyzed", "analysis_id": run["analysis_id"], "capture_id": capture_id}
    for run in existing:
        if run["status"].upper() == "RUNNING" and (now - run["started_ts"]) < RUNNING_STALE_SECONDS:
            return {"status": "skipped", "reason": "in_flight", "capture_id": capture_id}
    # Stale 'running' (process died mid-run) → mark failed, allow retry.
    for run in existing:
        if run["status"].upper() == "RUNNING":
            repo.complete_analysis_run(
                db_path, run["analysis_id"], _now_iso(),
                status="failed", error=json.dumps({"code": "stale_running", "message": "进程中断，已标记失败并允许重试"}),
            )

    analysis_id = str(uuid.uuid4())
    try:
        repo.insert_analysis_run(
            db_path=db_path,
            analysis_id=analysis_id,
            trigger_type="capture",
            raw_text=capture.get("raw_text"),
            raw_input_refs=[capture_id],
            analysis_model=engine.DEFAULT_MODEL,
            started_at=_now_iso(),
            fingerprint=fingerprint,
            status="running",
        )
    except Exception as exc:  # persistence failure — never crash the caller
        return {"status": "error", "capture_id": capture_id, "error_code": "persistence_error", "message": str(exc)[:200]}

    try:
        with _model_semaphore:
            output, warnings = engine.two_pass_analysis(
                capture.get("raw_text") or "",
                context={"trigger_type": "capture", "source_refs": [capture_id]},
                analysis_id=analysis_id,
                raise_on_model_error=True,
            )
        candidates = _persist_candidates(db_path, analysis_id, output, capture_id)
        outcome = "CANDIDATE_CREATED" if candidates else "NO_ACTION"
        repo.complete_analysis_run(db_path, analysis_id, _now_iso(), status="completed", outcome=outcome)
        _mark_capture_processed(capture_id, db_path)
        return {
            "status": "completed",
            "capture_id": capture_id,
            "analysis_id": analysis_id,
            "candidate_count": len(candidates),
            "candidate_ids": candidates,
            "warnings": warnings,
        }
    except engine.AnalysisRuntimeError as exc:
        error_meta = _classify_error(str(exc))
        repo.complete_analysis_run(db_path, analysis_id, _now_iso(), status="failed", error=json.dumps(error_meta, ensure_ascii=False), outcome="FAILED")
        return {
            "status": "failed",
            "capture_id": capture_id,
            "analysis_id": analysis_id,
            "error_code": error_meta["code"],
            "error_message": error_meta["message"],
        }
    except Exception as exc:  # defensive — any unexpected failure stays retryable
        error_meta = _classify_error(str(exc))
        try:
            repo.complete_analysis_run(db_path, analysis_id, _now_iso(), status="failed", error=json.dumps(error_meta, ensure_ascii=False), outcome="FAILED")
        except Exception:
            pass
        return {
            "status": "failed",
            "capture_id": capture_id,
            "analysis_id": analysis_id,
            "error_code": error_meta["code"],
            "error_message": error_meta["message"],
        }


def is_eligible_for_recovery(capture: dict) -> bool:
    """Fixed-boundary eligibility (V2 final semantics).

    A capture created at/after the auto-analysis activation boundary is
    recoverable for as long as processed_at IS NULL — 10 minutes, 8 hours,
    48 hours or 3 days later. Pre-activation captures are legacy backlog and
    are never auto-analyzed here.
    """
    if ACTIVATION_BOUNDARY is None:
        return False
    created = capture.get("created_at") or capture.get("captured_at") or ""
    created_dt = _parse_utc(created)
    if created_dt is None:
        return False  # unparseable dates are skipped (never bulk-analyzed)
    return created_dt >= ACTIVATION_BOUNDARY


def run_recovery_scan(limit: int = RECOVERY_BATCH,
                      db_path: Optional[str] = None) -> dict:
    """Durable recovery: re-analyze unprocessed captures created at/after the
    FIXED activation boundary (lost BackgroundTasks, failed runs, crashes).
    Bounded batch; age does not affect eligibility.

    The pre-activation legacy backlog is intentionally NOT bulk analyzed and
    stays listed via get_unprocessed_captures for a later decision.
    """
    db_path = db_path or PRODUCTION_DB
    if not enabled():
        return {"enabled": False}
    from brain.analysis import repository as repo
    captures = repo.get_unprocessed_captures(limit=limit * 4, db_path=db_path)
    scanned = 0
    analyzed = 0
    results = []
    for capture in captures:
        if not is_eligible_for_recovery(capture):
            continue  # pre-activation legacy backlog — never auto-analyzed
        scanned += 1
        result = analyze_capture(capture["id"], db_path=db_path)
        results.append(result)
        if result.get("status", "").upper() == "COMPLETED":
            analyzed += 1
        if analyzed >= limit:
            break
    return {"enabled": True, "scanned": scanned, "analyzed": analyzed, "results": results}


def start_recovery_loop() -> None:
    """Disabled: GPT Brain patrol is the sole Capture semantic consumer.

    Isolated tests may still call analyze_capture()/run_recovery_scan()
    directly. Production must not auto-invoke Mistral on unprocessed Captures.
    """
    return


async def _recovery_loop() -> None:
    import asyncio
    await asyncio.sleep(RECOVERY_STARTUP_DELAY_SECONDS)
    await _run_scan_async()
    while True:
        await asyncio.sleep(RECOVERY_INTERVAL_SECONDS)
        await _run_scan_async()


async def _run_scan_async() -> None:
    import asyncio
    try:
        await asyncio.to_thread(run_recovery_scan)
    except Exception:
        # Recovery must never take the app down.
        pass
