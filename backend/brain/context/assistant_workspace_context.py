"""Assistant Workspace Context — neutral context module for any assistant runtime.

Extracted from agnes_web_facade.py to serve Agy (and future runtimes).
Provides PAW_AGY_GROUNDED_CHAT_V1 evidence bundle and grounded prompt building.

Does NOT write canonical data. Does NOT expose secrets.
"""
from __future__ import annotations

import os
import sys

# Runtime module root comes from the explicit PAW Runtime Path Contract
# (/etc/paw-runtime-paths.env); no development-tree fallback.
_KSHEET_WORKER = (
    os.environ.get("PAW_KSHEET_WORKER_ROOT")
    or os.environ.get("PAW_RUNTIME_MODULE_ROOT")
)
if not _KSHEET_WORKER:
    raise RuntimeError(
        "PAW_RUNTIME_MODULE_ROOT (or legacy PAW_KSHEET_WORKER_ROOT) is required "
        "(PAW Runtime Path Contract, /etc/paw-runtime-paths.env)"
    )
if _KSHEET_WORKER not in sys.path:
    sys.path.insert(0, _KSHEET_WORKER)

MAX_SNAPSHOT_TODOS = 8
MAX_SNAPSHOT_CAPTURES = 5
MAX_SNAPSHOT_PROJECTS = 10


def workspace_snapshot_text() -> str:
    """Compact READ-ONLY snapshot of the current Workspace.

    Reuses existing repository readers only (no new SQL, no writes).
    Retrieval errors are surfaced, not swallowed.
    """
    try:
        from workspace_api import repository as ws_repo
        from brain.candidates.repository import candidate_summary as _candidate_summary

        today = ws_repo.today_view()
        projects = ws_repo.list_projects(80)
        open_projects = [p for p in projects if p.get("status") != "archived"][:MAX_SNAPSHOT_PROJECTS]
        summary = _candidate_summary()
        unprocessed = ws_repo.unprocessed_captures(5)

        lines = [f"日期：{today.get('date') or ''}"]

        if open_projects:
            lines.append("当前项目：")
            for p in open_projects:
                lines.append(f"  - {p.get('name', 'unnamed')} (status={p.get('status', '?')})")

        todos = today.get("todos", [])[:MAX_SNAPSHOT_TODOS] if isinstance(today.get("todos"), list) else []
        if todos:
            lines.append("今日待办：")
            for t in todos:
                lines.append(f"  - {t.get('title', '')} [{t.get('status', '?')}]")

        if unprocessed:
            lines.append(f"未处理快照：{len(unprocessed)} 条")

        if summary:
            pending = summary.get("pending_count", 0)
            if pending:
                lines.append(f"待审核候选：{pending} 条")

        return "\n".join(lines) if len(lines) > 1 else ""
    except Exception as exc:
        return f"[workspace_snapshot_error: {type(exc).__name__}]"


def source_context_for_message(message: str, project_id: str | None = None) -> str:
    """Route to trusted source content based on user message.

    Always attempts retrieval (not gated on PROJECT_HINT_RE).
    Returns source content with provenance and freshness disclosure.
    """
    try:
        from paw_agy_grounded_chat import build_evidence_bundle, format_evidence_block

        bundle = build_evidence_bundle(message, project_id=project_id)
        source_items = [e for e in bundle.get("evidence", []) if e.get("layer") == "SOURCE"]
        if not source_items:
            errors = bundle.get("retrieval_errors") or []
            if errors:
                return f"[来源检索：无命中；告警：{'; '.join(errors[:3])}]"
            return ""
        partial = {**bundle, "evidence": source_items, "evidence_count": len(source_items)}
        return format_evidence_block(partial)
    except Exception as exc:
        return f"[source_context_error: {type(exc).__name__}: {exc}]"


def _legacy_prompt(message: str, mode: str) -> str:
    """Pre-grounded-chat prompt builder for non-Web-Agy callers (R13E/brain)."""
    mode_instructions = {
        "chat": "You are a helpful assistant for the Personal AI Workspace. Answer using the provided workspace context.",
        "extract": "You are a data extraction assistant. Extract requested information from workspace data. Always cite the source.",
        "analyze": "You are an analysis assistant. Analyze workspace data and provide insights. Base analysis on real data only.",
        "plan": "You are a planning assistant. Help form a plan based on workspace context. Do not execute any changes.",
    }
    parts = [mode_instructions.get(mode, mode_instructions["chat"])]
    snapshot = workspace_snapshot_text()
    if snapshot:
        parts.append(f"\n--- Current Workspace ---\n{snapshot}")
    try:
        from brain.source_routing import source_context_for_message as _route
        source_ctx = _route(message)
    except Exception:
        source_ctx = ""
    if source_ctx:
        parts.append(f"\n--- Source Context ---\n{source_ctx}")
    parts.append(f"\n--- User Message ---\n{message}")
    return "\n".join(parts)


def build_assistant_message(
    message: str,
    mode: str,
    project_id: str | None = None,
    *,
    grounded: bool = True,
) -> tuple[str, dict]:
    """Build prompt and return (prompt, evidence_bundle).

    Web Agy path (grounded=True): PAW_AGY_GROUNDED_CHAT_V1 four-layer retrieval.
    Legacy callers (grounded=False): shallow snapshot, no output gate contract.
    """
    if not grounded:
        return _legacy_prompt(message, mode), {}

    from paw_agy_grounded_chat import (
        FAIL_CLOSED_MESSAGE,
        build_evidence_bundle,
        build_grounded_prompt,
        should_skip_inference,
    )

    bundle = build_evidence_bundle(message, project_id=project_id)
    if should_skip_inference(bundle):
        return FAIL_CLOSED_MESSAGE, bundle
    prompt = build_grounded_prompt(message, mode, bundle)
    return prompt, bundle


def build_assistant_message_legacy(message: str, mode: str, project_id: str | None = None) -> str:
    """Backward-compatible single-string builder for R13E/brain paths."""
    prompt, _bundle = build_assistant_message(message, mode, project_id=project_id, grounded=False)
    return prompt
