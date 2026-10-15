#!/usr/bin/env python3
"""Raw Quick Capture enqueue for the next GPT Patrol.

Save-time contract:
  - Persist exact raw Capture first (caller).
  - Register capture ID only (no raw-text copy, no LLM, no Formal State).
  - Never consult generic patrol_enabled; queueing is ingest infrastructure.
  - If the canonical OPEN batch cannot be written (permission / outage), keep a
    durable sidecar next to workspace.db so processed_at IS NULL remains the
    recoverability source of truth.

Semantic analysis is owned exclusively by the next ChatGPT GPT Agent scheduled
patrol. This module never marks processed_at.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

KIND_CAPTURE = "capture"
SIDECAR_SCHEMA = "paw-capture-patrol-enqueue/1"
QUEUED_FOR_PATROL = "QUEUED_FOR_PATROL"
IDEMPOTENT = "IDEMPOTENT"
SIDECAR_QUEUED = "SIDECAR_QUEUED"
QUEUE_UNAVAILABLE = "QUEUE_UNAVAILABLE"
ALREADY_PROCESSED = "ALREADY_PROCESSED"
FORMAL_STATE_BYPASS = 0

DEFAULT_SIDECAR = Path("/var/lib/personal-workspace/paw_capture_patrol_enqueue.json")
DEFAULT_DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))

_SIDECAR_THREAD = threading.RLock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sidecar_path(path: str | Path | None = None) -> Path:
    if path:
        return Path(path)
    env = os.environ.get("PAW_CAPTURE_PATROL_SIDECAR")
    if env:
        return Path(env)
    return DEFAULT_SIDECAR


def _blank_sidecar() -> dict[str, Any]:
    return {
        "schema": SIDECAR_SCHEMA,
        "items": {},
        "semantic_processing": False,
        "formal_state_bypass": FORMAL_STATE_BYPASS,
        "updated_at": utc_now(),
    }


def _load_sidecar_unlocked(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return _blank_sidecar()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _blank_sidecar()
    if not isinstance(data, dict):
        return _blank_sidecar()
    data.setdefault("schema", SIDECAR_SCHEMA)
    data.setdefault("items", {})
    data["semantic_processing"] = False
    data["formal_state_bypass"] = FORMAL_STATE_BYPASS
    if not isinstance(data.get("items"), dict):
        data["items"] = {}
    return data


def _save_sidecar_unlocked(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload["updated_at"] = utc_now()
    payload["semantic_processing"] = False
    payload["formal_state_bypass"] = FORMAL_STATE_BYPASS
    existing_stat = path.stat() if path.exists() else None
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o660)
    try:
        if existing_stat is not None:
            os.chown(tmp, existing_stat.st_uid, existing_stat.st_gid)
        elif os.geteuid() == 0:
            import grp
            import pwd
            os.chown(tmp, pwd.getpwnam("pawops").pw_uid, grp.getgrnam("pawdev").gr_gid)
    except OSError:
        pass
    tmp.replace(path)


def mutate_sidecar(path: str | Path | None = None):
    """Exclusive sidecar mutation. Yields the JSON object."""
    target = sidecar_path(path)
    lock_path = Path(str(target) + ".lock")

    class _Ctx:
        def __enter__(self_inner):
            _SIDECAR_THREAD.acquire()
            self_inner._fh = None
            try:
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                if fcntl is not None:
                    self_inner._fh = open(lock_path, "a+", encoding="utf-8")
                    fcntl.flock(self_inner._fh.fileno(), fcntl.LOCK_EX)
                self_inner.data = _load_sidecar_unlocked(target)
                return self_inner.data
            except Exception:
                if self_inner._fh is not None:
                    try:
                        fcntl.flock(self_inner._fh.fileno(), fcntl.LOCK_UN)
                    except Exception:
                        pass
                    self_inner._fh.close()
                _SIDECAR_THREAD.release()
                raise

        def __exit__(self_inner, exc_type, exc, tb):
            try:
                if exc_type is None:
                    _save_sidecar_unlocked(target, self_inner.data)
            finally:
                if self_inner._fh is not None:
                    try:
                        if fcntl is not None:
                            fcntl.flock(self_inner._fh.fileno(), fcntl.LOCK_UN)
                    except Exception:
                        pass
                    self_inner._fh.close()
                _SIDECAR_THREAD.release()
            return False

    return _Ctx()


def sidecar_put(capture_id: str, *, path: str | Path | None = None, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    cid = str(capture_id or "").strip()
    if not cid:
        return {"status": "error", "error": "capture_id_required", "queued": False, "formal_state_bypass": FORMAL_STATE_BYPASS}
    with mutate_sidecar(path) as data:
        items = data.setdefault("items", {})
        existing = items.get(cid) if isinstance(items.get(cid), dict) else None
        if existing and existing.get("kind") == KIND_CAPTURE:
            existing["semantic_processed"] = False
            return {
                "status": IDEMPOTENT,
                "queued": True,
                "kind": KIND_CAPTURE,
                "ref_id": cid,
                "sidecar": True,
                "semantic_processing": False,
                "formal_state_bypass": FORMAL_STATE_BYPASS,
            }
        item = {
            "kind": KIND_CAPTURE,
            "ref_id": cid,
            "payload_ref": "capture:" + cid,
            "ingested_at": utc_now(),
            "semantic_processed": False,
            "merged_into_batch": False,
            "formal_state_bypass": FORMAL_STATE_BYPASS,
        }
        if extra:
            item["extra"] = dict(extra)
        items[cid] = item
    return {
        "status": SIDECAR_QUEUED,
        "queued": True,
        "kind": KIND_CAPTURE,
        "ref_id": cid,
        "sidecar": True,
        "semantic_processing": False,
        "todo_written": False,
        "knowledge_written": False,
        "project_written": False,
        "formal_state_bypass": FORMAL_STATE_BYPASS,
    }


def sidecar_items(*, path: str | Path | None = None) -> list[dict[str, Any]]:
    data = _load_sidecar_unlocked(sidecar_path(path))
    out = []
    for raw in (data.get("items") or {}).values():
        if not isinstance(raw, dict):
            continue
        if str(raw.get("kind") or KIND_CAPTURE) != KIND_CAPTURE:
            continue
        ref = str(raw.get("ref_id") or "").strip()
        if ref:
            out.append(dict(raw))
    out.sort(key=lambda item: str(item.get("ingested_at") or ""))
    return out


def sidecar_unmerged_ids(*, path: str | Path | None = None) -> list[str]:
    return [
        str(item["ref_id"])
        for item in sidecar_items(path=path)
        if not item.get("merged_into_batch")
    ]


def sidecar_mark_merged(capture_ids: list[str], *, path: str | Path | None = None) -> None:
    wanted = {str(x).strip() for x in capture_ids if str(x).strip()}
    if not wanted:
        return
    with mutate_sidecar(path) as data:
        items = data.setdefault("items", {})
        now = utc_now()
        for cid in wanted:
            row = items.get(cid)
            if isinstance(row, dict):
                row["merged_into_batch"] = True
                row["merged_at"] = now


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def unprocessed_capture_ids(db_path: str) -> list[str]:
    if not db_path or not Path(db_path).is_file():
        return []
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id FROM capture WHERE processed_at IS NULL ORDER BY captured_at ASC"
        ).fetchall()
        return [str(row["id"]) for row in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def capture_processed_at(db_path: str, capture_id: str) -> str | None:
    if not db_path or not Path(db_path).is_file():
        return None
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT processed_at FROM capture WHERE id = ?",
            (capture_id,),
        ).fetchone()
        if not row:
            return None
        return row["processed_at"]
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def _patrol_store(store_path: str | Path | None = None) -> Path:
    if store_path:
        return Path(store_path)
    env = os.environ.get("PAW_PATROL_BATCH_STORE")
    if env:
        return Path(env)
    worker = (
        os.environ.get("PAW_KSHEET_WORKER_ROOT")
        or os.environ.get("PAW_RUNTIME_MODULE_ROOT")
)
    if not worker:
        raise RuntimeError(
            "PAW_RUNTIME_MODULE_ROOT (or legacy PAW_KSHEET_WORKER_ROOT) is required "
            "(PAW Runtime Path Contract, /etc/paw-runtime-paths.env)"
        )
    if worker not in sys.path:
        sys.path.insert(0, worker)
    from paw_patrol_orchestrator import default_store_path
    return default_store_path()


def _enqueue_canonical(
    capture_id: str,
    *,
    db_path: str,
    store_path: Path,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    worker = (
        os.environ.get("PAW_KSHEET_WORKER_ROOT")
        or os.environ.get("PAW_RUNTIME_MODULE_ROOT")
)
    if not worker:
        raise RuntimeError(
            "PAW_RUNTIME_MODULE_ROOT (or legacy PAW_KSHEET_WORKER_ROOT) is required "
            "(PAW Runtime Path Contract, /etc/paw-runtime-paths.env)"
        )
    if worker not in sys.path:
        sys.path.insert(0, worker)
    runtime = "/opt/personal-workspace/brain_runtime"
    if runtime not in sys.path:
        sys.path.append(runtime)
    from paw_patrol_orchestrator import PatrolOrchestrator
    orch = PatrolOrchestrator(store_path, db_path=db_path)
    return orch.enqueue_capture(
        capture_id,
        payload_ref="capture:" + capture_id,
        extra=extra,
    )


def enqueue_saved_capture(
    capture_id: str,
    *,
    db_path: str | None = None,
    store_path: str | Path | None = None,
    sidecar: str | Path | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Register one saved Capture for the next GPT Patrol. Never analyzes."""
    cid = str(capture_id or "").strip()
    db = db_path or os.environ.get("PAW_WORKSPACE_DB") or DEFAULT_DB
    if not cid:
        return {
            "status": "error",
            "error": "capture_id_required",
            "queued": False,
            "semantic_processing": False,
            "formal_state_bypass": FORMAL_STATE_BYPASS,
        }
    processed = capture_processed_at(db, cid) if Path(db).is_file() else None
    if processed:
        return {
            "status": ALREADY_PROCESSED,
            "capture_id": cid,
            "queued": False,
            "processed_at": processed,
            "semantic_processing": False,
            "formal_state_bypass": FORMAL_STATE_BYPASS,
        }
    side = sidecar_put(cid, path=sidecar, extra=extra)
    canonical: dict[str, Any] | None = None
    canonical_error = None
    try:
        canonical = _enqueue_canonical(cid, db_path=db, store_path=_patrol_store(store_path), extra=extra)
        if canonical.get("status") in {QUEUED_FOR_PATROL, IDEMPOTENT}:
            sidecar_mark_merged([cid], path=sidecar)
    except Exception as exc:
        canonical_error = str(exc)[:300]
    result = {
        "status": (canonical or {}).get("status") or side.get("status") or SIDECAR_QUEUED,
        "capture_id": cid,
        "kind": KIND_CAPTURE,
        "ref_id": cid,
        "queued": True,
        "sidecar": True,
        "canonical_batch": bool(canonical and canonical.get("status") in {QUEUED_FOR_PATROL, IDEMPOTENT}),
        "batch_id": (canonical or {}).get("batch_id"),
        "semantic_processing": False,
        "semantic_processing_on_ingest": False,
        "todo_written": False,
        "knowledge_written": False,
        "project_written": False,
        "processed_at": None,
        "generic_patrol_flag_consulted": False,
        "formal_state_bypass": FORMAL_STATE_BYPASS,
    }
    if canonical_error:
        result["canonical_error"] = canonical_error
        result["status"] = SIDECAR_QUEUED
        result["recovery"] = "processed_at_is_null_and_sidecar"
    return result


