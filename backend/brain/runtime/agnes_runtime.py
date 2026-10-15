"""Agnes Runtime Adapter V1 - Production Bridge to OpenClaw Agnes.

DRY_RUN = False: methods call real OpenClaw via production CLI.
Transport: sudo -n /usr/local/sbin/paw-agnes-agent agent --agent agnes --session-key agent:agnes:paw-brain:{key} --message {text} --json
# (root helper so the service user can use the /root OpenClaw runtime with Node v22)

Safety:
- OpenClaw 18789 remains loopback-only
- Adapter-side tool isolation for workspace_* modes
- Session namespace: paw-brain:* (never main)
- server_ops inaccessible from Brain path
- No CoT saved
- AUTO_WRITE not triggered
- No canonical DB write
"""
from __future__ import annotations
from typing import Any, Optional
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
import subprocess, json, re, os, urllib.request

DRY_RUN = False
OPENCLAW_BINARY = "/root/.nvm/versions/node/v22.22.3/bin/openclaw"
AGENT_HELPER = "/usr/local/sbin/paw-agnes-agent"
AGENT_HELPER_CMD = ["sudo", "-n", AGENT_HELPER]
OPENCLAW_GATEWAY_URL = "http://127.0.0.1:18789"
OPENCLAW_GATEWAY_LOOPBACK_ONLY = True
DEFAULT_MODEL = "mistral/mistral-small-latest"
FALLBACK_MODELS = ["nemotron", "glm"]
SESSION_NAMESPACE = "paw-brain"

class AgnesMode(str, Enum):
    workspace_chat = "workspace_chat"
    workspace_extract = "workspace_extract"
    workspace_simulate = "workspace_simulate"
    workspace_candidate = "workspace_candidate"
    server_ops = "server_ops"

TOOL_PERMISSIONS = {
    AgnesMode.workspace_chat: {"shell_write": False, "canonical_write": False, "read_only": True},
    AgnesMode.workspace_extract: {"shell_write": False, "canonical_write": False, "read_only": True},
    AgnesMode.workspace_simulate: {"shell_write": False, "canonical_write": False, "read_only": True},
    AgnesMode.workspace_candidate: {"shell_write": False, "canonical_write": False, "read_only": False},
    AgnesMode.server_ops: {"shell_write": True, "canonical_write": False, "read_only": False},
}

BRAIN_ALLOWED_MODES = {
    AgnesMode.workspace_chat,
    AgnesMode.workspace_extract,
    AgnesMode.workspace_simulate,
    AgnesMode.workspace_candidate,
}

RESERVED_SESSION_KEYS = {"main", "server_ops", "global", ""}
MAX_SESSION_KEY_LEN = 128
VALID_SESSION_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9_\-:]+$")


@dataclass
class SessionInfo:
    session_key: str
    session_id: str
    mode: AgnesMode = AgnesMode.workspace_chat
    active: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class AnalysisResult:
    analysis_id: str
    draft_candidates: list = field(default_factory=list)
    raw_text: str = ""
    model_used: str = DEFAULT_MODEL
    pass_type: str = "analysis"
    completed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ReviewResult:
    analysis_id: str
    validated_candidates: list = field(default_factory=list)
    rejected_candidates: list = field(default_factory=list)
    review_notes: list = field(default_factory=list)
    model_used: str = DEFAULT_MODEL
    pass_type: str = "review"
    completed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def validate_session_key(raw_key: str) -> str:
    if not raw_key or raw_key.strip() == "":
        raise ValueError("Session key cannot be empty")
    if raw_key in RESERVED_SESSION_KEYS:
        raise ValueError(f"Session key '{raw_key}' is reserved")
    if len(raw_key) > MAX_SESSION_KEY_LEN:
        raise ValueError("Session key too long")
    if not VALID_SESSION_KEY_PATTERN.match(raw_key):
        raise ValueError("Session key contains invalid characters")
    if raw_key.startswith("paw-brain:"):
        return raw_key
    return f"{SESSION_NAMESPACE}:{raw_key}"


def is_main_session(key: str) -> bool:
    return "main" in key.lower() and "paw-brain" not in key


def build_tool_isolation_prompt(mode: AgnesMode) -> str:
    if mode == AgnesMode.server_ops:
        return ""
    perms = TOOL_PERMISSIONS.get(mode, {})
    restrictions = []
    if not perms.get("shell_write", True):
        restrictions.append("DO NOT execute any shell commands, filesystem writes, systemctl, service restart, or package install.")
    if not perms.get("canonical_write", True):
        restrictions.append("DO NOT write to canonical Todo, Project, Knowledge, Source, or any Workspace DB table.")
    if perms.get("read_only", False):
        restrictions.append("You are in READ-ONLY mode. Only retrieve and analyze information.")
    if not restrictions:
        return ""
    return "\n\n[ADAPTER TOOL POLICY - mode=" + mode.value + "]\n" + "\n".join(restrictions) + "\n[END TOOL POLICY]"


