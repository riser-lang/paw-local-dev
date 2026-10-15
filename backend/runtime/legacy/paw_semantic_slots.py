"""PAW v1.3D / v2 - semantic slot + provider configuration layer.

Slots: "luna", "opus". Providers: "codex", "agy", "disabled".

Config resolution order:
  1. env PAW_SEMANTIC_SLOTS_CONFIG
  2. /etc/paw-semantic-slots.json

Missing file / empty "slots" -> builtin adapters, i.e. byte-identical to the
pre-change behaviour. Unknown provider or a provider listed in
"disabled_providers" resolves to DisabledAdapter (submit/poll return an ERROR
mapping the pipeline already knows how to hold on) plus an audit line.

Shadow adapters run the SAME input through a second provider purely for
comparison. Shadow results are written to a dedicated store and NEVER reach
Formal State, the state machine, attempt counters or the committer.

ASCII only.
"""
from __future__ import annotations

import dataclasses
import fcntl
import hashlib
import importlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping

SCHEMA = "paw-semantic-slots/1"
SCHEMA_V2 = "paw-semantic-slots/2"
ROLE_BASE_SLOTS = {"review_primary": "luna", "knowledge_extractor": "luna",
                   "planner": "opus", "validator": "opus",
                   "throughput_luna": "luna", "throughput_opus": "opus"}
ROLE_ALIASES = {"luna": "throughput_luna", "opus": "throughput_opus"}
CONFIG_ENV = "PAW_SEMANTIC_SLOTS_CONFIG"
DEFAULT_CONFIG_PATH = "/etc/paw-semantic-slots.json"
AUDIT_ENV = "PAW_SEMANTIC_SLOTS_AUDIT"
DEFAULT_AUDIT_PATH = "/var/lib/paw-semantic-slots/audit.jsonl"
AUDIT_MAX_BYTES = 50 * 1024 * 1024
AUDIT_BACKUPS = 5
SHADOW_STORE_ENV = "PAW_SEMANTIC_SHADOW_STORE"
DEFAULT_SHADOW_STORE = "/var/lib/paw-semantic-shadow"
MAX_CONFIG_BYTES = 65536
# Sentinel key inside a config dict that marks "file exists but is broken"
# (bad JSON / wrong root type / unsupported schema / unreadable).  Such a
# config MUST fail closed: resolve() returns DisabledAdapter for every slot.
INVALID_CONFIG_MARK = "__paw_slots_invalid__"

BUILTIN_PROVIDERS = {"luna": "codex", "opus": "agy"}
KNOWN_PROVIDERS = {"codex", "agy", "disabled"}
DEFAULT_LUNA_MODEL = "gpt-5.6-luna"
DEFAULT_OPUS_MODEL = "claude-opus-4-6-thinking"


class SlotResolutionError(RuntimeError):
    """Configuration cannot be honoured (unknown provider / bad schema)."""


# ---------------------------------------------------------------------------
# audit (best effort: never break the pipeline on audit IO)
# ---------------------------------------------------------------------------
def audit_event(kind: str, detail: Mapping[str, Any], audit_path: str | None = None) -> None:
    path = audit_path or os.environ.get(AUDIT_ENV) or DEFAULT_AUDIT_PATH
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kind": kind}
        row.update(detail)
        line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
        # Lock a stable sidecar, not the file being renamed. This also serializes
        # append/rotation across independent long-lived workers.
        with open(path + '.lock', 'a', encoding='utf-8') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if os.path.exists(path) and os.path.getsize(path) + len(line.encode('utf-8')) > AUDIT_MAX_BYTES:
                for i in range(AUDIT_BACKUPS, 0, -1):
                    source = path if i == 1 else path + '.' + str(i - 1)
                    if os.path.exists(source):
                        os.replace(source, path + '.' + str(i))
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# config loading (content-aware cached; hot reload without service restart)
# ---------------------------------------------------------------------------
# Cache key is the SHA256 of the actual file bytes - never the mtime.  Rapid
# rewrites can land inside the same mtime tick (observed: 683/2000 same-mtime
# writes), so an mtime key silently served stale providers.  Configs are
# capped at 64 KB, so hashing the bytes on every load is cheap and exact.
_cache: dict[str, Any] = {"path": None, "digest": None, "config": {}}


def _config_path() -> str:
    return os.environ.get(CONFIG_ENV) or DEFAULT_CONFIG_PATH


