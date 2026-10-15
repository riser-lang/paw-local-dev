"""Cursor Visual Admission: canonical visual artifacts Brain can reuse.

Analysis-plane only.  Never writes Formal State, never mutates the frozen
Cursor semantic queue/checkpoint, and never overwrites agy/qwen/gemini31pro
or existing Cursor semantic result identity.

Canonical store (Brain/ReusableArtifactIndex):
  <READER_ROOT>/semantic/cursor_visual/<batch>/<source_version_id>.json
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "paw-cursor-visual-artifact-v1/1"
WORKER = "cursor_visual"
DEFAULT_MODEL = "Cursor Grok 4.6"
DEFAULT_MODEL_ID = "cursor-grok-4.6"
DEFAULT_READER_ROOT = Path("/home/ubuntu/paw-dev/paw-full-reader-20260902")
ANALYSIS_LAYER = "cursor_visual_supplement"

_LOCATOR_RE = re.compile(
    r"^(visual_pages|tables|text_blocks|embedded_objects|derived_artifacts|"
    r"relationships|warnings)"
    r"\[(\d+)\]"
    r"(?:\.[A-Za-z_][\w]*)?$"
)
_SS_LOCATOR_RE = re.compile(
    r"^structured_semantics\.(drawing_inventory|slides|sheets|sections)\[(\d+)\]"
    r"(?:\.[A-Za-z_][\w]*)?$"
)
_FIDELITY_RE = re.compile(r"^fidelity_flags\.[A-Za-z_][\w]*$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_reuse_module(reader_root: Path | None = None):
    root = Path(reader_root or DEFAULT_READER_ROOT)
    module_path = root / "cursor_semantic_reuse.py"
    spec = importlib.util.spec_from_file_location(
        "_paw_cursor_semantic_reuse_visual", str(module_path)
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load ReusableArtifactIndex from {module_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def canonical_artifact_path(
    source_version_id: str,
    batch: str,
    *,
    reader_root: Path | None = None,
) -> Path:
    root = Path(reader_root or DEFAULT_READER_ROOT)
    safe_batch = batch.strip() or "_unbatched"
    return root / "semantic" / WORKER / safe_batch / f"{source_version_id}.json"


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def locator_exists(reader: Mapping[str, Any], locator: str) -> bool:
    loc = _text(locator)
    if not loc:
        return False
    m = _LOCATOR_RE.match(loc)
    if m:
        arr = reader.get(m.group(1))
        idx = int(m.group(2))
        return isinstance(arr, list) and 0 <= idx < len(arr)
    m = _SS_LOCATOR_RE.match(loc)
    if m:
        ss = reader.get("structured_semantics")
        if not isinstance(ss, Mapping):
            return False
        arr = ss.get(m.group(1))
        idx = int(m.group(2))
        return isinstance(arr, list) and 0 <= idx < len(arr)
    if _FIDELITY_RE.match(loc):
        flags = reader.get("fidelity_flags")
        key = loc.split(".", 1)[1]
        return isinstance(flags, Mapping) and key in flags
    return False


def validate_evidence(
    reader: Mapping[str, Any],
    locators: list[str],
) -> dict[str, Any]:
    invalid: list[str] = []
    valid: list[str] = []
    for raw in locators:
        loc = _text(raw)
        if not loc:
            continue
        if locator_exists(reader, loc):
            valid.append(loc)
        else:
            invalid.append(loc)
    status = "PASS" if valid and not invalid else ("NEEDS_REVIEW" if valid else "FAIL")
    if invalid and valid:
        status = "NEEDS_REVIEW"
    return {
        "status": status,
        "valid_locators": valid,
        "invalid_locators": invalid,
        "invalid_count": len(invalid),
        "valid_count": len(valid),
    }


def _require(source: Mapping[str, Any], *keys: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in keys:
        value = _text(source.get(key))
        if not value:
            raise ValueError(f"missing required source field: {key}")
        out[key] = value
    return out


def _dedupe_maps(items: list[Mapping[str, Any]] | None, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    seen: set[tuple[str, ...]] = set()
    out: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, Mapping):
            continue
        key = tuple(_text(item.get(k)) for k in keys)
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(item))
    return out


def merge_analysis_payloads(
    existing: Mapping[str, Any] | None,
    incoming: Mapping[str, Any] | None,
) -> dict[str, list[dict[str, Any]]]:
    """Union analysis lists; keep earlier grounded facts first."""
    old = existing or {}
    new = incoming or {}
    fact_keys = ("image_sha256", "content")
    loc_keys = ("locator", "image_sha256")
    return {
        "grounded_visual_facts": _dedupe_maps(
            list(old.get("grounded_visual_facts") or []) + list(new.get("grounded_visual_facts") or []),
            fact_keys,
        ),
        "table_observations": _dedupe_maps(
            list(old.get("table_observations") or []) + list(new.get("table_observations") or []),
            fact_keys,
        ),
        "visual_observations": _dedupe_maps(
            list(old.get("visual_observations") or []) + list(new.get("visual_observations") or []),
            fact_keys,
        ),
        "ambiguities": _dedupe_maps(
            list(old.get("ambiguities") or []) + list(new.get("ambiguities") or []),
            ("content",),
        ),
        "already_grounded_skipped": _dedupe_maps(
            list(old.get("already_grounded_skipped") or [])
            + list(new.get("already_grounded_skipped") or []),
            ("why", "content"),
        ),
        "page_dispositions": _dedupe_maps(
            list(old.get("page_dispositions") or []) + list(new.get("page_dispositions") or []),
            loc_keys,
        ),
    }


def merge_visual_inputs(
    existing: list[Mapping[str, Any]] | None,
    incoming: list[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Keep original first input, then append newly reviewed images."""
    return _dedupe_maps(list(existing or []) + list(incoming or []), ("locator", "image_sha256"))