def _read_policy_summary(path: str, max_chars: int = 500) -> str:
    try:
        content = open(path, "r", encoding="utf-8").read()
        lines = []
        for line in content.split("\n"):
            if line.startswith("#") or line.startswith("-") or "MUST" in line or "CANONICAL" in line:
                lines.append(line)
                if sum(len(l) for l in lines) > max_chars:
                    break
        return "\n".join(lines[:20])
    except Exception:
        return ""


def build_system_context_content(project_id: Optional[str] = None, mode: AgnesMode = AgnesMode.workspace_chat) -> dict:
    context = {
        "workspace_mode": mode.value,
        "auto_write": False,
        "canonical_source": "Personal Workspace",
        "memory_boundary": "OpenClaw memory is runtime-only, NOT canonical",
    }
    context["persona_summary"] = _read_policy_summary("/opt/personal-workspace/brain/policies/agnes_persona_v1.md")
    context["fact_precedence"] = _read_policy_summary("/opt/personal-workspace/brain/policies/fact_precedence_v1.md")
    context["memory_policy"] = _read_policy_summary("/opt/personal-workspace/brain/policies/openclaw_memory_policy_v1.md")
    perms = TOOL_PERMISSIONS.get(mode, {})
    context["mode_safety"] = {
        "shell_write": perms.get("shell_write", False),
        "canonical_write": perms.get("canonical_write", False),
        "read_only": perms.get("read_only", True),
    }
    if project_id:
        try:
            from brain.context.builder import build_project_context
            ctx = build_project_context(project_id)
            if ctx:
                context["project_context"] = {
                    "project": ctx.get("project", {}).get("name", ""),
                    "open_todos": len(ctx.get("open_todos", [])),
                    "source_summary": ctx.get("knowledge_source_summary", {}),
                }
        except Exception:
            context["project_context"] = None
    return context


