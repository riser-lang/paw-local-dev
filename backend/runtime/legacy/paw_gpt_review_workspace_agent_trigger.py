#!/usr/bin/python3
"""Thin ChatGPT Workspace Agent wake-up for ReviewBundle drain.

This process never claims ReviewBundles and never records semantic
decisions. It only reads backlog/status and, when authorized, POSTs a
wake payload so the real ChatGPT GPT Brain can use existing MCP tools
(gpt_brain_get_review_bundles / gpt_brain_record_bundle_review / post-Opus).

Cursor, Codex/Luna, Agy, Mistral, Gemini, and Claude are forbidden as
the reviewer. OPENAI_API_KEY / API billing is not required and is not
used. Distinct trigger env names keep paw-gpt-review-bundle-consumer
in WAITING_GPT so this bridge cannot duplicate local claims.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
GATEWAY = "/opt/paw-mcp-gateway"
if GATEWAY not in sys.path:
    sys.path.insert(0, GATEWAY)

from gpt_brain_common import FileLeaseLock, LockedJsonStore, text, utc_now
from paw_gpt_review_bundle import (
    OPUS_REVIEWED,
    POST_OPUS_PENDING,
    REVIEW_PENDING,
    ReviewBundleStore,
    queue_stats,
)

ACTOR = "paw-gpt-review-workspace-agent-trigger"
SCHEMA = "paw-gpt-review-workspace-agent-trigger/1"
DEFAULT_CONVERSATION_KEY = "paw-gpt-review-workspace-agent"
DEFAULT_POLL_SECONDS = 20.0
WAITING_AUTH_POLL_SECONDS = 60.0
MIN_TRIGGER_INTERVAL_SECONDS = 300.0
BATCH_LIMIT = 4
BACKOFF_SECONDS = (5, 15, 30, 60, 120, 300)
EXTERNAL_UNAVAILABLE_COOLDOWN_SECONDS = (300, 900, 1800, 3600)
HTTP_TIMEOUT_SECONDS = 30
TRIGGER_BLOCKER_UNAVAILABLE = "workspace_agent_trigger_unavailable_http_409"

AUTH_WAITING = "WAITING_AUTH"
AUTH_READY = "READY"
AUTH_REJECTED = "AUTH_REJECTED"

STATUS_WAITING_AUTH = "WAITING_AUTH"
STATUS_NO_WORK = "NO_WORK"
STATUS_DEBOUNCED = "DEBOUNCED"
STATUS_TRIGGERED = "TRIGGERED"
STATUS_IN_FLIGHT = "IN_FLIGHT"
STATUS_BACKOFF = "BACKOFF"
STATUS_AUTH_REJECTED = "AUTH_REJECTED"
STATUS_SINGLE_FLIGHT = "SINGLE_FLIGHT"

ENV_FILE = "/etc/paw-workspace-agent-trigger.env"
JSON_FILE = "/var/lib/paw-mcp-gateway/workspace_agent_trigger.json"
URL_ENV = "PAW_WORKSPACE_AGENT_TRIGGER_URL"
TOKEN_ENV = "PAW_WORKSPACE_AGENT_TRIGGER_TOKEN"
CONV_ENV = "PAW_WORKSPACE_AGENT_CONVERSATION_KEY"

# Presence-only aliases discovered from prior PAW GPT transport work.
# These are NEVER used to activate the local ReviewBundle consumer adapter.
LEGACY_URL_ENVS = ("PAW_WORKSPACE_AGENT_URL", "PAW_GPT_INVOCATION_URL", "WORKSPACE_AGENT_URL")
LEGACY_TOKEN_ENVS = ("PAW_GPT_API_KEY", "PAW_CHATGPT_API_KEY", "CHAT_GPT_API_KEY")

SECRET_KEYS = {
    "token", "secret", "authorization", "cookie", "password", "api_key",
    "apikey", "bearer", "credential", "set-cookie", "set_cookie",
    "access_token", "refresh_token", "jwt",
}


def _path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default))


def default_paths() -> dict[str, Path]:
    root = _path("PAW_GPT_REVIEW_TRIGGER_STATE_DIR", "/var/lib/paw-mcp-gateway")
    return {
        "state": root / "paw_gpt_review_workspace_agent_trigger.json",
        "lock": root / "paw_gpt_review_workspace_agent_trigger.lock",
        "brain": _path("PAW_GPT_BRAIN_STATE_PATH", "/var/lib/paw-mcp-gateway/gpt_brain_bootstrap.json"),
        "bundles": _path("PAW_GPT_REVIEW_BUNDLE_PATH", "/var/lib/paw-mcp-gateway/paw_gpt_review_bundles.json"),
        "luna_worker": root / "paw_codex_luna_bootstrap_worker.json",
        "luna_runner": root / "paw_codex_luna_bootstrap_runner.json",
        "env_file": Path(os.environ.get("PAW_WORKSPACE_AGENT_TRIGGER_ENV_FILE", ENV_FILE)),
        "json_file": Path(os.environ.get("PAW_WORKSPACE_AGENT_TRIGGER_JSON_FILE", JSON_FILE)),
    }


def _blank() -> dict[str, Any]:
    now = utc_now()
    return {
        "schema": SCHEMA,
        "status": STATUS_WAITING_AUTH,
        "auth_state": AUTH_WAITING,
        "run_id": str(uuid.uuid4()),
        "actor": ACTOR,
        "started_at": None,
        "updated_at": now,
        "conversation_key": DEFAULT_CONVERSATION_KEY,
        "chatgpt_conversation_id": None,
        "url_configured": False,
        "token_configured": False,
        "url_host": None,
        "last_trigger_at": None,
        "last_success_at": None,
        "last_error": None,
        "last_http_status": None,
        "last_reason": None,
        "trigger_in_flight": False,
        "backoff_index": 0,
        "ticks": 0,
        "triggers_attempted": 0,
        "triggers_succeeded": 0,
        "triggers_skipped_debounce": 0,
        "triggers_skipped_no_work": 0,
        "triggers_skipped_auth": 0,
        "bootstrap_completed_edges": 0,
        "last_bootstrap_status": None,
        "review_pending": None,
        "post_opus_pending": None,
        "review_bundles_claimed_by_bridge": 0,
        "formal_state_bypass": 0,
        "codex_luna_slots_consumed": 0,
        "hard_blocker": None,
        "trigger_blocker": None,
        "external_blocker_until": None,
        "consecutive_unavailable": 0,
        "one_time_user_action": one_time_user_action(),
        "events": [],
        "created_at": now,
    }


def one_time_user_action() -> str:
    return (
        "Write PAW_WORKSPACE_AGENT_TRIGGER_URL and PAW_WORKSPACE_AGENT_TRIGGER_TOKEN "
        "once into /etc/paw-workspace-agent-trigger.env (chmod 600). Use the ChatGPT "
        "Workspace Agent / Tasks trigger URL+token for the same GPT Brain already "
        "connected to PAW MCP. Do not set OPENAI_API_KEY. Do not set "
        "PAW_WORKSPACE_AGENT_URL (that would make the local consumer claim bundles). "
        "The trigger service re-reads the file on each tick; no other restart is required."
    )


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
        data["review_bundles_claimed_by_bridge"] = 0
        data["actor"] = ACTOR
        self.store.save(sanitize_for_log(data))

    def event(self, value: dict[str, Any], kind: str, **details: Any) -> None:
        events = value.setdefault("events", [])
        events.append(sanitize_for_log({"at": utc_now(), "type": kind, **details}))
        if len(events) > 200:
            value["events"] = events[-200:]
        value["updated_at"] = utc_now()


def _load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        return {}
    return out


def _load_json_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def safe_url_host(url: str) -> str | None:
    raw = text(url)
    if not raw:
        return None
    parsed = urlparse(raw)
    host = parsed.netloc or parsed.path.split("/")[0]
    return host or None


def redact_url(url: str) -> str | None:
    raw = text(url)
    if not raw:
        return None
    parsed = urlparse(raw)
    path = parsed.path or ""
    parts = [p for p in path.split("/") if p]
    shown = "/" + "/".join(parts[:2]) if parts else ""
    if len(parts) > 2:
        shown += "/…"
    host = parsed.netloc or "redacted"
    scheme = parsed.scheme or "https"
    return f"{scheme}://{host}{shown}"


def _looks_secret_key(key: str) -> bool:
    lowered = str(key or "").casefold().replace("-", "_")
    if lowered in SECRET_KEYS:
        return True
    return lowered.endswith(("_token", "_secret", "_password", "_api_key"))


def sanitize_for_log(value: Any) -> Any:
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if _looks_secret_key(str(key)):
                out[str(key)] = "present" if text(item) else None
            elif str(key).casefold() in {"url", "trigger_url"}:
                out[str(key)] = redact_url(str(item) if item else "")
            else:
                out[str(key)] = sanitize_for_log(item)
        return out
    if isinstance(value, list):
        return [sanitize_for_log(item) for item in value]
    if isinstance(value, str):
        lowered = value.casefold()
        if "bearer " in lowered or "sk-" in lowered:
            return "[redacted]"
    return value


def load_trigger_config(paths: Mapping[str, Path] | None = None) -> dict[str, Any]:
    """Presence-only config. Never returns the token value to callers that log."""
    paths = paths or default_paths()
    file_vals = _load_env_file(paths["env_file"])
    json_vals = _load_json_file(paths["json_file"])
    url = text(
        os.environ.get(URL_ENV)
        or file_vals.get(URL_ENV)
        or json_vals.get("url")
        or json_vals.get(URL_ENV)
    )
    token = text(
        os.environ.get(TOKEN_ENV)
        or file_vals.get(TOKEN_ENV)
        or json_vals.get("token")
        or json_vals.get(TOKEN_ENV)
    )
    conversation_key = text(
        os.environ.get(CONV_ENV)
        or file_vals.get(CONV_ENV)
        or json_vals.get("conversation_key")
        or json_vals.get(CONV_ENV)
    ) or DEFAULT_CONVERSATION_KEY
    auth_state = AUTH_READY if (url and token) else AUTH_WAITING
    return {
        "url": url,
        "token": token,
        "conversation_key": conversation_key,
        "url_configured": bool(url),
        "token_configured": bool(token),
        "auth_state": auth_state,
        "url_host": safe_url_host(url),
        "url_redacted": redact_url(url),
        "env_file_exists": paths["env_file"].is_file(),
        "json_file_exists": paths["json_file"].is_file(),
        "legacy_consumer_url_present": any(text(os.environ.get(name)) for name in LEGACY_URL_ENVS),
        "openai_api_key_present": bool(text(os.environ.get("OPENAI_API_KEY"))),
        "one_time_user_action": one_time_user_action(),
    }


def public_config(config: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "auth_state": config.get("auth_state"),
        "url_configured": bool(config.get("url_configured")),
        "token_configured": bool(config.get("token_configured")),
        "url_host": config.get("url_host"),
        "url_redacted": config.get("url_redacted"),
        "conversation_key": config.get("conversation_key") or DEFAULT_CONVERSATION_KEY,
        "env_file_exists": bool(config.get("env_file_exists")),
        "json_file_exists": bool(config.get("json_file_exists")),
        "legacy_consumer_url_present": bool(config.get("legacy_consumer_url_present")),
        "openai_api_key_present": bool(config.get("openai_api_key_present")),
        "one_time_user_action": config.get("one_time_user_action") or one_time_user_action(),
    }


def review_prompt(*, conversation_key: str, backlog: Mapping[str, Any], reason: str) -> str:
    pending = int(backlog.get("review_pending") or 0)
    post = int(backlog.get("post_opus_pending") or 0)
    return (
        "You are the PAW GPT Brain reviewer (real ChatGPT). Wake and drain one bounded "
        "ReviewBundle batch through existing MCP tools. Do not substitute Cursor, Codex, "
        "Luna, Agy, Mistral, Gemini, or Claude. Do not write Formal State.\n"
        f"conversation_key={conversation_key}\n"
        f"reason={reason}\n"
        f"review_pending={pending} post_opus_pending={post} batch_limit={BATCH_LIMIT}\n"
        "Required MCP flow:\n"
        "1. gpt_brain_review_queue_stats (read-only).\n"
        f"2. gpt_brain_get_review_bundles limit={BATCH_LIMIT} worker_id=chatgpt-main-review.\n"
        "3. Genuine GPT decision per bundle: CLEAR / NO_ACTION / OPUS_REQUIRED / "
        "USER_DECISION_REQUIRED / INSUFFICIENT_EVIDENCE.\n"
        "4. gpt_brain_record_bundle_review with the returned claim_id. CLEAR remains ChatGPT-owned.\n"
        "5. If post-Opus work exists: gpt_brain_get_post_opus_work limit=1 worker_id=chatgpt-post-opus "
        "then gpt_brain_record_post_opus_bundle.\n"
        "6. OPUS_REQUIRED goes to the existing Opus dispatcher (claude-opus-4-6-thinking). "
        "Do not impersonate Opus.\n"
        "7. Stop cleanly after this bounded batch. Do not keep an unbounded session."
    )


def build_trigger_payload(
    url: str,
    *,
    conversation_key: str,
    prompt: str,
    reason: str,
    backlog: Mapping[str, Any],
    conversation_id: str | None = None,
) -> dict[str, Any]:
    parsed = urlparse(url)
    path = (parsed.path or "").casefold()
    host = (parsed.netloc or "").casefold()
    common = {
        "conversation_key": conversation_key,
        "reason": reason,
        "review_pending": int(backlog.get("review_pending") or 0),
        "post_opus_pending": int(backlog.get("post_opus_pending") or 0),
        "batch_limit": BATCH_LIMIT,
        "kind": "paw_gpt_review_wake",
    }
    if host == "api.chatgpt.com" and path.startswith("/v1/workspace_agents/") and path.endswith("/trigger"):
        # Official Workspace Agent trigger contract. Model and reasoning are
        # owned by the published Agent configuration, not this wake payload.
        return {"input": prompt}
    if "scheduled_tasks" in path:
        return {**common, "action": "run_now", "instructions": prompt, "prompt": prompt}
    if "backend-api/conversation" in path or path.rstrip("/").endswith("/conversation"):
        payload = {
            **common,
            "action": "next",
            "messages": [{
                "id": str(uuid.uuid4()),
                "author": {"role": "user"},
                "content": {"content_type": "text", "parts": [prompt]},
            }],
            "model": "auto",
            "timezone_offset_min": -480,
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id
        return payload
    if "chatgpt.com" in host or "chat.openai.com" in host or "/backend-api/aip/" in path:
        return {
            **common,
            "prompt": prompt,
            "message": prompt,
            "text": prompt,
            "conversation_id": conversation_id or conversation_key,
        }
    return {
        **common,
        "prompt": prompt,
        "message": prompt,
        "text": prompt,
        "conversation_id": conversation_id or conversation_key,
        "model_identity_required": "ChatGPT GPT Brain",
        "forbidden_substitutes": ["cursor", "codex", "luna", "agy", "mistral", "gemini", "claude"],
    }


def default_http_post(url: str, payload: Mapping[str, Any], *, token: str, timeout: int = HTTP_TIMEOUT_SECONDS) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "paw-gpt-review-workspace-agent-trigger/1"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(url, data=body, method="POST", headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = int(getattr(response, "status", 200) or 200)
    except HTTPError as exc:
        try:
            error_body = exc.read(4096).decode("utf-8", errors="replace").strip()
        except Exception:
            error_body = ""
        detail = error_body[:1000] if error_body else f"HTTP {exc.code}"
        raise TriggerHttpError(int(exc.code), f"HTTP {exc.code}: {detail}") from None
    except URLError as exc:
        raise TriggerHttpError(0, "trigger endpoint unreachable") from None
    parsed: Any = {}
    if raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = {"ok": True, "non_json": True}
    if not isinstance(parsed, dict):
        parsed = {"ok": True, "http_status": status}
    parsed["http_status"] = status
    return parsed


class TriggerHttpError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def read_backlog(store: ReviewBundleStore) -> dict[str, Any]:
    """Read-only queue snapshot. Never claims."""
    stats = queue_stats(store=store)
    data = store.load()
    post = 0
    claimed = 0
    for bundle in (data.get("bundles") or {}).values():
        if not isinstance(bundle, Mapping):
            continue
        if text(bundle.get("state")) in {POST_OPUS_PENDING, OPUS_REVIEWED}:
            post += 1
        if isinstance(bundle.get("claim"), dict) or isinstance(bundle.get("post_opus_claim"), dict):
            claimed += 1
    pending = int(stats.get("review_pending") or 0)
    main_review_enabled = bool(stats.get("authoritative_review_drain_enabled"))
    return {
        "review_pending": pending,
        "post_opus_pending": post,
        "opus_required": int(stats.get("opus_required") or 0),
        "clear": int(stats.get("clear") or 0),
        "total_bundles": int(stats.get("total_bundles") or 0),
        "bundles_currently_claimed": claimed,
        # Keep review_pending truthful, but do not wake the ordinary main
        # review agent for a retired historical queue.  Post-Opus remains an
        # independent live path and still wakes the agent when needed.
        "has_work": (pending > 0 and main_review_enabled) or post > 0,
        "authoritative_review_drain_enabled": main_review_enabled,
        "authoritative_review_drain_blocked_reason": stats.get("authoritative_review_drain_blocked_reason"),
        "review_bundles_claimed_by_bridge": 0,
        "formal_state_bypass": 0,
    }


def _json_status(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return text(payload.get("status")).upper() or None


def read_source_bootstrap_status(paths: Mapping[str, Path] | None = None) -> dict[str, Any]:
    paths = paths or default_paths()
    worker = _json_status(paths["luna_worker"])
    runner = _json_status(paths["luna_runner"])
    brain_status = None
    brain = paths["brain"]
    if brain.is_file():
        try:
            payload = json.loads(brain.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                brain_status = text((payload.get("bootstrap") or {}).get("status")).upper() or None
        except (OSError, ValueError, MemoryError):
            brain_status = None
    statuses = [item for item in (brain_status, worker, runner) if item]
    completed = any(item == "COMPLETED" for item in statuses)
    return {
        "brain_status": brain_status,
        "luna_worker_status": worker,
        "luna_runner_status": runner,
        "completed": completed,
        "status": "COMPLETED" if completed else (brain_status or worker or runner),
    }


def _parse_utc(value: Any) -> datetime | None:
    raw = text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _within_debounce(last_trigger_at: Any, *, now: datetime | None = None, interval: float = MIN_TRIGGER_INTERVAL_SECONDS) -> bool:
    previous = _parse_utc(last_trigger_at)
    if previous is None:
        return False
    current = now or datetime.now(timezone.utc)
    return current < previous + timedelta(seconds=float(interval))


def decide_trigger(
    *,
    auth_state: str,
    backlog: Mapping[str, Any],
    bootstrap: Mapping[str, Any],
    last_bootstrap_status: str | None,
    last_trigger_at: str | None,
    trigger_in_flight: bool,
    now: datetime | None = None,
    external_blocker_until: str | None = None,
) -> dict[str, Any]:
    if trigger_in_flight:
        return {"action": "skip", "status": STATUS_SINGLE_FLIGHT, "reason": "single_flight"}
    if auth_state != AUTH_READY:
        return {"action": "skip", "status": STATUS_WAITING_AUTH, "reason": "waiting_auth"}
    current = now or datetime.now(timezone.utc)
    blocker_until = _parse_utc(external_blocker_until)
    if blocker_until is not None and current < blocker_until:
        return {
            "action": "skip",
            "status": STATUS_BACKOFF,
            "reason": "external_trigger_unavailable",
            "trigger_blocker": TRIGGER_BLOCKER_UNAVAILABLE,
        }
    bootstrap_edge = (
        text(bootstrap.get("status")).upper() == "COMPLETED"
        and text(last_bootstrap_status).upper() not in {"", "COMPLETED"}
        and bool(backlog.get("has_work"))
    )
    if not backlog.get("has_work") and not bootstrap_edge:
        return {"action": "skip", "status": STATUS_NO_WORK, "reason": "no_review_work"}
    reason = "bootstrap_completed" if bootstrap_edge else (
        "post_opus_pending" if int(backlog.get("post_opus_pending") or 0) > 0
        else (
            "historical_main_review_retired"
            if not backlog.get("authoritative_review_drain_enabled")
            else "review_pending"
        )
    )
    if bootstrap_edge:
        return {"action": "trigger", "status": STATUS_TRIGGERED, "reason": reason, "bootstrap_edge": True}
    if _within_debounce(last_trigger_at, now=now):
        return {"action": "skip", "status": STATUS_DEBOUNCED, "reason": "debounce_5m"}
    return {"action": "trigger", "status": STATUS_TRIGGERED, "reason": reason, "bootstrap_edge": False}


def _extract_conversation_id(payload: Mapping[str, Any] | None) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    for key in ("conversation_id", "conversationId", "id"):
        value = text(payload.get(key))
        if value and len(value) < 200:
            return value
    nested = payload.get("conversation")
    if isinstance(nested, Mapping):
        return text(nested.get("id")) or None
    return None


def _mark_external_unavailable(state: dict[str, Any], *, status: int, message: str, now: datetime | None = None) -> None:
    current = now or datetime.now(timezone.utc)
    consecutive = int(state.get("consecutive_unavailable") or 0) + 1
    state["consecutive_unavailable"] = consecutive
    idx = min(consecutive - 1, len(EXTERNAL_UNAVAILABLE_COOLDOWN_SECONDS) - 1)
    until = current + timedelta(seconds=float(EXTERNAL_UNAVAILABLE_COOLDOWN_SECONDS[idx]))
    state["status"] = STATUS_BACKOFF
    state["last_http_status"] = status
    state["last_error"] = message[:300]
    state["hard_blocker"] = (
        "ChatGPT Workspace Agent trigger is not currently available (HTTP 409). "
        "Durable queues preserved. Manual/scheduled GPT patrol can recover."
    )
    state["trigger_blocker"] = TRIGGER_BLOCKER_UNAVAILABLE
    state["external_blocker_until"] = until.isoformat()
    state["backoff_index"] = min(int(state.get("backoff_index") or 0) + 1, len(BACKOFF_SECONDS) - 1)


def tick_once(
    *,
    store: ReviewBundleStore | None = None,
    ledger: Ledger | None = None,
    paths: Mapping[str, Path] | None = None,
    config: Mapping[str, Any] | None = None,
    http_post: Callable[..., dict[str, Any]] | None = None,
    bootstrap_reader: Callable[[], Mapping[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    paths = paths or default_paths()
    ledger = ledger or Ledger(paths["state"])
    store = store or ReviewBundleStore(paths["bundles"])
    state = ledger.load()
    cfg = dict(config or load_trigger_config(paths))
    backlog = read_backlog(store)
    bootstrap = dict(bootstrap_reader() if bootstrap_reader is not None else read_source_bootstrap_status(paths))
    decision = decide_trigger(
        auth_state=text(cfg.get("auth_state")) or AUTH_WAITING,
        backlog=backlog,
        bootstrap=bootstrap,
        last_bootstrap_status=text(state.get("last_bootstrap_status")) or None,
        last_trigger_at=state.get("last_trigger_at"),
        trigger_in_flight=bool(state.get("trigger_in_flight")),
        now=now,
        external_blocker_until=text(state.get("external_blocker_until")) or None,
    )
    state["ticks"] = int(state.get("ticks") or 0) + 1
    state["auth_state"] = cfg.get("auth_state")
    state["url_configured"] = bool(cfg.get("url_configured"))
    state["token_configured"] = bool(cfg.get("token_configured"))
    state["url_host"] = cfg.get("url_host")
    state["conversation_key"] = cfg.get("conversation_key") or DEFAULT_CONVERSATION_KEY
    state["review_pending"] = backlog.get("review_pending")
    state["post_opus_pending"] = backlog.get("post_opus_pending")
    state["one_time_user_action"] = one_time_user_action()
    state["review_bundles_claimed_by_bridge"] = 0
    if text(bootstrap.get("status")).upper() == "COMPLETED" and text(state.get("last_bootstrap_status")).upper() not in {"", "COMPLETED"}:
        state["bootstrap_completed_edges"] = int(state.get("bootstrap_completed_edges") or 0) + 1
    if decision["action"] != "trigger":
        state["status"] = decision["status"]
        state["last_reason"] = decision["reason"]
        if decision["status"] == STATUS_WAITING_AUTH:
            state["triggers_skipped_auth"] = int(state.get("triggers_skipped_auth") or 0) + 1
            state["hard_blocker"] = "ChatGPT Workspace Agent URL/token not configured"
        elif decision["status"] == STATUS_NO_WORK:
            state["triggers_skipped_no_work"] = int(state.get("triggers_skipped_no_work") or 0) + 1
            state["hard_blocker"] = None
        elif decision["status"] == STATUS_DEBOUNCED:
            state["triggers_skipped_debounce"] = int(state.get("triggers_skipped_debounce") or 0) + 1
        elif decision["status"] == STATUS_BACKOFF and decision.get("reason") == "external_trigger_unavailable":
            state["hard_blocker"] = (
                "ChatGPT Workspace Agent trigger is not currently available (HTTP 409). "
                "Durable queues preserved. Manual/scheduled GPT patrol can recover."
            )
            state["trigger_blocker"] = TRIGGER_BLOCKER_UNAVAILABLE
        state["last_bootstrap_status"] = bootstrap.get("status")
        ledger.event(state, "tick", action="skip", reason=decision["reason"], review_pending=backlog.get("review_pending"))
        ledger.save(state)
        return {
            "status": state["status"],
            "auth_state": state["auth_state"],
            "reason": decision["reason"],
            "review_pending": backlog.get("review_pending"),
            "post_opus_pending": backlog.get("post_opus_pending"),
            "review_bundles_claimed_by_bridge": 0,
            "formal_state_bypass": 0,
            "triggered": False,
            "trigger_blocker": state.get("trigger_blocker"),
            "external_blocker_until": state.get("external_blocker_until"),
        }

    poster = http_post or default_http_post
    prompt = review_prompt(conversation_key=state["conversation_key"], backlog=backlog, reason=decision["reason"])
    payload = build_trigger_payload(
        text(cfg.get("url")),
        conversation_key=state["conversation_key"],
        prompt=prompt,
        reason=decision["reason"],
        backlog=backlog,
        conversation_id=text(state.get("chatgpt_conversation_id")) or None,
    )
    state["status"] = STATUS_IN_FLIGHT
    state["trigger_in_flight"] = True
    state["last_reason"] = decision["reason"]
    state["last_trigger_at"] = utc_now()
    state["triggers_attempted"] = int(state.get("triggers_attempted") or 0) + 1
    ledger.save(state)
    try:
        response = poster(text(cfg.get("url")), payload, token=text(cfg.get("token")), timeout=HTTP_TIMEOUT_SECONDS)
        http_status = int((response or {}).get("http_status") or 200)
        if http_status in {401, 403}:
            state["status"] = STATUS_AUTH_REJECTED
            state["auth_state"] = AUTH_REJECTED
            state["last_http_status"] = http_status
            state["last_error"] = "Workspace Agent rejected authorization"
            state["hard_blocker"] = state["last_error"]
            state["backoff_index"] = min(int(state.get("backoff_index") or 0) + 1, len(BACKOFF_SECONDS) - 1)
            ledger.event(state, "auth_rejected", http_status=http_status)
        elif http_status == 409 or "not currently available" in str((response or {}).get("error") or "").lower():
            _mark_external_unavailable(state, status=http_status, message=f"HTTP {http_status}: workspace agent trigger unavailable", now=now)
            ledger.event(state, "trigger_error", error=state["last_error"], http_status=http_status)
        else:
            conv = _extract_conversation_id(response)
            if conv:
                state["chatgpt_conversation_id"] = conv
            state["status"] = STATUS_TRIGGERED
            state["auth_state"] = AUTH_READY
            state["last_success_at"] = utc_now()
            state["last_http_status"] = http_status
            state["last_error"] = None
            state["hard_blocker"] = None
            state["trigger_blocker"] = None
            state["external_blocker_until"] = None
            state["consecutive_unavailable"] = 0
            state["backoff_index"] = 0
            state["triggers_succeeded"] = int(state.get("triggers_succeeded") or 0) + 1
            ledger.event(state, "triggered", reason=decision["reason"], http_status=http_status, bootstrap_edge=bool(decision.get("bootstrap_edge")))
    except TriggerHttpError as exc:
        if exc.status in {401, 403}:
            state["status"] = STATUS_AUTH_REJECTED
            state["auth_state"] = AUTH_REJECTED
            state["hard_blocker"] = "Workspace Agent rejected authorization"
            state["last_error"] = str(exc)[:300]
            state["last_http_status"] = exc.status or None
            state["backoff_index"] = min(int(state.get("backoff_index") or 0) + 1, len(BACKOFF_SECONDS) - 1)
        elif exc.status == 409 or "not currently available" in str(exc).lower():
            _mark_external_unavailable(state, status=exc.status or 409, message=str(exc), now=now)
        else:
            state["status"] = STATUS_BACKOFF
            state["hard_blocker"] = str(exc)[:300]
            state["last_error"] = str(exc)[:300]
            state["last_http_status"] = exc.status or None
            state["backoff_index"] = min(int(state.get("backoff_index") or 0) + 1, len(BACKOFF_SECONDS) - 1)
        ledger.event(state, "trigger_error", error=state["last_error"], http_status=exc.status)
    except Exception as exc:
        state["status"] = STATUS_BACKOFF
        state["last_error"] = str(exc)[:300]
        state["hard_blocker"] = state["last_error"]
        state["backoff_index"] = min(int(state.get("backoff_index") or 0) + 1, len(BACKOFF_SECONDS) - 1)
        ledger.event(state, "trigger_error", error=state["last_error"])
    finally:
        state["trigger_in_flight"] = False
        state["last_bootstrap_status"] = bootstrap.get("status")
        state["review_bundles_claimed_by_bridge"] = 0
        ledger.save(state)
    return {
        "status": state["status"],
        "auth_state": state["auth_state"],
        "reason": decision["reason"],
        "review_pending": backlog.get("review_pending"),
        "post_opus_pending": backlog.get("post_opus_pending"),
        "review_bundles_claimed_by_bridge": 0,
        "formal_state_bypass": 0,
        "triggered": state.get("status") == STATUS_TRIGGERED,
        "http_status": state.get("last_http_status"),
        "trigger_blocker": state.get("trigger_blocker"),
        "external_blocker_until": state.get("external_blocker_until"),
    }


def run_forever(*, poll_seconds: float = DEFAULT_POLL_SECONDS) -> None:
    paths = default_paths()
    ledger = Ledger(paths["state"])
    store = ReviewBundleStore(paths["bundles"])
    state = ledger.load()
    lock = FileLeaseLock(paths["lock"], stale_seconds=900)
    lock.acquire(run_id=str(state.get("run_id") or uuid.uuid4()), actor=ACTOR)
    try:
        state["started_at"] = state.get("started_at") or utc_now()
        ledger.event(state, "service_started", actor=ACTOR)
        ledger.save(state)
        while True:
            try:
                result = tick_once(store=store, ledger=ledger, paths=paths)
                lock.heartbeat()
                state = ledger.load()
                if result.get("status") == STATUS_WAITING_AUTH:
                    time.sleep(max(WAITING_AUTH_POLL_SECONDS, float(poll_seconds)))
                elif result.get("status") == STATUS_BACKOFF:
                    state = ledger.load()
                    until = _parse_utc(state.get("external_blocker_until"))
                    if until is not None:
                        delay = max(1.0, (until - datetime.now(timezone.utc)).total_seconds())
                    else:
                        delay = BACKOFF_SECONDS[min(int(state.get("backoff_index") or 0), len(BACKOFF_SECONDS) - 1)]
                    time.sleep(max(float(poll_seconds), float(delay)))
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
    backlog = read_backlog(ReviewBundleStore(paths["bundles"]))
    cfg = public_config(load_trigger_config(paths))
    bootstrap = read_source_bootstrap_status(paths)
    return sanitize_for_log({
        "trigger": ledger,
        "backlog": backlog,
        "config": cfg,
        "bootstrap": bootstrap,
        "review_bundles_claimed_by_bridge": 0,
        "formal_state_bypass": 0,
        "codex_luna_slots_consumed": 0,
    })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    args = parser.parse_args(argv)
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