def build_canonical_artifact(
    *,
    source: Mapping[str, Any],
    visual_inputs: list[Mapping[str, Any]],
    grounded_visual_facts: list[Mapping[str, Any]],
    table_observations: list[Mapping[str, Any]] | None = None,
    visual_observations: list[Mapping[str, Any]] | None = None,
    ambiguities: list[Mapping[str, Any]] | None = None,
    already_grounded_skipped: list[Mapping[str, Any]] | None = None,
    page_dispositions: list[Mapping[str, Any]] | None = None,
    source_status: str,
    model: str = DEFAULT_MODEL,
    model_id: str = DEFAULT_MODEL_ID,
    reader: Mapping[str, Any] | None = None,
    image_read_method: str = "cursor_read_image_pixels",
) -> dict[str, Any]:
    ident = _require(
        source,
        "source_version_id",
        "source_id",
        "stable_object_id",
        "version_marker",
        "raw_sha256",
        "reader_result_path",
    )
    if not visual_inputs:
        raise ValueError("visual_inputs required")
    if not grounded_visual_facts and not visual_observations and not table_observations:
        raise ValueError("at least one grounded visual/table observation required")

    normalized_inputs: list[dict[str, Any]] = []
    locators: list[str] = []
    for raw in visual_inputs:
        image_path = _text(raw.get("image_path") or raw.get("path"))
        if not image_path or not Path(image_path).is_file():
            raise ValueError(f"visual input image missing: {image_path}")
        expected_sha = _text(raw.get("image_sha256") or raw.get("sha256"))
        actual_sha = sha256_file(image_path)
        if expected_sha and expected_sha != actual_sha:
            raise ValueError(
                f"image sha256 mismatch for {image_path}: "
                f"declared {expected_sha} != actual {actual_sha}"
            )
        locator = _text(raw.get("locator") or raw.get("evidence_locator"))
        if not locator:
            raise ValueError(f"visual input missing locator: {image_path}")
        locators.append(locator)
        item = {
            "image_path": image_path,
            "image_sha256": actual_sha,
            "locator": locator,
            "kind": _text(raw.get("kind")) or None,
            "page": raw.get("page"),
            "slide": raw.get("slide"),
            "sheet": raw.get("sheet"),
            "render_mode": _text(raw.get("render_mode")) or None,
            "dimensions": raw.get("dimensions"),
            "image_read_method": _text(raw.get("image_read_method")) or image_read_method,
        }
        normalized_inputs.append(item)

    for group in (
        grounded_visual_facts,
        table_observations or (),
        visual_observations or (),
        ambiguities or (),
        page_dispositions or (),
    ):
        for fact in group:
            if isinstance(fact, Mapping):
                locators.extend(_text(x) for x in (fact.get("evidence_locators") or ()))
                loc = _text(fact.get("locator"))
                if loc:
                    locators.append(loc)

    reader_payload = reader
    if reader_payload is None:
        reader_path = Path(ident["reader_result_path"])
        reader_payload = json.loads(reader_path.read_text(encoding="utf-8"))
    evidence_validation = validate_evidence(reader_payload, locators)
    if evidence_validation["status"] == "FAIL":
        raise ValueError(f"evidence locators invalid: {evidence_validation['invalid_locators']}")

    payload = {
        "schema": SCHEMA,
        "status": "SUCCESS",
        "worker": WORKER,
        "model": model,
        "model_id": model_id,
        "analysis_layer": ANALYSIS_LAYER,
        "visual_coverage": True,
        "semantic_coverage": False,
        "completed_at": utc_now(),
        "authoritative_lineage": {
            "source_id": ident["source_id"],
            "source_version_id": ident["source_version_id"],
            "stable_object_id": ident["stable_object_id"],
            "version_marker": ident["version_marker"],
            "raw_sha256": ident["raw_sha256"],
            "reader_result_path": ident["reader_result_path"],
        },
        "routing": {
            "batch": _text(source.get("batch")),
            "project_tier": _text(source.get("project_tier")),
            "paw_project_name": _text(source.get("paw_project_name")),
            "paw_project_id": _text(source.get("paw_project_id")),
            "inventory_path": _text(source.get("inventory_path")),
            "title": _text(source.get("title")),
            "family": _text(source.get("family")),
        },
        "visual_input": normalized_inputs[0],
        "visual_inputs": normalized_inputs,
        "source_status": source_status,
        "evidence_validation": evidence_validation,
        "analysis": {
            "grounded_visual_facts": list(grounded_visual_facts),
            "table_observations": list(table_observations or []),
            "visual_observations": list(visual_observations or []),
            "ambiguities": list(ambiguities or []),
            "already_grounded_skipped": list(already_grounded_skipped or []),
            "page_dispositions": list(page_dispositions or []),
        },
        "governance": {
            "artifact_type": "analysis",
            "formal_state_write": "PROHIBITED",
            "cross_source_synthesis": "PROHIBITED",
            "does_not_modify": [
                "cursor_semantic_v1.queue",
                "cursor_semantic_v1.checkpoint",
                "cursor_semantic_v1.results",
                "semantic/agy",
                "semantic/qwen",
                "semantic/gemini31pro",
                "Knowledge",
                "Project",
                "Data",
                "Todo",
            ],
        },
    }
    return payload


