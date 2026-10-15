"""Artifact discovery for the Cursor semantic bootstrap.

This module is deliberately read-only.  It discovers prior semantic-worker
artifacts without copying or rewriting them, and returns provenance that the
Cursor control plane can attach to its own second-layer artifacts.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import threading


WORKERS = ("agy", "qwen", "gemini31pro", "cursor_visual")
VISUAL_WORKERS = frozenset({"gemini31pro", "cursor_visual"})
CURSOR_VISUAL_WORKER = "cursor_visual"
CURSOR_VISUAL_WORKER_ALIASES = frozenset({"cursor_visual", "cursor"})
CURSOR_VISUAL_LINEAGE_KEYS = (
    "source_version_id",
    "source_id",
    "stable_object_id",
    "version_marker",
    "raw_sha256",
    "reader_result_path",
)
SEMANTIC_FIELDS = (
    "summary",
    "facts",
    "key_facts",
    "inferences",
    "project_signals",
    "project_domain_signals",
    "timeline",
    "metrics",
    "metrics_data_signals",
    "decisions_or_plans",
    "conflicts",
    "ambiguities",
    "risks",
    "related_projects",
    "document_role",
)
GROUNDING_KEYS = {
    "evidence_locators",
    "evidence_pointers",
    "source_evidence_pointers",
    "evidence_locations",
    "evidence",
}
REVIEW_STATUS = {"NEEDS_REVIEW", "LOW_CONFIDENCE", "INSUFFICIENT_EVIDENCE", "RETRYABLE"}


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _nonempty(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def _has_grounding(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() in GROUNDING_KEYS and _nonempty(child):
                return True
            if _has_grounding(child):
                return True
    elif isinstance(value, list):
        return any(_has_grounding(child) for child in value)
    return False


def _first_visual_input(payload: dict[str, Any]) -> dict[str, Any] | None:
    vis = payload.get("visual_input")
    if isinstance(vis, dict):
        return vis
    vis_list = payload.get("visual_inputs")
    if isinstance(vis_list, list):
        for item in vis_list:
            if isinstance(item, dict):
                return item
    return None


def _nonempty_visual_facts(payload: dict[str, Any]) -> bool:
    analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
    for container in (analysis, payload):
        for key in (
            "grounded_visual_facts",
            "visual_observations",
            "table_observations",
            "facts",
            "key_facts",
        ):
            if _nonempty(container.get(key)):
                return True
    return False


def cursor_visual_admissible(payload: dict[str, Any]) -> bool:
    """Fail-closed gate for Cursor visual artifacts.

    Incomplete JSON under semantic/cursor_visual/ must not receive
    visual_coverage.  This does not change agy/qwen/gemini31pro rules.
    """
    if not isinstance(payload, dict):
        return False
    if str(payload.get("status") or "").upper() != "SUCCESS":
        return False
    worker = str(payload.get("worker") or "").strip()
    if worker not in CURSOR_VISUAL_WORKER_ALIASES:
        return False
    if not str(payload.get("model") or "").strip():
        return False
    lineage = payload.get("authoritative_lineage")
    if not isinstance(lineage, dict):
        return False
    for key in CURSOR_VISUAL_LINEAGE_KEYS:
        if not str(lineage.get(key) or "").strip():
            return False
    vis = _first_visual_input(payload)
    if not isinstance(vis, dict):
        return False
    image_path = str(vis.get("image_path") or vis.get("path") or "").strip()
    image_sha = str(vis.get("image_sha256") or vis.get("sha256") or "").strip()
    locator = str(vis.get("locator") or vis.get("evidence_locator") or "").strip()
    if not image_path or not image_sha or not locator:
        return False
    evidence = payload.get("evidence_validation")
    # Visual supplements are analysis-only evidence.  Do not admit partial
    # validation (for example NEEDS_REVIEW with an invalid locator) into the
    # Brain's visual evidence set.
    if not isinstance(evidence, dict) or str(evidence.get("status") or "").upper() != "PASS":
        return False
    if payload.get("visual_coverage") is not True:
        return False
    if payload.get("semantic_coverage") is not False:
        return False
    if not _nonempty_visual_facts(payload):
        return False
    return True


def contains_grounded_semantic_fields(payload: dict[str, Any]) -> bool:
    """Return true only for explicit semantic fields grounded by evidence.

    A Gemini visual artifact containing only image/layout observations will
    therefore remain visual coverage.  The detector accepts the repository's
    shared schema whether its fields are under ``analysis`` or at top level.
    """
    candidates = []
    analysis = payload.get("analysis")
    if isinstance(analysis, dict):
        candidates.append(analysis)
    candidates.append(payload)
    for candidate in candidates:
        has_semantic = any(_nonempty(candidate.get(field)) for field in SEMANTIC_FIELDS)
        if has_semantic and _has_grounding(candidate):
            return True
    return False


def _source_version_id(path: Path, payload: dict[str, Any]) -> str:
    lineage = payload.get("authoritative_lineage")
    if isinstance(lineage, dict) and lineage.get("source_version_id"):
        return str(lineage["source_version_id"])
    for container_key in ("analysis", "source_metadata", "source_metadata_echo", "reader_metadata"):
        container = payload.get(container_key)
        if isinstance(container, dict) and container.get("source_version_id"):
            return str(container["source_version_id"])
    return path.stem


def _review_signals(payload: dict[str, Any]) -> tuple[str, ...]:
    signals: set[str] = set()
    analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
    evidence = payload.get("evidence_validation") if isinstance(payload.get("evidence_validation"), dict) else {}
    for container in (payload, analysis, evidence):
        for key in ("needs_review", "review_required", "insufficient_evidence", "retryable"):
            if container.get(key) is True:
                signals.add(key)
        for key in ("status", "review_status", "confidence"):
            value = str(container.get(key) or "").upper()
            if value in REVIEW_STATUS:
                signals.add(value.lower())
        if container.get("needs_visual_review") is True:
            signals.add("needs_visual_review")
    if str(evidence.get("status") or "").upper() in REVIEW_STATUS:
        signals.add(str(evidence["status"]).lower())
    review_text = json.dumps((payload, analysis, evidence), ensure_ascii=False).casefold()
    if "insufficient evidence" in review_text or "证据不足" in review_text:
        signals.add("insufficient_evidence")
    if "low confidence" in review_text or "低置信" in review_text:
        signals.add("low_confidence")
    return tuple(sorted(signals))


@dataclass(frozen=True)
class Artifact:
    worker: str
    path: str
    source_version_id: str
    status: str
    model: str
    semantic_coverage: bool
    visual_coverage: bool
    grounded_semantic_fields: bool
    review_signals: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "worker": self.worker,
            "path": self.path,
            "source_version_id": self.source_version_id,
            "status": self.status,
            "model": self.model,
            "semantic_coverage": self.semantic_coverage,
            "visual_coverage": self.visual_coverage,
            "grounded_semantic_fields": self.grounded_semantic_fields,
            "review_signals": list(self.review_signals),
        }


class ReusableArtifactIndex:
    """Read-only index of prior worker artifacts keyed by SourceVersion."""

    def __init__(self, semantic_root: Path):
        self.semantic_root = Path(semantic_root)
        self.artifacts: list[Artifact] = []
        self._by_source: dict[str, list[Artifact]] = {}
        # The GPT Brain keeps this index alive.  A read can therefore race a
        # newly atomically-published artifact; serialize refreshes/snapshots
        # so callers never observe a partially rebuilt per-source bucket.
        self._lock = threading.RLock()
        self._scan()
        self._scan_cloudshell_takeovers()

    def _scan(self) -> None:
        for worker in WORKERS:
            worker_root = self.semantic_root / worker
            if not worker_root.is_dir():
                continue
            for path in sorted(worker_root.rglob("*.json")):
                self._index_worker_artifact(worker, path)

    def _index_worker_artifact(self, worker: str, path: Path) -> None:
        """Add one canonical worker artifact when it passes that worker's
        existing admission contract.  Kept shared by initial scan and the
        bounded live refresh path so their semantics cannot drift."""
        payload = _read_json(path)
        if not isinstance(payload, dict):
            return
        svid = _source_version_id(path, payload)
        if not svid:
            return
        # Cursor visual admission is restricted to the canonical, atomic
        # ``semantic/cursor_visual/<batch>/<SourceVersion>.json`` identity.
        # A payload whose lineage and filename disagree is malformed input,
        # not evidence for either SourceVersion.
        if worker == CURSOR_VISUAL_WORKER and path.stem != svid:
            return
        status = str(payload.get("status") or "").upper()
        grounded = contains_grounded_semantic_fields(payload)
        success = status == "SUCCESS"
        if worker == CURSOR_VISUAL_WORKER:
            if not cursor_visual_admissible(payload):
                return
            semantic = False
            visual = True
        else:
            semantic = success and (worker in {"agy", "qwen"} or grounded)
            visual = success and worker == "gemini31pro"
        artifact = Artifact(
            worker=worker,
            path=str(path),
            source_version_id=svid,
            status=status,
            model=str(payload.get("model") or worker),
            semantic_coverage=semantic,
            visual_coverage=visual,
            grounded_semantic_fields=grounded,
            review_signals=_review_signals(payload),
        )
        self.artifacts.append(artifact)
        self._by_source.setdefault(svid, []).append(artifact)

    def refresh_source(self, source_version_id: str) -> list[Artifact]:
        """Refresh one SourceVersion without re-reading the corpus.

        A long-lived Brain process can outlive a newly atomically-written
        cursor_visual artifact.  Canonical artifacts are named by
        SourceVersion, so re-scanning only canonical
        ``cursor_visual/<batch>/<svid>.json`` candidates is bounded and lets
        the same index instance observe both arrivals and
        removals/malformed replacements. CloudShell takeover artifacts are
        immutable historical input and deliberately remain untouched.
        """
        svid = str(source_version_id).strip()
        # SourceVersion is a filename component in the canonical artifact
        # contract.  Reject path-like values rather than traversing outside a
        # worker batch while trying to refresh one source.
        if not svid or Path(svid).name != svid:
            return []
        with self._lock:
            # Replace only live cursor_visual artifacts for this SourceVersion.
            # agy/qwen/gemini31pro and CloudShell takeovers are historical
            # initial-scan input; dropping them here made inherited semantic
            # coverage disappear from a long-lived Brain index.
            cursor_visual_paths = {
                str(Path(a.path).resolve())
                for a in self._by_source.get(svid, [])
                if a.worker == CURSOR_VISUAL_WORKER
                and Path(a.path).is_relative_to(self.semantic_root)
            }
            if cursor_visual_paths:
                self.artifacts = [
                    a for a in self.artifacts
                    if not (a.source_version_id == svid and str(Path(a.path).resolve()) in cursor_visual_paths)
                ]
            retained = [
                a for a in self._by_source.get(svid, [])
                if str(Path(a.path).resolve()) not in cursor_visual_paths
            ]
            if retained:
                self._by_source[svid] = retained
            else:
                self._by_source.pop(svid, None)
            # This is deliberately a Cursor-visual-only live path.  Existing
            # agy/qwen/gemini31pro discovery remains its historical initial
            # scan behavior; this repair must not give those workers a new
            # lifecycle or scheduling surface.
            for worker in (CURSOR_VISUAL_WORKER,):
                worker_root = self.semantic_root / worker
                if not worker_root.is_dir():
                    continue
                # Canonical visual artifacts have one batch directory.  This
                # avoids a corpus-wide re-scan for each long-lived read.
                for batch_dir in sorted(worker_root.iterdir()):
                    if not batch_dir.is_dir():
                        continue
                    path = batch_dir / f"{svid}.json"
                    if path.is_file():
                        self._index_worker_artifact(worker, path)
            return list(self._by_source.get(svid, ()))


    def _cloudshell_takeover_roots(self) -> list[tuple[Path, str]]:
        roots: list[tuple[Path, str]] = []
        candidates: set[Path] = set()
        # current semantic_root or direct sibling of its state-aware parent
        candidates.update(self.semantic_root.glob("cloudshell_*takeover*"))
        # standard production layout: <base>/semantic  with <base>/state/cloudshell_*
        state_dir = self.semantic_root.parent / "state"
        if state_dir.is_dir():
            candidates.update(state_dir.glob("cloudshell_*takeover*"))
        # test layout: <tmp>/semantic with <tmp>/state/cloudshell_*
        for parent_dir in (self.semantic_root.parent, self.semantic_root.parent.parent):
            if parent_dir is None or parent_dir == self.semantic_root:
                continue
            candidates.update(sorted(parent_dir.glob("cloudshell_*takeover*")))
            candidates.update(sorted(parent_dir.glob("state/cloudshell_*takeover*")))
        # Any sibling subfolders directly resolved from the semantic root.
        candidates.update(sorted(self.semantic_root.glob("../*/cloudshell_*takeover*")))
        for candidate in sorted(candidates):
            if not candidate.is_dir():
                continue
            results_dir = candidate / "results"
            if not results_dir.is_dir():
                continue
            provider = candidate.name
            for prefix in ("cloud_shell_", "cloudshell_"):
                if provider.startswith(prefix):
                    provider = provider[len(prefix):]
                    break
            roots.append((results_dir, provider or candidate.name))
        if not roots and self.semantic_root.name == "results" and self.semantic_root.parent.name.startswith("cloudshell_"):
            parent = self.semantic_root.parent
            provider = parent.name
            for prefix in ("cloud_shell_", "cloudshell_"):
                if provider.startswith(prefix):
                    provider = provider[len(prefix):]
                    break
            roots.append((self.semantic_root, provider or parent.name))
        return roots

    def _scan_cloudshell_takeovers(self) -> None:
        for results_root, provider in self._cloudshell_takeover_roots():
            for path in sorted(results_root.rglob("*.json")):
                payload = _read_json(path)
                if not isinstance(payload, dict):
                    continue
                svid = str(payload.get("source_version_id") or "").strip()
                if not svid:
                    continue
                if svid != path.stem:
                    continue
                analysis_layer = str(payload.get("analysis_layer") or "")
                if not (analysis_layer.startswith("cloudshell_") and analysis_layer.endswith("_source_semantic")):
                    # allow existing takeover layer names like glm53/kimik3 etc.
                    if not (analysis_layer.startswith("cloudshell_") and analysis_layer.split("_")[-1] == "semantic"):
                        continue
                model = str(payload.get("model") or "").strip()
                if not model:
                    continue
                if "self_check" in payload:
                    self_check = payload.get("self_check")
                    if not isinstance(self_check, dict):
                        continue
                    if any(str(v).upper() not in {"PASS", "TRUE", "1", "YES"} for v in self_check.values()):
                        continue
                if not contains_grounded_semantic_fields(payload):
                    continue
                artifact = Artifact(
                    worker=provider,
                    path=str(path),
                    source_version_id=svid,
                    status="SUCCESS",
                    model=model,
                    semantic_coverage=True,
                    visual_coverage=False,
                    grounded_semantic_fields=True,
                    review_signals=_review_signals(payload),
                )
                self.artifacts.append(artifact)
                self._by_source.setdefault(svid, []).append(artifact)

    def for_source(self, source_version_id: str) -> list[Artifact]:
        with self._lock:
            return list(self._by_source.get(str(source_version_id), ()))

    def all_artifacts(self) -> list[Artifact]:
        """Return a stable snapshot for long-lived external readers."""
        with self._lock:
            return list(self.artifacts)

    def metadata(self, source_version_id: str) -> dict[str, Any]:
        artifacts = self.for_source(source_version_id)
        return {
            "semantic_coverage": any(a.semantic_coverage for a in artifacts),
            "visual_coverage": any(a.visual_coverage for a in artifacts),
            "review_signals": sorted({signal for a in artifacts for signal in a.review_signals}),
            "provenance": [a.as_dict() for a in artifacts],
        }

    def semantic_sources(self) -> set[str]:
        with self._lock:
            return {a.source_version_id for a in self.artifacts if a.semantic_coverage}

    def visual_sources(self) -> set[str]:
        with self._lock:
            return {a.source_version_id for a in self.artifacts if a.visual_coverage}

    def failed_sources(self) -> set[str]:
        with self._lock:
            return {a.source_version_id for a in self.artifacts if a.status in {"FAILED", "RETRYABLE"}}

    def provenance_counts(self) -> dict[str, dict[str, int]]:
        with self._lock:
            counts: dict[str, dict[str, int]] = {}
            for worker in WORKERS:
                entries = [a for a in self.artifacts if a.worker == worker]
                models = sorted({a.model for a in entries})
                model_key = models[0] if len(models) == 1 else worker
                counts[model_key] = {
                    "artifact_count": len(entries),
                    "source_count": len({a.source_version_id for a in entries}),
                    "semantic_sources": len({a.source_version_id for a in entries if a.semantic_coverage}),
                    "visual_sources": len({a.source_version_id for a in entries if a.visual_coverage}),
                    "failed_artifacts": sum(a.status in {"FAILED", "RETRYABLE"} for a in entries),
                }
            return counts