def best_effort_enqueue_saved_capture(
    capture_id: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Never raises. Capture persistence is the caller's responsibility."""
    try:
        return enqueue_saved_capture(capture_id, **kwargs)
    except Exception as exc:
        try:
            sidecar_put(str(capture_id or "").strip(), path=kwargs.get("sidecar"))
        except Exception:
            pass
        return {
            "status": QUEUE_UNAVAILABLE,
            "capture_id": capture_id,
            "queued": False,
            "error": str(exc)[:300],
            "semantic_processing": False,
            "recovery": "processed_at_is_null_reconcile",
            "formal_state_bypass": FORMAL_STATE_BYPASS,
        }


def reconcile_unprocessed_captures(
    *,
    db_path: str | None = None,
    store_path: str | Path | None = None,
    sidecar: str | Path | None = None,
) -> dict[str, Any]:
    """Restore missing OPEN-batch capture refs from processed_at IS NULL + sidecar."""
    db = db_path or os.environ.get("PAW_WORKSPACE_DB") or DEFAULT_DB
    waiting = unprocessed_capture_ids(db)
    side_ids = [item["ref_id"] for item in sidecar_items(path=sidecar)]
    ordered: list[str] = []
    seen: set[str] = set()
    for cid in waiting + side_ids:
        if cid in seen:
            continue
        seen.add(cid)
        ordered.append(cid)
    results: list[dict[str, Any]] = []
    skipped_processed: list[str] = []
    for cid in ordered:
        processed = capture_processed_at(db, cid)
        if processed:
            skipped_processed.append(cid)
            sidecar_mark_merged([cid], path=sidecar)
            continue
        results.append(
            enqueue_saved_capture(
                cid,
                db_path=db,
                store_path=store_path,
                sidecar=sidecar,
            )
        )
    enqueued = [r["capture_id"] for r in results if r.get("status") == QUEUED_FOR_PATROL]
    idempotent = [r["capture_id"] for r in results if r.get("status") in {IDEMPOTENT, SIDECAR_QUEUED} and r.get("queued")]
    failed = [r["capture_id"] for r in results if not r.get("queued")]
    return {
        "status": "ok",
        "unprocessed_count": len(waiting),
        "sidecar_count": len(side_ids),
        "enqueued": enqueued,
        "idempotent": idempotent,
        "failed": failed,
        "skipped_processed": skipped_processed,
        "semantic_processing": False,
        "processed_at_marked": False,
        "generic_patrol_flag_consulted": False,
        "formal_state_bypass": FORMAL_STATE_BYPASS,
    }


def overlay_capture_inputs(
    inputs: list[Mapping[str, Any]] | None,
    *,
    sidecar: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Union batch inputs with sidecar capture refs. No semantic mutation."""
    merged = [dict(x) for x in (inputs or []) if isinstance(x, Mapping)]
    seen = {(str(x.get("kind") or ""), str(x.get("ref_id") or "")) for x in merged}
    for item in sidecar_items(path=sidecar):
        if item.get("merged_into_batch"):
            continue
        key = (KIND_CAPTURE, str(item.get("ref_id") or ""))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        merged.append({
            "kind": KIND_CAPTURE,
            "ref_id": item["ref_id"],
            "payload_ref": item.get("payload_ref") or ("capture:" + item["ref_id"]),
            "ingested_at": item.get("ingested_at"),
            "semantic_processed": False,
            "formal_state_bypass": FORMAL_STATE_BYPASS,
            "from_sidecar": True,
        })
    return merged