def load_config(path: str | None = None) -> dict[str, Any]:
    """Load the slot config (content-aware).

    File absent -> {} (builtin defaults; the pre-change behaviour).
    File present but broken (bad JSON / wrong root / unsupported schema /
    unreadable / oversized) -> dict carrying INVALID_CONFIG_MARK; resolve()
    maps that to a DisabledAdapter so a broken config can never silently fall
    back to a live provider.  Any byte-level content change (even with an
    identical mtime and size) is detected on the very next load.
    """
    p = path or _config_path()
    try:
        with open(p, "rb") as fh:
            raw = fh.read(MAX_CONFIG_BYTES + 1)
    except FileNotFoundError:
        _cache.update({"path": p, "digest": None, "config": {}})
        return {}
    except OSError as exc:
        audit_event("slot_config_error", {"path": p, "error": str(exc)[:200]})
        cfg = {INVALID_CONFIG_MARK: True, "error": f"unreadable: {str(exc)[:180]}"}
        _cache.update({"path": p, "digest": None, "config": cfg})
        return cfg

    digest = hashlib.sha256(raw).hexdigest()
    if _cache["path"] == p and _cache["digest"] == digest:
        return _cache["config"]

    def _invalid(reason: str) -> dict[str, Any]:
        audit_event("slot_config_error", {"path": p, "error": reason[:200]})
        cfg = {INVALID_CONFIG_MARK: True, "error": reason[:200]}
        _cache.update({"path": p, "digest": digest, "config": cfg})
        return cfg

    try:
        if len(raw) > MAX_CONFIG_BYTES:
            raise SlotResolutionError(f"config too large (> {MAX_CONFIG_BYTES} bytes)")
        cfg = json.loads(raw.decode("utf-8"))
        if not isinstance(cfg, dict):
            raise SlotResolutionError("config root must be an object")
        if cfg.get("schema") not in (None, SCHEMA, SCHEMA_V2):
            raise SlotResolutionError(f"unsupported config schema: {cfg.get('schema')!r}")
        _cache.update({"path": p, "digest": digest, "config": cfg})
        return cfg
    except (OSError, ValueError, SlotResolutionError) as exc:
        return _invalid(str(exc))


def invalidate_cache() -> None:
    _cache.update({"path": None, "digest": None, "config": {}})


