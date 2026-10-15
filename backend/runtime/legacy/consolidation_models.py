"""Analysis-only intermediate models for Knowledge/Project/Data proposals."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

from evidence_graph import EvidenceRef


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _source_ids(
    source_version_ids: tuple[str, ...], evidence_refs: tuple[EvidenceRef | Mapping[str, Any], ...]
) -> tuple[str, ...]:
    values = set(str(item) for item in source_version_ids if str(item).strip())
    for ref in evidence_refs:
        source = ref.get("source_version_id") if isinstance(ref, Mapping) else getattr(ref, "source_version_id", None)
        if source:
            values.add(str(source))
    return tuple(sorted(values))


def stable_finding_fingerprint(
    *,
    domain: str = "",
    project_id: str = "",
    finding_type: str = "UNSPECIFIED",
    affected_entity: str = "",
    target: str = "",
    source_version_ids: tuple[str, ...] = (),
    proposed_operation: str = "",
) -> str:
    """Build the stable dedup key described by the Patrol review contract."""
    value = {
        "domain": domain,
        "project_id": project_id,
        "finding_type": finding_type,
        "affected_entity": affected_entity,
        "target": target,
        "source_version_ids": sorted(set(source_version_ids)),
        "proposed_operation": proposed_operation,
    }
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CanonicalFinding:
    finding_id: str = field(default_factory=lambda: _new_id("finding"))
    finding_type: str = "UNSPECIFIED"
    claim: str = ""
    domain: str = ""
    project_id: str = ""
    affected_entity: str = ""
    target: str = ""
    proposed_operation: str = ""
    source_version_ids: tuple[str, ...] = ()
    evidence_refs: tuple[EvidenceRef | Mapping[str, Any], ...] = ()
    confidence: float = 0.0
    fingerprint: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        refs = tuple(self.evidence_refs)
        source_ids = _source_ids(tuple(self.source_version_ids), refs)
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(self, "evidence_refs", refs)
        object.__setattr__(self, "source_version_ids", source_ids)
        if not self.fingerprint:
            object.__setattr__(
                self,
                "fingerprint",
                stable_finding_fingerprint(
                    domain=self.domain,
                    project_id=self.project_id,
                    finding_type=self.finding_type,
                    affected_entity=self.affected_entity,
                    target=self.target,
                    source_version_ids=source_ids,
                    proposed_operation=self.proposed_operation,
                ),
            )

    @property
    def has_evidence(self) -> bool:
        return bool(self.evidence_refs)

    def evidence_keys(self) -> tuple[tuple[str, str], ...]:
        result = []
        for ref in self.evidence_refs:
            source = ref.get("source_version_id") if isinstance(ref, Mapping) else ref.source_version_id
            locator = ref.get("evidence_locator") if isinstance(ref, Mapping) else ref.evidence_locator
            result.append((str(source), str(locator)))
        return tuple(result)


@dataclass(frozen=True)
class ProposalBase:
    proposal_id: str = field(default_factory=lambda: _new_id("proposal"))
    finding_id: str = ""
    title: str = ""
    rationale: str = ""
    evidence_refs: tuple[EvidenceRef | Mapping[str, Any], ...] = ()
    confidence: float = 0.0
    operation: str = "UPSERT"
    governance_route: str = "Candidate -> ReviewDecision -> ApprovedChange -> Committer"
    formal_write_allowed: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def has_evidence(self) -> bool:
        return bool(self.evidence_refs)

    def assert_analysis_only(self) -> None:
        if self.formal_write_allowed:
            raise PermissionError("Phase A proposals are analysis-only")


@dataclass(frozen=True)
class KnowledgeProposal(ProposalBase):
    subject: str = ""
    proposed_content: str = ""


@dataclass(frozen=True)
class ProjectProposal(ProposalBase):
    project_id: str = ""
    proposed_change: str = ""


@dataclass(frozen=True)
class MetricProposal(ProposalBase):
    metric_name: str = ""
    proposed_value: Any = None
    unit: str = ""
    period: str = ""


__all__ = [
    "CanonicalFinding",
    "KnowledgeProposal",
    "MetricProposal",
    "ProjectProposal",
    "ProposalBase",
    "stable_finding_fingerprint",
]
