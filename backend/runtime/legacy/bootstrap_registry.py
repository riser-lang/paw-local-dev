"""Read-only-source registry for Cursor semantic artifact consolidation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConsolidationStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    NOT_REQUIRED = "NOT_REQUIRED"


_COMPLETE_STATUSES = {
    ConsolidationStatus.COMPLETED.value,
    ConsolidationStatus.SKIPPED.value,
    ConsolidationStatus.NOT_REQUIRED.value,
}


@dataclass
class SourceConsolidationRecord:
    source_version_id: str
    artifact_reference: Any
    visual_status: str = ConsolidationStatus.PENDING.value
    gpt_analysis_status: str = ConsolidationStatus.PENDING.value
    self_review_status: str = ConsolidationStatus.PENDING.value
    opus_status: str = ConsolidationStatus.NOT_REQUIRED.value
    final_status: str = ConsolidationStatus.PENDING.value
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def artifact_ref(self) -> Any:
        """Compatibility alias; the reference is stored, never the artifact content."""
        return self.artifact_reference

    def set_status(self, stage: str, status: str | ConsolidationStatus) -> None:
        stage_map = {
            "visual": "visual_status",
            "visual_status": "visual_status",
            "gpt": "gpt_analysis_status",
            "gpt_analysis": "gpt_analysis_status",
            "gpt_analysis_status": "gpt_analysis_status",
            "self_review": "self_review_status",
            "self_review_status": "self_review_status",
            "opus": "opus_status",
            "opus_status": "opus_status",
            "final": "final_status",
            "final_status": "final_status",
        }
        attribute = stage_map.get(stage)
        if attribute is None:
            raise ValueError(f"unknown consolidation stage: {stage}")
        value = status.value if isinstance(status, ConsolidationStatus) else str(status)
        setattr(self, attribute, value)
        self.updated_at = _now()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceConsolidationRecord":
        return cls(
            source_version_id=str(value["source_version_id"]),
            artifact_reference=value.get("artifact_reference", value.get("artifact_ref")),
            visual_status=str(value.get("visual_status", ConsolidationStatus.PENDING.value)),
            gpt_analysis_status=str(
                value.get("gpt_analysis_status", ConsolidationStatus.PENDING.value)
            ),
            self_review_status=str(
                value.get("self_review_status", ConsolidationStatus.PENDING.value)
            ),
            opus_status=str(value.get("opus_status", ConsolidationStatus.NOT_REQUIRED.value)),
            final_status=str(value.get("final_status", ConsolidationStatus.PENDING.value)),
            created_at=str(value.get("created_at", _now())),
            updated_at=str(value.get("updated_at", _now())),
            metadata=dict(value.get("metadata", {})),
        )


class SourceConsolidationRegistry:
    """Maps immutable artifact references to mutable bootstrap analysis status."""

    def __init__(self) -> None:
        self._records: dict[str, SourceConsolidationRecord] = {}

    def register(
        self,
        source_version_id: str,
        artifact_reference: Any,
        **statuses: str | ConsolidationStatus,
    ) -> SourceConsolidationRecord:
        if not str(source_version_id).strip():
            raise ValueError("source_version_id must not be empty")
        if artifact_reference is None:
            raise ValueError("artifact_reference is required")
        record = self._records.get(source_version_id)
        if record is None:
            record = SourceConsolidationRecord(
                source_version_id=str(source_version_id), artifact_reference=artifact_reference
            )
            self._records[record.source_version_id] = record
        else:
            # Re-registration may refresh the pointer, but never dereferences or writes it.
            record.artifact_reference = artifact_reference
            record.updated_at = _now()
        for stage, status in statuses.items():
            record.set_status(stage, status)
        return record

    register_artifact = register

    def get(self, source_version_id: str) -> SourceConsolidationRecord | None:
        return self._records.get(source_version_id)

    def require(self, source_version_id: str) -> SourceConsolidationRecord:
        record = self.get(source_version_id)
        if record is None:
            raise KeyError(f"unknown source_version_id: {source_version_id}")
        return record

    def update_status(
        self, source_version_id: str, stage: str, status: str | ConsolidationStatus
    ) -> SourceConsolidationRecord:
        record = self.require(source_version_id)
        record.set_status(stage, status)
        return record

    def mark_visual(self, source_version_id: str, status: str | ConsolidationStatus) -> SourceConsolidationRecord:
        return self.update_status(source_version_id, "visual", status)

    def mark_gpt_analysis(self, source_version_id: str, status: str | ConsolidationStatus) -> SourceConsolidationRecord:
        return self.update_status(source_version_id, "gpt_analysis", status)

    def mark_self_review(self, source_version_id: str, status: str | ConsolidationStatus) -> SourceConsolidationRecord:
        return self.update_status(source_version_id, "self_review", status)

    def mark_opus(self, source_version_id: str, status: str | ConsolidationStatus) -> SourceConsolidationRecord:
        return self.update_status(source_version_id, "opus", status)

    def mark_final(self, source_version_id: str, status: str | ConsolidationStatus) -> SourceConsolidationRecord:
        return self.update_status(source_version_id, "final", status)

    def records(self) -> tuple[SourceConsolidationRecord, ...]:
        return tuple(self._records.values())

    def coverage(self) -> dict[str, int | float]:
        records = self.records()
        total = len(records)

        def count(stage: str) -> int:
            return sum(getattr(record, stage) in _COMPLETE_STATUSES for record in records)

        result: dict[str, int | float] = {"total": total}
        for label, stage in (
            ("visual", "visual_status"),
            ("gpt_analysis", "gpt_analysis_status"),
            ("self_review", "self_review_status"),
            ("opus", "opus_status"),
            ("final", "final_status"),
        ):
            completed = count(stage)
            result[f"{label}_completed"] = completed
            result[f"{label}_coverage"] = (completed / total) if total else 0.0
        return result

    coverage_report = coverage

    def snapshot(self) -> dict[str, Any]:
        return {"records": [record.to_dict() for record in self.records()], "coverage": self.coverage()}

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, Any]) -> "SourceConsolidationRegistry":
        registry = cls()
        for value in snapshot.get("records", []):
            record = SourceConsolidationRecord.from_dict(value)
            registry._records[record.source_version_id] = record
        return registry


__all__ = [
    "ConsolidationStatus",
    "SourceConsolidationRecord",
    "SourceConsolidationRegistry",
]
