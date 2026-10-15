"""Independent, analysis-only runtime for the PAW Brain bootstrap phase.

This module deliberately does not import Patrol, Governance, Cursor, or any
formal-state adapter.  A :class:`BootstrapRun` owns only its local lifecycle,
checkpoint, and observability data.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BootstrapStatus(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    WAITING_INPUT = "WAITING_INPUT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


_ALLOWED_TRANSITIONS: dict[BootstrapStatus, set[BootstrapStatus]] = {
    BootstrapStatus.CREATED: {BootstrapStatus.RUNNING, BootstrapStatus.FAILED},
    BootstrapStatus.RUNNING: {
        BootstrapStatus.PAUSED,
        BootstrapStatus.WAITING_INPUT,
        BootstrapStatus.COMPLETED,
        BootstrapStatus.FAILED,
    },
    BootstrapStatus.PAUSED: {
        BootstrapStatus.RUNNING,
        BootstrapStatus.WAITING_INPUT,
        BootstrapStatus.FAILED,
    },
    BootstrapStatus.WAITING_INPUT: {
        BootstrapStatus.RUNNING,
        BootstrapStatus.PAUSED,
        BootstrapStatus.FAILED,
    },
    BootstrapStatus.COMPLETED: set(),
    BootstrapStatus.FAILED: set(),
}


@dataclass
class BootstrapObservability:
    """Small machine-readable event and metric ledger persisted in checkpoints."""

    metrics: dict[str, int | float] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    def increment(self, name: str, amount: int | float = 1) -> None:
        self.metrics[name] = self.metrics.get(name, 0) + amount

    def observe(self, name: str, value: int | float) -> None:
        self.metrics[name] = value

    def event(self, event_type: str, **details: Any) -> None:
        self.events.append({"at": _now(), "type": event_type, **details})
        self.increment("events_recorded")

    def to_dict(self) -> dict[str, Any]:
        return {"metrics": dict(self.metrics), "events": list(self.events)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "BootstrapObservability":
        value = value or {}
        return cls(
            metrics=dict(value.get("metrics", {})),
            events=[dict(item) for item in value.get("events", [])],
        )


@dataclass
class BootstrapCheckpoint:
    """Serializable resume point for one independent BootstrapRun."""

    run_id: str
    status: str
    current_step: str | None
    completed_steps: list[str]
    pending_input: str | None
    metrics: dict[str, int | float]
    events: list[dict[str, Any]]
    source_version_id: str | None
    error: str | None
    created_at: str
    updated_at: str
    resume_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BootstrapCheckpoint":
        required = {"run_id", "status", "completed_steps", "metrics", "events"}
        missing = sorted(required.difference(value))
        if missing:
            raise ValueError(f"invalid bootstrap checkpoint; missing: {', '.join(missing)}")
        return cls(
            run_id=str(value["run_id"]),
            status=str(value["status"]),
            current_step=value.get("current_step"),
            completed_steps=[str(item) for item in value.get("completed_steps", [])],
            pending_input=value.get("pending_input"),
            metrics=dict(value.get("metrics", {})),
            events=[dict(item) for item in value.get("events", [])],
            source_version_id=value.get("source_version_id"),
            error=value.get("error"),
            created_at=str(value.get("created_at", _now())),
            updated_at=str(value.get("updated_at", _now())),
            resume_count=int(value.get("resume_count", 0)),
        )


class BootstrapCheckpointStore:
    """JSON checkpoint store scoped to the path supplied by the caller."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)

    def save(self, checkpoint: BootstrapCheckpoint) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(checkpoint.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return self.path

    def load(self) -> BootstrapCheckpoint:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"bootstrap checkpoint not found: {self.path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid bootstrap checkpoint JSON: {self.path}") from exc
        return BootstrapCheckpoint.from_dict(value)


