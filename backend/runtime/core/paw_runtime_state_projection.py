#!/usr/bin/env python3
"""Runtime projection: historical bootstrap/review vs current incremental patrol.

Raw Source, SourceVersion, ReviewBundle, Opus, UserDecision, ReviewDecision,
ApprovedChange, CommitRecord and audit rows are never deleted or rewritten.
This module only *labels* retired bootstrap-era state so current Patrol APIs
do not treat it as production backlog.

The PROJECT-first terminal transition (COMPLETED + deferred queues preserved)
is the matching state-machine lifecycle.  This projection remains valid even
before that marker is written: 710/710 coverage + retired main-review +
enabled incremental gate means the operational plane is already
CURRENT_PRODUCTION_INCREMENTAL_PATROL.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from gpt_brain_common import utc_now

SCHEMA = "paw-runtime-state-projection/1"
LIFECYCLE_SCHEMA = "paw-runtime-state-lifecycle/1"
OPERATIONAL_PLANE = "CURRENT_PRODUCTION_INCREMENTAL_PATROL"
HISTORICAL_BOOTSTRAP_PLANE = "HISTORICAL_BOOTSTRAP"
HISTORICAL_REVIEW_ARCHIVE = "HISTORICAL_REVIEW_ARCHIVE"
ARCHIVED_TERMINAL = "ARCHIVED_TERMINAL"
ACTIVE_BOOTSTRAP = "ACTIVE_BOOTSTRAP"
FORMAL_STATE_BYPASS = 0
FROZEN_SOURCE_TOTAL = 710
DEFAULT_LIFECYCLE_PATH = Path("/var/lib/paw-mcp-gateway/paw_runtime_state_lifecycle.json")
_JSON_CACHE: dict[tuple[str, float], dict[str, Any]] = {}
COVERAGE_FINAL_STATES = frozenset({
    "CONSOLIDATED", "DATA_EXTRACTED", "FAILED", "INSUFFICIENT_EVIDENCE",
    "NO_KNOWLEDGE_IMPACT", "PROJECT_CONTEXT_ONLY", "REVIEW_REQUIRED",
})

CURRENT_POST_OPUS_STATES = frozenset({"POST_OPUS_PENDING"})
HISTORICAL_UNRESOLVED_STATES = frozenset({
    "OPUS_REQUIRED", "USER_DECISION_REQUIRED", "REVIEW_PENDING",
})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_json(path: str | os.PathLike[str] | None) -> dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return {}
    cache_key = (str(p), mtime)
    hit = _JSON_CACHE.get(cache_key)
    if hit is not None:
        return hit
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    data = payload if isinstance(payload, dict) else {}
    if len(_JSON_CACHE) > 16:
        _JSON_CACHE.clear()
    _JSON_CACHE[cache_key] = data
    return data


def _count_states(items: Mapping[str, Any] | None, key: str = "state") -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in (items or {}).values():
        if not isinstance(item, Mapping):
            continue
        name = _text(item.get(key)) or "UNKNOWN"
        counts[name] = counts.get(name, 0) + 1
    return counts


def _project_first_gate_enabled(gate: Mapping[str, Any]) -> bool:
    return (
        _text(gate.get("status")).upper() == "ENABLED"
        and _int(gate.get("machine_actionable_backlog_remaining")) == 0
        and _int(gate.get("baseline_projects")) > 0
        and _int(gate.get("baseline_readback_pass")) == _int(gate.get("baseline_projects"))
        and gate.get("full_corpus_reread") is False
        and _int(gate.get("formal_state_bypass")) == 0
    )


def _capture_unprocessed_count(db_path: str | os.PathLike[str] | None) -> int:
    if not db_path:
        return 0
    path = Path(db_path)
    if not path.is_file():
        return 0
    try:
        import sys
        kworker = (
            os.environ.get("PAW_RUNTIME_MODULE_ROOT")
)
        if not kworker:
            raise RuntimeError(
                "PAW_RUNTIME_MODULE_ROOT is required "
                "(PAW Runtime Path Contract, /etc/paw-runtime-paths.env)"
            )
        if kworker not in sys.path:
            sys.path.insert(0, kworker)
        from paw_capture_lifecycle import count_patrol_eligible_captures

        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return int(count_patrol_eligible_captures(conn))
        finally:
            conn.close()
    except (sqlite3.Error, ImportError, OSError):
        return 0


def _batch_kind_counts(batch: Mapping[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in batch.get("inputs") or []:
        if not isinstance(item, Mapping):
            continue
        kind = _text(item.get("kind")) or "unknown"
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def classify_historical_bootstrap(
    *,
    bootstrap: Mapping[str, Any],
    gate: Mapping[str, Any],
    frozen_total: int = FROZEN_SOURCE_TOTAL,
) -> dict[str, Any]:
    """Pure classifier. Does not mutate bootstrap rows."""
    raw_status = _text(bootstrap.get("status")).upper() or "UNKNOWN"
    terminal = bool(bootstrap.get("historical_bootstrap_terminal"))
    closure_mode = bootstrap.get("closure_mode")
    states = bootstrap.get("source_states") if isinstance(bootstrap.get("source_states"), Mapping) else {}
    derived_counts: dict[str, int] = {}
    for state in states.values():
        if isinstance(state, Mapping):
            name = _text(state.get("state")) or "UNKNOWN"
            derived_counts[name] = derived_counts.get(name, 0) + 1
    derived_consumed = sum(derived_counts.get(name, 0) for name in COVERAGE_FINAL_STATES)
    consumed = _int(bootstrap.get("bootstrap_consumed"), derived_consumed)
    if consumed == 0 and derived_consumed:
        consumed = derived_consumed
    visual = _int(bootstrap.get("visual_pending_count"), derived_counts.get("VISUAL_PENDING", 0))
    frozen = _int((bootstrap.get("frozen_queue") or {}).get("total_sources") or bootstrap.get("cursor_source_total") or len(states) or frozen_total)
    unprocessed = _int(bootstrap.get("unprocessed_source"))
    if "unprocessed_source" not in bootstrap and frozen:
        unprocessed = max(0, frozen - consumed)
    covered = _int(bootstrap.get("covered_semantic_sources"), consumed)
    coverage_complete = (
        frozen == frozen_total
        and unprocessed == 0
        and visual == 0
        and consumed == frozen_total
    )
    gate_ok = _project_first_gate_enabled(gate)
    archived = terminal or (coverage_complete and gate_ok and raw_status in {"ACTIVE", "COMPLETED"})
    lifecycle = ARCHIVED_TERMINAL if archived else ACTIVE_BOOTSTRAP
    return {
        "raw_status": raw_status,
        "lifecycle": lifecycle,
        "plane": HISTORICAL_BOOTSTRAP_PLANE if archived else ACTIVE_BOOTSTRAP,
        "historical_bootstrap_terminal": terminal,
        "closure_mode": closure_mode,
        "coverage_complete": coverage_complete,
        "frozen_source_total": frozen,
        "bootstrap_consumed": consumed,
        "unprocessed_source": unprocessed,
        "visual_pending_count": visual,
        "covered_semantic_sources": covered,
        "project_first_gate_enabled": gate_ok,
        "counts_as_current_production_work": False if archived else True,
        "formal_state_bypass": FORMAL_STATE_BYPASS,
    }


def historical_review_projection(
    *,
    total_bundles: int,
    state_counts: Mapping[str, int],
    metrics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metrics = metrics or {}
    unresolved = {
        "opus_required": _int(metrics.get("opus_required"), state_counts.get("OPUS_REQUIRED", 0)),
        "user_decision": _int(metrics.get("user_decision"), state_counts.get("USER_DECISION_REQUIRED", 0)),
        "review_pending": _int(metrics.get("review_pending"), state_counts.get("REVIEW_PENDING", 0)),
    }
    post_opus = _int(state_counts.get("POST_OPUS_PENDING"))
    return {
        "lifecycle": HISTORICAL_REVIEW_ARCHIVE,
        "plane": HISTORICAL_REVIEW_ARCHIVE,
        "total_bundles": total_bundles,
        "state_counts": dict(state_counts),
        "historical_unresolved_counts": unresolved,
        "counts_as_current_production_backlog": False,
        "queryable_auditable": True,
        "fabricated_decisions": False,
        "current_post_opus_claimable": post_opus,
        "authoritative_main_review_drain_enabled": False,
        "formal_state_bypass": FORMAL_STATE_BYPASS,
    }


def current_incremental_projection(
    *,
    incremental: Mapping[str, Any],
    patrol_gate: Mapping[str, Any],
    capture_unprocessed: int,
    open_batch: Mapping[str, Any] | None,
    historical_archived: bool,
) -> dict[str, Any]:
    items = incremental.get("items") if isinstance(incremental.get("items"), Mapping) else {}
    inc_states = _count_states(items, key="analysis_state")
    kind_counts = _batch_kind_counts(open_batch or {})
    capture_in_batch = _int(kind_counts.get("capture"))
    source_in_batch = _int(kind_counts.get("source"))
    receipt_in_batch = _int(kind_counts.get("decision_receipt"))
    terra = _int(inc_states.get("TERRA_PENDING")) + _int(inc_states.get("TERRA_RUNNING"))
    incremental_enabled = bool(patrol_gate.get("incremental_mirror_patrol_enabled"))
    daily_capture = bool(patrol_gate.get("daily_capture_patrol_enabled"))
    generic_enabled = bool((patrol_gate.get("patrol") or {}).get("enabled"))
    status = "ENABLED" if (incremental_enabled or daily_capture or historical_archived) else "BLOCKED"
    return {
        "plane": OPERATIONAL_PLANE,
        "status": status,
        "incremental_mirror_patrol_enabled": incremental_enabled,
        "daily_capture_patrol_enabled": daily_capture,
        "generic_patrol_enabled": generic_enabled,
        "generic_patrol_enabled_blocks_capture_discovery": False,
        "incremental_item_count": len(items) if isinstance(items, Mapping) else 0,
        "incremental_state_counts": inc_states,
        "current_incremental_backlog": {
            "terra_pending": terra,
            "reader_ready": _int(inc_states.get("READER_READY")),
            "patrol_ready": _int(inc_states.get("PATROL_READY")),
            "opus_pending": _int(inc_states.get("OPUS_PENDING")) + _int(inc_states.get("OPUS_RUNNING")),
            "user_decision_pending": _int(inc_states.get("USER_DECISION_PENDING")),
            "insufficient_hold": _int(inc_states.get("EXPLICIT_HOLD")),
        },
        "current_capture_backlog": capture_unprocessed,
        "open_batch": {
            "batch_id": (open_batch or {}).get("batch_id"),
            "frozen": bool((open_batch or {}).get("frozen")),
            "status": (open_batch or {}).get("status"),
            "source_count": source_in_batch,
            "capture_count": capture_in_batch,
            "decision_receipt_count": receipt_in_batch,
            "mixed_source_and_capture": source_in_batch > 0 and capture_in_batch > 0,
        },
        "authoritative": True,
        "formal_state_bypass": FORMAL_STATE_BYPASS,
    }


def project_runtime_state(
    *,
    bootstrap_status: Mapping[str, Any] | None = None,
    review_metrics: Mapping[str, Any] | None = None,
    review_state_counts: Mapping[str, int] | None = None,
    review_total: int | None = None,
    incremental: Mapping[str, Any] | None = None,
    patrol_gate: Mapping[str, Any] | None = None,
    gate: Mapping[str, Any] | None = None,
    open_batch: Mapping[str, Any] | None = None,
    capture_unprocessed: int | None = None,
    bootstrap_path: str | os.PathLike[str] | None = None,
    review_path: str | os.PathLike[str] | None = None,
    incremental_path: str | os.PathLike[str] | None = None,
    gate_path: str | os.PathLike[str] | None = None,
    batch_path: str | os.PathLike[str] | None = None,
    db_path: str | os.PathLike[str] | None = None,
    skip_review: bool = False,
) -> dict[str, Any]:
    """Build the operational vs historical projection. Read-only."""
    if bootstrap_status is None:
        raw_brain = _read_json(bootstrap_path or os.environ.get("PAW_GPT_BRAIN_STATE_PATH", "/var/lib/paw-mcp-gateway/gpt_brain_bootstrap.json"))
        bootstrap_status = {
            "bootstrap": raw_brain.get("bootstrap") or {},
            "patrol": raw_brain.get("patrol") or {},
        }
    bootstrap = dict(bootstrap_status.get("bootstrap") or {})
    gate = dict(gate or _read_json(gate_path or os.environ.get("PAW_PROJECT_FIRST_GATE_PATH", "/var/lib/paw-mcp-gateway/paw_project_first_incremental_gate.json")))
    if incremental is None:
        incremental = _read_json(incremental_path or "/var/lib/paw-mcp-gateway/paw_incremental_pipeline.json")
    if open_batch is None:
        batch_store = _read_json(batch_path or os.environ.get("PAW_PATROL_BATCH_STORE", "/var/lib/paw-mcp-gateway/paw_patrol_input_batch.json"))
        open_id = _text(batch_store.get("open_batch_id"))
        open_batch = (batch_store.get("batches") or {}).get(open_id) if open_id else {}
        if not isinstance(open_batch, Mapping):
            open_batch = {}
    if capture_unprocessed is None:
        capture_unprocessed = _capture_unprocessed_count(
            db_path or os.environ.get("PAW_WORKSPACE_DB", os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db")))
        )
    if skip_review:
        review_metrics = review_metrics or {}
        review_state_counts = review_state_counts or {}
        review_total = int(review_total or 0)
    elif review_metrics is None or review_total is None or review_state_counts is None:
        review = _read_json(review_path or os.environ.get("PAW_GPT_REVIEW_BUNDLE_PATH", "/var/lib/paw-mcp-gateway/paw_gpt_review_bundles.json"))
        bundles = review.get("bundles") if isinstance(review.get("bundles"), Mapping) else {}
        review_state_counts = review_state_counts or _count_states(bundles)
        review_metrics = review_metrics or (review.get("metrics") if isinstance(review.get("metrics"), Mapping) else {})
        review_total = review_total if review_total is not None else len(bundles)
    if patrol_gate is None:
        try:
            from paw_gpt_review_bundle import compute_patrol_resume_gate
            patrol_gate = compute_patrol_resume_gate(brain_data=bootstrap_status)
        except Exception:
            patrol_gate = {
                "incremental_mirror_patrol_enabled": _project_first_gate_enabled(gate),
                "daily_capture_patrol_enabled": True,
                "patrol": dict(bootstrap_status.get("patrol") or {}),
            }

    historical = classify_historical_bootstrap(bootstrap=bootstrap, gate=gate)
    review_proj = historical_review_projection(
        total_bundles=int(review_total or 0),
        state_counts=review_state_counts or {},
        metrics=review_metrics or {},
    )
    current = current_incremental_projection(
        incremental=incremental or {},
        patrol_gate=patrol_gate or {},
        capture_unprocessed=int(capture_unprocessed or 0),
        open_batch=open_batch or {},
        historical_archived=historical["lifecycle"] == ARCHIVED_TERMINAL,
    )
    operational_plane = OPERATIONAL_PLANE if historical["lifecycle"] == ARCHIVED_TERMINAL else ACTIVE_BOOTSTRAP
    return {
        "schema": SCHEMA,
        "projected_at": utc_now(),
        "operational_plane": operational_plane,
        "historical_bootstrap_status": historical,
        "historical_review_counts": review_proj,
        "current_incremental_patrol_status": current,
        "current_incremental_backlog": current["current_incremental_backlog"],
        "current_capture_backlog": current["current_capture_backlog"],
        "historical_unresolved_counts": review_proj["historical_unresolved_counts"],
        "bootstrap_top_level_misleading": (
            historical["raw_status"] == "ACTIVE" and historical["lifecycle"] == ARCHIVED_TERMINAL
        ),
        "formal_state_bypass": FORMAL_STATE_BYPASS,
    }


def compact_preflight(projection: Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    """Machine-readable overlay for always-called preflight tools. No raw captures."""
    kwargs.setdefault("skip_review", True)
    proj = dict(projection or project_runtime_state(**kwargs))
    hist = proj.get("historical_bootstrap_status") or {}
    current = proj.get("current_incremental_patrol_status") or {}
    batch = current.get("open_batch") or {}
    capture_n = _int(proj.get("current_capture_backlog"))
    terra_n = _int((proj.get("current_incremental_backlog") or {}).get("terra_pending"))
    reader_n = _int((proj.get("current_incremental_backlog") or {}).get("reader_ready"))
    patrol_n = _int((proj.get("current_incremental_backlog") or {}).get("patrol_ready"))
    post_opus = _int((proj.get("historical_review_counts") or {}).get("current_post_opus_claimable"))
    # reader_ready items are patrol-bound semantic work waiting for the GPT
    # patrol; excluding them from the work formula starved the pipeline
    # (production: 22 items stuck while patrol_work_available=false).
    # P0 arch fix: patrol_ready items are prepared Evidence Packets waiting
    # for GPT primary semantic review via list/claim/submit tools.
    work = capture_n > 0 or terra_n > 0 or post_opus > 0 or reader_n > 0 or patrol_n > 0 or _int(batch.get("source_count")) > 0
    return {
        "operational_plane": proj.get("operational_plane"),
        "historical_bootstrap_lifecycle": hist.get("lifecycle"),
        "historical_bootstrap_raw_status": hist.get("raw_status"),
        "pending_capture_count": capture_n,
        "capture_work_available": capture_n > 0,
        "daily_capture_patrol_enabled": bool(current.get("daily_capture_patrol_enabled")),
        "incremental_mirror_patrol_enabled": bool(current.get("incremental_mirror_patrol_enabled")),
        "generic_patrol_enabled": bool(current.get("generic_patrol_enabled")),
        "generic_patrol_enabled_blocks_capture_discovery": False,
        "current_incremental_backlog_count": _int(current.get("incremental_item_count")),
        "current_terra_pending_count": terra_n,
        "current_reader_ready_count": reader_n,
        "reader_ready_work_available": reader_n > 0,
        "current_patrol_ready_count": patrol_n,
        "patrol_ready_work_available": patrol_n > 0,
        "current_post_opus_claimable": post_opus,
        "open_patrol_batch_id": batch.get("batch_id"),
        "open_patrol_batch_frozen": bool(batch.get("frozen")),
        "open_patrol_source_count": _int(batch.get("source_count")),
        "open_patrol_capture_count": _int(batch.get("capture_count")),
        "patrol_work_available": work,
        "next_patrol_action": "gpt_patrol_discover_work",
        "next_capture_read_action": "gpt_patrol_list_unprocessed_captures" if capture_n > 0 else None,
        "semantic_processing": False,
        "formal_state_bypass": FORMAL_STATE_BYPASS,
    }


def write_lifecycle_marker(
    projection: Mapping[str, Any],
    *,
    path: str | os.PathLike[str] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Additive lifecycle sidecar. Does not rewrite historical bundles."""
    marker = {
        "schema": LIFECYCLE_SCHEMA,
        "written_at": utc_now(),
        "operational_plane": projection.get("operational_plane"),
        "historical_bootstrap_lifecycle": (projection.get("historical_bootstrap_status") or {}).get("lifecycle"),
        "historical_review_lifecycle": HISTORICAL_REVIEW_ARCHIVE,
        "formal_state_bypass": FORMAL_STATE_BYPASS,
        "projection_schema": SCHEMA,
    }
    if extra:
        marker.update(dict(extra))
    dest = Path(path or os.environ.get("PAW_RUNTIME_LIFECYCLE_PATH", str(DEFAULT_LIFECYCLE_PATH)))
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(dest)
    return marker


def apply_project_first_historical_terminal(
    *,
    brain: Any | None = None,
    gate_path: str | os.PathLike[str] | None = None,
    incremental_state_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Fail-closed PROJECT-first terminal. Preserves deferred Opus/User queues."""
    import sys
    gateway = "/opt/paw-mcp-gateway"
    if gateway not in sys.path:
        sys.path.insert(0, gateway)
    from app.gpt_brain_bootstrap import GPTKnowledgeBrain
    from app.gpt_brain_bootstrap_terminal import close_project_first_bootstrap

    target = brain or GPTKnowledgeBrain()
    return close_project_first_bootstrap(
        target,
        gate_path=gate_path,
        incremental_state_path=incremental_state_path,
    )


__all__ = [
    "SCHEMA",
    "OPERATIONAL_PLANE",
    "HISTORICAL_REVIEW_ARCHIVE",
    "ARCHIVED_TERMINAL",
    "FORMAL_STATE_BYPASS",
    "FROZEN_SOURCE_TOTAL",
    "classify_historical_bootstrap",
    "historical_review_projection",
    "project_runtime_state",
    "compact_preflight",
    "write_lifecycle_marker",
    "apply_project_first_historical_terminal",
]
