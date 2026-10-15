"""PAW v1.3D — Query Planner.

The Planner turns a typed payload into a BOUNDED plan of Gateway steps and runs
it. It is deliberately small:

* deterministic routing when the signal is obvious (an explicit ``intent``, or
  an explicit structural marker such as a SourceVersion id);
* typed agent semantic understanding supplies ``intent`` — PAW Core never
  guesses semantics from prose;
* a hard hop bound (``paw_query_contract.MAX_TOOL_HOPS``).

What the Planner is NOT
-----------------------
* NOT an Intent Classification Service.
* NOT a daemon, NOT a workflow engine, NOT an event bus.
* NO LangGraph / multi-agent orchestration.
* NO new judge model, NO model call of any kind.

``MODEL_CALLS = 0``. The semantic step belongs to the Query Agent, which
expresses its understanding as the typed contract; the Planner only routes,
validates and bounds.

Multi-intent
------------
``FORMAL_METRIC`` with ``follow_evidence=true`` expresses "what is the number,
and what is it based on". The plan is two steps: the metric leg, then the
evidence leg built from THAT metric's own refs. There is no second fuzzy
search: if the metric chain carries no structural locator, the evidence leg
returns the honest ``EVIDENCE_NOT_AVAILABLE`` disclosure together with the
SourceVersion refs it does have.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

_WS_ROOT = str(Path(__file__).resolve().parents[1 if "backend" in str(Path(__file__).parent.name) else 2])
if _WS_ROOT not in sys.path:
    sys.path.append(_WS_ROOT)

from . import paw_query_contract as qc  # noqa: E402
from . import paw_retrieval_gateway as gw  # noqa: E402

SCHEMA = "paw.query-planner.v13d/1"

# Markers that make routing deterministic WITHOUT semantic inference.
_MARKER_ROUTES = (
    ("source_version_id", qc.INTENT_EVIDENCE_QUERY),
    ("locator", qc.INTENT_EVIDENCE_QUERY),
    ("metric_id", qc.INTENT_FORMAL_METRIC),
    ("as_of", qc.INTENT_TEMPORAL_QUERY),
    ("entity_id", qc.INTENT_ENTITY_AUTHORITY),
)


@dataclass(frozen=True)
class PlanStep:
    """One bounded Gateway call."""

    step_id: str
    intent: str
    request: qc.QueryRequest
    derived_from: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "intent": self.intent,
            "derived_from": self.derived_from,
            "argument_keys": sorted(
                k for k, v in self.request.to_dict().items()
                if v not in (None, "", [], {}, False)),
        }


@dataclass(frozen=True)
class Plan:
    """A bounded, typed, deterministic plan."""

    steps: tuple[PlanStep, ...]
    routing_basis: str
    multi_intent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "routing_basis": self.routing_basis,
            "multi_intent": self.multi_intent,
            "step_count": len(self.steps),
            "max_tool_hops": qc.MAX_TOOL_HOPS,
            "within_hop_budget": len(self.steps) <= qc.MAX_TOOL_HOPS,
            "steps": [s.to_dict() for s in self.steps],
            "deterministic": True,
            "model_call": False,
        }


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------
def _infer_intent(payload: Mapping[str, Any]) -> tuple[str | None, str]:
    """Deterministic routing from EXPLICIT structural markers only.

    No prose is interpreted. If no structural marker is present the result is
    ``(None, "ambiguous")`` and the caller must fail closed.
    """
    for key, intent in _MARKER_ROUTES:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return intent, f"structural_marker:{key}"
    if (isinstance(payload.get("project_id"), str)
            and payload.get("project_id", "").strip()
            and not str(payload.get("query") or "").strip()):
        return qc.INTENT_PROJECT_CURRENT, "structural_marker:project_id"
    return None, "ambiguous"


def plan(payload: Mapping[str, Any]) -> tuple[Plan | None, dict | None]:
    """Build a bounded plan from a typed payload.

    Returns ``(plan, None)`` or ``(None, typed_error)``.
    """
    if not isinstance(payload, Mapping):
        return None, qc.invalid_args("request_must_be_object")

    if "intent" in payload:
        basis = "explicit_intent"
        intent = payload.get("intent")
    else:
        intent, basis = _infer_intent(payload)
        if intent is None:
            # Fail closed instead of guessing what the user meant.
            return None, qc.invalid_args(
                "intent_required",
                {"reason": "no_explicit_intent_and_no_structural_marker",
                 "routing_basis": basis})

    request, error = qc.validate_request({**payload, "intent": intent})
    if request is None:
        return None, error

    steps = [PlanStep(step_id="s1", intent=request.intent, request=request)]
    multi = False
    if request.follow_evidence and request.intent == qc.INTENT_FORMAL_METRIC:
        multi = True
        steps.append(PlanStep(
            step_id="s2",
            intent=qc.INTENT_EVIDENCE_QUERY,
            request=request,
            derived_from="s1:metric_evidence_refs",
        ))

    if len(steps) > qc.MAX_TOOL_HOPS:
        return None, qc.invalid_args("plan_exceeds_max_tool_hops",
                                     {"steps": len(steps)})

    return Plan(steps=tuple(steps), routing_basis=basis,
                multi_intent=multi), None


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def _evidence_from_metric_step(
    metric_response: Mapping[str, Any],
    request: qc.QueryRequest,
    path: str,
) -> dict[str, Any]:
    """Build the evidence leg from the metric's OWN refs.

    The metric chain supplies SourceVersion refs and (when present) structural
    locators. It is NEVER allowed to invent a locator: with no locator the leg
    fails closed and returns what it does know.
    """
    metrics = metric_response.get("metrics") or []
    source_versions: list[str] = []
    locators: list[str] = []
    change_refs: list[dict[str, Any]] = []
    for metric in metrics:
        for obs in (metric.get("effective_current_observations")
                    or metric.get("observations") or []):
            source_versions.extend(obs.get("source_version_ids") or [])
            for ref in (obs.get("evidence_refs") or []):
                text = str(ref)
                if "[" in text and text.endswith("]"):
                    locators.append(text)
            if obs.get("change_id") or obs.get("commit_ref"):
                change_refs.append({
                    "observation_id": obs.get("observation_id"),
                    "dimensions": obs.get("dimensions"),
                    "change_id": obs.get("change_id"),
                    "commit_ref": obs.get("commit_ref"),
                })

    source_versions = list(dict.fromkeys(source_versions))
    locators = list(dict.fromkeys(locators))

    if not locators:
        return gw.envelope(
            "evidence_query", qc.STATE_EVIDENCE_NOT_AVAILABLE,
            authority_state=None,
            canonical=False,
            source_refs=source_versions,
            evidence_refs=[],
            unresolved_reason="locator_not_persisted_in_metric_chain",
            provisional_reason=("metric lineage proves the SourceVersion and the "
                                "governed change, but the structural locator is "
                                "not persisted (evidence_locator=PARTIAL)"),
            source_version_id=(source_versions[0] if source_versions else None),
            locator=None,
            block_type=None,
            raw_artifact_available=None,
            identity_verification=None,
            change_refs=change_refs,
            governance_basis_available=bool(change_refs),
            locator_invented=False,
            multi_intent_step="evidence_from_metric",
            next_step=("supply the structural locator from a real EvidenceRef "
                       "to read the exact block"),
        )

    # A real locator is available: read the FIRST one through the Gateway.
    first_sv = source_versions[0] if source_versions else ""
    response = gw.execute(
        {"intent": qc.INTENT_EVIDENCE_QUERY,
         "source_version_id": first_sv, "locator": locators[0]},
        db_path=path)
    response["multi_intent_step"] = "evidence_from_metric"
    response["change_refs"] = change_refs
    return response


def run_plan(plan_: Plan, *, db_path: str | None = None) -> dict[str, Any]:
    """Execute a plan. Bounded, read-only, deterministic."""
    path = str(db_path or gw.DEFAULT_DB)
    steps_out: list[dict[str, Any]] = []
    hop = 0
    bounded = False

    for step in plan_.steps:
        hop += 1
        if hop > qc.MAX_TOOL_HOPS:
            bounded = True
            break
        if step.derived_from:
            metric_response = steps_out[-1]["response"]
            response = _evidence_from_metric_step(
                metric_response, step.request, path)
        else:
            response = gw.execute(step.request, db_path=path, hops=hop)
        response["hops"] = hop
        steps_out.append({
            "step_id": step.step_id,
            "intent": step.intent,
            "derived_from": step.derived_from,
            "response": response,
        })

    primary = steps_out[0]["response"] if steps_out else {}
    states = [s["response"].get("result_state") for s in steps_out]
    return {
        "schema": SCHEMA,
        "plan": plan_.to_dict(),
        "steps": steps_out,
        "step_count": len(steps_out),
        "hop_count": hop,
        "bounded": bounded,
        "max_tool_hops": qc.MAX_TOOL_HOPS,
        "result_state": primary.get("result_state"),
        "all_step_states": states,
        "any_step_failed": any(
            s not in (qc.STATE_OK, qc.STATE_NON_CANONICAL_ONLY)
            for s in states),
        "entry_point": "PLANNER->GATEWAY",
        "legacy_path_used": False,
        "model_call": False,
    }


def query(
    payload: Mapping[str, Any],
    *,
    db_path: str | None = None,
) -> dict[str, Any]:
    """The ONE Agent entry: plan then execute. Fail closed end to end."""
    plan_, error = plan(payload)
    if plan_ is None:
        error = dict(error or {})
        error["entry_point"] = "PLANNER->GATEWAY"
        return error
    return run_plan(plan_, db_path=db_path)


__all__ = ["SCHEMA", "PlanStep", "Plan", "plan", "run_plan", "query"]