def persist_canonical_artifact(
    payload: Mapping[str, Any],
    *,
    reader_root: Path | None = None,
    reuse_module=None,
) -> Path:
    lineage = payload.get("authoritative_lineage") or {}
    routing = payload.get("routing") or {}
    svid = _text(lineage.get("source_version_id"))
    batch = _text(routing.get("batch")) or "_unbatched"
    if not svid:
        raise ValueError("payload missing authoritative_lineage.source_version_id")
    mod = reuse_module or load_reuse_module(reader_root)
    if not mod.cursor_visual_admissible(dict(payload)):
        raise ValueError("payload failed cursor_visual_admissible")
    path = canonical_artifact_path(svid, batch, reader_root=reader_root)
    atomic_write_json(path, payload)
    return path


def existing_image_identities(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(payload, dict):
        return set()
    found: set[str] = set()
    vis = payload.get("visual_inputs")
    if isinstance(vis, list):
        for item in vis:
            if isinstance(item, dict) and item.get("image_sha256"):
                found.add(str(item["image_sha256"]))
    single = payload.get("visual_input")
    if isinstance(single, dict) and single.get("image_sha256"):
        found.add(str(single["image_sha256"]))
    return found


def should_skip_repeat(path: Path, image_sha256s: list[str]) -> bool:
    """Idempotent skip when REVIEWED artifact already covers the same images."""
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    if str(payload.get("status") or "").upper() != "SUCCESS":
        return False
    if str(payload.get("source_status") or "") not in {
        "REVIEWED_GROUNDED",
        "VISUAL_SUPPLEMENTED",
    }:
        return False
    existing = existing_image_identities(path)
    incoming = {s for s in image_sha256s if s}
    return bool(incoming) and incoming <= existing


def index_visual_coverage(
    source_version_id: str,
    *,
    reader_root: Path | None = None,
) -> dict[str, Any]:
    root = Path(reader_root or DEFAULT_READER_ROOT)
    mod = load_reuse_module(root)
    idx = mod.ReusableArtifactIndex(root / "semantic")
    artifacts = [a.as_dict() for a in idx.for_source(source_version_id)]
    visual = [a for a in artifacts if a.get("visual_coverage")]
    cursor_visual = [a for a in visual if a.get("worker") == WORKER]
    return {
        "source_version_id": source_version_id,
        "artifact_count": len(artifacts),
        "visual_artifact_count": len(visual),
        "cursor_visual_count": len(cursor_visual),
        "visual_coverage": any(a.get("visual_coverage") for a in artifacts),
        "semantic_coverage": any(a.get("semantic_coverage") for a in artifacts),
        "artifacts": artifacts,
        "cursor_visual": cursor_visual,
    }


def brain_read_semantic(
    source_version_id: str,
    *,
    reader_root: Path | None = None,
) -> dict[str, Any]:
    """Canonical Brain/Reader read path (CursorSemanticReader.read_semantic).

    Instantiates a fresh reader so newly written artifacts are visible.
    Does not claim sources or record consolidation.
    """
    root = Path(reader_root or DEFAULT_READER_ROOT)
    gateway = "/opt/paw-mcp-gateway"
    if gateway not in sys.path:
        sys.path.insert(0, gateway)
    # Drop cached reuse module so Brain reloads the patched scanner.
    sys.modules.pop("_cursor_semantic_reuse_canonical", None)
    from app.gpt_brain_bootstrap import CursorSemanticReader

    reader = CursorSemanticReader(root)
    return reader.read_semantic(source_version_id)