def config_generation(path: str | None = None) -> str:
    """Deterministic content-only generation token for the active configuration.

    Two configurations with identical bytes share a generation; any byte
    difference (even same mtime/size) yields a new one.  No mtime is mixed in.
    """
    cfg = load_config(path)
    blob = json.dumps(cfg, sort_keys=True)
    return "gen-" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def make_slot_binding(slot: str, provider: str, effective_model: str, generation: str, job_id: str | None = None) -> dict[str, Any]:
    binding = {
        "slot": str(slot),
        "role_slot": ROLE_ALIASES.get(str(slot), str(slot)),
        "provider": str(provider),
        "effective_model": str(effective_model),
        "generation": str(generation),
        "job_id": str(job_id) if job_id is not None else None,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    audit_event("slot_binding_resolved", binding)
    if not job_id:
        audit_event("slot_binding_missing_job_id", {"slot": slot, "generation": generation})
    return binding


# ---------------------------------------------------------------------------
# Result normalization: unified contract across Codex and Agy operators
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class NormalizedSlotResult:
    provider: str
    status: str
    exit_code: int | None
    raw_payload: str
    parsed_json: dict[str, Any] | None
    reported_model: str | None
    delivery_status: str | None
    error: str | None
    raw_result: dict[str, Any]


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            obj = json.loads(text[start:end + 1])
            return obj if isinstance(obj, dict) else None
        except ValueError:
            return None


def normalize_operator_result(provider: str, raw_result: Mapping[str, Any]) -> NormalizedSlotResult:
    """Normalize operator result dictionary into NormalizedSlotResult contract."""
    prov = str(provider or "").strip().lower()
    status = str(raw_result.get("status") or "UNKNOWN")
    exit_code = raw_result.get("exit_code")
    error = raw_result.get("error")
    reported_model = raw_result.get("model")

    raw_payload = ""
    delivery_status = raw_result.get("delivery_status")

    if prov == "codex":
        raw_payload = str(raw_result.get("final_message") or "")
        if not delivery_status:
            if raw_payload.strip():
                delivery_status = "DELIVERED"
            elif status == "COMPLETED":
                delivery_status = "DELIVERY_EMPTY"
            else:
                delivery_status = "NOT_DELIVERED"
    elif prov == "agy":
        raw_payload = str(raw_result.get("response") or "")
        if not delivery_status:
            if raw_payload.strip():
                delivery_status = "DELIVERED"
            elif status == "COMPLETED":
                delivery_status = "DELIVERY_EMPTY"
            else:
                delivery_status = "NOT_DELIVERED"
    else:
        # Provider agnostic fallback
        if "final_message" in raw_result:
            raw_payload = str(raw_result.get("final_message") or "")
        elif "response" in raw_result:
            raw_payload = str(raw_result.get("response") or "")

    parsed = _parse_json_object(raw_payload)
    return NormalizedSlotResult(
        provider=prov or "unknown",
        status=status,
        exit_code=exit_code if isinstance(exit_code, int) else None,
        raw_payload=raw_payload,
        parsed_json=parsed,
        reported_model=str(reported_model) if reported_model else None,
        delivery_status=str(delivery_status) if delivery_status else None,
        error=str(error) if error else None,
        raw_result=dict(raw_result),
    )


def read_and_normalize_operator_result(operator: Any, provider: str, job_id: str, max_lines: int = 200) -> NormalizedSlotResult:
    if hasattr(operator, "read_result"):
        try:
            raw = operator.read_result(job_id, max_lines=max_lines) or {}
        except Exception as exc:
            return NormalizedSlotResult(
                provider=provider, status="FAILED", exit_code=-1, raw_payload="",
                parsed_json=None, reported_model=None, delivery_status="NOT_DELIVERED",
                error=f"read_result_error: {str(exc)[:200]}", raw_result={},
            )
        return normalize_operator_result(provider, raw)
    return NormalizedSlotResult(
        provider=provider, status="FAILED", exit_code=-1, raw_payload="",
        parsed_json=None, reported_model=None, delivery_status="NOT_DELIVERED",
        error="operator_has_no_read_result", raw_result={},
    )


def _read_operator_result(operator: Any, job_id: str, provider: str | None = None) -> dict[str, Any] | None:
    """Normalized helper returning parsed JSON from operator read_result."""
    normalized = read_and_normalize_operator_result(operator, provider or "", job_id)
    return normalized.parsed_json


# ---------------------------------------------------------------------------
# provider adapters
# ---------------------------------------------------------------------------
class DisabledAdapter:
    """A slot whose provider is disabled. Never touches any operator."""

    def __init__(self, slot: str, provider: str, reason: str, audit_path: str | None = None):
        self.slot = slot
        self.provider = provider
        self.reason = reason
        audit_event("slot_provider_disabled",
                    {"slot": slot, "provider": provider, "reason": reason}, audit_path)

    def submit(self, item: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "status": "ERROR",
            "error": f"slot_disabled: {self.provider} ({self.reason})",
            "slot": self.slot,
            "provider": self.provider,
            "model": "disabled",
        }

    def poll(self, item: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "status": "ERROR",
            "error": f"slot_disabled: {self.provider}",
            "slot": self.slot,
            "provider": self.provider,
            "model": "disabled",
        }


def _luna_prompt(item: Mapping[str, Any]) -> str:
    sid = str(item.get("source_version_id") or "")
    return (
        f"You are PAW's server-side incremental preanalysis worker. Use ONLY the Reader "
        f"result JSON at {item.get('reader_result_path')}.\n"
        f"SourceVersion identity: {sid}\n"
        "This is POST_BOOTSTRAP/INCREMENTAL work. Do not read frozen historical 710, do not "
        "make a business decision, do not write canonical state.\n"
        "Return exactly one JSON object with source_version_id, coverage_state, summary, "
        "evidence_refs, findings, knowledge_proposals, project_consolidations, "
        "metric_proposals, data_assets, project_id, canonical_topic, risk_hints, write_plan. "
        "Preserve evidence locators and uncertainty. A write_plan is only a proposal; the "
        "deterministic shared risk router and governed committer decide whether it may execute."
    )


class _BaseCrossAdapter:
    """Drives a non-builtin CLI operator behind one of the pipeline Protocols."""

    operator_module = ""
    slot = ""
    provider = ""

    def __init__(self, slot: str, model: str, results_dir: Any = None):
        self.slot = slot
        self.model = model
        self.results_dir = results_dir
        self._operator: Any = None

    def _op(self) -> Any:
        if self._operator is None:
            self._operator = importlib.import_module(self.operator_module)
        return self._operator

    def _verify_note(self) -> str:
        return ("cross-provider adapter: the operator's own runtime-model verification "
                "must be extended for this provider before production cutover")


class AgyLunaAdapter(_BaseCrossAdapter):
    """Luna slot executed through the Agy CLI operator."""

    operator_module = "agy_operator"
    slot = "luna"
    provider = "agy"

    def submit(self, item: Mapping[str, Any]) -> Mapping[str, Any]:
        op = self._op()
        # Explicitly pass empty profile to prevent agy_operator from forcing
        # the model to claude-opus-4-6-thinking.
        result = op.submit_task(
            _luna_prompt(item),
            title=f"PAW incremental Luna {item.get('source_version_id')} (slot=luna provider=agy)",
            model=self.model,
            mode="analyze",
            sandbox=False,
            timeout_seconds=min(int(getattr(op, "MAX_TIMEOUT_SECONDS", 5400)), 90 * 60),
            profile="",
        )
        if result.get("error"):
            return {
                "status": "FAILED",
                "error": result.get("error"),
                "model": self.model,
                "provider": self.provider,
                "slot": self.slot,
            }

        effective_model = result.get("effective_model") or result.get("model") or self.model
        if effective_model != self.model:
            return {
                "status": "FAILED",
                "error": f"model_mismatch: requested {self.model} but got {effective_model}",
                "model": effective_model,
                "provider": self.provider,
                "slot": self.slot,
            }

        gen = config_generation()
        binding = make_slot_binding("luna", "agy", self.model, gen, result.get("job_id"))
        return {
            "status": "RUNNING",
            "job_id": result.get("job_id"),
            "model": self.model,
            "provider": self.provider,
            "slot": self.slot,
            "generation": gen,
            "binding": binding,
        }

    def poll(self, item: Mapping[str, Any]) -> Mapping[str, Any]:
        op = self._op()
        job_id = str(item.get("luna_job_id") or "")
        if not job_id:
            return {
                "status": "FAILED",
                "error": "missing_luna_job_id",
                "model": self.model,
                "provider": self.provider,
                "slot": self.slot,
            }

        status = op.task_status(job_id)
        lifecycle = str(status.get("status") or "")
        if lifecycle in {"QUEUED", "RUNNING"}:
            return {
                "status": "RUNNING",
                "job_id": job_id,
                "model": self.model,
                "provider": self.provider,
                "slot": self.slot,
            }
        if lifecycle != "COMPLETED":
            return {
                "status": "FAILED",
                "job_id": job_id,
                "error": f"agy_{lifecycle.lower()}",
                "model": self.model,
                "provider": self.provider,
                "slot": self.slot,
            }

        # Model validation: fail closed if reported model mismatches configured model
        reported_model = status.get("result_model") or status.get("model")
        if reported_model and reported_model != self.model:
            return {
                "status": "FAILED",
                "job_id": job_id,
                "error": f"model_mismatch: expected {self.model} but reported {reported_model}",
                "model": reported_model,
                "provider": self.provider,
                "slot": self.slot,
            }

        normalized = read_and_normalize_operator_result(op, "agy", job_id)
        if normalized.reported_model and normalized.reported_model != self.model:
            return {
                "status": "FAILED",
                "job_id": job_id,
                "error": f"model_mismatch: expected {self.model} but reported {normalized.reported_model}",
                "model": normalized.reported_model,
                "provider": self.provider,
                "slot": self.slot,
            }
        if normalized.parsed_json is None:
            return {
                "status": "FAILED",
                "job_id": job_id,
                "error": "provider_returned_no_json",
                "model": self.model,
                "provider": self.provider,
                "slot": self.slot,
            }

        parsed = dict(normalized.parsed_json)
        parsed.update({
            "status": "COMPLETED",
            "job_id": job_id,
            "model": self.model,
            "provider": self.provider,
            "slot": self.slot,
        })
        return parsed


class CodexOpusAdapter(_BaseCrossAdapter):
    """Opus slot executed through the Codex CLI operator.

    Model provenance truth (v2.1): the real codex_operator pins the CLI to
    FIXED_MODEL and never accepts a caller model.  This adapter therefore
    always records and reports operator.FIXED_MODEL; a configured model that
    differs from the operator capability fails closed BEFORE any submit.
    """

    operator_module = "codex_operator"
    slot = "opus"
    provider = "codex"

    def _fixed_model(self, op: Any) -> str:
        return str(getattr(op, "FIXED_MODEL", "") or "").strip()

    def submit(self, cluster: Mapping[str, Any]) -> Mapping[str, Any]:
        op = self._op()
        fixed = self._fixed_model(op)
        if not fixed:
            audit_event("slot_fail_closed",
                        {"slot": self.slot, "provider": self.provider,
                         "reason": "codex_fixed_model_unavailable"})
            return {
                "status": "FAILED",
                "error": "codex_fixed_model_unavailable: operator exposes no FIXED_MODEL",
                "model": self.model or "unknown",
                "provider": self.provider,
                "slot": self.slot,
            }
        if self.model and self.model != fixed:
            # fail closed BEFORE submitting: codex cannot run this model
            audit_event("slot_fail_closed",
                        {"slot": self.slot, "provider": self.provider,
                         "reason": "codex_model_not_supported",
                         "configured_model": self.model,
                         "operator_fixed_model": fixed})
            return {
                "status": "FAILED",
                "error": (f"codex_model_not_supported: configured {self.model!r} but "
                          f"codex_operator FIXED_MODEL is {fixed!r}"),
                "model": fixed,
                "provider": self.provider,
                "slot": self.slot,
            }
        effective = fixed  # the ONLY model codex can actually run
        prompt = (
            f"You are PAW's Opus escalation reviewer. Cluster key: "
            f"{cluster.get('cluster_key')}. Review the provided evidence and return one "
            "JSON object with outcome, review and reason_codes."
        )
        result = op.submit_task(
            prompt,
            title=f"PAW incremental Opus {cluster.get('cluster_key')} (slot=opus provider=codex)",
            network_access=False,
            timeout_seconds=int(getattr(op, "DEFAULT_TIMEOUT_SECONDS", 600)),
        )
        if result.get("error"):
            return {
                "status": "FAILED",
                "error": result.get("error"),
                "model": effective,
                "provider": self.provider,
                "slot": self.slot,
            }

        gen = config_generation()
        binding = make_slot_binding("opus", "codex", effective, gen, result.get("job_id"))
        return {
            "status": "RUNNING",
            "job_id": result.get("job_id"),
            "model": effective,
            "provider": self.provider,
            "slot": self.slot,
            "generation": gen,
            "binding": binding,
        }

    def poll(self, cluster: Mapping[str, Any]) -> Mapping[str, Any]:
        op = self._op()
        effective = self._fixed_model(op) or self.model
        job_id = str(cluster.get("opus_job_id") or cluster.get("job_id") or "")
        if not job_id:
            return {
                "status": "FAILED",
                "error": "missing_opus_job_id",
                "model": effective,
                "provider": self.provider,
                "slot": self.slot,
            }

        status = op.task_status(job_id)
        lifecycle = str(status.get("status") or "")
        if lifecycle in {"QUEUED", "RUNNING"}:
            return {
                "status": "RUNNING",
                "job_id": job_id,
                "model": effective,
                "provider": self.provider,
                "slot": self.slot,
            }
        if lifecycle != "COMPLETED" or status.get("exit_code") not in (0, None):
            return {
                "status": "FAILED",
                "job_id": job_id,
                "error": f"codex_{lifecycle.lower()}",
                "model": effective,
                "provider": self.provider,
                "slot": self.slot,
            }

        reported_model = status.get("model")
        if reported_model and reported_model != effective:
            return {
                "status": "FAILED",
                "job_id": job_id,
                "error": (f"model_mismatch: operator FIXED_MODEL {effective!r} "
                          f"but reported {reported_model!r}"),
                "model": reported_model,
                "provider": self.provider,
                "slot": self.slot,
            }

        normalized = read_and_normalize_operator_result(op, "codex", job_id)
        if normalized.parsed_json is None:
            return {
                "status": "FAILED",
                "job_id": job_id,
                "error": "provider_returned_no_json",
                "model": effective,
                "provider": self.provider,
                "slot": self.slot,
            }

        parsed = dict(normalized.parsed_json)
        parsed.update({
            "status": "COMPLETED",
            "job_id": job_id,
            "model": effective,   # provenance: real operator model, never the config string
            "provider": self.provider,
            "slot": self.slot,
        })
        return parsed


def _cross_adapter(slot: str, provider: str, model: str, results_dir: Any = None) -> Any:
    if slot == "luna" and provider == "agy":
        return AgyLunaAdapter(slot, model, results_dir)
    if slot == "opus" and provider == "codex":
        return CodexOpusAdapter(slot, model, results_dir)
    raise SlotResolutionError(f"provider '{provider}' is not available for slot '{slot}'")


# ---------------------------------------------------------------------------
# shadow (comparison only; never touches Formal State)
# ---------------------------------------------------------------------------
class ShadowingAdapter:
    """Wraps the primary adapter; mirrors the same input into a shadow provider.

    The primary response is returned untouched. Shadow observations land in a
    dedicated JSON store consumed only by the comparison report.
    """

    def __init__(self, primary: Any, shadow: Any, slot: str, store_dir: str | None = None,
                 sample_rate: float = 1.0, audit_path: str | None = None):
        self.primary = primary
        self.shadow = shadow
        self.slot = slot
        self.provider = getattr(primary, "provider", None)
        try:
            rate = float(sample_rate)
        except (TypeError, ValueError):
            rate = 1.0
        self.sample_rate = min(max(rate, 0.0), 1.0)
        self.audit_path = audit_path
        self.store_dir = Path(store_dir or os.environ.get(SHADOW_STORE_ENV)
                              or DEFAULT_SHADOW_STORE) / slot
        self.store_dir.mkdir(parents=True, exist_ok=True)
        audit_event("slot_shadow_enabled",
                    {"slot": slot, "shadow_provider": type(shadow).__name__,
                     "primary_provider": type(primary).__name__,
                     "sample_rate": self.sample_rate}, audit_path)

    @staticmethod
    def _key(item: Mapping[str, Any]) -> str:
        known = str(item.get("source_version_id") or item.get("cluster_key") or "").strip()
        if known:
            return known
        blob = json.dumps(item, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]

    def _sample(self, key: str) -> bool:
        bucket = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) % 10000
        return bucket / 10000.0 < self.sample_rate

    def _write(self, key: str, record: Mapping[str, Any]) -> None:
        path = self._path(key)
        try:
            fd, tmp = tempfile.mkstemp(dir=str(self.store_dir), prefix=".shadow-")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(record, fh, ensure_ascii=False, indent=1, sort_keys=True)
            os.replace(tmp, path)
        except OSError as exc:
            audit_event("slot_shadow_write_error",
                        {"slot": self.slot, "key": key, "error": str(exc)[:200]},
                        self.audit_path)

    def _path(self, key):
        # Source/cluster identifiers are untrusted metadata, never filenames.
        return self.store_dir / (hashlib.sha256(str(key).encode()).hexdigest()+'.json')

    @staticmethod
    def _output(resp):
        result=resp.get('result')
        if isinstance(result,Mapping):return dict(result)
        fields=('outcome','verdict','decision','review','reason_codes','evidence_refs',
                'summary','coverage_state','findings','knowledge_proposals','project_consolidations',
                'project_proposals','metric_proposals','data_assets','project_id','canonical_topic',
                'risk_hints','write_plan','material_business_decision','unresolved_conflict',
                'changes_effective_rule','creates_project','uncertainty','conflicts')
        return {k:resp[k] for k in fields if k in resp}

    @staticmethod
    def _summary(resp: Mapping[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {"status": str(resp.get("status") or ""),
                               "model": str(resp.get("model") or "")}
        for k in ("verdict", "outcome", "decision", "error"):
            if resp.get(k) is not None:
                out[k] = resp.get(k)
        result = resp.get("result")
        if isinstance(result, Mapping):
            out["result_keys"] = sorted(result.keys())
            out["summary"] = str(result.get("summary") or "")[:400]
        return out

    def _observe(self, phase: str, item: Mapping[str, Any], primary_resp: Mapping[str, Any]) -> None:
        try:
            source_key = self._key(item)
            if not self._sample(source_key):
                return
            primary_job=str(primary_resp.get('job_id') or item.get(self.slot+'_job_id') or item.get('job_id') or 'synchronous')
            key=primary_job if primary_job!='synchronous' else source_key+':synchronous'
            path=self._path(key)
            record=json.loads(path.read_text()) if path.is_file() else {}
            now=time.time()
            if phase=='submit' and record:
                # Never launch a second shadow for the same primary job.
                return
            if record and record.get('key')!=source_key:
                audit_event('slot_shadow_source_binding_mismatch',{'slot':self.slot,'primary_job':primary_job},self.audit_path)
                return
            if not record:
                if phase!='submit':
                    audit_event('slot_shadow_orphan_poll',{'slot':self.slot,'primary_job':primary_job},self.audit_path)
                    return
                record={'schema':'paw.shadow-pair/1','slot':self.slot,'key':source_key,
                        'primary_job_id':primary_job,'captured_epoch':now,
                        'input_hash':hashlib.sha256(json.dumps(item,sort_keys=True,default=str).encode()).hexdigest(),
                        'primary_provider':getattr(self.primary,'provider',None),
                        'shadow_provider':getattr(self.shadow,'provider',None),
                        'status':'CAPTURED'}
            if primary_resp.get('status') not in ('RUNNING','COMPLETED'):
                record['status']='EXPIRED';self._write(key,record);return
            if record.get('status') in ('PAIRED','EXPIRED'):
                return
            if now-record['captured_epoch']>7200:
                record['status']='EXPIRED';self._write(key,record);return
            record.update(phase=phase,at=time.strftime('%Y-%m-%dT%H:%M:%S%z'),primary=self._summary(primary_resp))
            if primary_resp.get('status')=='COMPLETED':
                record['primary_output']=self._output(primary_resp)
                record.setdefault('primary_completed_epoch',now)
                record['primary_latency_ms']=round((record['primary_completed_epoch']-record['captured_epoch'])*1000)
            try:
                if phase=='submit':
                    shadow_resp=dict(self.shadow.submit(item))
                    record['shadow_job_id']=shadow_resp.get('job_id')
                    record['shadow_binding']=shadow_resp.get('binding')
                elif 'shadow_output' in record:
                    shadow_resp={'status':'COMPLETED','model':record.get('shadow',{}).get('model'),
                                 'result':record['shadow_output']}
                else:
                    if not record.get('shadow_job_id'):
                        record['status']='EXPIRED';self._write(key,record);return
                    # The shadow must NEVER poll the primary's job id/binding.
                    shadow_item={**item,self.slot+'_job_id':record['shadow_job_id'],
                                 'job_id':record['shadow_job_id'],self.slot+'_binding':record.get('shadow_binding')}
                    shadow_resp=dict(self.shadow.poll(shadow_item))
                now=time.time()
            except Exception as exc:
                record["shadow"] = {"status": "SHADOW_ERROR", "error": str(exc)[:300]}
                record['status']='EXPIRED'
            else:
                record["shadow"] = self._summary(shadow_resp)
                record['primary_provider']=primary_resp.get('provider') or record.get('primary_provider')
                record['shadow_provider']=shadow_resp.get('provider') or record.get('shadow_provider')
                record['primary_model']=primary_resp.get('model') or record.get('primary_model')
                record['shadow_model']=shadow_resp.get('model') or record.get('shadow_model')
                if shadow_resp.get('status')=='COMPLETED':
                    record['shadow_output']=self._output(shadow_resp)
                    record.setdefault('shadow_completed_epoch',now)
                    record['shadow_latency_ms']=round((record['shadow_completed_epoch']-record['captured_epoch'])*1000)
                record['status']=('PAIRED' if 'primary_output' in record and 'shadow_output' in record else
                                  'PRIMARY_DONE' if 'primary_output' in record else
                                  'SHADOW_DONE' if 'shadow_output' in record else 'CAPTURED')
                if shadow_resp.get('status') in ('FAILED','ERROR','DELIVERY_EMPTY'):
                    record['status']='EXPIRED'
            record['latency_note']='observed end-to-end completion; includes queueing and polling delay, not pure model latency'
            self._write(key, record)
        except Exception as exc:  # shadow must never break the primary path
            audit_event("slot_shadow_error",
                        {"slot": self.slot, "error": str(exc)[:300]}, self.audit_path)

    def submit(self, item: Mapping[str, Any]) -> Mapping[str, Any]:
        resp = dict(self.primary.submit(item))
        self._observe("submit", item, resp)
        return resp

    def poll(self, item: Mapping[str, Any]) -> Mapping[str, Any]:
        resp = dict(self.primary.poll(item))
        self._observe("poll", item, resp)
        return resp


# ---------------------------------------------------------------------------
# slot resolution
# ---------------------------------------------------------------------------
def resolve(slot: str, *, default_factory: Callable[[], Any],
            results_dir: Any = None, audit_path: str | None = None,
            config: dict[str, Any] | None = None) -> Any:
    """Return the adapter instance for `slot`.

    No config / empty slot -> default_factory() (pre-change behaviour).
    Unknown or disabled provider -> DisabledAdapter + audit (fail closed).
    Shadow spec -> ShadowingAdapter(primary, shadow).
    """
    if slot in ROLE_BASE_SLOTS:
        return resolve_role(slot, default_factory=default_factory, results_dir=results_dir,
                            audit_path=audit_path, config=config)
    if slot not in BUILTIN_PROVIDERS:
        raise SlotResolutionError(f"unknown slot: {slot}")
    cfg = config if config is not None else load_config()
    if cfg.get("schema") == SCHEMA_V2 and not cfg.get("__role_merged__"):
        return resolve_role(slot, default_factory=default_factory, results_dir=results_dir,
                            audit_path=audit_path, config=cfg)
    if cfg.get(INVALID_CONFIG_MARK):
        # fail closed: a broken config must never fall back to a live provider
        reason = f"invalid_config: {cfg.get('error')}"
        audit_event("slot_fail_closed",
                    {"slot": slot, "provider": "invalid_config",
                     "reason": reason}, audit_path)
        return DisabledAdapter(slot, "invalid_config", reason, audit_path)
    slots = cfg.get("slots") or {}
    spec = slots.get(slot) or {}
    if not isinstance(spec, Mapping):
        raise SlotResolutionError(f"slot '{slot}' config must be an object")
    disabled = {str(x) for x in (cfg.get("disabled_providers") or [])}
    provider = str(spec.get("provider") or BUILTIN_PROVIDERS[slot]).strip()
    model = str(spec.get("model") or "").strip()

    if provider not in KNOWN_PROVIDERS:
        audit_event("slot_provider_rejected",
                    {"slot": slot, "provider": provider, "reason": "unknown_provider"},
                    audit_path)
        raise SlotResolutionError(f"unknown provider '{provider}' for slot '{slot}'")
    if provider in disabled:
        return DisabledAdapter(slot, provider, "listed in disabled_providers", audit_path)
    if provider == "disabled":
        return DisabledAdapter(slot, provider, "explicit disabled provider", audit_path)

    builtin = BUILTIN_PROVIDERS[slot]
    fixed_default = DEFAULT_LUNA_MODEL if slot=='luna' else DEFAULT_OPUS_MODEL
    if provider==builtin and model and model!=fixed_default:
        return DisabledAdapter(slot,provider,'configured_builtin_model_not_supported: '+model,audit_path)
    primary = default_factory() if provider == builtin else _cross_adapter(slot, provider, model, results_dir)

    shadow_spec = spec.get("shadow") or {}
    shadow_provider = str((shadow_spec or {}).get("provider") or "").strip()
    if shadow_provider:
        if slot=='opus' and shadow_provider=='agy':
            # The builtin Opus adapter submits to the shared durable production
            # dispatcher. It is NOT an isolated shadow operator and must never
            # be used as one. Keep the primary unchanged; no hidden queue write.
            audit_event('slot_shadow_rejected',{'slot':slot,'provider':shadow_provider,
                        'reason':'builtin_dispatcher_not_shadow_isolated'},audit_path)
            return primary
        shadow_model = str((shadow_spec or {}).get('model') or '').strip()
        if shadow_provider==builtin and shadow_model and shadow_model!=fixed_default:
            audit_event('slot_shadow_rejected',{'slot':slot,'provider':shadow_provider,
                        'reason':'configured_builtin_shadow_model_not_supported'},audit_path)
            return primary
        if shadow_provider in disabled or shadow_provider not in KNOWN_PROVIDERS:
            audit_event("slot_shadow_rejected",
                        {"slot": slot, "provider": shadow_provider}, audit_path)
        else:
            shadow_model = str((shadow_spec or {}).get("model") or "").strip()
            shadow = default_factory() if shadow_provider == builtin else \
                _cross_adapter(slot, shadow_provider, shadow_model, results_dir)
            return ShadowingAdapter(primary, shadow, slot,
                                    store_dir=os.environ.get(SHADOW_STORE_ENV),
                                    sample_rate=(shadow_spec or {}).get("sample_rate", 1.0),
                                    audit_path=audit_path)
    return primary


def resolve_role(role: str, *, default_factory: Callable[[], Any],
                 results_dir: Any = None, audit_path: str | None = None,
                 config: dict[str, Any] | None = None) -> Any:
    """Role adapter over the *current* binding-aware implementation.

    Registry remains data-only. No provider/model is changed by missing config,
    and existing in-flight bindings still resolve independently of new config.
    """
    canonical = ROLE_ALIASES.get(role, role)
    if canonical not in ROLE_BASE_SLOTS:
        return DisabledAdapter(role, "disabled", "unknown_role", audit_path)
    base = ROLE_BASE_SLOTS[canonical]
    cfg = dict(config if config is not None else load_config())
    if cfg.get(INVALID_CONFIG_MARK):
        return resolve(base, default_factory=default_factory, results_dir=results_dir,
                       audit_path=audit_path, config=cfg)
    roles = cfg.get("roles") or {}
    if not isinstance(roles, Mapping):
        return DisabledAdapter(role, "disabled", "roles_must_be_object", audit_path)
    override = roles.get(canonical) if cfg.get("schema") == SCHEMA_V2 else None
    slots = cfg.get("slots") or {}
    if not isinstance(slots, Mapping):
        return DisabledAdapter(role, "disabled", "slots_must_be_object", audit_path)
    slots = dict(slots)
    original = slots.get(base) or {}
    if original == ROLE_ALIASES.get(base):
        original = {}
    if not isinstance(original, Mapping):
        return DisabledAdapter(role, "disabled", "invalid_slot_spec", audit_path)
    spec = dict(original)
    if override is not None:
        if isinstance(override, str):
            override = {"provider": override}
        if not isinstance(override, Mapping):
            return DisabledAdapter(role, "disabled", "invalid_role_spec", audit_path)
        spec.update(override)
    slots[base] = spec
    cfg["slots"] = slots
    cfg["__role_merged__"] = True
    try:
        adapter = resolve(base, default_factory=default_factory, results_dir=results_dir,
                          audit_path=audit_path, config=cfg)
    except SlotResolutionError as exc:
        return DisabledAdapter(role, "disabled", str(exc), audit_path)
    audit_event("role_adapter_resolved", {"role_slot": canonical, "alias": role if role != canonical else None,
                "base_slot": base, "provider": spec.get("provider") or BUILTIN_PROVIDERS[base],
                "binding_source": "builtin", "registry_active": False,
                "generation": "gen-" + hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16],
                "schema": SCHEMA_V2}, audit_path)
    return adapter


def _resolve_adapter_for_binding_primary(
    binding: Mapping[str, Any],
    *,
    results_dir: Any = None,
    default_luna_factory: Callable[[], Any] | None = None,
    default_opus_factory: Callable[[], Any] | None = None,
) -> Any:
    """Resolve an adapter instance honoring an in-flight job's persisted binding."""
    slot = str(binding.get("slot") or "").strip().lower()
    provider = str(binding.get("provider") or "").strip().lower()
    model = str(binding.get("effective_model") or "").strip()

    if slot == "luna":
        if provider == "codex":
            if default_luna_factory:
                return default_luna_factory()
            # If no factory provided, import from pipeline
            from paw_incremental_decision_pipeline import CodexLunaIncrementalAdapter
            return CodexLunaIncrementalAdapter(results_dir)
        if provider == "agy":
            return AgyLunaAdapter(slot="luna", model=model or DEFAULT_LUNA_MODEL, results_dir=results_dir)
        if provider == "disabled":
            return DisabledAdapter("luna", provider, "binding was disabled")
    elif slot == "opus":
        if provider == "agy":
            if default_opus_factory:
                return default_opus_factory()
            from paw_incremental_decision_pipeline import AgyIncrementalOpusAdapter
            return AgyIncrementalOpusAdapter()
        if provider == "codex":
            # model truth: empty -> adapter adopts operator.FIXED_MODEL at runtime;
            # never fall back to a non-codex constant here
            return CodexOpusAdapter(slot="opus", model=model, results_dir=results_dir)
        if provider == "disabled":
            return DisabledAdapter("opus", provider, "binding was disabled")

    raise SlotResolutionError(f"cannot resolve adapter for binding: {dict(binding)}")


def resolve_adapter_for_binding(binding: Mapping[str, Any], **kwargs) -> Any:
    """Frozen primary plus its already-submitted shadow, never new config.

    The pipeline reconstructs adapters on every poll. Without restoring this
    sidecar, shadow records stop at CAPTURED and can never become PAIRED.
    Restoring only existing jobs cannot launch a new shadow after a hot reload.
    """
    primary=_resolve_adapter_for_binding_primary(binding,**kwargs)
    slot=str(binding.get('slot') or '')
    job=str(binding.get('job_id') or '')
    if not job or slot not in BUILTIN_PROVIDERS:return primary
    store=Path(os.environ.get(SHADOW_STORE_ENV) or DEFAULT_SHADOW_STORE)
    record_path=store/slot/(hashlib.sha256(job.encode()).hexdigest()+'.json')
    try:
        record=json.loads(record_path.read_text())
        shadow_binding=record.get('shadow_binding')
        if record.get('primary_job_id')!=job or record.get('status') in ('PAIRED','EXPIRED'):
            return primary
        if not isinstance(shadow_binding,Mapping) or shadow_binding.get('slot')!=slot:
            return primary
        if slot=='opus' and shadow_binding.get('provider')=='agy':
            return primary  # never restore a shared-dispatcher shadow
        shadow=_resolve_adapter_for_binding_primary(shadow_binding,**kwargs)
        return ShadowingAdapter(primary,shadow,slot,store_dir=str(store),sample_rate=1.0)
    except (OSError,ValueError,SlotResolutionError):
        return primary  # broken/absent sidecar cannot affect the primary job


__all__ = [
    "SCHEMA",
    "SlotResolutionError",
    "DisabledAdapter",
    "ShadowingAdapter",
    "AgyLunaAdapter",
    "CodexOpusAdapter",
    "load_config",
    "resolve",
    "invalidate_cache",
    "audit_event",
    "BUILTIN_PROVIDERS",
    "KNOWN_PROVIDERS",
    "DEFAULT_LUNA_MODEL",
    "DEFAULT_OPUS_MODEL",
    "INVALID_CONFIG_MARK",
    "NormalizedSlotResult",
    "normalize_operator_result",
    "read_and_normalize_operator_result",
    "config_generation",
    "make_slot_binding",
    "resolve_adapter_for_binding",
]
