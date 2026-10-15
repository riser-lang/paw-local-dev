#!/usr/bin/python3
"""Durable continuous GPT ReviewBundle consumer.

GPT remains the semantic reviewer. This process never impersonates GPT with
Cursor, Codex/Luna, Agy, Mistral, Gemini, or Claude. CLEAR is recorded only
through ChatGPT-owned worker ids into the existing ReviewBundle store, then
the standing policy committer / Opus dispatcher own the rest of the chain.

If no outbound GPT/Workspace-Agent path is configured, the service stays
boot-enabled, claims nothing, and waits. Inbound ChatGPT MCP is not a push
invocation path.
"""
from __future__ import annotations

import argparse
import json
import os
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

from gpt_brain_common import FileLeaseLock, LockedJsonStore, safe_copy, text, utc_now
from paw_gpt_invocation import (
    DEFAULT_GPT_MODEL,
    GPT_INVOCATION_LIVE,
    GPT_INVOCATION_WAITING_GPT,
    MAIN_REVIEW_WORKER,
    POST_OPUS_WORKER,
    WaitingGptAdapter,
    build_live_adapter,
    probe_gpt_invocation_path,
)
from paw_gpt_review_bundle import (
    BUNDLE_OUTCOMES,
    POST_OPUS_OUTCOMES,
    ReviewBundleStore,
    claim_post_opus_work,
    claim_review_bundles,
    get_bundle_preanalysis,
    queue_stats,
    record_bundle_review,
    record_post_opus_bundle,
)

ACTOR = "paw-gpt-review-bundle-consumer"
SCHEMA = "paw-gpt-review-bundle-consumer/1"
DEFAULT_POLL_SECONDS = 20.0
WAITING_POLL_SECONDS = 45.0
MAX_MAIN_PER_TICK = 1
MAX_POST_OPUS_PER_TICK = 1
MAIN_REVIEW_LIMIT = 1
POST_OPUS_LIMIT = 1
BACKOFF_SECONDS = (5, 15, 30, 60, 120)


def _path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default))


def default_paths() -> dict[str, Path]:
    root = _path("PAW_GPT_REVIEW_CONSUMER_STATE_DIR", "/var/lib/paw-mcp-gateway")
    return {
        "state": root / "paw_gpt_review_consumer.json",
        "lock": root / "paw_gpt_review_consumer.lock",
        "brain": _path("PAW_GPT_BRAIN_STATE_PATH", "/var/lib/paw-mcp-gateway/gpt_brain_bootstrap.json"),
        "bundles": _path("PAW_GPT_REVIEW_BUNDLE_PATH", "/var/lib/paw-mcp-gateway/paw_gpt_review_bundles.json"),
    }


