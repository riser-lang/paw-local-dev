"""PAW Agy Conversation Context V2 (spec §16) — conversation continuity.

Agy must remember what the conversation is about so that a follow-up like
"它现在什么状态" resolves to the project discussed one turn ago, instead of
restarting from scratch.

Stored per conversation (spec §16):
    active_project_id / active_project_name / active_knowledge_ids /
    active_source_version_ids / temporal_scope / current_topic /
    last_resolved_entities / last_user_intent

NEVER stored: chain-of-thought, hidden reasoning, model scratchpads.  This
module enforces that with an explicit forbidden-key check.

Pure module: no I/O, no writes, no LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

#: keys that must never appear in a persisted conversation context
FORBIDDEN_KEYS = (
    "chain_of_thought", "chainofthought", "cot", "reasoning", "reasoning_trace",
    "thoughts", "thinking", "scratchpad", "internal_monologue", "hidden_state",
    "raw_prompt", "system_prompt",
)

#: temporal scopes (spec §16 / §23)
T_CURRENT = "current"
T_HISTORICAL = "historical"
T_ALL = "all"
TEMPORAL_SCOPES = (T_CURRENT, T_HISTORICAL, T_ALL)

#: pronouns / deictics that refer back to the active project
_REFERENTIAL_RE = re.compile(
    r"(它|这个项目|该项目|此项目|那个项目|上述项目|前面提到的项目|这个|该项目|"
    r"他|她|这家|同一项目|it|this project|that project|the project)",
    re.IGNORECASE)

#: an explicit new project mention wins over the active one
_NEW_PROJECT_RE = re.compile(r"项目[「\"]?([^\s」\"，。,；;]{2,30})[」\"]?")


def _s(v: Any) -> str:
    return "" if v is None else str(v)


@dataclass
class ConversationContext:
    """Bounded, auditable conversation state (no chain-of-thought)."""

    conversation_id: str = ""
    turn_index: int = 0

    active_project_id: Optional[str] = None
    active_project_name: Optional[str] = None
    active_knowledge_ids: list[str] = field(default_factory=list)
    active_source_version_ids: list[str] = field(default_factory=list)
    temporal_scope: str = T_CURRENT
    current_topic: str = ""
    last_resolved_entities: list[str] = field(default_factory=list)
    last_user_intent: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        leaks = [k for k in d if k.lower() in FORBIDDEN_KEYS]
        if leaks:
            raise ValueError("forbidden context keys: %s" % leaks)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConversationContext":
        if not isinstance(data, dict):
            return cls()
        leaks = [k for k in data if str(k).lower() in FORBIDDEN_KEYS]
        if leaks:
            raise ValueError("refusing to load forbidden context keys: %s" % leaks)
        allowed = set(cls().__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in allowed})

    # -- reference resolution -------------------------------------------------
    def resolve_reference(self, message: str) -> dict[str, Any]:
        """Decide whether ``message`` refers back to the active project.

        Returns ``{refers_to_active, project_id, project_name, reason}``.
        """
        msg = _s(message)
        if not self.active_project_id:
            return {"refers_to_active": False, "project_id": None,
                    "project_name": None, "reason": "no_active_project"}
        if _REFERENTIAL_RE.search(msg):
            return {"refers_to_active": True,
                    "project_id": self.active_project_id,
                    "project_name": self.active_project_name,
                    "reason": "referential_pronoun"}
        # a bare follow-up that names no other project keeps the active one
        if self.active_project_name and self.active_project_name in msg:
            return {"refers_to_active": True,
                    "project_id": self.active_project_id,
                    "project_name": self.active_project_name,
                    "reason": "active_project_named"}
        m = _NEW_PROJECT_RE.search(msg)
        if m and self.active_project_name and m.group(1) != self.active_project_name:
            return {"refers_to_active": False, "project_id": None,
                    "project_name": m.group(1), "reason": "different_project_named"}
        return {"refers_to_active": False, "project_id": None,
                "project_name": None, "reason": "no_reference"}

    def wants_historical(self, message: str) -> bool:
        return bool(re.search(r"(历史|以前|之前|曾经|上一版|旧版|更早|superseded|"
                              r"previous|earlier|history|最早|v1|原方案|原定)", _s(message), re.IGNORECASE))

    def update(self, *, user_message: str, intent: str = "",
               project_id: Optional[str] = None,
               project_name: Optional[str] = None,
               knowledge_ids: Optional[list[str]] = None,
               source_version_ids: Optional[list[str]] = None,
               entities: Optional[list[str]] = None) -> "ConversationContext":
        """Advance the conversation by one turn (in place, returns self)."""
        self.turn_index += 1
        if project_id:
            self.active_project_id = project_id
        if project_name:
            self.active_project_name = project_name
        if knowledge_ids:
            merged = list(dict.fromkeys(list(self.active_knowledge_ids)
                                        + list(knowledge_ids)))
            self.active_knowledge_ids = merged[:50]
        if source_version_ids:
            merged = list(dict.fromkeys(list(self.active_source_version_ids)
                                        + list(source_version_ids)))
            self.active_source_version_ids = merged[:50]
        if entities:
            self.last_resolved_entities = list(
                dict.fromkeys(list(self.last_resolved_entities) + list(entities)))[:30]
        if intent:
            self.last_user_intent = intent
        self.current_topic = _s(user_message)[:200]
        self.temporal_scope = (T_HISTORICAL if self.wants_historical(user_message)
                               else T_CURRENT)
        return self

    def prompt_block(self) -> str:
        """Compact, auditable rendering injected into the model prompt."""
        lines = ["--- 会话上下文 ---"]
        if self.active_project_name or self.active_project_id:
            lines.append("当前讨论项目：%s（%s）"
                         % (_s(self.active_project_name) or "（未命名）",
                            _s(self.active_project_id) or "—"))
        if self.current_topic:
            lines.append("当前话题：%s" % self.current_topic)
        if self.last_resolved_entities:
            lines.append("已确定实体：%s" % "、".join(self.last_resolved_entities[:8]))
        lines.append("时间范围：%s" % self.temporal_scope)
        if self.last_user_intent:
            lines.append("上一轮意图：%s" % self.last_user_intent)
        return "\n".join(lines)


def new_context(conversation_id: str = "") -> ConversationContext:
    return ConversationContext(conversation_id=conversation_id)
