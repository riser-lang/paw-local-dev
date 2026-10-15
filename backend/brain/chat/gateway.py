"""Agnes Chat Gateway V1 — API Contract & Safety Rules.

This module is DESIGN-ONLY. It is not mounted on any production router.
It defines the future high-level API for Agnes chat interaction.

DEPLOYMENT_STATUS: READY_FOR_INTEGRATION (not deployed)
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


# ── Chat Modes ────────────────────────────────────────────────────────────

class ChatMode(str, Enum):
    chat = "chat"
    extract = "extract"
    summarize = "summarize"
    simulate = "simulate"
    project_review = "project_review"


# ── Request Model ──────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    """Request for POST /v1/brain/chat."""
    message: str = Field(min_length=1, max_length=10000)
    session_id: Optional[str] = Field(default=None, max_length=200)
    project_id: Optional[str] = Field(default=None, max_length=200)
    mode: ChatMode = ChatMode.chat


class ChatMessageRequest(BaseModel):
    """Request for POST /v1/brain/chat/{session_id}/messages."""
    message: str = Field(min_length=1, max_length=10000)


# ── Response Model ──────────────────────────────────────────────────────────

class ChatResponse(BaseModel):
    """Response from the chat gateway."""
    session_id: str
    message: str
    mode: ChatMode
    project_context_ref: Optional[dict[str, Any]] = None
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SessionInfo(BaseModel):
    """Session metadata for GET /v1/brain/chat/{session_id}."""
    session_id: str
    created_at: str
    message_count: int = 0
    last_message_at: Optional[str] = None
    project_id: Optional[str] = None


# ── API Route Design ────────────────────────────────────────────────────────

ROUTES = {
    "POST /v1/brain/chat": "Create a new chat session and send first message",
    "POST /v1/brain/chat/{session_id}/messages": "Send a message to an existing session",
    "GET /v1/brain/chat/{session_id}": "Get session info and recent messages",
}

# ── Context Pipeline ───────────────────────────────────────────────────────

PIPELINE = [
    "User Message",
    "→ Intent Classification",
    "→ Project Context Builder (read-only)",
    "→ Workspace Retrieval (read-only)",
    "→ AGNES_PERSONA_V1 injection",
    "→ Agnes (LLM inference)",
    "→ Response",
    "→ (if candidate needed) Analysis Engine → Candidate",
]

# ── Safety Rules ───────────────────────────────────────────────────────────

class ChatSafetyRules:
    """Enforced safety invariants for the chat gateway."""

    # chat mode: default no canonical write
    CHAT_NO_WRITE = True

    # extract mode: default no canonical write
    EXTRACT_NO_WRITE = True

    # simulate mode: NEVER write canonical
    SIMULATE_NEVER_WRITE = True

    # project_review mode: default no canonical write
    PROJECT_REVIEW_NO_WRITE = True

    # Only approved apply action can write canonical
    ONLY_APPROVED_APPLY_WRITES = True


# ── Session Model ──────────────────────────────────────────────────────────

class SessionModel(BaseModel):
    """Session design rules.

    Chat history is NOT a fact source.
    Each turn's key facts are re-fetched from Workspace Context.
    Session only preserves conversation continuity.
    """
    session_id: str
    messages: list[dict[str, Any]] = Field(default_factory=list)
    project_id: Optional[str] = None
    created_at: str = ""

    class Config:
        # Session memory must not be treated as confirmed knowledge
        authoritative_fact_source = False


# ── Custom GPT Relationship ───────────────────────────────────────────────

RELATIONSHIP = {
    "agnes": {
        "role": "local inference, information processing, retrieval, candidate generation",
        "cannot": "confirm knowledge, make final decisions, maintain separate knowledge base",
    },
    "custom_gpt": {
        "role": "deep reasoning, cross-project decisions, correction authority, knowledge compilation, confirmation workflow, approved canonical changes",
    },
    "shared": ["Workspace (single source of truth)"],
}
