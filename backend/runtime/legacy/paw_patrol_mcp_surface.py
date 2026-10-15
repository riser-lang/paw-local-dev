#!/usr/bin/env python3
"""MCP tool-surface contract for GPT Patrol discovery.

ChatGPT GPT Agent Scheduled Task is the semantic owner. This module only
defines which tools must appear in MCP ``tools/list`` and in which order.
It never analyzes Capture text, never sets processed_at, and never writes
Formal State.

ChatGPT connectors can truncate or deprioritize late tools in a large catalog.
Pinned tools are therefore placed at the front of ``tools/list``.

PAW v1.3D: the semantic patrol capability is exposed twice — the legacy
``gpt_patrol_*`` names (kept for the existing ChatGPT Workspace Agent) and
the provider-neutral ``patrol_*`` names (canonical for every future agent
consumer: ChatGPT, Gemini Spark, Agy, Claude). Both delegate to the same
implementation; neither writes Formal State.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from paw_patrol_contract import FORMAL_STATE_BYPASS
from paw_patrol_discovery import AUTHORITATIVE_TOOL, CAPTURE_DECIDE_TOOL, CAPTURE_READ_TOOL, CLAIM_TOOL

GET_WORKSPACE_STATUS = "get_workspace_status"
GET_TODAY = "get_today"
SOURCE_INCREMENTAL_PREPARE_TOOL = "gpt_patrol_prepare_semantic_items"
SOURCE_INCREMENTAL_READ_TOOL = "list_incremental_terra_review"
SOURCE_INCREMENTAL_CLAIM_TOOL = "claim_incremental_terra_review"
SOURCE_INCREMENTAL_SUBMIT_TOOL = "submit_incremental_terra_review"
GATEWAY_SOURCE_INCREMENTAL_READ_TOOL = "gpt_patrol_fetch_incremental_context"

# GPT Primary Semantic Queue tools (P0 architecture correction)
GPT_SEMANTIC_LIST_TOOL = "gpt_patrol_list_semantic_items"
GPT_SEMANTIC_CLAIM_TOOL = "gpt_patrol_claim_semantic_items"
GPT_SEMANTIC_SUBMIT_TOOL = "gpt_patrol_submit_semantic_decision"

# Provider-Neutral Patrol Capability Surface (v1.3D exposure repair).
# Same PAW Patrol Capability implementation, provider-neutral names: the
# canonical entries for every future agent consumer (ChatGPT, Gemini Spark,
# Agy, Claude). The gpt_* names above are kept for backward compatibility.
NEUTRAL_PREPARE_TOOL = "patrol_prepare_semantic_items"
NEUTRAL_LIST_TOOL = "patrol_list_semantic_items"
NEUTRAL_CLAIM_TOOL = "patrol_claim_semantic_items"
NEUTRAL_SUBMIT_TOOL = "patrol_submit_semantic_decision"
NEUTRAL_PATROL_TOOLS = (
    NEUTRAL_PREPARE_TOOL,
    NEUTRAL_LIST_TOOL,
    NEUTRAL_CLAIM_TOOL,
    NEUTRAL_SUBMIT_TOOL,
)

# PAW v1.2.1 phase 4: allowlist entries that are NOT registered in the R13
# tool surface. They can never match a real tool, so they are removed from
# ROUTINE_OAUTH_PATROL_TOOLS / PINNED_MCP_TOOLS to keep the contract honest.
# Recorded here so the drift stays visible instead of silently recurring.
PHANTOM_ALLOWLIST_ENTRIES = (
    GATEWAY_SOURCE_INCREMENTAL_READ_TOOL,   # never registered in R13
    "gpt_patrol_status",
    "gpt_patrol_list_review_work",
    "gpt_patrol_claim_review_work",
    "gpt_patrol_record_review_outcome",
)

# Must remain callable after exposure repair.
REQUIRED_EXISTING_TOOLS = (
    GET_WORKSPACE_STATUS,
    GET_TODAY,
    CAPTURE_READ_TOOL,
    CAPTURE_DECIDE_TOOL,
    SOURCE_INCREMENTAL_PREPARE_TOOL,
    SOURCE_INCREMENTAL_READ_TOOL,
    GPT_SEMANTIC_LIST_TOOL,
    GPT_SEMANTIC_CLAIM_TOOL,
    GPT_SEMANTIC_SUBMIT_TOOL,
    *NEUTRAL_PATROL_TOOLS,
)

# Authoritative discovery plus the read/claim/decide path the Agent follows.
REQUIRED_DISCOVERY_TOOLS = (
    AUTHORITATIVE_TOOL,
    CLAIM_TOOL,
    CAPTURE_READ_TOOL,
    CAPTURE_DECIDE_TOOL,
)

PROJECT_FIRST_CONTEXT_TOOL = "gpt_patrol_project_context"
PROJECT_FIRST_IMPACTED_TOOL = "gpt_patrol_list_impacted_projects"

# First-page pin order for ChatGPT Workspace Agent tools/list.
PINNED_MCP_TOOLS = (
    GET_WORKSPACE_STATUS,
    GET_TODAY,
    AUTHORITATIVE_TOOL,
    SOURCE_INCREMENTAL_PREPARE_TOOL,
    GPT_SEMANTIC_LIST_TOOL,
    GPT_SEMANTIC_CLAIM_TOOL,
    GPT_SEMANTIC_SUBMIT_TOOL,
    NEUTRAL_PREPARE_TOOL,
    NEUTRAL_LIST_TOOL,
    NEUTRAL_CLAIM_TOOL,
    NEUTRAL_SUBMIT_TOOL,
    CAPTURE_READ_TOOL,
    CLAIM_TOOL,
    CAPTURE_DECIDE_TOOL,
    SOURCE_INCREMENTAL_READ_TOOL,
    SOURCE_INCREMENTAL_CLAIM_TOOL,
    SOURCE_INCREMENTAL_SUBMIT_TOOL,
    PROJECT_FIRST_CONTEXT_TOOL,
    PROJECT_FIRST_IMPACTED_TOOL,
)

# Discovery must survive typical ChatGPT first-page / first-N truncation.
MAX_DISCOVERY_LIST_INDEX = 5

# Bump when OAuth/443 routine patrol tool surface changes (ChatGPT connector cache).
OAUTH_PATROL_SURFACE_VERSION = "provider-neutral-patrol-20260922"

# Bounded routine surface for OAuth/443 ChatGPT Workspace Agent (Project-First v2).
ROUTINE_OAUTH_PATROL_TOOLS: frozenset[str] = frozenset({
    # Preflight
    GET_WORKSPACE_STATUS,
    GET_TODAY,
    "get_recent_activity",
    "list_projects",
    # GPT Primary Semantic Queue (P0 architecture correction)
    SOURCE_INCREMENTAL_PREPARE_TOOL,
    GPT_SEMANTIC_LIST_TOOL,
    GPT_SEMANTIC_CLAIM_TOOL,
    GPT_SEMANTIC_SUBMIT_TOOL,
    # Provider-Neutral Patrol Capability Surface (v1.3D exposure repair):
    # same implementation, provider-neutral names for every agent consumer.
    NEUTRAL_PREPARE_TOOL,
    NEUTRAL_LIST_TOOL,
    NEUTRAL_CLAIM_TOOL,
    NEUTRAL_SUBMIT_TOOL,
    # Read plane
    "search_knowledge",
    "get_knowledge_detail",
    "get_project_context",
    "search_memory_context",
    "get_pending_review",
    "search_source",
    "get_source_content",
    # Project-first synthesis
    PROJECT_FIRST_CONTEXT_TOOL,
    PROJECT_FIRST_IMPACTED_TOOL,
    "get_project_draft_context",
    "list_project_draft_contexts",
    "search_project_draft_contexts",
    "query_project_data",
    "search_drafts",
    "get_draft_detail",
    # Discovery + capture
    AUTHORITATIVE_TOOL,
    CLAIM_TOOL,
    CAPTURE_READ_TOOL,
    CAPTURE_DECIDE_TOOL,
    # Incremental semantic preparation (Reader Ready -> TERRA_PENDING)
    # PAW v1.2.1 phase 4: this tool is advertised by
    # gpt_patrol_discover_work.next_actions whenever reader_ready_count > 0.
    # It was absent from this allowlist, so the OAuth/443 transform removed it
    # from tools/list and returned None from get_tool: the Agent was told to
    # call a tool it could not see. That is a hard contract violation.
    SOURCE_INCREMENTAL_PREPARE_TOOL,
    # Incremental Terra review
    SOURCE_INCREMENTAL_READ_TOOL,
    SOURCE_INCREMENTAL_CLAIM_TOOL,
    SOURCE_INCREMENTAL_SUBMIT_TOOL,
    # Post-approval governed write
    "gpt_patrol_list_accepted_writes",
    "gpt_patrol_consume_accepted_writes",
    "gpt_patrol_list_decision_only_successors",
    "gpt_patrol_prepare_successor",
    "gpt_patrol_list_post_approval_recovery",
    "gpt_patrol_claim_post_approval_recovery",
    "gpt_patrol_submit_post_approval_execution",
    # Post-Opus
    "gpt_brain_get_opus_queue_status",
    "gpt_brain_get_post_opus_work",
    "gpt_brain_record_post_opus_bundle",
    # Bootstrap / incremental health
    "gpt_brain_bootstrap_status",
    "gpt_brain_acceptance_report",
    # Governed candidate path
    "submit_candidate",
    "attach_candidate_review_brief",
    # Patrol review statistics
    "gpt_brain_review_queue_stats",
    "gpt_brain_record_user_decision_cluster_receipt",
})

# Must never appear on the routine OAuth patrol surface.
FORBIDDEN_ROUTINE_PATROL_TOOLS: frozenset[str] = frozenset({
    "agy_server_admin_task",
    "execute_command",
    "control_service",
    "create_project",
    "update_project",
    "update_todo",
    "update_knowledge",
    "create_knowledge",
    "create_todo",
    "commit_approved_change",
    "gpt_brain_get_next_sources",
    "gpt_brain_get_review_bundles",
    "gpt_brain_record_bundle_review",
})

SERVER_SIDE_GPT_TRIGGER = "DISABLED"
SEMANTIC_PROCESSING_ON_SERVER = "NO"
GPT_PATROL_TRIGGER_OWNER = "CHATGPT_GPT_AGENT_SCHEDULED_TASK"


def _tool_name(tool: Any) -> str:
    if isinstance(tool, str):
        return tool
    name = getattr(tool, "name", None)
    if name:
        return str(name)
    if isinstance(tool, dict):
        return str(tool.get("name") or "")
    return ""


def reorder_mcp_tools(tools: Sequence[Any]) -> list[Any]:
    """Stable pin of patrol discovery tools to the front of tools/list."""
    original: list[Any] = list(tools)
    by_name = {_tool_name(tool): tool for tool in original if _tool_name(tool)}
    pinned: list[Any] = []
    seen: set[str] = set()
    for name in PINNED_MCP_TOOLS:
        tool = by_name.get(name)
        if tool is not None and name not in seen:
            pinned.append(tool)
            seen.add(name)
    rest = [tool for tool in original if _tool_name(tool) not in seen]
    return pinned + rest


def mcp_tool_names(tools: Iterable[Any]) -> list[str]:
    return [_tool_name(tool) for tool in tools if _tool_name(tool)]


def discovery_tool_index(names: Sequence[str]) -> int | None:
    try:
        return list(names).index(AUTHORITATIVE_TOOL)
    except ValueError:
        return None


def discovery_is_exposed(names: Sequence[str]) -> bool:
    idx = discovery_tool_index(names)
    return idx is not None and idx <= MAX_DISCOVERY_LIST_INDEX


def filter_routine_patrol_tools(tools: Sequence[Any]) -> list[Any]:
    """Keep only bounded Project-First v2 routine tools, then pin discovery."""
    allowed = ROUTINE_OAUTH_PATROL_TOOLS
    filtered = [tool for tool in tools if _tool_name(tool) in allowed]
    return reorder_mcp_tools(filtered)


def routine_surface_contract(names: Sequence[str]) -> dict[str, Any]:
    """Acceptance contract for OAuth/443 routine patrol tool surface."""
    present = set(names)
    required_groups = {
        "preflight": {GET_WORKSPACE_STATUS, GET_TODAY, "get_recent_activity", "list_projects"},
        "terra": {
            SOURCE_INCREMENTAL_PREPARE_TOOL,
            SOURCE_INCREMENTAL_READ_TOOL,
            SOURCE_INCREMENTAL_CLAIM_TOOL,
            SOURCE_INCREMENTAL_SUBMIT_TOOL,
        },
        "capture": {CAPTURE_READ_TOOL, CAPTURE_DECIDE_TOOL},
        "semantic_neutral": set(NEUTRAL_PATROL_TOOLS),
        "post_approval": {
            "gpt_patrol_list_accepted_writes",
            "gpt_patrol_consume_accepted_writes",
            "gpt_patrol_list_decision_only_successors",
            "gpt_patrol_prepare_successor",
            "gpt_patrol_list_post_approval_recovery",
            "gpt_patrol_claim_post_approval_recovery",
            "gpt_patrol_submit_post_approval_execution",
        },
        "post_opus": {
            "gpt_brain_get_opus_queue_status",
            "gpt_brain_get_post_opus_work",
            "gpt_brain_record_post_opus_bundle",
        },
        "bootstrap": {"gpt_brain_bootstrap_status", "gpt_brain_acceptance_report"},
        "project_first": {PROJECT_FIRST_CONTEXT_TOOL, PROJECT_FIRST_IMPACTED_TOOL, "get_project_context"},
    }
    group_status = {key: req <= present for key, req in required_groups.items()}
    forbidden_present = sorted(present & FORBIDDEN_ROUTINE_PATROL_TOOLS)
    legacy_drain_present = sorted(
        present & {"gpt_brain_get_review_bundles", "gpt_brain_record_bundle_review", "gpt_brain_get_next_sources"}
    )
    return {
        "surface_version": OAUTH_PATROL_SURFACE_VERSION,
        "tool_count": len(present),
        "discovery_is_exposed": discovery_is_exposed(names),
        "group_status": group_status,
        "all_groups_pass": all(group_status.values()),
        "forbidden_present": forbidden_present,
        "legacy_drain_present": legacy_drain_present,
        "formal_state_bypass": FORMAL_STATE_BYPASS,
    }


def exposure_contract(names: Sequence[str]) -> dict[str, Any]:
    present = set(names)
    return {
        "authoritative_tool": AUTHORITATIVE_TOOL,
        "mcp_tool_exposed": AUTHORITATIVE_TOOL in present,
        "discovery_list_index": discovery_tool_index(names),
        "discovery_is_exposed": discovery_is_exposed(names),
        "required_existing_present": {
            name: name in present for name in REQUIRED_EXISTING_TOOLS if name != GATEWAY_SOURCE_INCREMENTAL_READ_TOOL
        },
        "capture_read_present": CAPTURE_READ_TOOL in present,
        "capture_decide_present": CAPTURE_DECIDE_TOOL in present,
        "workspace_status_present": GET_WORKSPACE_STATUS in present,
        "get_today_present": GET_TODAY in present,
        "server_side_gpt_trigger": SERVER_SIDE_GPT_TRIGGER,
        "semantic_processing_on_server": SEMANTIC_PROCESSING_ON_SERVER,
        "gpt_patrol_trigger_owner": GPT_PATROL_TRIGGER_OWNER,
        "formal_state_bypass": FORMAL_STATE_BYPASS,
    }


__all__ = [
    "AUTHORITATIVE_TOOL",
    "CAPTURE_DECIDE_TOOL",
    "CAPTURE_READ_TOOL",
    "CLAIM_TOOL",
    "FORBIDDEN_ROUTINE_PATROL_TOOLS",
    "GET_TODAY",
    "GET_WORKSPACE_STATUS",
    "MAX_DISCOVERY_LIST_INDEX",
    "NEUTRAL_CLAIM_TOOL",
    "NEUTRAL_LIST_TOOL",
    "NEUTRAL_PATROL_TOOLS",
    "NEUTRAL_PREPARE_TOOL",
    "NEUTRAL_SUBMIT_TOOL",
    "OAUTH_PATROL_SURFACE_VERSION",
    "PHANTOM_ALLOWLIST_ENTRIES",
    "PINNED_MCP_TOOLS",
    "REQUIRED_DISCOVERY_TOOLS",
    "REQUIRED_EXISTING_TOOLS",
    "ROUTINE_OAUTH_PATROL_TOOLS",
    "SOURCE_INCREMENTAL_PREPARE_TOOL",
    "SOURCE_INCREMENTAL_READ_TOOL",
    "SOURCE_INCREMENTAL_CLAIM_TOOL",
    "SOURCE_INCREMENTAL_SUBMIT_TOOL",
    "discovery_is_exposed",
    "discovery_tool_index",
    "exposure_contract",
    "filter_routine_patrol_tools",
    "mcp_tool_names",
    "reorder_mcp_tools",
    "routine_surface_contract",
]
