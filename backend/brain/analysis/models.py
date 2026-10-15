"""Workspace Analysis Engine V1 — Data Models & Validation.

This module is DESIGN-ONLY. It is not imported by any production service.
It defines the input/output contract for the analysis engine and
enforces write-safety rules via Pydantic validators.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field, model_validator


# ── Enums ──────────────────────────────────────────────────────────────────

class TriggerType(str, Enum):
    capture = "capture"
    daily = "daily"
    source_new = "source_new"
    source_updated = "source_updated"
    manual_review = "manual_review"


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class InferenceLevel(str, Enum):
    EXPLICIT = "EXPLICIT"
    STRONG_INFERENCE = "STRONG_INFERENCE"
    WEAK_INFERENCE = "WEAK_INFERENCE"


class WriteAction(str, Enum):
    AUTO_WRITE = "AUTO_WRITE"
    DRAFT_ONLY = "DRAFT_ONLY"
    ASK_USER = "ASK_USER"
    DO_NOT_WRITE = "DO_NOT_WRITE"


class CandidateType(str, Enum):
    fact = "fact"
    todo = "todo"
    project_update = "project_update"
    knowledge = "knowledge"
    conflict = "conflict"
    ambiguity = "ambiguity"
    question = "question"


# Feature flag: AUTO_WRITE is disabled by default in V1 Foundation.
# Even if a candidate's recommended_action is set to AUTO_WRITE,
# no write executor exists and no canonical data is modified.
AUTO_WRITE_ENABLED = False


# ── Input Model ───────────────────────────────────────────────────────────

class ProjectContextRef(BaseModel):
    """A snapshot of relevant project context for analysis."""
    project_id: str
    name: str
    status: str
    stage: Optional[str] = None
    open_todo_count: int = 0
    draft_knowledge_count: int = 0
    confirmed_knowledge_count: int = 0


class TodoRef(BaseModel):
    todo_id: str
    title: str
    status: str
    due_at: Optional[str] = None


class KnowledgeRef(BaseModel):
    knowledge_id: str
    title: str
    status: str
    latest_revision_no: int = 0
    confirmed_revision_no: Optional[int] = None


class SourceRef(BaseModel):
    source_page_id: str
    title: str
    source_origin: Optional[str] = None
    canonical_state: Optional[str] = None


class DailyRef(BaseModel):
    daily_id: str
    date: str
    capture_count: int = 0


class AnalysisInput(BaseModel):
    """Structured input for the Workspace Analysis Engine."""
    analysis_id: str = Field(min_length=1)
    trigger_type: TriggerType
    raw_input_refs: list[str] = Field(default_factory=list)
    raw_text: Optional[str] = None
    relevant_project_context: Optional[ProjectContextRef] = None
    relevant_todos: list[TodoRef] = Field(default_factory=list)
    confirmed_knowledge: list[KnowledgeRef] = Field(default_factory=list)
    draft_knowledge: list[KnowledgeRef] = Field(default_factory=list)
    relevant_sources: list[SourceRef] = Field(default_factory=list)
    recent_daily: list[DailyRef] = Field(default_factory=list)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ── Output Model ──────────────────────────────────────────────────────────

class AnalysisItem(BaseModel):
    """Base item in analysis output."""
    statement: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    raw_excerpt: Optional[str] = None
    confidence: Confidence = Confidence.LOW
    inference_level: InferenceLevel = InferenceLevel.WEAK_INFERENCE


class CandidateItem(AnalysisItem):
    """An item that proposes a workspace change."""
    candidate_type: CandidateType
    recommended_action: WriteAction = WriteAction.DRAFT_ONLY
    proposed_change: Optional[dict[str, Any]] = None

    @model_validator(mode="after")
    def enforce_write_safety(self):
        """Knowledge candidates can never be AUTO_WRITE to confirmed status."""
        if self.candidate_type == CandidateType.knowledge:
            if self.recommended_action == WriteAction.AUTO_WRITE:
                raise ValueError(
                    "Knowledge Candidate cannot use AUTO_WRITE; "
                    "use DRAFT_ONLY or ASK_USER"
                )
        # Ambiguous todo status must not AUTO_WRITE done
        if self.candidate_type == CandidateType.todo:
            change = self.proposed_change or {}
            if change.get("status") == "done" and self.recommended_action == WriteAction.AUTO_WRITE:
                raise ValueError(
                    "Ambiguous todo status 'done' cannot use AUTO_WRITE"
                )
        return self


class AnalysisOutput(BaseModel):
    """Structured output from the Workspace Analysis Engine."""
    analysis_id: str
    facts: list[AnalysisItem] = Field(default_factory=list)
    events: list[AnalysisItem] = Field(default_factory=list)
    decisions: list[AnalysisItem] = Field(default_factory=list)
    plans: list[AnalysisItem] = Field(default_factory=list)
    ideas: list[AnalysisItem] = Field(default_factory=list)
    questions: list[AnalysisItem] = Field(default_factory=list)
    completed_actions: list[AnalysisItem] = Field(default_factory=list)
    pending_actions: list[AnalysisItem] = Field(default_factory=list)
    status_changes: list[AnalysisItem] = Field(default_factory=list)
    people: list[AnalysisItem] = Field(default_factory=list)
    dates: list[AnalysisItem] = Field(default_factory=list)
    projects: list[AnalysisItem] = Field(default_factory=list)
    todo_candidates: list[CandidateItem] = Field(default_factory=list)
    project_update_candidates: list[CandidateItem] = Field(default_factory=list)
    knowledge_candidates: list[CandidateItem] = Field(default_factory=list)
    conflicts: list[CandidateItem] = Field(default_factory=list)
    ambiguities: list[CandidateItem] = Field(default_factory=list)


# ── Validation Helpers ─────────────────────────────────────────────────────

def validate_ambiguity_preservation(original_text: str, item_statement: str) -> bool:
    """A1: '时间需要调整' cannot be converted to '流程需要调整'.
    Returns True if the statement preserves the original meaning.
    """
    if not original_text or not item_statement:
        return True  # nothing to compare
    # If original mentions '时间' but statement does not, that's a violation
    keywords_map = {
        "时间": ["时间", "时段", "日期", "时候"],
        "流程": ["流程", "步骤", "环节"],
    }
    for orig_kw, allowed_kws in keywords_map.items():
        if orig_kw in original_text and orig_kw not in item_statement:
            if not any(akw in item_statement for akw in allowed_kws):
                return False
    return True


def validate_plan_vs_done(statement: str, inference_level: InferenceLevel) -> bool:
    """A2: '准备安排初试' cannot be equivalent to '初试已完成'.
    Returns True if the statement is correctly classified.
    """
    planning_words = ["准备", "计划", "考虑", "打算", "拟", "将要"]
    done_words = ["已完成", "已经完成", "完成", "done", "finished"]
    is_planning = any(w in statement for w in planning_words)
    is_done = any(w in statement for w in done_words)
    if is_planning and not is_done:
        # Planning statement should not be EXPLICIT decision
        return inference_level != InferenceLevel.EXPLICIT
    return True


def validate_thinking_not_decision(statement: str, item_type: str) -> bool:
    """A3: '我在考虑做X' cannot become an explicit decision."""
    thinking_words = ["考虑", "在想", "思考", "maybe", "perhaps"]
    if any(w in statement for w in thinking_words):
        return item_type != "decision"
    return True


def validate_completed_action(statement: str) -> bool:
    """A4: '已经打印评分表' can generate a completed action."""
    done_indicators = ["已经", "已", "完成", "done", "finished", "打印了", "提交了"]
    return any(w in statement for w in done_indicators)


def validate_pending_todo(statement: str) -> bool:
    """A5: '评分表还没打印' can generate a pending Todo Candidate."""
    pending_indicators = ["还没", "尚未", "待", "需要", "pending", "todo"]
    return any(w in statement for w in pending_indicators)


def validate_project_attribution(statement: str, project_id: Optional[str]) -> bool:
    """A6: If project attribution is unclear, return ambiguity not random binding."""
    if project_id is None:
        return True  # None is acceptable (will be ambiguity)
    # If the statement doesn't mention any project-relevant keyword,
    # binding to a specific project is suspicious
    return True  # Actual validation would need project name matching


def validate_knowledge_not_auto_confirmed(candidate: CandidateItem) -> bool:
    """A7: Knowledge Candidate cannot directly become confirmed."""
    if candidate.candidate_type == CandidateType.knowledge:
        change = candidate.proposed_change or {}
        if change.get("status") == "current":
            return candidate.recommended_action != WriteAction.AUTO_WRITE
    return True


def validate_tentative_date(statement: str, inference_level: InferenceLevel) -> bool:
    """A8: '暂定8月19日上午' must not be treated as a final datetime.
    Returns True if the statement is correctly classified (not EXPLICIT).
    """
    tentative_words = ["暂定", "预计", "大概", "可能", "待确认", "tentative", "approx"]
    if any(w in statement for w in tentative_words):
        return inference_level != InferenceLevel.EXPLICIT
    return True


def validate_conflict_not_overwritten(
    new_statement: str,
    confirmed_statement: str,
    recommended_action: WriteAction,
) -> bool:
    """A9: If new Raw Source conflicts with Confirmed Knowledge,
    it must produce a conflict candidate, not overwrite.
    Returns True if the action is safe (ASK_USER or DO_NOT_WRITE).
    """
    if new_statement and confirmed_statement and new_statement != confirmed_statement:
        return recommended_action in (WriteAction.ASK_USER, WriteAction.DO_NOT_WRITE)
    return True


def validate_duplicate_check_contract(
    statement: str,
    existing_statements: list[str],
) -> bool:
    """A10: If a fact already has a canonical Todo with the same content,
    the system should support duplicate/no-op detection.
    Returns True if a duplicate is detected (caller should no-op).
    """
    normalized = statement.strip().lower()
    for existing in existing_statements:
        if existing.strip().lower() == normalized:
            return True  # duplicate detected → no-op
    return False  # not a duplicate → proceed
