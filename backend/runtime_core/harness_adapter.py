"""PAW Harness Adapter - Bridges External Agent Frameworks with PAW Governance.

Enforces the absolute boundary:
- Harness cannot bypass PAW Governance.
- LLM / Agent cannot directly write to SQLite database.
- Agent intents are translated into Candidate proposals for Human Review.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from backend.governance.service import governance
from backend.provider.adapter import get_active_provider
from backend.workspace_api.repository import list_knowledge, list_projects

logger = logging.getLogger("paw.harness")


@dataclass
class AgentProposalEnvelope:
    intent: str
    target_type: str
    operation: str
    payload: Dict[str, Any]
    justification: str
    evidence_refs: List[str]
    confidence: str = "HIGH"


class HarnessAdapter:
    def __init__(self) -> None:
        self.provider = get_active_provider()

    def process_agent_intent(self, intent: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Process an agent intent, ensuring strict Candidate encapsulation."""
        ctx = context or {}
        evidence_refs = ctx.get("evidence_refs", [])
        
        # Guard: Reject any direct write calls that bypass governance
        if ctx.get("direct_write_requested"):
            logger.warning("Rejected illegal direct_write_requested from agent harness.")

        # Prepare proposal
        projects = list_projects()
        knowledge = list_knowledge(limit=10)

        # Build candidate via governance
        cid = governance.create_candidate(
            target_type=ctx.get("target_type", "knowledge"),
            operation=ctx.get("operation", "create"),
            payload=ctx.get("payload", {"title": intent[:50], "content": intent}),
            raw_excerpt=ctx.get("raw_excerpt", intent),
            source_refs=evidence_refs,
            confidence=ctx.get("confidence", "HIGH"),
            analysis_model="HarnessAgent",
            review_brief={
                "intent": intent,
                "harness_source": ctx.get("harness_name", "LocalAgent"),
                "governance_guarantee": "formal_state_bypass = 0",
            },
            evidence_locators=ctx.get("locators", []),
        )

        return {
            "status": "CANDIDATE_SUBMITTED",
            "candidate_id": cid,
            "message": "提议已安全进入 PAW 审查中心，等待用户确认后由 Committer 写入正式业务库。",
            "direct_write": False,
        }


harness_adapter = HarnessAdapter()
