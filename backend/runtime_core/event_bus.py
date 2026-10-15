"""PAW Personal Work OS v2.1 - Event Bus.

Supports core events:
- FILE_CHANGED: Local file change detected
- SOURCE_READY: File settled and registered
- ANALYSIS_READY: High-fidelity evidence parsed
- REVIEW_REQUIRED: Candidates generated for user review
- COMMITTED: Approved changes committed to Formal State
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("paw.event_bus")


class EventType(str, Enum):
    FILE_CHANGED = "FILE_CHANGED"
    SOURCE_READY = "SOURCE_READY"
    ANALYSIS_READY = "ANALYSIS_READY"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    COMMITTED = "COMMITTED"
    CRUISE_STARTED = "CRUISE_STARTED"
    CRUISE_COMPLETED = "CRUISE_COMPLETED"
    EVOLUTION_HEALTH_CHECK = "EVOLUTION_HEALTH_CHECK"
    EVOLUTION_PROPOSAL_GENERATED = "EVOLUTION_PROPOSAL_GENERATED"


@dataclass
class PawEvent:
    event_type: EventType
    payload: Dict[str, Any]
    source: str = "paw_runtime"
    event_id: str = field(default_factory=lambda: f"evt_{int(time.time()*1000)}")
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["event_type"] = self.event_type.value
        return d


class EventBus:
    def __init__(self, max_history: int = 100) -> None:
        self._subscribers: Dict[EventType, List[Callable[[PawEvent], None]]] = {
            et: [] for et in EventType
        }
        self._history: List[PawEvent] = []
        self._lock = threading.Lock()
        self._max_history = max_history

    def subscribe(self, event_type: EventType, callback: Callable[[PawEvent], None]) -> None:
        with self._lock:
            if callback not in self._subscribers[event_type]:
                self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: EventType, callback: Callable[[PawEvent], None]) -> None:
        with self._lock:
            if callback in self._subscribers[event_type]:
                self._subscribers[event_type].remove(callback)

    def publish(self, event_type: EventType, payload: Dict[str, Any], source: str = "paw_runtime") -> PawEvent:
        event = PawEvent(event_type=event_type, payload=payload, source=source)
        with self._lock:
            self._history.insert(0, event)
            if len(self._history) > self._max_history:
                self._history.pop()
            callbacks = list(self._subscribers.get(event_type, []))

        for cb in callbacks:
            try:
                cb(event)
            except Exception as e:
                logger.error(f"Error executing callback for {event_type.value}: {e}", exc_info=True)

        return event

    def get_recent_events(self, limit: int = 30) -> List[Dict[str, Any]]:
        with self._lock:
            return [e.to_dict() for e in self._history[:limit]]

    def clear(self) -> None:
        with self._lock:
            self._history.clear()


# Global Event Bus Singleton
event_bus = EventBus()