@dataclass
class BootstrapRun:
    """Lifecycle container for a single analysis-only bootstrap run."""

    run_id: str = field(default_factory=lambda: f"bootstrap-{uuid.uuid4().hex}")
    source_version_id: str | None = None
    status: BootstrapStatus = BootstrapStatus.CREATED
    current_step: str | None = None
    completed_steps: list[str] = field(default_factory=list)
    pending_input: str | None = None
    observability: BootstrapObservability = field(default_factory=BootstrapObservability)
    checkpoint_path: Path | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    error: str | None = None
    resume_count: int = 0
    last_checkpoint: BootstrapCheckpoint | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.status = BootstrapStatus(self.status)
        if self.checkpoint_path is not None:
            self.checkpoint_path = Path(self.checkpoint_path)

    @classmethod
    def create(
        cls,
        *,
        source_version_id: str | None = None,
        checkpoint_path: str | os.PathLike[str] | None = None,
        run_id: str | None = None,
    ) -> "BootstrapRun":
        return cls(
            run_id=run_id or f"bootstrap-{uuid.uuid4().hex}",
            source_version_id=source_version_id,
            checkpoint_path=Path(checkpoint_path) if checkpoint_path else None,
        )

    def _transition(self, target: BootstrapStatus, **details: Any) -> None:
        target = BootstrapStatus(target)
        if target not in _ALLOWED_TRANSITIONS[self.status]:
            raise ValueError(f"invalid bootstrap transition: {self.status.value} -> {target.value}")
        previous = self.status
        self.status = target
        self.updated_at = _now()
        self.observability.event(
            "status_transition", from_status=previous.value, to_status=target.value, **details
        )

    def start(self) -> "BootstrapRun":
        self._transition(BootstrapStatus.RUNNING)
        return self

    def pause(self, reason: str | None = None) -> "BootstrapRun":
        self._transition(BootstrapStatus.PAUSED, reason=reason)
        return self

    def wait_for_input(self, prompt: str) -> "BootstrapRun":
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        self.pending_input = prompt
        self._transition(BootstrapStatus.WAITING_INPUT, prompt=prompt)
        return self

    def resume_run(self) -> "BootstrapRun":
        if self.status not in {BootstrapStatus.PAUSED, BootstrapStatus.WAITING_INPUT}:
            raise ValueError(f"cannot resume bootstrap run from {self.status.value}")
        self.pending_input = None
        self.resume_count += 1
        self._transition(BootstrapStatus.RUNNING, resume_count=self.resume_count)
        return self

    def resume(self) -> "BootstrapRun":
        """Instance-friendly resume alias; disk loading is provided by ``resume_from``."""
        return self.resume_run()

    def complete(self) -> "BootstrapRun":
        self._transition(BootstrapStatus.COMPLETED)
        self.pending_input = None
        self.observability.increment("completed_runs")
        return self

    def fail(self, error: str) -> "BootstrapRun":
        if not error.strip():
            raise ValueError("error must not be empty")
        self.error = error
        if self.status not in {BootstrapStatus.COMPLETED, BootstrapStatus.FAILED}:
            self._transition(BootstrapStatus.FAILED, error=error)
        return self

    def set_step(self, step: str) -> "BootstrapRun":
        if not step.strip():
            raise ValueError("step must not be empty")
        self.current_step = step
        self.updated_at = _now()
        self.observability.event("step_started", step=step)
        return self

    def complete_step(self, step: str | None = None) -> "BootstrapRun":
        step = step or self.current_step
        if not step:
            raise ValueError("a current step or explicit step is required")
        if step not in self.completed_steps:
            self.completed_steps.append(step)
        self.current_step = None
        self.updated_at = _now()
        self.observability.increment("steps_completed")
        self.observability.event("step_completed", step=step)
        return self

    def record_metric(self, name: str, value: int | float) -> "BootstrapRun":
        self.observability.observe(name, value)
        self.updated_at = _now()
        return self

    def increment_metric(self, name: str, amount: int | float = 1) -> "BootstrapRun":
        self.observability.increment(name, amount)
        self.updated_at = _now()
        return self

    def record_event(self, event_type: str, **details: Any) -> "BootstrapRun":
        self.observability.event(event_type, **details)
        self.updated_at = _now()
        return self

    def to_checkpoint(self) -> BootstrapCheckpoint:
        return BootstrapCheckpoint(
            run_id=self.run_id,
            status=self.status.value,
            current_step=self.current_step,
            completed_steps=list(self.completed_steps),
            pending_input=self.pending_input,
            metrics=dict(self.observability.metrics),
            events=list(self.observability.events),
            source_version_id=self.source_version_id,
            error=self.error,
            created_at=self.created_at,
            updated_at=self.updated_at,
            resume_count=self.resume_count,
        )

    def checkpoint(
        self, path: str | os.PathLike[str] | None = None
    ) -> BootstrapCheckpoint:
        if path is not None:
            self.checkpoint_path = Path(path)
        checkpoint = self.to_checkpoint()
        self.last_checkpoint = checkpoint
        if self.checkpoint_path is not None:
            BootstrapCheckpointStore(self.checkpoint_path).save(checkpoint)
        return checkpoint

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint: BootstrapCheckpoint | Mapping[str, Any],
        *,
        checkpoint_path: str | os.PathLike[str] | None = None,
    ) -> "BootstrapRun":
        if not isinstance(checkpoint, BootstrapCheckpoint):
            checkpoint = BootstrapCheckpoint.from_dict(checkpoint)
        run = cls(
            run_id=checkpoint.run_id,
            source_version_id=checkpoint.source_version_id,
            status=BootstrapStatus(checkpoint.status),
            current_step=checkpoint.current_step,
            completed_steps=list(checkpoint.completed_steps),
            pending_input=checkpoint.pending_input,
            observability=BootstrapObservability(
                metrics=dict(checkpoint.metrics), events=list(checkpoint.events)
            ),
            checkpoint_path=Path(checkpoint_path) if checkpoint_path else None,
            created_at=checkpoint.created_at,
            updated_at=checkpoint.updated_at,
            error=checkpoint.error,
            resume_count=checkpoint.resume_count,
            last_checkpoint=checkpoint,
        )
        return run

    @classmethod
    def resume_from(cls, path: str | os.PathLike[str]) -> "BootstrapRun":
        store = BootstrapCheckpointStore(path)
        return cls.from_checkpoint(store.load(), checkpoint_path=path)

    @classmethod
    def load_checkpoint(cls, path: str | os.PathLike[str]) -> "BootstrapRun":
        return cls.resume_from(path)

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "source_version_id": self.source_version_id,
            "status": self.status.value,
            "current_step": self.current_step,
            "completed_steps": list(self.completed_steps),
            "pending_input": self.pending_input,
            "observability": self.observability.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "resume_count": self.resume_count,
        }


__all__ = [
    "BootstrapCheckpoint",
    "BootstrapCheckpointStore",
    "BootstrapObservability",
    "BootstrapRun",
    "BootstrapStatus",
]
