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
        from backend.pm.storage import ingest_evidence
        ingest_evidence(evidence, file_hash)
        from backend.pm.receipts import register as register_receipt
        register_receipt(evidence,file_hash)
        from backend.pm.versions import process_source
        process_source(file_hash)
        display_name = evidence.get("display_name") or "untitled"
        text_blocks = evidence.get("text_blocks", [])
        combined_text = " ".join(b.get("text", "") for b in text_blocks[:15])

        # A version that a newer version of the same document already replaced
        # never proposes again: its content contradicts the current version, and
        # the committer would refuse it anyway ("来源版本已被替换").  Proposals it
        # already produced are retired here so the review list only shows the
        # current version.
        from backend.pm import storage
        from backend.pm import versions as pm_versions
        with storage.connect() as conn:
            replaced = pm_versions.is_replaced(conn, file_hash)
        if replaced:
            # Retire only this file's own proposals: the newer versions are the
            # current ones and their proposals stay.
            pm_versions.withdraw_candidates_for([file_hash])
            logger.info(f"{display_name} 已被更新版本替代，本次不生成提议")
            return []

        # 1. Fetch current formal state
        projects = list_projects()
        knowledge_items = list_knowledge(limit=100)

        # 2. Check project alignment
        aligned_projects = conflict_analyzer.analyze_project_alignment(combined_text, projects)

        # 3. Model Inference via Provider Adapter
        provider = get_active_provider()
        from backend.pm import storage
        from backend.pm.updates import process_source
        from backend.provider import model_calls
        with storage.connect() as conn:
            conn.execute('INSERT INTO pm_source_location VALUES(?,?) ON CONFLICT(source_version_id) DO UPDATE SET path=excluded.path',(file_hash,source_file_path))
        # Attribute every scan model call to this file: section_update and propose
        # both run inside this context so each row carries the file name.
        file_ctx = model_calls.begin_file(display_name)
        try:
            process_source(file_hash,provider)
            raw_proposals = provider.propose(evidence, projects, knowledge_items)
        finally:
            model_calls.end_file(file_ctx)

        created_candidate_ids = []
        failed_proposals = 0

        # 4. Evaluate each proposal with Conflict Analyzer
        from backend.pm import display_text
        for prop in raw_proposals:
            target_type = prop.get("target_type")
            operation = prop.get("operation")
            payload = display_text.clean_payload(prop.get("payload", {}))
            raw_excerpt = prop.get("raw_excerpt", "")
            locators = prop.get("evidence_locators", [])
            review_brief = prop.get("review_brief", {})
            # Review brief text is shown next to the card as well.
            display_text.clean_fields(review_brief, ("summary", "recommended_action"))

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
                failed_proposals += 1
                logger.error(f"AgentPlanner failed to create candidate: {exc}", exc_info=True)

        if failed_proposals:
            raise RuntimeError(f'候选落库失败 {failed_proposals} 条；资料分析未完成，可重试')

        # The newer version may have been filed while this proposal batch was being
        # built (phase 2 analyses files in parallel).  Re-check at the end: a batch
        # that belongs to a replaced version must not stay in the review list,
        # where it can never be accepted.
        with storage.connect() as conn:
            replaced_now = pm_versions.is_replaced(conn, file_hash)
        if replaced_now and created_candidate_ids:
            pm_versions.withdraw_candidates_for([file_hash])
            logger.info(f"{display_name} 在分析期间被更新版本替代，撤回本次 {len(created_candidate_ids)} 条提议")
            return []

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
