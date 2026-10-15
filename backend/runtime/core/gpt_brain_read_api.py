"""Read-only Brain Layer APIs.  No Formal State CRUD."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from gpt_brain_bootstrap_runner import BootstrapRunner, build_live_runner, default_paths, probe_gpt_invocation_path
from gpt_brain_common import LockedJsonStore, text
from gpt_brain_data_layer import MetricDataLayer
from gpt_brain_project_builder import ProjectBuilder, default_project_catalog


def _live_builder() -> ProjectBuilder:
    paths = default_paths()
    return ProjectBuilder(
        LockedJsonStore(paths["project_builder"]),
        default_project_catalog(),
    )


def _live_data_layer() -> MetricDataLayer:
    paths = default_paths()
    return MetricDataLayer(LockedJsonStore(paths["data_layer"]))


def h_runner_status(runner: BootstrapRunner, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return runner.status()


def h_query_metrics(layer: MetricDataLayer, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    return layer.query_observations(
        project=params.get("project"),
        metric=params.get("metric"),
        period_from=params.get("period_from"),
        period_to=params.get("period_to"),
        dimensions=params.get("dimensions"),
        source_version_id=params.get("source_version_id"),
        include_conflicts=bool(params.get("include_conflicts", True)),
        limit=int(params.get("limit") or 50),
        offset=int(params.get("offset") or 0),
        redact=bool(params.get("redact", True)),
    )


def h_list_data_assets(layer: MetricDataLayer, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    return layer.list_data_assets(
        project=params.get("project"),
        source_version_id=params.get("source_version_id"),
        limit=int(params.get("limit") or 50),
        offset=int(params.get("offset") or 0),
        redact=bool(params.get("redact", True)),
    )


def h_list_data_conflicts(layer: MetricDataLayer, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    return layer.list_conflicts(
        project=params.get("project"),
        metric=params.get("metric"),
        limit=int(params.get("limit") or 50),
        offset=int(params.get("offset") or 0),
        redact=bool(params.get("redact", True)),
    )


def h_get_data_observation(layer: MetricDataLayer, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    fingerprint = params.get("fingerprint")
    if not fingerprint:
        raise ValueError("fingerprint is required")
    record = layer.get_observation(str(fingerprint), redact=bool(params.get("redact", True)))
    if record is None:
        return {"found": False, "formal_state_write": False}
    return {"found": True, "observation": record, "formal_state_write": False}


def h_get_data_asset(layer: MetricDataLayer, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    fingerprint = params.get("fingerprint")
    if not fingerprint:
        raise ValueError("fingerprint is required")
    record = layer.get_data_asset(str(fingerprint), redact=bool(params.get("redact", True)))
    if record is None:
        return {"found": False, "formal_state_write": False}
    return {"found": True, "data_asset": record, "formal_state_write": False}


def h_project_data_summary(layer: MetricDataLayer, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    project = params.get("project") or params.get("project_name")
    if not project:
        raise ValueError("project is required")
    return layer.project_data_summary(
        str(project),
        observation_limit=int(params.get("observation_limit") or 20),
        asset_limit=int(params.get("asset_limit") or 20),
        conflict_limit=int(params.get("conflict_limit") or 20),
        redact=bool(params.get("redact", True)),
    )


def h_get_project_context(builder: ProjectBuilder, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    project_id = params.get("project_id")
    if not project_id:
        raise ValueError("project_id is required")
    include_drafts = bool(params.get("include_drafts", True))
    include_data = bool(params.get("include_data", False))
    if include_drafts:
        record = builder.get_draft_context(str(project_id), redact=bool(params.get("redact", True)))
    else:
        record = builder.get_context(str(project_id), redact=bool(params.get("redact", True)))
    if record is None:
        return {"project_id": project_id, "found": False, "formal_project_mutated": False}
    payload: dict[str, Any] = {
        "found": True,
        "project_context": record,
        "formal_project_mutated": False,
    }
    if include_data:
        layer = params.get("_data_layer")
        if layer is None:
            layer = _live_data_layer()
        project_name = text(record.get("project_name") if isinstance(record, Mapping) else "")
        formal = (record.get("formal_project") or {}) if isinstance(record, Mapping) else {}
        project_key = project_name or formal.get("project_name") or project_id
        payload["data_summary"] = layer.project_data_summary(
            str(project_key),
            observation_limit=int(params.get("observation_limit") or 20),
            asset_limit=int(params.get("asset_limit") or 20),
            conflict_limit=int(params.get("conflict_limit") or 20),
            redact=bool(params.get("redact", True)),
        )
    return payload


def h_list_project_contexts(builder: ProjectBuilder, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    if bool(params.get("include_drafts", True)):
        return builder.list_draft_contexts(redact=bool(params.get("redact", True)))
    return builder.list_contexts(redact=bool(params.get("redact", True)))


def h_search_project_contexts(builder: ProjectBuilder, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    return builder.search_contexts(
        query=params.get("query"),
        project_id=params.get("project_id"),
        limit=int(params.get("limit") or 50),
        offset=int(params.get("offset") or 0),
        redact=bool(params.get("redact", True)),
    )


def h_list_project_proposals(builder: ProjectBuilder, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return builder.list_proposals()


def h_invocation_probe(params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return probe_gpt_invocation_path()


READ_TOOL_SCHEMAS = {
    "gpt_brain_runner_status": {
        "description": "Read Bootstrap Runner + Brain status. Analysis plane only.",
        "handler": "h_runner_status",
        "permission": "READ_ONLY",
    },
    "gpt_brain_query_metrics": {
        "description": "Query metric observations by project/metric/period/dimensions. Default redacted.",
        "handler": "h_query_metrics",
        "permission": "READ_ONLY",
    },
    "gpt_brain_list_data_assets": {
        "description": "List dataset-level metadata. Analysis index only.",
        "handler": "h_list_data_assets",
        "permission": "READ_ONLY",
    },
    "gpt_brain_list_data_conflicts": {
        "description": "List metric conflicts from the analysis data layer.",
        "handler": "h_list_data_conflicts",
        "permission": "READ_ONLY",
    },
    "gpt_brain_get_data_observation": {
        "description": "Get one analysis-plane metric observation by fingerprint.",
        "handler": "h_get_data_observation",
        "permission": "READ_ONLY",
    },
    "gpt_brain_get_data_asset": {
        "description": "Get one analysis-plane data asset by fingerprint.",
        "handler": "h_get_data_asset",
        "permission": "READ_ONLY",
    },
    "gpt_brain_project_data_summary": {
        "description": "Paginated data summary for a project from the analysis layer.",
        "handler": "h_project_data_summary",
        "permission": "READ_ONLY",
    },
    "gpt_brain_get_project_context": {
        "description": "Read Project Builder snapshot for a human-defined project.",
        "handler": "h_get_project_context",
        "permission": "READ_ONLY",
    },
    "gpt_brain_list_project_contexts": {
        "description": "List Project Builder snapshots. No Formal Project CRUD.",
        "handler": "h_list_project_contexts",
        "permission": "READ_ONLY",
    },
    "gpt_brain_search_project_contexts": {
        "description": "Search/list project draft contexts with authority metadata.",
        "handler": "h_search_project_contexts",
        "permission": "READ_ONLY",
    },
    "gpt_brain_list_project_proposals": {
        "description": "List PROJECT_PROPOSAL items awaiting user decision.",
        "handler": "h_list_project_proposals",
        "permission": "READ_ONLY",
    },
}


def register_read_tools_if_gateway_available(runner: BootstrapRunner) -> dict[str, Any]:
    """Best-effort MCP registration.  Never registers Formal State write tools."""
    try:
        from app.tools.registry import register_tool, SENS_INTERNAL, SENS_CONFIDENTIAL
    except Exception as exc:
        return {"registered": False, "reason": f"gateway registry unavailable: {exc}"}

    def status(_p):
        return h_runner_status(runner, _p)

    def metrics(p):
        if runner.data_layer is None:
            raise RuntimeError("data layer not configured")
        return h_query_metrics(runner.data_layer, p)

    def assets(p):
        if runner.data_layer is None:
            raise RuntimeError("data layer not configured")
        return h_list_data_assets(runner.data_layer, p)

    def conflicts(p):
        if runner.data_layer is None:
            raise RuntimeError("data layer not configured")
        return h_list_data_conflicts(runner.data_layer, p)

    def obs(p):
        if runner.data_layer is None:
            raise RuntimeError("data layer not configured")
        return h_get_data_observation(runner.data_layer, p)

    def asset(p):
        if runner.data_layer is None:
            raise RuntimeError("data layer not configured")
        return h_get_data_asset(runner.data_layer, p)

    def summary(p):
        if runner.data_layer is None:
            raise RuntimeError("data layer not configured")
        return h_project_data_summary(runner.data_layer, p)

    def ctx(p):
        if runner.project_builder is None:
            raise RuntimeError("project builder not configured")
        params = dict(p or {})
        if runner.data_layer is not None:
            params["_data_layer"] = runner.data_layer
        return h_get_project_context(runner.project_builder, params)

    def ctxs(p):
        if runner.project_builder is None:
            raise RuntimeError("project builder not configured")
        return h_list_project_contexts(runner.project_builder, p)

    def search_ctx(p):
        if runner.project_builder is None:
            raise RuntimeError("project builder not configured")
        return h_search_project_contexts(runner.project_builder, p)

    def props(p):
        if runner.project_builder is None:
            raise RuntimeError("project builder not configured")
        return h_list_project_proposals(runner.project_builder, p)

    _R = {"type": "object", "properties": {}, "additionalProperties": True}
    register_tool("gpt_brain_runner_status", "Runner status.", _R, status, purpose="Inspect runner.", data_sensitivity=SENS_INTERNAL)
    register_tool("gpt_brain_query_metrics", "Query metrics.", _R, metrics, purpose="Query data layer.", data_sensitivity=SENS_CONFIDENTIAL)
    register_tool("gpt_brain_list_data_assets", "List data assets.", _R, assets, purpose="List datasets.", data_sensitivity=SENS_INTERNAL)
    register_tool("gpt_brain_list_data_conflicts", "List data conflicts.", _R, conflicts, purpose="List conflicts.", data_sensitivity=SENS_INTERNAL)
    register_tool("gpt_brain_get_data_observation", "Get data observation.", _R, obs, purpose="Read observation.", data_sensitivity=SENS_CONFIDENTIAL)
    register_tool("gpt_brain_get_data_asset", "Get data asset.", _R, asset, purpose="Read asset.", data_sensitivity=SENS_INTERNAL)
    register_tool("gpt_brain_project_data_summary", "Project data summary.", _R, summary, purpose="Summarize project data.", data_sensitivity=SENS_CONFIDENTIAL)
    register_tool("gpt_brain_get_project_context", "Get project context.", _R, ctx, purpose="Read project snapshot.", data_sensitivity=SENS_INTERNAL)
    register_tool("gpt_brain_list_project_contexts", "List project contexts.", _R, ctxs, purpose="List snapshots.", data_sensitivity=SENS_INTERNAL)
    register_tool("gpt_brain_search_project_contexts", "Search project contexts.", _R, search_ctx, purpose="Search snapshots.", data_sensitivity=SENS_INTERNAL)
    register_tool("gpt_brain_list_project_proposals", "List project proposals.", _R, props, purpose="List proposals.", data_sensitivity=SENS_INTERNAL)
    return {"registered": True, "tools": list(READ_TOOL_SCHEMAS)}


def build_project_page_projection(
    project_id: str,
    *,
    include_data: bool = True,
    redact: bool = True,
) -> dict[str, Any]:
    """Compose draft project context + data summary for the workspace project page."""
    builder = _live_builder()
    layer = _live_data_layer()
    draft = builder.get_draft_context(project_id, redact=redact)
    if draft is None:
        return {"found": False, "project_id": project_id}
    project_name = text(draft.get("project_name"))
    data_summary = (
        layer.project_data_summary(project_name, redact=redact)
        if include_data and project_name
        else None
    )
    return {
        "found": True,
        "project_id": project_id,
        "draft_context": draft,
        "data_summary": data_summary,
        "authority_level": "draft",
        "is_canonical": False,
        "formal_state_write": False,
    }
