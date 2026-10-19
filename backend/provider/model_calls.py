"""Per-call telemetry for the document-analysis (Phase 2) model path.

One row per model request made while a scan file context is active:
file name, call source (propose / section_update / chat_completion), attempt
number, elapsed seconds, HTTP status, whether it timed out, the number of
characters sent, the usage counters (prompt / completion / reasoning tokens,
when the provider returns them) and whether the rule fallback answered instead.

Only calls made inside a scan file context are recorded, so chat Q&A calls are
neither throttled nor logged here. Request bodies, prompts and credentials are
never stored: only their character count.
"""
from __future__ import annotations

import contextvars
import threading
from typing import Any, Dict, List, Optional

_MAX_ROWS = 400

# Set by the planner for the duration of one Phase 2 file (file display name).
_scan_file: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "paw_scan_file", default=None
)
# Semantic task name (section_update, project_advice, ...) for one call.
_task: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "paw_scan_task", default=None
)
# Answer turn (chat session id) while one assistant answer is being produced.
_answer: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "paw_answer_turn", default=None
)
# Stage of the answer turn: "first" or "gate_retry", plus the attempt offset so a
# retry is reported as attempt 2 instead of restarting at 1.
_stage: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "paw_answer_stage", default=None
)
_attempt_base: contextvars.ContextVar[int] = contextvars.ContextVar(
    "paw_answer_attempt_base", default=0
)

_rows: List[Dict[str, Any]] = []
_lock = threading.Lock()


class _Scope:
    """Context-manager wrapper around one or more contextvars tokens."""

    __slots__ = ("token", "_reset")

    def __init__(self, token, reset):
        self.token = token
        self._reset = reset

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        self._reset(self.token)
        return False


def _reset_file(token) -> None:
    try:
        _scan_file.reset(token)
    except (ValueError, RuntimeError):
        pass


def _reset_task(token) -> None:
    try:
        _task.reset(token)
    except (ValueError, RuntimeError):
        pass


def begin_file(name: str):
    """Enter the scan-file context; use it as a context manager or pass it to :func:`end_file`."""
    return _Scope(_scan_file.set(str(name or "")), _reset_file)


def end_file(scope) -> None:
    token = getattr(scope, "token", scope)
    _reset_file(token)


def begin_task(name: str):
    """Enter a semantic-task context; use it as a context manager or pass it to :func:`end_task`."""
    return _Scope(_task.set(str(name or "")), _reset_task)


def _reset_answer(token) -> None:
    try:
        _answer.reset(token)
    except (ValueError, RuntimeError):
        pass


def _reset_call(tokens) -> None:
    stage_token, base_token = tokens
    for var, token in ((_stage, stage_token), (_attempt_base, base_token)):
        try:
            var.reset(token)
        except (ValueError, RuntimeError):
            pass


def begin_answer(answer_id: str):
    """Enter the answer-turn context (the chat session id)."""
    return _Scope(_answer.set(str(answer_id or "")), _reset_answer)


def end_answer(scope) -> None:
    token = getattr(scope, "token", scope)
    _reset_answer(token)


def begin_call(stage: str, attempt_base: int = 0):
    """Label the call about to run: stage name and the attempt number it starts at."""
    return _Scope((_stage.set(str(stage or "")), _attempt_base.set(int(attempt_base))),
                  _reset_call)


def end_call(scope) -> None:
    token = getattr(scope, "token", scope)
    _reset_call(token)


def in_answer() -> bool:
    """True while one assistant answer is being produced."""
    return bool(_answer.get())


def count() -> int:
    """Number of recorded rows so far (a marker for :func:`rows_since`)."""
    with _lock:
        return len(_rows)


def rows_since(mark: int) -> List[Dict[str, Any]]:
    """Rows recorded after ``mark`` (used to attribute calls to one answer turn)."""
    with _lock:
        return [dict(row) for row in _rows[max(0, int(mark)):]]


def end_task(scope) -> None:
    token = getattr(scope, "token", scope)
    _reset_task(token)


def in_scan() -> bool:
    """True while a Phase 2 analysis file context is active."""
    return bool(_scan_file.get())


def current_file() -> Optional[str]:
    return _scan_file.get()


