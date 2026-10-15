"""PAW Agent Planner - Evaluates events, plans actions, and dispatches candidates.

Enforces:
- Model NEVER writes directly to Formal State.
- Event -> Planner Judgment -> Model Inference -> Candidate -> Review.
- Conflict analysis is integrated before candidate generation.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from backend.governance.service import governance
from backend.provider.adapter import get_active_provider
from backend.runtime_core.conflict_analyzer import conflict_analyzer
from backend.runtime_core.event_bus import EventType, event_bus
from backend.workspace_api.repository import list_knowledge, list_projects

logger = logging.getLogger("paw.planner")


class AgentPlanner:
    def __init__(self) -> None:
        pass

    def plan_document_impact(
        self,
        evidence: Dict[str, Any],
        source_file_path: str,
        file_hash: str,
    ) -> List[str]:
        """Core planning execution when ANALYSIS_READY event occurs."""
        display_name = evidence.get("display_name") or "untitled"
        text_blocks = evidence.get("text_blocks", [])
        combined_text = " ".join(b.get("text", "") for b in text_blocks[:15])

        # 1. Fetch current formal state
        projects = list_projects()
        knowledge_items = list_knowledge(limit=100)

        # 2. Check project alignment
        aligned_projects = conflict_analyzer.analyze_project_alignment(combined_text, projects)

        # 3. Model Inference via Provider Adapter
        provider = get_active_provider()
        raw_proposals = provider.propose(evidence, projects, knowledge_items)

        created_candidate_ids = []

        # 4. Evaluate each proposal with Conflict Analyzer
        for prop in raw_proposals:
            target_type = prop.get("target_type")
            operation = prop.get("operation")
            payload = prop.get("payload", {})
            raw_excerpt = prop.get("raw_excerpt", "")
            locators = prop.get("evidence_locators", [])
            review_brief = prop.get("review_brief", {})

            # If project aligned, automatically associate project_id to todo or knowledge
            if aligned_projects and not payload.get("project_id"):
                payload["project_id"] = aligned_projects[0]["project_id"]
                review_brief["aligned_project"] = aligned_projects[0]["project_name"]

            # Conflict analysis for knowledge proposals
            if target_type == "knowledge":
                k_title = payload.get("title", "")
                k_content = payload.get("content", "")
                conflict_res = conflict_analyzer.analyze_knowledge_conflict(k_title, k_content, knowledge_items)
                if conflict_res and conflict_res.get("is_conflict"):
                    review_brief["conflict_detected"] = True
                    review_brief["conflict_info"] = conflict_res
                    review_brief["recommended_action"] = conflict_res.get("recommendation")
                    # If strong topic match, propose update instead of create
                    if conflict_res.get("nature") == "UPDATE_SUPERSEDE":
                        operation = "update"
                        payload["id"] = conflict_res.get("target_id")

            # 5. Insert Candidate into Governance
            try:
                cid = governance.create_candidate(
                    target_type=target_type,
                    operation=operation,
                    payload=payload,
                    raw_excerpt=raw_excerpt,
                    source_refs=[file_hash],
                    confidence=prop.get("confidence", "HIGH"),
                    analysis_model="AgentPlanner+" + type(provider).__name__,
                    review_brief=review_brief,
                    evidence_locators=locators,
                )
                created_candidate_ids.append(cid)
            except Exception as exc:
                logger.error(f"AgentPlanner failed to create candidate: {exc}", exc_info=True)

        # 6. Publish REVIEW_REQUIRED event
        if created_candidate_ids:
            event_bus.publish(
                EventType.REVIEW_REQUIRED,
                payload={
                    "file": display_name,
                    "file_hash": file_hash,
                    "candidate_ids": created_candidate_ids,
                    "candidates_count": len(created_candidate_ids),
                },
                source="agent_planner",
            )
            logger.info(f"AgentPlanner created {len(created_candidate_ids)} candidates for {display_name}")

        return created_candidate_ids


agent_planner = AgentPlanner()