def _blank() -> dict[str, Any]:
    now = utc_now()
    return {
        "schema": SCHEMA,
        "status": "CREATED",
        "run_id": str(uuid.uuid4()),
        "actor": ACTOR,
        "started_at": None,
        "updated_at": now,
        "gpt_invocation_path": GPT_INVOCATION_WAITING_GPT,
        "main_review_model_identity": GPT_INVOCATION_WAITING_GPT,
        "hard_blocker": None,
        "last_error": None,
        "last_success": None,
        "backoff_index": 0,
        "ticks": 0,
        "claimed_main": 0,
        "recorded_main": 0,
        "claimed_post_opus": 0,
        "recorded_post_opus": 0,
        "released_claims": 0,
        "waiting_gpt_ticks": 0,
        "formal_state_bypass": 0,
        "codex_luna_slots_consumed": 0,
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
        data["schema"] = SCHEMA
        data["formal_state_bypass"] = 0
        data["codex_luna_slots_consumed"] = 0
        self.store.save(data)

    def event(self, value: dict[str, Any], kind: str, **details: Any) -> None:
        events = value.setdefault("events", [])
        events.append({"at": utc_now(), "type": kind, **details})
        if len(events) > 400:
            value["events"] = events[-400:]
        value["updated_at"] = utc_now()


def _load_brain():
    try:
        from app.gpt_brain_bootstrap import AtomicJsonStore, GPTKnowledgeBrain
        return GPTKnowledgeBrain(store=AtomicJsonStore(default_paths()["brain"]))
    except Exception:
        return None


def _maybe_brain(store: ReviewBundleStore):
    try:
        if Path(store.store.path).resolve() == default_paths()["bundles"].resolve():
            return _load_brain()
    except Exception:
        return None
    return None


def _compact_bundle(item: Mapping[str, Any]) -> dict[str, Any]:
    findings = []
    for finding in item.get("findings") or []:
        if not isinstance(finding, Mapping):
            continue
        findings.append({
            "id": finding.get("id"),
            "finding_type": finding.get("finding_type"),
            "assertion": finding.get("assertion"),
            "project_or_domain": finding.get("project_or_domain"),
            "affected_targets": list(finding.get("affected_targets") or [])[:8],
            "proposed_target": finding.get("proposed_target"),
            "proposed_operation": finding.get("proposed_operation"),
        })
    luna_pre = item.get("luna_preanalysis") or get_bundle_preanalysis(item)
    return {
        "bundle_id": item.get("bundle_id") or item.get("id"),
        "source_version_id": item.get("source_version_id"),
        "state": item.get("state"),
        "coverage_state": item.get("coverage_state"),
        "zero_findings": item.get("zero_findings"),
        "has_writable_proposals": item.get("has_writable_proposals"),
        "is_authoritative": bool(item.get("is_authoritative")),
        "luna_preanalysis": safe_copy(luna_pre),
        "knowledge_proposals": safe_copy(item.get("knowledge_proposals") or [])[:12],
        "project_proposals": safe_copy(item.get("project_proposals") or [])[:12],
        "metric_proposals": safe_copy(item.get("metric_proposals") or [])[:12],
        "findings": findings[:12],
        "evidence_refs": safe_copy(item.get("evidence_refs") or [])[:20],
        "source_refs": list(item.get("source_refs") or [])[:12],
        "provenance": safe_copy(item.get("provenance") or {}),
        "opus_review": safe_copy(item.get("opus_review")) if item.get("opus_review") else None,
        "opus_cluster_id": item.get("opus_cluster_id"),
    }


def _review_prompt(item: Mapping[str, Any], *, kind: str) -> str:
    compact = _compact_bundle(item)
    if kind == "post_opus":
        duty = (
            "This is post-Opus authoritative GPT consolidation. Opus already reviewed the cluster. "
            "Evaluate proposals against canonical evidence. Choose CLEAR/FINALIZE only for safe auto-write, "
            "or USER_DECISION_REQUIRED, NO_ACTION, or INSUFFICIENT_EVIDENCE. "
            "Specify action_on_luna_proposal as ACCEPT, REVISE, or REJECT. Do not impersonate Opus."
        )
        outcomes = 'CLEAR | FINALIZE | USER_DECISION_REQUIRED | NO_ACTION | INSUFFICIENT_EVIDENCE'
    else:
        duty = (
            "This is authoritative GPT Brain semantic consolidation. Luna/Codex pre-analysis outputs "
            "are non-authoritative candidate proposals and evidence enrichment. "
            "Independently evaluate proposals against canonical evidence and source references. "
            "Choose outcome: CLEAR | NO_ACTION | OPUS_REQUIRED | USER_DECISION_REQUIRED | INSUFFICIENT_EVIDENCE. "
            "Choose action_on_luna_proposal: ACCEPT | REVISE | REJECT. "
            "CLEAR may include exact write_changes only when they match GPT_CLEAR_AUTO_WRITE_V1. "
            "If proposals need alteration before CLEAR, provide revised envelopes in write_changes. "
            "OPUS_REQUIRED uses the existing claude-opus-4-6-thinking dispatcher. "
            "USER_DECISION_REQUIRED waits for a human. Do not invent numbers."
        )
        outcomes = "CLEAR | NO_ACTION | OPUS_REQUIRED | USER_DECISION_REQUIRED | INSUFFICIENT_EVIDENCE"
    return (
        f"{duty}\n"
        "Return exactly one JSON object with keys: outcome, action_on_luna_proposal, reason, write_changes.\n"
        f"outcome must be one of: {outcomes}\n"
        "action_on_luna_proposal must be one of: ACCEPT | REVISE | REJECT\n"
        "write_changes is an array of exact envelopes or [].\n"
        "Standing safe auto-write: knowledge create/update; project update of "
        "latest_progress_summary/next_action_summary/description; data create/revise; "
        "knowledge_project_link link. Never auto-write delete/archive/todo/unlink/"
        "project create/visual-pending/weak evidence.\n\n"
        f"bundle={json.dumps(compact, ensure_ascii=False, default=str)}"
    )


def _release_claim(store: ReviewBundleStore, *, bundle_id: str, claim_id: str, field: str) -> bool:
    with store.store.mutate() as data:
        bundle = (data.get("bundles") or {}).get(bundle_id)
        if not isinstance(bundle, dict):
            return False
        claim = bundle.get(field) if isinstance(bundle.get(field), dict) else {}
        if text(claim.get("claim_id")) != text(claim_id):
            return False
        bundle[field] = None
        bundle["updated_at"] = utc_now()
        return True


def _normalize_decision(raw: Mapping[str, Any], *, allowed: frozenset[str], default_model: str | None = None) -> dict[str, Any]:
    outcome = text(raw.get("outcome")).upper()
    if outcome not in allowed:
        raise ValueError(f"unsupported GPT outcome: {outcome}")
    writes = raw.get("write_changes") or []
    if isinstance(writes, dict):
        writes = [writes]
    if not isinstance(writes, list):
        writes = []
    action = text(raw.get("action_on_luna_proposal")).upper()
    if action not in {"ACCEPT", "REVISE", "REJECT"}:
        if outcome in {"CLEAR", "FINALIZE"}:
            action = "REVISE" if (writes and any(isinstance(w, Mapping) and (w.get("revised") or w.get("revision")) for w in writes)) else "ACCEPT"
        else:
            action = "REJECT"
    model_id = text(raw.get("model_identity")) or (text(default_model) if default_model else "")
    return {
        "outcome": outcome,
        "action_on_luna_proposal": action,
        "reason": text(raw.get("reason"))[:4000],
        "write_changes": writes,
        "model_identity": model_id,
    }


def consume_main(
    adapter: Any,
    store: ReviewBundleStore,
    ledger: Ledger,
    state: dict[str, Any],
    *,
    limit: int = MAIN_REVIEW_LIMIT,
) -> dict[str, Any]:
    if not adapter.available():
        return {"consumed": 0, "waiting_gpt": True}
    claimed = claim_review_bundles(limit=max(1, min(int(limit), MAX_MAIN_PER_TICK)), worker_id=MAIN_REVIEW_WORKER, store=store)
    items = list(claimed.get("items") or [])
    state["claimed_main"] = int(state.get("claimed_main") or 0) + len(items)
    recorded = 0
    brain = _maybe_brain(store)
    for item in items:
        bundle_id = text(item.get("bundle_id"))
        claim = item.get("claim") if isinstance(item.get("claim"), Mapping) else {}
        claim_id = text(claim.get("claim_id"))
        try:
            decision = adapter.review(_review_prompt(item, kind="main_review"), kind="main_review")
            if not isinstance(decision, Mapping):
                raise RuntimeError("GPT adapter returned no decision")
            adapter_model = getattr(adapter, "model_identity", None)
            normalized = _normalize_decision(decision, allowed=BUNDLE_OUTCOMES, default_model=adapter_model)
            effective_model = normalized.get("model_identity") or adapter_model
            record_result = record_bundle_review(
                bundle_id=bundle_id,
                claim_id=claim_id,
                outcome=normalized["outcome"],
                reason=normalized["reason"],
                write_changes=normalized["write_changes"],
                worker_id=MAIN_REVIEW_WORKER,
                model_identity=effective_model,
                action_on_luna_proposal=normalized.get("action_on_luna_proposal"),
                store=store,
                brain=brain,
            )
            if record_result.get("status") not in {"PERSISTED", "ALREADY_PERSISTED"}:
                raise RuntimeError(f"record_bundle_review failed: {record_result.get('status')}")
            recorded += 1
            state["recorded_main"] = int(state.get("recorded_main") or 0) + 1
            state["last_success"] = {
                "kind": "main_review",
                "bundle_id": bundle_id,
                "outcome": normalized["outcome"],
                "model_identity": effective_model,
                "action_on_luna_proposal": normalized.get("action_on_luna_proposal"),
                "at": utc_now(),
            }
            ledger.event(state, "main_recorded", bundle_id=bundle_id, outcome=normalized["outcome"])
        except Exception as exc:
            if bundle_id and claim_id:
                _release_claim(store, bundle_id=bundle_id, claim_id=claim_id, field="claim")
                state["released_claims"] = int(state.get("released_claims") or 0) + 1
            state["last_error"] = str(exc)[:1000]
            ledger.event(state, "main_failed", bundle_id=bundle_id, error=state["last_error"])
    return {"consumed": recorded, "claimed": len(items), "waiting_gpt": False}


def consume_post_opus(
    adapter: Any,
    store: ReviewBundleStore,
    ledger: Ledger,
    state: dict[str, Any],
    *,
    limit: int = POST_OPUS_LIMIT,
) -> dict[str, Any]:
    if not adapter.available():
        return {"consumed": 0, "waiting_gpt": True}
    claimed = claim_post_opus_work(limit=max(1, min(int(limit), MAX_POST_OPUS_PER_TICK)), worker_id=POST_OPUS_WORKER, store=store)
    items = list(claimed.get("items") or [])
    state["claimed_post_opus"] = int(state.get("claimed_post_opus") or 0) + len(items)
    recorded = 0
    brain = _maybe_brain(store)
    for item in items:
        bundle_id = text(item.get("bundle_id"))
        claim = item.get("claim") if isinstance(item.get("claim"), Mapping) else {}
        claim_id = text(claim.get("claim_id") or ((item.get("post_opus_claim") or {}).get("claim_id") if isinstance(item.get("post_opus_claim"), Mapping) else ""))
        try:
            decision = adapter.review(_review_prompt(item, kind="post_opus"), kind="post_opus")
            if not isinstance(decision, Mapping):
                raise RuntimeError("GPT adapter returned no post-Opus decision")
            adapter_model = getattr(adapter, "model_identity", None)
            normalized = _normalize_decision(decision, allowed=POST_OPUS_OUTCOMES, default_model=adapter_model)
            effective_model = normalized.get("model_identity") or adapter_model
            record_post_opus_bundle(
                bundle_id=bundle_id,
                claim_id=claim_id,
                outcome=normalized["outcome"],
                reason=normalized["reason"],
                write_changes=normalized["write_changes"],
                worker_id=POST_OPUS_WORKER,
                model_identity=effective_model,
                action_on_luna_proposal=normalized.get("action_on_luna_proposal"),
                store=store,
                brain=brain,
            )
            recorded += 1
            state["recorded_post_opus"] = int(state.get("recorded_post_opus") or 0) + 1
            state["last_success"] = {
                "kind": "post_opus",
                "bundle_id": bundle_id,
                "outcome": normalized["outcome"],
                "model_identity": effective_model,
                "action_on_luna_proposal": normalized.get("action_on_luna_proposal"),
                "at": utc_now(),
            }
            ledger.event(state, "post_opus_recorded", bundle_id=bundle_id, outcome=normalized["outcome"])
        except Exception as exc:
            if bundle_id and claim_id:
                _release_claim(store, bundle_id=bundle_id, claim_id=claim_id, field="post_opus_claim")
                state["released_claims"] = int(state.get("released_claims") or 0) + 1
            state["last_error"] = str(exc)[:1000]
            ledger.event(state, "post_opus_failed", bundle_id=bundle_id, error=state["last_error"])
    return {"consumed": recorded, "claimed": len(items), "waiting_gpt": False}


def tick_once(adapter: Any | None = None, *, store: ReviewBundleStore | None = None, ledger: Ledger | None = None) -> dict[str, Any]:
    paths = default_paths()
    ledger = ledger or Ledger(paths["state"])
    store = store or ReviewBundleStore(paths["bundles"])
    state = ledger.load()
    adapter = adapter or build_live_adapter()
    probe = probe_gpt_invocation_path()
    state["ticks"] = int(state.get("ticks") or 0) + 1
    state["gpt_invocation_path"] = GPT_INVOCATION_LIVE if adapter.available() else probe.get("path") or GPT_INVOCATION_WAITING_GPT
    state["main_review_model_identity"] = getattr(adapter, "model_identity", None) or probe.get("main_review_model_identity")
    if not adapter.available() or isinstance(adapter, WaitingGptAdapter):
        state["status"] = "WAITING_GPT"
        state["waiting_gpt_ticks"] = int(state.get("waiting_gpt_ticks") or 0) + 1
        state["hard_blocker"] = probe.get("reason")
        state["backoff_index"] = min(int(state.get("backoff_index") or 0) + 1, len(BACKOFF_SECONDS) - 1)
        ledger.event(state, "waiting_gpt", reason=state["hard_blocker"])
        ledger.save(state)
        stats = queue_stats(store=store)
        return {
            "status": "WAITING_GPT",
            "gpt_invocation_path": state["gpt_invocation_path"],
            "main_review_model_identity": state["main_review_model_identity"],
            "review_pending": stats.get("review_pending"),
            "hard_blocker": state["hard_blocker"],
            "formal_state_bypass": 0,
            "probe": probe,
        }
    state["status"] = "RUNNING"
    state["hard_blocker"] = None
    post = consume_post_opus(adapter, store, ledger, state)
    main = consume_main(adapter, store, ledger, state)
    if (post.get("consumed") or 0) + (main.get("consumed") or 0) > 0:
        state["backoff_index"] = 0
        state["last_error"] = None
    ledger.save(state)
    stats = queue_stats(store=store)
    return {
        "status": state["status"],
        "gpt_invocation_path": state["gpt_invocation_path"],
        "main_review_model_identity": state["main_review_model_identity"],
        "post_opus": post,
        "main": main,
        "review_pending": stats.get("review_pending"),
        "formal_state_bypass": 0,
    }


def run_forever(*, poll_seconds: float = DEFAULT_POLL_SECONDS, adapter: Any | None = None) -> None:
    paths = default_paths()
    ledger = Ledger(paths["state"])
    store = ReviewBundleStore(paths["bundles"])
    state = ledger.load()
    lock = FileLeaseLock(paths["lock"], stale_seconds=900)
    lock.acquire(run_id=str(state.get("run_id") or uuid.uuid4()), actor=ACTOR)
    try:
        state["status"] = "RUNNING"
        state["started_at"] = state.get("started_at") or utc_now()
        ledger.event(state, "service_started", actor=ACTOR)
        ledger.save(state)
        live_adapter = adapter
        while True:
            try:
                live_adapter = live_adapter or build_live_adapter()
                result = tick_once(live_adapter, store=store, ledger=ledger)
                state = ledger.load()
                if result.get("status") == "WAITING_GPT":
                    live_adapter = build_live_adapter()
                    delay = BACKOFF_SECONDS[min(int(state.get("backoff_index") or 0), len(BACKOFF_SECONDS) - 1)]
                    time.sleep(max(WAITING_POLL_SECONDS, float(delay)))
                else:
                    time.sleep(max(1.0, float(poll_seconds)))
            except Exception as exc:
                state = ledger.load()
                state["status"] = "BLOCKED"
                state["last_error"] = str(exc)[:1000]
                ledger.event(state, "tick_error", error=state["last_error"])
                ledger.save(state)
                time.sleep(max(5.0, float(poll_seconds)))
    finally:
        lock.release()


def status_payload() -> dict[str, Any]:
    paths = default_paths()
    ledger = Ledger(paths["state"]).load()
    stats = queue_stats(store=ReviewBundleStore(paths["bundles"]))
    probe = probe_gpt_invocation_path()
    return {
        "consumer": ledger,
        "queue": stats,
        "probe": probe,
        "source_processor_model": "gpt-5.6-luna",
        "source_processor_role": "luna_preanalysis",
        "authoritative_gpt_model": probe.get("authoritative_model_selection") or "WAITING_GPT",
        "authoritative_model_configured": bool(probe.get("authoritative_model_configured")),
        "formal_state_bypass": 0,
        "codex_luna_slots_consumed": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--probe-gpt", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    args = parser.parse_args(argv)
    if args.probe_gpt:
        json.dump(probe_gpt_invocation_path(), sys.stdout, ensure_ascii=False, indent=2, default=str)
        sys.stdout.write("\n")
        return 0
    if args.status:
        json.dump(status_payload(), sys.stdout, ensure_ascii=False, indent=2, default=str)
        sys.stdout.write("\n")
        return 0
    if args.once:
        json.dump(tick_once(), sys.stdout, ensure_ascii=False, indent=2, default=str)
        sys.stdout.write("\n")
        return 0
    run_forever(poll_seconds=args.poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
