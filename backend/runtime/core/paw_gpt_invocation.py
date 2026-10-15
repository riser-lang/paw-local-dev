#!/usr/bin/python3
"""Presence-only GPT / Workspace-Agent invocation probe and adapters.

This module never prints secret values. Cursor, Codex/Luna, Agy, Mistral,
Gemini, and Claude are not GPT Brain and must not be used as the main reviewer.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from gpt_brain_common import text

GPT_INVOCATION_LIVE = "LIVE"
GPT_INVOCATION_WAITING_GPT = "WAITING_GPT"
GPT_INVOCATION_BLOCKED = "BLOCKED"

CREDENTIAL_ENV_NAMES = (
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "OPENAI_BASE_URL",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "PAW_GPT_API_KEY",
    "PAW_GPT_INVOCATION_URL",
    "PAW_WORKSPACE_AGENT_URL",
    "CHAT_GPT_API_KEY",
    "GPT_API_KEY",
    "PAW_CHATGPT_API_KEY",
    "WORKSPACE_AGENT_URL",
)

ENV_FILES = (
    "/etc/paw-chatgpt-mcp-r13.env",
    "/etc/paw-mcp-gateway.env",
    "/etc/paw-mcp-gateway/gpt-patrol-token.env",
    "/etc/paw-gpt-invocation.env",
)

CREDENTIAL_PATHS = (
    "/var/lib/paw-mcp-gateway/gpt_invocation.json",
    "/var/lib/paw-chatgpt-mcp-r13/gpt-credentials.json",
    "/root/.openai/api_key",
    "/home/ubuntu/.openai/api_key",
)

FORBIDDEN_MAIN_REVIEWERS = frozenset({
    "cursor", "codex", "luna", "agy", "mistral", "gemini", "claude", "opus", "grok", "qwen",
})

# Explicit allowlist of permissible authoritative GPT models for user selection later.
# Luna/Codex is NEVER in this list and cannot be selected as the authoritative consolidator.
ALLOWED_AUTHORITATIVE_GPT_MODELS = frozenset({
    "gpt-5.6-terra",
    "gpt-5.6-sol",
    "gpt-5.6",
    "gpt-5.5",
    "gpt-5",
    "gpt-4.5-preview",
    "gpt-4o",
    "gpt-4o-2024-08-06",
    "gpt-4o-mini",
    "o1",
    "o1-preview",
    "o1-mini",
    "o3-mini",
})

CONFIG_PATHS = (
    "/etc/paw-authoritative-gpt.json",
    "/var/lib/paw-mcp-gateway/paw_authoritative_gpt_config.json",
)
MODEL_CONFIG_ENV = "PAW_AUTHORITATIVE_GPT_MODEL"

MAIN_REVIEW_WORKER = "chatgpt-main-review"
POST_OPUS_WORKER = "chatgpt-post-opus"

# Historical bootstrap declaration preserved for frozen-run telemetry; never rewrite live metadata.
HISTORICAL_DECLARATION_MODEL = "GPT-5.6 Sol"

# Current Workspace Agent policy (configuration surface; governance owner is CHATGPT_WORKSPACE_AGENT).
CURRENT_DEFAULT_AGENT_MODEL = "GPT-5.6 Terra"
CURRENT_DEFAULT_AGENT_MODEL_SLUG = "gpt-5.6-terra"
DEFAULT_REASONING_EFFORT = "medium"
AUTHORITATIVE_REVIEW_OWNER = "CHATGPT_WORKSPACE_AGENT"
AUTHORITATIVE_REVIEW_MODEL = CURRENT_DEFAULT_AGENT_MODEL
AUTHORITATIVE_REVIEW_MODEL_SLUG = CURRENT_DEFAULT_AGENT_MODEL_SLUG
ESCALATION_MODEL = "GPT-5.6 Sol"
ESCALATION_MODEL_SLUG = "gpt-5.6-sol"
DEFAULT_GPT_MODEL = CURRENT_DEFAULT_AGENT_MODEL


def load_configured_authoritative_model() -> tuple[str | None, str]:
    """Return (model_name, source_description). None if unconfigured."""
    env = _merged_env()
    env_model = text(env.get(MODEL_CONFIG_ENV))
    if env_model:
        return env_model, f"env:{MODEL_CONFIG_ENV}"
    for cfg_path in CONFIG_PATHS:
        p = Path(cfg_path)
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    cfg_model = text(data.get("authoritative_gpt_model") or data.get("model"))
                    if cfg_model:
                        return cfg_model, f"config_file:{cfg_path}"
            except Exception:
                pass
    return None, "UNCONFIGURED"


def validate_authoritative_model(model: str | None) -> tuple[bool, str]:
    """Validate whether candidate model can serve as authoritative GPT reviewer."""
    if not model:
        return False, "No authoritative GPT model configured; user must select a model from allowlist"
    lowered = model.casefold().strip()
    if any(token in lowered for token in FORBIDDEN_MAIN_REVIEWERS):
        return False, f"Model {model} contains forbidden reviewer token"
    normalized = lowered.replace("_", "-")
    if normalized.startswith("scripted-"):
        return True, "valid_test_adapter"
    if normalized in ALLOWED_AUTHORITATIVE_GPT_MODELS or any(normalized.startswith(prefix) for prefix in ("gpt-", "o1", "o3", "chatgpt-")):
        return True, "valid"
    return False, f"Model {model} is not in the allowlist of authoritative GPT models"


def list_authoritative_gpt_capability_surface() -> dict[str, Any]:
    """Small explicit provider capability and allowlist surface for user selection."""
    current_model, source = load_configured_authoritative_model()
    is_valid, reason = validate_authoritative_model(current_model)
    return {
        "configured_model": current_model,
        "configuration_source": source,
        "is_configured_and_valid": is_valid and bool(current_model),
        "status": "CONFIGURED" if (is_valid and current_model) else "WAITING_GPT_SELECTION",
        "validation_message": reason,
        "allowed_models": sorted(ALLOWED_AUTHORITATIVE_GPT_MODELS),
        "forbidden_models": sorted(FORBIDDEN_MAIN_REVIEWERS),
        "config_locations": {
            "environment_variable": MODEL_CONFIG_ENV,
            "json_config_file": CONFIG_PATHS[1],
        },
        "default_behavior": "FAIL_CLOSED_WAITING_GPT",
        "current_default_agent_model": CURRENT_DEFAULT_AGENT_MODEL,
        "default_reasoning_effort": DEFAULT_REASONING_EFFORT,
        "authoritative_review_owner": AUTHORITATIVE_REVIEW_OWNER,
        "authoritative_review_model": AUTHORITATIVE_REVIEW_MODEL,
        "escalation_model": ESCALATION_MODEL,
        "sol_default_for_routine_patrol": False,
    }


def _env_file_keys(path: str) -> list[str]:
    file_path = Path(path)
    if not file_path.is_file():
        return []
    keys: list[str] = []
    try:
        for raw in file_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            keys.append(line.split("=", 1)[0].strip())
    except OSError:
        return []
    return keys


def _load_env_file_values(path: str) -> dict[str, str]:
    file_path = Path(path)
    out: dict[str, str] = {}
    if not file_path.is_file():
        return out
    try:
        for raw in file_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        return out
    return out


def _merged_env() -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in ENV_FILES:
        merged.update(_load_env_file_values(path))
    merged.update({key: str(value) for key, value in os.environ.items() if value})
    return merged


def _inbound_chatgpt_mcp_live() -> dict[str, Any]:
    tunnel_unit = "paw-chatgpt-mcp-r13-tunnel.service"
    client_unit = "paw-chatgpt-r13-tunnel-client.service"
    status: dict[str, Any] = {
        "tunnel_unit": tunnel_unit,
        "client_unit": client_unit,
        "listen_127_0_0_1_8816": False,
        "units_active": [],
    }
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.3)
        status["listen_127_0_0_1_8816"] = sock.connect_ex(("127.0.0.1", 8816)) == 0
        sock.close()
    except OSError:
        status["listen_127_0_0_1_8816"] = False
    try:
        for unit in (tunnel_unit, client_unit):
            result = subprocess.run(
                ["systemctl", "is-active", unit],
                capture_output=True, text=True, timeout=3, check=False,
            )
            if text(result.stdout) == "active":
                status["units_active"].append(unit)
    except (OSError, subprocess.SubprocessError):
        pass
    status["inbound_only"] = True
    status["cannot_push_review_without_chatgpt_session"] = True
    return status


def probe_gpt_invocation_path() -> dict[str, Any]:
    """Inspect Workspace Agent / GPT transport without returning secret values."""
    env = _merged_env()
    present_env = [name for name in CREDENTIAL_ENV_NAMES if text(env.get(name))]
    present_files = [path for path in CREDENTIAL_PATHS if Path(path).is_file()]
    env_file_report = []
    for path in ENV_FILES:
        keys = _env_file_keys(path)
        hits = [key for key in keys if key in CREDENTIAL_ENV_NAMES]
        env_file_report.append({
            "path": path,
            "exists": Path(path).is_file(),
            "credential_keys_present": hits,
        })
    inbound = _inbound_chatgpt_mcp_live()
    url = text(env.get("PAW_WORKSPACE_AGENT_URL") or env.get("PAW_GPT_INVOCATION_URL") or env.get("WORKSPACE_AGENT_URL"))
    api_key_present = any(text(env.get(name)) for name in (
        "OPENAI_API_KEY", "PAW_GPT_API_KEY", "CHAT_GPT_API_KEY", "GPT_API_KEY",
        "PAW_CHATGPT_API_KEY", "AZURE_OPENAI_API_KEY",
    ))

    # Authoritative GPT model selection check (fails closed if unconfigured)
    selected_model, model_src = load_configured_authoritative_model()
    model_valid, model_reason = validate_authoritative_model(selected_model)

    if not (selected_model and model_valid):
        path = GPT_INVOCATION_WAITING_GPT
        reason = (
            "no authoritative GPT model configured (pending user selection from allowlist); "
            "fail closed in WAITING_GPT. Luna/Codex results are non-authoritative pre-analysis "
            "and cannot self-authorize write-ready transitions"
        )
        effective_model_identity = GPT_INVOCATION_WAITING_GPT
    elif api_key_present or url:
        path = GPT_INVOCATION_LIVE
        reason = f"authoritative GPT model configured ({selected_model} via {model_src}) with outbound transport"
        effective_model_identity = selected_model
    else:
        path = GPT_INVOCATION_WAITING_GPT
        reason = (
            f"authoritative GPT model selected ({selected_model}), but no outbound GPT/Workspace-Agent "
            "invocation credentials or URL configured; inbound ChatGPT MCP tunnel is not an autonomous reviewer"
        )
        effective_model_identity = GPT_INVOCATION_WAITING_GPT

    capability = list_authoritative_gpt_capability_surface()

    return {
        "path": path,
        "reason": reason,
        "env_names_present": present_env,
        "credential_files_present": [Path(item).name for item in present_files],
        "env_files": env_file_report,
        "inbound_chatgpt_mcp": inbound,
        "cursor_as_gpt_forbidden": True,
        "codex_luna_as_gpt_forbidden": True,
        "agy_opus_as_gpt_forbidden": True,
        "mcp_inbound_chatgpt_expected": True,
        "workspace_agent_url_configured": bool(url),
        "api_key_configured": api_key_present,
        "authoritative_model_configured": bool(selected_model and model_valid),
        "authoritative_model_selection": selected_model,
        "main_review_model_identity": effective_model_identity,
        "source_processor_model": "gpt-5.6-luna",
        "forbidden_substitutes": sorted(FORBIDDEN_MAIN_REVIEWERS),
        "capability_surface": capability,
    }


@dataclass
class WaitingGptAdapter:
    name: str = "none"
    model_identity: str = GPT_INVOCATION_WAITING_GPT

    def available(self) -> bool:
        return False

    def review(self, prompt: str, *, kind: str = "main_review") -> Mapping[str, Any] | None:
        return None


@dataclass
class ScriptedReviewAdapter:
    """Deterministic test adapter. Never used as a live GPT stand-in."""

    scripts: dict[str, Mapping[str, Any]]
    name: str = "scripted-chatgpt"
    model_identity: str = "scripted-chatgpt"
    fail_ids: set[str] = field(default_factory=set)

    def available(self) -> bool:
        return True

    def review(self, prompt: str, *, kind: str = "main_review") -> Mapping[str, Any] | None:
        marker = _prompt_bundle_id(prompt)
        if marker in self.fail_ids:
            raise RuntimeError(f"scripted adapter failed for {marker}")
        return self.scripts.get(marker) or self.scripts.get("*")


def _prompt_bundle_id(prompt: str) -> str:
    match = re.search(r'"bundle_id"\s*:\s*"([^"]+)"', prompt)
    return match.group(1) if match else ""


def _http_json(url: str, payload: Mapping[str, Any], *, token: str, timeout: int = 120) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": "Bearer " + token,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GPT transport HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError("GPT transport unreachable") from None
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("GPT transport returned a non-object")
    return parsed


def _extract_message_text(payload: Mapping[str, Any]) -> str:
    if text(payload.get("content")):
        return text(payload.get("content"))
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], Mapping) else {}
        if isinstance(message, Mapping):
            return text(message.get("content"))
    output = payload.get("output_text")
    if text(output):
        return text(output)
    return json.dumps(payload, ensure_ascii=False)


def parse_review_json(raw: str) -> dict[str, Any]:
    candidates = [text(raw)]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw or "", flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        candidates.insert(0, fenced.group(1))
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            if isinstance(value, dict) and value.get("outcome"):
                return value
        except ValueError:
            pass
        for index, char in enumerate(candidate):
            if char != "{":
                continue
            try:
                value, _ = decoder.raw_decode(candidate[index:])
            except ValueError:
                continue
            if isinstance(value, dict) and value.get("outcome"):
                return value
    raise RuntimeError("GPT returned no JSON review object")


@dataclass
class HttpGptAdapter:
    """Outbound ChatGPT / Workspace-Agent HTTP path. Never impersonates another model."""

    url: str
    token: str
    model: str = DEFAULT_GPT_MODEL
    name: str = "workspace-agent-http"
    timeout_seconds: int = 120

    @property
    def model_identity(self) -> str:
        return self.model

    def available(self) -> bool:
        lowered = self.model.casefold()
        if any(token in lowered for token in FORBIDDEN_MAIN_REVIEWERS):
            return False
        valid, _ = validate_authoritative_model(self.model)
        return bool(valid and self.url and self.token and self.model)

    def review(self, prompt: str, *, kind: str = "main_review") -> Mapping[str, Any] | None:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are the authoritative PAW GPT Brain reviewer. Return exactly one JSON object. "
                        "Do not impersonate Cursor, Codex, Luna, Agy, Mistral, Gemini, or Claude. "
                        "Independently accept, revise, or reject Luna pre-analysis proposals."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "kind": kind,
        }
        parsed = _http_json(self.url, payload, token=self.token, timeout=self.timeout_seconds)
        raw = _extract_message_text(parsed)
        decision = parse_review_json(raw)
        decision["model_identity"] = text(parsed.get("model")) or self.model
        return decision


def build_live_adapter() -> WaitingGptAdapter | HttpGptAdapter:
    probe = probe_gpt_invocation_path()
    if probe.get("path") != GPT_INVOCATION_LIVE:
        return WaitingGptAdapter()
    env = _merged_env()
    url = text(env.get("PAW_WORKSPACE_AGENT_URL") or env.get("PAW_GPT_INVOCATION_URL") or env.get("WORKSPACE_AGENT_URL"))
    token = text(
        env.get("PAW_GPT_API_KEY")
        or env.get("OPENAI_API_KEY")
        or env.get("PAW_CHATGPT_API_KEY")
        or env.get("CHAT_GPT_API_KEY")
        or env.get("GPT_API_KEY")
        or env.get("AZURE_OPENAI_API_KEY")
    )
    model = probe.get("authoritative_model_selection")
    if not url and token:
        base = text(env.get("OPENAI_BASE_URL") or env.get("OPENAI_API_BASE") or env.get("AZURE_OPENAI_ENDPOINT"))
        url = (base.rstrip("/") + "/v1/chat/completions") if base else "https://api.openai.com/v1/chat/completions"
    if url and token and model:
        return HttpGptAdapter(url=url, token=token, model=model, name="chatgpt-http")
    return WaitingGptAdapter()


__all__ = [
    "ALLOWED_AUTHORITATIVE_GPT_MODELS",
    "AUTHORITATIVE_REVIEW_MODEL",
    "AUTHORITATIVE_REVIEW_MODEL_SLUG",
    "AUTHORITATIVE_REVIEW_OWNER",
    "CREDENTIAL_ENV_NAMES",
    "CURRENT_DEFAULT_AGENT_MODEL",
    "CURRENT_DEFAULT_AGENT_MODEL_SLUG",
    "DEFAULT_GPT_MODEL",
    "DEFAULT_REASONING_EFFORT",
    "ESCALATION_MODEL",
    "ESCALATION_MODEL_SLUG",
    "FORBIDDEN_MAIN_REVIEWERS",
    "GPT_INVOCATION_BLOCKED",
    "GPT_INVOCATION_LIVE",
    "GPT_INVOCATION_WAITING_GPT",
    "HISTORICAL_DECLARATION_MODEL",
    "HttpGptAdapter",
    "MAIN_REVIEW_WORKER",
    "MODEL_CONFIG_ENV",
    "POST_OPUS_WORKER",
    "ScriptedReviewAdapter",
    "WaitingGptAdapter",
    "build_live_adapter",
    "list_authoritative_gpt_capability_surface",
    "load_configured_authoritative_model",
    "parse_review_json",
    "probe_gpt_invocation_path",
    "validate_authoritative_model",
]
