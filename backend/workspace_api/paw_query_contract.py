"""PAW v1.3D — Typed Agent Query Contract (provider-neutral).

This module is the typed boundary between the Query Agent and PAW Core.

It defines ONLY types, bounds and validation:

* ``Intent``        — what the user is asking for (closed set).
* ``QueryRequest``  — the typed, bounded, validated argument object.
* ``validate_request`` — fail-closed validation of an untrusted payload.

It deliberately contains:

* NO retrieval algorithm (the ONE contract is
  ``workspace_api.retrieval_contract`` / ``paw.retrieval-contract.v13/1``);
* NO Current algorithm (the ONE authority is
  ``paw_temporal_authority.TemporalAuthorityResolver``);
* NO database access, NO writes, NO model calls;
* NO vendor SDK, NO vendor response object, NO vendor chat identity.

Provider neutrality
-------------------
Everything here is stdlib: ``dataclasses`` + ``frozenset`` + ``datetime``.
No vendor SDK is imported, no vendor response object is accepted as input, no
vendor chat identity is read or written, no vendor-side durable state is
addressed, no vendor model name is referenced, and no vendor-specific
tool-call payload is produced or consumed anywhere in the module. The contract
describes what a Query Agent must PRODUCE, not what any particular vendor
RETURNS. Swapping the Query Agent vendor therefore requires no change to this
file.

Untrusted input
---------------
``validate_request`` treats its payload as hostile:

* unknown keys are rejected, never ignored;
* every value is type-checked and range-bounded;
* an unparseable timestamp is a rejection, never a silent fallback;
* an unknown/misspelled intent is a rejection, never a "best guess";
* missing per-intent required arguments are a rejection.

A rejected payload yields ``(None, typed_error)`` and the Gateway answers with
``INVALID_ARGS``. Nothing is routed on a guess. ``MODEL_CALLS = 0``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

SCHEMA = "paw.query-contract.v13d/1"

# ---------------------------------------------------------------------------
# Bounded agent behaviour
# ---------------------------------------------------------------------------
# Default tool hops for a standard business question is 1-3; 4 is the hard cap.
# Exceeding it must return a bounded / next-step state rather than loop.
MAX_TOOL_HOPS = 4

# ---------------------------------------------------------------------------
# Intents
# ---------------------------------------------------------------------------
# The seven mandatory v1.3D intents.
INTENT_PROJECT_CURRENT = "PROJECT_CURRENT"
INTENT_KNOWLEDGE_CURRENT = "KNOWLEDGE_CURRENT"
INTENT_TEMPORAL_QUERY = "TEMPORAL_QUERY"
INTENT_FORMAL_METRIC = "FORMAL_METRIC"
INTENT_EVIDENCE_QUERY = "EVIDENCE_QUERY"
INTENT_RECENT_CHANGES = "RECENT_CHANGES"
INTENT_ANALYSIS_DATA = "ANALYSIS_DATA"
# Two further intents exist because v1.3D Gateway coverage explicitly includes
# ``resolve_current`` and ``get_timeline``; they are typed capabilities of the
# same read plane, not speculative additions.
INTENT_ENTITY_AUTHORITY = "ENTITY_AUTHORITY"
INTENT_TIMELINE = "TIMELINE"

MANDATORY_INTENTS = (
    INTENT_PROJECT_CURRENT,
    INTENT_KNOWLEDGE_CURRENT,
    INTENT_TEMPORAL_QUERY,
    INTENT_FORMAL_METRIC,
    INTENT_EVIDENCE_QUERY,
    INTENT_RECENT_CHANGES,
    INTENT_ANALYSIS_DATA,
)
INTENTS = frozenset(MANDATORY_INTENTS + (
    INTENT_ENTITY_AUTHORITY, INTENT_TIMELINE,
))

# ---------------------------------------------------------------------------
# Result / error states (never flattened into "nothing found")
# ---------------------------------------------------------------------------
STATE_OK = "OK"
STATE_NOT_FOUND = "NOT_FOUND"
STATE_UNRESOLVED = "UNRESOLVED"
STATE_TEMPORAL_HISTORY_INCOMPLETE = "TEMPORAL_HISTORY_INCOMPLETE"
STATE_EVIDENCE_NOT_AVAILABLE = "EVIDENCE_NOT_AVAILABLE"
STATE_RETRIEVAL_STALE = "RETRIEVAL_STALE"
STATE_NON_CANONICAL_ONLY = "NON_CANONICAL_ONLY"
STATE_AMBIGUOUS_QUERY = "AMBIGUOUS_QUERY"
STATE_INVALID_ARGS = "INVALID_ARGS"

RESULT_STATES = frozenset({
    STATE_OK, STATE_NOT_FOUND, STATE_UNRESOLVED,
    STATE_TEMPORAL_HISTORY_INCOMPLETE, STATE_EVIDENCE_NOT_AVAILABLE,
    STATE_RETRIEVAL_STALE, STATE_NON_CANONICAL_ONLY,
    STATE_AMBIGUOUS_QUERY, STATE_INVALID_ARGS,
})

# Entity types the authority layer can resolve. Mirrors
# paw_temporal_authority.SUPPORTED_ENTITY_TYPES; kept as data here so this
# module never imports the authority layer (no import-time coupling).
SUPPORTED_ENTITY_TYPES = frozenset({"knowledge", "metric", "source_version"})

# Structural locator SHAPE. The authoritative whitelist of locator roots and
# the fail-closed parser stay in paw_retrieval_core._parse_locator; this is a
# pure typed-shape guard so a non-locator string never reaches the core.
_LOCATOR_SHAPE_RE = re.compile(r"^[a-z_]+\[\d+\]$")

# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------
_STR_MAX = 300
_LOCATOR_MAX = 64
_DIMENSIONS_MAX = 8
_DIM_KEY_MAX = 64
_DIM_VALUE_MAX = 128
_ENTITY_TYPES_MAX = 10
_LIMIT_MIN = 1
_LIMIT_MAX = 50
_LIMIT_DEFAULT = 20

_ALLOWED_KEYS = frozenset({
    "intent",
    "project_id", "project_name",
    "query",
    "metric_id", "metric_name",
    "period", "dimensions",
    "as_of", "known_as_of",
    "source_version_id", "locator",
    "entity_type", "entity_id",
    "since", "entity_types",
    "include_historical", "include_provisional", "analysis_allowed",
    "follow_evidence",
    "limit",
})

# Per-intent required argument groups. Each group is an OR-list of AND-sets:
# the request must satisfy at least ONE group completely.
_REQUIRED: dict[str, tuple[tuple[str, ...], ...]] = {
    INTENT_PROJECT_CURRENT: (("project_id",), ("project_name",)),
    INTENT_KNOWLEDGE_CURRENT: (("query",),),
    INTENT_TEMPORAL_QUERY: (("query", "as_of"),),
    INTENT_ENTITY_AUTHORITY: (("entity_type", "entity_id"),),
    INTENT_TIMELINE: (
        ("project_id",), ("project_name",), ("entity_type", "entity_id"),
    ),
    INTENT_FORMAL_METRIC: (("metric_id",), ("metric_name",)),
    INTENT_EVIDENCE_QUERY: (("source_version_id", "locator"),),
    INTENT_RECENT_CHANGES: ((),),
    INTENT_ANALYSIS_DATA: ((),),
}


@dataclass(frozen=True)
class QueryRequest:
    """A typed, bounded, validated query intent.

    Every field is validated before construction; an instance of this class is
    therefore safe to route on. ``MODEL_CALLS = 0``.
    """

    intent: str
    project_id: str | None = None
    project_name: str | None = None
    query: str | None = None
    metric_id: str | None = None
    metric_name: str | None = None
    period: str | None = None
    dimensions: dict[str, str] = field(default_factory=dict)
    as_of: str | None = None
    known_as_of: str | None = None
    source_version_id: str | None = None
    locator: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    since: str | None = None
    entity_types: tuple[str, ...] = ()
    include_historical: bool = False
    include_provisional: bool = False
    analysis_allowed: bool = False
    # Multi-intent expression: FORMAL_METRIC followed by the EVIDENCE leg built
    # from the metric's OWN evidence refs. It never triggers a second fuzzy
    # search -- the evidence step consumes the metric's refs or fails closed.
    follow_evidence: bool = False
    limit: int = _LIMIT_DEFAULT

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "intent": self.intent,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "query": self.query,
            "metric_id": self.metric_id,
            "metric_name": self.metric_name,
            "period": self.period,
            "dimensions": dict(self.dimensions),
            "as_of": self.as_of,
            "known_as_of": self.known_as_of,
            "source_version_id": self.source_version_id,
            "locator": self.locator,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "since": self.since,
            "entity_types": list(self.entity_types),
            "include_historical": self.include_historical,
            "include_provisional": self.include_provisional,
            "analysis_allowed": self.analysis_allowed,
            "follow_evidence": self.follow_evidence,
            "limit": self.limit,
            "max_tool_hops": MAX_TOOL_HOPS,
            "deterministic": True,
            "model_call": False,
        }


# ---------------------------------------------------------------------------
# Fail-closed error object
# ---------------------------------------------------------------------------
def invalid_args(reason: str, detail: Any = None) -> dict[str, Any]:
    """The ONE typed rejection object. Never a prose apology."""
    out: dict[str, Any] = {
        "schema": SCHEMA,
        "result_type": "query_contract",
        "result_state": STATE_INVALID_ARGS,
        "authority_state": None,
        "canonical": False,
        "temporal_state": None,
        "source_refs": [],
        "evidence_refs": [],
        "project_refs": [],
        "valid_from": None,
        "valid_to": None,
        "known_from": None,
        "known_to": None,
        "unresolved_reason": reason,
        "provisional_reason": None,
        "input_watermark": None,
        "deterministic": True,
        "model_call": False,
    }
    if detail is not None:
        out["detail"] = detail
    return out


# ---------------------------------------------------------------------------
# Field validators
# ---------------------------------------------------------------------------
def _parse_iso(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _opt_str(payload: Mapping[str, Any], key: str, *, max_len: int = _STR_MAX):
    """Optional bounded string. Absent -> None. Wrong type/empty/oversized -> error."""
    if key not in payload:
        return None, None
    raw = payload[key]
    if raw is None:
        return None, None
    if not isinstance(raw, str):
        return None, f"{key}_must_be_string"
    text = raw.strip()
    if not text:
        return None, f"{key}_must_not_be_empty"
    if len(text) > max_len:
        return None, f"{key}_too_long"
    return text, None


def _opt_bool(payload: Mapping[str, Any], key: str):
    if key not in payload:
        return False, None
    raw = payload[key]
    if raw is None:
        return False, None
    if not isinstance(raw, bool):
        return None, f"{key}_must_be_boolean"
    return raw, None


def _opt_limit(payload: Mapping[str, Any]):
    if "limit" not in payload or payload["limit"] is None:
        return _LIMIT_DEFAULT, None
    raw = payload["limit"]
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None, "limit_must_be_integer"
    if raw < _LIMIT_MIN or raw > _LIMIT_MAX:
        return None, f"limit_out_of_range[{_LIMIT_MIN},{_LIMIT_MAX}]"
    return raw, None


def _opt_dimensions(payload: Mapping[str, Any]):
    if "dimensions" not in payload or payload["dimensions"] is None:
        return {}, None
    raw = payload["dimensions"]
    if not isinstance(raw, Mapping):
        return None, "dimensions_must_be_object"
    if len(raw) > _DIMENSIONS_MAX:
        return None, "dimensions_too_many_keys"
    out: dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not k.strip():
            return None, "dimensions_keys_must_be_nonempty_strings"
        if isinstance(v, bool) or not isinstance(v, (str, int, float)):
            return None, "dimensions_values_must_be_scalar"
        ks, vs = k.strip(), str(v).strip()
        if len(ks) > _DIM_KEY_MAX or len(vs) > _DIM_VALUE_MAX:
            return None, "dimensions_entry_too_long"
        out[ks] = vs
    return out, None


def _opt_entity_types(payload: Mapping[str, Any]):
    if "entity_types" not in payload or payload["entity_types"] is None:
        return (), None
    raw = payload["entity_types"]
    if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
        return None, "entity_types_must_be_list"
    if len(raw) > _ENTITY_TYPES_MAX:
        return None, "entity_types_too_many"
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            return None, "entity_types_items_must_be_nonempty_strings"
        out.append(item.strip()[:64])
    return tuple(out), None


def _opt_ts(payload: Mapping[str, Any], key: str):
    """Optional ISO-8601 timestamp. Unparseable -> rejection, never a fallback."""
    text, err = _opt_str(payload, key, max_len=64)
    if err:
        return None, err
    if text is None:
        return None, None
    if _parse_iso(text) is None:
        return None, f"{key}_must_be_iso8601"
    return text, None


# ---------------------------------------------------------------------------
# The fail-closed validator
# ---------------------------------------------------------------------------
def validate_request(payload: Any) -> tuple[QueryRequest | None, dict | None]:
    """Validate an untrusted payload into a QueryRequest.

    Returns ``(request, None)`` on success, ``(None, typed_error)`` on
    rejection. Rejections are total: no partial acceptance, no coercion of a
    bad value into a plausible one, no routing on a guess.
    """
    if not isinstance(payload, Mapping):
        return None, invalid_args("request_must_be_object")

    unknown = sorted(set(payload.keys()) - _ALLOWED_KEYS)
    if unknown:
        return None, invalid_args("unknown_fields", unknown)

    if "intent" not in payload:
        return None, invalid_args("intent_required")
    raw_intent = payload["intent"]
    if not isinstance(raw_intent, str) or not raw_intent.strip():
        return None, invalid_args("intent_must_be_nonempty_string")
    intent = raw_intent.strip().upper()
    if intent not in INTENTS:
        return None, invalid_args(
            "unknown_intent", {"received": raw_intent.strip()[:64],
                               "allowed": sorted(INTENTS)})

    fields = ("project_id", "project_name", "query", "metric_id",
              "metric_name", "period", "entity_id", "entity_type")
    values: dict[str, Any] = {"intent": intent}
    for name in fields:
        val, err = _opt_str(payload, name)
        if err:
            return None, invalid_args(err)
        values[name] = val

    sv, err = _opt_str(payload, "source_version_id", max_len=_LOCATOR_MAX)
    if err:
        return None, invalid_args(err)
    values["source_version_id"] = sv

    locator, err = _opt_str(payload, "locator", max_len=_LOCATOR_MAX)
    if err:
        return None, invalid_args(err)
    if locator is not None and _LOCATOR_SHAPE_RE.match(locator) is None:
        return None, invalid_args("locator_must_be_structural_locator",
                                  {"received": locator[:64]})
    values["locator"] = locator

    if values["entity_type"] is not None:
        et = values["entity_type"].lower()
        if et not in SUPPORTED_ENTITY_TYPES:
            return None, invalid_args("unsupported_entity_type",
                                      {"received": et})
        values["entity_type"] = et

    for key in ("as_of", "known_as_of", "since"):
        val, err = _opt_ts(payload, key)
        if err:
            return None, invalid_args(err)
        values[key] = val

    dimensions, err = _opt_dimensions(payload)
    if err:
        return None, invalid_args(err)
    values["dimensions"] = dimensions

    entity_types, err = _opt_entity_types(payload)
    if err:
        return None, invalid_args(err)
    values["entity_types"] = entity_types

    for key in ("include_historical", "include_provisional",
                "analysis_allowed", "follow_evidence"):
        val, err = _opt_bool(payload, key)
        if err:
            return None, invalid_args(err)
        values[key] = val

    limit, err = _opt_limit(payload)
    if err:
        return None, invalid_args(err)
    values["limit"] = limit

    # Per-intent required arguments.
    present = {k for k, v in values.items()
               if v not in (None, "", (), {}) }
    groups = _REQUIRED[intent]
    if not any(all(k in present for k in group) for group in groups):
        need = " OR ".join("+".join(g) if g else "(no args)" for g in groups)
        return None, invalid_args("missing_required_arguments",
                                  {"intent": intent, "required": need})

    # ANALYSIS_DATA is non-canonical by definition: it must be an explicit
    # opt-in, so it can never be reached by a generic question.
    if intent == INTENT_ANALYSIS_DATA and not values["analysis_allowed"]:
        return None, invalid_args(
            "analysis_allowed_required_for_ANALYSIS_DATA")

    # follow_evidence is a FORMAL_METRIC-only multi-intent switch. Accepting it
    # elsewhere would silently change another intent's meaning, so it is
    # rejected rather than ignored.
    if values["follow_evidence"] and intent != INTENT_FORMAL_METRIC:
        return None, invalid_args(
            "follow_evidence_only_valid_for_FORMAL_METRIC",
            {"intent": intent})

    return QueryRequest(**values), None


__all__ = [
    "SCHEMA", "MAX_TOOL_HOPS",
    "MANDATORY_INTENTS", "INTENTS", "RESULT_STATES",
    "SUPPORTED_ENTITY_TYPES",
    "STATE_OK", "STATE_NOT_FOUND", "STATE_UNRESOLVED",
    "STATE_TEMPORAL_HISTORY_INCOMPLETE", "STATE_EVIDENCE_NOT_AVAILABLE",
    "STATE_RETRIEVAL_STALE", "STATE_NON_CANONICAL_ONLY",
    "STATE_AMBIGUOUS_QUERY", "STATE_INVALID_ARGS",
    "INTENT_PROJECT_CURRENT", "INTENT_KNOWLEDGE_CURRENT",
    "INTENT_TEMPORAL_QUERY", "INTENT_FORMAL_METRIC",
    "INTENT_EVIDENCE_QUERY", "INTENT_RECENT_CHANGES",
    "INTENT_ANALYSIS_DATA", "INTENT_ENTITY_AUTHORITY", "INTENT_TIMELINE",
    "QueryRequest", "validate_request", "invalid_args",
]
