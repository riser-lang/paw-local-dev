"""Candidate Store V1 — Data Model, Repository Interface, Migration Draft.

This module is DESIGN-ONLY. It is not imported by any production service.
The migration SQL is prepared but NOT applied to the production database.

MIGRATION_STATUS = PREPARED_NOT_APPLIED
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field, model_validator


class CandidateStatus(str, Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
    applied = "applied"


# Re-use enums from analysis models for consistency
from brain.analysis.models import (
    CandidateType,
    Confidence,
    InferenceLevel,
    WriteAction,
)


class Candidate(BaseModel):
    """A single AI-generated candidate for workspace modification.

    Every candidate must be fully traceable:
    - Which raw capture/source it came from
    - What the original text was
    - Who analyzed it (which model/agent)
    - When it was analyzed
    - Confidence level
    - Whether it was accepted/rejected by user
    - Whether it was applied to canonical
    """
    id: str = Field(min_length=1)
    analysis_run_id: str = Field(min_length=1)
    candidate_type: CandidateType
    source_refs: list[str] = Field(default_factory=list)
    raw_excerpt: Optional[str] = None
    proposed_change: Optional[dict[str, Any]] = None
    confidence: Confidence = Confidence.LOW
    inference_level: InferenceLevel = InferenceLevel.WEAK_INFERENCE
    recommended_action: WriteAction = WriteAction.DRAFT_ONLY

    @model_validator(mode="after")
    def enforce_default_write_policy(self):
        """V1 default write policy by candidate type.

        conflict/ambiguity/question → ASK_USER
        fact/todo/project_update/knowledge → DRAFT_ONLY
        AUTO_WRITE is never the default.
        """
        ask_user_types = {
            CandidateType.conflict,
            CandidateType.ambiguity,
            CandidateType.question,
        }
        if self.candidate_type in ask_user_types:
            if self.recommended_action == WriteAction.DRAFT_ONLY:
                self.recommended_action = WriteAction.ASK_USER
        return self
    analysis_model: str = Field(default="unknown", min_length=1)
    status: CandidateStatus = CandidateStatus.pending
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    reviewed_at: Optional[str] = None
    reviewed_by: Optional[str] = None
    applied_entity_type: Optional[str] = None
    applied_entity_id: Optional[str] = None


class CandidateStore:
    """Repository interface for Candidate Store.

    This is a contract/interface only. The actual implementation will
    use a separate SQLite database or table when deployed.
    """

    def create(self, candidate: Candidate) -> Candidate:
        raise NotImplementedError

    def get(self, candidate_id: str) -> Optional[Candidate]:
        raise NotImplementedError

    def list_by_analysis(self, analysis_run_id: str) -> list[Candidate]:
        raise NotImplementedError

    def list_pending(self, limit: int = 100) -> list[Candidate]:
        raise NotImplementedError

    def update_status(
        self,
        candidate_id: str,
        status: CandidateStatus,
        reviewed_by: str,
    ) -> Optional[Candidate]:
        raise NotImplementedError

    def list_applied(self) -> list[Candidate]:
        raise NotImplementedError


# ── Migration SQL Draft (DO NOT APPLY) ─────────────────────────────────────

MIGRATION_SQL_DRAFT = """
-- Candidate Store V1 Migration
-- MIGRATION_STATUS: PREPARED_NOT_APPLIED
-- Target: workspace.db (when approved in a future round)
-- Schema version: 4 → 5 (future)

CREATE TABLE IF NOT EXISTS analysis_run (
    id TEXT PRIMARY KEY,
    trigger_type TEXT NOT NULL,
    raw_text TEXT,
    raw_input_refs TEXT,  -- JSON array
    analysis_model TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate (
    id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL,
    candidate_type TEXT NOT NULL CHECK (candidate_type IN (
        'fact', 'todo', 'project_update', 'knowledge', 'conflict', 'ambiguity', 'question'
    )),
    source_refs TEXT,  -- JSON array of capture_id / source_page_id
    raw_excerpt TEXT,
    proposed_change TEXT,  -- JSON object
    confidence TEXT NOT NULL CHECK (confidence IN ('HIGH', 'MEDIUM', 'LOW')),
    inference_level TEXT NOT NULL CHECK (inference_level IN (
        'EXPLICIT', 'STRONG_INFERENCE', 'WEAK_INFERENCE'
    )),
    recommended_action TEXT NOT NULL CHECK (recommended_action IN (
        'AUTO_WRITE', 'DRAFT_ONLY', 'ASK_USER', 'DO_NOT_WRITE'
    )),
    analysis_model TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending', 'accepted', 'rejected', 'applied'
    )),
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    reviewed_by TEXT,
    applied_entity_type TEXT,
    applied_entity_id TEXT,
    FOREIGN KEY (analysis_run_id) REFERENCES analysis_run(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_candidate_analysis_run
    ON candidate(analysis_run_id);
CREATE INDEX IF NOT EXISTS idx_candidate_status
    ON candidate(status);
CREATE INDEX IF NOT EXISTS idx_candidate_type
    ON candidate(candidate_type);
"""

# Explicitly mark as not applied
MIGRATION_STATUS = "PREPARED_NOT_APPLIED"
