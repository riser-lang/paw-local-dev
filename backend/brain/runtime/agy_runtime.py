"""Agy Runtime Adapter — Production Bridge to Antigravity CLI (agy).

Web path: sudo -n /usr/local/sbin/paw-agy-web (runs as root, HOME=/root)
R13E path: direct agy_operator.py (already root)

Safety:
- Web path uses helper with strict contract (no --dangerously-skip-permissions)
- health_check is cheap (no inference tokens consumed)
- Secret redaction on all output
- Model allowlist from real `agy models` output
- Session resume via --conversation ID
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from brain.runtime import agy_chat_history as hist
from brain.runtime import agy_context_budget as BUDGET

_LOGGER = logging.getLogger(__name__)

AGY_EXECUTABLE = "/usr/local/bin/agy"
AGY_WEB_HELPER = "/usr/local/sbin/paw-agy-web"
DEFAULT_MODEL = "gemini-3.8-flash-high"
DEFAULT_TIMEOUT_SECONDS = 300
MAX_TIMEOUT_SECONDS = 1800
MIN_TIMEOUT_SECONDS = 30

SESSION_DIR = Path("/var/lib/paw-agent-jobs/agy")

WEB_MODES = {"chat", "extract", "analyze", "plan"}
R13E_MODES = {"chat", "extract", "analyze", "plan", "exec"}

SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_\-:]{1,128}$")
CONVERSATION_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

_model_cache: list[str] | None = None
_model_cache_ts: float = 0
_MODEL_CACHE_TTL = 300

# Track last inference result for cheap health check
_last_inference_status: str = "UNKNOWN"
_last_inference_at: str = ""


@dataclass
class AgyResult:
    status: str = "UNKNOWN"
    response: str = ""
    conversation_id: str = ""
    model: str = DEFAULT_MODEL
    duration_seconds: float = 0.0
    num_turns: int = 0
    usage: dict = field(default_factory=dict)
    error: str = ""
    exit_code: int = 0

    def to_dict(self) -> dict:
        return {
            "status": self.status, "response": self.response,
            "conversation_id": self.conversation_id, "model": self.model,
            "duration_seconds": round(self.duration_seconds, 3),
            "num_turns": self.num_turns, "usage": self.usage,
            "error": self.error, "exit_code": self.exit_code,
        }


@dataclass
class AgySession:
    session_id: str
    conversation_id: str = ""
    model: str = DEFAULT_MODEL
    mode: str = "chat"
    project_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = ""
    message_count: int = 0
    # Bounded V2 continuity metadata only; never model reasoning or prompts.
    v2_context: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id, "conversation_id": self.conversation_id,
            "model": self.model, "mode": self.mode,
            "project_id": self.project_id or None,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "message_count": self.message_count,
            "v2_context": self.v2_context,
        }


_SECRET_PATTERNS = [
    (re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]+", re.IGNORECASE), r"\1<REDACTED>"),
    (re.compile(r"(api[_-]?key[\"\'\s:=]+)[^\s,;]+", re.IGNORECASE), r"\1<REDACTED>"),
    (re.compile(r"(token[\"\'\s:=]+)[^\s,;]+", re.IGNORECASE), r"\1<REDACTED>"),
    (re.compile(r"(secret[\"\'\s:=]+)[^\s,;]+", re.IGNORECASE), r"\1<REDACTED>"),
    (re.compile(r"(password[\"\'\s:=]+)[^\s,;]+", re.IGNORECASE), r"\1<REDACTED>"),
]


def _redact(text: str) -> str:
    result = text or ""
    for pattern, replacement in _SECRET_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


class AgyRuntimeAdapter:
    def __init__(self, timeout: int = DEFAULT_TIMEOUT_SECONDS):
        self.timeout = min(max(timeout, MIN_TIMEOUT_SECONDS), MAX_TIMEOUT_SECONDS)

    def health_check(self) -> dict:
        """Cheap health check — no inference tokens consumed."""
        global _last_inference_status, _last_inference_at
        installed = os.path.isfile(AGY_EXECUTABLE) and os.access(AGY_EXECUTABLE, os.X_OK)
        helper_exists = os.path.isfile(AGY_WEB_HELPER) and os.access(AGY_WEB_HELPER, os.X_OK)
        version = "unknown"
        if installed:
            try:
                result = subprocess.run([AGY_EXECUTABLE, "--version"], shell=False, capture_output=True, text=True, timeout=10)
                version = (result.stdout or result.stderr or "").strip()
            except Exception:
                pass
        models = self.list_models() if installed else []
        session_writable = SESSION_DIR.is_dir() and os.access(SESSION_DIR, os.W_OK)
        return {
            "installed": installed,
            "binary": AGY_EXECUTABLE,
            "version": version,
            "helper_exists": helper_exists,
            "authenticated": installed and len(models) > 0,
            "healthy": installed and len(models) > 0 and session_writable,
            "default_model": DEFAULT_MODEL,
            "available_model_count": len(models),
            "eligible": "unknown" if _last_inference_status != "SUCCESS" else True,
            "last_inference_status": _last_inference_status,
            "last_inference_at": _last_inference_at,
            "session_dir_writable": session_writable,
        }

    def inference_smoke_test(self) -> dict:
        """Explicit inference smoke test — consumes tokens. Use sparingly."""
        global _last_inference_status, _last_inference_at
        result = self._invoke_web(prompt="Reply OK", model=DEFAULT_MODEL, timeout=30)
        _last_inference_status = result.status
        _last_inference_at = datetime.now(timezone.utc).isoformat()
        return {"smoke_status": result.status, "eligible": result.status == "SUCCESS"}

    def list_models(self) -> list[dict]:
        global _model_cache, _model_cache_ts
        now = time.time()
        if _model_cache and (now - _model_cache_ts) < _MODEL_CACHE_TTL:
            return [{"model_id": m, "display_name": m} for m in _model_cache]
        models: list[dict] = []
        try:
            result = subprocess.run([AGY_EXECUTABLE, "models"], shell=False, capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                for line in (result.stdout or "").splitlines():
                    line = line.strip()
                    if not line or line.startswith("Fetching"):
                        continue
                    parts = line.split(None, 1)
                    if parts:
                        models.append({"model_id": parts[0], "display_name": parts[1] if len(parts) > 1 else parts[0]})
        except Exception:
            pass

        # paw-actions runs as pawops whose HOME is intentionally /nonexistent,
        # so the raw Agy CLI cannot enumerate models there.  The Web inference
        # path already uses the root-owned, sudo-allowlisted paw-agy-web helper;
        # use that same helper as the authoritative fallback for model discovery.
        if not models:
            try:
                helper = subprocess.run(
                    ["sudo", "-n", AGY_WEB_HELPER, "models"],
                    shell=False, capture_output=True, text=True, timeout=20,
                )
                if helper.returncode == 0 and helper.stdout.strip():
                    data = json.loads(helper.stdout)
                    models = [
                        {"model_id": str(item.get("model_id")), "display_name": str(item.get("display_name") or item.get("model_id"))}
                        for item in (data.get("models") or []) if item.get("model_id")
                    ]
            except Exception:
                pass

        _model_cache = [m["model_id"] for m in models]
        _model_cache_ts = now
        return models

    def _valid_models(self) -> set[str]:
        return {m["model_id"] for m in self.list_models()}

    def create_session(
        self,
        session_id: str,
        model: str = DEFAULT_MODEL,
        mode: str = "chat",
        project_id: str | None = None,
    ) -> AgySession:
        if not SESSION_ID_RE.match(session_id):
            raise ValueError("Invalid session_id")
        if model not in self._valid_models() and model != DEFAULT_MODEL:
            raise ValueError(f"Invalid model: {model}")
        if mode not in WEB_MODES and mode not in R13E_MODES:
            raise ValueError(f"Invalid mode: {mode}")
        if project_id:
            self._validate_project_id(project_id)
        session = AgySession(session_id=session_id, model=model, mode=mode, project_id=project_id or "")
        self._save_session(session)
        return session

    def _validate_project_id(self, project_id: str) -> None:
        try:
            from workspace_api import repository as ws_repo
            if ws_repo.project_context(project_id) is None:
                raise ValueError(f"Project not found: {project_id}")
        except ImportError:
            pass

    def get_session(self, session_id: str) -> Optional[AgySession]:
        return self._load_session(session_id)

    def _bounded_context_prompt(self, session: AgySession,
                                message: str) -> tuple[str, dict]:
        """Bound the model-side context, rotating the conversation if needed.

        Rolling history retention bounds what the *user* can scroll back to.
        It does not bound what the *model* carries: the Antigravity thread named
        by ``conversation_id`` grows for as long as it is resumed, so token and
        quota cost grow without limit.  Rotation is the half that actually
        bounds it (spec §13-§17).

        On rotation the old ``conversation_id`` is dropped, so the next call
        starts a fresh Antigravity conversation carrying only a deterministic
        summary plus the most recent complete rounds.
        """
        ctx = dict(session.v2_context or {})
        state = BUDGET.rotation_state(ctx)
        try:
            transcript = hist.list_messages(session.session_id)
        except Exception:                                          # noqa: BLE001
            _LOGGER.warning("context rotation: transcript unavailable",
                            exc_info=True)
            transcript = []
        # The facade records the current user turn as pending before calling
        # in; it belongs to *this* turn, not to the history being summarised.
        history = [m for m in transcript
                   if str(m.get("status")) != hist.STATUS_PENDING]
        rounds = BUDGET.count_rounds(history)
        carried = int(ctx.get("turn_index") or rounds)
        # Epoch-relative on purpose.  A whole-transcript measure never shrinks
        # after a rotation, so it would stay above the budget and rotate the
        # conversation on every single subsequent turn.
        epoch_chars = BUDGET.estimate_context_chars(
            BUDGET.epoch_messages(history, state["epoch_start_turn"]))
        rotate, reason = BUDGET.should_rotate(
            rounds=carried,
            epoch_start_turn=state["epoch_start_turn"],
            estimated_chars=epoch_chars,
        )
        if not rotate:
            return message, {"rotated": False,
                             "rounds_in_epoch": carried - state["epoch_start_turn"]}

        summary = BUDGET.build_summary(history, ctx)
        recent = BUDGET.split_recent_rounds(history)
        # The block is prepended to an already-rendered PAW prompt, so it has to
        # fit in whatever is left of the helper's hard limit — not in the prompt
        # allowance.  Sizing it against the allowance is what produced
        # "Message too long (max 8000)" on rotation turns.
        allowance = BUDGET.carry_allowance(len(message))
        block = BUDGET.render_context_block(summary, recent,
                                            epoch=state["epoch"] + 1,
                                            max_chars=allowance)
        if not block:
            _LOGGER.warning(
                "context rotation: no room to carry context forward "
                "(session=%s prompt_chars=%s allowance=%s)",
                session.session_id, len(message), allowance)
        old_conversation_id = session.conversation_id
        # Dropping the id is what makes the next call a NEW conversation.
        # Resuming it "just this once" would defeat the entire mechanism.
        session.conversation_id = ""
        ctx["context_epoch"] = state["epoch"] + 1
        ctx["epoch_start_turn"] = carried
        ctx["rotation_count"] = state["rotation_count"] + 1
        ctx["context_summary"] = summary
        ctx["rotated_at"] = datetime.now(timezone.utc).isoformat()
        ctx["rotation_reason"] = reason
        session.v2_context = ctx
        info = {
            "rotated": True,
            "reason": reason,
            "epoch": ctx["context_epoch"],
            "old_conversation_id": old_conversation_id,
            "summary_chars": len(summary),
            "block_chars": len(block),
            "allowance_chars": allowance,
            "prompt_chars": len(message),
            "recent_rounds": len(BUDGET.user_messages(recent)),
            "rounds_in_epoch": carried - state["epoch_start_turn"],
        }
        _LOGGER.info(
            "context rotation: session=%s epoch=%s reason=%s summary_chars=%s "
            "recent_rounds=%s old_conversation_id=%s",
            session.session_id, info["epoch"], reason, info["summary_chars"],
            info["recent_rounds"], old_conversation_id or "<none>")
        final = (block + message) if block else message
        if len(final) > BUDGET.HARD_MESSAGE_LIMIT:
            # Unreachable while carry_allowance() holds, but a rejected turn is
            # far worse than a turn with no carried context, so degrade loudly
            # instead of letting the helper fail the whole request.
            _LOGGER.error(
                "context rotation: combined message still too long "
                "(session=%s total=%s limit=%s); dropping the carried block",
                session.session_id, len(final), BUDGET.HARD_MESSAGE_LIMIT)
            info["block_chars"] = 0
            info["block_dropped"] = True
            final = message
        return final, info

    def send_message(self, session_id: str, message: str, mode: Optional[str] = None,
                      model: Optional[str] = None, timeout: Optional[int] = None) -> AgyResult:
        global _last_inference_status, _last_inference_at
        session = self._load_session(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")
        eff_mode = mode or session.mode
        eff_model = model or session.model
        eff_timeout = timeout or self.timeout
        if eff_model not in self._valid_models() and eff_model != DEFAULT_MODEL:
            raise ValueError(f"Invalid model: {eff_model}")
        prompt, rotation = self._bounded_context_prompt(session, message)
        result = self._invoke_web(
            prompt=prompt, model=eff_model, mode=eff_mode,
            conversation_id=session.conversation_id, timeout=eff_timeout,
        )
        _last_inference_status = result.status
        _last_inference_at = datetime.now(timezone.utc).isoformat()
        if result.conversation_id and result.conversation_id != session.conversation_id:
            session.conversation_id = result.conversation_id
        session.updated_at = datetime.now(timezone.utc).isoformat()
        session.message_count += 1
        if mode: session.mode = mode
        if model: session.model = model
        self._save_session(session)
        # Expose the rotation on the result so the caller can surface it without
        # re-reading the session file.
        result.usage = {**(result.usage or {}), "context_rotation": rotation}
        return result

    def close_session(self, session_id: str) -> bool:
        path = self._session_path(session_id)
        if path.is_file():
            path.unlink()
            return True
        return False

    def run_task(self, instruction: str, model: str = DEFAULT_MODEL, mode: str = "chat",
                 timeout: int = DEFAULT_TIMEOUT_SECONDS, sandbox: bool = False) -> AgyResult:
        """R13E path — direct agy call (not via helper)."""
        if mode not in R13E_MODES:
            raise ValueError(f"Invalid mode: {mode}")
        if model not in self._valid_models() and model != DEFAULT_MODEL:
            raise ValueError(f"Invalid model: {model}")
        return self._invoke_direct(prompt=instruction, model=model, mode=mode, timeout=timeout, sandbox=sandbox)

    def extract(self, instruction: str, model: str = DEFAULT_MODEL, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> AgyResult:
        return self.run_task(instruction, model=model, mode="extract", timeout=timeout)

    def analyze(self, instruction: str, model: str = DEFAULT_MODEL, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> AgyResult:
        return self.run_task(instruction, model=model, mode="analyze", timeout=timeout)

    def plan(self, instruction: str, model: str = DEFAULT_MODEL, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> AgyResult:
        return self.run_task(instruction, model=model, mode="plan", timeout=timeout)

    def _invoke_web(self, prompt: str, model: str = DEFAULT_MODEL, mode: str = "chat",
                    conversation_id: str = "", timeout: int = DEFAULT_TIMEOUT_SECONDS) -> AgyResult:
        """Web path: via sudo -n paw-agy-web helper."""
        stdin_data = json.dumps({
            "message": prompt,
            "model": model,
            "mode": mode,
            "conversation_id": conversation_id,
            "timeout": timeout,
        })
        argv = ["sudo", "-n", AGY_WEB_HELPER, "send"]
        try:
            proc = subprocess.run(
                argv, shell=False, input=stdin_data,
                capture_output=True, text=True, timeout=timeout + 20,
            )
        except subprocess.TimeoutExpired:
            return AgyResult(status="TIMEOUT", error=f"Helper timed out after {timeout}s", exit_code=124, model=model)
        except Exception as e:
            return AgyResult(status="ERROR", error=_redact(str(e)[:300]), exit_code=1, model=model)

        raw_stdout = (proc.stdout or "").strip()
        if not raw_stdout:
            return AgyResult(status="ERROR", error=_redact((proc.stderr or "")[:500]) or "No output", exit_code=proc.returncode, model=model)
        try:
            data = json.loads(raw_stdout)
            return AgyResult(
                status=data.get("status", "UNKNOWN"),
                response=_redact(data.get("response", "")),
                conversation_id=data.get("conversation_id", ""),
                model=model,
                duration_seconds=data.get("duration_seconds", 0.0),
                num_turns=data.get("num_turns", 0),
                usage=data.get("usage", {}),
                error=_redact(data.get("error", "")),
                exit_code=proc.returncode,
            )
        except json.JSONDecodeError:
            return AgyResult(status="ERROR", error=_redact(raw_stdout[:500]), exit_code=proc.returncode, model=model)

    def _invoke_direct(self, prompt: str, model: str = DEFAULT_MODEL, mode: Optional[str] = None,
                       timeout: int = DEFAULT_TIMEOUT_SECONDS, sandbox: bool = False) -> AgyResult:
        """R13E path: direct agy call (already root)."""
        argv = [AGY_EXECUTABLE, "-p", prompt, "--output-format", "json", "--model", model]
        agy_mode_map = {"chat": None, "extract": None, "analyze": None, "plan": "plan", "exec": "accept-edits"}
        agy_mode = agy_mode_map.get(mode or "chat")
        if agy_mode:
            argv.extend(["--mode", agy_mode])
        if mode == "exec":
            argv.append("--dangerously-skip-permissions")
        if sandbox:
            argv.append("--sandbox")
        eff_timeout = min(max(timeout, MIN_TIMEOUT_SECONDS), MAX_TIMEOUT_SECONDS)
        argv.extend(["--print-timeout", f"{eff_timeout}s"])
        env = {"HOME": "/root", "PATH": "/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "en_US.UTF-8", "TERM": "dumb"}
        try:
            proc = subprocess.run(argv, shell=False, capture_output=True, text=True, timeout=eff_timeout + 10, env=env)
        except subprocess.TimeoutExpired:
            return AgyResult(status="TIMEOUT", error=f"agy timed out after {eff_timeout}s", exit_code=124, model=model)
        except Exception as e:
            return AgyResult(status="ERROR", error=_redact(str(e)[:300]), exit_code=1, model=model)
        raw_stdout = (proc.stdout or "").strip()
        if not raw_stdout:
            return AgyResult(status="ERROR", error=_redact((proc.stderr or "")[:500]) or "No output", exit_code=proc.returncode, model=model)
        try:
            data = json.loads(raw_stdout)
            return AgyResult(
                status=data.get("status", "UNKNOWN"), response=_redact(data.get("response", "")),
                conversation_id=data.get("conversation_id", ""), model=model,
                duration_seconds=data.get("duration_seconds", 0.0), num_turns=data.get("num_turns", 0),
                usage=data.get("usage", {}), exit_code=proc.returncode,
            )
        except json.JSONDecodeError:
            return AgyResult(status="ERROR", error=_redact(raw_stdout[:500]), exit_code=proc.returncode, model=model)

    def _session_path(self, session_id: str) -> Path:
        return SESSION_DIR / f"{session_id}.session.json"

    def _save_session(self, session: AgySession) -> None:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        path = self._session_path(session.session_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(session.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _load_session(self, session_id: str) -> Optional[AgySession]:
        path = self._session_path(session_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return AgySession(
                session_id=data.get("session_id", ""), conversation_id=data.get("conversation_id", ""),
                model=data.get("model", DEFAULT_MODEL), mode=data.get("mode", "chat"),
                project_id=data.get("project_id") or "",
                created_at=data.get("created_at", ""), updated_at=data.get("updated_at", ""),
                message_count=data.get("message_count", 0),
                v2_context=data.get("v2_context") or {},
            )
        except Exception:
            return None


_adapter: Optional[AgyRuntimeAdapter] = None

def get_adapter(timeout: int = DEFAULT_TIMEOUT_SECONDS) -> AgyRuntimeAdapter:
    global _adapter
    if _adapter is None:
        _adapter = AgyRuntimeAdapter(timeout=timeout)
    return _adapter