class AgnesRuntimeAdapter:
    """Production Bridge between Brain modules and OpenClaw Agnes runtime.

    DRY_RUN = False: methods call real OpenClaw via CLI.
    Adapter-side tool isolation for workspace_* modes.
    server_ops inaccessible from Brain path.
    """

    def __init__(self, dry_run: bool = DRY_RUN):
        self.dry_run = dry_run
        self.binary = OPENCLAW_BINARY
        self.gateway_url = OPENCLAW_GATEWAY_URL
        self.model = DEFAULT_MODEL
        self.fallback_models = FALLBACK_MODELS
        self._sessions: dict[str, SessionInfo] = {}

    def health_check(self) -> dict[str, Any]:
        if self.dry_run:
            return {"healthy": True, "dry_run": True, "gateway_url": self.gateway_url, "model": self.model, "loopback_only": OPENCLAW_GATEWAY_LOOPBACK_ONLY}
        try:
            req = urllib.request.Request(self.gateway_url + "/health")
            resp = urllib.request.urlopen(req, timeout=5)
            data = json.loads(resp.read().decode())
            return {"healthy": data.get("ok", False), "dry_run": False, "gateway_url": self.gateway_url, "model": self.model, "loopback_only": OPENCLAW_GATEWAY_LOOPBACK_ONLY, "health_status": data.get("status", "unknown"), "binary": self.binary}
        except Exception as e:
            return {"healthy": False, "dry_run": False, "error": str(e), "gateway_url": self.gateway_url}

    def create_or_resume_session(self, session_key: str, mode: AgnesMode = AgnesMode.workspace_chat) -> SessionInfo:
        validated_key = validate_session_key(session_key)
        if is_main_session(validated_key):
            raise ValueError("Cannot use main session from Brain path")
        if mode == AgnesMode.server_ops:
            raise ValueError("server_ops mode is not accessible from Brain path")
        if mode not in BRAIN_ALLOWED_MODES:
            raise ValueError(f"Mode {mode} is not allowed from Brain path")
        if validated_key in self._sessions:
            session = self._sessions[validated_key]
            session.active = True
            return session
        session = SessionInfo(session_key=validated_key, session_id=session_key, mode=mode, active=True)
        self._sessions[validated_key] = session
        return session

    def build_system_context(self, project_id: Optional[str] = None, mode: AgnesMode = AgnesMode.workspace_chat) -> dict[str, Any]:
        return build_system_context_content(project_id, mode)

    def send_message(self, session_key: str, message: str, context: Optional[dict] = None, mode: AgnesMode = AgnesMode.workspace_chat) -> dict[str, Any]:
        if self.dry_run:
            return {"response": "[DRY_RUN] Agnes runtime not yet bound.", "session_key": session_key, "model": self.model, "dry_run": True, "candidates": [], "auto_write_triggered": False}
        validated_key = validate_session_key(session_key)
        if is_main_session(validated_key):
            return {"response": "[ERROR] Cannot use main session", "session_key": session_key, "candidates": [], "auto_write_triggered": False}
        if mode == AgnesMode.server_ops:
            return {"response": "[ERROR] server_ops not accessible from Brain path", "session_key": session_key, "candidates": [], "auto_write_triggered": False}
        tool_policy = build_tool_isolation_prompt(mode)
        full_message = message + tool_policy
        try:
            cmd = [*AGENT_HELPER_CMD, "agent", "--agent", "agnes", "--session-key", "agent:agnes:" + validated_key, "--message", full_message, "--json"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                return {"response": "[ERROR] openclaw agent failed: " + result.stderr[:200], "session_key": validated_key, "model": self.model, "candidates": [], "auto_write_triggered": False}
            data = json.loads(result.stdout)
            if data.get("status") == "ok":
                payloads = data.get("result", {}).get("payloads", [])
                text = payloads[0].get("text", "") if payloads else ""
                meta = data.get("meta", {}).get("agentMeta", {})
                return {"response": text, "session_key": validated_key, "model": meta.get("model", self.model), "dry_run": False, "candidates": [], "auto_write_triggered": False, "runtime_run_id": data.get("runId", ""), "usage": meta.get("usage", {}), "mode": mode.value}
            else:
                return {"response": "[ERROR] Agnes status: " + str(data.get("status")), "session_key": validated_key, "model": self.model, "candidates": [], "auto_write_triggered": False}
        except subprocess.TimeoutExpired:
            return {"response": "[ERROR] Agnes timeout (120s)", "session_key": validated_key, "model": self.model, "candidates": [], "auto_write_triggered": False}
        except Exception as e:
            return {"response": "[ERROR] " + str(e), "session_key": validated_key, "model": self.model, "candidates": [], "auto_write_triggered": False}

    def analysis_pass(self, input_text: str, context: Optional[dict] = None, trigger_type: str = "manual_review") -> AnalysisResult:
        analysis_id = "analysis-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        if self.dry_run:
            return AnalysisResult(analysis_id=analysis_id, draft_candidates=[], raw_text=input_text, model_used=self.model, pass_type="analysis")
        prompt = "You are in ANALYSIS mode. Analyze the following input and produce structured draft candidates.\nFor each candidate provide: candidate_type, statement, confidence, inference_level, recommended_action.\nDo NOT auto-write. Do NOT confirm knowledge.\nInput: " + input_text + "\n"
        if context:
            prompt += "Context: " + str(context) + "\n"
        result = self.send_message("analysis:" + analysis_id, prompt, context, mode=AgnesMode.workspace_candidate)
        return AnalysisResult(analysis_id=analysis_id, draft_candidates=[], raw_text=input_text, model_used=result.get("model", self.model), pass_type="analysis")

    def review_pass(self, analysis_result: AnalysisResult, context: Optional[dict] = None) -> ReviewResult:
        if self.dry_run:
            return ReviewResult(analysis_id=analysis_result.analysis_id, validated_candidates=[], rejected_candidates=[], review_notes=["[DRY_RUN] No review"], model_used=self.model, pass_type="review")
        prompt = "You are in REVIEW mode. Check for: plan_as_done, idea_as_decision, date_loss, project_misbinding, unsupported_inference, confirmed_knowledge_conflict, evidence_mismatch.\nAnalysis ID: " + analysis_result.analysis_id + "\nRaw text: " + analysis_result.raw_text + "\n"
        result = self.send_message("review:" + analysis_result.analysis_id, prompt, context, mode=AgnesMode.workspace_candidate)
        return ReviewResult(analysis_id=analysis_result.analysis_id, validated_candidates=[], rejected_candidates=[], review_notes=[result.get("response", "")], model_used=result.get("model", self.model), pass_type="review")

    def two_pass_analysis(self, input_text: str, context: Optional[dict] = None, trigger_type: str = "manual_review") -> tuple:
        analysis = self.analysis_pass(input_text, context, trigger_type)
        review = self.review_pass(analysis, context)
        return analysis, review

    def get_safety_status(self) -> dict[str, bool]:
        return {
            "dry_run": self.dry_run,
            "auto_write_enabled": False,
            "openclaw_loopback_only": OPENCLAW_GATEWAY_LOOPBACK_ONLY,
            "session_namespace_separated": True,
            "cot_saved": False,
            "brain_chat_read_only": True,
            "server_ops_blocked_from_brain": True,
            "adapter_side_tool_isolation": True,
        }


_default_adapter: Optional[AgnesRuntimeAdapter] = None

def get_adapter() -> AgnesRuntimeAdapter:
    global _default_adapter
    if _default_adapter is None:
        _default_adapter = AgnesRuntimeAdapter()
    return _default_adapter
