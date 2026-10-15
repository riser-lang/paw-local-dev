"""Evidence references and a small traceability graph for analysis artifacts."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class EvidenceRef:
    """Compatibility value object for the existing ``source + locator`` contract."""

    source_version_id: str
    evidence_locator: str
    artifact_reference: Any = None
    excerpt: str | None = None
    ref_type: str = "semantic"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.source_version_id).strip():
            raise ValueError("source_version_id must not be empty")
        if not str(self.evidence_locator).strip():
            raise ValueError("evidence_locator must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ref_value(ref: Any, name: str, default: Any = None) -> Any:
    if isinstance(ref, Mapping):
        return ref.get(name, default)
    return getattr(ref, name, default)


def _ref_key(ref: Any) -> tuple[str, str]:
    source = _ref_value(ref, "source_version_id")
    locator = _ref_value(ref, "evidence_locator")
    if not str(source).strip() or not str(locator).strip():
        raise ValueError("evidence reference requires source_version_id and evidence_locator")
    return str(source), str(locator)


@dataclass(frozen=True)
class EvidenceNode:
    node_id: str
    source_version_id: str
    evidence_locator: str
    node_type: str = "EVIDENCE"
    label: str | None = None
    artifact_reference: Any = None
    excerpt: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_ref(
        cls, ref: EvidenceRef | Mapping[str, Any] | Any, *, label: str | None = None
    ) -> "EvidenceNode":
        source, locator = _ref_key(ref)
        return cls(
            node_id=f"evidence-{uuid.uuid5(uuid.NAMESPACE_URL, f'{source}:{locator}').hex}",
            source_version_id=source,
            evidence_locator=locator,
            label=label or _ref_value(ref, "label"),
            artifact_reference=_ref_value(ref, "artifact_reference"),
            excerpt=_ref_value(ref, "excerpt"),
            metadata=dict(_ref_value(ref, "metadata", {}) or {}),
        )

    @property
    def key(self) -> tuple[str, str]:
        return self.source_version_id, self.evidence_locator

    def to_ref(self) -> EvidenceRef:
        return EvidenceRef(
            source_version_id=self.source_version_id,
            evidence_locator=self.evidence_locator,
            artifact_reference=self.artifact_reference,
            excerpt=self.excerpt,
            metadata=self.metadata,
        )


@dataclass(frozen=True)
class EvidenceEdge:
    edge_id: str
    source_node_id: str
    target_node_id: str
    relation: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def from_node_id(self) -> str:
        return self.source_node_id

    @property
    def to_node_id(self) -> str:
        return self.target_node_id


class EvidenceGraph:
    """In-memory graph; it never mutates source artifacts or formal records."""

    def __init__(self) -> None:
        self._nodes: dict[str, EvidenceNode] = {}
        self._edges: dict[str, EvidenceEdge] = {}
        self._finding_nodes: dict[str, str] = {}

    @property
    def nodes(self) -> tuple[EvidenceNode, ...]:
        return tuple(self._nodes.values())

    @property
    def edges(self) -> tuple[EvidenceEdge, ...]:
        return tuple(self._edges.values())

    def add_evidence(self, ref: EvidenceRef | Mapping[str, Any] | Any) -> EvidenceNode:
        node = EvidenceNode.from_ref(ref)
        existing = self._nodes.get(node.node_id)
        if existing is not None:
            return existing
        self._nodes[node.node_id] = node
        return node

    add_ref = add_evidence

    def add_edge(
        self,
        source_node_id: str,
        target_node_id: str,
        relation: str = "SUPPORTS",
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> EvidenceEdge:
        if source_node_id not in self._nodes and source_node_id not in self._finding_nodes.values():
            raise KeyError(f"unknown source node: {source_node_id}")
        if target_node_id not in self._nodes and target_node_id not in self._finding_nodes.values():
            raise KeyError(f"unknown target node: {target_node_id}")
        edge_id = f"edge-{uuid.uuid4().hex}"
        edge = EvidenceEdge(edge_id, source_node_id, target_node_id, relation, dict(metadata or {}))
        self._edges[edge_id] = edge
        return edge

    def link_finding(
        self,
        finding_id: str,
        evidence_refs: list[EvidenceRef | Mapping[str, Any] | Any] | tuple[Any, ...],
    ) -> tuple[EvidenceEdge, ...]:
        if not str(finding_id).strip():
            raise ValueError("finding_id must not be empty")
        finding_node_id = self._finding_nodes.setdefault(finding_id, f"finding-{finding_id}")
        edges: list[EvidenceEdge] = []
        for ref in evidence_refs:
            node = self.add_evidence(ref)
            # A finding node is represented by its stable id; add_edge accepts that id.
            edges.append(self.add_edge(node.node_id, finding_node_id, "SUPPORTS"))
        return tuple(edges)

    def trace(self, finding_id: str) -> tuple[EvidenceNode, ...]:
        finding_node_id = self._finding_nodes.get(finding_id, f"finding-{finding_id}")
        evidence_ids = {
            edge.source_node_id
            for edge in self._edges.values()
            if edge.target_node_id == finding_node_id and edge.relation == "SUPPORTS"
        }
        return tuple(self._nodes[node_id] for node_id in evidence_ids if node_id in self._nodes)

    trace_for_finding = trace

    def is_traceable(self, finding_id: str) -> bool:
        return bool(self.trace(finding_id))

    def refs_for_finding(self, finding_id: str) -> tuple[EvidenceRef, ...]:
        return tuple(node.to_ref() for node in self.trace(finding_id))

    def snapshot(self) -> dict[str, Any]:
        return {
            "nodes": [asdict(node) for node in self.nodes],
            "edges": [asdict(edge) for edge in self.edges],
            "finding_nodes": dict(self._finding_nodes),
        }


__all__ = ["EvidenceEdge", "EvidenceGraph", "EvidenceNode", "EvidenceRef"]