def source_name(default: str = "other") -> str:
    """Call source label: the semantic task name, or ``default``."""
    return _task.get() or default


def _token(usage: Dict[str, Any], key: str, details_key: str) -> int:
    value = usage.get(key)
    if isinstance(value, (int, float)):
        return int(value)
    details = usage.get(f"{key}_details") or usage.get(details_key)
    if isinstance(details, dict):
        inner = details.get("reasoning_tokens") if key == "completion_tokens" else None
        if isinstance(inner, (int, float)):
            return int(inner)
    if isinstance(value, bool):
        return 0
    return 0


def usage_tokens(usage: Any) -> Dict[str, int]:
    """Normalize prompt/completion/reasoning token counters from a provider usage block."""
    if not isinstance(usage, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0}
    prompt = _token(usage, "prompt_tokens", "prompt_tokens_details")
    completion = _token(usage, "completion_tokens", "completion_tokens_details")
    reasoning = _token(usage, "reasoning_tokens", "reasoning_tokens_details")
    details = usage.get("completion_tokens_details")
    if not reasoning and isinstance(details, dict):
        inner = details.get("reasoning_tokens")
        if isinstance(inner, (int, float)):
            reasoning = int(inner)
    return {"prompt_tokens": prompt, "completion_tokens": completion, "reasoning_tokens": reasoning}


def record(
    *,
    source: str,
    attempt: int,
    seconds: float,
    status: Optional[int],
    timed_out: bool,
    prompt_chars: int,
    usage: Any = None,
    error: str = "",
    fallback_rule: bool = False,
    thinking_disabled: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    """Append one telemetry row. Outside a scan file or answer turn nothing is recorded."""
    file_name = _scan_file.get()
    answer_id = _answer.get()
    if not file_name and not answer_id:
        return None
    tokens = usage_tokens(usage)
    row: Dict[str, Any] = {
        "file": file_name or "",
        "answer": answer_id or "",
        "channel": "scan" if file_name else "chat",
        "source": source,
        "stage": _stage.get() or "",
        "attempt": int(attempt) + int(_attempt_base.get() or 0),
        "seconds": round(float(seconds), 2),
        "status": int(status) if status is not None else None,
        "timed_out": bool(timed_out),
        "prompt_chars": int(prompt_chars),
        "prompt_tokens": tokens["prompt_tokens"],
        "completion_tokens": tokens["completion_tokens"],
        "reasoning_tokens": tokens["reasoning_tokens"],
        "fallback_rule": bool(fallback_rule),
        "thinking_disabled": thinking_disabled,
        "error": str(error or "")[:40],
    }
    with _lock:
        _rows.append(row)
        if len(_rows) > _MAX_ROWS:
            del _rows[:-_MAX_ROWS]
    return row


def mark_fallback(row: Optional[Dict[str, Any]]) -> None:
    """Mark the given row as the call that ended in the rule-based fallback."""
    if row is not None:
        row["fallback_rule"] = True


def mark_usage(row: Optional[Dict[str, Any]], usage: Any) -> None:
    if row is None:
        return
    tokens = usage_tokens(usage)
    row.update(tokens)


def snapshot(channel: Optional[str] = None) -> List[Dict[str, Any]]:
    """Recorded rows, optionally limited to one channel ("scan" or "chat")."""
    with _lock:
        rows = [dict(row) for row in _rows]
    if channel:
        return [row for row in rows if row.get("channel") == channel]
    return rows


def reset() -> None:
    with _lock:
        _rows.clear()


def summary(channel: Optional[str] = None) -> Dict[str, Any]:
    rows = snapshot(channel)
    return {
        "calls": len(rows),
        "timeouts": sum(1 for row in rows if row.get("timed_out")),
        "failed": sum(1 for row in rows if row.get("status") != 200),
        "fallback_rule": sum(1 for row in rows if row.get("fallback_rule")),
        "seconds": round(sum(float(row.get("seconds") or 0.0) for row in rows), 1),
        "sent_characters": sum(int(row.get("prompt_chars") or 0) for row in rows),
        "reasoning_tokens": sum(int(row.get("reasoning_tokens") or 0) for row in rows),
    }
